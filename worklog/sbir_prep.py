"""Build profiles/sbir/prep.md for the next SBIR call.

    cue-cli prep-sbir "Pat Lee"        (or tray -> Prep SBIR call…)

Finds the person or organization in contacts.md / contacts_manual.md, reads your offer, proof,
rules, FAQ, intro and voice, plus any pasted email thread in profiles/sbir/threads/<slug>.md,
and writes a one-page prep sheet. SBIR mode loads it as context and Practice quizzes you on it.
"""
from __future__ import annotations

import logging
import re
import sys
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # running from source

from cue import config  # noqa: E402
from cue.context import load_context  # noqa: E402
from worklog.summarize import claude_p  # noqa: E402

SBIR = config.ROOT / "profiles" / "sbir"
OUT = SBIR / "prep.md"
log = logging.getLogger("sbir_prep")

PROMPT = """Prepare {name} for a call today with: {who}

{name} runs a small independent SBIR/STTR consulting practice (technical sections of proposals, technical reviews of drafts). {name} knows the material but freezes on pricing and "why you", so everything must be sayable out loud, in {name}'s own voice (see VOICE and INTRO), and grounded only in the files below. Never invent clients, results, prices or endorsements. Follow RULES exactly; they override everything else.

WHAT WE KNOW ABOUT THIS CONTACT (from the tracker and notes; may be partial):
{contact}

EMAIL THREAD (if pasted):
{thread}

{name}'S FILES (intro, offer, proof, FAQ, voice):
{files}

RULES:
{rules}

Write exactly this markdown (short lines, no em dashes):
# Prep: {who} ({today})
## Who they are
(2-4 bullets: org, role, how {name} got connected, what kind of partner or client this is)
## The one thing I want out of this call
(1 bullet: the concrete ask, e.g. "added to their provider list", "intro to a counterpart in a neighboring state", "a client's draft to review")
## What they already said / what I already promised
(bullets from the contact history; "Nothing yet" if none)
## My 20-second intro for them
(3-4 sentences in {name}'s voice, tailored to this person; built from INTRO)
## Likely questions
(10-15 questions this person is likely to ask. For each:)
### <question?>
- <talking point>
- <talking point>
## Questions I should ask them
(3 questions, e.g. "When a company needs technical help, how do they usually find someone?")
## Things not to say to this contact
(bullets from RULES and the contact's own restrictions)"""


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def find_contact(who: str) -> tuple[str, str]:
    """(heading, block) of the best-matching '### ...' contact across both contacts files."""
    blocks = []
    for f in (SBIR / "contacts.md", SBIR / "contacts_manual.md"):
        if f.exists():
            for m in re.finditer(r"^### (.+?)$(.*?)(?=^#{1,3} |\Z)", f.read_text(encoding="utf-8"), re.M | re.S):
                blocks.append((m.group(1).strip(), m.group(0).strip()))
    q = who.lower()

    def score(b):
        text = b[1].lower()
        if q in text:
            return 2.0
        words = [w for w in re.findall(r"\w+", q) if len(w) > 2]
        hit = sum(w in text for w in words) / max(1, len(words))
        return hit + SequenceMatcher(None, q, b[0].lower()).ratio() * 0.5

    ranked = sorted(blocks, key=score, reverse=True)
    if not ranked or score(ranked[0]) < 0.5:
        return who, ""
    best = ranked[0]
    # the same org can appear in both files (tracker + your notes): include every block for it
    same = [b[1] for b in blocks if b[0].lower() == best[0].lower()]
    return best[0], "\n\n".join(same)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname).1s %(message)s", datefmt="%H:%M:%S")
    argv = sys.argv[1:] if argv is None else argv
    who = " ".join(argv).strip()
    if not who:
        print('usage: cue-cli prep-sbir "<person or organization>"')
        return 2
    cfg = config.load()
    heading, contact = find_contact(who)
    if contact:
        log.info("matched contact: %s", heading)
    else:
        log.warning("no contact matched %r in contacts.md / contacts_manual.md — prepping from the name only", who)
    thread = ""
    for key in {_slug(who), _slug(heading)}:
        p = SBIR / "threads" / f"{key}.md"
        if p.exists():
            thread = p.read_text(encoding="utf-8")[:20_000]
            break
    files, used = load_context([str(p) for p in (SBIR / "intro.md", SBIR / "offer.md", SBIR / "proof.md",
                                                 SBIR / "faq.md", config.ROOT / "profiles" / "voice.md")])
    rules, _ = load_context([str(SBIR / "rules.md")])
    log.info("prep from %s", used)
    text = claude_p(PROMPT.format(name=cfg.user.name, who=heading if contact else who, today=date.today(),
                                  contact=contact or "(not in the contacts files)", thread=thread or "(none)",
                                  files=files, rules=rules or "(none)"),
                    f"You are {cfg.user.name}'s call coach. Output only the markdown.", cfg.worklog.model,
                    timeout=600)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text.strip() + "\n", encoding="utf-8")
    log.info("wrote %s", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
