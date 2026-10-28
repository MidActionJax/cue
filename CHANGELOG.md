# Changelog

## 0.3.0-beta (2026-10-28): smarter answers

### Answers
- Measured on 27 real moments from past calls (a blind judge scoring each answer against the
  transcript and what was actually said next): **5.37 → 6.85 out of 8**. Right project 81% → 100%,
  nothing invented 78% → 96%, sounds like you 1.22 → 1.70 of 2.
- Live answers use Claude Sonnet by default (`llm.claude_cli_model`); it beat Haiku on the same
  test (6.85 vs 6.59) for ~0.4 s more to the first word.
- New answer instructions built from a review of every answer Cue showed on real calls: work out
  the question and its topic from the conversation thread (not keyword matches), commit to one
  project and use only its facts, answer first in plain spoken words, keep internals and numbers
  for when they're asked for, never claim work that isn't in the context, never invent personal
  facts, and never narrate ("Jaxon can say…").
- Each answer shows which project it thinks the question is about, so a wrong one is obvious.
- Every answer gets a fresh Claude session (a warm spare is always ready): earlier questions and
  answers no longer bleed into later ones, and requests stay small.
- What Cue learns on calls is kept per mode (`profiles/<mode>/learned.md`): team calls no longer
  see SBIR pricing or job-search details, and the other way round. An older shared
  `profiles/learned.md` becomes the team-call file on first start (backup kept); old configs are mapped.

- Team-call context leaves out the "side projects" sections of the brief and history, and the
  daily job keeps job hunting and side businesses out of the work history's snapshot, people and
  suggested answers.
- Only team calls feed the work brief (interviews no longer do).

### Speakers
- The accurate after-call pass takes speaker names from the live transcript (lined up by time)
  instead of re-guessing them: on real calls the re-guess named strangers after coworkers and
  merged several coworkers into one. Voice grouping only fills gaps the live transcript missed.
- Saved voices are kept per mode: coworkers are only matched on team calls, so an SBIR contact or
  an interviewer no longer gets a coworker's name.
- `cue-cli notes rewrite <call>` writes a call's final notes again from its transcript (the old
  version is kept in `data/notes_backup/`).

### When Claude is unavailable
- The local model gets a short instruction set with a worked example, and its answers are checked
  before anything is shown; a failed answer is dropped instead of displayed.
- After a usage limit, Cue goes back to Claude at the reset time the limit message gives.
- Live notes during a call use the local model when it's available, which saves most of Cue's
  Claude usage for answers (the final notes still come from Claude).

## 0.2.0-beta (2026-10-28)

### New
- **SBIR mode** for consultants on SBIR/STTR calls with referral partners and founders:
  - answers from your intro, offer, proof and FAQ files
  - a `rules.md` of hard limits that's read last and never broken (no invented prices, clients or endorsements)
  - `cue-cli prep-sbir "<name>"` (tray → *Prep SBIR call…*) builds a one-page prep from your tracker
    spreadsheets, earlier calls with that contact, and any pasted email thread
  - after the call: a follow-up email in your style, a tracker line to paste, and promises filed
    under the contact. SBIR calls stay out of the work brief.
- **Answers from your documents**: add PDFs, Word (.docx) or text from the tray, by dragging them
  onto the panel, or with `cue-cli docs`. Choose all calls or one mode. Big documents are searched,
  so only the passages that match the question go along.
- **Accurate notes**: after the call, the other side's audio is re-transcribed with `large-v3` and
  full context, and speakers are matched across the whole call. The audio is deleted as soon as the
  notes exist (`notes.accurate_pass: false` turns it off).
- Any speaker name in the live transcript can be corrected for the whole call; tray → *Saved
  voices* → *Forget* for a voice print learned from the wrong person.
- `stt.me_match`: a separate "is this my voice" threshold for distant mics (webcams).
- Cue starts Ollama when it's installed but not running, so the local fallback is there.

### Fixed
- The other side's transcript could get stuck printing ". . ." for the rest of a call.
- Speaker labels flipped between two similar saved voices mid-sentence.
- The local fallback model lost the SBIR rules (its prompt was cut from the end).
- Opening Work mode while a brief refresh was already running restarted the Claude session every
  few seconds.
- The compiled app ignored Windows close requests; quitting mid-answer could hang.
- The live accurate model (`stt.accurate_speakers`) no longer stalls the transcript while it loads.
- Word tables, short text files, unicode file names and odd encodings in documents.

## 0.1.0-beta (2026-09-28)

First compiled build: a Windows installer (no Python needed), single-instance app with
`Cue.exe --quit`, `cue-cli.exe`, live answers, What?, scripts, the daily work brief, live and final
notes with a follow-up email, speaker fingerprints, voice learning, practice mode and interview prep.
