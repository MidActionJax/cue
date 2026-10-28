"""Where expected outputs come from: running code, not trusting a model's arithmetic.

The Test author writes one Python module with two independent solutions (`reference`, fast,
and `brute`, deliberately simple), a random input generator `gen(n, rnd)`, and optionally
`valid(*args)` (is this input allowed?) and `check(args, output)` (for problems where several
answers are right). Cue runs reference and brute side by side on hundreds of random small inputs
plus the edge cases; expected outputs are trusted only when the two agree.

The module runs in Cue's own Python (`Cue.exe trainer-exec`), so Python needn't be installed.
"""
from __future__ import annotations

import json
import logging
import math
import queue
import subprocess
import threading
from pathlib import Path
from typing import Callable

from ..cli import self_cmd, self_cwd
from ..config import DATA

log = logging.getLogger(__name__)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

DRIVER = r'''# Cue oracle: answers one JSON request per line about the reference solution.
import copy, json, random, sys, traceback
sys.setrecursionlimit(100000)
from cue.trainer.harness import outputs_match

ns = {"__name__": "__cue_oracle__"}
exec(compile(open(sys.argv[1], encoding="utf-8").read(), "oracle.py", "exec"), ns)
reference, brute = ns.get("reference"), ns.get("brute")
gen, valid, check = ns.get("gen"), ns.get("valid"), ns.get("check")


def call(fn, args):
    return fn(*copy.deepcopy(args))


def is_valid(args):
    if valid is None:
        return True
    try:
        return bool(valid(*copy.deepcopy(args)))
    except Exception:
        return False


def agree(args, a, b, mode):
    if check is not None:
        return bool(check(copy.deepcopy(args), a)) and bool(check(copy.deepcopy(args), b))
    return outputs_match(a, b, mode)


def short(v, n=300):
    s = json.dumps(v, default=str)
    return s if len(s) <= n else s[:n] + "…"


def handle(req):
    cmd = req.get("cmd")
    if cmd == "verify":
        mode, rnd = req.get("compare", "exact"), random.Random(req.get("seed", 7))
        inputs = [a for a in req.get("edge", []) if is_valid(a)]
        max_n, tries = req.get("max_n", 8), 0
        while len(inputs) < len(req.get("edge", [])) + req.get("random", 300) and tries < 3000 and gen:
            tries += 1
            try:
                a = gen(rnd.randint(0, max_n), rnd)
            except Exception:
                continue
            if is_valid(a):
                inputs.append(a)
        bad, errors, checked = [], [], 0
        for a in inputs:
            try:
                r = call(reference, a)
            except Exception as e:
                errors.append({"input": short(a), "who": "reference", "error": f"{e.__class__.__name__}: {e}"})
                continue
            if brute is None:
                checked += 1
                continue
            try:
                b = call(brute, a)
            except Exception as e:
                errors.append({"input": short(a), "who": "brute", "error": f"{e.__class__.__name__}: {e}"})
                continue
            checked += 1
            if not agree(a, r, b, mode):
                bad.append({"input": short(a), "reference": short(r), "brute": short(b)})
        return {"checked": checked, "disagree": len(bad), "examples": bad[:5], "errors": errors[:5],
                "error_count": len(errors), "generated": len(inputs)}
    if cmd == "expected":
        out = []
        for a in req["inputs"]:
            try:
                out.append({"ok": True, "output": call(reference, a), "valid": is_valid(a)})
            except Exception as e:
                out.append({"ok": False, "error": f"{e.__class__.__name__}: {e}", "valid": is_valid(a)})
        return {"results": out}
    if cmd == "gen":
        rnd = random.Random(req.get("seed", 11))
        out = []
        for n in req["sizes"]:
            for _ in range(20):
                a = gen(n, rnd)
                if is_valid(a):
                    break
            out.append(a)
        return {"inputs": out}
    if cmd == "check":
        res = []
        for c in req["cases"]:
            try:
                if check is not None:
                    res.append(bool(check(copy.deepcopy(c["args"]), c["output"])))
                else:
                    res.append(outputs_match(call(reference, c["args"]), c["output"], req.get("compare", "exact")))
            except Exception:
                res.append(False)
        return {"results": res}
    if cmd == "valid":
        return {"results": [is_valid(a) for a in req["inputs"]]}
    return {"error": f"unknown command {cmd}"}


print(json.dumps({"ready": True, "has": [k for k in ("reference", "brute", "gen", "valid", "check") if ns.get(k)]}),
      flush=True)
for line in sys.stdin:
    if not line.strip():
        continue
    try:
        resp = handle(json.loads(line))
    except Exception as e:
        resp = {"error": f"{e.__class__.__name__}: {e}", "trace": traceback.format_exc()[-1500:]}
    print(json.dumps(resp, default=str), flush=True)
'''


