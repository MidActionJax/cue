"""Build profiles/interview/prep.md for an upcoming interview.

    prep.cmd            (or tray -> Build interview prep)

Reads everything in profiles/interview/ (resume, job.md with the job description, notes) plus
your auto-generated work history and learned voice, and writes likely questions with talking
points in your voice. Interview mode loads it as context and Practice mode quizzes you on it.
"""
from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # running from source

from cue import config  # noqa: E402
from cue.context import load_context  # noqa: E402
from worklog.summarize import claude_p  # noqa: E402

ROOT = config.ROOT  # your data home
OUT = ROOT / "profiles" / "interview" / "prep.md"
log = logging.getLogger("interview_prep")

PROMPT = """Prepare {name} for a job interview. {name} knows their work but freezes when speaking, so everything must be sayable out loud, in {name}'s own voice (see VOICE if present), with real specifics from their history. Never invent experience; if the job asks for something {name} hasn't done, give an honest bridge from what they have done.

JOB / INTERVIEW MATERIAL (resume, job description, notes — may be empty):
{job}

{name}'S WORK HISTORY AND VOICE:
{history}

Write:
# Interview prep — {role} (generated {today})
## My 30-second intro
(3–4 sentences, first person, spoken style)
## Likely questions
(18–22 questions this interviewer is likely to ask: role-specific technical, behavioral, "tell me about a time", motivation. For each:)
### <question?>
- <talking point with a concrete specific>
- <talking point>
- <result / why it matters>
## Stories to have ready
(4–6 STAR stories from real events, 2 lines each)
## Questions to ask them
(5 smart questions tailored to the role)
## Gaps to handle
(anything the role wants that {name} lacks, and the honest bridge for each)"""


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname).1s %(message)s", datefmt="%H:%M:%S")
    cfg = config.load()
    inputs = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "profiles" / "interview").glob("*")
                    if p.is_file() and p.name.lower() not in ("readme.md", "prep.md"))
    job, files = load_context(inputs)
    history, _ = load_context(["profiles/voice.md", "profiles/me.md", "profiles/work/all_time.md"])
    role = "this role"
    for line in job.splitlines():
        if line.lower().startswith(("title:", "role:", "position:")):
            role = line.split(":", 1)[1].strip() or role
            break
    log.info("building interview prep from %s", [f for f in files if not f.endswith("prep.md")] or "work history only")
    text = claude_p(PROMPT.format(name=cfg.user.name, job=job or "(none provided — prepare for a general software/research engineering interview)",
                                  history=history, role=role, today=date.today()),
                    f"You are an interview coach for {cfg.user.name}. Output only the markdown.", cfg.worklog.model,
                    timeout=900)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text.strip() + "\n", encoding="utf-8")
    log.info("wrote %s", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
