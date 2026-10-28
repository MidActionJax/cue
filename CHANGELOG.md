# Changelog

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
