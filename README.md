<p align="center">
  <img src="assets/banner.png" alt="Cue — know what to say, the moment you're asked" width="100%">
</p>

<p align="center">
  <img alt="Platform" src="https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-2b3a55?style=flat-square">
  <img alt="Python" src="https://img.shields.io/badge/python-3.11-2b3a55?style=flat-square">
  <img alt="Status" src="https://img.shields.io/badge/status-beta-4cd97b?style=flat-square">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-8fb4ff?style=flat-square">
  <a href="https://github.com/MidActionJax/cue/releases"><img alt="Download" src="https://img.shields.io/github/v/release/MidActionJax/cue?include_prereleases&label=download&style=flat-square&color=4cd97b"></a>
</p>

**Cue is a real-time copilot for calls.** It listens to your meeting, transcribes everyone
(heavy accents included), and the moment you're asked something it shows you **what to say**: an
opening line you can say out loud, plus the specifics that make you sound prepared. It knows those
specifics because it reads **what you actually worked on**, rebuilt every morning from your Claude
Code and Claude Cowork history, git commits and past call notes.

It's for anyone who knows their stuff but freezes when it's their turn to talk. And with
[**Trainer mode**](#trainer-mode-coding-interview-practice), for practicing coding-interview rounds.

<p align="center">
  <img src="assets/screenshots/3-answer.png" alt="Cue answering a question in under a second" width="820">
</p>

---

## What it does

|  |  |
|---|---|
| ⚡ **Answers in about a second** | Press `Tab` when you're asked something, or let it pop up when you're named or a question is clearly meant for you. The first words appear in ~0.5 s: an opener you can say as-is, plus 2–3 grounded points. Double-tap `Tab` for a full **script** to read word for word. |
| 🧠 **Knows your actual work** | A daily job reads your Claude Code / Cowork sessions, git history and call notes, and writes a brief: *what you completed last week, grouped by workstream*, status, blockers, and likely questions with answers. A second pass fact-checks every line against your notes. |
| 🗣 **Understands heavy accents** | Whisper `large-v3-turbo` on your GPU, primed with your team's names and jargon, with noise and hallucination filters. Couldn't catch what someone said? **What?** (`Shift+Tab`) turns it into plain English and tells you what they want from you. |
| 📝 **Takes the notes** | Key points, decisions, action items (yours first) and what you were asked, updated live. When the call ends you get polished notes and **a ready-to-send follow-up email**. Next time, your home screen shows what you promised. |
| 👥 **Knows who said what** | Voice fingerprints split the call into speakers. Name someone once and they're recognized on every future call. It also tells your voice apart from other people in the room. |
| 📈 **Learns how you talk** | After each call it compares what it suggested with what you actually said, and learns your phrasing, what you use and skip, and facts you stated. Facts are kept per kind of call, so a team call never brings up your consulting or job hunt. Suggestions sound more like you every week. |
| 📄 **Answers from your documents** | Add PDFs, Word files or text (tray → *Add documents…*, or drag them onto the panel): a solicitation, a paper, a project plan. When you're asked about something in them, the talking points use the document's real figures and wording. Big documents are searched, so only the passages that match the question go along. |
| 🤝 **SBIR mode** | For consulting calls with SBIR program managers and founders: answers from your offer, proof and FAQ, a per-contact prep sheet built from your tracker spreadsheets, and a rules file it never breaks (no invented prices, clients or endorsements). |
| 🎯 **Practice mode** | It asks likely questions out loud, you answer out loud, and it coaches you with what worked, what to add, and a tighter version in your own words. |
| 🧑‍💻 **Trainer mode** | For coding-round practice. It keeps the problem's requirements and the questions to ask up to date as the interviewer talks, starts a countdown, and lays out brute-force-to-optimal approaches. It writes tests whose expected outputs come from running two independent solutions against each other, and watches your file in VS Code. A debug key shrinks a failing test to the smallest failing input, traces it, and points at the broken line. |

<table>
  <tr>
    <td width="50%"><img src="assets/screenshots/2-home.png" alt="Work home: last week by workstream"><br><sub><b>Work mode home</b>: last week, grouped by workstream, plus what you owe from the last call.</sub></td>
    <td width="50%"><img src="assets/screenshots/4-explain.png" alt="What? decodes a hard-to-understand speaker"><br><sub><b>What?</b>: plain-English version of what was just said, and what they want from you.</sub></td>
  </tr>
  <tr>
    <td width="50%"><img src="assets/screenshots/5-practice.png" alt="Practice mode coaching"><br><sub><b>Practice</b>: rehearse out loud, get coached, and train the voice model.</sub></td>
    <td width="50%"><img src="assets/screenshots/7-notes.png" alt="Live notes pane"><br><sub><b>Live notes</b>: decisions and action items while the call happens.</sub></td>
  </tr>
</table>

The panel is frosted glass, floats above everything, **never takes keyboard focus** (clicking it
won't pull you out of Teams or Zoom), and is **invisible to screen sharing**.

---

## Trainer mode: coding-interview practice

Never done a coding round? Trainer mode is for practicing them: a mock interview with a friend,
a mock-interview site, or a recorded interview played on your PC. Cue listens to the interviewer,
works out exactly what's being asked, and lays out everything around your editor: the spec, the
questions to ask, the plan in plain words, the approaches from brute force to optimal, and tests
that check your code every time you save.

<p align="center">
  <img src="assets/screenshots/8-trainer.png" alt="Trainer mode: answers and the plan in plain words on the left, the spec board in the middle, your code and the design on the right" width="100%">
</p>

<table>
  <tr>
    <td width="50%" valign="top"><img src="assets/screenshots/9-trainer-spec.png" alt="Spec board"><br><sub><b>Spec board</b> (the main panel): requirements edited in place as the interviewer refines them, each with the quote it came from. Plus the signature, the examples, a countdown sized to the problem, and pacing nudges.</sub></td>
    <td width="50%" valign="top"><img src="assets/screenshots/10-trainer-howto.png" alt="How to solve it"><br><sub><b>How to solve it</b>: the best approach as verbal pseudocode, the steps the way you'd say them out loud, walked through the interviewer's example.</sub></td>
  </tr>
  <tr>
    <td width="50%" valign="top"><img src="assets/screenshots/11-trainer-design.png" alt="Design tracks"><br><sub><b>Design tracks</b>: brute force → optimal, with time/space, why the slow ones lose at the real input size, and a line to say out loud. It also shows which one your code is following.</sub></td>
    <td width="50%" valign="top"><img src="assets/screenshots/12-trainer-code.png" alt="Your code, live"><br><sub><b>My code</b>: your file, live. Red means proven broken (a failing test or a real error), yellow means suspect, green means right. Requirements tick off as you write them.</sub></td>
  </tr>
  <tr>
    <td width="50%" valign="top"><img src="assets/screenshots/13-trainer-tests.png" alt="Tests"><br><sub><b>Tests</b>: the interviewer's examples, edge cases, random inputs and a full-size speed test. Expected outputs are cross-checked by running two independent solutions, never guessed.</sub></td>
    <td width="50%" valign="top"><img src="assets/screenshots/14-trainer-debug.png" alt="Debug"><br><sub><b>Debug</b> (<code>Ctrl+Alt+D</code>): the smallest input that still fails, a line-by-line trace, the exact broken line, the fix, and what to say while you fix it.</sub></td>
  </tr>
</table>

**How a round goes**

1. **Trainer** on the start screen → pick the language and a solution file → **Start**. Cue
   creates the file and opens it in VS Code. ▶ in the language list means tests can run on this
   PC; any other language still gets everything except running the tests.
2. The interviewer explains the problem. The **spec board** fills in and **Ask next** lists the
   clarifying questions worth asking; they tick off as you ask them. Got the problem as text?
   **📋 Paste problem**.
3. Once the problem is clear the **countdown** starts. A few seconds later you get the plan in
   **How to solve it**, the approaches in **Design tracks**, and the reference solution in
   **Code → Blocks**. Cross-checked tests usually follow within a minute.
4. You code. Every save reruns the tests and marks your lines. The interviewer's questions get
   answered about *your* code in **Answers**.

| Key | |
|---|---|
| `Ctrl+Alt+A` | answer the interviewer's latest question (twice = word-for-word script) |
| `Ctrl+Alt+N` | what to say out loud right now, to show how you're thinking |
| `Ctrl+Alt+R` · `Ctrl+Alt+D` | run the tests · debug the first failure |
| `Ctrl+Alt+S` · `Ctrl+Alt+W` | what did they just say? · show/hide the trainer windows |

`Tab` stays free for indenting during a round.

```mermaid
flowchart LR
    I[Interviewer<br/>or pasted problem] --> L[Listener<br/>small edits to the spec]
    L --> S[Spec board +<br/>Ask next]
    L -- problem is clear --> D[Designer<br/>plan · approaches · blocks]
    L -- problem is clear --> T[Test author<br/>reference + brute force]
    T --> O{Do they agree on<br/>300 random inputs?}
    O -- yes --> X[Tests with<br/>trusted answers]
    F[Your file<br/>VS Code] --> R[Run tests on save]
    X --> R
    R --> M[Marks + Reviewer]
    R -- Ctrl+Alt+D --> G[Shrink → trace → exact line]
```

- **Every window reads one shared picture of the problem.** The answers can't drift to a
  different problem than the design, and they see the code you actually wrote.
- **Red means proven.** A line turns red only when a test fails or a real compiler or runtime
  error points at it. Model hunches are yellow.
- **The optional VS Code helper** (setup window → *Install VS Code helper*) shows Cue your edits
  before you save and draws the marks inside the editor.
- **Each session is saved** to `trainer/` in your data folder: the problem, requirements, questions
  and answers, approaches, results and your code. Practice never touches your work brief.
- **`cue-cli trainer-eval`** replays scripted mock interviews and scores the whole pipeline, including
  whether a known-good solution passes every generated test.

---

## How it works

```mermaid
flowchart LR
    subgraph Live call
      A[Call audio<br/>WASAPI loopback] --> V[VAD + voice<br/>fingerprints]
      M[Your mic] --> V
      V --> W[faster-whisper<br/>on your GPU]
      W --> T[Rolling transcript<br/>who said what]
      T --> Q{Asked something?<br/>name · smart · Tab}
      Q --> L[Claude, streaming<br/>local model fallback]
      L --> P[Glass panel]
      T --> N[Live notes]
    end
    subgraph Every morning
      H[Claude Code + Cowork<br/>sessions, git, call notes] --> D[Daily notes] --> B[Brief: last week<br/>by workstream<br/>+ fact-check]
    end
    B --> L
    N -- after the call --> F[Final notes + follow-up email]
    N -- after the call --> X[Learns how you talk]
    X --> L
```

| Job | Model | Why |
|---|---|---|
| Live answers, scripts, **What?** | Claude Sonnet, a fresh session for every answer (~1.3 s to first word) | best on real calls: picks the right project and invents nothing |
| …if Claude errors, stalls or hits a usage limit | local `qwen2.5:7b` via Ollama, a short instruction set, answers checked before they're shown; back to Claude at the limit's reset time | never leaves you hanging mid-call |
| "Is that question for me?" | local `qwen2.5:7b` (~0.3 s) | free and instant |
| Live notes during the call | local `qwen2.5:7b` (Claude if there's no local model) | saves your Claude usage for answers |
| Final notes, learning, the weekly brief | Claude Sonnet | quality |
| Speech-to-text, voice fingerprints | Whisper `large-v3-turbo`, WeSpeaker ResNet34, all local | fast, private |

Claude runs through the official `claude` CLI on **your own Claude subscription**. No API key is
needed (one is supported if you have it).

---

## Getting started

**You need:** Windows 10/11 · an NVIDIA GPU (built on an RTX 3060, 12 GB) ·
[Claude Code](https://docs.claude.com/en/docs/claude-code) logged in to your Claude account
(`claude auth login`) · [Ollama](https://ollama.com) with `ollama pull qwen2.5:7b` (the local
fallback) · ~6 GB of disk.

### Install (recommended)

1. Download **`Cue-Setup-<version>.exe`** from the [latest release](https://github.com/MidActionJax/cue/releases).
2. Run it. No admin rights needed. It asks two things: where to install the app (~2 GB, mostly
   GPU libraries) and **where to keep your data** (settings, profiles, call notes, and the
   Whisper model, downloaded on first use). Put both on whichever drive has room.
3. Open **Cue** from the Start menu or desktop.

The installer also schedules the daily work-brief refresh (7:30 AM). Uninstalling removes the
app and that task and **leaves your data folder alone**. Upgrading keeps everything.

> **Beta:** the installer isn't code-signed yet, so Windows SmartScreen may warn on first run.
> Choose *More info → Run anyway*.

<details>
<summary><b>Run from source instead</b> (Python 3.11)</summary>

```powershell
git clone https://github.com/MidActionJax/cue.git
cd cue
powershell -ExecutionPolicy Bypass -File setup.ps1   # venv, dependencies, models, daily job, desktop shortcut
```

To build the app and installer yourself: `packaging\build.ps1 -Out D:\CueBuild` (needs
`pip install pyinstaller` and [Inno Setup 6](https://jrsoftware.org/isinfo.php)).

</details>

### Set it up

Fill in the three small files that Cue created from templates in your data folder (all private):

| File | What goes in it |
|---|---|
| `config.yaml` | your name + how transcription mishears it, teammates' names, jargon |
| `profiles/me.md` | your role and how you like to talk |
| `profiles/work/workstreams.md` | the 3–5 things your job breaks into (this drives the "last week" grouping) |

Build your work memory once (after that it updates itself at 7:30 every morning):

```powershell
& "$env:LOCALAPPDATA\Programs\Cue\cue-cli.exe" worklog --backfill   # installed (or wherever you put it)
.\worklog.cmd --backfill                                             # from source
```

`cue-cli.exe` is the command-line side of the app: `worklog`, `note "…"`, `prep`,
`prep-sbir "<name>"`, `sbir-sync`, `docs`, `accent-test`, `devices`. `Cue.exe --quit` ends a running Cue's call (notes saved) and closes it.

**Before your first call:** open **Cue** from the desktop and pick **Work**. When people talk,
the **Them** light should turn green (click it to pick your call's audio device). Then click
**🗣 Set up my voice** and read for 20 seconds.

---

## Using it

<img src="assets/screenshots/1-start.png" alt="Start screen" width="620" align="right">

1. Open Cue → **Work**, **Interview**, **SBIR**, **Practice** or **Trainer**.
2. Asked something? **`Tab`**.
3. Didn't understand them? **`Shift+Tab`** (What?).
4. Call's over → the **red dot**. Notes save automatically (they save even if the window is just closed).

<br clear="right"/>

| Key / control | |
|---|---|
| `Tab` · ⚡ Ask | What should I say to the question I was just asked? |
| `Tab` `Tab` · **Script** | The same answer as full sentences to read word for word |
| `Shift+Tab` · **What?** | Plain-English version of what was just said, and what they want from you |
| trigger pill | When it answers on its own: `auto` (every question) → `smart` (named, or clearly meant for you) → `name` → `manual` |
| **● Them ● You** | Live audio lights. Click to switch devices. Plugging in a headset mid-call is handled automatically |
| 🔴 🟡 🟢 | End call & save · shrink to a bar · live transcript + notes |
| `Ctrl+Alt+M / T / H / Q` | Switch mode · cycle trigger · hide/re-show · end call |
| Trainer: `Ctrl+Alt+A / S / N / R / D / W` | Answer · What? · narrate · run tests · debug · show/hide the trainer windows |

`Tab` still types a Tab in whatever app you're in; Alt+Tab and Ctrl+Tab don't trigger it.

**Names.** In the transcript pane, click **Speaker 2 ✎** and type who it is. Their voice is saved,
and every future call of that kind labels them automatically (voices are kept per mode, so a
coworker's voice is never matched on an SBIR call or an interview). A wrong name is clickable too:
fix it and the whole call is relabeled; tray → *Saved voices* → *Forget* drops a voice learned from
the wrong person. `cue-cli notes rewrite <call>` writes a call's notes again afterwards. For the
hardest-to-understand speaker, add their name to `stt.accurate_speakers` to route only their
speech through the slower, more accurate `large-v3` model.

**Interviews.** Drop your resume and the job description (`job.md`) into `profiles/interview/`
and run `cue-cli prep` (`prep.cmd` from source). You get a 30-second intro, likely questions with talking points in your
voice, STAR stories from your real history, and questions to ask them. Practice mode quizzes you on them.

**Coding rounds.** Pick **Trainer** on the start screen. See [Trainer mode](#trainer-mode-coding-interview-practice).

**Documents.** Tray → **Add documents…** or drag files onto the panel, then pick which calls
they're for (all, or only Work / Interview / SBIR). PDFs, Word (.docx), text and markdown work;
scanned or password-protected PDFs and old `.doc` files get a clear message saying how to convert
them. Remove them from the tray's **Documents** menu, or `cue-cli docs list / add / remove`.
Documents are sent to Claude with your questions, so only add what you're allowed to share
with an AI tool.

**SBIR mode.** For consultants on SBIR/STTR calls with referral partners (state programs, SBDCs,
I-Corps hubs, firms) and founders. Fill in `profiles/sbir/` (intro, offer, proof, FAQ, and a
`rules.md` of hard limits, read last so nothing overrides it), point `sbir.tracker_dir` at your
pipeline spreadsheets, and before a call run `cue-cli prep-sbir "Pat Lee"` (or tray → *Prep SBIR
call…*) for a one-page prep on that person. After the call you get a follow-up email in your
style, a line to paste into your tracker, and anything you promised is filed under that contact
for next time.

**Quick notes.** Work that never touched Claude: `cue-cli note "restarted the nightly cron"`, or
tray → *Add a quick note…*. It goes into tomorrow's brief.

**Accents.** To measure the transcriber on a specific person, record a clip where they talk and
compare models side by side: `cue-cli accent-test "meeting.mp4" --start 120 --length 60`.

---

## Your data

- **Stays on your machine**, in the data folder you picked at install (`%USERPROFILE%\Cue` by
  default; the repo folder when running from source): transcripts, notes,
  voice fingerprints, what Cue learned, and your work brief. To move it, move the folder and edit
  the path in `cue_home.txt` next to `Cue.exe`.
- **Call audio** is saved only temporarily: the other side's audio is recorded during the call so
  the notes can come from an accurate second pass (big model, full context, speakers matched
  across the whole call), and it's deleted as soon as the notes are written (anything left over
  is deleted after 7 days). Turn it off with `notes.accurate_pass: false`; then audio is never saved.
- **Sent to Claude** (through your own account): transcript snippets when you ask for an answer,
  and your summaries/notes when they're generated. Set `llm.backend: ollama` and
  `notes.live_backend: local` to keep live calls fully offline.
- The panel is excluded from screen capture, so people on the call don't see it when you share.
- **Be a good participant.** Check your organization's policies on transcription and AI tools,
  and tell people when a call is being transcribed. Some interviews don't allow assistance at all.

---

## Configuration

Everything lives in `config.yaml`, and every option is commented. The usual knobs:

| Setting | Default | |
|---|---|---|
| `llm.claude_cli_model` | `haiku` | `sonnet` is smarter, ~1 s slower |
| `stt.model` / `stt.accurate_model` | `large-v3-turbo` / `large-v3` | speed vs. accuracy per speaker |
| `stt.voice_match` | `0.6` | raise if two people get merged into one speaker |
| `stt.min_rms` | `0.004` | raise if keyboard or fan noise gets transcribed |
| `overlay.width`, `font_size`, `tint_alpha` | `760`, `16`, `150` | panel look |
| `notes.learn` | `true` | learn your voice from each call |
| `trainer.models.listener` | `sonnet` | `haiku` keeps the live requirements board lighter on usage |
| `trainer.auto_run` | `true` | run the tests every time you save |

<details>
<summary><b>Troubleshooting</b></summary>

| Symptom | Fix |
|---|---|
| **Them** light never turns green | click it and pick the device your call audio plays through |
| Title shows `Listening · local` | Claude is unavailable (login or usage limit), so answers come from the local model. Run `claude auth login` |
| Your own speech shows as "In room" | redo **Set up my voice**, or lower `stt.voice_match` |
| Answers feel generic | run `cue-cli worklog`, then fill in `profiles/me.md` and `workstreams.md` |
| Something went wrong on a call | tray → *Open app log* (`data/logs/app-<date>.log` in your data folder) |
| Panel is off-screen | delete `data/ui_state.json` in your data folder |
| Try it without a call | `Cue.exe --demo --mode work` or `--mode sbir` (or `run.cmd --demo …` from source) |

</details>

---

## Roadmap

- [x] **Windows installer**: a compiled app, no Python needed
- [x] **Trainer mode**: coding-interview practice with a live spec, verified tests and debugging
- [ ] Trainer: a practice partner that plays the interviewer, with hints and a scorecard
- [ ] First-run setup inside the app (name, teammates, workstreams) instead of editing files
- [ ] Code-signed builds and auto-update
- [ ] Settings window (devices, names, triggers) instead of editing YAML
- [ ] Calendar awareness: know who's on the call and the agenda before it starts
- [ ] Per-speaker transcription tuning that adapts to each accent over time
- [ ] Send the follow-up email straight from the notes
- [ ] macOS support

---

## Project layout

```
cue/          the app: audio, speech-to-text, speakers, triggers, LLM backends, panel, notes, practice
cue/trainer/  trainer mode: problem state, model roles, test oracle, runner, windows, eval
tools/vscode-cue-trainer/   the VS Code helper extension
evals/trainer/  scripted mock interviews with gold answers (cue-cli trainer-eval)
tests/        pytest (python -m pytest tests)
worklog/      the daily job: chat history + git + notes → daily notes → weekly → brief (+ fact-check) → history
profiles/     your context (templates ship as *.example.md; your copies stay private)
assets/       logo, icon, splash, installer art, screenshots (tools/ regenerates them)
packaging/    PyInstaller spec + Inno Setup installer (build.ps1 builds both)
```

Built on [faster-whisper](https://github.com/SYSTRAN/faster-whisper),
[sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) + WeSpeaker, [Ollama](https://ollama.com),
PyQt6, and [Claude](https://claude.com).

## License

[MIT](LICENSE) © Jaxon Doolittle
