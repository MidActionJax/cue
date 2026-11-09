"""The floating glass panels' native settings, per OS.

Every Cue window (the main panel and the trainer's) is frameless, always on top, never takes
keyboard focus away from the call app, and is left out of screen sharing.

Windows: WS_EX_NOACTIVATE + taskbar button, SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE),
DWM acrylic blur. macOS: a non-activating NSPanel (Qt.Tool windows are NSPanels) that stays up
when another app is active, floats over full-screen apps on every Space, and has
sharingType = none. Note: from macOS 15, apps that capture the screen with ScreenCaptureKit may
still see windows marked sharingType = none.
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import Qt

from . import IS_MAC, IS_WIN

log = logging.getLogger(__name__)


def base_flags(taskbar: bool) -> Qt.WindowType:
    """Qt window flags for a glass panel."""
    flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus
    if IS_MAC or not taskbar:
        flags |= Qt.WindowType.Tool   # macOS: an NSPanel, which can be non-activating
    return flags


def prepare_widget(widget) -> None:
    """Before the window exists (call from __init__)."""
    widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    widget.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    if IS_MAC:
        widget.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)   # don't hide when Cue isn't active


def on_show(widget, cfg, taskbar: bool, tint, alpha: int, acrylic_done: bool) -> bool:
    """Once the native window exists (call from showEvent). Returns whether a native blur is on."""
    try:
        if IS_WIN:
            return _win(widget, cfg, taskbar, tint, alpha, acrylic_done)
        if IS_MAC:
            _mac(widget, cfg)
    except Exception as e:
        log.warning("native window setup failed: %s", e)
    return acrylic_done


# --------------------------------------------------------------------------- Windows
def _win(widget, cfg, taskbar, tint, alpha, acrylic_done) -> bool:
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    GWL_EXSTYLE, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE, WS_EX_APPWINDOW = -20, 0x80, 0x08000000, 0x40000
    WDA_EXCLUDEFROMCAPTURE = 0x11
    hwnd = int(widget.winId())
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    # never steal focus; with show_in_taskbar Cue gets a taskbar button with its icon
    ex |= WS_EX_NOACTIVATE
    ex = (ex | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW if taskbar else ex | WS_EX_TOOLWINDOW
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
    if cfg.get("hide_from_screen_share", True):
        user32.SetWindowDisplayAffinity(wintypes.HWND(hwnd), WDA_EXCLUDEFROMCAPTURE)
    if cfg.get("acrylic", True) and not acrylic_done:
        return _win_acrylic(hwnd, tint, alpha)
    return acrylic_done


def _win_acrylic(hwnd: int, rgb, alpha: int) -> bool:
    """Native frosted-glass blur behind the window (SetWindowCompositionAttribute)."""
    import ctypes
    from ctypes import wintypes

    class ACCENT(ctypes.Structure):
        _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                    ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_int)]

    class DATA(ctypes.Structure):
        _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.c_void_p), ("SizeOfData", ctypes.c_size_t)]

    try:
        accent = ACCENT()
        accent.AccentState = 4  # ACCENT_ENABLE_ACRYLICBLURBEHIND
        accent.AccentFlags = 2
        accent.GradientColor = (alpha << 24) | (rgb.blue() << 16) | (rgb.green() << 8) | rgb.red()
        data = DATA()
        data.Attribute = 19  # WCA_ACCENT_POLICY
        data.SizeOfData = ctypes.sizeof(accent)
        data.Data = ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p)
        ok = ctypes.windll.user32.SetWindowCompositionAttribute(wintypes.HWND(hwnd), ctypes.byref(data))
        pref = ctypes.c_int(2)  # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(wintypes.HWND(hwnd), 33, ctypes.byref(pref), ctypes.sizeof(pref))
        return bool(ok)
    except Exception as e:
        log.warning("acrylic unavailable: %s", e)
        return False


# --------------------------------------------------------------------------- macOS
NSWindowStyleMaskNonactivatingPanel = 1 << 7
NSWindowCollectionBehaviorCanJoinAllSpaces = 1 << 0
NSWindowCollectionBehaviorStationary = 1 << 4
NSWindowCollectionBehaviorIgnoresCycle = 1 << 6
NSWindowCollectionBehaviorFullScreenAuxiliary = 1 << 8
NSWindowSharingNone = 0
NSFloatingWindowLevel = 3


def _mac(widget, cfg) -> None:
    from PyQt6.QtGui import QGuiApplication
    if QGuiApplication.platformName() != "cocoa":
        return   # e.g. the offscreen platform (tests, CI): winId() isn't an NSView there
    import objc
    view = objc.objc_object(c_void_p=int(widget.winId()))
    win = view.window()
    if win is None:
        return
    # clicking the panel never activates Cue, so Zoom / Teams keeps keyboard focus
    if win.isKindOfClass_(objc.lookUpClass("NSPanel")):
        win.setStyleMask_(win.styleMask() | NSWindowStyleMaskNonactivatingPanel)
        win.setBecomesKeyOnlyIfNeeded_(True)
        win.setFloatingPanel_(True)
    win.setHidesOnDeactivate_(False)
    win.setLevel_(NSFloatingWindowLevel)
    # over a full-screen meeting, on every desktop Space, and out of Cmd+` window cycling
    win.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary
                               | NSWindowCollectionBehaviorStationary | NSWindowCollectionBehaviorIgnoresCycle)
    if cfg.get("hide_from_screen_share", True):
        win.setSharingType_(NSWindowSharingNone)
    win.setHasShadow_(True)
