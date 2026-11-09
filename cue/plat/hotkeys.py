"""Global hotkeys ("tab", "shift+tab", "ctrl+alt+m") that never swallow the key.

Windows: the `keyboard` library. macOS: one pynput listener (a Quartz event tap), which needs
the Accessibility permission (System Settings > Privacy & Security > Accessibility). Keys are
matched by physical key code there, because Option changes the character ("option+m" types µ).
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
        self._mac: dict[int, tuple[frozenset[str], str, Callable]] = {}
        self._lock = threading.Lock()
        self._listener = None
        self._down: set[str] = set()

    # ------------------------------------------------------------------ public
    def add(self, combo: str, callback: Callable[[], None]) -> int:
        hid = next(self._ids)
        if IS_MAC:
            mods, key = parse(combo)
            with self._lock:
                self._mac[hid] = (mods, key, callback)
            self._ensure_listener()
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
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    # ------------------------------------------------------------------ macOS
    def _ensure_listener(self) -> None:
        if self._listener is not None:
            return
        from pynput import keyboard as kb
        self._kb = kb
        self._listener = kb.Listener(on_press=self._press, on_release=self._release)
        self._listener.daemon = True
        self._listener.start()
        if not input_monitoring_allowed(prompt=True):
            log.warning("hotkeys need the Accessibility permission: System Settings > Privacy & Security > "
                        "Accessibility > turn on Cue, then restart Cue")

    def _mod(self, key) -> str | None:
        K = self._kb.Key
        for name, members in (("ctrl", (K.ctrl, K.ctrl_l, K.ctrl_r)), ("alt", (K.alt, K.alt_l, K.alt_r)),
                              ("shift", (K.shift, K.shift_l, K.shift_r)), ("cmd", (K.cmd, K.cmd_l, K.cmd_r))):
            if key in members:
                return name
        return None

    def _name(self, key) -> str | None:
        """Our name for a non-modifier key, from its physical key code."""
        vk = getattr(key, "vk", None)
        if vk is None and hasattr(key, "value"):
            vk = getattr(key.value, "vk", None)
        if vk is not None:
            for name, code in MAC_VK.items():
                if code == vk:
                    return name
        char = getattr(key, "char", None)
        return char.lower() if char else getattr(key, "name", None)

    def _press(self, key) -> None:
        try:
            m = self._mod(key)
            if m:
                self._down.add(m)
                return
            name = self._name(key)
            if not name:
                return
            mods = frozenset(self._down)
            with self._lock:
                hits = [cb for (want_mods, k, cb) in self._mac.values() if k == name and want_mods == mods]
            for cb in hits:
                cb()
        except Exception:
            log.exception("hotkey handler failed")

    def _release(self, key) -> None:
        m = self._mod(key)
        if m:
            self._down.discard(m)


def input_monitoring_allowed(prompt: bool = False) -> bool:
    """macOS: may this app see keystrokes system-wide (Accessibility)? prompt=True also shows
    macOS's own dialog that opens the right settings page. Always True elsewhere."""
    if not IS_MAC:
        return True
    try:
        import ApplicationServices as AS
        if prompt:
            return bool(AS.AXIsProcessTrustedWithOptions({AS.kAXTrustedCheckOptionPrompt: True}))
        return bool(AS.AXIsProcessTrusted())
    except Exception as e:
        log.debug("accessibility check: %s", e)
        return True
