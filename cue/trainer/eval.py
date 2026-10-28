"""Replay scripted mock interviews through the trainer (no audio, no windows) and score it.

    cue-cli trainer-eval              every script in evals/trainer/
    cue-cli trainer-eval two_sum      one script
    cue-cli trainer-eval --lang=rust  in another language (checks the model-written test driver)

Each script is the interview as lines ("INTERVIEWER: ...", "ME: ...") with a gold header:
the requirements that must end up on the board (keyword groups), lines that must NOT be live
(things the interviewer took back), questions that should be ticked off, the difficulty, the
estimate range and the optimal complexity, plus a known-correct solution. That solution must pass
every test the trainer generated (so a wrong expected output costs points), and so must the
Designer's reference code.
"""
from __future__ import annotations

import json
import logging
import re
import sys
import textwrap
import threading
import time
from pathlib import Path

from ..config import APP_DIR, DATA, load
from .brain import Brain
from .state import ProblemState

log = logging.getLogger(__name__)
EVALS = APP_DIR / "evals" / "trainer"


def parse_script(path: Path) -> tuple[dict, str, list[tuple[str, str]]]:
    gold_lines, sol_lines, lines, in_sol = [], [], [], False
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.startswith("#"):
            body = raw[1:]
            if body.strip() == "solution:":
                in_sol = True
            elif in_sol:
                sol_lines.append(body[1:] if body.startswith(" ") else body)
            else:
                gold_lines.append(body.replace("gold:", "", 1) if "gold:" in body else body)
            continue
        m = re.match(r"^\s*([A-Z][A-Z ]*?)\s*:\s*(.+)$", raw)
        if m:
            lines.append((m.group(1), m.group(2)))
    gold = json.loads(" ".join(gold_lines)) if gold_lines else {}
    return gold, textwrap.dedent("\n".join(sol_lines)).strip() + "\n", lines


def _has(text: str, group: list[str]) -> bool:
    t = text.lower()
    return any(k.lower() in t for k in group)


def _cx(s: str) -> str:
    return re.sub(r"\s+", "", s.lower().replace("²", "^2").replace("·", "*"))


def run_script(path: Path, cfg, name: str = "ME", pace_s: float = 1.2, lang: str | None = None) -> dict:
    gold, solution, lines = parse_script(path)
    if lang and lang != gold.get("language", "python"):
        solution = ""   # the known-good solution is in the script's language; only the reference is checked
    lang = lang or gold.get("language", "python")
    folder = DATA / "trainer" / "eval" / path.stem
    folder.mkdir(parents=True, exist_ok=True)
    from . import langs
    ext = langs.get(lang).ext
    state = ProblemState(lang, str(folder / f"solution{ext}"))
    lock = threading.RLock()

    def post(fn):
        with lock:
            fn()
    brain = Brain(state, cfg.get("trainer") or {}, cfg.llm, name, folder, post, lambda keys: None,
                  lambda s: log.info("[%s] %s", path.stem, s))
    t0 = time.time()
    try:
        for who, text in lines:
            brain.hear("INTERVIEWER" if who == "INTERVIEWER" else name.upper(), text)
            with lock:
                brain.flush()
            time.sleep(pace_s)
        end = time.time() + 240
        while time.time() < end:
            with lock:
                brain.tick()
                idle = not brain.jobs.busy("listener") and not brain._pending_lines
            if idle:
                break
            time.sleep(0.5)
        end = time.time() + 420
        while time.time() < end and (brain._designing or brain._testing or not state.tests):
            if not state.problem.defined and not brain.jobs.busy("listener"):
                break
            time.sleep(1)
        listen_s = time.time() - t0
        score = _score(gold, state, brain, solution)
        score["seconds"] = round(listen_s)
        score["calls"] = brain.jobs.calls
        state.save(folder / "state.json")
        return score
    finally:
        brain.close()


