"""Before a trainer session: which language, which file, and (optionally) the problem text.
A normal window (the glass panel can't take keyboard focus, and this needs typing)."""
from __future__ import annotations

import os
import time
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QCompleter, QDialog, QDialogButtonBox,
                             QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                             QRadioButton, QVBoxLayout)

from ..config import ASSETS
from ..overlay import load_ui_state, save_ui_state
from . import langs, vscode

STYLE = """
QDialog { background:#141821; }
QLabel { color:#dfe6ef; font:13px 'Segoe UI'; }
QLabel#head { color:#8fb4ff; font:600 11px 'Segoe UI'; letter-spacing:0.5px; margin-top:8px; }
QLabel#muted { color:rgba(210,220,235,150); font:12px 'Segoe UI'; }
QRadioButton, QCheckBox { color:#dfe6ef; font:13px 'Segoe UI'; }
QComboBox, QLineEdit, QPlainTextEdit { background:#1d2230; color:#f4f7fb; border:1px solid #2c3446; border-radius:6px;
                                        padding:5px 8px; font:13px 'Segoe UI'; }
QComboBox QAbstractItemView { background:#1d2230; color:#f4f7fb; selection-background-color:#34507e; }
QPushButton { background:#232a3a; color:#dfe6ef; border:1px solid #33405a; border-radius:6px; padding:5px 12px;
              font:13px 'Segoe UI'; }
QPushButton:hover { background:#2c3650; }
QPushButton:default { background:#3a5a94; border-color:#5b7fc0; color:white; }
"""


def default_folder(tcfg) -> Path:
    f = (tcfg.get("folder") or "").strip()
    return Path(os.path.expandvars(f)) if f else Path.home() / "Documents" / "Cue Trainer"


