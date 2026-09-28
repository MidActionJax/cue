"""The workstream registry (profiles/work/workstreams.md): stable group names for the brief."""
from __future__ import annotations

import re
from datetime import date

from cue.config import ROOT as _HOME

PATH = _HOME / "profiles" / "work" / "workstreams.md"
_JOB_HEADING = "## Job"


def text() -> str:
    return PATH.read_text(encoding="utf-8") if PATH.exists() else ""


def job_names() -> list[str]:
    names, in_job = [], False
    for line in text().splitlines():
        if line.startswith("## "):
            in_job = line.startswith(_JOB_HEADING)
        elif in_job and line.startswith("- "):
            names.append(re.split(r"\s+[—-]\s+", line[2:], maxsplit=1)[0].strip())
    return names


def completed_groups(brief: str) -> list[str]:
    """### headings inside the brief's '## Completed last week' section."""
    m = re.search(r"^## Completed last week.*?$(.*?)(?=^## )", brief, flags=re.M | re.S)
    return re.findall(r"^### (.+?)\s*$", m.group(1), flags=re.M) if m else []


def add_new(brief: str) -> list[str]:
    """Append job workstreams the brief invented to the registry. Returns what was added."""
    known = {n.lower() for n in job_names()}
    new = [g for g in completed_groups(brief) if g.lower() not in known]
    if not new or not PATH.exists():
        return []
    lines = text().splitlines()
    # insert after the last bullet of the Job section
    idx = last = next(i for i, l in enumerate(lines) if l.startswith(_JOB_HEADING))
    for i in range(idx + 1, len(lines)):
        if lines[i].startswith("## "):
            break
        if lines[i].startswith("- "):
            last = i
    insert_at = last + 1
    for g in new:
        lines.insert(insert_at, f"- {g} — (auto-added {date.today()})")
        insert_at += 1
    PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return new
