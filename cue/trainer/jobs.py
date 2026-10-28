"""Background model jobs for the trainer's roles (Listener, Designer, Test author, Harness,
Reviewer, Debugger), next to the live answer backend.

- Frequent roles (Listener, Reviewer) keep a warm `claude -p` session, so an update takes ~2-3 s.
  Rare ones (Designer, Test author, Harness, Debugger) start a one-shot `claude -p` (~7 s) and may
  think first, since they're judged by running code anyway.
- Latest wins: while a role is busy, newer requests replace each other, and only the newest runs
  next. A transcript update that's already stale is never sent.
- Replies must be JSON. A reply that doesn't parse is retried once with the error; after that the
  job fails and nothing is shown.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from typing import Callable

from ..config import DATA
from ..llm import AnthropicBackend, ClaudeCLIBackend

log = logging.getLogger(__name__)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

WARM = ("listener", "reviewer")
TIMEOUTS = {"listener": 60, "reviewer": 90, "designer": 300, "tests": 300, "harness": 240, "debugger": 240}


class JobError(Exception):
    pass


def parse_reply(role: str, text: str) -> dict:
    """The Test author sends its Python module as a fenced block (code inside a JSON string breaks
    on quotes and backslashes), then the details as JSON. Everyone else: one JSON object."""
    if role == "tests":
        m = re.search(r"```python[ \t]*\r?\n(.*?)```", text, re.S)
        if m:
            data = _lenient_details(text[:m.start()] + text[m.end():])
            data["module"] = m.group(1)
            return data
    return parse_json(text)


def _lenient_details(text: str) -> dict:
    """The Test author's details. A good module is worth keeping even if one edge case in the JSON
    is malformed: try JSON, then Python-style literals, then salvage edge cases one by one."""
    import ast
    try:
        return parse_json(text)
    except ValueError as e:
        err = e
    body = text[text.find("{"): text.rfind("}") + 1]
    try:
        py = re.sub(r"\btrue\b", "True", re.sub(r"\bfalse\b", "False", re.sub(r"\bnull\b", "None", body)))
        data = ast.literal_eval(py)
        if isinstance(data, dict):
            return data
    except (ValueError, SyntaxError):
        pass
    data = {"compare": (re.search(r'"compare"\s*:\s*"([^"]+)"', text) or [None, "exact"])[1], "edge_cases": []}
    for n in ("brute_max_n", "n_max"):
        m = re.search(rf'"{n}"\s*:\s*(\d+)', text)
        if m:
            data[n] = int(m.group(1))
    for m in re.finditer(r'\{\s*"name"\s*:.*?"input"\s*:.*?\}(?=\s*[,\]])', text, re.S):
        try:
            data["edge_cases"].append(json.loads(m.group(0)))
        except ValueError:
            pass
    log.warning("test details weren't valid JSON (%s); kept %d edge cases", err, len(data["edge_cases"]))
    return data


def parse_json(text: str) -> dict:
    """The JSON object in a reply (tolerates a stray fence or a sentence around it)."""
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the reply")
    return json.loads(t[start:end + 1])


class Jobs:
    """on_result(role, data | None, error | None, ctx) is called from a worker thread."""

    def __init__(self, tcfg, llm_cfg, systems: dict[str, str], on_result: Callable):
        self.tcfg, self.llm_cfg = tcfg, llm_cfg
        self.systems = systems
        self.on_result = on_result
        self.models = dict(tcfg.get("models") or {})
        self.calls = 0
        self.kind = "cli" if ClaudeCLIBackend.available() else "api" if AnthropicBackend.available() else None
        self._lanes: dict[str, dict] = {}
        self._warm: dict[str, ClaudeCLIBackend] = {}
        self._lock = threading.Lock()
        self._closed = False
        if self.kind == "cli":
            for role in WARM:
                b = ClaudeCLIBackend(self.model(role), tag=f"trainer_{role}")
                b.configure(systems[role])
                self._warm[role] = b

    @property
    def available(self) -> bool:
        return self.kind is not None

    def model(self, role: str) -> str:
        return self.models.get(role) or self.models.get("default") or "sonnet"

    def busy(self, role: str) -> bool:
        lane = self._lanes.get(role)
        return bool(lane and lane["running"])

    def submit(self, role: str, prompt: str, ctx=None) -> None:
        if not self.available or self._closed:
            self.on_result(role, None, "Trainer needs Claude (the claude CLI logged in, or an API key)", ctx)
            return
        with self._lock:
            lane = self._lanes.setdefault(role, {"running": False, "pending": None})
            if lane["running"]:
                lane["pending"] = (prompt, ctx)   # latest wins
                return
            lane["running"] = True
        threading.Thread(target=self._work, args=(role, prompt, ctx), daemon=True, name=f"trainer-{role}").start()

    def _work(self, role: str, prompt: str, ctx) -> None:
        while True:
            t = time.monotonic()
            try:
                data = self._ask_json(role, prompt)
                log.info("trainer %s done in %.1fs", role, time.monotonic() - t)
                err = None
            except Exception as e:
                data, err = None, str(e)[:500]
                log.warning("trainer %s failed after %.1fs: %s", role, time.monotonic() - t, err)
            if not self._closed:
                try:
                    self.on_result(role, data, err, ctx)
                except Exception:
                    log.exception("trainer %s result handler failed", role)
            with self._lock:
                lane = self._lanes[role]
                if lane["pending"] is None or self._closed:
                    lane["running"] = False
                    return
                (prompt, ctx), lane["pending"] = lane["pending"], None

    def _ask_json(self, role: str, prompt: str) -> dict:
        text = self.ask_text(role, prompt)
        try:
            return parse_reply(role, text)
        except ValueError as e:
            log.info("trainer %s: reply wasn't JSON (%s), retrying once", role, e)
            retry = (f"{prompt}\n\nYour previous reply could not be parsed ({e}). It began:\n{text[:600]}\n\n"
                     "Reply again in exactly the format asked for (valid JSON: escape quotes and backslashes in strings).")
            return parse_reply(role, self.ask_text(role, retry, warm=False))

    def ask_text(self, role: str, prompt: str, warm: bool = True) -> str:
        self.calls += 1
        timeout = TIMEOUTS.get(role, 180)
        if self.kind == "api":
            return self._api(role, prompt, timeout)
        if warm and role in self._warm:
            return self._ask_warm(self._warm[role], prompt, timeout)
        return one_shot(self.systems[role], prompt, self.model(role), timeout,
                        thinking=int(self.tcfg.get("thinking_tokens") or 0) if role not in WARM else 0,
                        tag=f"trainer_{role}")

    @staticmethod
    def _ask_warm(backend: ClaudeCLIBackend, prompt: str, timeout: float) -> str:
        done = threading.Event()
        out: dict = {}

        def on_done(_rid, text, err):
            out["text"], out["err"] = text, err
            done.set()
        backend.ask(prompt, lambda *_: None, on_done)
        if not done.wait(timeout):
            raise JobError(f"no reply in {timeout:.0f}s")
        if out.get("err"):
            raise JobError(out["err"])
        return out.get("text", "")

    def _api(self, role: str, prompt: str, timeout: float) -> str:
        import anthropic
        client = anthropic.Anthropic(timeout=timeout)
        model = self.tcfg.get("anthropic_model") or self.llm_cfg.anthropic_model
        r = client.messages.create(model=model, max_tokens=8000,
                                   system=[{"type": "text", "text": self.systems[role],
                                            "cache_control": {"type": "ephemeral"}}],
                                   messages=[{"role": "user", "content": prompt}])
        return "".join(b.text for b in r.content if getattr(b, "type", "") == "text")

    def close(self) -> None:
        self._closed = True
        for b in self._warm.values():
            try:
                b.close()
            except Exception:
                pass


def one_shot(system: str, prompt: str, model: str = "sonnet", timeout: float = 240, thinking: int = 0,
             tag: str = "trainer") -> str:
    """One `claude -p` call (no warm session): the prompt on stdin, the reply's text back."""
    exe = shutil.which("claude")
    if not exe:
        raise JobError("the claude CLI isn't installed")
    sp = DATA / "tmp" / f"{tag}_oneshot_system.md"
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(system, encoding="utf-8")
    env = dict(os.environ, MAX_THINKING_TOKENS=str(thinking))
    try:
        r = subprocess.run([exe, "-p", "--output-format", "text", "--model", model, "--tools", "",
                            "--no-session-persistence", "--strict-mcp-config", "--setting-sources", "",
                            "--system-prompt-file", str(sp)],
                           input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=timeout,
                           cwd=str(sp.parent), env=env, creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise JobError(f"no reply in {timeout:.0f}s")
    if r.returncode != 0:
        raise JobError((r.stdout + r.stderr).strip()[:400] or f"claude exited with {r.returncode}")
    return r.stdout
