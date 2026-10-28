"""Trainer core: state patches, test drivers, the runner, the oracle and shrinking.

    python -m pytest tests/trainer
"""
import json
import time

import pytest

from cue.trainer import harness, langs, oracle, runner
from cue.trainer.state import ProblemState


# ---------------------------------------------------------------------------- state patches

def test_requirements_are_edited_not_duplicated():
    s = ProblemState()
    s.apply([{"op": "add_req", "text": "Return the indices of the two numbers", "kind": "io",
              "status": "stated", "source": "give me the positions"}])
    s.apply([{"op": "add_req", "text": "Return the indices of the two numbers.", "status": "stated",
              "source": "positions, yeah"}])
    assert len(s.live_requirements()) == 1
    s.apply([{"op": "edit_req", "id": "R1", "text": "Return the indices in increasing order"}])
    r = s.req("R1")
    assert r.text == "Return the indices in increasing order"
    assert r.history and r.history[0]["text"].startswith("Return the indices of")


def test_stated_without_a_quote_becomes_assumed():
    s = ProblemState()
    s.apply([{"op": "add_req", "text": "Input fits in memory", "status": "stated"}])
    assert s.req("R1").status == "assumed"


def test_drop_and_unknown_ops():
    s = ProblemState()
    s.apply([{"op": "add_req", "text": "Array is sorted", "source": "it's sorted"}])
    changed = s.apply([{"op": "drop_req", "id": "R1", "reason": "they said unsorted"},
                       {"op": "nonsense"}, {"op": "edit_req", "id": "R99", "text": "x"}])
    assert changed == {"R1"}
    assert not s.live_requirements()


def test_questions_lifecycle():
    s = ProblemState()
    s.apply([{"op": "add_q", "text": "Can the array be empty?", "priority": 1}])
    s.apply([{"op": "add_q", "text": "Can the array be empty"}])   # duplicate: ignored
    assert len(s.questions) == 1
    s.apply([{"op": "q_status", "id": "Q1", "status": "answered", "answer": "No, at least 2"}])
    q = s.question("Q1")
    assert q.status == "answered" and q.answer == "No, at least 2"


def test_timer_and_roundtrip(tmp_path):
    s = ProblemState()
    s.apply([{"op": "estimate", "minutes": 20, "rationale": "medium"}])
    s.start_timer(now=1000)
    assert s.seconds_left(now=1060) == 20 * 60 - 60
    s.pause_timer(now=1060)
    assert s.seconds_left(now=5000) == 20 * 60 - 60
    s.start_timer(now=5000)
    assert abs(s.seconds_left(now=5010) - (20 * 60 - 70)) < 1e-6
    p = tmp_path / "state.json"
    s.save(p)
    s2 = ProblemState.from_dict(json.loads(p.read_text()))
    assert s2.estimate.minutes == 20 and s2.version == s.version


# ---------------------------------------------------------------------------- comparing outputs

def test_outputs_match_modes():
    assert harness.outputs_match([0, 1], [0, 1])
    assert harness.outputs_match([0, 1], (0, 1.0))
    assert not harness.outputs_match([0, 1], [1, 0])
    assert harness.outputs_match([[1, 2], [3]], [[3], [1, 2]], "unordered")
    assert harness.outputs_match([[2, 1], [3]], [[3], [1, 2]], "unordered_deep")
    assert harness.outputs_match(0.1 + 0.2, 0.3, "float")
    assert harness.outputs_match("a  b\n", "a b", "text")


# ---------------------------------------------------------------------------- python / js drivers

SIG = {"name": "two_sum", "params": [["nums", "int[]"], ["target", "int"]], "returns": "int[]"}
PY_GOOD = "def twoSum(nums, target):\n    seen = {}\n    for i, x in enumerate(nums):\n" \
          "        if target - x in seen:\n            return [seen[target - x], i]\n        seen[x] = i\n"
PY_LEET = "class Solution:\n    def twoSum(self, nums: List[int], target: int) -> List[int]:\n" \
          "        print('debug', len(nums))\n" \
          "        for i in range(len(nums)):\n            for j in range(i + 1, len(nums)):\n" \
          "                if nums[i] + nums[j] == target:\n                    return [i, j]\n"
JS_GOOD = "const twoSum = (nums, target) => {\n  const m = new Map();\n  for (let i = 0; i < nums.length; i++) {\n" \
          "    if (m.has(target - nums[i])) return [m.get(target - nums[i]), i];\n    m.set(nums[i], i);\n  }\n};\n"


def _run(lang_key, code, args, sig=SIG):
    lang = langs.get(lang_key)
    h = harness.builtin(lang, sig, lang.user_name(code))
    b = runner.prepare(lang, code, h)
    assert b.ok, b.output
    return runner.run(b.run_cmd, b.dir, harness.stdin_line(args), timeout=10)


@pytest.mark.skipif(not langs.runnable(langs.get("python")), reason="no python")
@pytest.mark.parametrize("code", [PY_GOOD, PY_LEET])
def test_python_driver(code):
    r = _run("python", code, [[2, 7, 11, 15], 9])
    assert r.ok, r.stderr
    out, err = harness.parse_result(r.result_line)
    assert err is None and out == [0, 1]


