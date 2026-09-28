"""Pulls the "Completed last week" bullets, grouped by workstream, out of profiles/work/last_week.md."""
from __future__ import annotations

import json
import re
from datetime import date

from .config import resolve

BRIEF = "profiles/work/last_week.md"


def _clean(line: str) -> str:
    line = re.sub(r"^\s*[-*•]\s*", "", line)
    line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
    return line.strip()


def brief_is_current() -> bool:
    """The worklog pipeline finished a run today, so the brief covers the right 'last week'
    and the latest work (it's only rewritten when something changed)."""
    try:
        state = json.loads(resolve("data/worklog/state.json").read_text(encoding="utf-8"))
        return resolve(BRIEF).exists() and state.get("last_run", "")[:10] == date.today().isoformat()
    except (OSError, ValueError):
        return False


def work_recap(max_items: int = 8) -> tuple[str, list[tuple[str, list[str]]]]:
    """Returns (header, [(workstream, bullets), ...])."""
    path = resolve(BRIEF)
    if not path.exists():
        return "Last week", [("", ["No brief yet — it's being generated (or tray → Refresh work log now)."])]
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^## Completed last week\s*\(?([^)\n]*)\)?\s*$(.*?)(?=^## |\Z)", text, flags=re.M | re.S)
    if not m:
        return "Last week", [("", ["Couldn't find the 'Completed last week' section in last_week.md."])]
    span, body = m.group(1).strip(), m.group(2)
    groups: list[tuple[str, list[str]]] = []
    current = ("", [])
    for line in body.splitlines():
        if line.startswith("### "):
            if current[1]:
                groups.append(current)
            current = (line[4:].strip(), [])
        elif re.match(r"^\s*[-*•]\s+\S", line):
            current[1].append(_clean(line))
    if current[1]:
        groups.append(current)

    # cap the total number of bullets, keeping at least one per workstream
    out, left = [], max_items
    for name, items in groups:
        take = items[: max(1, min(len(items), left, 2))]   # glanceable: 2 per workstream max
        left -= len(take)
        out.append((name, take))
    header = f"Last week · {_pretty_span(span)}" if span else "Last week"
    if not brief_is_current():
        header += "  (updating…)"
    return header, out


def last_call_items(name: str, max_items: int = 4) -> list[tuple[str, list[str]]]:
    """From the most recent finished call's notes: what you said you'd do, and what you were
    asked. Shown on the Work home screen before the next call."""
    meetings = sorted(resolve("meetings").glob("*.md")) if resolve("meetings").exists() else []
    for path in reversed(meetings):
        text = path.read_text(encoding="utf-8")
        if not text or "(live notes)" in text.splitlines()[0]:
            continue  # unfinished call
        try:
            when = date.fromisoformat(path.stem[:10])
        except ValueError:
            continue
        if when >= date.today():
            continue  # today's call is the one you're on
        owed, asked = [], []
        section = ""
        for line in text.splitlines():
            if line.startswith("## "):
                section = line.lower()
            elif re.match(r"^\s*[-*]\s", line):
                item = _clean(re.sub(r"^\s*[-*]\s*\[[ x]\]\s*", "- ", line))
                if "action" in section and re.match(rf"({name}|me)\b", item, re.I):
                    owed.append(re.sub(rf"^({name}|me)\s*:\s*", "", item, flags=re.I))
                elif "asked of me" in section:
                    asked.append(item)
        label = f"{when:%b} {when.day}"
        groups = []
        if owed:
            groups.append((f"From your last call ({label}) — you said you'd", owed[:max_items]))
        if asked:
            groups.append((f"They asked you ({label})", asked[:max_items]))
        return groups
    return []


def _pretty_span(span: str) -> str:
    """'2026-09-14 – 2026-09-20' -> 'Sep 14 – 20'."""
    ds = re.findall(r"\d{4}-\d{2}-\d{2}", span)
    if len(ds) != 2:
        return span
    a, b = (date.fromisoformat(d) for d in ds)
    return f"{a:%b} {a.day} – {b.day}" if a.month == b.month else f"{a:%b} {a.day} – {b:%b} {b.day}"
