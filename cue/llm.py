"""Streaming LLM backends: Claude CLI (subscription), Anthropic API, Ollama (local).

Every backend exposes:
    configure(system_prompt)             (re)load context; may restart/warm up
    ask(user_prompt, on_token, on_done)  async, streams tokens; a new ask cancels the old one
    close()
on_token(req_id, text_so_far) and on_done(req_id, full_text, error_or_None) are called
from background threads.
"""
from __future__ import annotations

import itertools
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from typing import Callable

from .config import DATA

log = logging.getLogger(__name__)

TokenCb = Callable[[int, str], None]
DoneCb = Callable[[int, str, "str | None"], None]
_ids = itertools.count(1)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# One context size for every local request: Ollama reloads the whole model when it changes.
NUM_CTX = 16384


class Backend:
    name = "?"

    def configure(self, system: str) -> None: ...
    def ask(self, user: str, on_token: TokenCb, on_done: DoneCb) -> int: ...
    def close(self) -> None: ...


# --------------------------------------------------------------------------- Claude CLI

class ClaudeCLIBackend(Backend):
    """Keeps one `claude -p` process alive in stream-json mode, so each answer only pays
    for the API round trip (no CLI startup). Uses the user's Claude subscription login."""

    name = "claude"
    MAX_TURNS = 30  # restart to keep the session context small

    def __init__(self, model: str):
        self.model = model
        self.exe = shutil.which("claude")
        self.proc: subprocess.Popen | None = None
        self.system = ""
        self._lock = threading.Lock()
        self._current: tuple[int, TokenCb, DoneCb] | None = None
        self._pending: tuple[int, str, TokenCb, DoneCb] | None = None
        self._busy = False
        self._text = ""
        self._turns = 0
        self._warm = threading.Event()
        self.last_error: str | None = None

    @staticmethod
    def available() -> bool:
        return shutil.which("claude") is not None

    def configure(self, system: str) -> None:
        self.system = system
        self._restart()

    def _restart(self) -> None:
        self.close()
        sp = DATA / "tmp" / "live_system_prompt.md"
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(self.system, encoding="utf-8")
        cwd = DATA / "tmp"
        args = [self.exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
                "--verbose", "--include-partial-messages", "--model", self.model, "--tools", "",
                "--no-session-persistence", "--strict-mcp-config", "--setting-sources", "",
                "--system-prompt-file", str(sp)]
        env = dict(os.environ, MAX_THINKING_TOKENS="0")  # thinking costs ~2s of first-token latency
        self.proc = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", bufsize=1, cwd=cwd, env=env, creationflags=_NO_WINDOW,
        )
        self._turns = 0
        self._busy = False
        self._current = None
        self._warm.clear()
        threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()
        # warm-up turn: primes the prompt cache with the (large) context and checks auth
        self._busy = True
        self._current = (0, lambda *_: None, self._warm_done)
        self._send("Reply with just OK.")

    def _warm_done(self, _rid, _text, err):
        self.last_error = err
        if err:
            log.error("Claude CLI warm-up failed: %s", err)
        else:
            log.info("Claude CLI ready (%s)", self.model)
        self._warm.set()

    def _send(self, text: str) -> None:
        msg = {"type": "user", "message": {"role": "user", "content": text}}
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        self._text = ""

    def ask(self, user: str, on_token: TokenCb, on_done: DoneCb) -> int:
        rid = next(_ids)
        with self._lock:
            if self.proc is None or self.proc.poll() is not None:
                self._restart()
            if self._busy:
                # cancel the in-flight answer; the new one is sent when it stops
                if self._current and self._current[0] != 0:
                    self._interrupt()
                self._pending = (rid, user, on_token, on_done)
            else:
                self._start(rid, user, on_token, on_done)
        return rid

    def _start(self, rid, user, on_token, on_done):
        self._busy = True
        self._current = (rid, on_token, on_done)
        self._send(user)

    def _interrupt(self) -> None:
        req = {"type": "control_request", "request_id": f"int-{time.time()}", "request": {"subtype": "interrupt"}}
        try:
            self.proc.stdin.write(json.dumps(req) + "\n")
            self.proc.stdin.flush()
        except OSError:
            pass
        self._current = (-1, lambda *_: None, lambda *_: None)

    def _reader(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            try:
                self._handle(json.loads(line))
            except json.JSONDecodeError:
                continue
            except Exception:
                log.exception("claude stream handler failed")
        # process ended
        cur = self._current
        if cur and proc is self.proc:
            cur[2](cur[0], self._text, "Claude CLI exited")

    def _handle(self, ev: dict) -> None:
        kind = ev.get("type")
        if kind == "stream_event":
            d = ev.get("event", {})
            if d.get("type") == "content_block_delta" and d.get("delta", {}).get("type") == "text_delta":
                self._text += d["delta"]["text"]
                cur = self._current
                if cur:
                    cur[1](cur[0], self._text)
        elif kind == "result":
            err = ev.get("result") if ev.get("is_error") else None
            with self._lock:
                cur, self._current, self._busy = self._current, None, False
                self._turns += 1
                if cur:
                    cur[2](cur[0], self._text, err)
                if self._pending:
                    p, self._pending = self._pending, None
                    if self._turns >= self.MAX_TURNS:
                        threading.Thread(target=self._restart_then, args=(p,), daemon=True).start()
                    else:
                        self._start(*p)

    def _restart_then(self, p) -> None:
        with self._lock:
            self._restart()
        self._warm.wait(15)
        with self._lock:
            self._start(*p)

    def close(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=3)
            except Exception:
                self.proc.kill()
        self.proc = None


# --------------------------------------------------------------------------- Anthropic API

class AnthropicBackend(Backend):
    name = "anthropic"

    def __init__(self, model: str, max_tokens: int):
        import anthropic
        self.client = anthropic.Anthropic()
        self.model, self.max_tokens = model, max_tokens
        self.system = ""
        self._active = 0

    @staticmethod
    def available() -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY"))

    def configure(self, system: str) -> None:
        self.system = system

    def ask(self, user, on_token, on_done) -> int:
        rid = self._active = next(_ids)
        threading.Thread(target=self._run, args=(rid, user, on_token, on_done), daemon=True).start()
        return rid

    def _run(self, rid, user, on_token, on_done):
        text = ""
        try:
            with self.client.messages.stream(
                model=self.model, max_tokens=self.max_tokens,
                system=[{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
            ) as stream:
                for chunk in stream.text_stream:
                    if rid != self._active:
                        return
                    text += chunk
                    on_token(rid, text)
            on_done(rid, text, None)
        except Exception as e:
            on_done(rid, text, str(e))


# --------------------------------------------------------------------------- Ollama

class OllamaBackend(Backend):
    name = "ollama"

    def __init__(self, model: str, url: str, max_tokens: int, max_context_chars: int):
        self.model, self.url, self.max_tokens = model, url.rstrip("/"), max_tokens
        self.max_context_chars = max_context_chars
        self.system = ""
        self._active = 0

    @staticmethod
    def available(url: str = "http://localhost:11434") -> bool:
        import httpx
        try:
            return httpx.get(url.rstrip("/") + "/api/tags", timeout=1.5).status_code == 200
        except Exception:
            return False

    @staticmethod
    def start_if_installed(url: str = "http://localhost:11434", wait_s: float = 20) -> bool:
        """Ollama installed but not running (closed, crashed, not started at login): start its
        server in the background so the mid-call fallback exists. True once it answers."""
        if OllamaBackend.available(url):
            return True
        if "localhost" not in url and "127.0.0.1" not in url:
            return False
        exe = shutil.which("ollama") or os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe")
        if not os.path.isfile(exe):
            return False
        log.info("Ollama isn't running; starting it for the local fallback")
        flags = _NO_WINDOW | getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        breakaway = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB: keep running after Cue exits
        for extra in (breakaway, 0):  # breakaway is refused inside some job objects: then plain detached
            try:
                subprocess.Popen([exe, "serve"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, creationflags=flags | extra, close_fds=True)
                break
            except OSError as e:
                if not extra:
                    log.warning("couldn't start Ollama: %s", e)
                    return False
        end = time.time() + wait_s
        while time.time() < end:
            if OllamaBackend.available(url):
                log.info("Ollama started")
                return True
            time.sleep(0.5)
        return False

    def configure(self, system: str) -> None:
        if len(system) > self.max_context_chars:
            system = system[: self.max_context_chars] + "\n[context truncated]"
        self.system = system
        # load the model + evaluate the system prompt now so the first real answer is fast
        threading.Thread(target=self._chat, args=(0, "Reply with just OK.", None, None, 1), daemon=True).start()

    def ask(self, user, on_token, on_done) -> int:
        rid = self._active = next(_ids)
        threading.Thread(target=self._chat, args=(rid, user, on_token, on_done), daemon=True).start()
        return rid

    def _chat(self, rid, user, on_token, on_done, num_predict=None):
        import httpx
        body = {
            "model": self.model, "stream": True, "keep_alive": "60m",
            "messages": [{"role": "system", "content": self.system}, {"role": "user", "content": user}],
            "options": {"num_ctx": NUM_CTX, "temperature": 0.3, "num_predict": num_predict or self.max_tokens},
        }
        text = ""
        try:
            with httpx.stream("POST", self.url + "/api/chat", json=body, timeout=120) as r:
                for line in r.iter_lines():
                    if not line:
                        continue
                    if rid and rid != self._active:
                        return
                    d = json.loads(line)
                    text += d.get("message", {}).get("content", "")
                    if on_token and text:
                        on_token(rid, text)
                    if d.get("done"):
                        break
            if on_done:
                on_done(rid, text, None)
        except Exception as e:
            log.error("ollama: %s", e)
            if on_done:
                on_done(rid, text, str(e))


def local_chat(prompt: str, system: str, model: str, url: str, timeout: int = 180) -> str:
    """One-shot, non-streaming call to the local model (background jobs: live notes, classifiers)."""
    import httpx
    r = httpx.post(url.rstrip("/") + "/api/chat", timeout=timeout, json={
        "model": model, "stream": False, "keep_alive": "60m",
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "options": {"num_ctx": NUM_CTX, "temperature": 0.2},
    })
    r.raise_for_status()
    return r.json()["message"]["content"]


def make_local(cfg) -> OllamaBackend:
    return OllamaBackend(cfg.ollama_model, cfg.ollama_url, cfg.max_tokens, cfg.ollama_context_chars)


def make_backend(cfg) -> Backend:
    choice = cfg.backend
    if choice == "auto":
        if AnthropicBackend.available():
            choice = "anthropic"
        elif ClaudeCLIBackend.available():
            choice = "claude_cli"
        else:
            choice = "ollama"
    if choice == "anthropic":
        return AnthropicBackend(cfg.anthropic_model, cfg.max_tokens)
    if choice == "claude_cli":
        return ClaudeCLIBackend(cfg.claude_cli_model)
    return OllamaBackend(cfg.ollama_model, cfg.ollama_url, cfg.max_tokens, cfg.ollama_context_chars)
