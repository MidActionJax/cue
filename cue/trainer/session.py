"""One trainer session: the Brain, the windows, your file, the timer.

The Controller (app.py) owns the call: audio, transcript, hotkeys and the live answer backend.
It hands this session every final utterance and every answer to show; this session owns the
ProblemState and everything drawn from it.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QObject, QPoint, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QApplication, QInputDialog

from ..config import DATA, ROOT
from . import langs, prompts, runner, views, vscode
from .brain import Brain, _hash
from .state import ProblemState
from .watch import Bridge, FileWatcher
from .windows import AnswersWindow, TrainerWindow

log = logging.getLogger(__name__)
SESSIONS = ROOT / "trainer"


class _Post(QObject):
    call = pyqtSignal(object)


class TrainerSession(QObject):
    def __init__(self, ctl, settings: dict):
        super().__init__()
        self.ctl = ctl
        cfg = ctl.cfg
        self.tcfg = cfg.get("trainer") or {}
        self.name = cfg.user.name
        self.fs = int(cfg.overlay.font_size * 0.9)
        self.lang = langs.get(settings["language"])
        self.path = Path(settings["path"])
        self.started = time.time()
        self.stamp = time.strftime("%Y-%m-%d_%H%M%S")
        self.folder = DATA / "trainer" / "sessions" / self.stamp
        self.folder.mkdir(parents=True, exist_ok=True)
        self.state = ProblemState(self.lang.key, str(self.path))
        self._prepare_file(settings)

        self._post = _Post()
        self._post.call.connect(lambda fn: fn())
        self.brain = Brain(self.state, self.tcfg, cfg.llm, self.name, self.folder, self.post,
                           self._changed, self._status)
        if not self.brain.jobs.available:
            ctl.overlay.show_status("Trainer needs Claude: run `claude auth login` (or set an API key)", 8000)

        ov = cfg.overlay
        self.answers = AnswersWindow(ov)
        self.ask_win = TrainerWindow(ov, "ask", "ASK NEXT", (440, 300))
        self.howto_win = TrainerWindow(ov, "howto", "HOW TO SOLVE IT", (440, 360))
        self.design_win = TrainerWindow(ov, "design", "DESIGN TRACKS", (560, 380))
        self.code_win = TrainerWindow(ov, "code", f"CODE · {self.lang.name.upper()}", (560, 560))
        self.windows = [self.answers, self.howto_win, self.ask_win, self.design_win, self.code_win]
        self.design_win.add_header_button("⟳", "Design again from the current requirements", self.redesign)
        self.design_win.link.connect(self._design_link)
        self.code_win.link.connect(self._code_link)
        self._tab = "blocks"
        self._show_code = True
        self._expanded: set[str] = set()
        self._open_tests: set[str] = set()
        self._show_trace = False
        self.auto_run = bool(self.tcfg.get("auto_run", True))
        self.tab_btns = {}
        for key, label in (("blocks", "Blocks"), ("mycode", "My code"), ("tests", "Tests"), ("debug", "Debug")):
            b = self.code_win.add_header_button(label, "", lambda _=False, k=key: self.set_tab(k), checkable=True)
            self.tab_btns[key] = b
        self._place_windows()
        for w in self.windows:
            w.show()
        self.set_tab("blocks")
        self.answers._render()

        self._render_timer = QTimer(self, singleShot=True, interval=80, timeout=self.render)
        self._syntax_timer = QTimer(self, singleShot=True, interval=500, timeout=self._syntax_check)
        self._review_timer = QTimer(self, singleShot=True, interval=2000, timeout=self._review)
        self._listen_timer = QTimer(self, singleShot=True, interval=1200, timeout=self._listen)
        self._save_timer = QTimer(self, singleShot=True, interval=1500, timeout=self.save)
        self._tick_timer = QTimer(self, interval=1000, timeout=self._tick)
        self._tick_timer.start()
        self._last_review = 0.0
        self._last_listen = 0.0
        self._autorun_pending = False
        self._running = False
        self._measured_for = ""
        self._marks_cache = {"path": str(self.path), "marks": [], "version": 0, "language": self.lang.key}

        self.watcher = FileWatcher(str(self.path))
        self.watcher.changed.connect(self._on_saved)
        self.watcher.start()
        self.bridge = Bridge(lambda: self._marks_cache)
        self.bridge.buffer.connect(self._on_buffer)
        self.bridge.command.connect(self.hotkey)
        try:
            self.bridge.start()
        except OSError as e:
            log.warning("trainer bridge didn't start: %s", e)

        self._set_overlay_actions()
        if settings.get("problem_text"):
            self.brain.hear("PASTED PROBLEM", settings["problem_text"])
            self._listen()
        self.render()

    # ------------------------------------------------------------------ setup
    def _prepare_file(self, settings: dict):
        p = self.path
        self._starter = ""   # a file you already had counts as yours from the start
        try:
            if not p.exists():
                p.parent.mkdir(parents=True, exist_ok=True)
                self._starter = langs.starter(self.lang)
                p.write_text(self._starter, encoding="utf-8")
                vs = p.parent / ".vscode" / "settings.json"
                if not vs.exists():   # save as you type, so Cue sees changes even without the extension
                    vs.parent.mkdir(exist_ok=True)
                    vs.write_text(json.dumps({"files.autoSave": "afterDelay", "files.autoSaveDelay": 800}, indent=2),
                                  encoding="utf-8")
        except OSError as e:
            log.warning("couldn't create %s: %s", p, e)
        if settings.get("open_editor"):
            vscode.open_file(p, self.tcfg.get("editor") or "code")

    def _place_windows(self):
        """First time: Answers, How to solve it and Ask next down the left, Code + Design down the
        right, your editor in between. After that every window stays where you put it."""
        # the screen the panel is actually on (its screen() can still be stale right after it moved)
        screen = QGuiApplication.screenAt(self.ctl.overlay.geometry().center()) or QGuiApplication.primaryScreen()
        g = screen.availableGeometry()
        h = g.height()
        right = g.right() - 572
        self.code_win._default = (560, int(h * 0.58))
        self.design_win._default = (560, int(h * 0.38) - 28)
        self.answers._default = (440, int(h * 0.30))
        self.howto_win._default = (440, int(h * 0.38))
        self.ask_win._default = (440, int(h * 0.28) - 28)
        self.code_win.place(QPoint(right, g.top() + 12))
        self.design_win.place(QPoint(right, g.top() + 12 + int(h * 0.58) + 8))
        self.answers.place(QPoint(g.left() + 12, g.top() + 12))
        self.howto_win.place(QPoint(g.left() + 12, g.top() + 12 + int(h * 0.30) + 8))
        self.ask_win.place(QPoint(g.left() + 12, g.top() + 12 + int(h * 0.68) + 16))

    def post(self, fn):
        self._post.call.emit(fn)

    def _status(self, text: str):
        self.post(lambda: self.ctl.overlay.show_status(text, 4000))

    def _changed(self, keys: set):
        self._render_timer.start()
        self._save_timer.start()
        s = self.state
        if s.problem.defined and s.estimate.minutes and not s.estimate.started_at and self.tcfg.get("timer_autostart", True):
            s.start_timer()
            self.ctl.overlay.show_status(f"⏱ {s.estimate.minutes} min on the clock ({s.estimate.rationale})", 5000)
        if "runs" in keys:
            self._after_run()
        if "debug" in keys:
            self.set_tab("debug")
        if "tests" in keys and self.auto_run and not s.runs:
            self.run_tests(auto=True)

    # ------------------------------------------------------------------ transcript
    def on_line(self, me: bool, text: str):
        self.brain.hear(self.name.upper() if me else "INTERVIEWER", text)
        wait = max(0.0, float(self.tcfg.get("listen_every_s") or 4) - (time.time() - self._last_listen))
        self._listen_timer.start(int(max(1.2, wait) * 1000))

    def _listen(self):
        if self.brain.flush():
            self._last_listen = time.time()

    def paste_problem(self):
        text = QApplication.clipboard().text().strip()
        if not text:
            text, ok = QInputDialog.getMultiLineText(None, "Cue Trainer: the problem", "Paste the problem statement:")
            if not ok or not text.strip():
                return
        self.brain.hear("PASTED PROBLEM", text)
        self._listen()
        self.ctl.overlay.show_status(f"Reading the pasted problem ({len(text)} characters)…", 4000)

    # ------------------------------------------------------------------ your code
    def _on_saved(self, text: str):
        self.on_code(text)
        if self.auto_run and self.state.tests and langs.runnable(self.lang):
            self._autorun_pending = True
            self._syntax_timer.start(150)

    def _on_buffer(self, path: str, text: str, diagnostics: list):
        if path and Path(path).resolve() != self.path.resolve():
            return
        errs = [{"line": int(d.get("line", 0)), "message": str(d.get("message", ""))[:200]}
                for d in diagnostics if str(d.get("severity", "error")).lower() == "error"]
        self.state.code["diagnostics"] = errs
        self.on_code(text.replace("\r\n", "\n"))

    def on_code(self, text: str):
        s = self.state
        if text == s.code.get("text"):
            return
        s.code["text"] = text
        self._render_timer.start()
        self._syntax_timer.start()
        if s.problem.defined and self.has_code(text):
            gap = time.time() - self._last_review
            min_gap = float(self.tcfg.get("review_every_s") or 8)
            self._review_timer.start(int(max(2.0, min_gap - gap) * 1000))

    def _syntax_check(self):
        code, lang = self.state.code.get("text", ""), self.lang
        path = str(self.path)

        def work():
            try:
                errs = runner.syntax_check(lang, code, path)
            except Exception as e:
                log.warning("syntax check failed: %s", e)
                errs = []

            def done():
                if code != self.state.code.get("text"):
                    return
                diag = self.state.code.get("diagnostics") or []
                self.state.code["syntax"] = errs or diag
                self._render_timer.start()
                if self._autorun_pending and not errs:
                    self._autorun_pending = False
                    self.run_tests(auto=True)
            self.post(done)
        threading.Thread(target=work, daemon=True).start()

    def _review(self):
        if self.brain.jobs.busy("reviewer"):
            self._review_timer.start(2000)
            return
        self._last_review = time.time()
        self.brain.review(self.state.code.get("text", ""), self.state.code.get("syntax"))

    # ------------------------------------------------------------------ tests / debug
    def has_code(self, text: str) -> bool:
        return has_own_code(text, self._starter, self.lang.comment)

    def run_tests(self, auto: bool = False):
        """auto: after a save or when the tests arrive. Those never run on a starter file and never
        switch the Code window's tab; a run you ask for does both."""
        s = self.state
        if self._running:
            return
        if not s.tests:
            if not auto:
                self.ctl.overlay.show_status("No tests yet: they're built once the problem is clear", 3000)
            return
        if auto and not self.has_code(s.code.get("text", "")):
            return
        self._running = True
        self.code_win.set_status("running tests…")
        self.brain.run_tests(s.code.get("text", ""), on_done=lambda run: self._ran(run, auto))

    def _ran(self, run, auto: bool = False):
        self._running = False
        self.code_win.set_status("")
        if run.get("build_error"):
            self.ctl.overlay.show_status("Tests: your code doesn't build or run, see Code → Tests", 4000)
        else:
            self.ctl.overlay.show_status(f"Tests: {run['passed']}/{run['total']} pass", 3000)
        if not auto and self._tab in ("blocks", "mycode") and (run.get("build_error") or run["passed"] < run["total"]):
            self.set_tab("tests")
        # a fresh review with this run as evidence
        if self.state.problem.defined:
            self._review_timer.start(300)

    def _after_run(self):
        s = self.state
        run = s.runs[-1] if s.runs else None
        if not run or run.get("build_error") or not run["total"]:
            return
        normal = [r for r in run["results"] if not next((t for t in s.tests if t.id == r["id"] and t.origin == "stress"), None)]
        if all(r.get("ok") for r in normal) and run["code_hash"] != self._measured_for and self.brain.oracle:
            self._measured_for = run["code_hash"]
            code = s.code.get("text", "")

            def work():
                m = self.brain.measure(code)
                if m:
                    m["code_hash"] = _hash(code)

                    def done():
                        s.measured = m
                        self._render_timer.start()
                    self.post(done)
            threading.Thread(target=work, daemon=True).start()

    def debug(self):
        self.set_tab("debug")
        self.state.debug = {"summary": "Debugging: running the tests, shrinking the failing input, tracing it…"}
        self.render()
        self.brain.debug(self.state.code.get("text", ""))

    def redesign(self):
        if not self.state.problem.defined:
            self.ctl.overlay.show_status("The problem isn't clear yet: nothing to design", 3000)
            return
        self.brain.design()
        self.brain.make_tests()

    def add_test(self):
        sig = self.state.problem.signature
        hint = ", ".join(n for n, _ in sig.get("params", [])) if sig else "the arguments"
        text, ok = QInputDialog.getMultiLineText(
            None, "Cue Trainer: add a test",
            f"Arguments as a JSON array, in order ({hint}).\nOptionally a second line with the expected output\n"
            f"(leave it out and the reference solution computes it).\n\nExample:\n[[2, 7, 11, 15], 9]\n[0, 1]")
        if not ok or not text.strip():
            return
        lines = [l for l in text.strip().splitlines() if l.strip()]
        try:
            args = json.loads(lines[0])
            if not isinstance(args, list):
                args = [args]
            expected = json.loads(lines[1]) if len(lines) > 1 else None
        except ValueError as e:
            self.ctl.overlay.show_status(f"That isn't valid JSON: {e}", 5000)
            return
        self.brain.add_custom_test(args, expected)

    # ------------------------------------------------------------------ hotkeys / buttons
    def hotkey(self, action: str):
        if action == "run_tests":
            self.run_tests()
        elif action == "debug":
            self.debug()
        elif action == "windows":
            self.toggle_windows()

    def toggle_windows(self):
        show = not any(w.isVisible() for w in self.windows)
        for w in self.windows:
            w.setVisible(show)

    def toggle_timer(self):
        s = self.state
        if not s.estimate.started_at or s.estimate.paused_left >= 0:
            s.start_timer()
        else:
            s.pause_timer()
        self._set_overlay_actions()
        self.render_spec()

    def add_minutes(self):
        self.state.estimate.extra_s += 300
        if not self.state.estimate.started_at:
            self.state.start_timer()
        self.render_spec()

    def _set_overlay_actions(self):
        s = self.state
        running = s.estimate.started_at and s.estimate.paused_left < 0
        self.ctl.overlay.set_actions([
            ("📋 Paste problem", self.paste_problem),
            ("⏸ Pause" if running else "⏱ Start timer", self.toggle_timer),
            ("+5 min", self.add_minutes),
            ("▶ Tests", self.run_tests),
            ("⚡ Debug", self.debug),
            ("✕ End", self.ctl.end_trainer),
        ])

    def set_tab(self, key: str):
        self._tab = key
        for k, b in self.tab_btns.items():
            b.setChecked(k == key)
        tools = {
            "blocks": [("Pseudocode" if self._show_code else "Code", "Switch between pseudocode and code", self._toggle_code)],
            "mycode": [("▶ Run tests", "Ctrl+Alt+R", self.run_tests), ("⚡ Debug", "Ctrl+Alt+D", self.debug),
                       ("Open file", str(self.path), lambda: vscode.open_file(self.path, self.tcfg.get("editor") or "code"))],
            "tests": [("▶ Run all", "Ctrl+Alt+R", self.run_tests), ("+ Add test", "Your own test case", self.add_test),
                      (f"Auto-run: {'on' if self.auto_run else 'off'}", "Run the tests every time you save", self._toggle_autorun)],
            "debug": [("⚡ Debug now", "Ctrl+Alt+D", self.debug)],
        }[key]
        self.code_win.set_tools(tools)
        self.render_code()

    def _toggle_code(self):
        self._show_code = not self._show_code
        self.set_tab("blocks")

    def _toggle_autorun(self):
        self.auto_run = not self.auto_run
        self.set_tab("tests")

    def _design_link(self, url: str):
        kind, _, arg = url.partition(":")
        s = self.state
        if kind == "pin":
            for a in s.approaches:
                a.chosen = (a.id == arg) and not a.chosen
            self.render_design()
        elif kind == "toggle":
            a = next((a for a in s.approaches if a.id == arg), None)
            shown = arg in self._expanded or (a and a.recommended and f"-{arg}" not in self._expanded)
            self._expanded.discard(arg)
            self._expanded.discard(f"-{arg}")
            if shown and a and a.recommended:
                self._expanded.add(f"-{arg}")
            elif not shown:
                self._expanded.add(arg)
            self.render_design()

    def _code_link(self, url: str):
        kind, _, arg = url.partition(":")
        if kind == "test":
            self._open_tests ^= {arg}
            self.render_code()
        elif url == "trace":
            self._show_trace = not self._show_trace
            self.render_code()

    # ------------------------------------------------------------------ answers (from the Controller)
    def show_answer(self, status: str, text: str, new: bool):
        if not self.answers.isVisible():
            self.answers.show()
        self.answers.show_answer(status, text, new)

    def answer_prompt(self, trigger_desc: str, fmt: str, transcript: str) -> str:
        return prompts.ANSWER_USER.format(fmt=fmt, trigger=trigger_desc, name=self.name, NAME=self.name.upper(),
                                          state=self.state.brief(include_design=True, include_code=True),
                                          transcript=transcript or "(nothing transcribed yet)")

    # ------------------------------------------------------------------ drawing
    def render(self):
        self.render_spec()
        open_n = len(self.state.open_questions())
        self.ask_win.set_status(f"{open_n} to ask" if open_n else "")
        self.ask_win.set_html(views.questions_html(self.state, self.fs))
        self.howto_win.set_html(views.walkthrough_html(self.state, self.fs))
        self.render_design()
        self.render_code()
        self._update_marks()

    def render_spec(self):
        self.ctl.overlay.set_home(views.spec_header(self.state), views.spec_html(self.state, self.ctl.overlay.fs))

    def render_design(self):
        s = self.state
        self.design_win.set_status(s.chosen_approach().time if s.chosen_approach() else "")
        self.design_win.set_html(views.design_html(s, self.fs, self._expanded))

    def render_code(self):
        s, lexer = self.state, self.lang.lexer
        last = s.runs[-1] if s.runs else None
        if last and not self._running:
            fresh = last.get("code_hash") == _hash(s.code.get("text", ""))
            self.code_win.set_status(("build error" if last.get("build_error") else f"{last['passed']}/{last['total']} ✓")
                                     + ("" if fresh else " (older code)"))
        if self._tab == "blocks":
            html = views.blocks_html(s, self.fs, lexer, self._show_code)
        elif self._tab == "mycode":
            html = views.mycode_html(s, self.fs, lexer)
        elif self._tab == "tests":
            html = views.tests_html(s, self.fs, self._open_tests)
        else:
            html = views.debug_html(s, self.fs, lexer, self._show_trace)
        self.code_win.set_html(html)

    def _update_marks(self):
        s = self.state
        marks = [{**v, "line": ln} for ln, v in views.relocate(s.code.get("lines") or {}, s.code.get("text", "")).items()]
        marks += [{"line": e["line"], "mark": "red", "why": e.get("message", "")} for e in (s.code.get("syntax") or []) if e.get("line")]
        self._marks_cache = {"path": str(self.path), "marks": marks, "version": s.version, "language": self.lang.key,
                             "summary": s.code.get("summary", "")}

    def _tick(self):
        self.brain.tick()
        if self.state.estimate.started_at:
            self.render_spec()

    # ------------------------------------------------------------------ end
    def save(self):
        self.state.save(self.folder / "state.json")

    def save_quietly(self):
        """Save the state and the session record without touching any window (any thread: the
        console being closed, Windows shutting down)."""
        try:
            self.save()
            self._write_record()
        except Exception:
            log.exception("couldn't save the trainer session")

    def end(self):
        """Save the session record and close everything (safe to call twice)."""
        if getattr(self, "_ended", False):
            return
        self._ended = True
        for t in (self._tick_timer, self._render_timer, self._syntax_timer, self._review_timer, self._listen_timer,
                  self._save_timer):
            t.stop()
        self.watcher.stop()
        self.bridge.stop()
        self.save()
        try:
            self._write_record()
        except Exception:
            log.exception("couldn't write the trainer session record")
        self.brain.close()
        for w in self.windows:
            w.save_geometry()
            w.close()
            w.deleteLater()

    def _write_record(self):
        s = self.state
        if not s.problem.title and not s.requirements and not s.code.get("text", "").strip():
            return
        SESSIONS.mkdir(parents=True, exist_ok=True)
        used = (time.time() - s.estimate.started_at) / 60 if s.estimate.started_at else None
        out = [f"# {s.problem.title or 'Coding round'} ({self.lang.name})", "",
               f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(self.started))}"
               + (f" · {s.problem.difficulty}" if s.problem.difficulty else "")
               + (f" · estimate {s.estimate.minutes} min" if s.estimate.minutes else "")
               + (f" · used {used:.0f} min" if used is not None else ""), ""]
        if s.problem.summary:
            out += [s.problem.summary, ""]
        out.append("## Requirements")
        for r in s.live_requirements():
            out.append(f"- [{'x' if r.met == 'yes' else ' '}] {r.text}" + (" (assumed)" if r.status == "assumed" else ""))
        out += ["", "## Questions"]
        for q in s.questions:
            if q.status != "dropped":
                out.append(f"- {q.text}" + (f" → {q.answer}" if q.answer else f" ({q.status})"))
        if s.approaches:
            out += ["", "## Approaches"]
            for a in s.approaches:
                tag = " ★" if a.recommended else ""
                tag += " (yours)" if s.code.get("detected_approach") == a.id else ""
                out.append(f"- **{a.name}**{tag}: {a.time} time, {a.space} space. {a.why_not}".rstrip())
        if s.runs:
            last = s.runs[-1]
            out += ["", "## Result", f"Tests: {last['passed']}/{last['total']} passing" if not last.get("build_error")
                    else "Didn't build at the end."]
            if s.measured.get("label") and s.measured.get("code_hash") == _hash(s.code.get("text", "")):
                out.append(f"Measured: {s.measured['label']}")
        if s.debug.get("summary"):
            out += ["", "## Last debug", s.debug["summary"]]
        if s.follow_ups:
            out += ["", "## Follow-ups"] + [f"- {f}" for f in s.follow_ups]
        if s.code.get("text", "").strip():
            out += ["", "## Your code", f"```{self.lang.key}", s.code["text"].rstrip(), "```"]
        path = SESSIONS / f"{self.stamp} {_safe(s.problem.title or 'coding round')}.md"
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
        log.info("trainer session saved to %s", path)


SKIP_PREFIXES = ("//", "#include", "#!", "--", "%", ";", "/*", "*", "'", "import ", "from ", "using ", "package ",
                 "<?php", "module ")


def has_own_code(text: str, starter: str, comment: str) -> bool:
    """Is there anything of yours in the file yet, beyond the starter's comments and includes?
    (Running tests against an empty starter only produces a wall of failures.)"""
    def meaningful(t: str) -> set[str]:
        out = set()
        for line in t.splitlines():
            s = line.strip()
            if not s or s.startswith(SKIP_PREFIXES) or (comment == "#" and s.startswith("#")):
                continue
            out.add(s)
        return out
    return bool(meaningful(text) - meaningful(starter))


def _safe(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in " -_").strip()[:60] or "session"
