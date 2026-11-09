"""Global hotkeys ("tab", "shift+tab", "ctrl+alt+m") that never swallow the key.

Windows: the `keyboard` library. macOS: one listen-only Quartz event tap on its own thread,
which needs the Input Monitoring permission (System Settings > Privacy & Security > Input
Monitoring). Keys are matched by physical key code and modifier flags there, because Option
changes the character ("option+m" types µ). Only those two numbers are read from each event:
asking macOS for the typed character off the main thread crashes on recent macOS versions.
"""
from __future__ import annotations

import itertools
import logging
import threading
from typing import Callable

from . import IS_MAC

log = logging.getLogger(__name__)

MODS = {"ctrl", "alt", "shift", "cmd"}
_ALIASES = {"control": "ctrl", "option": "alt", "opt": "alt", "command": "cmd", "super": "cmd", "win": "cmd",
            "escape": "esc", "return": "enter"}
# macOS virtual key codes (ANSI layout)
MAC_VK = {"a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12,
          "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23,
          "=": 24, "9": 25, "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34,
          "p": 35, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46,
          ".": 47, "tab": 48, "space": 49, "`": 50, "enter": 36, "esc": 53, "delete": 51,
          "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100, "f9": 101,
          "f10": 109, "f11": 103, "f12": 111}


def parse(combo: str) -> tuple[frozenset[str], str]:
    """'ctrl+alt+m' -> ({'ctrl','alt'}, 'm'). The last non-modifier part is the key."""
    parts = [_ALIASES.get(p.strip().lower(), p.strip().lower()) for p in combo.split("+") if p.strip()]
    mods = frozenset(p for p in parts if p in MODS)
    keys = [p for p in parts if p not in MODS]
    if len(keys) != 1:
        raise ValueError(f"hotkey {combo!r}: need exactly one non-modifier key")
    return mods, keys[0]


class Hotkeys:
    """add(combo, callback) -> handle; remove(handle). Callbacks run on a background thread."""

    def __init__(self):
        self._ids = itertools.count(1)
        self._win: dict[int, object] = {}
        self._mac: dict[int, tuple[frozenset[str], int, Callable]] = {}
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._loop = None
        self._tap = None

    # ------------------------------------------------------------------ public
    def add(self, combo: str, callback: Callable[[], None]) -> int:
        hid = next(self._ids)
        if IS_MAC:
            mods, key = parse(combo)
            if key not in MAC_VK:
                raise ValueError(f"hotkey {combo!r}: unknown key {key!r} on macOS")
            with self._lock:
                self._mac[hid] = (mods, MAC_VK[key], callback)
            self._ensure_tap()
        else:
            import keyboard
            # suppress=False: the key still reaches whatever app you're in
            self._win[hid] = keyboard.add_hotkey(combo, callback, suppress=False)
        return hid

    def remove(self, hid: int) -> None:
        if IS_MAC:
            with self._lock:
                self._mac.pop(hid, None)
        elif hid in self._win:
            import keyboard
            try:
                keyboard.remove_hotkey(self._win.pop(hid))
            except (KeyError, ValueError):
                pass

    def stop(self) -> None:
        if self._loop is not None:
            import Quartz as Q
            Q.CFRunLoopStop(self._loop)
            self._loop = None

    # ------------------------------------------------------------------ macOS
    def _ensure_tap(self) -> None:
        if self._thread is not None:
            return
        if not input_monitoring_allowed(prompt=True):
            log.warning(PERMISSION_HELP)
        self._thread = threading.Thread(target=self._run_tap, name="hotkeys", daemon=True)
        self._thread.start()

    def _run_tap(self) -> None:
        import Quartz as Q
        mask = Q.CGEventMaskBit(Q.kCGEventKeyDown)

        def on_event(proxy, etype, event, refcon):
            try:
                if etype in (Q.kCGEventTapDisabledByTimeout, Q.kCGEventTapDisabledByUserInput):
                    Q.CGEventTapEnable(self._tap, True)   # macOS turns a slow tap off; turn it back on
                elif etype == Q.kCGEventKeyDown and not Q.CGEventGetIntegerValueField(event, Q.kCGKeyboardEventAutorepeat):
                    self._dispatch(Q.CGEventGetIntegerValueField(event, Q.kCGKeyboardEventKeycode),
                                   Q.CGEventGetFlags(event))
            except Exception:
                log.exception("hotkey event")
            return event

        self._tap = Q.CGEventTapCreate(Q.kCGSessionEventTap, Q.kCGHeadInsertEventTap,
                                       Q.kCGEventTapOptionListenOnly, mask, on_event, None)
        if self._tap is None:
            log.warning("hotkeys are off: %s", PERMISSION_HELP)
            self._thread = None
            return
        source = Q.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        self._loop = Q.CFRunLoopGetCurrent()
        Q.CFRunLoopAddSource(self._loop, source, Q.kCFRunLoopCommonModes)
        Q.CGEventTapEnable(self._tap, True)
        log.info("hotkeys: listening (%d)", len(self._mac))
        Q.CFRunLoopRun()

    def _dispatch(self, keycode: int, flags: int) -> None:
        mods = mods_from_flags(flags)
        with self._lock:
            hits = [cb for (want, code, cb) in self._mac.values() if code == keycode and want == mods]
        for cb in hits:   # off the tap's thread: a slow callback would get the tap switched off
            threading.Thread(target=_safe_call, args=(cb,), daemon=True).start()


# CGEventFlags bits for the modifiers
MAC_FLAGS = {"shift": 0x20000, "ctrl": 0x40000, "alt": 0x80000, "cmd": 0x100000}
PERMISSION_HELP = ("Cue's shortcut keys need the Input Monitoring permission: System Settings > Privacy & "
                   "Security > Input Monitoring > turn on Cue, then quit and reopen Cue")


def mods_from_flags(flags: int) -> frozenset[str]:
    return frozenset(name for name, bit in MAC_FLAGS.items() if flags & bit)


def _safe_call(cb: Callable[[], None]) -> None:
    try:
        cb()
    except Exception:
        log.exception("hotkey handler failed")


def input_monitoring_allowed(prompt: bool = False) -> bool:
    """macOS: may this app see keystrokes system-wide (Input Monitoring)? prompt=True also shows
    macOS's own dialog the first time. Always True elsewhere."""
    if not IS_MAC:
        return True
    try:
        import Quartz as Q
        if prompt:
            return bool(Q.CGRequestListenEventAccess())
        return bool(Q.CGPreflightListenEventAccess())
    except Exception as e:
        log.debug("input monitoring check: %s", e)
        return True
