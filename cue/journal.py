"""Quick notes for work that never touched Claude (terminal sessions, emails, hallway talks).

    note.cmd "restarted the nightly cron after the maintenance window"

Entries land in profiles/work/journal.md and flow into the daily work log like everything else.
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from datetime import date, datetime

from .config import ROOT

JOURNAL = ROOT / "profiles" / "work" / "journal.md"
_LINE = re.compile(r"^- (\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}) — (.+)$")


def add(text: str) -> None:
    text = " ".join(text.split())
    if not text:
        return
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    new = not JOURNAL.exists()
    with open(JOURNAL, "a", encoding="utf-8") as f:
        if new:
            f.write("# Work journal (quick notes)\n\n")
        f.write(f"- {datetime.now():%Y-%m-%d %H:%M} — {text}\n")


def by_day(since: date, until: date) -> dict[date, list[str]]:
    out: dict[date, list[str]] = defaultdict(list)
    if JOURNAL.exists():
        for line in JOURNAL.read_text(encoding="utf-8").splitlines():
            m = _LINE.match(line.strip())
            if m:
                d = date.fromisoformat(m.group(1))
                if since <= d <= until:
                    out[d].append(f"{m.group(2)} {m.group(3)}")
    return out


if __name__ == "__main__":
    if len(sys.argv) > 1:
        add(" ".join(sys.argv[1:]))
        print("noted.")
    else:
        print(__doc__)
