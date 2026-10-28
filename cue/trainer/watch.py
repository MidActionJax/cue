"""Seeing your code as you write it.

FileWatcher: polls your solution file (every save; with VS Code's auto-save that's about once a
second). Polling rather than change notifications: editors save by replacing the file, OneDrive
folders swallow events, and a stat every 400 ms costs nothing.

Bridge: a tiny HTTP server on 127.0.0.1 for the Cue Trainer VS Code extension, which sends the
unsaved buffer plus VS Code's own diagnostics on every edit, and fetches Cue's line marks to draw
them in the editor. The port and a random token are written to %LOCALAPPDATA%\\Cue\\trainer_bridge.json;
requests without the token are refused.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

log = logging.getLogger(__name__)
BRIDGE_FILE = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Cue" / "trainer_bridge.json"


class FileWatcher(QObject):
    changed = pyqtSignal(str)          # the file's new text

    def __init__(self, path: str, interval_ms: int = 400):
        super().__init__()
        self.path = Path(path)
        self._sig = None
        self._text = None
        self._timer = QTimer(self, interval=interval_ms, timeout=self._poll)

    def start(self):
        self._poll()
        self._timer.start()

    def stop(self):
        self._timer.stop()

    def _poll(self):
        try:
            st = self.path.stat()
        except OSError:
            return
        sig = (st.st_mtime_ns, st.st_size)
        if sig == self._sig:
            return
        self._sig = sig
        try:
            text = self.path.read_text(encoding="utf-8-sig", errors="replace").replace("\r\n", "\n")
        except OSError:
            self._sig = None   # mid-save: try again on the next tick
            return
        if text != self._text:
            self._text = text
            self.changed.emit(text)


class Bridge(QObject):
    buffer = pyqtSignal(str, str, list)    # path, text, diagnostics [{"line", "message", "severity"}]
    command = pyqtSignal(str)              # run_tests | debug

    def __init__(self, get_session: callable):
        super().__init__()
        self.token = secrets.token_hex(16)
        self.get_session = get_session     # -> {"path": ..., "marks": {...}, "version": n} (any thread)
        self.server: ThreadingHTTPServer | None = None

    def start(self) -> int:
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _ok(self) -> bool:
                if self.headers.get("X-Cue-Token") != bridge.token:
                    self.send_response(403)
                    self.end_headers()
                    return False
                return True

            def _json(self, obj, code=200):
                body = json.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if not self._ok():
                    return
                if self.path.startswith("/session"):
                    self._json(bridge.get_session())
                else:
                    self._json({"error": "not found"}, 404)

            def do_POST(self):
                if not self._ok():
                    return
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    data = json.loads(self.rfile.read(min(n, 4_000_000)) or b"{}")
                except ValueError:
                    self._json({"error": "bad json"}, 400)
                    return
                if self.path.startswith("/buffer"):
                    bridge.buffer.emit(str(data.get("path", "")), str(data.get("text", "")),
                                       list(data.get("diagnostics") or []))
                    self._json({"ok": True})
                elif self.path.startswith("/command"):
                    bridge.command.emit(str(data.get("cmd", "")))
                    self._json({"ok": True})
                else:
                    self._json({"error": "not found"}, 404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True, name="trainer-bridge").start()
        try:
            BRIDGE_FILE.parent.mkdir(parents=True, exist_ok=True)
            BRIDGE_FILE.write_text(json.dumps({"port": port, "token": self.token, "pid": os.getpid()}), encoding="utf-8")
        except OSError as e:
            log.warning("couldn't write the bridge file: %s", e)
        log.info("trainer bridge on 127.0.0.1:%d", port)
        return port

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        try:
            if BRIDGE_FILE.exists() and json.loads(BRIDGE_FILE.read_text(encoding="utf-8")).get("token") == self.token:
                BRIDGE_FILE.unlink()
        except (OSError, ValueError):
            pass
