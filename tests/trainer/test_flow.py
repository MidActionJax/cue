"""Trainer flow without any model: a fake Jobs answers each role with canned JSON, so the brain's
wiring (Listener patches -> design + tests -> runs -> review marks -> debug) and the views are
checked end to end, offline.

    python -m pytest tests/trainer
"""
import json
import threading
import time
import zipfile

import pytest

from cue.trainer import langs, views, vscode
from cue.trainer.brain import Brain
from cue.trainer.state import ProblemState

from .test_core import ORACLE

LISTEN = {"ops": [
    {"op": "problem", "title": "Two Sum", "summary": "indices of two numbers adding to target", "style": "function",
     "difficulty": "easy", "pattern": ["arrays & hashing"], "n_max": 10000, "defined": True},
    {"op": "signature", "name": "two_sum", "params": [["nums", "int[]"], ["target", "int"]], "returns": "int[]"},
    {"op": "add_req", "text": "Find two different indices whose values sum to target", "kind": "functional",
     "status": "stated", "source": "find two numbers that add up"},
    {"op": "add_example", "input": [[2, 7, 11, 15], 9], "output": [0, 1], "source": "2,7,11,15 and 9 gives 0 and 1"},
    {"op": "add_q", "text": "Can the array be empty?", "priority": 1},
]}
DESIGN = {
    "plan": [{"step": "Map value to index", "detail": "hash map", "branch": ""}],
    "approaches": [
        {"id": "A1", "name": "Brute force: every pair", "time": "O(n²)", "space": "O(1)", "idea": "nested loops",
         "pseudocode": "for i\n  for j>i", "why_not": "5e7 checks", "ops": 5e7, "recommended": False},
        {"id": "A2", "name": "One-pass hash map", "time": "O(n)", "space": "O(n)", "idea": "look up the complement",
         "pseudocode": "for i, x: if t-x in seen", "why_not": "", "ops": 1e4, "recommended": True}],
    "walkthrough": {"steps": ["Go through the numbers once.", "For each, check if its partner was seen."],
                    "example": ["2 needs 7."], "edge": [], "why": "one pass"},
    "blocks": [
        {"id": "B1", "name": "Setup", "purpose": "", "pseudo": "seen = {}", "code": "def two_sum(nums, target):\n    seen = {}\n"},
        {"id": "B2", "name": "Core loop", "purpose": "", "pseudo": "loop",
         "code": "    for i, x in enumerate(nums):\n        if target - x in seen:\n            return [seen[target - x], i]\n        seen[x] = i\n"}],
}
TESTS = {"module": ORACLE, "compare": "exact", "brute_max_n": 8, "n_max": 10000,
         "edge_cases": [{"name": "pair at the end", "input": [[1, 5, 3, 4], 7]}, {"name": "duplicates", "input": [[3, 3], 6]}]}
REVIEW = {"detected_approach": "A1", "blocks": {"B1": "done", "B2": "partial"}, "requirements": {"R1": "partial"},
          "lines": [{"line": 3, "mark": "wrong", "why": "j starts at i"}, {"line": 4, "mark": "good", "why": "sum check"}],
          "summary": "fix the inner loop start"}
DEBUG = {"summary": "j starts at i", "lines": [{"line": 3, "why": "self-pairing", "evidence": "[3,3]"}],
         "fix": [{"line": 3, "before": "range(i,", "after": "range(i + 1,"}], "say_it": "start j at i+1"}
BUGGY = ("def two_sum(nums, target):\n    for i in range(len(nums)):\n        for j in range(i, len(nums)):\n"
         "            if nums[i] + nums[j] == target:\n                return [i, j]\n")


class FakeJobs:
    available = True
    calls = 0

    def __init__(self, answers):
        self.answers, self.on_result, self.prompts = answers, None, {}

    def busy(self, role):
        return False

    def submit(self, role, prompt, ctx=None):
        self.prompts[role] = prompt
        threading.Thread(target=lambda: self.on_result(role, self.answers[role], None, ctx), daemon=True).start()

    def close(self):
        pass


@pytest.fixture
def brain(tmp_path):
    if not langs.runnable(langs.get("python")):
        pytest.skip("no python")
    state = ProblemState("python", str(tmp_path / "solution.py"))
    lock, changes = threading.RLock(), []

    def post(fn):
        with lock:
            fn()
    jobs = FakeJobs({"listener": LISTEN, "designer": DESIGN, "tests": TESTS, "reviewer": REVIEW, "debugger": DEBUG})
    b = Brain(state, {"verify_count": 150, "shrink_budget": 30}, None, "Jax", tmp_path, post,
              lambda keys: changes.append(keys), jobs=jobs)
    jobs.on_result = b._job_done
    yield b
    b.close()


