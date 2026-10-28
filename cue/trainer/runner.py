"""Compile and run code in a throwaway folder, with a time limit, a memory cap and the whole
process tree killed on timeout (a Windows Job Object), and output capped so a print loop can't
eat memory.
"""
from __future__ import annotations

import ctypes
import hashlib
import logging
import os
import shutil
import subprocess
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path

from ..config import DATA
from . import langs

log = logging.getLogger(__name__)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
WORK = DATA / "trainer" / "build"
MAX_OUT = 16 * 1024 * 1024        # a 10^5-element answer is ~1 MB of JSON
MARK = "@@CUE_RESULT@@"


@dataclass
class RunResult:
    ok: bool
    code: int | None
    stdout: str
    stderr: str
    ms: float
    timed_out: bool = False
    error: str = ""                # couldn't start at all

    @property
    def result_line(self) -> str | None:
        """The harness's JSON result (the last marker line), separate from your own prints."""
        for line in reversed(self.stdout.splitlines()):
            if line.startswith(MARK):
                return line[len(MARK):].strip()
        return None

    @property
    def printed(self) -> str:
        return "\n".join(l for l in self.stdout.splitlines() if not l.startswith(MARK))


# ---------------------------------------------------------------------------- job objects

class _IO(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in ("r", "w", "o", "rt", "wt", "ot")]


class _BASIC(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]


