"""One Cue at a time.

A second launch hands its request to the running instance instead of starting a duplicate
(two copies would double the hotkeys and the audio capture):
    Cue.exe          -> the running Cue pops its panel back up
    Cue.exe --quit   -> the running Cue ends the call (notes saved) and exits
"""
from __future__ import annotations

import getpass

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

NAME = f"cue-{getpass.getuser()}"


def send_to_running(message: str, timeout_ms: int = 800) -> bool:
    """True if a Cue is already running (and got the message)."""
    sock = QLocalSocket()
    sock.connectToServer(NAME)
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write(message.encode())
    sock.flush()
    sock.waitForBytesWritten(timeout_ms)
    sock.disconnectFromServer()
    return True


class InstanceServer(QObject):
    message = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.server = QLocalServer(self)
        QLocalServer.removeServer(NAME)  # clean up after a crash
        self.server.listen(NAME)
        self.server.newConnection.connect(self._on_connection)

    def _on_connection(self):
        sock = self.server.nextPendingConnection()
        if sock is None:
            return
        sock.readyRead.connect(lambda s=sock: self.message.emit(bytes(s.readAll()).decode(errors="ignore").strip()))
        sock.disconnected.connect(sock.deleteLater)
