"""Controller: wires audio -> STT -> transcript -> triggers -> LLM -> panel, plus live notes."""
from __future__ import annotations

import html
import logging
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QCursor, QIcon
from PyQt6.QtWidgets import QApplication, QInputDialog, QMenu, QSystemTrayIcon

from . import prompts
from .config import ROOT, resolve
from .context import load_context, load_vocabulary
from .llm import ClaudeCLIBackend, OllamaBackend, make_backend, make_local
from .notes import NoteTaker, spawn_detached
from .overlay import Overlay, bullets_html, grouped_html, load_ui_state, save_ui_state
from .practice import PracticeSession
from .recap import BRIEF, brief_is_current, last_call_items, work_recap
from .transcript import Transcript
from urllib.parse import quote
from .triggers import addressed_to_other, looks_like_question, mentions_name

log = logging.getLogger(__name__)
TRIGGERS = ["auto", "smart", "name", "manual"]


class Bridge(QObject):
    """Cross-thread signals -> Qt main thread."""
    text = pyqtSignal(object, str)           # Utterance, text
    speaking = pyqtSignal(str, bool)
    token = pyqtSignal(int, str)
    done = pyqtSignal(int, str, object)
    hotkey = pyqtSignal(str)
    status = pyqtSignal(str, int)
    title = pyqtSignal(str)
    notes = pyqtSignal(str)
    reload_context = pyqtSignal()
    audio_changed = pyqtSignal(str)
    smart_yes = pyqtSignal(object)           # turn key the local classifier said is for you


