"""Practice mode: it asks (out loud), you answer (out loud), it coaches you.

Every answer also feeds the post-session learning (voice.md) and your voice fingerprint, so
practice makes live suggestions sound more like you before any real call.
"""
from __future__ import annotations

import logging
import random
import re
import time

from .config import resolve

log = logging.getLogger(__name__)

WORK_STANDARD = [
    "What did you get done last week?",
    "What are you working on this week?",
    "Anything blocking you right now?",
]
INTERVIEW_STANDARD = [
    "Tell me about yourself.",
    "Walk me through a project you're proud of.",
    "Tell me about a time you tracked down a really hard bug.",
    "Why are you interested in this role?",
    "Tell me about a time you disagreed with a teammate. How did you handle it?",
    "What's something you're working on getting better at?",
    "Tell me about a time you had to learn something new quickly.",
]


def _questions_from(path: str, heading_hint: str) -> list[str]:
    """Questions from a markdown section whose heading contains heading_hint."""
    p = resolve(path)
    if not p.exists():
        return []
    text = p.read_text(encoding="utf-8")
    m = re.search(rf"^##+ [^\n]*{heading_hint}[^\n]*$(.*?)(?=^## |\Z)", text, flags=re.M | re.S | re.I)
    body = m.group(1) if m else ""
    out = []
    for line in body.splitlines():
        line = re.sub(r"\*\*|__|^[\s\-*\d.]+|^#+\s*|^Q:\s*", "", line.strip())
        if "?" in line:
            q = line[: line.index("?") + 1].strip(" —-:")
            if 12 < len(q) < 200:
                out.append(q)
    return out


def question_bank(kind: str) -> list[str]:
    if kind == "interview":
        extra = _questions_from("profiles/interview/prep.md", "question")
        rest = extra + INTERVIEW_STANDARD[1:]
        random.shuffle(rest)
        return [INTERVIEW_STANDARD[0]] + rest
    extra = _questions_from("profiles/work/last_week.md", "likely questions")
    random.shuffle(extra)
    return WORK_STANDARD[:1] + extra + WORK_STANDARD[1:]


class Voice:
    """Windows' built-in text-to-speech (SAPI) for reading questions out loud."""

    def __init__(self):
        try:
            import comtypes.client
            self._v = comtypes.client.CreateObject("SAPI.SpVoice")
            self._v.Rate = 0
        except Exception as e:
            log.warning("text-to-speech unavailable: %s", e)
            self._v = None

    def say(self, text: str) -> None:
        if self._v:
            self._v.Speak(text, 1 | 2)  # async + purge anything still being spoken

    def stop(self) -> None:
        if self._v:
            self._v.Speak("", 2)


class PracticeSession:
    """State machine: asking -> answering -> reviewing -> (next) asking ..."""

    SILENCE_DONE_S = 2.5   # you've stopped talking this long -> answer is done
    MIN_WORDS = 6

    def __init__(self, kind: str):
        self.kind = kind
        self.questions = question_bank(kind)
        self.i = -1
        self.state = "idle"
        self.answer: list[str] = []
        self._silent_since: float | None = None
        self.voice = Voice()

    @property
    def question(self) -> str:
        return self.questions[self.i % len(self.questions)] if self.i >= 0 else ""

    def progress(self) -> str:
        return f"PRACTICE · {self.kind} · {self.i % len(self.questions) + 1}/{len(self.questions)}"

    def next(self) -> str:
        self.i += 1
        self.state = "asking"
        self.answer = []
        self._silent_since = None
        self.voice.say(self.question)
        return self.question

    def retry(self) -> str:
        self.state = "asking"
        self.answer = []
        self._silent_since = None
        self.voice.say(self.question)
        return self.question

    def on_my_words(self, text: str) -> None:
        if self.state in ("asking", "answering"):
            self.state = "answering"
            self.answer.append(text)

    def answer_finished(self, me_speaking: bool) -> bool:
        """Poll from the UI tick: True once when your answer is over."""
        if self.state != "answering" or me_speaking:
            self._silent_since = None
            return False
        if self._silent_since is None:
            self._silent_since = time.monotonic()
            return False
        if time.monotonic() - self._silent_since >= self.SILENCE_DONE_S and \
                len(" ".join(self.answer).split()) >= self.MIN_WORDS:
            self.state = "reviewing"
            return True
        return False

    def stop(self) -> None:
        self.voice.stop()
        self.state = "idle"