class _EXTENDED(ctypes.Structure):
    _fields_ = [("Basic", _BASIC), ("Io", _IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


_k32 = ctypes.windll.kernel32
_k32.CreateJobObjectW.restype = wintypes.HANDLE
_k32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
_k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
_k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
_k32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
_k32.CloseHandle.argtypes = (wintypes.HANDLE,)
KILL_ON_CLOSE, PROCESS_MEMORY = 0x2000, 0x100


def _job(mem_mb: int) -> int | None:
    try:
        job = _k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _EXTENDED()
        info.Basic.LimitFlags = KILL_ON_CLOSE | (PROCESS_MEMORY if mem_mb else 0)
        info.ProcessMemoryLimit = mem_mb * 1024 * 1024
        _k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
        return job
    except Exception as e:
        log.debug("job object unavailable: %s", e)
        return None


def _reader(stream, sink: list, cap: int) -> None:
    size = 0
    while True:
        chunk = stream.read(8192)
        if not chunk:
            break
        if size < cap:
            sink.append(chunk[: cap - size])
        size += len(chunk)
    if size > cap:
        sink.append(b"\n[output cut: too much printed]\n")


def run(cmd: list[str], cwd: Path | str, stdin: str = "", timeout: float = 5.0, mem_mb: int = 1024,
        env: dict | None = None) -> RunResult:
    t0 = time.perf_counter()
    e = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", PYTHONDONTWRITEBYTECODE="1")
    e.update(env or {})
    try:
        p = subprocess.Popen(cmd, cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, creationflags=_NO_WINDOW, env=e)
    except OSError as ex:
        return RunResult(False, None, "", "", 0.0, error=f"couldn't start {Path(cmd[0]).name}: {ex}")
    job = _job(mem_mb)
    if job:
        _k32.AssignProcessToJobObject(job, int(p._handle))
    out, err = [], []
    readers = [threading.Thread(target=_reader, args=(p.stdout, out, MAX_OUT), daemon=True),
               threading.Thread(target=_reader, args=(p.stderr, err, 256 * 1024), daemon=True)]
    for r in readers:
        r.start()
    try:
        if stdin:
            p.stdin.write(stdin.encode("utf-8"))
        p.stdin.close()
    except OSError:
        pass
    timed_out = False
    try:
        p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        if job:
            _k32.TerminateJobObject(job, 1)
        else:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True, creationflags=_NO_WINDOW)
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            p.kill()
    ms = (time.perf_counter() - t0) * 1000
    for r in readers:
        r.join(timeout=2)
    if job:
        _k32.CloseHandle(job)
    stdout = b"".join(out).decode("utf-8", "replace").replace("\r\n", "\n")
    stderr = b"".join(err).decode("utf-8", "replace").replace("\r\n", "\n")
    return RunResult(p.returncode == 0 and not timed_out, p.returncode, stdout, stderr, ms, timed_out)


# ---------------------------------------------------------------------------- build folders

@dataclass
class Build:
    ok: bool
    dir: Path
    run_cmd: list[str] = field(default_factory=list)
    output: str = ""               # compiler output (errors) with your file's real name
    ms: float = 0.0


def _fill(cmd: list[str], d: Path, main: Path, src: Path, files: list[Path]) -> list[str]:
    out = []
    for part in cmd:
        if part == "{files}":
            out += [str(f) for f in files]
            continue
        out.append(part.replace("{exe}", str(d / "prog.exe")).replace("{main}", str(main))
                   .replace("{src}", str(src)).replace("{harness}", str(main)).replace("{dir}", str(d)))
    return out


def _csproj(d: Path) -> None:
    (d / "cue.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType>'
        '<TargetFramework>net8.0</TargetFramework><RollForward>Major</RollForward><Nullable>disable</Nullable>'
        '<ImplicitUsings>enable</ImplicitUsings><AssemblyName>cue</AssemblyName>'
        '<TreatWarningsAsErrors>false</TreatWarningsAsErrors><NoWarn>CS8632;CS0168;CS0219</NoWarn>'
        '</PropertyGroup></Project>', encoding="utf-8")


def prepare(lang: langs.Language, code: str, harness: str | None, user_path: str = "",
            timeout: float = 60.0) -> Build:
    """Write your code (+ the harness) into a build folder and compile if the language needs it.
    The folder is keyed by the content, so an unchanged file is never rebuilt."""
    tc = langs.toolchain(lang)
    if tc is None:
        return Build(False, WORK, output=f"{lang.name} isn't installed on this PC (reference only).")
    key = hashlib.sha1(f"{lang.key}\0{code}\0{harness or ''}".encode("utf-8")).hexdigest()[:16]
    d = WORK / f"{lang.key}-{key}"
    src = d / lang.user_name(code)
    if harness is not None and lang.wiring == "append":
        main = d / lang.harness_name()
    else:
        main = d / lang.harness_name() if harness is not None else src
    stamp = d / ".built"
    if stamp.exists():
        return Build(True, d, _fill(tc.run, d, main, src, _sources(d)), "", 0.0)
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    if harness is not None and lang.wiring == "append":
        main.write_text(code.rstrip() + "\n\n" + harness, encoding="utf-8")
        src.write_text(code, encoding="utf-8")   # kept for the syntax check / error messages
    else:
        src.write_text(code, encoding="utf-8")
        if harness is not None:
            main.write_text(harness, encoding="utf-8")
    if lang.key == "csharp":
        _csproj(d)
    files = _sources(d)
    if harness is not None and lang.wiring == "append":
        files = [main]
    out, ms = "", 0.0
    if tc.build:
        r = run(_fill(tc.build, d, main, src, files), d, timeout=timeout, mem_mb=4096)
        ms = r.ms
        out = _real_names((r.stdout + "\n" + r.stderr).strip(), d, src, user_path)
        if not r.ok:
            if r.timed_out:
                out = "The build took too long and was stopped.\n" + out
            if r.error:
                out = r.error
            return Build(False, d, [], out, ms)
    stamp.write_text("ok", encoding="utf-8")
    _prune()
    return Build(True, d, _fill(tc.run, d, main, src, files), out, ms)


def _sources(d: Path) -> list[Path]:
    return sorted(p for p in d.iterdir() if p.is_file() and not p.name.startswith(".")
                  and p.suffix not in (".exe", ".pdb", ".obj", ".o", ".csproj", ".jar", ".rmeta", ".class"))


def _real_names(text: str, d: Path, src: Path, user_path: str) -> str:
    """Compiler messages name the copy in the build folder: show your file's name instead."""
    if not user_path:
        return text
    name = Path(user_path).name
    return text.replace(str(src), name).replace(str(d) + os.sep, "").replace(src.name, name)


def _prune(keep: int = 40) -> None:
    try:
        dirs = sorted((p for p in WORK.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime)
        for p in dirs[:-keep]:
            shutil.rmtree(p, ignore_errors=True)
    except OSError:
        pass


def syntax_check(lang: langs.Language, code: str, user_path: str = "") -> list[dict]:
    """Real syntax/type errors in your file alone: [{"line", "message"}]. Empty = clean (or no checker)."""
    if lang.key == "python":
        try:
            compile(code, user_path or "solution.py", "exec")
            return []
        except SyntaxError as e:
            return [{"line": e.lineno or 1, "message": f"{e.__class__.__name__}: {e.msg}"}]
    tc = langs.toolchain(lang)
    if tc is None or not tc.check:
        return []
    d = WORK / f"check-{lang.key}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "check").mkdir(exist_ok=True)
    src = d / lang.user_name(code)
    for old in d.glob("*" + lang.ext):
        if old != src:
            old.unlink(missing_ok=True)
    src.write_text(code, encoding="utf-8")
    r = run(_fill(tc.check, d, src, src, [src]), d, timeout=20, mem_mb=2048)
    if r.ok and not (lang.key == "go" and r.stderr.strip()):
        return []
    text = _real_names(r.stdout + "\n" + r.stderr, d, src, user_path)
    return parse_errors(text, Path(user_path or src.name).name)


def parse_errors(text: str, filename: str) -> list[dict]:
    """Pull "file:line[:col]: message" style errors out of compiler output."""
    import re
    errs, seen = [], set()
    pats = [rf"{re.escape(filename)}[:(](\d+)[,:]?(\d+)?\)?:?\s*(?:error|fatal error|Error)?:?\s*(.*)",
            r"line (\d+)()[:,]?\s*(.+)", r"-->\s*\S+?:(\d+):(\d+)()"]
    lines = text.splitlines()
    for i, line in enumerate(lines):
        for pat in pats:
            m = re.search(pat, line)
            if m:
                ln = int(m.group(1))
                msg = (m.group(3) or "").strip()
                if not msg:   # node: "file:3" then the source line, a caret, and "SyntaxError: ..." below
                    msg = next((l.strip() for l in lines[i + 1:i + 6] if re.match(r"^\s*\w*(Error|error)\b", l)), "")
                if not msg and i > 0:   # rustc puts the message on the line before "-->"
                    msg = lines[i - 1].strip()
                if "warning" in msg.lower()[:10]:
                    break
                if (ln, msg) not in seen:
                    seen.add((ln, msg))
                    errs.append({"line": ln, "message": msg[:300] or "error"})
                break
    if not errs and text.strip():
        errs.append({"line": 0, "message": text.strip().splitlines()[0][:300]})
    return errs[:20]