class SetupDialog(QDialog):
    def __init__(self, tcfg, parent=None):
        super().__init__(parent, Qt.WindowType.WindowStaysOnTopHint)
        self.tcfg = tcfg
        self.setWindowTitle("Cue Trainer: new coding round")
        self.setWindowIcon(QIcon(str(ASSETS / "cue.ico")))
        self.setStyleSheet(STYLE)
        self.setMinimumWidth(560)
        st = load_ui_state().get("trainer_setup", {})
        lay = QVBoxLayout(self)
        lay.setSpacing(6)

        title = QLabel("<span style='font:700 18px Segoe UI; color:#f4f7fb'>Trainer</span>"
                       "<br><span style='color:rgba(210,220,235,150)'>Practice a coding round: Cue keeps the spec, "
                       "designs approaches, writes verified tests and watches your code.</span>")
        title.setWordWrap(True)
        lay.addWidget(title)

        lay.addWidget(self._head("LANGUAGE"))
        self.lang = QComboBox()
        self.lang.setEditable(True)
        self.lang.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        names = []
        langs.refresh()   # a compiler installed since Cue started shows up as runnable
        runnable = [l for l in langs.LANGUAGES if l.toolchains and langs.runnable(l)]
        others = [l for l in langs.LANGUAGES if l not in runnable]
        for l in runnable + others:
            tag = "▶ runs here" if l in runnable else ("not installed" if l.toolchains else "reference only")
            self.lang.addItem(f"{l.name}   ·   {tag}", l.key)
            names.append(l.name)
        comp = QCompleter(names, self)
        comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        comp.setFilterMode(Qt.MatchFlag.MatchContains)
        self.lang.setCompleter(comp)
        want = st.get("language") or tcfg.get("language") or "python"
        idx = self.lang.findData(want)
        self.lang.setCurrentIndex(idx if idx >= 0 else 0)
        lay.addWidget(self.lang)
        note = QLabel("▶ = tests run on this PC. Others still get the spec, design, reference code and review; "
                      "install the compiler to run tests.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        lay.addWidget(note)

        lay.addWidget(self._head("YOUR SOLUTION FILE"))
        self.new_file = QRadioButton(f"New file in {default_folder(tcfg)}")
        self.existing = QRadioButton("A file I already have:")
        grp = QButtonGroup(self)
        grp.addButton(self.new_file)
        grp.addButton(self.existing)
        lay.addWidget(self.new_file)
        row = QHBoxLayout()
        row.addWidget(self.existing)
        self.path = QLineEdit(st.get("path", ""))
        row.addWidget(self.path, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        lay.addLayout(row)
        (self.existing if st.get("existing") and st.get("path") else self.new_file).setChecked(True)
        self.path.textEdited.connect(lambda _: self.existing.setChecked(True))

        editor = (tcfg.get("editor") or "").strip()
        self.open_editor = QCheckBox("Open it in VS Code" if editor == "code" else f"Open it with {editor}")
        self.open_editor.setChecked(bool(editor) and st.get("open_editor", True))
        self.open_editor.setVisible(bool(editor))
        lay.addWidget(self.open_editor)

        lay.addWidget(self._head("THE PROBLEM (OPTIONAL)"))
        self.problem = QPlainTextEdit()
        self.problem.setPlaceholderText("Paste the problem statement here if you have it as text.\n"
                                        "Leave it empty and Cue listens to the interviewer instead (or both).")
        self.problem.setFixedHeight(110)
        lay.addWidget(self.problem)

        self.vs_row = QHBoxLayout()
        self.vs_label = QLabel()
        self.vs_label.setObjectName("muted")
        self.vs_label.setWordWrap(True)
        self.vs_btn = QPushButton("Install VS Code helper")
        self.vs_btn.clicked.connect(self._install_ext)
        self.vs_row.addWidget(self.vs_label, 1)
        self.vs_row.addWidget(self.vs_btn)
        lay.addLayout(self.vs_row)
        self._refresh_ext()

        buttons = QDialogButtonBox()
        start = buttons.addButton("Start", QDialogButtonBox.ButtonRole.AcceptRole)
        start.setDefault(True)
        buttons.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addSpacing(6)
        lay.addWidget(buttons)
        self.result_settings: dict | None = None

    def _head(self, text: str) -> QLabel:
        l = QLabel(text)
        l.setObjectName("head")
        return l

    def _browse(self):
        p, _ = QFileDialog.getOpenFileName(self, "Your solution file", self.path.text() or str(default_folder(self.tcfg)))
        if p:
            self.path.setText(p)
            self.existing.setChecked(True)
            guess = next((l for l in langs.LANGUAGES if Path(p).suffix.lower() == l.ext.lower() and l.toolchains), None)
            if guess:
                self.lang.setCurrentIndex(self.lang.findData(guess.key))

    def _refresh_ext(self):
        if not vscode.code_cli():
            self.vs_label.setText("VS Code not found: Cue reads your file each time you save it.")
            self.vs_btn.hide()
        elif vscode.installed():
            self.vs_label.setText("VS Code helper installed: Cue sees your edits live (before you save) and draws its "
                                  "marks right in the editor.")
            self.vs_btn.hide()
        else:
            self.vs_label.setText("Optional: the VS Code helper lets Cue see edits before you save and draws the "
                                  "red/green marks inside VS Code. Without it, Cue reads the file on every save.")
            self.vs_btn.show()

    def _install_ext(self):
        self.vs_btn.setEnabled(False)
        self.vs_label.setText("Installing…")
        ok, msg = vscode.install()
        self.vs_btn.setEnabled(True)
        self._refresh_ext()
        if not ok:
            self.vs_label.setText(f"Couldn't install it: {msg}")

    def _language(self):
        key = self.lang.currentData()
        text = self.lang.currentText().split("·")[0].strip()
        if key and self.lang.itemText(self.lang.currentIndex()).startswith(text):
            return langs.get(key)
        return langs.find(text) or langs.get(key or "python")

    def _accept(self):
        lang = self._language()
        if self.existing.isChecked() and self.path.text().strip():
            path = Path(self.path.text().strip().strip('"'))
        else:
            folder = default_folder(self.tcfg) / time.strftime("%Y-%m-%d_%H%M")
            path = folder / f"{lang.user_name() if lang.key in ('java', 'haskell') else 'solution' + lang.ext}"
        self.result_settings = {"language": lang.key, "path": str(path), "problem_text": self.problem.toPlainText().strip(),
                                "open_editor": self.open_editor.isChecked() and self.open_editor.isVisible(),
                                "new_file": not self.existing.isChecked()}
        state = load_ui_state()
        state["trainer_setup"] = {"language": lang.key, "path": self.path.text().strip(),
                                  "existing": self.existing.isChecked(), "open_editor": self.open_editor.isChecked()}
        save_ui_state(state)
        self.accept()
