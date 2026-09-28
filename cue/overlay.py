"""Glassmorphic always-on-top panel with a Mac-style title bar (PyQt6 + Win32).

The panel never takes keyboard focus (WS_EX_NOACTIVATE), so clicking it or dragging it
around doesn't pull focus away from the call app.

    ●●●  Cue · Listening           [ Work | Interview ]  name   ⚡ Ask
    ─────────────────────────────────────────────────────────────────────────
    body: mode chooser → home (last-week recap) → live answer → back to home
    (green dot) Transcript | Notes pane
"""
from __future__ import annotations

import ctypes
import html
import json
import logging
from ctypes import wintypes
from urllib.parse import unquote

from PyQt6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton, QTextBrowser,
                             QVBoxLayout, QWidget)

from .config import ASSETS, DATA

log = logging.getLogger(__name__)

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_APPWINDOW = 0x00040000
WDA_EXCLUDEFROMCAPTURE = 0x11
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2
UI_STATE = DATA / "ui_state.json"

TEXT = "#F4F7FB"
MUTED = "rgba(210,220,235,150)"
ACCENT = "#8fb4ff"


class _ACCENT_POLICY(ctypes.Structure):
    _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_int)]


class _WINCOMPATTRDATA(ctypes.Structure):
    _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.c_void_p), ("SizeOfData", ctypes.c_size_t)]


def _enable_acrylic(hwnd: int, rgb: QColor, alpha: int) -> bool:
    """Native frosted-glass blur behind the window (SetWindowCompositionAttribute)."""
    try:
        accent = _ACCENT_POLICY()
        accent.AccentState = 4  # ACCENT_ENABLE_ACRYLICBLURBEHIND
        accent.AccentFlags = 2
        accent.GradientColor = (alpha << 24) | (rgb.blue() << 16) | (rgb.green() << 8) | rgb.red()
        data = _WINCOMPATTRDATA()
        data.Attribute = 19  # WCA_ACCENT_POLICY
        data.SizeOfData = ctypes.sizeof(accent)
        data.Data = ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p)
        ok = user32.SetWindowCompositionAttribute(wintypes.HWND(hwnd), ctypes.byref(data))
        pref = ctypes.c_int(DWMWCP_ROUND)
        dwmapi.DwmSetWindowAttribute(wintypes.HWND(hwnd), DWMWA_WINDOW_CORNER_PREFERENCE,
                                     ctypes.byref(pref), ctypes.sizeof(pref))
        return bool(ok)
    except Exception as e:
        log.warning("acrylic unavailable: %s", e)
        return False


# ---------------------------------------------------------------------------- widgets

class Dot(QWidget):
    """Mac traffic-light button: shows its glyph while the title bar is hovered."""
    clicked = pyqtSignal()

    def __init__(self, color: str, glyph: str, tip: str):
        super().__init__()
        self.color, self.glyph = QColor(color), glyph
        self.hot = False
        self.setFixedSize(14, 14)
        self.setToolTip(tip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(self.color)
        p.setPen(QPen(self.color.darker(125), 0.8))
        p.drawEllipse(QRectF(0.5, 0.5, 13, 13))
        if self.hot:
            p.setPen(QPen(QColor(60, 20, 10, 190), 1.4))
            c = 7.0
            if self.glyph == "x":
                p.drawLine(QRectF(4, 4, 6, 6).topLeft(), QRectF(4, 4, 6, 6).bottomRight())
                p.drawLine(QRectF(4, 4, 6, 6).topRight(), QRectF(4, 4, 6, 6).bottomLeft())
            elif self.glyph == "-":
                p.drawLine(QPoint(4, int(c)), QPoint(10, int(c)))
            else:
                p.drawLine(QPoint(4, int(c)), QPoint(10, int(c)))
                p.drawLine(QPoint(int(c), 4), QPoint(int(c), 10))

    def mouseReleaseEvent(self, e):
        if self.rect().contains(e.position().toPoint()):
            self.clicked.emit()


class LevelDot(QWidget):
    """Tiny live indicator: is audio arriving / is someone speaking on this source?
    Click to pick the device."""
    clicked = pyqtSignal()
    COLORS = {"speaking": QColor("#4cd97b"), "live": QColor(76, 217, 123, 110),
              "silent": QColor(255, 255, 255, 55), "off": QColor("#ff5f57")}

    def __init__(self, label: str):
        super().__init__()
        self.label, self.state = label, "silent"
        self.setFixedSize(46, 16)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_state(self, state: str, tip: str):
        if state != self.state or tip != self.toolTip():
            self.state = state
            self.setToolTip(tip)
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.COLORS[self.state])
        p.drawEllipse(QRectF(1, 4, 8, 8))
        p.setPen(QColor(210, 220, 235, 150))
        f = p.font()
        f.setPixelSize(11)
        p.setFont(f)
        p.drawText(QRectF(13, 0, 34, 16), Qt.AlignmentFlag.AlignVCenter, self.label)

    def mouseReleaseEvent(self, e):
        self.clicked.emit()


