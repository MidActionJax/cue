"""Rolling, thread-safe conversation transcript."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class Line:
    speaker: str   # "them" | "me"  (someone else talking into your mic counts as "them")
    text: str
    t_start: float
    t_end: float
    utt_key: tuple
    who: str = ""   # display name: "Sam", "Speaker 2", "In room"; "" = unknown
    source: str = ""  # which audio it came from: "them" (call) | "me" (your mic)


class Transcript:
    def __init__(self, keep_s: float = 300):
        self.keep_s = keep_s
        self._lines: list[Line] = []
        self._partials: dict[str, Line] = {}
        self._lock = threading.Lock()

    def add(self, speaker: str, text: str, t_start: float, t_end: float, utt_key: tuple, final: bool,
            who: str = "", source: str = "") -> Line:
        source = source or speaker
        line = Line(speaker, text, t_start, t_end, utt_key, who, source)
        with self._lock:
            if final:
                self._partials.pop(source, None)
                self._lines.append(line)
                cutoff = time.time() - self.keep_s
                self._lines = [l for l in self._lines if l.t_end >= cutoff]
            else:
                self._partials[source] = line
        return line

    def lines(self, since_s: float | None = None, include_partials: bool = True) -> list[Line]:
        with self._lock:
            lines = list(self._lines)
            if include_partials:
                lines += list(self._partials.values())
        if since_s is not None:
            cutoff = time.time() - since_s
            lines = [l for l in lines if l.t_end >= cutoff]
        return sorted(lines, key=lambda l: l.t_start)

    def relabel(self, old: str, new: str) -> None:
        with self._lock:
            for l in self._lines:
                if l.who == old:
                    l.who = new

    @staticmethod
    def label(line: Line, user_name: str) -> str:
        if line.speaker == "me":
            return user_name.upper()
        return f"THEM ({line.who})" if line.who else "THEM"

    def format(self, since_s: float | None = None, user_name: str = "ME") -> str:
        now = time.time()
        return "\n".join(f"[-{int(now - l.t_start)}s] {self.label(l, user_name)}: {l.text}"
                         for l in self.lines(since_s))

    def current_them_turn(self, max_gap_s: float = 8.0) -> tuple[str, float | None]:
        """What the other side has said since you last spoke: (joined text, turn start time)."""
        lines = self.lines(include_partials=False)
        turn: list[str] = []
        last_t = None
        for l in reversed(lines):
            if l.speaker == "me":
                break
            if last_t is not None and last_t - l.t_end > max_gap_s:
                break
            turn.append(l.text)
            last_t = l.t_start
        return " ".join(reversed(turn)), last_t

    def recent_them(self, seconds: float = 45) -> str:
        now = time.time()
        return "\n".join(f"[-{int(now - l.t_start)}s] {self.label(l, '')}: {l.text}"
                         for l in self.lines(seconds, include_partials=False) if l.speaker == "them")
