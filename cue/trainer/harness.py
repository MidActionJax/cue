"""Test drivers: the small program that feeds one test case to your function and prints what it
returned, without touching your file.

Protocol (every language, so results compare the same way):
    stdin   one line: a JSON array with the arguments, in the signature's order
    stdout  your own prints, then a last line "@@CUE_RESULT@@ <json>" with the return value
Linked lists and trees travel as LeetCode-style arrays ([1,2,3] / [1,null,2]) and are built into
ListNode / TreeNode objects for your code.

Python and JavaScript drivers are written here. Other languages get one from the model (see
prompts.HARNESS), checked by compiling it against the reference code before it's trusted.
"""
from __future__ import annotations

import json
import math

from .langs import Language

PY = r'''# Cue test driver: calls your function with one test case (stdin) and prints the result.
import json, sys, traceback, typing
from collections import *          # LeetCode-style code often uses these without importing
from typing import *

SPEC = __SPEC__
USER = __import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), __USER__)
MARK = "@@CUE_RESULT@@"


class ListNode:
    def __init__(self, val=0, next=None):
        self.val, self.next = val, next


class TreeNode:
    def __init__(self, val=0, left=None, right=None):
        self.val, self.left, self.right = val, left, right


def to_list(L, head):
    out, seen = [], 0
    while head is not None and seen < 100000:
        out.append(head.val)
        head, seen = head.next, seen + 1
    return out


def from_list(L, vals):
    dummy = cur = L(0)
    for v in vals or []:
        cur.next = L(v)
        cur = cur.next
    return dummy.next


def from_tree(T, vals):
    if not vals or vals[0] is None:
        return None
    root = T(vals[0])
    q, i = [root], 1
    while q and i < len(vals):
        node = q.pop(0)
        for side in ("left", "right"):
            if i < len(vals) and vals[i] is not None:
                child = T(vals[i])
                setattr(node, side, child)
                q.append(child)
            i += 1
    return root


def to_tree(root):
    out, q = [], [root]
    while q:
        node = q.pop(0)
        if node is None:
            out.append(None)
            continue
        out.append(node.val)
        q += [node.left, node.right]
    while out and out[-1] is None:
        out.pop()
    return out


TRACE = __import__("os").environ.get("CUE_TRACE") == "1"
STEPS = []


def _short(v):
    try:
        s = repr(v)
    except Exception:
        s = "?"
    return s if len(s) <= 80 else s[:77] + "..."


def tracer(frame, event, arg):
    """Debug runs: every line executed in your file, with the local variables at that moment."""
    if frame.f_code.co_filename != USER:
        return None
    if len(STEPS) >= 400:
        return None
    if event in ("line", "return", "exception"):
        loc = {k: _short(v) for k, v in frame.f_locals.items() if not k.startswith("__") and k != "self"}
        step = {"line": frame.f_lineno, "fn": frame.f_code.co_name, "event": event, "vars": loc}
        if event == "return":
            step["value"] = _short(arg)
        STEPS.append(step)
    return tracer


def norm(name):
    return name.replace("_", "").lower()


def find(ns, name):
    mine = {k: v for k, v in ns.items() if not k.startswith("_") and callable(v)
            and getattr(getattr(v, "__code__", None), "co_filename", None) == USER}
    if name in mine:
        return mine[name]
    for k, v in mine.items():
        if norm(k) == norm(name):
            return v
    cls = ns.get("Solution")
    if isinstance(cls, type):
        obj = cls()
        methods = [m for m in dir(obj) if not m.startswith("_") and callable(getattr(obj, m))]
        for m in methods:
            if norm(m) == norm(name):
                return getattr(obj, m)
        if len(methods) == 1:
            return getattr(obj, methods[0])
    if len(mine) == 1:
        return next(iter(mine.values()))
    raise NameError(f"couldn't find a function called {name} in your file")


def convert_in(L, T, typ, v):
    t = (typ or "").replace(" ", "").lower()
    if t.startswith("listnode[]") or t.startswith("list[listnode"):
        return [from_list(L, x) for x in v]
    if "listnode" in t:
        return from_list(L, v)
    if "treenode" in t:
        return from_tree(T, v)
    return v


def convert_out(L, T, v):
    if isinstance(v, L) or (v is not None and hasattr(v, "next") and hasattr(v, "val")):
        return to_list(L, v)
    if v is not None and hasattr(v, "left") and hasattr(v, "right") and hasattr(v, "val"):
        return to_tree(v)
    if isinstance(v, (list, tuple)):
        return [convert_out(L, T, x) for x in v]
    if isinstance(v, (set, frozenset)):
        return sorted(convert_out(L, T, x) for x in v)
    if isinstance(v, dict):
        return {str(k): convert_out(L, T, x) for k, x in v.items()}
    if isinstance(v, float) and v != v:
        return "NaN"
    return v


def main():
    args = json.loads(sys.stdin.readline() or "[]")
    ns = {"__name__": "__cue_solution__", "__file__": USER, "ListNode": ListNode, "TreeNode": TreeNode,
          "List": typing.List, "Optional": typing.Optional, "Dict": typing.Dict, "Tuple": typing.Tuple,
          "Set": typing.Set}
    for k in ("defaultdict", "Counter", "deque", "OrderedDict"):
        ns[k] = globals()[k]
    with open(USER, encoding="utf-8") as f:
        code = compile(f.read(), USER, "exec")
    exec(code, ns)
    L, T = ns.get("ListNode", ListNode), ns.get("TreeNode", TreeNode)
    fn = find(ns, SPEC["name"])
    params = SPEC.get("params") or []
    call = [convert_in(L, T, params[i][1] if i < len(params) else "", a) for i, a in enumerate(args)]
    sys.setrecursionlimit(100000)
    if TRACE:
        sys.settrace(tracer)
    try:
        out = fn(*call)
    finally:
        sys.settrace(None)
        if TRACE:
            print("@@CUE_TRACE@@ " + json.dumps(STEPS, default=str))
    ret = (SPEC.get("returns") or "").lower()
    if out is None and call and ret in ("", "none", "void", "null"):
        out = call[0]   # in-place: the result is the first argument after the call
    if out is None and ("listnode" in ret or "treenode" in ret):
        out = []        # an empty list / tree travels as []
    sys.stdout.flush()
    print(MARK + " " + json.dumps(convert_out(L, T, out), default=str))


try:
    main()
except Exception as e:
    tb = traceback.extract_tb(e.__traceback__)
    mine = [f for f in tb if f.filename == USER]
    print("Traceback (your code):", file=sys.stderr)
    for f in mine or tb[-1:]:
        print(f'  File "{f.filename}", line {f.lineno}, in {f.name}\n    {f.line}', file=sys.stderr)
    print(f"{e.__class__.__name__}: {e}", file=sys.stderr)
    sys.exit(1)
'''