def _wait(cond, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.2)
    return False


def test_flow_end_to_end(brain):
    s = brain.state
    brain.hear("INTERVIEWER", "find two numbers that add up to target, 2 7 11 15 and 9 gives 0 and 1")
    assert brain.flush()
    assert _wait(lambda: s.approaches and s.tests and not brain._testing)
    assert s.estimate.minutes == 15            # the listener forgot it: filled from the difficulty
    assert s.oracle["verified"]
    assert [t.origin for t in s.tests][:3] == ["interviewer", "edge", "edge"]
    assert s.tests[0].expected == [0, 1] and s.tests[0].verified

    good = brain.reference_code()
    run = brain.run_now(good)
    assert run["passed"] == run["total"] and run["total"] >= 6, run

    s.code["text"] = BUGGY
    run = brain.run_now(BUGGY, stress=False)
    assert run["passed"] < run["total"]
    s.runs.append(run)
    brain._apply_review(REVIEW, run["code_hash"])
    assert s.code["lines"][3]["mark"] == "red"            # a failing test backs it up
    assert 4 not in s.code["lines"]                        # "good" isn't shown while tests fail
    assert s.blocks[0].status == "done" and s.req("R1").met == "partial"

    brain.debug(BUGGY)
    assert _wait(lambda: s.debug.get("summary") == "j starts at i")
    assert s.debug["input"] == [[3, 3], 6] or len(json.dumps(s.debug["input"])) <= len(json.dumps([[1, 5, 3, 4], 7]))
    assert "range(i, len(nums))" in s.debug.get("trace", "")

    assert s.walkthrough["steps"][0] == "Go through the numbers once."
    for html in (views.spec_html(s, 16), views.walkthrough_html(s, 14), views.questions_html(s, 14), views.design_html(s, 14),
                 views.blocks_html(s, 14, "python", True), views.blocks_html(s, 14, "python", False),
                 views.mycode_html(s, 14, "python"), views.tests_html(s, 14), views.debug_html(s, 14, "python", True)):
        assert isinstance(html, str) and html


def test_marks_follow_their_line():
    code = "a = 1\nb = 2\nreturn b\n"
    marks = {"2": {"mark": "red", "why": "x", "text": "b = 2"}}
    assert 2 in views.relocate(marks, code)
    moved = "# new line\na = 1\nb = 2\nreturn b\n"
    assert 3 in views.relocate(marks, moved)
    assert views.relocate(marks, "a = 1\nreturn b\n") == {}


def test_vsix_is_a_valid_package(tmp_path):
    out = vscode.build_vsix(tmp_path / "x.vsix")
    names = zipfile.ZipFile(out).namelist()
    assert {"[Content_Types].xml", "extension.vsixmanifest", "extension/package.json", "extension/extension.js"} <= set(names)


def test_every_language_has_a_starter_and_lexer():
    from cue.trainer import highlight
    for lang in langs.LANGUAGES:
        assert langs.starter(lang).strip()
        assert highlight.lines_html("x = 1\n", lang.lexer)


def test_starter_file_is_not_your_code():
    from cue.trainer.session import has_own_code
    cpp = langs.get("cpp")
    starter = langs.starter(cpp)
    assert not has_own_code(starter, starter, cpp.comment)
    assert not has_own_code(starter + "\n#include <vector>\n// thinking...\n", starter, cpp.comment)
    assert has_own_code(starter + "int findMax(vector<int>& a) {\n", starter, cpp.comment)
    java = langs.get("java")
    assert not has_own_code(langs.starter(java), langs.starter(java), java.comment)
    py = langs.get("python")
    assert has_own_code("def f(x):\n    return x\n", "", py.comment)


def test_walkthrough_shows_in_plain_words():
    s = ProblemState()
    s.walkthrough = {"steps": ["Go through the numbers once.", "Remember each one."], "example": ["At 2 I need 7."],
                     "edge": ["Empty list: return nothing."], "why": "One pass."}
    html = views.walkthrough_html(s, 14)
    assert "Go through the numbers once." in html and "Why it works" in html
    assert "PLAN IN PLAIN WORDS: 1. Go through" in s.brief()