def _score(gold: dict, s: ProblemState, brain: Brain, solution: str) -> dict:
    live = s.live_requirements()
    text = " | ".join(r.text for r in live)
    out: dict = {"points": 0.0, "max": 0, "notes": []}

    def add(name, value, note=""):
        out[name] = round(value, 2)
        out["points"] += value
        out["max"] += 1
        if note and value < 1:
            out["notes"].append(f"{name}: {note}")
    add("defined", 1.0 if s.problem.defined else 0.0, "problem never marked defined")
    groups = gold.get("requirements", [])
    hit = [g for g in groups if _has(text, g)]
    add("req_recall", len(hit) / len(groups) if groups else 1.0,
        "missing " + "; ".join(g[0] for g in groups if g not in hit))
    bad = [g for g in gold.get("not_live", []) if _has(text, g)]
    add("retracted_gone", 0.0 if bad else 1.0, "still live: " + "; ".join(g[0] for g in bad))
    qs = [q for q in s.questions if q.status in ("asked", "answered")]
    asked = gold.get("asked", [])
    got = [g for g in asked if any(_has(q.text + " " + q.answer, g) for q in qs)]
    add("questions_ticked", len(got) / len(asked) if asked else 1.0,
        "not ticked: " + "; ".join(g[0] for g in asked if g not in got))
    add("difficulty", 1.0 if s.problem.difficulty in gold.get("difficulty", [s.problem.difficulty]) else 0.0,
        f"got {s.problem.difficulty!r}")
    lo, hi = gold.get("minutes", [0, 999])
    add("estimate", 1.0 if lo <= (s.estimate.minutes or -1) <= hi else 0.0, f"got {s.estimate.minutes} min")
    rec = s.chosen_approach()
    add("optimal_recommended", 1.0 if rec and _cx(gold.get("optimal", "")) in _cx(rec.time) else 0.0,
        f"recommended {rec.name if rec else None} {rec.time if rec else ''}")
    add("tests_verified", 1.0 if s.oracle.get("verified") else 0.0, f"oracle {s.oracle.get('agreement')} {s.oracle.get('report')}")
    if s.tests and solution:   # (in another language the gold solution isn't checked, only the reference)
        run = brain.run_now(solution, stress=True)
        ok = run["total"] and run["passed"] == run["total"] and not run.get("build_error")
        add("gold_passes_tests", 1.0 if ok else 0.0,
            f"{run['passed']}/{run['total']} {run.get('build_error', '')[:200]} " +
            "; ".join(f"{r['id']} exp {json.dumps(r.get('expected'))[:60]} got {json.dumps(r.get('actual'))[:60]} {r.get('error', '')[:80]}"
                      for r in run["results"] if not r.get("ok"))[:600])
    if s.tests:
        ref = brain.reference_code()
        run2 = brain.run_now(ref, stress=True) if ref else {"total": 0, "passed": 0, "results": []}
        ok2 = run2["total"] and run2["passed"] == run2["total"] and not run2.get("build_error")
        add("reference_passes_tests", 1.0 if ok2 else 0.0,
            f"{run2['passed']}/{run2['total']} {run2.get('build_error', '')[:200]}")
    if not s.tests:
        add("gold_passes_tests", 0.0, "no tests were built")
        add("reference_passes_tests", 0.0, "no tests were built")
    if gold.get("hint"):
        add("hint_seen", 1.0 if any(_has(h["text"] + " " + h.get("points_to", ""), gold["hint"]) for h in s.hints)
            or any(_has(a.name, gold["hint"]) for a in s.approaches) else 0.0, "hint not captured")
    out["requirements"] = [f"{r.id} [{r.status}] {r.text}" for r in s.requirements]
    out["questions"] = [f"{q.id} [{q.status}] {q.text} -> {q.answer}" for q in s.questions]
    out["approaches"] = [f"{'*' if a.recommended else ' '} {a.name} {a.time}" for a in s.approaches]
    out["tests"] = len(s.tests)
    return out


def main(args: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname).1s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    cfg = load()
    lang = next((a.split("=", 1)[1] for a in args if a.startswith("--lang=")), None)
    names = [a for a in args if not a.startswith("-")]
    scripts = sorted(EVALS.glob("*.txt"))
    if names:
        scripts = [p for p in scripts if p.stem in names]
    results = {}
    for p in scripts:
        print(f"\n=== {p.stem} ===", flush=True)
        try:
            r = run_script(p, cfg, lang=lang)
        except Exception as e:
            log.exception("eval %s crashed", p.stem)
            r = {"points": 0, "max": 1, "notes": [f"crashed: {e}"]}
        results[p.stem] = r
        print(f"{p.stem}: {r['points']:.2f} / {r['max']}  ({r.get('seconds', '?')} s, {r.get('calls', '?')} calls)")
        for n in r.get("notes", []):
            print("   -", n)
        for k in ("requirements", "questions", "approaches"):
            for line in r.get(k, []):
                print(f"   {k[:3]}: {line}")
    total = sum(r["points"] for r in results.values())
    most = sum(r["max"] for r in results.values())
    print(f"\nTOTAL {total:.2f} / {most}  ({100 * total / max(most, 1):.0f}%)")
    out = DATA / "trainer" / "eval" / f"results-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(f"saved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
