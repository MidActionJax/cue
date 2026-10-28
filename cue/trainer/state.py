"""The one picture of the problem that every trainer window and model role reads from.

The Listener never rewrites it: it sends small patches (add / edit / drop requirement R3, mark
question Q2 asked...) against stable ids, so lines are edited in place instead of jumping
around, and every change can be shown. Patches are validated before they're applied; a bad op
is dropped and logged, never shown.
"""
from __future__ import annotations

import difflib
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

PHASES = ["listening", "clarifying", "defined", "designing", "coding", "testing", "follow_up", "done"]
REQ_KINDS = ["functional", "io", "constraint", "edge", "performance"]
DIFFICULTY_MINUTES = {"easy": 15, "medium": 25, "hard": 40}


@dataclass
class Requirement:
    id: str
    text: str
    kind: str = "functional"
    status: str = "stated"          # stated (they said it) | assumed (inferred, not confirmed) | dropped
    met: str = "no"                 # no | partial | yes (from the Reviewer)
    source: str = ""                # the transcript quote it came from
    history: list = field(default_factory=list)   # [{"text", "at"}] earlier wordings
    changed_at: float = 0.0


@dataclass
class Question:
    id: str
    text: str
    why: str = ""
    status: str = "open"            # open | asked | answered | dropped
    answer: str = ""
    priority: int = 2               # 1 = ask first
    changed_at: float = 0.0


@dataclass
class Approach:
    id: str
    name: str
    time: str = ""
    space: str = ""
    idea: str = ""
    pseudocode: str = ""
    why_not: str = ""               # why it loses to the better one (empty for the best)
    tradeoffs: str = ""
    say_it: str = ""                # how to explain it out loud
    ops: float = 0                  # rough operation count at the problem's n_max
    recommended: bool = False
    chosen: bool = False


@dataclass
class Block:
    id: str
    name: str
    purpose: str = ""
    pseudo: str = ""
    code: str = ""
    status: str = "todo"            # todo | partial | done | issue
    lines: list = field(default_factory=list)     # [first, last] in your file, from the Reviewer
    note: str = ""


@dataclass
class Test:
    id: str
    name: str
    origin: str = "edge"            # interviewer | edge | generated | stress | custom
    input: object = None            # list of argument values in signature order (stdin style: a string)
    expected: object = None
    verified: bool = False          # expected came from the reference run, cross-checked by the brute force
    note: str = ""


@dataclass
class Problem:
    title: str = ""
    summary: str = ""
    style: str = "function"         # function (call a function) | stdin (read input, print output)
    signature: dict = field(default_factory=dict)  # {"name", "params": [[name, type]], "returns"}
    examples: list = field(default_factory=list)   # [{"input": [...], "output": ..., "source"}]
    pattern: list = field(default_factory=list)
    difficulty: str = ""
    n_max: int = 0
    defined: bool = False
    confidence: float = 0.0


@dataclass
class Estimate:
    minutes: int = 0
    rationale: str = ""
    started_at: float = 0.0         # time.time() when the countdown started; 0 = not started
    paused_left: float = -1.0       # seconds left while paused; -1 = running (or not started)
    extra_s: float = 0.0            # +5 min clicks


