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

DEMO = {
    "work": [
        ("them", "Okay, thanks everyone. Let's go around quickly."),
        ("them", "{name}, where are we with the data pipeline? Is the dashboard running on its own now?"),
    ],
    "sbir": [
        ("them", "Thanks for making the time. I've read a little about what you do."),
        ("them", "So {name}, what do you charge for something like this?"),
        ("them", "And how do you handle our proprietary data if you use AI tools?"),
        ("them", "Also, could we put you on our website as a partner?"),
    ],
}


_handler_ref = None


def _splash():
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QPixmap
    from PyQt6.QtWidgets import QSplashScreen
    pm = QPixmap(str(config.ASSETS / "splash.png"))
    if pm.isNull():
        return None
    pm.setDevicePixelRatio(2.0)  # rendered at 2x for crisp text
    s = QSplashScreen(pm, Qt.WindowType.WindowStaysOnTopHint)
    s.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)  # rounded corners
    s.show()
    QApplication.processEvents()
    return s


_crash_file = None


def _log_crashes(logs) -> None:
    """The compiled app has no console, so an uncaught error would vanish. Send Python errors
    (main thread and worker threads) to the app log, and native crashes to logs/crash.log."""
    global _crash_file
    import faulthandler
    import threading
    log = logging.getLogger("crash")
    sys.excepthook = lambda t, e, tb: log.critical("uncaught error", exc_info=(t, e, tb))
    threading.excepthook = lambda a: log.critical("uncaught error in thread %s", a.thread and a.thread.name,
                                                  exc_info=(a.exc_type, a.exc_value, a.exc_traceback))
    try:
        _crash_file = open(logs / "crash.log", "a", encoding="utf-8")
        faulthandler.enable(_crash_file)
    except OSError:
        pass


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
                _hard_exit(0)
        return False

    _handler_ref = handler  # must stay referenced or ctypes frees it
    ctypes.windll.kernel32.SetConsoleCtrlHandler(handler, True)


def main():
    ap = argparse.ArgumentParser(prog="cue")
    ap.add_argument("--devices", action="store_true", help="list audio devices and exit")
    ap.add_argument("--demo", action="store_true", help="no audio: inject a fake question to test overlay + LLM")
    ap.add_argument("--mode", choices=["work", "interview", "sbir"], help="skip the start-screen chooser")
    ap.add_argument("--quit", action="store_true", help="end the running Cue's call (notes saved) and exit")
    ap.add_argument("--demo-script", help=argparse.SUPPRESS)  # testing: a text file, one 'them' line per row
    args = ap.parse_args()

    logs = config.DATA / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname).1s %(name)s: %(message)s",
                        datefmt="%H:%M:%S", handlers=[
                            logging.FileHandler(logs / f"app-{time.strftime('%Y-%m-%d')}.log", encoding="utf-8"),
                            # the compiled app has no console
                            *([logging.StreamHandler()] if sys.stderr else [])])
    _log_crashes(logs)
    if args.devices:
        from .audio import list_devices
        list_devices()
        return

    cfg = config.load()
    # its own taskbar identity (icon + grouping) instead of "python.exe"
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MidActionJax.Cue")
    app = QApplication(sys.argv)
    from .instance import InstanceServer, send_to_running
    if send_to_running("quit" if args.quit else "show"):
        return  # the running Cue handles it; don't start a second copy
    if args.quit:
        return  # nothing running
    splash = _splash()
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Cue")
    from PyQt6.QtGui import QIcon
    app.setWindowIcon(QIcon(str(config.ASSETS / "cue.ico")))
    from .app import Controller
    ctl = Controller(cfg, audio=not args.demo, mode=args.mode)
    if splash:
        QTimer.singleShot(1200, lambda: splash.finish(ctl.overlay))
    _on_console_close(ctl.shutdown)
    instance = InstanceServer()
    def on_message(msg: str):
        logging.getLogger(__name__).info("another launch asked: %s", msg or "show")
        ctl.quit() if msg == "quit" else ctl.bring_back()
    instance.message.connect(on_message)
    app.aboutToQuit.connect(ctl.shutdown)  # sign-out / shutdown: still save the call

    if args.demo:
        from .audio import Utterance
        import numpy as np

        lines = DEMO.get(args.mode or "", DEMO["work"])
        if args.demo_script:
            rows = open(args.demo_script, encoding="utf-8").read().splitlines()
            lines = [("them", r.strip()) for r in rows if r.strip()]

        def inject(i=0):  # one line at a time, like a real call (each question gets its own answer)
            src, text = lines[i]
            t = time.time() - 3
            ctl.bridge.text.emit(Utterance(src, i, np.zeros(1), t, t + 3, True), text.format(name=cfg.user.name))
            if i + 1 < len(lines):
                QTimer.singleShot(12000 if "?" in text else 1500, lambda: inject(i + 1))
        QTimer.singleShot(6000, inject)

    code = app.exec()
    logging.getLogger(__name__).info("bye")
    _hard_exit(code)


def _hard_exit(code: int) -> None:
    """Everything is saved by now. Skip interpreter teardown (audio/STT threads are daemons) and
    DLL unload too: os._exit's ExitProcess can hang in the CUDA/ONNX libraries' unload handlers
    while a worker thread is mid-inference."""
    logging.shutdown()
    sys.stdout and sys.stdout.flush()
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    k32.TerminateProcess(k32.GetCurrentProcess(), code)
    os._exit(code)


if __name__ == "__main__":
    main()