def _pill_style(checked_bg="rgba(143,180,255,0.28)") -> str:
    return (
        "QPushButton{color:%s; background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.10);"
        " border-radius:9px; padding:2px 10px; font:12px 'Segoe UI';}"
        "QPushButton:hover{background:rgba(255,255,255,0.13);}"
        "QPushButton:checked{background:%s; color:white; border-color:rgba(143,180,255,0.5);}"
    ) % (MUTED, checked_bg)


class TitleBar(QWidget):
    def __init__(self, panel: "Overlay"):
        super().__init__()
        self.panel = panel
        self.setFixedHeight(38)
        self.setMouseTracking(True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 12, 0)
        lay.setSpacing(8)
        self.dots = [Dot("#ff5f57", "x", "End call & quit (saves notes)"),
                     Dot("#febc2e", "-", "Shrink to bar (answers still pop up)"),
                     Dot("#28c840", "+", "Live transcript & notes")]
        for d in self.dots:
            lay.addWidget(d)
        lay.addSpacing(8)
        mark = QLabel()
        mark.setPixmap(QPixmap(str(ASSETS / "icon-64.png")).scaled(
            18, 18, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        mark.setToolTip("Cue")
        lay.addWidget(mark)
        lay.addSpacing(2)
        self.title = QLabel()
        self.title.setStyleSheet(f"color:{MUTED}; font:12px 'Segoe UI';")
        lay.addWidget(self.title, 1)
        self.levels = {"them": LevelDot("Them"), "me": LevelDot("You")}
        for src, dot in self.levels.items():
            dot.clicked.connect(lambda s=src: panel.devices_clicked.emit(s))
            lay.addWidget(dot)
        lay.addSpacing(4)

        self.mode_btns = {}
        seg = QHBoxLayout()
        seg.setSpacing(0)
        for i, m in enumerate(("work", "interview")):
            b = QPushButton(m.title())
            b.setCheckable(True)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            radius = "border-top-right-radius:0; border-bottom-right-radius:0;" if i == 0 else \
                     "border-top-left-radius:0; border-bottom-left-radius:0;"
            b.setStyleSheet(_pill_style() + "QPushButton{%s}" % radius)
            b.clicked.connect(lambda _=False, m=m: panel.mode_clicked.emit(m))
            seg.addWidget(b)
            self.mode_btns[m] = b
        lay.addLayout(seg)
        self.trigger_btn = QPushButton("name")
        self.trigger_btn.setToolTip("When to answer automatically: auto → smart → name → manual")
        self.trigger_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.trigger_btn.setStyleSheet(_pill_style())
        self.trigger_btn.clicked.connect(panel.trigger_clicked.emit)
        lay.addWidget(self.trigger_btn)
        self.script_btn = QPushButton("Script")
        self.script_btn.setCheckable(True)
        self.script_btn.setToolTip("On: full sentences you can read word for word.\n"
                                   "Off: an opener + bullets. (Double-tap Tab = script for one answer.)")
        self.script_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.script_btn.setStyleSheet(_pill_style())
        self.script_btn.toggled.connect(panel.script_toggled.emit)
        lay.addWidget(self.script_btn)
        self.explain_btn = QPushButton("What?")
        self.explain_btn.setToolTip("Couldn't understand that? Rewrites what was just said in plain English (Shift+Tab)")
        self.explain_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.explain_btn.setStyleSheet(_pill_style())
        self.explain_btn.clicked.connect(panel.explain_clicked.emit)
        lay.addWidget(self.explain_btn)
        self.ask_btn = QPushButton("⚡ Ask")
        self.ask_btn.setToolTip("I was just asked something (Tab)")
        self.ask_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.ask_btn.setStyleSheet(_pill_style().replace(MUTED, TEXT, 1))
        self.ask_btn.clicked.connect(panel.ask_clicked.emit)
        lay.addWidget(self.ask_btn)
        self._drag: QPoint | None = None

    def set_hot(self, hot: bool):
        for d in self.dots:
            d.hot = hot
            d.update()

    def enterEvent(self, e):
        self.set_hot(True)

    def leaveEvent(self, e):
        self.set_hot(False)

    # manual drag: the window never activates, so we move it ourselves
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.panel.pos()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.panel.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self._drag = None
            self.panel.save_position()


def load_ui_state() -> dict:
    """Small per-machine UI state: panel position, chosen audio devices."""
    try:
        return json.loads(UI_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_ui_state(state: dict) -> None:
    try:
        UI_STATE.parent.mkdir(parents=True, exist_ok=True)
        UI_STATE.write_text(json.dumps(state, indent=1), encoding="utf-8")
    except OSError:
        pass


class Overlay(QWidget):
    mode_clicked = pyqtSignal(str)      # "work" | "interview" | "practice"
    devices_clicked = pyqtSignal(str)   # "them" | "me"
    script_toggled = pyqtSignal(bool)
    explain_clicked = pyqtSignal()
    voice_setup_clicked = pyqtSignal()
    speaker_clicked = pyqtSignal(str)   # a speaker label in the transcript pane
    trigger_clicked = pyqtSignal()
    ask_clicked = pyqtSignal()
    close_clicked = pyqtSignal()

    def __init__(self, cfg):
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.WindowDoesNotAcceptFocus)
        if not cfg.get("show_in_taskbar", True):
            flags |= Qt.WindowType.Tool
        super().__init__(None, flags)
        self.setWindowTitle("Cue")
        self.cfg = cfg
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.tint = QColor(cfg.tint)
        self.acrylic = False
        self.fs = fs = cfg.font_size
        self.minimized = False
        self.expanded = False
        self.showing_answer = False
        self._home_html = ""
        self._base_title = "Cue"

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.bar = TitleBar(self)
        root.addWidget(self.bar)
        self.bar.dots[0].clicked.connect(self.close_clicked.emit)
        self.bar.dots[1].clicked.connect(self.toggle_minimized)
        self.bar.dots[2].clicked.connect(self.toggle_expanded)

        self.body = QWidget()
        bl = QVBoxLayout(self.body)
        bl.setContentsMargins(22, 10, 22, 16)
        bl.setSpacing(4)
        self.header = QLabel()
        self.header.setStyleSheet(f"color:{MUTED}; font:{int(fs * 0.72)}px 'Segoe UI';")
        self.content = QLabel()
        self.content.setStyleSheet(f"color:{TEXT}; font:{fs}px 'Segoe UI';")
        for lbl in (self.header, self.content):
            lbl.setWordWrap(True)
            lbl.setTextFormat(Qt.TextFormat.RichText)
        bl.addWidget(self.header)
        bl.addWidget(self.content)
        self.actions = QWidget()          # contextual buttons (practice: Hint / Next / Done)
        self._actions_lay = QHBoxLayout(self.actions)
        self._actions_lay.setContentsMargins(0, 6, 0, 0)
        self._actions_lay.setSpacing(8)
        self.actions.hide()
        bl.addWidget(self.actions)
        self.chooser = self._build_chooser()
        bl.addWidget(self.chooser)
        self._fx = QGraphicsOpacityEffect(self.body)
        self._fx.setOpacity(1.0)
        self.body.setGraphicsEffect(self._fx)
        root.addWidget(self.body)

        self.pane = self._build_pane()
        root.addWidget(self.pane)
        self.pane.hide()

        self.setFixedWidth(cfg.width)
        self._anim = QPropertyAnimation(self._fx, b"opacity", self)
        self._anim.finished.connect(self._fade_finished)
        self._status_timer = QTimer(self, singleShot=True, timeout=lambda: self.set_title(self._base_title))
        self._first_place = True

    # ------------------------------------------------------------------ building blocks
    def _build_chooser(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 4, 0, 0)
        head = QHBoxLayout()
        head.setSpacing(12)
        logo = QLabel()
        logo.setPixmap(QPixmap(str(ASSETS / "logo-512.png")).scaled(
            44, 44, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        head.addWidget(logo)
        words = QLabel(f"<div style='font:700 {int(self.fs * 1.5)}px Segoe UI; color:{TEXT}'>Cue</div>"
                       f"<div style='font:{int(self.fs * 0.85)}px Segoe UI; color:{MUTED}'>"
                       "What kind of call is this?</div>")
        words.setTextFormat(Qt.TextFormat.RichText)
        head.addWidget(words)
        head.addStretch(1)
        lay.addLayout(head)
        lay.addSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(12)
        big = ("QPushButton{color:%s; background:rgba(255,255,255,0.07); border:1px solid rgba(255,255,255,0.14);"
               " border-radius:12px; padding:14px; font:%dpx 'Segoe UI'; text-align:left;}"
               "QPushButton:hover{background:rgba(143,180,255,0.22); border-color:rgba(143,180,255,0.55);}")
        for mode, title, sub in (("work", "Work", "Team call · answers from\nwhat I did · takes notes"),
                                 ("interview", "Interview", "Answers every question\nfrom resume && history"),
                                 ("practice", "Practice", "Rehearse out loud · it\ncoaches && learns my voice")):
            b = QPushButton(f"{title}\n{sub}")
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setStyleSheet(big % (TEXT, int(self.fs * 0.85)))
            b.setMinimumHeight(64)
            b.clicked.connect(lambda _=False, m=mode: self.mode_clicked.emit(m))
            row.addWidget(b)
        lay.addLayout(row)
        # which audio it's listening to — the #1 thing to get right before a call
        devs = QHBoxLayout()
        devs.setSpacing(8)
        self.device_btns = {}
        for src, icon in (("them", "🎧 Call audio"), ("me", "🎙 Mic")):
            b = QPushButton(icon)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setStyleSheet(_pill_style())
            b.setToolTip("Click to change device")
            b.clicked.connect(lambda _=False, s=src: self.devices_clicked.emit(s))
            devs.addWidget(b)
            self.device_btns[src] = (b, icon)
        devs.addStretch(1)
        self.voice_btn = QPushButton("🗣 Set up my voice")
        self.voice_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.voice_btn.setStyleSheet(_pill_style())
        self.voice_btn.setToolTip("Talk for ~20 s so it can tell your voice from others in the room")
        self.voice_btn.clicked.connect(self.voice_setup_clicked.emit)
        devs.addWidget(self.voice_btn)
        lay.addSpacing(4)
        lay.addLayout(devs)
        w.hide()
        return w

    def _build_pane(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(18, 0, 18, 14)
        lay.setSpacing(6)
        tabs = QHBoxLayout()
        tabs.setSpacing(6)
        self.tab_btns = {}
        for key, label in (("transcript", "Live transcript"), ("notes", "Call notes")):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setStyleSheet(_pill_style())
            b.clicked.connect(lambda _=False, k=key: self._set_tab(k))
            tabs.addWidget(b)
            self.tab_btns[key] = b
        tabs.addStretch(1)
        lay.addLayout(tabs)
        self.pane_text = QTextBrowser()
        self.pane_text.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.pane_text.setOpenExternalLinks(False)
        self.pane_text.setOpenLinks(False)
        # speaker labels are links: click "Speaker 2" to tell it who that is
        self.pane_text.anchorClicked.connect(
            lambda url: url.toString().startswith("spk:") and self.speaker_clicked.emit(unquote(url.toString()[4:])))
        self.pane_text.setFixedHeight(260)
        self.pane_text.setStyleSheet(
            f"QTextBrowser{{background:rgba(0,0,0,0.18); border:1px solid rgba(255,255,255,0.08);"
            f" border-radius:10px; color:{TEXT}; font:{int(self.fs * 0.82)}px 'Segoe UI'; padding:6px;}}"
            "QScrollBar:vertical{background:transparent; width:8px;}"
            "QScrollBar::handle:vertical{background:rgba(255,255,255,0.25); border-radius:4px;}"
            "QScrollBar::add-line, QScrollBar::sub-line{height:0;}")
        lay.addWidget(self.pane_text)
        self._tab = "transcript"
        self._pane_data = {"transcript": "", "notes": ""}
        self.tab_btns["transcript"].setChecked(True)
        return w

    def _set_tab(self, key: str):
        self._tab = key
        for k, b in self.tab_btns.items():
            b.setChecked(k == key)
        self._render_pane(scroll_end=True)

    def _render_pane(self, scroll_end: bool = False):
        sb = self.pane_text.verticalScrollBar()
        at_end = scroll_end or sb.value() >= sb.maximum() - 4
        self.pane_text.setHtml(self._pane_data[self._tab] or
                               f"<span style='color:{MUTED}'>Nothing yet.</span>")
        if at_end:
            sb.setValue(sb.maximum())

    # ------------------------------------------------------------------ native window setup
    def showEvent(self, e):
        super().showEvent(e)
        hwnd = int(self.winId())
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        # never steal focus; with show_in_taskbar Cue gets a taskbar button with its icon
        ex |= WS_EX_NOACTIVATE
        ex = (ex | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW if self.cfg.get("show_in_taskbar", True) \
            else ex | WS_EX_TOOLWINDOW
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
        if self.cfg.hide_from_screen_share:
            user32.SetWindowDisplayAffinity(wintypes.HWND(hwnd), WDA_EXCLUDEFROMCAPTURE)
        if self.cfg.acrylic and not self.acrylic:
            self.acrylic = _enable_acrylic(hwnd, self.tint, self.cfg.tint_alpha)

    def closeEvent(self, e):
        # taskbar "Close window", Alt+F4, Windows asking the app to close: end the call properly
        e.ignore()
        self.close_clicked.emit()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 14, 14)
        fill = QColor(self.tint)
        # with acrylic the blur+tint is native, so only a light sheen is painted on top
        fill.setAlpha(40 if self.acrylic else min(255, self.cfg.tint_alpha + 60))
        p.fillPath(path, fill)
        p.setPen(QPen(QColor(255, 255, 255, 45), 1))
        p.drawPath(path)
        if self.body.isVisible() or self.pane.isVisible():
            p.setPen(QPen(QColor(255, 255, 255, 22), 1))
            y = self.bar.height() + 0.5
            p.drawLine(QPoint(14, int(y)), QPoint(self.width() - 14, int(y)))

    # ------------------------------------------------------------------ placement
    def _relayout(self):
        # word-wrapped labels make adjustSize() guess the height at the wrong width, and the
        # slack ends up as big gaps; ask the layout for the true height at our fixed width
        lay = self.layout()
        lay.invalidate()  # heights are cached; content or visibility just changed
        lay.activate()
        h = lay.totalHeightForWidth(self.width())
        # -1 when nothing visible wraps (e.g. shrunk to the bar): plain size hint is exact then
        self.setFixedHeight(h if h > 0 else lay.totalSizeHint().height())
        # labels re-wrap new text on the next event-loop turn; measure again once they have
        if not getattr(self, "_settling", False):
            QTimer.singleShot(0, self._settle)

    def _settle(self):
        self._settling = True
        try:
            self._relayout()
        finally:
            self._settling = False
        if self._first_place:
            self._first_place = False
            pos = self._saved_position()
            if pos is None:
                screens = QGuiApplication.screens()
                screen = screens[self.cfg.screen] if self.cfg.screen < len(screens) else QGuiApplication.primaryScreen()
                g = screen.availableGeometry()
                pos = QPoint(g.x() + (g.width() - self.width()) // 2, g.y() + self.cfg.margin)
            self.move(pos)
        self.update()

    def _saved_position(self) -> QPoint | None:
        d = load_ui_state()
        if "x" in d and "y" in d:
            p = QPoint(d["x"], d["y"])
            if any(s.availableGeometry().contains(p + QPoint(40, 10)) for s in QGuiApplication.screens()):
                return p
        return None

    def save_position(self):
        state = load_ui_state()
        state.update(x=self.x(), y=self.y())
        save_ui_state(state)

    def set_devices(self, them: str, me: str):
        for src, name in (("them", them), ("me", me)):
            b, icon = self.device_btns[src]
            b.setText(f"{icon}: {name}")

    def set_levels(self, them: tuple[str, str], me: tuple[str, str]):
        self.bar.levels["them"].set_state(*them)
        self.bar.levels["me"].set_state(*me)

    # ------------------------------------------------------------------ public API
    def start(self):
        self.show()
        self._relayout()

    def set_title(self, text: str):
        self.bar.title.setText(html.escape(text))

    def set_base_title(self, text: str):
        self._base_title = text
        if not self._status_timer.isActive():
            self.set_title(text)

    def show_status(self, text: str, ms: int = 2500):
        """Transient message in the title bar."""
        self.set_title(text)
        self._status_timer.start(ms)

    def set_mode(self, mode: str, trigger: str):
        for m, b in self.bar.mode_btns.items():
            b.setChecked(m == mode)
        self.bar.trigger_btn.setText(trigger)

    def show_chooser(self):
        self.chooser.show()
        self.header.hide()
        self.content.hide()
        self.body.show()
        self._relayout()

    def set_home(self, header: str, body_html: str):
        """Idle content (e.g. last week's recap). Shown whenever no answer is on screen."""
        self._home = (header, body_html)
        if not self.showing_answer:
            self._show_home()

    def _show_home(self):
        self.chooser.hide()
        header, body_html = getattr(self, "_home", ("", ""))
        self._set_header(header)
        self.content.setText(body_html)
        self.content.show()
        self._fx.setOpacity(1.0)
        self.body.setVisible(not self.minimized)
        self._relayout()

    def show_answer(self, status: str, text: str):
        self._anim.stop()
        self._fx.setOpacity(1.0)
        self.showing_answer = True
        self.chooser.hide()
        self._set_header(status)
        self.content.setText(render(text, self.fs) or f"<span style='color:{MUTED}'>…</span>")
        self.content.show()
        self.body.show()  # answers pop open even when shrunk to the bar
        self._relayout()

    def fade_out(self):
        """Answer done: fade it away and fall back to the home content."""
        if not self.showing_answer or self._anim.state() == QPropertyAnimation.State.Running:
            return
        self._anim.setDuration(self.cfg.fade_ms)
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._anim.start()

    def _fade_finished(self):
        if self._fx.opacity() <= 0.01:
            self.showing_answer = False
            self._show_home()

    def set_transcript(self, html_text: str):
        self._pane_data["transcript"] = html_text
        if self.expanded and self._tab == "transcript":
            self._render_pane()

    def set_notes(self, html_text: str):
        self._pane_data["notes"] = html_text
        if self.expanded and self._tab == "notes":
            self._render_pane()

    def toggle_minimized(self):
        self.minimized = not self.minimized
        if not self.chooser.isVisible():
            self.body.setVisible(not self.minimized or self.showing_answer)
        self._relayout()

    def toggle_expanded(self):
        self.expanded = not self.expanded
        self.pane.setVisible(self.expanded)
        if self.expanded:
            self._render_pane(scroll_end=True)
        self._relayout()

    def _set_header(self, text: str):
        self.header.setText(html.escape(text))
        self.header.setVisible(bool(text))

    def set_actions(self, actions: list[tuple[str, object]]):
        """Contextual buttons under the content, e.g. practice: Hint / Next / Done. [] hides them."""
        while self._actions_lay.count():
            item = self._actions_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for label, callback in actions:
            b = QPushButton(label)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setStyleSheet(_pill_style().replace(MUTED, TEXT, 1))
            b.clicked.connect(callback)
            self._actions_lay.addWidget(b)
        self._actions_lay.addStretch(1)
        self.actions.setVisible(bool(actions))
        self._relayout()

    def set_voice_status(self, text: str):
        self.voice_btn.setText(text)

    def set_script(self, on: bool):
        self.bar.script_btn.blockSignals(True)
        self.bar.script_btn.setChecked(on)
        self.bar.script_btn.blockSignals(False)


def render(text: str, fs: int) -> str:
    """Q:/>/- lines -> styled HTML. Tolerates partial (streaming) text."""
    out = []
    for raw in text.strip().splitlines():
        line = raw.strip()
        if not line:
            continue
        esc = html.escape(line.lstrip("-•*> ").strip())
        if line.startswith("Q:"):
            out.append(f"<div style='color:rgba(170,200,255,190); font-size:{int(fs * 0.78)}px'>"
                       f"{html.escape(line[2:].strip())}</div>")
        elif line.startswith(">"):
            out.append(f"<div style='font-size:{int(fs * 1.12)}px; font-weight:600; margin:2px 0 6px 0'>"
                       f"“{esc}”</div>")
        elif line[0] in "-•*":
            out.append(f"<div style='margin-left:4px'>•&nbsp; {esc}</div>")
        elif line[0] in "✓+":   # practice coaching
            color = "#4cd97b" if line[0] == "✓" else "#febc2e"
            out.append(f"<div style='margin:2px 0'><span style='color:{color}; font-weight:700'>{line[0]}</span>"
                       f"&nbsp; {html.escape(line[1:].strip())}</div>")
        else:                  # script mode: plain sentences to read out loud
            out.append(f"<div style='font-size:{int(fs * 1.08)}px; line-height:140%; margin:2px 0'>{esc}</div>")
    return "".join(out)


def bullets_html(items: list[str], fs: int) -> str:
    return "".join(f"<div style='margin:2px 0 2px 4px'>•&nbsp; {html.escape(i)}</div>" for i in items)


def grouped_html(groups: list[tuple[str, list[str]]], fs: int) -> str:
    """Workstream label, then its bullets: the Work-mode 'last week' recap."""
    out = []
    for i, (name, items) in enumerate(groups):
        if name:
            top = 2 if i == 0 else 9
            out.append(f"<div style='color:{ACCENT}; font-size:{int(fs * 0.74)}px; font-weight:600;"
                       f" letter-spacing:0.5px; margin:{top}px 0 1px 0'>{html.escape(name.upper())}</div>")
        out.append(bullets_html(items, fs))
    return "".join(out)