class OracleError(Exception):
    pass


class Oracle:
    """A long-lived oracle process for one problem (restarted if a request hangs)."""

    def __init__(self, module_code: str, folder: Path | None = None):
        self.folder = folder or DATA / "trainer" / "oracle"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.module = self.folder / "oracle.py"
        self.module.write_text(module_code, encoding="utf-8")
        self.driver = self.folder / "cue_oracle_driver.py"
        self.driver.write_text(DRIVER, encoding="utf-8")
        self.proc: subprocess.Popen | None = None
        self.has: list[str] = []
        self.lock = threading.Lock()
        self._out: queue.Queue = queue.Queue()

    def _start(self, timeout: float = 30) -> None:
        self.close()
        self._out = queue.Queue()
        self.proc = subprocess.Popen(self_cmd("trainer-exec", str(self.driver), str(self.module)), cwd=self_cwd(),
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, encoding="utf-8", bufsize=1, creationflags=_NO_WINDOW,
                                     env=_env())
        q, p = self._out, self.proc
        threading.Thread(target=lambda: [q.put(l) for l in p.stdout] + [q.put(None)], daemon=True).start()
        first = self._read(timeout)
        if not first or not first.get("ready"):
            err = (self.proc.stderr.read() if self.proc.poll() is not None else "")[-1500:]
            self.close()
            raise OracleError(f"the test oracle didn't start: {err or first}")
        self.has = first.get("has", [])

    def _read(self, timeout: float) -> dict | None:
        try:
            line = self._out.get(timeout=timeout)
        except queue.Empty:
            return None
        if line is None:
            return None
        try:
            return json.loads(line)
        except ValueError:
            return {"error": f"bad oracle output: {line[:200]}"}

    def call(self, req: dict, timeout: float = 30) -> dict:
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self._start()
            try:
                self.proc.stdin.write(json.dumps(req, default=str) + "\n")
                self.proc.stdin.flush()
            except OSError as e:
                self.close()
                raise OracleError(f"oracle crashed: {e}")
            resp = self._read(timeout)
            if resp is None:
                self.close()   # hung (an endless loop in the reference) or died: next call restarts it
                raise OracleError(f"the oracle took longer than {timeout:.0f}s ({req.get('cmd')})")
            if "error" in resp:
                raise OracleError(resp["error"])
            return resp

    def close(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.kill()
            except OSError:
                pass
        self.proc = None

    # convenience
    def verify(self, edge: list, compare: str, max_n: int = 8, count: int = 300) -> dict:
        return self.call({"cmd": "verify", "edge": edge, "random": count, "max_n": max_n, "compare": compare},
                         timeout=60)

    def expected(self, inputs: list) -> list[dict]:
        return self.call({"cmd": "expected", "inputs": inputs}, timeout=60)["results"]

    def generate(self, sizes: list[int], seed: int = 11) -> list:
        return self.call({"cmd": "gen", "sizes": sizes, "seed": seed}, timeout=60)["inputs"]

    def check(self, cases: list[dict], compare: str) -> list[bool]:
        return self.call({"cmd": "check", "cases": cases, "compare": compare}, timeout=60)["results"]

    def valid(self, inputs: list) -> list[bool]:
        return self.call({"cmd": "valid", "inputs": inputs}, timeout=30)["results"]


def _env() -> dict:
    import os
    return dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", PYTHONDONTWRITEBYTECODE="1")


# ---------------------------------------------------------------------------- shrinking a failing input

def _candidates(args: list):
    """Smaller versions of an input: drop chunks of each list/string, then single items, then
    pull numbers toward zero. Biggest cuts first, so a few accepted steps shrink it a lot."""
    for i, a in enumerate(args):
        if isinstance(a, (list, str)) and len(a) > 0:
            n = len(a)
            size = n // 2
            while size >= 1:
                for start in range(0, n, size):
                    smaller = a[:start] + a[start + size:]
                    yield args[:i] + [smaller] + args[i + 1:]
                size //= 2
            if isinstance(a, list):
                for j, x in enumerate(a):
                    if isinstance(x, (list, str)) and x:
                        for sub in _candidates([x]):
                            yield args[:i] + [a[:j] + [sub[0]] + a[j + 1:]] + args[i + 1:]
                    elif isinstance(x, int) and not isinstance(x, bool) and x not in (0, 1):
                        for y in (0, 1, x // 2):
                            if y != x:
                                yield args[:i] + [a[:j] + [y] + a[j + 1:]] + args[i + 1:]
        elif isinstance(a, int) and not isinstance(a, bool) and a not in (0, 1, -1):
            for y in (0, 1, a // 2):
                if y != a:
                    yield args[:i] + [y] + args[i + 1:]


def shrink(args: list, still_fails: Callable[[list], bool], budget: int = 80) -> tuple[list, int]:
    """Smallest input that still fails (greedy delta debugging). `still_fails` must also reject
    inputs the problem doesn't allow. Returns (input, runs used)."""
    best, runs = args, 0
    improved = True
    while improved and runs < budget:
        improved = False
        for cand in _candidates(best):
            if runs >= budget:
                break
            if _size(cand) >= _size(best):
                continue
            runs += 1
            try:
                ok = still_fails(cand)
            except Exception:
                ok = False
            if ok:
                best, improved = cand, True
                break
    return best, runs


def _size(v) -> int:
    if isinstance(v, (list, tuple)):
        return 1 + sum(_size(x) for x in v)
    if isinstance(v, str):
        return 1 + len(v)
    if isinstance(v, int) and not isinstance(v, bool):
        return 1 + min(abs(v), 10 ** 6).bit_length()
    return 1


# ---------------------------------------------------------------------------- measured complexity

def fit_complexity(points: list[tuple[int, float]], baseline_ms: float) -> dict:
    """Growth exponent from (n, ms) runs: the slope of log(time) over log(n), with process start-up
    (the baseline) taken out. Only points well above the noise count."""
    usable = [(n, ms - baseline_ms) for n, ms in points if n > 0 and ms - baseline_ms > 25]
    if len(usable) < 2:
        return {"exponent": None, "label": "too fast to measure (fine)", "points": points}
    (n1, t1), (n2, t2) = usable[-2], usable[-1]
    if n2 <= n1:
        return {"exponent": None, "label": "not enough sizes", "points": points}
    k = math.log(max(t2, 1e-3) / max(t1, 1e-3)) / math.log(n2 / n1)
    if k < 0.4:
        label = "≈ O(1) / O(log n)"
    elif k < 1.35:
        label = "≈ O(n) or O(n log n)"
    elif k < 1.75:
        label = "≈ O(n^1.5)"
    elif k < 2.5:
        label = "≈ O(n²)"
    else:
        label = "≈ O(n³) or worse"
    return {"exponent": round(k, 2), "label": label, "points": points}
