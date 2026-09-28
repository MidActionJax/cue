"""Instant (no-LLM) heuristics for "was that a question aimed at me?"."""
from __future__ import annotations

import re

_INTERROGATIVE = re.compile(
    r"^(?:(?:so|and|but|okay|ok|alright|um|uh|well|now|also|then|right)[, ]+)*"
    r"(who|what|what's|whats|when|where|why|how|how's|hows|which|whose|"
    r"is|are|was|were|do|does|did|can|could|would|will|should|shall|have|has|had|"
    r"may|might|any|anything|isn't|aren't|don't|doesn't|didn't|won't|wouldn't)\b",
    re.I,
)

_REQUESTS = re.compile(
    r"\b(tell (me|us)|walk (me|us) through|talk (me |us )?(about|through)|describe|explain|"
    r"give (me|us)|share|thoughts on|what about|how about|any updates?|an update|update on|"
    r"go ahead|your turn|over to you|what do you think|your take|you want to|can you|could you|"
    r"would you|have you|do you|did you|are you|were you|where are (we|you)|how('s| is) it going|"
    r"elaborate|expand on|example of|curious (about|how|what|why)|wondering)\b",
    re.I,
)

_TAG_QUESTION = re.compile(r"\b(right|correct|yeah|no|huh|okay|ok)\s*\?*\s*$", re.I)


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.?!])\s+", text.strip())
    return [p for p in parts if p]


def looks_like_question(text: str) -> bool:
    sents = _sentences(text)
    if not sents:
        return False
    tail = sents[-2:]
    for s in tail:
        if s.rstrip().endswith("?"):
            return True
        if _INTERROGATIVE.match(s) and len(s.split()) >= 3:
            return True
        if _REQUESTS.search(s):
            return True
    return bool(_TAG_QUESTION.search(sents[-1]) and "?" in sents[-1])


def mentions_name(text: str, aliases: list[str]) -> bool:
    if not aliases:
        return False
    pat = r"\b(" + "|".join(re.escape(a) for a in aliases) + r")\b"
    return re.search(pat, text, re.I) is not None


def addressed_to_other(text: str, others: list[str], aliases: list[str]) -> bool:
    """The latest sentence names a teammate (and not you): "Sam, do you know what that peak is?"."""
    sents = _sentences(text)
    last = " ".join(sents[-2:]) if sents else text
    return mentions_name(last, others) and not mentions_name(last, aliases)