class Controller(QObject):
    def __init__(self, cfg, audio: bool = True, mode: str | None = None):
        super().__init__()
        self.cfg = cfg
        self.name = cfg.user.name
        self.mode: str | None = None       # set once you pick Work / Interview
        self.trigger_mode = {m: cfg.modes[m]["trigger"] for m in cfg.modes}
        self.transcript = Transcript(cfg.transcript.buffer_s)
        self.bridge = Bridge()
        self.overlay = Overlay(cfg.overlay)
        self.backend = make_backend(cfg.llm)
        self.local: OllamaBackend | None = None     # warm local model: mid-call fallback
        self.audio = None
        self.speakers = None                        # voice fingerprints (you vs. room; who's talking)
        self.practice: PracticeSession | None = None
        self._enroll: dict | None = None            # voice setup in progress
        self.script = bool(load_ui_state().get("script", False))
        self._last_tab = 0.0
        self.notes: NoteTaker | None = None
        self.req_id = 0
        self.req_kind = ""
        self.req_status = ""
        self.last_answer = ""
        self._system = ""
        self._last_user = ""
        self._req_backend = None
        self._claude_down_until = 0.0
        self.me_speaking = False
        self.them_speaking = False
        self._fired_turn_key = None
        self._first_token = False
        self._stt_ready = not audio
        # fade state
        self._visible_since = 0.0
        self._spoke_s = 0.0
        self._silence_since: float | None = None
        self._last_tick = time.monotonic()
        self._refreshing = False
        self._brief_mtime = self._mtime(BRIEF)
        self._next_brief_check = 0.0

        b = self.bridge
        b.text.connect(self._on_text)
        b.speaking.connect(self._on_speaking)
        b.token.connect(self._on_token)
        b.done.connect(self._on_done)
        b.hotkey.connect(self._on_hotkey)
        b.status.connect(lambda t, ms: self.overlay.show_status(t, ms))
        b.title.connect(self.overlay.set_base_title)
        b.notes.connect(lambda md: self.overlay.set_notes(md_html(md)))
        b.reload_context.connect(self.load_mode)
        b.audio_changed.connect(self._on_audio_changed)
        b.smart_yes.connect(self._on_smart_yes)
        ov = self.overlay
        ov.mode_clicked.connect(self._set_mode)
        ov.trigger_clicked.connect(lambda: self._on_hotkey("cycle_trigger"))
        ov.ask_clicked.connect(lambda: self.ask("manual"))
        ov.close_clicked.connect(self.quit)
        ov.devices_clicked.connect(self._device_menu)
        ov.script_toggled.connect(self._set_script)
        ov.explain_clicked.connect(lambda: self.ask("explain"))
        ov.voice_setup_clicked.connect(self.start_voice_setup)
        ov.speaker_clicked.connect(self._name_speaker)
        ov.set_script(self.script)

        self._tick_timer = QTimer(self, interval=200, timeout=self._tick)
        self._tick_timer.start()
        self._build_tray()
        self._register_hotkeys()
        ov.start()
        if audio:
            self._start_audio()
        self._update_title()
        spawn_detached("recover")  # finish any earlier call that closed before its notes were saved
        if mode or not cfg.ask_mode_on_start:
            self._set_mode(mode or cfg.mode)
        else:
            ov.show_chooser()

    # ------------------------------------------------------------------ setup
    def _set_mode(self, mode: str):
        if mode == "practice":
            self.start_practice(self.mode or "work")
            return
        if self.practice:  # Work/Interview toggle during practice switches the question set
            self.start_practice(mode)
            return
        if mode == self.mode:
            return
        first = self.mode is None
        self.mode = mode
        if first and self.cfg.notes.enabled:
            self.notes = self._new_notes()
            # anything said while you were choosing still counts, at the time it was said
            for l in self.transcript.lines(include_partials=False):
                self.notes.add_line(self._label(l.speaker), l.text, datetime.fromtimestamp(l.t_start))
        elif self.notes:
            self.notes.write_sidecar()
        self.load_mode()

    def _new_notes(self) -> NoteTaker:
        return NoteTaker(self.cfg.notes, self.cfg.llm, self.name,
                         lambda: "practice" if self.practice else (self.mode or "work"),
                         lambda md: self.bridge.notes.emit(md), self.cfg.user.aliases)

    def load_mode(self):
        if self.mode is None:
            return
        context, files = load_context(self.cfg.modes[self.mode]["context"])
        self._system = prompts.system_prompt(self.name, self.mode, context, self.cfg.user.aliases)
        self.backend.configure(self._system)
        log.info("mode=%s trigger=%s backend=%s context=%d chars from %s",
                 self.mode, self.trigger, self.backend.name, len(context), files)
        # keep the local model loaded with the same context, so a mid-call fallback is instant
        if self.backend.name != "ollama" and OllamaBackend.available(self.cfg.llm.ollama_url):
            self.local = self.local or make_local(self.cfg.llm)
            self.local.configure(self._system)
        self.overlay.set_mode(self.mode, self.trigger)
        if not self.practice:
            self._set_home(files)
        self._update_title()
        self._refresh_tray()
        if isinstance(self.backend, ClaudeCLIBackend):
            threading.Thread(target=self._check_backend, args=(self.backend,), daemon=True).start()

    def _set_home(self, files: list[str]):
        fs = self.cfg.overlay.font_size
        if self.mode == "work":
            header, groups = work_recap()
            self.overlay.set_home(header, grouped_html(groups + last_call_items(self.name), fs))
            if not brief_is_current():
                self.refresh_worklog()  # e.g. Monday before the 7:30 task ran, or the PC was off
        else:
            names = ", ".join(os.path.basename(f) for f in files if "interview" in f) or "no files yet — add your resume to profiles/interview/"
            self.overlay.set_home("Interview mode", bullets_html([
                "Every question they ask gets talking points automatically.",
                f"Using: {names}",
                "Press Tab if it misses one.",
            ], fs))

    def _update_title(self):
        if not self._stt_ready:
            t = "Loading speech model…"
        else:
            brain = "local" if self._claude_down() else self.backend.name
            t = f"Listening · {brain}"
        self.bridge.title.emit(t)

    def _check_backend(self, backend):
        if not backend._warm.wait(30) or backend.last_error:
            err = backend.last_error or "no response"
            log.error("Claude CLI unusable at startup (%s)", err)
            self._mark_claude_down(err)
            hint = " — run `claude auth login`" if "auth" in err.lower() or "log in" in err.lower() else ""
            if self.local or OllamaBackend.available(self.cfg.llm.ollama_url):
                self.bridge.status.emit(f"Claude unavailable{hint}. Answering with local {self.cfg.llm.ollama_model}.", 6000)
            else:
                self.bridge.status.emit(f"Claude unavailable{hint}, and Ollama isn't running.", 8000)

    @property
    def trigger(self) -> str:
        return self.trigger_mode[self.mode] if self.mode else "manual"

    def _label(self, speaker: str) -> str:
        return "THEM" if speaker == "them" else self.name.upper()

    # ------------------------------------------------------------------ audio
    def _start_audio(self):
        from .audio import AudioManager, Segmenter
        from .stt import Transcriber

        stt_cfg, a = self.cfg.stt, self.cfg.audio
        prefs = load_ui_state().get("devices", {})
        if stt_cfg.speaker_id:
            from .speakers import Speakers
            if Speakers.available():
                v = stt_cfg.voice_match
                # "is this you" is a bit more forgiving: your voice on a different day/mic still passes
                self.speakers = Speakers(me_threshold=v - 0.05, match_threshold=v + 0.05, new_threshold=v)
        self.overlay.set_voice_status("🗣 Voice set up ✓" if self.speakers and self.speakers.me is not None
                                      else "🗣 Set up my voice")
        self.stt = Transcriber(stt_cfg, lambda u, t: self.bridge.text.emit(u, t),
                               load_vocabulary(stt_cfg.vocabulary), self.speakers)
        self.stt.start()
        self.audio = AudioManager(prefs.get("them", a.loopback_device), prefs.get("me", a.mic_device))
        self.audio.on_change = lambda reason: self.bridge.audio_changed.emit(reason)
        self.audio.start()
        speak = lambda src, s: self.bridge.speaking.emit(src, s)
        common = dict(aggressiveness=stt_cfg.vad_aggressiveness, silence_s=stt_cfg.utterance_silence_s,
                      max_utt_s=stt_cfg.max_utterance_s, partial_every_s=stt_cfg.partial_every_s)
        self.segs = [
            Segmenter("them", self.audio.them, self.stt.submit, speak, **common),
            Segmenter("me", self.audio.me, self.stt.submit, speak, emit_utterances=stt_cfg.transcribe_mic,
                      gate=(lambda: self.them_speaking) if a.echo_guard else None,
                      **{**common, "aggressiveness": stt_cfg.mic_vad_aggressiveness}),
        ]
        for s in self.segs:
            s.start()
        self._on_audio_changed("")
        threading.Thread(target=self._announce_ready, daemon=True).start()

    def _announce_ready(self):
        self.stt.ready.wait()
        self._stt_ready = True
        self._update_title()

    def _on_audio_changed(self, reason: str):
        if not self.audio:
            return
        self.overlay.set_devices(_short(self.audio.them.device_name), _short(self.audio.me.device_name))
        if reason:
            self.overlay.show_status(f"Audio: {_short(self.audio.them.device_name)} / "
                                     f"{_short(self.audio.me.device_name)}", 4000)

    def _device_menu(self, source: str):
        """Pick the call-audio or mic device (from the chooser, the level dots, or the tray)."""
        if not self.audio:
            return
        menu = QMenu()
        self._fill_device_menu(menu, source)
        menu.exec(QCursor.pos())

    def _fill_device_menu(self, menu: QMenu, source: str):
        menu.clear()
        loopbacks, mics = self.audio.devices() if self.audio else ([], [])
        current = self.audio.want[source] if self.audio else "default"
        options = [("default", "Windows default (follows headset plug/unplug)")] + \
                  [(d.replace(" [Loopback]", ""), _short(d)) for d in (loopbacks if source == "them" else mics)]
        for value, label in options:
            a = menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(value == current)
            a.triggered.connect(lambda _=False, v=value: self._choose_device(source, v))

    def _choose_device(self, source: str, value: str):
        state = load_ui_state()
        state.setdefault("devices", {})[source] = value
        save_ui_state(state)
        threading.Thread(target=self.audio.set_device, args=(source, value), daemon=True).start()

    def _register_hotkeys(self):
        import keyboard
        for action, combo in self.cfg.hotkeys.items():
            # suppress=False: the key still reaches whatever app you're in
            keyboard.add_hotkey(combo, lambda a=action: self.bridge.hotkey.emit(a), suppress=False)

    # ------------------------------------------------------------------ events
    def _on_text(self, utt, text: str):
        if self.practice and utt.source == "them":
            return  # its own text-to-speech coming back through the speakers
        # someone else talking into your mic is another participant, not you
        speaker = "them" if (utt.source == "them" or utt.who == "room") else "me"
        who = "In room" if utt.who == "room" else (utt.who if utt.source == "them" else "")
        line = self.transcript.add(speaker, text, utt.t_start, utt.t_end, (utt.source, utt.utt_id), utt.final,
                                   who, utt.source)
        self._refresh_transcript_pane()
        if not utt.final:
            return
        label = Transcript.label(line, self.name)
        print(f"{datetime.now():%H:%M:%S} {label:>6}: {text}", flush=True)
        log.info("%s: %s", label, text)
        if self._enroll is not None and utt.source == "me" and utt.emb is not None:
            self._enroll_sample(utt)
            return
        if self.notes:
            self.notes.add_line(label, text, datetime.fromtimestamp(utt.t_start))
        if self.practice:
            if speaker == "me":
                self.practice.on_my_words(text)
                if self.speakers and utt.emb is not None:  # practice doubles as voice training
                    self.speakers.add_me_sample(utt.emb, utt.t_end - utt.t_start)
            return
        if speaker == "them" and self.mode:
            self._maybe_auto_trigger(text)

    def _refresh_transcript_pane(self):
        rows = []
        finals = {id(l) for l in self.transcript.lines(include_partials=False)}
        for l in self.transcript.lines()[-120:]:
            if l.speaker == "me":
                who, color = self.name.upper(), "#9be3b0"
            elif l.who.startswith("Speaker "):  # unnamed: click to say who it is
                who, color = f"<a href='spk:{quote(l.who)}' style='color:#9fc0ff'>{l.who} ✎</a>", "#9fc0ff"
            else:
                who, color = html.escape(l.who or "THEM"), "#9fc0ff"
            partial = id(l) not in finals
            style = "color:rgba(244,247,251,0.55); font-style:italic" if partial else ""
            rows.append(f"<div style='margin:3px 0'><span style='color:{color}; font-weight:600'>{who}</span> "
                        f"<span style='color:rgba(210,220,235,0.45)'>{datetime.fromtimestamp(l.t_start):%H:%M:%S}</span>"
                        f"<br><span style='{style}'>{html.escape(l.text)}</span></div>")
        self.overlay.set_transcript("".join(rows))

    def _maybe_auto_trigger(self, last_utt: str):
        trig = self.trigger
        if trig == "manual" or self.me_speaking:
            return
        # the turn is identified by when it started, so one long turn doesn't fire repeatedly
        turn, turn_key = self.transcript.current_them_turn()
        is_q = looks_like_question(last_utt)
        named = mentions_name(turn, self.cfg.user.aliases) and (turn_key != self._fired_turn_key or is_q)
        if trig == "auto":
            fire = is_q
        elif trig == "smart":
            fire = named
            if not named and is_q and turn_key != self._fired_turn_key and self.local \
                    and not addressed_to_other(last_utt, self.cfg.user.teammates or [], self.cfg.user.aliases):
                threading.Thread(target=self._classify, args=(turn_key,), daemon=True).start()
        else:  # name
            fire = named
        if fire:
            self._fired_turn_key = turn_key
            self.ask(trig if trig != "smart" else "name")

    SMART_Q = ("""You're watching a live {kind}. {name} works on: {streams}.
Recent transcript (THEM = others, {NAME} = {name}; newest last):
{transcript}

The last speaker just asked something. Should {name} be the one to answer? YES if it's aimed at {name}, or it's an open question to the group about something {name} works on. NO if it's aimed at someone else, rhetorical, or not about {name}'s work. Reply with exactly YES or NO.
""")

    def _classify(self, turn_key):
        """Local yes/no: is this unnamed question for you? (smart trigger)"""
        from .llm import local_chat
        try:
            from worklog.workstreams import job_names
            streams = ", ".join(job_names()) or "their projects"
        except Exception:
            streams = "their projects"
        prompt = self.SMART_Q.format(kind="team call" if self.mode == "work" else "interview", name=self.name,
                                     NAME=self.name.upper(), streams=streams,
                                     transcript=self.transcript.format(90, self.name))
        try:
            t = time.monotonic()
            out = local_chat(prompt, "Answer YES or NO.", self.cfg.llm.ollama_model, self.cfg.llm.ollama_url, timeout=8)
            log.info("smart trigger: %s (%.1fs)", out.strip()[:10], time.monotonic() - t)
            if out.strip().upper().startswith("YES"):
                self.bridge.smart_yes.emit(turn_key)
        except Exception as e:
            log.debug("smart trigger classifier failed: %s", e)

    def _on_smart_yes(self, turn_key):
        _, current = self.transcript.current_them_turn()
        if current == turn_key and turn_key != self._fired_turn_key and not self.me_speaking:
            self._fired_turn_key = turn_key
            self.ask("smart")

    def _on_speaking(self, source: str, speaking: bool):
        if source == "me":
            self.me_speaking = speaking
        else:
            self.them_speaking = speaking

    def _on_hotkey(self, action: str):
        if action == "answer":
            now = time.monotonic()
            double = now - self._last_tab < 0.6
            self._last_tab = now
            if self.practice:
                self._practice_hint()
            elif double:
                self.ask("manual", fmt="SCRIPT")  # double-tap: same question, full script
            else:
                self.ask("manual")
        elif action == "explain":
            self.ask("explain")
        elif action == "toggle_mode":
            modes = list(self.cfg.modes)
            self._set_mode(modes[(modes.index(self.mode) + 1) % len(modes)] if self.mode else modes[0])
        elif action == "cycle_trigger" and self.mode:
            self._set_trigger(TRIGGERS[(TRIGGERS.index(self.trigger) + 1) % len(TRIGGERS)])
        elif action == "hide":
            if self.overlay.showing_answer:
                self.overlay.fade_out()
            elif self.last_answer:
                self._reset_fade()
                self.overlay.show_answer(self.req_status, self.last_answer)
        elif action == "quit":
            self.quit()

    # ------------------------------------------------------------------ LLM
    def _claude_down(self) -> bool:
        return time.time() < self._claude_down_until

    def _mark_claude_down(self, reason: str):
        # usage limits last hours; network blips don't
        self._claude_down_until = time.time() + (1800 if "limit" in reason.lower() else 120)
        self._update_title()

    def ask(self, kind: str, fmt: str | None = None, transcript: str | None = None, status: str = ""):
        if self.mode is None:
            self.overlay.show_status("Pick Work, Interview or Practice first", 2000)
            return
        fmt = fmt or ("EXPLAIN" if kind == "explain" else "SCRIPT" if self.script else "POINTS")
        backend = self.local if (self._claude_down() and self.local) else self.backend
        window = 60 if kind == "explain" else (self.cfg.transcript.buffer_s if backend.name != "ollama" else 120)
        if transcript is None:
            if kind in ("manual", "explain") and not any(
                    l.speaker == "them" for l in self.transcript.lines(window, include_partials=True)):
                self._reset_fade()
                self.overlay.show_answer(f"{self.mode.upper()} · {kind}",
                                         "Q: Nothing heard from the call yet\n"
                                         "- If people are talking, the Them light should be green — click it and pick "
                                         "the device your call audio plays on\n"
                                         "- Otherwise press Tab again right after you're asked something")
                self.last_answer = ""
                return
            transcript = self.transcript.format(window, self.name)
        self._last_user = prompts.user_prompt(self.name, kind, transcript, fmt)
        self.req_kind = kind
        extra = "" if fmt == "POINTS" else f" · {fmt.lower()}"
        self.req_status = status or f"{self.mode.upper()} · {kind}{extra} · {datetime.now():%H:%M:%S}"
        self._start_request(backend)
        self._reset_fade()
        if kind in ("manual", "explain", "practice"):
            self.overlay.show_answer(self.req_status, "")

    def _start_request(self, backend):
        self._t_ask = time.monotonic()
        self._first_token = False
        self._got_output = False
        self._req_backend = backend
        log.info("asking %s (%s)", backend.name, self.req_kind)
        rid = self.req_id = backend.ask(self._last_user, lambda r, t: self.bridge.token.emit(r, t),
                                        lambda r, t, e: self.bridge.done.emit(r, t, e))
        if backend is not self.local:
            # a stalled Claude call is as bad as a failed one mid-conversation
            QTimer.singleShot(int(self.cfg.llm.first_token_timeout_s * 1000),
                              lambda: self._got_output or rid != self.req_id or self._fallback("no response"))

    def _fallback(self, reason: str) -> bool:
        """Re-ask the same question on the local model. Returns True if it did."""
        if self._req_backend is self.local or not self.local:
            return False
        log.warning("Claude failed mid-call (%s); answering with the local model", reason)
        self._mark_claude_down(reason)
        self.overlay.show_status("Claude didn't answer — using the local model", 3000)
        self._start_request(self.local)
        return True

    @staticmethod
    def _maybe_skip(text: str) -> bool:
        t = text.strip().upper()
        return t.startswith("SKIP") or (len(t) < 5 and "SKIP".startswith(t))

    def _on_token(self, rid: int, text: str):
        if rid != self.req_id:
            return
        self._got_output = True   # even a SKIP means the model is alive (no fallback needed)
        if self._maybe_skip(text):
            return
        if not self._first_token:
            self._first_token = True
            self._reset_fade()
            log.info("first token after %.2fs", time.monotonic() - self._t_ask)
        self.overlay.show_answer(self.req_status, text)

    def _on_done(self, rid: int, text: str, err):
        if rid != self.req_id:
            return
        if err:
            log.error("LLM error: %s", err)
            if not self._fallback(str(err)):
                self.overlay.show_status(f"LLM error: {str(err)[:120]}", 5000)
            return
        log.info("answer done in %.2fs", time.monotonic() - self._t_ask)
        if self._maybe_skip(text):
            log.info("model: not a question for %s (SKIP)", self.name)
            if self.req_kind in ("manual", "explain"):
                self.overlay.show_answer(self.req_status, "Q: No question for you in the last few minutes\n"
                                         "- Press Tab again right after you're asked, or What? to decode the last speaker")
            elif self.overlay.showing_answer:
                self.overlay.fade_out()
            return
        self.last_answer = text
        self.overlay.show_answer(self.req_status, text)
        if self.notes:
            self.notes.add_assist(text)  # lets the post-call learning compare this with what you said
        if self.practice and self.req_kind == "practice":
            self._practice_actions(reviewed=True)
        log.info("ASSIST: %s", text.replace("\n", " | "))
        print("  ASSIST:\n    " + text.replace("\n", "\n    "), flush=True)

    # ------------------------------------------------------------------ fade logic + periodic checks
    def _reset_fade(self):
        self._visible_since = time.monotonic()
        self._spoke_s = 0.0
        self._silence_since = None

    @staticmethod
    def _mtime(path: str) -> float:
        p = resolve(path)
        return p.stat().st_mtime if p.exists() else 0.0

    def _tick(self):
        if self.notes:
            self.notes.tick()
        now = time.monotonic()
        if self.audio:
            self.overlay.set_levels(_level_state(self.audio.them, self.them_speaking, now),
                                    _level_state(self.audio.me, self.me_speaking, now))
        if now >= self._next_brief_check:
            # the brief lands before the (slower) all-time history, so pick it up as soon as it's written
            self._next_brief_check = now + 2
            m = self._mtime(BRIEF)
            if m != self._brief_mtime:
                self._brief_mtime = m
                if self.mode == "work":
                    log.info("new work brief — reloading")
                    self.load_mode()
        dt, self._last_tick = now - self._last_tick, now
        ov, c = self.overlay, self.cfg.overlay
        if self.practice and self.practice.answer_finished(self.me_speaking):
            self._practice_feedback()
        if not ov.showing_answer or self.practice or self._enroll is not None:
            return
        if self.me_speaking:
            self._spoke_s += dt
            self._silence_since = None
        elif self._spoke_s >= c.user_speech_min_s:
            if self._silence_since is None:
                self._silence_since = now
            elif now - self._silence_since >= c.fade_after_user_silence_s:
                ov.fade_out()
        if now - self._visible_since > c.max_visible_s:
            ov.fade_out()

    # ------------------------------------------------------------------ script / speakers
    def _set_script(self, on: bool):
        self.script = on
        state = load_ui_state()
        state["script"] = on
        save_ui_state(state)
        self.overlay.show_status("Script: full sentences to read word for word" if on
                                 else "Bullets: an opener + talking points", 2200)

    def _name_speaker(self, label: str):
        if not self.speakers:
            return
        name, ok = QInputDialog.getText(None, "Who is this?", f"Name for {label} (their voice is remembered):")
        name = name.strip()
        if not ok or not name:
            return
        self.speakers.name_speaker(label, name)
        self.transcript.relabel(label, name)
        if self.notes:
            self.notes.rename_speaker(label, name)
        self._refresh_transcript_pane()
        self.overlay.show_status(f"{label} is {name} — recognized automatically from now on", 3500)

    def _quick_note(self):
        from .journal import add
        text, ok = QInputDialog.getText(None, "Quick work note", "What did you do? (goes into your work history)")
        if ok and text.strip():
            add(text)
            self.overlay.show_status("Noted — it'll be in tomorrow's work log", 2500)

    # ------------------------------------------------------------------ voice setup
    ENROLL_TEXT = ("This week I've mostly been working on the data pipeline. The nightly job is running on the "
                   "new machines now, and the fetch step pulls from the main source with a backup as the fallback. "
                   "Next I'm writing up the fixes and checking the dashboard numbers with the team. Overall things "
                   "are moving, and I'll keep everyone posted once I hear back about the last few issues.")

    def start_voice_setup(self):
        if not self.speakers:
            self.overlay.show_status("Voice fingerprints aren't available (audio off or model missing)", 4000)
            return
        self._enroll = {"need": 20.0, "back_to_chooser": self.mode is None}
        self.speakers._me_samples, self.speakers._me_seconds = [], 0.0
        self.overlay.show_answer("VOICE SETUP · 0 / 20 s of speech",
                                 f"Q: Read this out loud at your normal pace (headphones on)\n{self.ENROLL_TEXT}")
        self.overlay.set_actions([("Cancel", self._end_voice_setup)])

    def _enroll_sample(self, utt):
        self.speakers.add_me_sample(utt.emb, utt.t_end - utt.t_start)
        got = self.speakers.me_seconds_collected()
        self.overlay.show_answer(f"VOICE SETUP · {min(got, 20):.0f} / 20 s of speech",
                                 f"Q: Keep reading — nearly there\n{self.ENROLL_TEXT}")
        if got >= self._enroll["need"]:
            self.speakers.save_me(blend_existing=False)
            self.overlay.set_voice_status("🗣 Voice set up ✓")
            self.overlay.show_answer("VOICE SETUP", "Q: Done\nVoice saved. Speech on your mic that isn't you "
                                     "will now show as \"In room\" and won't be learned as yours.")
            QTimer.singleShot(3000, self._end_voice_setup)

    def _end_voice_setup(self):
        back = self._enroll and self._enroll.get("back_to_chooser")
        self._enroll = None
        self.overlay.set_actions([])
        self.overlay.showing_answer = False
        if back or self.mode is None:
            self.overlay.show_chooser()
        else:
            self.overlay.fade_out()
            self.load_mode()

    # ------------------------------------------------------------------ practice
    def start_practice(self, kind: str):
        if self.notes and not self.practice:  # a real call was running: save it first
            self.notes.finalize()
            self.notes = None
        if self.practice:
            self.practice.stop()
        self.mode = kind
        self.practice = PracticeSession(kind)
        if self.notes is None and self.cfg.notes.enabled:
            self.notes = self._new_notes()
        self.load_mode()
        self.overlay.set_mode(kind, "practice")
        self._practice_next()

    def _practice_next(self):
        q = self.practice.next()
        if self.notes:
            self.notes.add_line("THEM (practice question)", q)
        self.overlay.show_answer(self.practice.progress(), f"Q: Answer out loud — I'm listening\n{q}")
        self._practice_actions(reviewed=False)

    def _practice_retry(self):
        q = self.practice.retry()
        self.overlay.show_answer(self.practice.progress(), f"Q: Try it again — out loud\n{q}")
        self._practice_actions(reviewed=False)

    def _practice_actions(self, reviewed: bool):
        if reviewed:
            self.overlay.set_actions([("↻ Try again", self._practice_retry), ("▶ Next question", self._practice_next),
                                      ("✕ Done practicing", self.end_practice)])
        else:
            self.overlay.set_actions([("💡 Hint (Tab)", self._practice_hint), ("⏭ Skip", self._practice_next),
                                      ("✕ Done practicing", self.end_practice)])

    def _practice_hint(self):
        self.ask("manual", transcript=f"THEM: {self.practice.question}",
                 status=f"{self.practice.progress()} · hint")

    def _practice_feedback(self):
        q = self.practice.question
        user = prompts.PRACTICE_FEEDBACK.format(name=self.name, question=q, answer=" ".join(self.practice.answer),
                                                short_question=q[:70])
        self._last_user = user
        self.req_kind = "practice"
        self.req_status = f"{self.practice.progress()} · coaching"
        self.overlay.show_answer(self.req_status, "")
        backend = self.local if (self._claude_down() and self.local) else self.backend
        self._start_request(backend)

    def end_practice(self):
        if not self.practice:
            return
        self.practice.stop()
        self.practice = None
        if self.speakers and self.speakers.me_seconds_collected() >= 10:
            self.speakers.save_me()
            self.overlay.set_voice_status("🗣 Voice set up ✓")
        if self.notes:
            self.notes.finalize()  # learning runs on what you said
            self.notes = None
        self.mode = None
        self.overlay.set_actions([])
        self.overlay.showing_answer = False
        self.overlay.show_status("Practice saved — your answers feed the voice learning", 4000)
        self.overlay.show_chooser()

    # ------------------------------------------------------------------ tray
    def _build_tray(self):
        self.tray = QSystemTrayIcon(QIcon(str(ROOT / "assets" / "cue.ico")))
        menu = QMenu()
        menu.addAction("Answer now", lambda: self.ask("manual"))
        menu.addSeparator()
        self.mode_group = QActionGroup(menu)
        for m in self.cfg.modes:
            a = QAction(f"{m.title()} mode", menu, checkable=True)
            a.triggered.connect(lambda _=False, m=m: self._set_mode(m))
            self.mode_group.addAction(a)
            menu.addAction(a)
        menu.addSeparator()
        self.trig_group = QActionGroup(menu)
        for t in TRIGGERS:
            a = QAction(f"Trigger: {t}", menu, checkable=True)
            a.triggered.connect(lambda _=False, t=t: self._set_trigger(t))
            self.trig_group.addAction(a)
            menu.addAction(a)
        menu.addSeparator()
        for source, label in (("them", "Call audio device"), ("me", "Microphone")):
            sub = menu.addMenu(label)
            sub.aboutToShow.connect(lambda s=sub, src=source: self._fill_device_menu(s, src))
        menu.addSeparator()
        menu.addAction("Add a quick note…", self._quick_note)
        menu.addAction("Practice answering…", lambda: self._set_mode("practice"))
        menu.addAction("Set up my voice…", self.start_voice_setup)
        menu.addSeparator()
        menu.addAction("Refresh work log now", self.refresh_worklog)
        menu.addAction("Build interview prep", self.build_interview_prep)
        menu.addAction("Open meeting notes folder", lambda: os.startfile(ROOT / "meetings")
                       if (ROOT / "meetings").exists() else self.overlay.show_status("No meeting notes yet", 2000))
        menu.addAction("Open profiles folder", lambda: os.startfile(resolve("profiles")))
        menu.addAction("Open app log", lambda: os.startfile(ROOT / "data" / "logs"))
        menu.addAction("Edit config", lambda: os.startfile(ROOT / "config.yaml"))
        menu.addSeparator()
        menu.addAction("End call && quit", self.quit)
        self.tray.setContextMenu(menu)
        self.tray.show()

    def _refresh_tray(self):
        for a in self.mode_group.actions():
            a.setChecked(bool(self.mode) and a.text().lower().startswith(self.mode))
        for a in self.trig_group.actions():
            a.setChecked(a.text().endswith(self.trigger))
        self.tray.setToolTip(f"Cue — {self.mode or 'choosing'} · {self.trigger} · {self.backend.name}")

    def _set_trigger(self, t):
        if not self.mode:
            return
        self.trigger_mode[self.mode] = t
        self.overlay.set_mode(self.mode, t)
        self.overlay.show_status({"auto": "Auto: answers every question",
                                  "smart": "Smart: when you're named, or a question is clearly meant for you",
                                  "name": "Name: answers when you're named",
                                  "manual": "Manual: only when you press Tab"}[t], 2600)
        self._refresh_tray()

    def refresh_worklog(self):
        if self._refreshing:
            return
        self._refreshing = True

        def run():
            self.bridge.status.emit("Updating last week's summary from your Claude sessions (~2 min)…", 150_000)
            r = subprocess.run([sys.executable, "-m", "worklog"], cwd=ROOT, capture_output=True, text=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self._refreshing = False
            ok = r.returncode == 0
            log.info("worklog finished rc=%s\n%s", r.returncode, (r.stdout + r.stderr)[-1500:])
            self.bridge.status.emit("Work log up to date." if ok else "Work log refresh failed — see the app log.", 4000)
            if ok and self.mode == "work":
                self.bridge.reload_context.emit()  # all_time.md may have changed too
        threading.Thread(target=run, daemon=True).start()

    def build_interview_prep(self):
        def run():
            self.bridge.status.emit("Building interview prep from your resume + history (~2 min)…", 150_000)
            r = subprocess.run([sys.executable, "-m", "worklog.interview_prep"], cwd=ROOT, capture_output=True,
                               text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            log.info("interview prep rc=%s\n%s", r.returncode, (r.stdout + r.stderr)[-1000:])
            self.bridge.status.emit("Interview prep ready (profiles/interview/prep.md)" if r.returncode == 0
                                    else "Interview prep failed — see the app log", 5000)
            if r.returncode == 0 and self.mode == "interview":
                self.bridge.reload_context.emit()
        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------------ shutdown
    def shutdown(self):
        """Save everything. Safe to call from any thread and more than once (red dot, tray,
        hotkey, or the console window being closed)."""
        if self.notes:
            self.notes.finalize()
        try:
            self.overlay.save_position()
        except Exception:
            pass

    def quit(self):
        if self.practice:
            self.practice.stop()
        self.shutdown()
        for s in getattr(self, "segs", []):
            s.stop()
        if self.audio:
            self.audio.stop()
        self.backend.close()
        self.tray.hide()
        QApplication.quit()


def _short(device: str) -> str:
    """'Microphone (HD Pro Webcam C920)' -> 'HD Pro Webcam C920'."""
    device = device.replace(" [Loopback]", "")
    m = re.match(r"^(?:Speakers|Headphones|Microphone|Headset|Line)\s*\((.+)\)$", device)
    return (m.group(1) if m else device)[:40]


def _level_state(cap, speaking: bool, now: float) -> tuple[str, str]:
    """(state, tooltip) for a level dot: speaking / live / silent / off."""
    if cap.device_name in ("none", "unavailable"):
        return "off", f"No device ({cap.device_name}) — click to pick one"
    if speaking:
        return "speaking", f"Hearing speech on {_short(cap.device_name)}"
    if now - cap.last_frame < 2 and cap.level > 0.002:
        return "live", f"Audio coming in on {_short(cap.device_name)}"
    return "silent", f"{_short(cap.device_name)} — quiet right now (click to change device)"


def md_html(md: str) -> str:
    """Tiny markdown -> HTML for the notes pane (headings, bullets, checkboxes, bold)."""
    out = []
    for line in md.splitlines():
        s = line.strip()
        if not s:
            continue
        esc = html.escape(s)
        esc = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc)
        if s.startswith("#"):
            out.append(f"<div style='color:#8fb4ff; font-weight:600; margin-top:8px'>{esc.lstrip('#').strip()}</div>")
        elif re.match(r"[-*] \[[ x]\]", s):
            box = "☑" if "[x]" in s[:5] else "☐"
            out.append(f"<div style='margin:2px 0 2px 6px'>{box} {esc[6:].strip()}</div>")
        elif s[0] in "-*•":
            out.append(f"<div style='margin:2px 0 2px 6px'>•&nbsp; {esc[1:].strip()}</div>")
        else:
            out.append(f"<div>{esc}</div>")
    return "".join(out)