@pytest.mark.skipif(not langs.runnable(langs.get("python")), reason="no python")
def test_python_driver_reports_your_line():
    code = "def two_sum(nums, target):\n    x = nums[10]\n    return x\n"
    r = _run("python", code, [[1, 2], 3])
    assert not r.ok
    assert "line 2" in r.stderr and "IndexError" in r.stderr


@pytest.mark.skipif(not langs.runnable(langs.get("python")), reason="no python")
def test_python_linked_list():
    sig = {"name": "reverse_list", "params": [["head", "ListNode"]], "returns": "ListNode"}
    code = "def reverseList(head):\n    prev = None\n    while head:\n        head.next, prev, head = prev, head, head.next\n    return prev\n"
    r = _run("python", code, [[1, 2, 3]], sig)
    assert harness.parse_result(r.result_line)[0] == [3, 2, 1], r.stderr


@pytest.mark.skipif(not langs.runnable(langs.get("javascript")), reason="no node")
def test_js_driver():
    r = _run("javascript", JS_GOOD, [[3, 2, 4], 6])
    assert r.ok, r.stderr
    assert harness.parse_result(r.result_line)[0] == [1, 2]


@pytest.mark.skipif(not langs.runnable(langs.get("javascript")), reason="no node")
def test_js_tree():
    sig = {"name": "maxDepth", "params": [["root", "TreeNode"]], "returns": "int"}
    code = "var maxDepth = function(root) {\n  if (!root) return 0;\n  return 1 + Math.max(maxDepth(root.left), maxDepth(root.right));\n};\n"
    r = _run("javascript", code, [[3, 9, 20, None, None, 15, 7]], sig)
    assert harness.parse_result(r.result_line)[0] == 3, r.stderr


def test_timeout_kills():
    lang = langs.get("python")
    if not langs.runnable(lang):
        pytest.skip("no python")
    code = "def two_sum(nums, target):\n    while True:\n        pass\n"
    t = time.time()
    r = _run("python", code, [[1], 1])
    assert time.time() - t < 15
    assert not r.ok


def test_syntax_check_python():
    errs = runner.syntax_check(langs.get("python"), "def f(:\n  pass\n", "solution.py")
    assert errs and errs[0]["line"] == 1


@pytest.mark.skipif(not langs.runnable(langs.get("javascript")), reason="no node")
def test_syntax_check_js():
    errs = runner.syntax_check(langs.get("javascript"), "function f( {\n}\n", "solution.js")
    assert errs and errs[0]["line"] >= 1


# ---------------------------------------------------------------------------- oracle + shrinking

ORACLE = '''
def reference(nums, target):
    seen = {}
    for i, x in enumerate(nums):
        if target - x in seen:
            return [seen[target - x], i]
        seen[x] = i
    return []

def brute(nums, target):
    for i in range(len(nums)):
        for j in range(i + 1, len(nums)):
            if nums[i] + nums[j] == target:
                return [i, j]
    return []

def gen(n, rnd):
    nums = [rnd.randint(-5, 5) for _ in range(max(2, n))]
    i, j = rnd.sample(range(len(nums)), 2)
    return [nums, nums[i] + nums[j]]

def check(args, out):
    nums, target = args
    return isinstance(out, list) and len(out) == 2 and out[0] != out[1] and nums[out[0]] + nums[out[1]] == target
'''


def test_oracle_verify_and_expected(tmp_path):
    o = oracle.Oracle(ORACLE, tmp_path)
    try:
        v = o.verify([[[2, 7, 11, 15], 9], [[3, 3], 6]], "exact", max_n=8, count=200)
        assert v["checked"] >= 150 and v["disagree"] == 0, v
        exp = o.expected([[[2, 7, 11, 15], 9]])
        assert exp[0]["output"] == [0, 1]
        assert o.check([{"args": [[3, 3], 6], "output": [1, 0]}], "exact") == [True]
        big = o.generate([1000])
        assert len(big[0][0]) == 1000
    finally:
        o.close()


def test_oracle_catches_a_wrong_reference(tmp_path):
    bad = ORACLE.replace("if target - x in seen:", "if target - x in seen and x > 0:").replace("def check", "def _no")
    o = oracle.Oracle(bad, tmp_path)
    try:
        assert o.verify([], "exact", max_n=8, count=200)["disagree"] > 0
    finally:
        o.close()


def test_shrink_finds_small_input():
    # "fails" whenever the list holds a 7 and a 3
    fails = lambda a: 7 in a[0] and 3 in a[0]
    small, runs = oracle.shrink([[1, 9, 7, 4, 4, 2, 3, 8, 5, 6], 10], fails)
    assert sorted(small[0]) == [3, 7] and runs <= 80


def test_fit_complexity():
    quad = [(1000, 60), (4000, 260), (16000, 3260)]
    assert "n²" in oracle.fit_complexity(quad, 50)["label"]
    lin = [(10000, 80), (40000, 170), (160000, 530)]
    assert "O(n)" in oracle.fit_complexity(lin, 50)["label"]
