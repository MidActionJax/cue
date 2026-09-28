"""Live call notes: key points, decisions, action items — even when nobody's talking to you.

During the call the transcript is appended to data/calls/<stamp>.txt, and every ~90 s the new
part is folded into running notes in meetings/<stamp>.md (Haiku, via `claude -p`). When the
call ends, a detached process writes the polished final version (Sonnet), so quitting is instant:

    Cue.exe notes finalize <stamp>     (one call)
    Cue.exe notes recover              (any call that was never finalized)

The worklog job picks meetings/*.md up, so calls land in your work history too.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from typing import Callable

from .config import DATA, ROOT, resolve

log = logging.getLogger(__name__)
MEETINGS = ROOT / "meetings"
CALLS = DATA / "calls"

UPDATE = """You're keeping live notes for {name} during a {kind}. The transcript is auto-generated and several speakers have heavy accents, so it contains mis-heard words: infer what was meant from context, never copy garbled text, and drop anything you can't make sense of. THEM = other participants (names are only known if someone says them), {NAME} = {name}. {name} is often transcribed as {aliases} — those all mean {name}.

Glossary of names/terms that may appear (possibly mis-spelled in the transcript): {glossary}

CURRENT NOTES:
{notes}

NEW TRANSCRIPT SINCE LAST UPDATE:
{chunk}

