"""python -m cue [--devices] [--demo]"""
import argparse
import os
import logging
import sys
import time

from .cuda_setup import add_cuda_dlls

add_cuda_dlls()

from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from . import config  # noqa: E402

DEMO = [
    ("them", "Okay, thanks everyone. Let's go around quickly."),
    ("them", "{name}, where are we with the data pipeline? Is the dashboard running on its own now?"),
]


_handler_ref = None


def _on_console_close(save) -> None:
    """Closing the console window (its X) kills the process ~5 s later without any Qt event.
    Catch it so the call's notes and learning still get saved."""
    global _handler_ref
    import ctypes
    from ctypes import wintypes

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
    def handler(event):
        if event in (0, 1, 2, 5, 6):  # Ctrl+C, Ctrl+Break, close, logoff, shutdown
            logging.getLogger(__name__).info("console closing (event %d) — saving the call", event)
            try:
                save()
            finally:
                os._exit(0)
        return False

    _handler_ref = handler  # must stay referenced or ctypes frees it
    ctypes.windll.kernel32.SetConsoleCtrlHandler(handler, True)


def main():
    ap = argparse.ArgumentParser(prog="cue")
    ap.add_argument("--devices", action="store_true", help="list audio devices and exit")
    ap.add_argument("--demo", action="store_true", help="no audio: inject a fake question to test overlay + LLM")
    ap.add_argument("--mode", choices=["work", "interview"], help="skip the Work/Interview chooser")
    args = ap.parse_args()

    logs = config.DATA / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname).1s %(name)s: %(message)s",
                        datefmt="%H:%M:%S", handlers=[
                            logging.StreamHandler(),
                            logging.FileHandler(logs / f"app-{time.strftime('%Y-%m-%d')}.log", encoding="utf-8")])
    if args.devices:
        from .audio import list_devices
        list_devices()
        return

    cfg = config.load()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Cue")
    from PyQt6.QtGui import QIcon
    app.setWindowIcon(QIcon(str(config.ROOT / "assets" / "cue.ico")))
    from .app import Controller
    ctl = Controller(cfg, audio=not args.demo, mode=args.mode)
    _on_console_close(ctl.shutdown)

    if args.demo:
        from .audio import Utterance
        import numpy as np

        def inject():
            for i, (src, text) in enumerate(DEMO):
                t = time.time() - 10 + i * 4
                ctl.bridge.text.emit(Utterance(src, i, np.zeros(1), t, t + 3, True), text.format(name=cfg.user.name))
        QTimer.singleShot(6000, inject)

    code = app.exec()
    sys.stdout.flush()
    os._exit(code)  # skip interpreter teardown: audio/STT threads are daemons and native libs complain


if __name__ == "__main__":
    main()
