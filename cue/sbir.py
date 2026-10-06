"""SBIR mode helpers: the home screen, and the contacts files the prep / post-call steps share.

    profiles/sbir/contacts.md         generated from your tracker spreadsheets (cue-cli sbir-sync)
    profiles/sbir/contacts_manual.md  yours: never overwritten; call promises are appended here
    profiles/sbir/prep.md             the next call's prep sheet (cue-cli prep-sbir "<name or org>")
"""
from __future__ import annotations

import re
from datetime import date

from .config import ROOT

SBIR = ROOT / "profiles" / "sbir"
CONTACTS = SBIR / "contacts.md"
MANUAL = SBIR / "contacts_manual.md"
PREP = SBIR / "prep.md"
RULES = SBIR / "rules.md"


def section(md: str, hint: str) -> list[str]:
    """Bullet lines of the first '## ...hint...' section."""
    m = re.search(rf"^##+ [^\n]*{hint}[^\n]*$(.*?)(?=^#{{1,2}} |\Z)", md, flags=re.M | re.S | re.I)
    if not m:
        return []
    return [re.sub(r"^\s*[-*]\s+|\*\*", "", l).strip() for l in m.group(1).splitlines()
            if re.match(r"^\s*[-*]\s+\S", l)]


def home_items() -> tuple[str, list[tuple[str, list[str]]]]:
    """What the panel shows in SBIR mode before anyone asks anything."""
    if not PREP.exists():
        return "SBIR call", [("", ["Answers come from your offer, proof, FAQ and rules files.",
                                   "Before a call: tray → Prep SBIR call… for a one-page prep on that person.",
                                   "Press Tab if it misses a question."])]
    md = PREP.read_text(encoding="utf-8")
    first = next((l for l in md.splitlines() if l.startswith("# ")), "# Call prep")
    header = first.lstrip("# ").strip()
    groups = []
    for title, hint in (("Goal for this call", "want out of"), ("They already said / you promised", "already"),
                        ("Don't say", "not to say")):
        items = section(md, hint)[:3]
        if items:
            groups.append((title, items))
    return header, groups or [("", ["Prep loaded. Press Tab when you're asked something."])]


def append_promises(contact: str, lines: list[str], when: date | None = None) -> None:
    """Add dated promises/notes under '### <contact>' in contacts_manual.md (created if missing)."""
    lines = [l.strip().lstrip("-* ").strip() for l in lines if l.strip().lstrip("-* ").strip()]
    if not lines:
        return
    stamp = f"{(when or date.today()):%Y-%m-%d}"
    new = [f"- {stamp}: {l}" for l in lines]
    text = MANUAL.read_text(encoding="utf-8") if MANUAL.exists() else \
        "# Contacts, hand-kept\n\nSync never overwrites this file. Post-call promises are added here.\n"
    head = f"### {contact.strip()}"
    out = text.splitlines()
    idx = next((i for i, l in enumerate(out) if l.strip().lower() == head.lower()), None)
    if idx is None:
        out += ["", head, *new]
    else:
        end = idx + 1
        while end < len(out) and not out[end].startswith("#"):
            end += 1
        while end > idx + 1 and not out[end - 1].strip():
            end -= 1
        out[end:end] = new
    MANUAL.parent.mkdir(parents=True, exist_ok=True)
    MANUAL.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