Return the complete updated notes. Keep existing items unless the new transcript corrects them; merge duplicates; one line per item; attribute to people when you can tell who.
Only write down what was actually said. An action item needs someone to have committed to it or been asked to do it — never invent tasks. "Asked of me" is only for questions or requests aimed at {name}.
Use these sections and leave out any section with nothing in it:
## Key points
## Decisions
## Action items
- [ ] <owner>: <task>   ({name}'s items first)
## Asked of me / follow up
## Open questions
No preamble."""


def _drop_empty_sections(md: str) -> str:
    """Small local models often print every heading even with nothing under it."""
    out, block = [], []
    filler = re.compile(r"^\s*[-*]?\s*\(?(none|n/?a|nothing( yet)?|no .{0,30} yet)\)?\.?\s*$", re.I)
    lines = [l for l in md.strip().splitlines() if not filler.match(l)]
    for line in lines + ["## "]:
        if line.startswith("## "):
            if any(l.strip() for l in block[1:]):
                out += block
            block = [line]
        else:
            block.append(line)
    return "\n".join(out).strip()

FINAL = """Below is the full auto-generated transcript of a {kind} {name} was on ({when}, {minutes} min), plus the running notes taken live. Accents make the transcript noisy: infer meaning, don't quote garbled text.

{NAME} lines are what {name} actually said. Others often say {name}'s name and it gets transcribed as {aliases}: those all mean {name}, not a different person. If {name} has no lines after being asked something, {name} didn't answer on the call: list it under "Asked of me" and never write an answer for them.
The running notes were written live by a small model and can be wrong; where they disagree with the transcript, the transcript wins. Action items need someone to have committed to the task or been asked to do it.

Glossary of names/terms: {glossary}

How {name} talks (use it for the email draft): {voice}

Write the final notes {name} will keep and search later:
# {title} — {when}
## Summary (3–6 bullets: what the call was about and what came out of it)
## Decisions
## Action items (checkbox list, owner first; {name}'s items first)
## Updates by person (who reported what, when you can tell)
## Asked of me / what I said I'd do
## Open questions / risks
## Follow-up email draft
(Only if {name} owes someone something from this call: a short email {name} can send right after — to whoever asked — confirming what {name} will do and by when. Plain, friendly, in {name}'s own voice; no subject-line fluff, no "I hope this finds you well". Start with "To: <name>".)
Omit empty sections. No preamble.

RUNNING NOTES:
{notes}

TRANSCRIPT:
{transcript}"""

LEARN = """You maintain two small files that make {name}'s live call assistant better after every call.

VOICE (profiles/voice.md) — how {name} actually talks on calls: typical length, how answers are structured (result first? context first?), openers and phrases {name} really uses, formality, words to avoid because {name} never says them. End with "## Examples": 5–10 short answers in {name}'s own words (fix transcription errors, keep their phrasing), each preceded by the question it answered.

LEARNED (profiles/learned.md) — what the assistant should know next time:
## Facts from calls   (things {name} or others stated: statuses, numbers, decisions, owners — each with its date; newer replaces older)
## What {name} uses vs ignores   (which kinds of suggested points {name} actually said out loud, which were skipped or corrected)
## Recurring questions   (what gets asked, by whom if known, and the answer {name} gave)
## Preferences   (anything {name} corrected or clearly prefers)

This call ({when}, {kind}): THEM = other participants, {NAME} = what {name} actually said (mic transcription, may contain errors), "ASSIST (shown)" = the talking points on screen at that moment. Compare what was shown with what {name} said right after.

Rules: merge into the current files — keep what's still true, update what changed, drop trivia. VOICE at most 60 lines, LEARNED at most 90 lines (drop the oldest, least useful items first). This shapes phrasing and emphasis only: never conclude a topic or workstream should be dropped because {name} didn't talk about it. If this call taught nothing new for a file, return that file unchanged.

Output exactly, nothing else:
===VOICE===
<complete voice.md>
===LEARNED===
<complete learned.md>

CURRENT VOICE:
{voice}

CURRENT LEARNED:
{learned}

TRANSCRIPT:
{transcript}"""

KIND = {"work": "team/work call", "interview": "job interview", "practice": "practice session"}
VOICE = ROOT / "profiles" / "voice.md"
LEARNED = ROOT / "profiles" / "learned.md"


def _glossary() -> str:
    p = resolve("profiles/work/glossary.txt")
    return p.read_text(encoding="utf-8").strip()[:1500] if p.exists() else "(none)"


class NoteTaker:
    """One per call. Writes the transcript + a sidecar (mode, start) to data/calls/, and keeps
    running notes in meetings/. A call only counts as finished once its .done marker exists,
    so a call that never got finalized (window closed, usage limit) is picked up later."""

    def __init__(self, cfg, llm_cfg, name: str, get_mode: Callable[[], str], on_update: Callable[[str], None],
                 aliases: list[str] = ()):
        self.cfg, self.llm_cfg, self.name, self.get_mode, self.on_update = cfg, llm_cfg, name, get_mode, on_update
        self.aliases = [a for a in aliases if a.lower() != name.lower()] or [name]
        self.started = datetime.now()
        self.stamp = self.started.strftime("%Y-%m-%d_%H%M")
        self.transcript_path = CALLS / f"{self.stamp}.txt"
        self.notes_path = MEETINGS / f"{self.stamp}.md"
        self.notes = ""
        self._pending: list[str] = []
        self._words = 0
        self._total_words = 0
        self._last_update = time.monotonic()
        self._busy = False
        self._file = None
        self._finalized = False
        self.speaker_names: dict[str, str] = {}   # "Speaker 2" -> "Sam", named mid-call

    def _open(self) -> None:
        if self._file is None:
            CALLS.mkdir(parents=True, exist_ok=True)
            self._file = open(self.transcript_path, "a", encoding="utf-8")
            self.write_sidecar()

    def write_sidecar(self) -> None:
        CALLS.mkdir(parents=True, exist_ok=True)
        (CALLS / f"{self.stamp}.json").write_text(json.dumps(
            {"mode": self.get_mode(), "started": self.started.isoformat(), "speakers": self.speaker_names}),
            encoding="utf-8")

    def rename_speaker(self, label: str, name: str) -> None:
        self.speaker_names[label] = name
        self.write_sidecar()

    def add_line(self, who: str, text: str, when: datetime | None = None) -> None:
        self._open()
        line = f"{(when or datetime.now()):%H:%M:%S} {who}: {text}"
        self._file.write(line + "\n")
        self._file.flush()
        self._pending.append(line)
        n = len(text.split())
        self._words += n
        self._total_words += n

    def add_assist(self, text: str) -> None:
        """Record what was on screen, so the post-call learning can compare it with what you said."""
        self._open()
        flat = " | ".join(l.strip() for l in text.strip().splitlines() if l.strip())
        self._file.write(f"{datetime.now():%H:%M:%S} ASSIST (shown): {flat}\n")
        self._file.flush()

    def tick(self) -> None:
        """Called periodically from the UI thread; kicks off an update when enough is new."""
        if self._busy or not self._pending:
            return
        due = time.monotonic() - self._last_update >= self.cfg.update_every_s
        if self._words >= self.cfg.min_new_words and due:
            chunk, self._pending, self._words = "\n".join(self._pending), [], 0
            self._busy = True
            self._last_update = time.monotonic()
            threading.Thread(target=self._update, args=(chunk,), daemon=True).start()

    def _update(self, chunk: str) -> None:
        try:
            prompt = UPDATE.format(name=self.name, NAME=self.name.upper(), kind=KIND[self.get_mode()],
                                   aliases=", ".join(self.aliases), glossary=_glossary(),
                                   notes=self.notes or "(none yet)", chunk=chunk)
            self.notes = _drop_empty_sections(_live_llm(prompt, self.cfg, self.llm_cfg))
            MEETINGS.mkdir(parents=True, exist_ok=True)
            header = f"# {KIND[self.get_mode()].capitalize()} — {self.started:%Y-%m-%d %H:%M} (live notes)\n\n"
            self.notes_path.write_text(header + self.notes + "\n", encoding="utf-8")
            self.on_update(self.notes)
        except Exception as e:
            log.warning("notes update failed: %s", e)
            self._pending.insert(0, chunk)  # retry with the next batch
        finally:
            self._busy = False

    def finalize(self) -> None:
        """Hand off to a detached process so the app can close immediately. Safe to call twice."""
        if self._finalized:
            return
        self._finalized = True
        if self._file:
            self._file.close()
            self._file = None
        if self._total_words < self.cfg.min_call_words:
            log.info("call too short for notes (%d words)", self._total_words)
            if self.transcript_path.exists():
                _mark_done(self.stamp)
            return
        self.write_sidecar()
        spawn_detached("finalize", self.stamp)
        log.info("final notes will be written to %s", self.notes_path)


_claude_notes_down_until = 0.0


def _live_llm(prompt: str, cfg, llm_cfg) -> str:
    """auto: Claude Haiku (small usage, much better notes than a 7B model), switching to the
    local model for 30 min whenever Claude fails (e.g. usage limit). local / claude: forced."""
    global _claude_notes_down_until
    from worklog.summarize import claude_p

    from .llm import OllamaBackend, local_chat
    local_ok = cfg.live_backend != "claude" and OllamaBackend.available(llm_cfg.ollama_url)
    if cfg.live_backend != "local" and (time.time() > _claude_notes_down_until or not local_ok):
        try:
            return claude_p(prompt, "Output only the notes.", cfg.live_model, timeout=120, retries=0)
        except Exception as e:
            if not local_ok:
                raise
            log.warning("Claude notes failed (%s); using the local model for a while", e)
            _claude_notes_down_until = time.time() + 1800
    return local_chat(prompt, "Output only the notes.", llm_cfg.ollama_model, llm_cfg.ollama_url)


def spawn_detached(*args: str) -> None:
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    from .cli import self_cmd, self_cwd
    subprocess.Popen(self_cmd("notes", *args), cwd=self_cwd(), creationflags=flags, close_fds=True)


def _mark_done(stamp: str) -> None:
    (CALLS / f"{stamp}.done").write_text(datetime.now().isoformat(), encoding="utf-8")


def finalize(stamp: str) -> None:
    """Final notes (Sonnet) + learning for one call. Only marks the call done if both succeed,
    so a usage-limit failure gets retried by recover()."""
    from worklog.summarize import claude_p
    from . import config

    cfg = config.load()
    name = cfg.user.name
    meta = json.loads((CALLS / f"{stamp}.json").read_text(encoding="utf-8"))
    mode, start = meta["mode"], datetime.fromisoformat(meta["started"])
    transcript = (CALLS / f"{stamp}.txt").read_text(encoding="utf-8")
    for label, real in meta.get("speakers", {}).items():   # speakers you named mid-call
        transcript = transcript.replace(f"({label})", f"({real})")
    notes_path = MEETINGS / f"{stamp}.md"
    notes = notes_path.read_text(encoding="utf-8") if notes_path.exists() else "(none)"
    last = (CALLS / f"{stamp}.txt").stat().st_mtime
    minutes = max(1, int((last - start.timestamp()) // 60))
    if mode != "practice":
        title = "Interview" if mode == "interview" else "Team call"
        # the on-screen suggestions were private: the notes must only reflect what was said aloud
        spoken = "\n".join(l for l in transcript.splitlines() if "ASSIST (shown)" not in l)
        aliases = ", ".join(a for a in cfg.user.aliases if a.lower() != name.lower())
        text = claude_p(FINAL.format(kind=KIND[mode], name=name, NAME=name.upper(), aliases=aliases,
                                     when=f"{start:%Y-%m-%d %H:%M}", minutes=minutes, glossary=_glossary(),
                                     notes=notes, transcript=spoken[-120_000:], title=title,
                                     voice=VOICE.read_text(encoding="utf-8")[:3000] if VOICE.exists() else "(not learned yet)"),
                        "Output only the notes.", cfg.notes.final_model, timeout=600)
        MEETINGS.mkdir(parents=True, exist_ok=True)
        notes_path.write_text(text.strip() + "\n", encoding="utf-8")
    if cfg.notes.learn:
        learn(cfg, transcript, mode, start)
    _mark_done(stamp)
    log.info("finalized %s", stamp)


def pending_calls(min_age_s: int = 20 * 60) -> list[str]:
    """Calls that were never finalized and aren't still in progress."""
    out = []
    for meta in sorted(CALLS.glob("*.json")):
        stamp = meta.stem
        t = CALLS / f"{stamp}.txt"
        if (CALLS / f"{stamp}.done").exists() or not t.exists():
            continue
        if time.time() - t.stat().st_mtime >= min_age_s:
            out.append(stamp)
    return out


def recover() -> None:
    """Finish calls whose app was closed before saving (console window closed, crash, usage
    limit). Run at app start and by the daily worklog job, before it reads meeting notes."""
    from . import config
    cfg = config.load()
    for stamp in pending_calls():
        words = sum(len(l.split(": ", 1)[-1].split()) for l in
                    (CALLS / f"{stamp}.txt").read_text(encoding="utf-8").splitlines() if "ASSIST (shown)" not in l)
        if words < cfg.notes.min_call_words:
            _mark_done(stamp)
            continue
        try:
            finalize(stamp)
        except Exception as e:  # e.g. usage limit — try again next time
            log.warning("could not finalize %s yet: %s", stamp, e)
            break



def learn(cfg, transcript: str, mode: str, start: datetime) -> None:
    """Update voice.md / learned.md from what you actually said on this call."""
    from worklog.summarize import claude_p

    name = cfg.user.name
    mine = [l for l in transcript.splitlines() if f" {name.upper()}: " in l]
    if sum(len(l.split()) for l in mine) < cfg.notes.learn_min_words:
        log.info("not enough of your own speech to learn from")
        return
    read = lambda p: p.read_text(encoding="utf-8") if p.exists() else "(empty — first call)"
    out = claude_p(LEARN.format(name=name, NAME=name.upper(), kind=KIND[mode], when=f"{start:%Y-%m-%d %H:%M}",
                                voice=read(VOICE), learned=read(LEARNED), transcript=transcript[-120_000:]),
                   "Output only the two files in the requested format.", cfg.notes.final_model, timeout=600)
    if "===VOICE===" not in out or "===LEARNED===" not in out:
        log.warning("learning output malformed; files left unchanged")
        return
    voice, learned = out.split("===VOICE===", 1)[1].split("===LEARNED===", 1)
    backup = DATA / "learn_history"
    backup.mkdir(parents=True, exist_ok=True)
    stamp = f"{start:%Y-%m-%d_%H%M}"
    for path, new in ((VOICE, voice), (LEARNED, learned)):
        if path.exists():
            (backup / f"{stamp}_{path.name}").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        path.write_text(new.strip() + "\n", encoding="utf-8")


def cli(args: list[str]) -> int:
    (DATA / "logs").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname).1s %(message)s",
                        handlers=[logging.FileHandler(DATA / "logs" / "notes.log", encoding="utf-8")])
    if len(args) >= 2 and args[0] == "finalize":
        finalize(args[1])
    elif args and args[0] == "recover":
        recover()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
