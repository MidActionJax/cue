"""Prompts for turning session digests into work-history docs."""

SYSTEM = (
    "You turn raw logs of {name}'s AI-assisted work sessions into accurate, specific work-history notes. "
    "You write in first person as {name} ('I ...'), because {name} directed the work even when an AI assistant "
    "executed it. Never invent facts; if something is unclear, leave it out. Output markdown only, no preamble."
)

BACKGROUND = """Background on {name} (from their own notes — trust this over guesses):
{background}

Primary JOB projects: {work_projects}. Anything else (personal apps, side projects, Cowork errands) is a SIDE project — still record it, but keep it brief and after the job work.

WORKSTREAMS — job work is organized into these, and each description says what belongs where. Use these names exactly as section headings for job work:
{workstreams}"""

DAILY = """{background_block}

Below is a condensed log of everything {name} did on {day} in Claude Code / Claude Cowork sessions, plus git commits.
"ME:" = {name}'s own messages (often containing pasted emails, Slack messages, or command output — these are gold: they say what {name} reported to teammates). "CLAUDE:" = the AI assistant. "tools:" = actions taken. "Call notes" sections are notes from live calls/meetings {name} was on — record what was discussed, decided, and asked of {name}. Git commits marked "(me)" are {name}'s; others are teammates'.

Write this day's work log. Be concrete: systems, scripts, files, jobs, queues, people, numbers, error messages, and outcomes. Skip chit-chat and tool noise. Say clearly when something is still unresolved.

Format:
## {day}
### <Workstream or side project>   (job work under the WORKSTREAMS names above; one section each; job first)
- **Did:** ...
- **Result:** ...
- **Decisions:** ... (only if any — include the why)
- **Open / blockers:** ... (only if any)
- **People:** ... (only if someone was involved: who, and what about)
### Automated jobs   (only if scheduled tasks ran; one line each)

At most ~450 words. If nothing meaningful happened, output the heading and "- No substantive work logged."

LOG:
{digest}"""

WEEKLY = """{background_block}

Here are {name}'s daily work notes for ISO week {week} ({start} – {end}). Condense them into a weekly summary.

Format:
## Week {week} ({start} – {end})
**Headline:** one or two sentences — the story of the week.
### <Workstream or side project>   (WORKSTREAMS names for job work)
- **Shipped / fixed:** ...
- **In progress:** ...
- **Problems hit:** ... (with how they were handled)
- **Decisions:** ...
- **Key specifics:** numbers, names, systems worth remembering

At most ~500 words; job projects first; side projects briefly.

DAILY NOTES:
{notes}"""

LAST_WEEK = """{background_block}

Today is {today} ({weekday}). "Last week" means Monday {prev_start} – Sunday {prev_end}. "This week so far" means {this_start} – {today}. Below are {name}'s daily work notes from {prev_start} through today.

Group job work under the WORKSTREAMS above, spelled exactly as listed. If finished job work clearly fits none of them, create a new short name (2–3 words, same style). Side projects never go in job sections.

Write the brief {name} leans on in team calls (typically Monday morning, reporting on last week). A live assistant reads it to feed {name} talking points in real time, so be specific and phrase things the way {name} actually talks (see the VOICE notes in the background if present).

Format:
# Work brief — week of {prev_start}
## Completed last week ({prev_start} – {prev_end})
### <Workstream>
- <finished result, at most 14 words, outcome first>
(Every job workstream with finished work last week gets a ### heading with 1–2 bullets — never more than 2, pick the biggest results; each bullet at most 16 words. Only finished results. Don't overstate: "worked around" is not "fixed".)
## This week so far ({this_start} – {today})
(same ### <Workstream> structure, 1 bullet each; omit this section entirely if nothing happened yet)
## Standup answer (3 bullets: last week, this week, blockers — spoken style, first person)
## Status by workstream
### <Workstream>
- **Done:** ... (concrete results)
- **In progress:** ...
- **Blockers / waiting on:** ... (who/what)
- **Details to have ready:** numbers, job IDs, file/script names, error messages, dates
## Side projects (one line each, only if touched)
## Decisions I made and why
## People — who I'm working with on what
## Likely questions on a call → how I'd answer
(8–12 Q/A pairs; each answer 1–2 sentences in natural spoken first person, with specifics)
## Next up

DAILY NOTES:
{notes}"""

ALL_TIME = """{background_block}

Today is {today}. Below are {name}'s weekly work summaries (oldest first), followed by this week's daily notes. Build a complete, durable WORK HISTORY knowledge base. It's used (a) by a live call assistant to answer questions about any past work, and (b) as interview material beyond the resume.

Format:
# {name} — Work history (auto-generated {today})
## Snapshot
Who I am, what I work on, my role — 3–4 sentences.
## Projects
### <Project> (job projects first; for job projects, organize accomplishments and hard problems by workstream from WORKSTREAMS)
- **What it is:** a plain-English explanation I could say on a call in 2 sentences
- **My role:** ...
- **Stack / systems:** ...
- **Timeline:** key milestones with dates
- **Accomplishments:** concrete, with numbers where possible
- **Hard problems I solved:** problem → what I did → result
## Skills demonstrated (each with the evidence)
## Interview stories (5–8, STAR: situation, task, action, result — real events only)
## People I work with (name — role/relationship — what we work on)
## Timeline (one line per week, most recent first)

Be thorough but tight; prefer specifics over adjectives.

WEEKLY SUMMARIES:
{weeks}

THIS WEEK'S DAILY NOTES:
{current}"""

GLOSSARY = """From the notes below, list up to 80 words and phrases likely to be SPOKEN on calls about this work that a speech recognizer could mis-hear: people's names, project and system names, acronyms, tools, technical jargon. Output ONE comma-separated line, nothing else.

{notes}"""

VERIFY = """{background_block}

Below is a DRAFT work brief and the daily work notes it was written from. {name} will say these things out loud to their team, so check the draft line by line against the notes and return a corrected brief.

Fix:
- Anything the notes don't support: remove it. Anything overstated: "fixed" when it was worked around or is still in progress, "done" when it's pending, "shipped" when it's on a branch. Say what actually happened.
- Numbers, job IDs, dates and names must match the notes exactly and stay attached to the job, run or event they belong to.
- Filing: every job item sits under the WORKSTREAM whose description fits it. Merge sections that are really the same workstream, use the exact workstream names, and keep side projects out of job sections.
- "Completed last week" contains only work finished between {prev_start} and {prev_end}.
- Remove duplicates between sections.
- Keep "Completed last week" and "This week so far" glanceable: at most 2 bullets per workstream, each at most 16 words. Put nuance in a few words ("worked around", "partly", "pending review"), and move detail to "Status by workstream".
Keep everything else (structure, headings, voice) the same. Output only the corrected brief, no commentary.

DRAFT:
{draft}

DAILY NOTES:
{notes}"""