class ProblemState:
    def __init__(self, language: str = "python", path: str = ""):
        self.phase = "listening"
        self.problem = Problem()
        self.requirements: list[Requirement] = []
        self.questions: list[Question] = []
        self.hints: list[dict] = []
        self.follow_ups: list[str] = []
        self.estimate = Estimate()
        self.plan: list[dict] = []
        self.walkthrough: dict = {}       # the recommended approach in plain words: steps, example, edge, why
        self.approaches: list[Approach] = []
        self.blocks: list[Block] = []
        self.tests: list[Test] = []
        self.oracle: dict = {}
        self.compare = "exact"
        self.code = {"path": path, "language": language, "text": "", "detected_approach": "",
                     "lines": {}, "syntax": [], "summary": ""}
        self.runs: list[dict] = []
        self.measured: dict = {}
        self.debug: dict = {}
        self.version = 0
        self._next = {"R": 1, "Q": 1, "T": 1}

    # ------------------------------------------------------------------ ids
    def new_id(self, prefix: str) -> str:
        n = self._next.get(prefix, 1)
        self._next[prefix] = n + 1
        return f"{prefix}{n}"

    def req(self, rid: str) -> Requirement | None:
        return next((r for r in self.requirements if r.id == rid), None)

    def question(self, qid: str) -> Question | None:
        return next((q for q in self.questions if q.id == qid), None)

    def live_requirements(self) -> list[Requirement]:
        return [r for r in self.requirements if r.status != "dropped"]

    def open_questions(self) -> list[Question]:
        return [q for q in self.questions if q.status == "open"]

    def chosen_approach(self) -> Approach | None:
        return next((a for a in self.approaches if a.chosen), None) or \
            next((a for a in self.approaches if a.recommended), None)

    # ------------------------------------------------------------------ the Listener's patches
    def apply(self, ops: list[dict]) -> set[str]:
        """Apply a Listener patch. Returns the ids (and section names) that changed."""
        changed: set[str] = set()
        now = time.time()
        # edits may only point at items that existed before this patch: a model that adds R4 and
        # then "edits R4" in the same reply is guessing ids, and would overwrite the wrong line
        known = {r.id for r in self.requirements} | {q.id for q in self.questions}
        for op in ops if isinstance(ops, list) else []:
            if not isinstance(op, dict):
                continue
            if op.get("op") in ("edit_req", "drop_req", "edit_q", "q_status") and op.get("id") not in known:
                log.warning("patch op on an id that isn't on the board yet, dropped: %s", op)
                continue
            try:
                kind = op.get("op")
                fn = getattr(self, f"_op_{kind}", None)
                if fn is None and kind in ("title", "summary", "style", "difficulty", "pattern", "defined", "confidence"):
                    # {"op": "difficulty", "difficulty": "easy"}: a problem field sent as its own op
                    op = {kind: op.get(kind, op.get("value"))}
                    fn = self._op_problem
                if fn is None:
                    log.warning("unknown patch op dropped: %s", op)
                    continue
                got = fn(op, now)
                if got:
                    changed |= got if isinstance(got, set) else {got}
            except Exception:
                log.exception("patch op failed: %s", op)
        if changed:
            self.version += 1
        return changed

    def _similar_req(self, text: str) -> Requirement | None:
        t = _norm(text)
        for r in self.live_requirements():
            if difflib.SequenceMatcher(None, _norm(r.text), t).ratio() > 0.86:
                return r
        return None

    def _op_add_req(self, op: dict, now: float):
        text = str(op.get("text", "")).strip()
        if not text:
            return None
        status = op.get("status") if op.get("status") in ("stated", "assumed") else "stated"
        source = str(op.get("source", "")).strip()
        if status == "stated" and not source:
            status = "assumed"   # no quote, no proof it was said
        same = self._similar_req(text)
        if same:   # a near-duplicate is an edit of the line that's already there
            return self._op_edit_req({"id": same.id, "text": text, "source": source,
                                      "status": "stated" if status == "stated" else same.status}, now)
        kind = op.get("kind") if op.get("kind") in REQ_KINDS else "functional"
        r = Requirement(self.new_id("R"), text, kind, status, source=source, changed_at=now)
        self.requirements.append(r)
        return r.id

    def _op_edit_req(self, op: dict, now: float):
        r = self.req(str(op.get("id", "")))
        if r is None:
            return None
        text = str(op.get("text") or "").strip()
        did = False
        if text and text != r.text:
            r.history.append({"text": r.text, "at": now})
            r.text, did = text, True
        if op.get("kind") in REQ_KINDS and op["kind"] != r.kind:
            r.kind, did = op["kind"], True
        if op.get("status") in ("stated", "assumed") and op["status"] != r.status:
            r.status, did = op["status"], True
        if op.get("source"):
            r.source = str(op["source"]).strip()
        if r.status == "dropped" and text:
            r.status, did = "stated" if r.source else "assumed", True
        if did:
            r.changed_at = now
            return r.id
        return None

    def _op_drop_req(self, op: dict, now: float):
        r = self.req(str(op.get("id", "")))
        if r is None or r.status == "dropped":
            return None
        r.history.append({"text": r.text, "at": now, "dropped": str(op.get("reason", ""))})
        r.status, r.changed_at = "dropped", now
        return r.id

    def _op_add_q(self, op: dict, now: float):
        text = str(op.get("text", "")).strip()
        if not text:
            return None
        t = _norm(text)
        for q in self.questions:
            if q.status != "dropped" and difflib.SequenceMatcher(None, _norm(q.text), t).ratio() > 0.8:
                return None
        try:
            pr = max(1, min(3, int(op.get("priority", 2))))
        except (TypeError, ValueError):
            pr = 2
        q = Question(self.new_id("Q"), text, str(op.get("why", "")).strip(), priority=pr, changed_at=now)
        if op.get("status") in ("asked", "answered"):   # the candidate asked something that wasn't on the list
            q.status, q.answer = op["status"], str(op.get("answer") or "").strip()
        self.questions.append(q)
        return q.id

    def _op_edit_q(self, op: dict, now: float):
        q = self.question(str(op.get("id", "")))
        text = str(op.get("text") or "").strip()
        if q is None or not text or text == q.text:
            return None
        q.text, q.changed_at = text, now
        return q.id

    def _op_q_status(self, op: dict, now: float):
        q = self.question(str(op.get("id", "")))
        status = op.get("status")
        if q is None or status not in ("open", "asked", "answered", "dropped"):
            return None
        if op.get("answer"):
            q.answer = str(op["answer"]).strip()
        if status == q.status and not op.get("answer"):
            return None
        q.status, q.changed_at = status, now
        return q.id

    def _op_problem(self, op: dict, now: float):
        p, changed = self.problem, False
        for key in ("title", "summary"):
            if op.get(key) and str(op[key]).strip() != getattr(p, key):
                setattr(p, key, str(op[key]).strip())
                changed = True
        if op.get("style") in ("function", "stdin") and op["style"] != p.style:
            p.style, changed = op["style"], True
        if op.get("difficulty") in DIFFICULTY_MINUTES and op["difficulty"] != p.difficulty:
            p.difficulty, changed = op["difficulty"], True
        if isinstance(op.get("pattern"), list):
            pat = [str(x) for x in op["pattern"] if str(x).strip()][:3]
            if pat != p.pattern:
                p.pattern, changed = pat, True
        if op.get("n_max"):
            try:
                n = int(float(op["n_max"]))
                if n > 0 and n != p.n_max:
                    p.n_max, changed = n, True
            except (TypeError, ValueError):
                pass
        if "defined" in op and bool(op["defined"]) != p.defined:
            p.defined, changed = bool(op["defined"]), True
        if op.get("confidence") is not None:
            try:
                p.confidence = float(op["confidence"])
            except (TypeError, ValueError):
                pass
        return "problem" if changed else None

    def _op_n_max(self, op: dict, now: float):
        return self._op_problem({"n_max": op.get("n_max") or op.get("value") or op.get("text")}, now)

    def _op_signature(self, op: dict, now: float):
        name = str(op.get("name", "")).strip()
        params = op.get("params")
        if not name or not isinstance(params, list):
            return None
        clean = []
        for p in params:
            if isinstance(p, (list, tuple)) and len(p) == 2:
                clean.append([str(p[0]), str(p[1])])
            elif isinstance(p, dict) and p.get("name"):
                clean.append([str(p["name"]), str(p.get("type", "any"))])
        sig = {"name": name, "params": clean, "returns": str(op.get("returns", "any"))}
        if sig == self.problem.signature:
            return None
        self.problem.signature = sig
        return "signature"

    def _op_add_example(self, op: dict, now: float):
        if "input" not in op:
            return None
        ex = {"input": op["input"], "output": op.get("output"), "source": str(op.get("source", "")),
              "explanation": str(op.get("explanation", ""))}
        for old in self.problem.examples:
            if old["input"] == ex["input"]:
                if old.get("output") == ex["output"]:
                    return None
                old.update(ex)   # the interviewer corrected the expected output
                return "examples"
        self.problem.examples.append(ex)
        return "examples"

    def _op_hint(self, op: dict, now: float):
        text = str(op.get("text", "")).strip()
        if not text or any(h["text"] == text for h in self.hints):
            return None
        self.hints.append({"text": text, "at": now, "points_to": str(op.get("points_to", ""))})
        return "hints"

    def _op_follow_up(self, op: dict, now: float):
        text = str(op.get("text", "")).strip()
        if not text or text in self.follow_ups:
            return None
        self.follow_ups.append(text)
        return "follow_ups"

    def _op_phase(self, op: dict, now: float):
        ph = op.get("phase")
        if ph not in PHASES or ph == self.phase:
            return None
        self.phase = ph
        return "phase"

    def _op_estimate(self, op: dict, now: float):
        try:
            minutes = int(round(float(op.get("minutes", 0))))
        except (TypeError, ValueError):
            return None
        if minutes <= 0 or minutes > 120:
            return None
        e = self.estimate
        if minutes == e.minutes and op.get("rationale", e.rationale) == e.rationale:
            return None
        e.minutes = minutes
        e.rationale = str(op.get("rationale", e.rationale)).strip()
        return "estimate"

    # ------------------------------------------------------------------ timer
    def seconds_left(self, now: float | None = None) -> float | None:
        e = self.estimate
        if not e.minutes or not e.started_at:
            return None
        if e.paused_left >= 0:
            return e.paused_left
        now = time.time() if now is None else now
        return e.minutes * 60 + e.extra_s - (now - e.started_at)

    def start_timer(self, now: float | None = None) -> None:
        e = self.estimate
        now = time.time() if now is None else now
        if not e.minutes:
            e.minutes = DIFFICULTY_MINUTES.get(self.problem.difficulty, 25)
        if e.paused_left >= 0:   # resume
            e.started_at = now - (e.minutes * 60 + e.extra_s - e.paused_left)
            e.paused_left = -1.0
        elif not e.started_at:
            e.started_at = now

    def pause_timer(self, now: float | None = None) -> None:
        left = self.seconds_left(now)
        if left is not None and self.estimate.paused_left < 0:
            self.estimate.paused_left = left

    # ------------------------------------------------------------------ text for the models
    def brief(self, include_design: bool = True, include_code: bool = False) -> str:
        """The state as compact text for a model prompt (ids included so patches can point at them)."""
        p, out = self.problem, []
        out.append(f"PHASE: {self.phase}")
        out.append(f"LANGUAGE: {self.code.get('language', '')}")
        out.append("PROBLEM: " + (f"{p.title} — {p.summary}" if p.title or p.summary else "(not stated yet)"))
        meta = []
        if p.style:
            meta.append(f"style={p.style}")
        if p.difficulty:
            meta.append(f"difficulty={p.difficulty}")
        if p.pattern:
            meta.append("pattern=" + ", ".join(p.pattern))
        if p.n_max:
            meta.append(f"n_max={p.n_max}")
        meta.append(f"defined={'yes' if p.defined else 'no'}")
        out.append("  " + " · ".join(meta))
        if p.signature:
            s = p.signature
            out.append("SIGNATURE: " + s["name"] + "(" + ", ".join(f"{n}: {t}" for n, t in s["params"])
                       + f") -> {s.get('returns', '')}")
        if p.examples:
            out.append("EXAMPLES:")
            for ex in p.examples:
                out.append(f"  input={json.dumps(ex['input'])} -> output={json.dumps(ex['output'])}")
        out.append("REQUIREMENTS:")
        live = self.live_requirements()
        if not live:
            out.append("  (none yet)")
        for r in live:
            tag = "" if r.status == "stated" else " [assumed]"
            met = f" (met: {r.met})" if include_code and r.met != "no" else ""
            out.append(f"  {r.id} [{r.kind}]{tag} {r.text}{met}")
        dropped = [r for r in self.requirements if r.status == "dropped"]
        if dropped:
            out.append("DROPPED (no longer true): " + "; ".join(f"{r.id} {r.text}" for r in dropped[-5:]))
        out.append("QUESTIONS FOR THE INTERVIEWER:")
        if not self.questions:
            out.append("  (none yet)")
        for q in self.questions:
            if q.status == "dropped":
                continue
            ans = f" -> {q.answer}" if q.answer else ""
            out.append(f"  {q.id} [{q.status}] {q.text}{ans}")
        if self.hints:
            out.append("HINTS FROM THE INTERVIEWER: " + "; ".join(h["text"] for h in self.hints))
        if self.follow_ups:
            out.append("FOLLOW-UPS: " + "; ".join(self.follow_ups))
        if self.estimate.minutes:
            out.append(f"ESTIMATE: {self.estimate.minutes} min ({self.estimate.rationale})")
        if include_design and self.walkthrough.get("steps"):
            out.append("PLAN IN PLAIN WORDS: " + " ".join(f"{i}. {t}" for i, t in enumerate(self.walkthrough["steps"], 1)))
        if include_design and self.approaches:
            out.append("APPROACHES:")
            for a in self.approaches:
                flag = " [CHOSEN]" if a.chosen else (" [recommended]" if a.recommended else "")
                out.append(f"  {a.id}{flag} {a.name}: time {a.time}, space {a.space}. {a.idea}")
        if include_code and self.code.get("text"):
            out.append(f"CANDIDATE'S CODE ({self.code.get('language')}, line-numbered):")
            out.append(numbered(self.code["text"]))
            if self.runs:
                last = self.runs[-1]
                out.append(f"LAST TEST RUN: {last.get('passed', 0)}/{last.get('total', 0)} passed"
                           + (f"; build error: {last['build_error'][:400]}" if last.get("build_error") else ""))
        return "\n".join(out)

    # ------------------------------------------------------------------ persistence
    def to_dict(self) -> dict:
        return {
            "phase": self.phase, "problem": asdict(self.problem),
            "requirements": [asdict(r) for r in self.requirements],
            "questions": [asdict(q) for q in self.questions],
            "hints": self.hints, "follow_ups": self.follow_ups, "estimate": asdict(self.estimate),
            "plan": self.plan, "walkthrough": self.walkthrough, "approaches": [asdict(a) for a in self.approaches],
            "blocks": [asdict(b) for b in self.blocks], "tests": [asdict(t) for t in self.tests],
            "oracle": self.oracle, "compare": self.compare, "code": self.code, "runs": self.runs[-20:],
            "measured": self.measured, "debug": self.debug, "version": self.version, "next": self._next,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ProblemState":
        s = cls()
        s.phase = d.get("phase", "listening")
        s.problem = Problem(**d.get("problem", {}))
        s.requirements = [Requirement(**r) for r in d.get("requirements", [])]
        s.questions = [Question(**q) for q in d.get("questions", [])]
        s.hints, s.follow_ups = d.get("hints", []), d.get("follow_ups", [])
        s.estimate = Estimate(**d.get("estimate", {}))
        s.plan = d.get("plan", [])
        s.walkthrough = d.get("walkthrough", {})
        s.approaches = [Approach(**a) for a in d.get("approaches", [])]
        s.blocks = [Block(**b) for b in d.get("blocks", [])]
        s.tests = [Test(**t) for t in d.get("tests", [])]
        s.oracle, s.compare = d.get("oracle", {}), d.get("compare", "exact")
        s.code.update(d.get("code", {}))
        s.runs, s.measured, s.debug = d.get("runs", []), d.get("measured", {}), d.get("debug", {})
        s.version, s._next = d.get("version", 0), d.get("next", s._next)
        return s

    def save(self, path: Path) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.to_dict(), indent=1, default=str), encoding="utf-8")
            tmp.replace(path)
        except OSError as e:
            log.warning("couldn't save trainer state: %s", e)


def numbered(code: str) -> str:
    return "\n".join(f"{i:>3}| {line}" for i, line in enumerate(code.splitlines(), 1))


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", text.lower()).strip()
