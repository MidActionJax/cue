"""The trainer's glass windows: Answers, Ask next, Design tracks and Code. Same frosted glass as
the main panel, never take focus, left out of screen capture, each remembers where you put it.
Their content is HTML from views.py; links in it (pin:A2, test:T3, ...) come back as `link`."""
from __future__ import annotations

from PyQt6.QtCore import QPoint, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizeGrip, QTextBrowser, QVBoxLayout, QWidget

from ..overlay import ACCENT, MUTED, TEXT, GlassWindow, _pill_style, load_ui_state, render, save_ui_state


class Header(QWidget):
    """Title row: drag it to move the window."""

    def __init__(self, win: "TrainerWindow", title: str):
        super().__init__()
        self.win = win
        self.setFixedHeight(32)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 8, 0)
        lay.setSpacing(6)
        self.title = QLabel(title)
        self.title.setStyleSheet(f"color:{ACCENT}; font:600 12px 'Segoe UI'; letter-spacing:0.5px;")
        lay.addWidget(self.title)
        self.status = QLabel()
        self.status.setStyleSheet(f"color:{MUTED}; font:11px 'Segoe UI';")
        lay.addWidget(self.status, 1)
        self.extra = QHBoxLayout()
        self.extra.setSpacing(4)
        lay.addLayout(self.extra)
        close = QPushButton("×")
        close.setToolTip("Hide this window (Ctrl+Alt+W brings all the trainer windows back)")
        close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close.setFixedSize(22, 20)
        close.setStyleSheet(_pill_style().replace("padding:2px 10px", "padding:0"))
        close.clicked.connect(win.hide)
        lay.addWidget(close)
        self._drag: QPoint | None = None

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.win.pos()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.win.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self._drag = None
            self.win.save_geometry()


class TrainerWindow(GlassWindow):
    link = pyqtSignal(str)

    def __init__(self, cfg, key: str, title: str, size: tuple[int, int]):
        super().__init__(cfg, taskbar=False)
        self.key = key
        self.setWindowTitle(f"Cue · {title}")
        self.fs = int(cfg.font_size * 0.9)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 4)
        root.setSpacing(0)
        self.header = Header(self, title)
        root.addWidget(self.header)
        self.toolbar = QWidget()
        self._tools = QHBoxLayout(self.toolbar)
        self._tools.setContentsMargins(12, 0, 12, 4)
        self._tools.setSpacing(6)
        self.toolbar.hide()
        root.addWidget(self.toolbar)
        self.view = QTextBrowser()
        self.view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.view.setOpenLinks(False)
        self.view.setOpenExternalLinks(False)
        self.view.anchorClicked.connect(lambda url: self.link.emit(url.toString()))
        self.view.setStyleSheet(
            f"QTextBrowser{{background:transparent; border:none; color:{TEXT}; font:{self.fs}px 'Segoe UI';"
            " padding:2px 10px 6px 12px;}"
            "QScrollBar:vertical{background:transparent; width:8px;}"
            "QScrollBar::handle:vertical{background:rgba(255,255,255,0.25); border-radius:4px;}"
            "QScrollBar::add-line, QScrollBar::sub-line{height:0;}"
            "QScrollBar:horizontal{height:0;}")
        root.addWidget(self.view, 1)
        grip_row = QHBoxLayout()
        grip_row.setContentsMargins(0, 0, 4, 0)
        grip_row.addStretch(1)
        grip = QSizeGrip(self)
        grip.setFixedSize(14, 14)
        grip_row.addWidget(grip)
        root.addLayout(grip_row)
        self.setMinimumSize(260, 160)
        self._html = ""
        self._save_timer = QTimer(self, singleShot=True, interval=600, timeout=self.save_geometry)
        self._default = size
        self._placed = False

    # ------------------------------------------------------------------ content
    def set_html(self, html_text: str, keep_scroll: bool = True):
        if html_text == self._html:
            return
        self._html = html_text
        sb = self.view.verticalScrollBar()
        pos = sb.value()
        self.view.setHtml(html_text)
        if keep_scroll:
            sb.setValue(pos)

    def set_status(self, text: str):
        self.header.status.setText(text)

    def add_header_button(self, label: str, tip: str, callback, checkable: bool = False) -> QPushButton:
        b = QPushButton(label)
        b.setToolTip(tip)
        b.setCheckable(checkable)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setStyleSheet(_pill_style())
        b.clicked.connect(callback)
        self.header.extra.addWidget(b)
        return b

    def set_tools(self, buttons: list[tuple[str, str, object]]):
        """A row of small buttons under the title: [(label, tooltip, callback)]. [] hides it."""
        while self._tools.count():
            item = self._tools.takeAt(0)
            if item.widget():
                item.widget().hide()   # deleteLater waits for the event loop: don't show it until then
                item.widget().deleteLater()
        for label, tip, cb in buttons:
            b = QPushButton(label)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setStyleSheet(_pill_style())
            b.clicked.connect(cb)
            self._tools.addWidget(b)
        self._tools.addStretch(1)
        self.toolbar.setVisible(bool(buttons))

    # ------------------------------------------------------------------ placement
    def place(self, default_pos: QPoint):
        state = load_ui_state().get("trainer_windows", {}).get(self.key)
        size = QSize(*self._default)
        pos = default_pos
        if state:
            size = QSize(state.get("w", size.width()), state.get("h", size.height()))
            p = QPoint(state.get("x", pos.x()), state.get("y", pos.y()))
            if any(s.availableGeometry().contains(p + QPoint(40, 10)) for s in QGuiApplication.screens()):
                pos = p
        self.resize(size)
        self.move(pos)
        self._placed = True

    def save_geometry(self):
        if not self._placed:
            return
        state = load_ui_state()
        state.setdefault("trainer_windows", {})[self.key] = {"x": self.x(), "y": self.y(),
                                                             "w": self.width(), "h": self.height()}
        save_ui_state(state)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._save_timer.start()

    def moveEvent(self, e):
        super().moveEvent(e)
        self._save_timer.start()


class AnswersWindow(TrainerWindow):
    """The interviewer's questions answered, newest on top (the last few stay below it)."""

    def __init__(self, cfg):
        super().__init__(cfg, "answers", "ANSWERS", (440, 330))
        self._stack: list[tuple[str, str]] = []

    def show_answer(self, status: str, text: str, new: bool):
        if new or not self._stack:
            self._stack.insert(0, (status, text))
            self._stack = self._stack[:5]
        else:
            self._stack[0] = (status, text)
        self._render()

    def _render(self):
        parts = []
        for i, (status, text) in enumerate(self._stack):
            fs = self.fs if i == 0 else int(self.fs * 0.85)
            op = "" if i == 0 else "opacity:0.6;"
            body = render(text, fs) or f"<span style='color:{MUTED}'>…</span>"
            parts.append(f"<div style='{op}'><div style='color:{MUTED}; font-size:{int(self.fs * 0.7)}px'>{status}</div>"
                         f"<div style='color:{TEXT if i == 0 else MUTED}'>{body}</div></div>")
            if i == 0 and len(self._stack) > 1:
                parts.append("<hr style='border:0; color:rgba(255,255,255,0.1)'>")
        if not parts:
            parts.append(f"<div style='color:{MUTED}'>The interviewer's questions get answered here. "
                         "Ctrl+Alt+A answers the latest one; Ctrl+Alt+N says what to narrate right now.</div>")
        self.set_html("".join(parts), keep_scroll=False)