JS = r'''// Cue test driver: calls your function with one test case (stdin) and prints the result.
const fs = require("fs"), vm = require("vm");
const SPEC = __SPEC__;
const USER = require("path").join(__dirname, __USER__);
const MARK = "@@CUE_RESULT@@";

function ListNode(val, next) { this.val = val === undefined ? 0 : val; this.next = next === undefined ? null : next; }
function TreeNode(val, left, right) {
  this.val = val === undefined ? 0 : val; this.left = left === undefined ? null : left; this.right = right === undefined ? null : right;
}
globalThis.ListNode = ListNode; globalThis.TreeNode = TreeNode;
globalThis.module = { exports: {} }; globalThis.exports = globalThis.module.exports; globalThis.require = require;

const fromList = (L, vals) => { const d = new L(0); let c = d; for (const v of vals || []) { c.next = new L(v); c = c.next; } return d.next; };
const toList = (h) => { const o = []; let n = 0; while (h && n++ < 100000) { o.push(h.val); h = h.next; } return o; };
const fromTree = (T, vals) => {
  if (!vals || !vals.length || vals[0] === null) return null;
  const root = new T(vals[0]); const q = [root]; let i = 1;
  while (q.length && i < vals.length) {
    const n = q.shift();
    for (const side of ["left", "right"]) {
      if (i < vals.length && vals[i] !== null) { const c = new T(vals[i]); n[side] = c; q.push(c); }
      i++;
    }
  }
  return root;
};
const toTree = (root) => {
  const out = [], q = [root];
  while (q.length) { const n = q.shift(); if (!n) { out.push(null); continue; } out.push(n.val); q.push(n.left, n.right); }
  while (out.length && out[out.length - 1] === null) out.pop();
  return out;
};
const norm = (s) => s.replace(/_/g, "").toLowerCase();

function convertOut(v) {
  if (v && typeof v === "object" && "val" in v && "next" in v) return toList(v);
  if (v && typeof v === "object" && "val" in v && "left" in v && "right" in v) return toTree(v);
  if (Array.isArray(v)) return v.map(convertOut);
  if (v instanceof Set) return [...v].map(convertOut).sort();
  if (v instanceof Map) return Object.fromEntries([...v].map(([k, x]) => [String(k), convertOut(x)]));
  if (typeof v === "bigint") return Number(v);
  if (v === undefined) return null;
  return v;
}

function lookup(name) {
  try { return vm.runInThisContext(`typeof ${name} !== "undefined" ? ${name} : undefined`); } catch (e) { return undefined; }
}

function findFn(src, name) {
  const names = new Set();
  for (const re of [/function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\(/g, /(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:function|\(|[A-Za-z_$][\w$]*\s*=>)/g,
                    /class\s+([A-Za-z_$][\w$]*)/g]) {
    let m; while ((m = re.exec(src))) names.add(m[1]);
  }
  const exp = globalThis.module.exports;
  if (typeof exp === "function") return exp;
  for (const k of Object.keys(exp || {})) if (typeof exp[k] === "function" && norm(k) === norm(name)) return exp[k];
  for (const n of names) if (norm(n) === norm(name)) { const f = lookup(n); if (typeof f === "function" && !/^class\s/.test(String(f))) return f; }
  const Sol = lookup("Solution");
  if (typeof Sol === "function") {
    const obj = new Sol(); const ms = Object.getOwnPropertyNames(Object.getPrototypeOf(obj)).filter((m) => m !== "constructor");
    const hit = ms.find((m) => norm(m) === norm(name)) || (ms.length === 1 ? ms[0] : null);
    if (hit) return obj[hit].bind(obj);
  }
  const fns = [...names].map(lookup).filter((f) => typeof f === "function" && f !== ListNode && f !== TreeNode && !/^class\s/.test(String(f)));
  if (fns.length === 1) return fns[0];
  throw new Error(`couldn't find a function called ${name} in your file`);
}

try {
  const line = fs.readFileSync(0, "utf8").split("\n")[0] || "[]";
  const args = JSON.parse(line);
  const src = fs.readFileSync(USER, "utf8");
  vm.runInThisContext(src, { filename: USER });
  const fn = findFn(src, SPEC.name);
  const L = lookup("ListNode") || ListNode, T = lookup("TreeNode") || TreeNode;
  const params = SPEC.params || [];
  const call = args.map((a, i) => {
    const t = ((params[i] || [])[1] || "").replace(/\s/g, "").toLowerCase();
    if (t.startsWith("listnode[]")) return a.map((x) => fromList(L, x));
    if (t.includes("listnode")) return fromList(L, a);
    if (t.includes("treenode")) return fromTree(T, a);
    return a;
  });
  let out = fn(...call);
  const ret = (SPEC.returns || "").toLowerCase();
  if (out === undefined && call.length && ["", "void", "none", "null", "undefined"].includes(ret)) out = call[0];
  if ((out === null || out === undefined) && (ret.includes("listnode") || ret.includes("treenode"))) out = [];
  process.stdout.write("\n" + MARK + " " + JSON.stringify(convertOut(out)) + "\n");
} catch (e) {
  const stack = String(e && e.stack || e).split("\n").filter((l, i) => i === 0 || l.includes(USER)).join("\n");
  process.stderr.write(stack + "\n");
  process.exit(1);
}
'''


