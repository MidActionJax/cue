"""The trainer's logic, without any windows: transcript in, a living ProblemState out.

    lines heard -> Listener patches -> (problem defined) -> Designer + Test author
    Test author's oracle -> cross-checked expected outputs -> tests
    your file -> syntax check -> Reviewer -> marks;  run tests -> results;  debug -> shrink + trace + Debugger

Everything slow runs on worker threads. Results come back through `post(fn)`, which runs fn on
the owner's thread (the Qt main thread in the app, a lock in the headless eval), so the state is
only ever changed from one place. `on_change(keys)` tells the owner what to redraw.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Callable

from . import harness, langs, oracle, prompts, runner
from .jobs import Jobs
from .state import DIFFICULTY_MINUTES, Approach, Block, ProblemState, Test, numbered

log = logging.getLogger(__name__)

MATERIAL = {"functional", "io", "constraint", "edge"}


class Brain:
    def __init__(self, state: ProblemState, tcfg, llm_cfg, name: str, folder: Path,
                 post: Callable[[Callable], None], on_change: Callable[[set], None],
                 on_status: Callable[[str], None] = lambda s: None, jobs: Jobs | None = None):
        self.state, self.tcfg, self.name = state, tcfg, name
        self.folder = folder
        self.post, self.on_change, self.on_status = post, on_change, on_status
        self.lang = langs.get(state.code.get("language", "python"))
        fmt = dict(name=name, NAME=name.upper(), language=self.lang.name)
        systems = {"listener": prompts.LISTENER.format(**fmt), "designer": prompts.DESIGNER.format(**fmt),
                   "tests": prompts.TEST_AUTHOR.format(**fmt), "reviewer": prompts.REVIEWER.format(**fmt),
                   "debugger": prompts.DEBUGGER.format(**fmt),
                   "harness": "You write small, correct test-driver programs. Reply with only the JSON asked for."}
        self.jobs = jobs or Jobs(tcfg, llm_cfg, systems, self._job_done)
        self._pending_lines: list[str] = []
        self._sent_lines: list[str] = []
        self._design_version = -1         # state.version the current design was made from
        self._design_stale_at = 0.0
        self._designing = self._testing = False
        self._test_tries = 0
        self.oracle: oracle.Oracle | None = None
        self.stress_input = None
        self._harness_cache: dict[str, str] = {}
        self._run_lock = threading.Lock()
        self._review_code = ""

    # ------------------------------------------------------------------ transcript -> Listener
    def hear(self, who: str, text: str) -> None:
        """who: INTERVIEWER | the user's name | PASTED PROBLEM"""
        text = text.strip()
        if not text:
            return
        stamp = time.strftime("%H:%M:%S")
        if who == "PASTED PROBLEM":
            self._pending_lines.append(f"PASTED PROBLEM:\n{text}")
        else:
            self._pending_lines.append(f"[{stamp}] {who}: {text}")

    def flush(self) -> bool:
        """Send what was heard since the last update to the Listener (if it isn't busy)."""
        if not self._pending_lines or self.jobs.busy("listener"):
            return False
        lines, self._pending_lines = self._pending_lines, []
        prompt = (f"CURRENT STATE:\n{self.state.brief(include_design=False)}\n\n"
                  f"EARLIER TRANSCRIPT (already processed, for context):\n" + ("\n".join(self._sent_lines[-14:]) or "(none)")
                  + "\n\nNEW TRANSCRIPT:\n" + "\n".join(lines))
        self.jobs.submit("listener", prompt, ctx=lines)
        return True

    def _job_done(self, role, data, err, ctx) -> None:
        self.post(lambda: self._handle(role, data, err, ctx))

    def _handle(self, role, data, err, ctx) -> None:
        if role == "listener":
            if err:
                self._pending_lines = list(ctx or []) + self._pending_lines   # try them again next time
                self.on_status(f"Listener: {err[:120]}")
            else:
                self._sent_lines += ctx or []
                self._apply_listener(data)
            if self._pending_lines:
                self.flush()
        elif role == "designer":
            self._designing = False
            if err:
                self.on_status(f"Design failed: {err[:120]}")
            else:
                self._apply_design(data, ctx)
        elif role == "tests":
            if err:
                self._testing = False
                self.on_status(f"Test author failed: {err[:120]}")
            else:
                threading.Thread(target=self._verify_tests, args=(data, ctx), daemon=True).start()
        elif role == "reviewer":
            if not err:
                self._apply_review(data, ctx)
        elif role == "debugger":
            self._apply_debug(data, err, ctx)
        elif role == "harness":
            ev, box = ctx
            box["data"], box["err"] = data, err
            ev.set()

    def _apply_listener(self, data: dict) -> None:
        s = self.state
        before_sig, before_examples = json.dumps(s.problem.signature), len(s.problem.examples)
        material_before = self._material_key()
        changed = s.apply(data.get("ops", []) if isinstance(data, dict) else [])
        if not changed:
            return
        log.info("listener changed: %s", sorted(changed))
        p = s.problem
        if p.defined and not s.estimate.minutes and p.difficulty:   # the model forgot the estimate
            s.apply([{"op": "estimate", "minutes": DIFFICULTY_MINUTES[p.difficulty],
                      "rationale": f"typical for a {p.difficulty} problem"}])
            changed.add("estimate")
        self.on_change(changed)
        if p.defined and self._design_version < 0 and not self._designing:
            self.design()
            self.make_tests()
        elif self._design_version >= 0 and (json.dumps(p.signature) != before_sig or self._material_key() != material_before):
            self._design_stale_at = time.time()   # redesign once the conversation settles (tick)
        elif len(p.examples) != before_examples and self.oracle:
            threading.Thread(target=self._refresh_example_tests, daemon=True).start()

    def _material_key(self) -> str:
        return json.dumps([[r.text, r.kind] for r in self.state.live_requirements() if r.kind in MATERIAL]
                          + [self.state.problem.signature, self.state.problem.style])

    def tick(self) -> None:
        """Called about once a second by the owner."""
        if self._design_stale_at and time.time() - self._design_stale_at > 8 and not self.jobs.busy("listener") \
                and not self._pending_lines and not self._designing:
            self._design_stale_at = 0.0
            self.on_status("The problem changed: updating the design and tests…")
            self.design()
            self.make_tests()
        if self._pending_lines and not self.jobs.busy("listener"):
            self.flush()

    # ------------------------------------------------------------------ Designer
    def design(self) -> None:
        self._designing = True
        self.on_status("Designing approaches…")
        prompt = (f"PROBLEM STATE:\n{self.state.brief(include_design=False)}\n\n"
                  f"Write everything for {self.lang.name}.")
        self.jobs.submit("designer", prompt, ctx=self.state.version)

    def _apply_design(self, data: dict, version) -> None:
        s = self.state
        chosen = next((a.name for a in s.approaches if a.chosen), None)
        try:
            s.plan = [{"step": str(p.get("step", "")), "detail": str(p.get("detail", "")),
                       "branch": str(p.get("branch", ""))} for p in data.get("plan", []) if isinstance(p, dict)]
            aps = []
            for i, a in enumerate(data.get("approaches", []), 1):
                aps.append(Approach(id=str(a.get("id") or f"A{i}"), name=str(a.get("name", "")),
                                    time=str(a.get("time", "")), space=str(a.get("space", "")),
                                    idea=str(a.get("idea", "")), pseudocode=str(a.get("pseudocode", "")),
                                    why_not=str(a.get("why_not", "")), tradeoffs=str(a.get("tradeoffs", "")),
                                    say_it=str(a.get("say_it", "")), ops=_num(a.get("ops")),
                                    recommended=bool(a.get("recommended"))))
            if aps and not any(a.recommended for a in aps):
                aps[-1].recommended = True
            for a in aps:
                a.chosen = chosen is not None and a.name == chosen
            s.approaches = aps
            w = data.get("walkthrough") if isinstance(data.get("walkthrough"), dict) else {}
            s.walkthrough = {k: [str(x) for x in w.get(k, []) if str(x).strip()] for k in ("steps", "example", "edge")}
            s.walkthrough["why"] = str(w.get("why", ""))
            old = {b.name: b for b in s.blocks}
            s.blocks = []
            for i, b in enumerate(data.get("blocks", []), 1):
                nb = Block(id=str(b.get("id") or f"B{i}"), name=str(b.get("name", "")), purpose=str(b.get("purpose", "")),
                           pseudo=str(b.get("pseudo", "")), code=str(b.get("code", "")))
                if nb.name in old:
                    nb.status = old[nb.name].status
                s.blocks.append(nb)
        except Exception:
            log.exception("bad design reply")
            self.on_status("The design reply was malformed; press ⟳ in Design to retry")
            return
        # the Listener sometimes leaves these out; without them there's no countdown
        if not s.problem.difficulty and data.get("difficulty") in DIFFICULTY_MINUTES:
            s.problem.difficulty = data["difficulty"]
        if not s.estimate.minutes:
            s.apply([{"op": "estimate", "minutes": data.get("estimate_minutes") or DIFFICULTY_MINUTES.get(s.problem.difficulty, 25),
                      "rationale": data.get("estimate_rationale") or "typical for this kind of problem"}])
        self._design_version = version if isinstance(version, int) else s.version
        if s.phase in ("listening", "clarifying", "defined"):
            s.phase = "designing"
        s.version += 1
        self.on_change({"design", "blocks", "phase", "estimate", "problem", "walkthrough"})

    def reference_code(self) -> str:
        return "\n".join(b.code.rstrip("\n") for b in self.state.blocks if b.code.strip())

    # ------------------------------------------------------------------ Test author + oracle
    def make_tests(self, feedback: str = "") -> None:
        if self._testing and not feedback:
            return
        self._testing = True
        self.on_status("Writing and cross-checking tests…")
        prompt = f"PROBLEM STATE:\n{self.state.brief(include_design=False)}"
        if feedback:
            prompt += f"\n\nYOUR PREVIOUS MODULE FAILED CROSS-CHECKING:\n{feedback}\nFix it (reference and brute must agree)."
        self.jobs.submit("tests", prompt, ctx=feedback)

    def _verify_tests(self, data: dict, feedback) -> None:
        """Worker thread: run the Test author's module, cross-check reference vs brute, compute expected outputs."""
        s = self.state
        try:
            module = str(data.get("module", ""))
            compare = str(data.get("compare", "exact"))
            edge = [e for e in data.get("edge_cases", []) if isinstance(e, dict) and isinstance(e.get("input"), list)]
            if self.oracle:
                self.oracle.close()
            o = oracle.Oracle(module, self.folder / "oracle")
            examples = [ex["input"] for ex in s.problem.examples if isinstance(ex.get("input"), list)]
            v = o.verify(examples + [e["input"] for e in edge], compare,
                         max_n=int(data.get("brute_max_n") or 8), count=int(self.tcfg.get("verify_count") or 300))
        except Exception as e:
            log.warning("oracle failed: %s", e)
            v, o = {"error": str(e)}, None
        bad = v.get("error") or v.get("disagree") or (v.get("error_count", 0) > 0) or not v.get("checked")
        if bad and not feedback and self._test_tries < 1:
            self._test_tries += 1
            report = json.dumps({k: v.get(k) for k in ("error", "disagree", "examples", "errors", "checked")}, default=str)
            self.post(lambda: self.make_tests(feedback=report[:3000]))
            return
        verified = not bad
        tests: list[Test] = []
        try:
            n = 1
            for ex in s.problem.examples:
                if not isinstance(ex.get("input"), list):
                    continue
                t = Test(f"T{n}", f"example {n}", "interviewer", ex["input"], ex.get("output"), False)
                tests.append(t)
                n += 1
            for e in edge:
                tests.append(Test(f"T{n}", str(e.get("name", f"edge {n}"))[:60], "edge", e["input"], None, verified))
                n += 1
            if o is not None:
                gen = o.generate([12, 20, 40], seed=5)
                for i, a in enumerate(gen, 1):
                    tests.append(Test(f"T{n}", f"random #{i}", "generated", a, None, verified))
                    n += 1
                need = [t for t in tests if t.origin != "interviewer"]
                for t, r in zip(need, o.expected([t.input for t in need])):
                    if r.get("ok"):
                        t.expected = r["output"]
                    else:
                        t.verified, t.note = False, f"reference failed: {r.get('error', '')[:100]}"
                inter = [t for t in tests if t.origin == "interviewer"]
                for t, r in zip(inter, o.expected([t.input for t in inter])):
                    if not r.get("ok"):
                        continue
                    if t.expected is None:
                        t.expected, t.verified = r["output"], verified
                        t.note = "output computed (the interviewer didn't say it)"
                    else:
                        agree = o.check([{"args": t.input, "output": t.expected}], compare)[0]
                        t.verified = bool(agree)
                        if not agree:
                            t.note = f"the reference gets {json.dumps(r['output'])[:80]}: check the example"
                self._add_stress(o, tests, n, int(s.problem.n_max or data.get("n_max") or 0), verified)
        except Exception:
            log.exception("building tests failed")

        def done():
            self._testing = False
            self.oracle = o
            keep = [t for t in s.tests if t.origin == "custom"]
            for i, t in enumerate(keep, len(tests) + 1):
                t.id = f"T{i}"
            s.tests = tests + keep
            s.compare = compare if o else s.compare
            s.oracle = {"module": data.get("module", ""), "agreement": f"{v.get('checked', 0) - v.get('disagree', 0)}/{v.get('checked', 0)}",
                        "verified": verified, "report": {k: v.get(k) for k in ("disagree", "examples", "errors", "error")},
                        "has": o.has if o else []}
            if data.get("n_max") and not s.problem.n_max:
                s.problem.n_max = int(data["n_max"])
            s.version += 1
            self.on_change({"tests"})
            self.on_status(f"{len(s.tests)} tests ready" + ("" if verified else " (not cross-checked: treat expected outputs with care)"))
        self.post(done)

    def _add_stress(self, o: oracle.Oracle, tests: list[Test], n: int, n_max: int, verified: bool) -> None:
        """The full-size speed test (the stated input limit). If the generator can't make an input
        that big, there's just no speed test: the other tests still stand."""
        if n_max < 1000:
            return
        try:
            big = o.generate([n_max], seed=9)[0]
        except Exception as e:
            log.warning("no stress test: couldn't generate n=%d (%s)", n_max, e)
            return
        self.stress_input = big
        try:
            (self.folder / "stress_input.json").write_text(json.dumps(big), encoding="utf-8")
        except (OSError, TypeError, ValueError):
            pass
        st = Test(f"T{n}", f"stress: n = {n_max:,}", "stress", None, None, verified,
                  note="checks speed: must finish within the time limit")
        try:
            exp = o.expected([big])[0]
            st.expected = exp.get("output") if exp.get("ok") else None
        except oracle.OracleError:
            st.note = "speed only (the reference was too slow to compute an answer)"
        tests.append(st)

    def _refresh_example_tests(self) -> None:
        """New interviewer examples after the tests were built: add them (no model call)."""
        s, o = self.state, self.oracle
        have = [json.dumps(t.input) for t in s.tests]
        new = [ex for ex in s.problem.examples if isinstance(ex.get("input"), list) and json.dumps(ex["input"]) not in have]
        if not new or o is None:
            return
        try:
            res = o.expected([ex["input"] for ex in new])
        except oracle.OracleError:
            res = [{} for _ in new]

        def done():
            n = len(s.tests) + 1
            for ex, r in zip(new, res):
                exp = ex.get("output") if ex.get("output") is not None else r.get("output")
                ok = r.get("ok") and (ex.get("output") is None or harness.outputs_match(r["output"], ex["output"], s.compare))
                s.tests.insert(sum(1 for t in s.tests if t.origin == "interviewer"),
                               Test(f"T{n}", "example (new)", "interviewer", ex["input"], exp, bool(ok)))
                n += 1
            for i, t in enumerate(s.tests, 1):
                t.id = f"T{i}"
            s.version += 1
            self.on_change({"tests"})
        self.post(done)

    def add_custom_test(self, args: list, expected=None, name: str = "") -> None:
        s = self.state

        def compute():
            exp, ver = expected, False
            if self.oracle is not None:
                try:
                    r = self.oracle.expected([args])[0]
                    if r.get("ok"):
                        if exp is None:
                            exp, ver = r["output"], True
                        else:
                            ver = self.oracle.check([{"args": args, "output": exp}], s.compare)[0]
                except oracle.OracleError:
                    pass

            def done():
                s.tests.append(Test(f"T{len(s.tests) + 1}", name or "your test", "custom", args, exp, ver))
                s.version += 1
                self.on_change({"tests"})
            self.post(done)
        threading.Thread(target=compute, daemon=True).start()

    # ------------------------------------------------------------------ the test driver
    def _harness_for(self, code: str) -> tuple[str | None, str]:
        """(driver source, error). Python/JS: Cue's own. Others: written by the model, checked by building it."""
        s, lang = self.state, self.lang
        if s.problem.style == "stdin":
            return None, ""
        sig = s.problem.signature or {"name": "solve", "params": [], "returns": ""}
        own = harness.builtin(lang, sig, lang.user_name(code))
        if own is not None:
            return own, ""
        shape = " ".join(re.findall(r"\b(?:def|fn|func|function|class|struct|fun|sub|public|static|int|long|bool|"
                                    r"vector|List|string|String|void)\b[^\n{;=]*", code))[:2000]
        key = hashlib.sha1(f"{lang.key}{json.dumps(sig)}{shape}".encode()).hexdigest()[:12]
        if key in self._harness_cache:
            return self._harness_cache[key], ""
        tc = langs.toolchain(lang)
        cmds = f"build: {' '.join(tc.build) if tc and tc.build else '(none)'}; run: {' '.join(tc.run) if tc and tc.run else ''}"
        wiring = prompts.WIRING["include_c"] if lang.key in ("c", "cpp") else \
            prompts.WIRING["include_rust"] if lang.key == "rust" else prompts.WIRING.get(lang.wiring, prompts.WIRING["include"])
        user_file = lang.user_name(code)
        shown = code if re.search(re.escape(sig["name"]), code, re.I) else (self.reference_code() or code)
        base = prompts.HARNESS.format(language=lang.name, user_file=user_file, harness_file=lang.harness_name(),
                                      wiring=wiring.format(user_file=user_file), commands=cmds,
                                      signature=json.dumps(sig), code=numbered(shown)[:12000])
        feedback = ""
        for attempt in range(3):
            data, err = self._ask_sync("harness", base + feedback)
            if err or not data or not data.get("harness"):
                return None, f"couldn't write a test driver for {lang.name}: {err or 'empty reply'}"
            src = str(data["harness"])
            b = runner.prepare(lang, code, src, s.code.get("path", ""))
            harness_name = lang.harness_name()
            if b.ok or harness_name not in b.output:
                self._harness_cache[key] = src
                return src, ""
            feedback = (f"\n\nYOUR PREVIOUS DRIVER DIDN'T BUILD (attempt {attempt + 1}):\n{b.output[-3000:]}\n"
                        f"Fix the driver ({harness_name}); don't change their code.")
        return None, f"couldn't build a test driver for {lang.name} (see the build output)"

    def _ask_sync(self, role: str, prompt: str) -> tuple[dict | None, str | None]:
        ev, box = threading.Event(), {}
        self.jobs.submit(role, prompt, ctx=(ev, box))
        if not ev.wait(300):
            return None, "timed out"
        return box.get("data"), box.get("err")

    # ------------------------------------------------------------------ running tests
    def run_tests(self, code: str | None = None, on_done: Callable | None = None, stress: bool = True) -> None:
        threading.Thread(target=self._run_tests, args=(code, on_done, stress), daemon=True).start()

    def _run_tests(self, code, on_done, stress) -> dict:
        s = self.state
        code = s.code.get("text", "") if code is None else code
        with self._run_lock:
            run = self.run_now(code, stress=stress)

        def done():
            s.runs.append(run)
            s.runs = s.runs[-20:]
            if run.get("total") and run.get("passed") == run.get("total") and s.phase in ("coding", "designing", "defined"):
                s.phase = "testing"
            s.version += 1
            self.on_change({"runs", "tests"})
            if on_done:
                on_done(run)
        self.post(done)
        return run

    def run_now(self, code: str, stress: bool = True, only: list[str] | None = None) -> dict:
        """Build and run every test against `code` (worker thread). Returns the run record."""
        s, lang = self.state, self.lang
        run = {"at": time.time(), "code_hash": _hash(code), "passed": 0, "total": 0, "results": [],
               "build_error": "", "errors": []}
        if not langs.runnable(lang):
            run["build_error"] = f"{lang.name} isn't installed on this PC, so tests can't run (reference only)."
            return run
        if not s.tests:
            run["build_error"] = "No tests yet: they're built once the problem is defined."
            return run
        drv, err = self._harness_for(code)
        if err:
            run["build_error"] = err
            return run
        b = runner.prepare(lang, code, drv, s.code.get("path", ""))
        if not b.ok:
            run["build_error"] = b.output or "build failed"
            run["errors"] = runner.parse_errors(b.output, Path(s.code.get("path") or lang.user_name(code)).name)
            return run
        tests = [t for t in s.tests if (stress or t.origin != "stress") and (only is None or t.id in only)]
        timeout = float(self.tcfg.get("test_timeout_s") or 5)
        results = {}

        def one(t: Test):
            if t.origin == "stress":
                if self.stress_input is None:
                    return t.id, {"ok": None, "error": "no stress input"}
                return t.id, self._run_one(b, t, self.stress_input, float(self.tcfg.get("stress_timeout_s") or 10))
            return t.id, self._run_one(b, t, t.input, timeout)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            for tid, r in pool.map(one, [t for t in tests if t.origin != "stress"]):
                results[tid] = r
        for t in tests:
            if t.origin == "stress":
                results[t.id] = one(t)[1]
        # problems with several right answers: the oracle's check() decides
        has_check = "check" in (s.oracle.get("has") or [])
        if has_check and self.oracle:
            cases = [(tid, r) for tid, r in results.items() if r.get("returned")]
            try:
                verdicts = self.oracle.check([{"args": self._input_of(tid), "output": r["actual"]} for tid, r in cases],
                                             s.compare)
                for (tid, r), ok in zip(cases, verdicts):
                    r["ok"] = bool(ok) and not r.get("too_slow")
            except oracle.OracleError as e:
                log.warning("checker failed: %s", e)
        for t in tests:
            r = results.get(t.id, {})
            if r.get("ok") is None:
                continue
            run["total"] += 1
            run["passed"] += 1 if r.get("ok") else 0
            run["results"].append({"id": t.id, **{k: v for k, v in r.items() if k != "returned"}})
        fname = Path(s.code.get("path") or lang.user_name(code)).name
        for r in run["results"]:
            if r.get("error"):
                run["errors"] += runtime_error_lines(r["error"], fname, lang.user_name(code))
        return run

    def _input_of(self, tid: str):
        t = next((t for t in self.state.tests if t.id == tid), None)
        if t and t.origin == "stress":
            return self.stress_input
        return t.input if t else None

    def _run_one(self, b: runner.Build, t: Test, args, timeout: float) -> dict:
        s = self.state
        stdin = str(args[0]) if s.problem.style == "stdin" and isinstance(args, list) and args else harness.stdin_line(args)
        r = runner.run(b.run_cmd, b.dir, stdin, timeout=timeout, mem_mb=int(self.tcfg.get("memory_mb") or 1024))
        out: dict = {"ms": round(r.ms), "printed": r.printed[-1500:] if s.problem.style != "stdin" else ""}
        if r.timed_out:
            out.update(ok=False, error=f"took longer than {timeout:.0f}s (an endless loop, or too slow)", too_slow=True)
            return out
        if s.problem.style == "stdin":
            actual = r.stdout
            out["actual"], out["returned"] = actual[-2000:], r.ok
        else:
            actual, perr = harness.parse_result(r.result_line)
            if perr:
                out.update(ok=False, error=(r.stderr.strip() or r.error or perr)[-2500:])
                out["error"] = _clean_paths(out["error"], b.dir, s.code.get("path", ""), self.lang.user_name(s.code.get("text", "")))
                return out
            out["actual"], out["returned"] = actual, True
        if not r.ok:
            out.update(ok=False, error=_clean_paths((r.stderr or r.error)[-2500:], b.dir, s.code.get("path", ""),
                                                    self.lang.user_name(s.code.get("text", ""))))
            return out
        if t.expected is None and t.origin == "stress":
            out["ok"] = True   # speed only
        else:
            out["ok"] = harness.outputs_match(t.expected, actual, s.compare)
        if t.origin != "stress":
            out["expected"] = t.expected
        return out

    def measure(self, code: str) -> dict:
        """Time your code at growing sizes and fit the growth (worker thread)."""
        s, lang = self.state, self.lang
        if self.oracle is None or s.problem.style == "stdin":
            return {}
        drv, err = self._harness_for(code)
        b = runner.prepare(lang, code, drv, s.code.get("path", "")) if not err else None
        if not b or not b.ok:
            return {}
        # past the stated limit if needed: below ~10^4 most solutions finish before start-up noise
        n_max = min(max(s.problem.n_max or 10000, 40000), 200000)
        sizes = sorted({max(1, n_max // 64), max(1, n_max // 16), max(1, n_max // 4), n_max})
        try:
            inputs = self.oracle.generate(sizes, seed=21)
            tiny = self.oracle.generate([2], seed=3)[0]
        except oracle.OracleError:
            return {}
        base = min(runner.run(b.run_cmd, b.dir, harness.stdin_line(tiny), timeout=10).ms for _ in range(2))
        points = []
        for n, a in zip(sizes, inputs):
            r = runner.run(b.run_cmd, b.dir, harness.stdin_line(a), timeout=float(self.tcfg.get("stress_timeout_s") or 10))
            if r.timed_out:
                points.append((n, None))
                break
            points.append((n, r.ms))
        timed = [(n, ms) for n, ms in points if ms is not None]
        fit = oracle.fit_complexity(timed, base)
        if any(ms is None for _, ms in points):
            slow = next(n for n, ms in points if ms is None)
            fit["label"] = f"too slow at n = {slow:,}" + (f" ({fit['label']} before that)" if fit.get("exponent") else "")
        fit["baseline_ms"] = round(base)
        return fit

    # ------------------------------------------------------------------ Reviewer
    def review(self, code: str, diagnostics: list[dict] | None = None) -> None:
        s = self.state
        if not code.strip() or not s.problem.defined:
            return
        self._review_code = code
        blocks = "\n".join(f"  {b.id} {b.name}: {b.purpose}\n    pseudo: {b.pseudo.strip()[:300]}" for b in s.blocks) or "  (no design yet)"
        last = s.runs[-1] if s.runs and s.runs[-1].get("code_hash") == _hash(code) else None
        evidence = "none for this version of the code"
        if last:
            fails = [r for r in last["results"] if not r.get("ok")]
            evidence = (f"build error: {last['build_error'][:800]}" if last.get("build_error") else
                        f"{last['passed']}/{last['total']} tests pass" +
                        "".join(f"\n  {r['id']} failed: " + (r.get("error") or
                                f"expected {json.dumps(r.get('expected'))[:120]}, got {json.dumps(r.get('actual'))[:120]}")[:300]
                                for r in fails[:4]))
        syntax = "; ".join(f"line {d['line']}: {d['message']}" for d in (diagnostics or s.code.get("syntax") or [])[:8])
        prompt = (f"PROBLEM STATE:\n{s.brief(include_design=True)}\n\n"
                  f"DESIGN BLOCKS:\n{blocks}\n\nTEST EVIDENCE: {evidence}\n"
                  f"SYNTAX ERRORS: {syntax or 'none'}\n\n"
                  f"{self.name.upper()}'S CODE ({self.lang.name}, numbered):\n{numbered(code)}")
        self.jobs.submit("reviewer", prompt, ctx=_hash(code))

    def _apply_review(self, data: dict, code_hash: str) -> None:
        s = self.state
        if code_hash != _hash(s.code.get("text", "")):
            return   # the code moved on; the next review covers it
        evidence = None
        last = s.runs[-1] if s.runs and s.runs[-1].get("code_hash") == code_hash else None
        failing = bool(last and (last.get("build_error") or last["passed"] < last["total"]))
        passing = bool(last and not last.get("build_error") and last["total"] and last["passed"] == last["total"])
        if failing:
            bad = [r for r in last["results"] if not r.get("ok")]
            evidence = bad[0]["id"] if bad else "build"
        marks = {}
        src = s.code.get("text", "").splitlines()
        for m in data.get("lines", []) if isinstance(data.get("lines"), list) else []:
            try:
                ln = int(m.get("line"))
            except (TypeError, ValueError):
                continue
            kind, why = m.get("mark"), str(m.get("why", ""))[:140]
            if kind == "wrong":
                marks[ln] = {"mark": "red" if failing else "yellow", "why": why,
                             "evidence": evidence if failing else "no failing test yet: run the tests"}
            elif kind == "suspect":
                marks[ln] = {"mark": "yellow", "why": why}
            elif kind == "good" and not failing:
                marks[ln] = {"mark": "green", "why": why, "evidence": "tests pass" if passing else ""}
            if ln in marks and 0 < ln <= len(src):
                marks[ln]["text"] = src[ln - 1].strip()
        s.code["lines"] = marks
        s.code["summary"] = str(data.get("summary", ""))[:300]
        det = str(data.get("detected_approach", ""))
        s.code["detected_approach"] = det if any(a.id == det for a in s.approaches) else ""
        for b in s.blocks:
            st = (data.get("blocks") or {}).get(b.id)
            if st in ("todo", "partial", "done", "issue"):
                b.status = st
            rng = (data.get("block_lines") or {}).get(b.id)
            if isinstance(rng, list) and len(rng) == 2:
                b.lines = rng
        for r in s.live_requirements():
            st = (data.get("requirements") or {}).get(r.id)
            if st in ("no", "partial", "yes"):
                r.met = st
        if s.phase in ("defined", "designing") and len(s.code.get("text", "").strip().splitlines()) > 6:
            s.phase = "coding"
        s.version += 1
        self.on_change({"review", "blocks", "requirements", "phase"})

    # ------------------------------------------------------------------ Debug
    def debug(self, code: str) -> None:
        self.on_status("Debugging: running every test…")
        threading.Thread(target=self._debug, args=(code,), daemon=True).start()

    def _debug(self, code: str) -> None:
        s, lang = self.state, self.lang
        with self._run_lock:
            run = self.run_now(code, stress=False)
        self.post(lambda: (s.runs.append(run), self.on_change({"runs", "tests"})))
        report: dict = {"code_hash": _hash(code), "run": {"passed": run["passed"], "total": run["total"]}}
        fname = Path(s.code.get("path") or lang.user_name(code)).name
        if not run.get("build_error") and run["total"] and run["passed"] == run["total"]:
            report.update(summary=f"All {run['total']} tests pass. Nothing is failing; try the stress test or "
                                  f"walk an edge case out loud.", lines=[], fix=[], say_it="")
            self.post(lambda: self._set_debug(report))
            return
        evidence = []
        if run.get("build_error"):
            evidence.append(f"BUILD OUTPUT:\n{run['build_error'][-3000:]}")
        else:
            fails = [r for r in run["results"] if not r.get("ok")]
            fails.sort(key=lambda r: oracle._size(self._input_of(r["id"])))
            first = fails[0]
            t = next(t for t in s.tests if t.id == first["id"])
            small = t.input
            if self.oracle is not None and isinstance(t.input, list) and s.problem.style != "stdin":
                self.on_status("Debugging: shrinking the failing input…")
                small = self._shrink(code, t)
            trace = ""
            if small is not t.input:
                r_small = self._run_args(code, small)
                expected = r_small.get("expected")
                evidence.append(f"SMALLEST FAILING INPUT (shrunk from {t.name}): {json.dumps(small)[:600]}\n"
                                f"expected: {json.dumps(expected)[:400]}\nactual: {json.dumps(r_small.get('actual'))[:400]}"
                                + (f"\nerror: {r_small['error'][-1200:]}" if r_small.get("error") else ""))
                report["input"], report["expected"], report["actual"] = small, expected, r_small.get("actual")
            else:
                evidence.append(f"FAILING TEST {t.name}: input {json.dumps(t.input)[:600]}\n"
                                f"expected: {json.dumps(t.expected)[:400]}\nactual: {json.dumps(first.get('actual'))[:400]}"
                                + (f"\nerror: {first['error'][-1200:]}" if first.get("error") else ""))
                report["input"], report["expected"], report["actual"] = t.input, t.expected, first.get("actual")
            if lang.key == "python" and s.problem.style != "stdin":
                trace = self._trace(code, report.get("input"))
                if trace:
                    evidence.append("EXECUTION TRACE (line: variables before it runs):\n" + trace)
                    report["trace"] = trace
            others = [f"{r['id']}: " + (r.get("error", "")[-200:] or f"expected {json.dumps(r.get('expected'))[:80]}, got {json.dumps(r.get('actual'))[:80]}")
                      for r in fails[1:6]]
            if others:
                evidence.append("OTHER FAILURES:\n" + "\n".join(others))
        prompt = (f"PROBLEM STATE:\n{s.brief(include_design=False)}\n\n{self.name.upper()}'S CODE ({fname}, numbered):\n"
                  f"{numbered(code)}\n\n" + "\n\n".join(evidence))
        self.on_status("Debugging: finding the broken lines…")
        self.jobs.submit("debugger", prompt, ctx=report)

    def _run_args(self, code: str, args) -> dict:
        """Run your code on one input and compare with the reference (worker thread)."""
        s, lang = self.state, self.lang
        drv, err = self._harness_for(code)
        b = runner.prepare(lang, code, drv, s.code.get("path", "")) if not err else None
        if not b or not b.ok:
            return {"ok": False, "error": err or (b.output if b else "build failed")}
        try:
            r = self.oracle.expected([args])[0]
            exp = r.get("output") if r.get("ok") else None
        except oracle.OracleError:
            exp = None
        t = Test("probe", "probe", "edge", args, exp)
        out = self._run_one(b, t, args, float(self.tcfg.get("test_timeout_s") or 5))
        if "check" in (s.oracle.get("has") or []) and out.get("returned"):
            try:
                out["ok"] = self.oracle.check([{"args": args, "output": out["actual"]}], s.compare)[0]
            except oracle.OracleError:
                pass
        out["expected"] = exp
        return out

    def _shrink(self, code: str, t: Test):
        def still_fails(cand):
            try:
                if not self.oracle.valid([cand])[0]:
                    return False
            except oracle.OracleError:
                return False
            r = self._run_args(code, cand)
            return r.get("expected") is not None and not r.get("ok")
        small, runs = oracle.shrink(t.input, still_fails, budget=int(self.tcfg.get("shrink_budget") or 60))
        log.info("shrunk %s in %d runs: %s", t.id, runs, json.dumps(small)[:200])
        return small

    def _trace(self, code: str, args) -> str:
        s, lang = self.state, self.lang
        drv, err = self._harness_for(code)
        b = runner.prepare(lang, code, drv, s.code.get("path", "")) if not err else None
        if not b or not b.ok or args is None:
            return ""
        r = runner.run(b.run_cmd, b.dir, harness.stdin_line(args), timeout=10, env={"CUE_TRACE": "1"})
        line = next((l for l in reversed(r.stdout.splitlines()) if l.startswith("@@CUE_TRACE@@")), None)
        if not line:
            return ""
        try:
            steps = json.loads(line[len("@@CUE_TRACE@@"):])
        except ValueError:
            return ""
        src = code.splitlines()
        out = []
        for st in steps[:120]:
            ln = st.get("line", 0)
            text = src[ln - 1].strip() if 0 < ln <= len(src) else ""
            vars_ = ", ".join(f"{k}={v}" for k, v in st.get("vars", {}).items())
            tail = f" -> returns {st['value']}" if st.get("event") == "return" else (" !! exception" if st.get("event") == "exception" else "")
            out.append(f"{ln:>3}  {text[:70]}\n       {vars_[:160]}{tail}")   # the line, then its variables
        if len(steps) > 120:
            out.append(f"... ({len(steps) - 120} more steps)")
        return "\n".join(out)

    def _apply_debug(self, data, err, report: dict) -> None:
        if err:
            report.update(summary=f"The debugger couldn't finish: {err[:200]}", lines=[], fix=[], say_it="")
        else:
            report.update(summary=str(data.get("summary", "")), lines=data.get("lines") or [],
                          fix=data.get("fix") or [], say_it=str(data.get("say_it", "")))
        self._set_debug(report)

    def _set_debug(self, report: dict) -> None:
        s = self.state
        s.debug = report
        if report.get("code_hash") == _hash(s.code.get("text", "")):
            src = s.code.get("text", "").splitlines()
            for m in report.get("lines", []):
                try:
                    ln = int(m.get("line"))
                    s.code["lines"][ln] = {"mark": "red", "why": str(m.get("why", ""))[:160],
                                           "evidence": str(m.get("evidence", ""))[:160],
                                           "text": src[ln - 1].strip() if 0 < ln <= len(src) else ""}
                except (TypeError, ValueError):
                    pass
        s.version += 1
        self.on_change({"debug", "review"})
        self.on_status("Debug report ready")

    def close(self) -> None:
        self.jobs.close()
        if self.oracle:
            self.oracle.close()


def runtime_error_lines(text: str, shown_name: str, build_name: str) -> list[dict]:
    """Line numbers in your file from a runtime error / stack trace, with the error message."""
    lines = text.splitlines()
    msg = next((l.strip() for l in reversed(lines) if re.match(r"^\s*[\w.]*(Error|Exception|panic|error)\b", l)),
               lines[-1].strip() if lines else "")
    out, seen = [], set()
    for name in {shown_name, build_name}:
        for m in re.finditer(rf'{re.escape(name)}"?(?:, line |:)(\d+)', text):
            ln = int(m.group(1))
            if ln not in seen:
                seen.add(ln)
                out.append({"line": ln, "message": msg[:200]})
    return out[-3:]


def _clean_paths(text: str, d: Path, user_path: str, build_name: str) -> str:
    """Errors name the copy in the build folder: show your file's name instead."""
    text = text.replace(str(d) + "\\", "").replace(str(d) + "/", "")
    name = Path(user_path).name if user_path else ""
    return text.replace(build_name, name) if name and name != build_name else text


def _hash(code: str) -> str:
    return hashlib.sha1(code.encode("utf-8")).hexdigest()[:12]


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0
