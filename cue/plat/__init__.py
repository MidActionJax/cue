"""What differs between Windows and macOS, in one place.

Everything else in Cue calls these instead of Windows or macOS APIs directly:

    IS_WIN / IS_MAC
    open_path(p)                 open a file or folder in Explorer / Finder
    popen_kwargs(detached=...)   no console window (Windows) / own session (macOS)
    hard_exit(code)              leave without interpreter or library teardown
    set_app_identity()           taskbar grouping (Windows); nothing needed on macOS
    on_shutdown(save)            save the call when the console closes / the process is told to stop
    fix_path()                   a Finder-launched Mac app doesn't get the shell's PATH (claude, ollama, git, compilers)
    say(text) / stop_saying()    text-to-speech for practice mode
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def open_path(p) -> None:
    """Open a file or folder the way double-clicking it would."""
    p = str(p)
    if IS_WIN:
        os.startfile(p)  # noqa: S606 - opening the user's own file
    elif IS_MAC:
        subprocess.Popen(["open", p])
    else:
        subprocess.Popen(["xdg-open", p])


def popen_kwargs(detached: bool = False) -> dict:
    """Keyword arguments for subprocess.Popen/run: never a console window on Windows; a detached
    child keeps running after Cue exits (its own process group/session)."""
    if IS_WIN:
        flags = NO_WINDOW
        if detached:
            flags |= getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        return {"creationflags": flags}
    return {"start_new_session": True} if detached else {}


def hard_exit(code: int) -> None:
    """Everything is saved by now. Skip interpreter teardown (audio/STT threads are daemons) and
    library unload too: on Windows, ExitProcess can hang in the CUDA/ONNX libraries' unload
    handlers while a worker thread is mid-inference."""
    logging.shutdown()
    if sys.stdout:
        sys.stdout.flush()
    if IS_WIN:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
        k32.TerminateProcess(k32.GetCurrentProcess(), code)
    os._exit(code)


def set_app_identity(app_id: str = "MidActionJax.Cue") -> None:
    """Windows: its own taskbar button and icon instead of python.exe's."""
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        except Exception as e:
            log.debug("app id: %s", e)


_handler_ref = None


def on_shutdown(save) -> None:
    """Save the call when the process is being ended from outside: the console window's close
    button / logoff on Windows (no Qt event arrives), SIGTERM / SIGHUP / Ctrl+C on macOS."""
    global _handler_ref
    if IS_WIN:
        import ctypes
        from ctypes import wintypes

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
        def handler(event):
            if event in (0, 1, 2, 5, 6):  # Ctrl+C, Ctrl+Break, close, logoff, shutdown
                log.info("console closing (event %d) — saving the call", event)
                try:
                    save()
                finally:
                    hard_exit(0)
            return False

        _handler_ref = handler  # must stay referenced or ctypes frees it
        ctypes.windll.kernel32.SetConsoleCtrlHandler(handler, True)
        return
    import signal

    def handler(signum, _frame):
        log.info("signal %d — saving the call", signum)
        try:
            save()
        finally:
            hard_exit(0)

    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):
            pass


# Where command-line tools live on a Mac when the app is started from Finder or the Dock (those
# apps get a bare PATH: /usr/bin:/bin:/usr/sbin:/sbin).
_MAC_PATHS = ["/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin", "~/.local/bin", "~/.claude/local",
              "~/.npm-global/bin", "~/.bun/bin", "~/.cargo/bin", "~/go/bin", "~/.deno/bin",
              "/Applications/Ollama.app/Contents/Resources"]


def fix_path() -> None:
    """macOS: give a Finder-launched app the PATH a Terminal would have, so `claude`, `ollama`,
    `git` and compilers are found. Asks the login shell once (short timeout), then adds the usual
    install locations."""
    if not IS_MAC:
        return
    have = os.environ.get("PATH", "").split(os.pathsep)
    extra: list[str] = []
    shell = os.environ.get("SHELL") or "/bin/zsh"
    try:
        out = subprocess.run([shell, "-lic", "echo __CUE_PATH__$PATH"], capture_output=True, text=True, timeout=4)
        line = next((l for l in out.stdout.splitlines() if l.startswith("__CUE_PATH__")), "")
        extra += line[len("__CUE_PATH__"):].split(os.pathsep) if line else []
    except Exception as e:
        log.debug("login shell PATH: %s", e)
    extra += [os.path.expanduser(p) for p in _MAC_PATHS]
    nvm = Path.home() / ".nvm" / "versions" / "node"
    if nvm.is_dir():
        extra += sorted((str(d / "bin") for d in nvm.iterdir()), reverse=True)
    merged = list(dict.fromkeys([p for p in have + extra if p and os.path.isdir(p)]))
    os.environ["PATH"] = os.pathsep.join(merged)


_say_proc: subprocess.Popen | None = None
_sapi = None


def say(text: str) -> None:
    """Read text out loud without blocking, cutting off anything still being spoken."""
    global _say_proc, _sapi
    stop_saying()
    if IS_MAC:
        try:
            _say_proc = subprocess.Popen(["say", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            log.warning("text-to-speech unavailable: %s", e)
        return
    if IS_WIN:
        try:
            if _sapi is None:
                import comtypes.client
                _sapi = comtypes.client.CreateObject("SAPI.SpVoice")
                _sapi.Rate = 0
            _sapi.Speak(text, 1 | 2)  # async + purge anything still being spoken
        except Exception as e:
            log.warning("text-to-speech unavailable: %s", e)


def stop_saying() -> None:
    global _say_proc
    if _say_proc is not None and _say_proc.poll() is None:
        _say_proc.terminate()
    _say_proc = None
    if _sapi is not None:
        try:
            _sapi.Speak("", 2)
        except Exception:
            pass