def builtin(lang: Language, signature: dict, user_file: str) -> str | None:
    """The driver Cue writes itself (Python, JavaScript), or None for languages that need a model one.
    `user_file` is your file's name in the build folder (next to the driver)."""
    spec = json.dumps({"name": signature.get("name", "solve"), "params": signature.get("params", []),
                       "returns": signature.get("returns", "")})
    if lang.key == "python":
        return PY.replace("__SPEC__", spec).replace("__USER__", json.dumps(user_file))
    if lang.key == "javascript":
        return JS.replace("__SPEC__", spec).replace("__USER__", json.dumps(user_file))
    return None


def stdin_line(args) -> str:
    return json.dumps(args if isinstance(args, list) else [args]) + "\n"


def parse_result(line: str | None):
    if line is None:
        return None, "no result printed (did it crash, or exit early?)"
    try:
        return json.loads(line), None
    except ValueError:
        return None, f"couldn't read the result: {line[:200]}"


# ---------------------------------------------------------------------------- comparing outputs

def _canon(v):
    """Make values from different languages comparable: tuples are lists, 1.0 == 1, "true" stays a string."""
    if isinstance(v, tuple):
        v = list(v)
    if isinstance(v, list):
        return [_canon(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _canon(x) for k, x in v.items()}
    if isinstance(v, float) and v.is_integer() and abs(v) < 2 ** 53:
        return int(v)
    return v


def _sort_key(v):
    return json.dumps(v, sort_keys=True)


def outputs_match(expected, actual, mode: str = "exact") -> bool:
    e, a = _canon(expected), _canon(actual)
    if mode.startswith("float"):
        tol = 1e-6
        if ":" in mode:
            try:
                tol = float(mode.split(":", 1)[1])
            except ValueError:
                pass
        return _close(e, a, tol)
    if mode == "unordered" and isinstance(e, list) and isinstance(a, list):
        return sorted(e, key=_sort_key) == sorted(a, key=_sort_key)
    if mode == "unordered_deep":
        return _deep_sort(e) == _deep_sort(a)
    if mode == "text":
        return " ".join(str(e).split()) == " ".join(str(a).split())
    return e == a


def _deep_sort(v):
    if isinstance(v, list):
        return sorted((_deep_sort(x) for x in v), key=_sort_key)
    return v


def _close(e, a, tol: float) -> bool:
    if isinstance(e, (int, float)) and isinstance(a, (int, float)) and not isinstance(e, bool):
        return math.isclose(e, a, rel_tol=tol, abs_tol=tol)
    if isinstance(e, list) and isinstance(a, list):
        return len(e) == len(a) and all(_close(x, y, tol) for x, y in zip(e, a))
    return e == a
