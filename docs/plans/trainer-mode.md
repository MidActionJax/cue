# Section 2: Trainer mode (coding interviews)

Status: **built** (0.4.0-beta, 2026-10-28). Written against Cue 0.3.0-beta (`main` @ `20c415d`).
The code lives in `cue/trainer/`, the VS Code helper in `tools/vscode-cue-trainer/`, the scripted
interviews in `evals/trainer/`. Differences from this plan:

- Test drivers: Cue writes the Python and JavaScript ones itself. Every other language gets one
  from the model, which is only trusted once it builds against your file; if it doesn't, the
  build errors go back to the model (up to 3 tries).
- Highlighting is Pygments → Qt rich text (one HTML string per line, so lines can carry marks),
  not a `QSyntaxHighlighter`.
- Line marks remember their line's text, so they follow the line when you insert code above it.
- Session records go to `trainer/` in the data folder, not `meetings/`, so practice never reaches
  the work brief.

---

## 1. What we're building

Cue in a coding round. It listens to the interviewer (audio, or problem text you paste) and keeps
a live picture of the problem. Around that picture sit several glass windows:

| Window | What it shows |
|---|---|
| **Main Cue panel → Spec board** | The requirements list. It stays up the whole session; lines get edited as they change, never wiped and rebuilt. It also holds the problem summary, the time estimate and the **countdown**. |
| **Answers** | The `Tab` answers you already know, but grounded in this problem, your chosen approach and the code you've written so far. |
| **Ask next** | Questions to ask the interviewer. It starts with about 3 general ones and grows as the problem takes shape. Each one is ticked off when you ask it and removed when it no longer applies. |
| **Design tracks** | A structural plan overview first, then every approach from brute force to optimal: complexity, why the slower ones lose (nested loop vs. hash map, with real numbers), and the trade-offs. |
| **Code** | Reference pseudocode and code split into logical blocks (init, edge cases, core loop, return). A live view of *your* file from VS Code with lines marked green or red, requirements ticked off as you implement them, built-in test cases with expected vs. actual output, and a **debug hotkey** that traces exactly which lines break which tests. |

A **language dropdown** at the start lists every common language. Code runs for any language
whose toolchain is installed; the rest still get reference code, analysis and review.

### What a coding round looks like (so the timer and prompts know)

A 45–60 minute round usually goes like this:

1. About 5 min of intros.
2. The interviewer states a problem, usually small: 20–40 lines of solution.
3. You ask clarifying questions (input size, empty input, duplicates, return format, in place or not).
4. You talk through a brute-force idea, then a better one, and agree on complexity.
5. You code while thinking out loud.
6. You walk through tests by hand and fix bugs.
7. Follow-ups ("what if it's sorted?", "what if it doesn't fit in memory?").
8. About 5 min for your questions to them.

They grade four things: **problem solving, communication, code quality, and testing/verification**.

Typical time to solve once the problem is defined: Easy ≈ 10–15 min, Medium ≈ 20–30 min,
Hard ≈ 35–45 min.

Common patterns (used as tags): arrays & hashing, two pointers, sliding window, stack/monotonic
stack, binary search, linked lists, trees (DFS/BFS), heaps, graphs, intervals, greedy,
backtracking, dynamic programming (1D/2D), tries, bit manipulation, matrix, string building, math.

---

## 2. How accuracy is engineered in (the "levels better" part)

The 0.3.0 complaint was answers that are confidently right about the wrong thing. Trainer mode
fixes that with structure, not just better prompts:

1. **One shared problem state.** The requirements, questions, approaches, code blocks, tests and
   answers all read from one `ProblemState`. The Answers window can't contradict the Design window
   or talk about a different problem, because both see the same state, and the answer prompt
   includes your actual code.
2. **Edits, not rewrites.** The listener returns small *patches* (add, edit or drop requirement 3;
   mark question 2 asked) with stable IDs. Lines don't jump around or vanish between updates, and
   every change is visible (briefly highlighted, with history on hover).
3. **Every requirement cites its source.** Each line keeps the transcript quote it came from.
   *Stated* requirements (the interviewer said it) look different from *assumed* ones (inferred,
   not confirmed). Assumed ones automatically create a question in Ask next ("Can I assume the
   input fits in memory?").
4. **Expected outputs come from running code, not from the model.** For every problem the model
   writes two independent Python solutions: a reference and a deliberately simple brute force.
   Cue runs both on hundreds of random inputs (differential testing). Expected outputs are trusted
   only when the two agree. If they disagree, the tests are marked "unverified" and regenerated.
5. **Red means proven.** Syntax errors come from the real compiler or VS Code diagnostics. A logic
   line turns **red** only when a failing test or real execution trace backs it up. A model
   opinion with no evidence is **yellow** ("suspect"). Green means a passing test exercised it.
6. **Measured complexity.** Your solution runs at n = 10³, 10⁴, 10⁵ and the growth is fitted
   ("measured ≈ O(n²)"). The Design window shows *measured* next to *claimed* complexity.
7. **An eval set like the 0.3.0 one.** About 15 scripted mock interviews (classic problems, with
   interruptions, changed requirements and hints) are replayed through the pipeline. Each run is
   scored for requirement recall and precision, question tracking, whether tests are correct
   (oracle agreement), estimate sanity and approach correctness. Every prompt change is measured
   against it (`cue-cli trainer-eval`).

---

## 3. The user flow

1. **Chooser → Trainer** (a 5th big button: "Trainer · coding round practice").
2. **Setup dialog.** This is a normal, focusable window (the glass panel can't take keyboard focus):
   - **Language**: a searchable dropdown of all common languages. A ▶ marks the ones that can run on
     this machine (toolchain detected); the rest say "reference only".
   - **Solution file**: "Create one for me" (default: `Documents\Cue Trainer\<date>_<time>\solution.<ext>`
     with a starter template, opened in VS Code via `code -r`) or "Pick an existing file".
   - **Problem source**: Listen to the call (default) · Paste problem text · Both.
   - Optional: a target duration override.
3. **The windows open** in the saved layout (default: Spec board top-center, the other windows
   down the right side; the Code window is tall). Every window is glass, never steals focus,
   follows the screen-share setting, and remembers its own position. Layout presets are on the
   tray menu.
4. **Listening.** As the interviewer talks, the Spec board fills in. The question list adapts.
   Once the problem counts as *defined* (enough to solve), Cue:
   - shows the estimate ("Medium · ~25 min · two pointers") and **starts the countdown**
     (or on click, if `trainer.timer_autostart: false`)
   - builds the Design tracks, Code blocks and verified Tests in the background
5. **Coding.** Every save (or every keystroke with the VS Code extension) refreshes the live view of
   your code, the syntax marks, block statuses and requirement checkmarks. Tests auto-run on save
   for runnable languages (toggle).
6. **Interviewer asks something** → the answer hotkey → the Answers window.
7. **Stuck or failing** → the **debug hotkey** → a full error trace in the Code window (and in
   VS Code with the extension).
8. **End** (red dot / `Ctrl+Alt+Q`). A session record is saved: problem, final requirements, your
   code, test results, time used vs. estimate, approaches. It's written like call notes, in
   `meetings/` and `data/trainer/<stamp>/`.

### Hotkeys (trainer mode only)

**`Tab` can't be the answer key in trainer mode**, because you'll press it constantly to indent
code. While a trainer session is running, the normal hotkeys are swapped for these (all
configurable):

| Key | Action |
|---|---|
| `Ctrl+Alt+A` | Answer the interviewer's latest question (double-tap = full script) |
| `Ctrl+Alt+S` | What? (plain-English version of what they just said) |
| `Ctrl+Alt+N` | Narrate: what to say out loud *right now* for this phase ("Let me start with brute force…") |
| `Ctrl+Alt+R` | Run tests |
| `Ctrl+Alt+D` | **Debug**: full error trace |
| `Ctrl+Alt+W` | Show/hide all trainer windows |
| `Ctrl+Alt+H` / `Ctrl+Alt+Q` | Hide / end session (unchanged) |

---

## 4. The problem state (single source of truth)

`cue/trainer/state.py`, plain dataclasses saved as JSON to `data/trainer/<stamp>/state.json`
after every change (so a crash loses nothing and the session can be reviewed afterwards).

```jsonc
{
  "phase": "listening | clarifying | defined | designing | coding | testing | follow_up | done",
  "problem": {
    "title": "Two Sum", "summary": "Return indices of two numbers that add to target.",
    "style": "function | stdin",                 // LeetCode-style vs HackerRank-style
    "signature": {"name": "two_sum", "params": [["nums","int[]"],["target","int"]], "returns": "int[]"},
    "examples": [{"input": {"nums":[2,7,11,15],"target":9}, "output":[0,1], "source":"THEM 10:02:14"}],
    "pattern": ["arrays & hashing"], "difficulty": "easy|medium|hard",
    "defined": true, "confidence": 0.85
  },
  "requirements": [
    {"id":"R3", "text":"Return indices, not values", "kind":"functional|io|constraint|edge|performance",
     "status":"stated|assumed|dropped", "met": "no|partial|yes", "source":"\"give me back the positions\" (10:02:31)",
     "history":[{"text":"Return the two numbers","at":"10:02:20"}]}
  ],
  "questions": [
    {"id":"Q2", "text":"Can the same element be used twice?", "why":"changes the hash-map check order",
     "status":"open|asked|answered|dropped", "answer":"No (10:03:05)", "priority":1}
  ],
  "hints": [{"text":"what if it were sorted?", "at":"10:09:40", "points_to":"A3"}],
  "estimate": {"minutes": 15, "rationale": "single pass with a hash map; standard easy", "started_at": null},
  "plan": [{"step":"Index values → positions", "detail":"hash map value→index"}, {"step":"One pass", "branch_of": null}],
  "approaches": [
    {"id":"A1", "name":"Brute force: check every pair", "time":"O(n²)", "space":"O(1)",
     "idea":"...", "pseudocode":"...", "why_not":"n=10⁵ → ~5·10⁹ pair checks (minutes) vs 10⁵ lookups",
     "say_it":"The simple way is to check every pair, but that's quadratic…", "chosen": false}
  ],
  "blocks": [
    {"id":"B1", "name":"Initialization", "pseudo":"...", "code":"...", "status":"todo|partial|done|issue", "lines":[3,4]}
  ],
  "tests": [
    {"id":"T1", "name":"example 1", "origin":"interviewer|edge|generated|stress|custom",
     "input":{...}, "expected":[0,1], "verified":true, "compare":"exact|unordered|float:1e-6"}
  ],
  "oracle": {"reference":"<python>", "brute":"<python>", "generator":"<python gen(n, rnd)>", "agreement": "500/500"},
  "code": {"path":"...", "language":"cpp", "text":"...", "detected_approach":"A2",
           "lines": {"12":{"mark":"red","why":"off-by-one: j starts at i","evidence":"T4"}}},
  "runs": [{"at":"...", "passed":5, "total":7, "results":[...]}],
  "follow_ups": ["What if the array is sorted?"]
}
```

Every model update to this is a **patch** (a list of ops), validated before it's applied. Unknown
IDs, a requirement with no source quote, or a dropped line coming back without new evidence are
all rejected. The UI animates the diff.

---

## 5. The brain: model roles

A new module, `cue/trainer/jobs.py`, runs background model jobs alongside the existing live
answer backend. Each role has its own prompt, its own warm session (where latency matters), and
**latest-wins coalescing**: if a new update is queued while one is running, the stale one is
dropped.

| Role | Fires when | Model (default) | Output |
|---|---|---|---|
| **Listener** | each final utterance (THEM or ME), coalesced, ≥ 4 s apart; also on pasted text | Sonnet, warm | state patch (requirements, questions, hints, phase, examples, estimate) |
| **Designer** | problem becomes *defined*; requirements change materially; a hint lands | Sonnet | plan, approaches, blocks (pseudo + code in your language) |
| **Test author** | after the designer | Sonnet | reference + brute force + input generator + edge cases (verified by running them, §7) |
| **Reviewer** | code changed and idle 2 s (at most every 8 s) | Sonnet (Haiku if `trainer.fast_review`) | per-line verdicts, block statuses, requirements met, detected approach |
| **Answerer** | answer hotkey / interviewer question (auto trigger) | existing live backend | Q / About / > / - format, grounded in state + code |
| **Narrator** | narrate hotkey | live backend | 1–3 sentences to say out loud now |
| **Debugger** | debug hotkey | Sonnet | exact failing lines, why, minimal fix (§8) |

Implementation notes:
- Reuse `ClaudeCLIBackend` (warm `claude -p` sessions) for the warm roles, one instance per role.
  The **state goes in the user message, never the system prompt**: `configure()` restarts the
  session pool, and the state changes every few seconds.
- JSON roles: use the CLI's structured-output flag if the installed `claude` supports it;
  otherwise parse robustly and retry once with the parse error. A patch that fails validation is
  logged and dropped. The board never shows garbage.
- `AnthropicBackend` also works (API key users). Ollama is the fallback for the Answerer only;
  the JSON roles need Claude-level reliability and pause (with a status line) when Claude is down.
- **Usage budget:** a 45-minute session is roughly 80–150 Claude calls (mostly small Listener
  patches). The cadences above are in `config.yaml`, and the title bar shows the count of calls
  this session.

Trainer prompts live in `cue/trainer/prompts.py`. Key rules for the Listener:
- Interviewers restate, correct themselves and think out loud. Only the *latest* intent counts.
  Edit the existing line; don't add a near-duplicate.
- Your own speech (ME) asking a question marks the matching open question **asked**. The
  interviewer's reply marks it **answered** and usually becomes or edits a requirement.
- "What if…" from the interviewer during design is a **hint**, not a requirement. After you've
  solved it, it's a **follow-up**.
- Never invent constraints. An inference is `assumed` plus a question.

---

## 6. The windows

New base class `GlassWindow` (moved out of `Overlay`): acrylic, non-activating, excluded from
capture, manual drag, rounded corners, per-window saved position (`ui_state.json` → `windows.<key>`).
`Overlay` then subclasses it, so the main panel keeps working exactly as it does now.

**Spec board (inside the main panel while trainer mode is on)**
- Top: problem title + one-line summary, pattern tag, difficulty, **countdown** (`18:42 left`),
  and the phase pill.
- Requirements, grouped by kind. Stated = solid; assumed = italic with an "ask?" chip; met = ✓
  (from the Reviewer); dropped = struck through, then gone after 10 s. A fresh edit glows for 2 s,
  and hovering shows the source quote and history.
- Pacing nudges under the timer, as a share of the estimate: ~15% "clarify done? say the brute
  force", ~30% "start coding", ~80% "walk a test out loud", 100% "time: narrow scope, say what's
  left". Timer colors: blue → amber at 25% left → red at 0, then it counts up.

**Answers.** The same rendering as today's answers (Q / About pill / quote / bullets). Keeps the
last 5 answers as a scrollable stack, newest on top.

**Ask next.** A priority-ordered checklist: ☐ open, ✓ asked (greyed, with the answer underneath),
dropped ones fade out. An "assumed" requirement adds its question here automatically. At most 6
open; the rest collapse under "more".

**Design tracks**
- **Plan overview**: the structure as a horizontal flow of steps with arrows and "or" branches
  (`Parse → Index values (hash map) → One pass: lookup target−x → Return / not found`).
- **Tracks**: one card per approach, ordered brute → optimal: name, time/space, the one-line
  idea, collapsible pseudocode, **why it loses**, with an op-count bar at n = 10⁵ (e.g. 5·10⁹ vs
  10⁵ on a log scale), trade-offs, and a "say it" line for explaining it out loud.
- Badges: ★ recommended, "you're on this one" (detected from your code), "hint points here".
  Click a card to pin it as the chosen track; the Code blocks follow the pinned track.
- **Measured vs. claimed** complexity once tests run (§7).

**Code** (tall window with tabs)
- **Blocks**: Initialization · Input & edge cases · Core loop · State update · Return (whatever
  fits the problem). Each block toggles between pseudocode and code in your language, with syntax
  highlighting, and shows a status (☐ todo, ◐ partial, ☑ done, ✗ issue) from the Reviewer.
  Clicking a block highlights its lines in your file.
- **My code**: a read-only live mirror of your file with line numbers and highlighting. Real
  syntax errors are red with the compiler message, test-backed logic errors red, suspect lines
  yellow, verified lines green. A requirements strip on top shows each requirement with its ✓.
- **Tests**: a table of # · name · origin · input · expected · actual · ✓/✗ · ms. Click a row
  for a diff plus stdout/stderr. Buttons: ▶ Run all (`Ctrl+Alt+R`) · + Add test (dialog: input,
  and expected or "compute from reference") · Auto-run on save. Summary in the title: `5/7 ✓`.
- **Debug**: the latest debug report (§8).

Syntax highlighting uses **Pygments** (pure Python, covers every language in the dropdown)
through a small `QSyntaxHighlighter` adapter. It's the only new dependency.

---

## 7. Running code: languages, harness, oracle, tests

**Language registry** (`cue/trainer/langs.py`). One entry per language: display name, Pygments
lexer, file extension, comment style, starter template, syntax-check command, build/run commands,
and the harness template. Availability is detected at startup (`shutil.which`, plus the usual
install folders) and refreshed from the setup dialog.

- **Runnable tier** (built-in templates, tested): Python, JavaScript (node), TypeScript
  (tsx/ts-node/deno), Java (single-file `java Main.java`), C, C++ (g++/clang++/MSVC `cl`),
  C# (a cached `dotnet` template project, so each run is just a rebuild), Go, Rust, Kotlin,
  Ruby, PHP, Swift (if installed), Scala (scala-cli), Dart, Lua, R, Julia, Haskell (runghc),
  Perl, Bash.
- **Reference tier**: every other Pygments language (SQL, Elixir, OCaml, F#, Clojure, Zig, Nim,
  MATLAB, …). You get reference blocks, review and debug reasoning; tests show a model-predicted
  result clearly labeled *not executed*.
- Commands can be overridden per language in `trainer.toolchains` (e.g. a specific `g++` path or
  flags).

**Harness** (`cue/trainer/harness.py`)
- *Function style*: Cue writes a driver next to your file (never inside it). The driver
  imports/includes your solution, reads one JSON test case from stdin, converts it to the
  signature's types, calls your function and prints JSON. Templates for the runnable tier; for
  unusual signatures (linked lists, trees), helpers build them from LeetCode-style arrays
  (`[1,null,2]`).
- *Stdin style*: your program runs as-is with the test's stdin; stdout is compared.
- Line numbers in errors are mapped back to **your** file (the driver's own lines are subtracted
  out).

**Runner** (`cue/trainer/runner.py`). Runs in a temp dir as a subprocess, with a timeout per test
(default 5 s; stress tests 10 s) and memory capped by a Windows Job Object that also kills the
whole process tree on timeout. Captures stdout, stderr, exit code and time. Compiled languages
build once per file version, not once per test.

**Oracle** (`cue/trainer/oracle.py`)
- The Test author writes `reference.py`, `brute.py` and `gen.py` (`gen(n, rnd) -> input`).
  They run under **Cue's own bundled Python** through a new internal command
  (`Cue.exe trainer-exec <file>`, which uses `runpy`), so Python doesn't need to be installed.
- Differential check: 300 random small inputs plus the edge cases through reference and brute.
  Any disagreement → regenerate (up to 2 times) → otherwise mark the tests *unverified*.
- Expected outputs for every test come from the **reference run**, never from model text.
- **Stress test**: n = 10⁵ (or whatever the constraints say), with a time limit. This catches an
  O(n²) solution that passes the small cases.
- **Measured complexity**: time your solution at three sizes and fit the log-log slope.

---

## 8. The debug hotkey (`Ctrl+Alt+D`)

1. Save state, then run all tests (compile errors short-circuit to step 4 with the compiler
   output).
2. **Shrink** the smallest failing test: delta-debug the input with the reference as the judge
   until it's minimal (e.g. `nums=[3,3], target=6`). Works in every runnable language, because it
   only runs your program.
3. **Real trace** on the minimal input:
   - Python: run your function under `sys.settrace` and record every executed line with the local
     variables (capped). Then find the first line where the state diverges from what the
     reference implies.
   - Other runnable languages: the real runtime error and stack trace (mapped to your lines),
     plus actual vs. expected on the minimal input.
4. The **Debugger** model gets: your numbered code, the minimal failing input, expected vs.
   actual, the trace or stack, compiler output and requirement statuses. It returns the exact
   lines, why each is wrong, the evidence (which test or trace step proves it) and the smallest
   fix.
5. The Code → Debug tab shows the report, red lines are marked in My code (and in VS Code via the
   extension), and a short summary goes in the Answers window ("Line 14: j should start at i+1;
   it reuses the same element. Fails on [3,3], 6.").

---

## 9. Seeing your code: file watcher + VS Code extension

**Phase A (no install needed): file watcher.** `QFileSystemWatcher` on your solution file (with
handling for editors that save by replacing the file). It picks up every save. With VS Code
`files.autoSave: afterDelay` (Cue offers to set this in the workspace `.vscode/settings.json` it
creates next to the file), it updates about once a second as you type.

**Phase B: the "Cue Trainer" VS Code extension** (`tools/vscode-cue-trainer/`, plain JavaScript,
no build step, packaged as a `.vsix` and shipped in the installer; Cue offers
`code --install-extension`).
- Sends the **unsaved** buffer, language ID, cursor line and **VS Code's own diagnostics** (the
  language server's red squiggles, which covers almost every language) to Cue on change
  (debounced 300 ms).
- Draws Cue's verdicts **inside the editor**: green/red/yellow line backgrounds, gutter icons, and
  hover text with the reason. It also gets "run tests" and "debug" commands with keybindings that
  work inside VS Code.
- Transport: Cue runs a small HTTP server on `127.0.0.1` only (stdlib `ThreadingHTTPServer`).
  The port and a random token are written to `%LOCALAPPDATA%\Cue\trainer_bridge.json`, which the
  extension reads. Requests without the token are refused.

---

## 10. Code layout

```
cue/trainer/
  __init__.py
  session.py        TrainerSession: owns the state, wires transcript → roles → windows, timers, save/end
  state.py          dataclasses, patch ops + validation, JSON persistence, diffs for the UI
  jobs.py           background model jobs: warm sessions per role, coalescing, JSON parse/retry, call counter
  prompts.py        Listener / Designer / Test author / Reviewer / Answerer / Narrator / Debugger prompts
  langs.py          language registry, toolchain detection, starter templates
  harness.py        function-style drivers per language, stdin style, line mapping
  runner.py         sandboxed compile/run, job object limits, timing
  oracle.py         reference/brute/gen execution, differential testing, input shrinking, complexity fit
  trace.py          Python line tracer
  watch.py          file watcher + localhost bridge server for the VS Code extension
  windows.py        GlassWindow-based Answers / Ask next / Design / Code windows; Spec board widget
  highlight.py      Pygments → QSyntaxHighlighter
  setup_dialog.py   language dropdown, solution file, problem source
  eval.py           replay scripted interviews + scoring (cue-cli trainer-eval)
tools/vscode-cue-trainer/   package.json, extension.js, README
tests/trainer/              pytest: patches, harness per language, runner, oracle, shrinking, complexity fit
evals/trainer/              ~15 scripted mock interviews (text with timestamps) + gold answers
```

Changes to existing files:
- `overlay.py`: extract `GlassWindow`, add the Trainer chooser button, and a slot for the Spec
  board widget in the body.
- `app.py`: `_set_mode("trainer")` → setup dialog → `TrainerSession`; `_on_text` forwards final
  utterances to the session; hotkeys are registered per mode, so they're swapped on
  entering/leaving trainer (`keyboard.remove_hotkey` / re-add); `ask()` gets a trainer path that
  builds its prompt from the state; a tray submenu for layouts/windows.
- `cli.py`: `trainer-exec`, `trainer-eval`, `trainer --replay <file>` (dev/demo with no audio,
  like `--demo`).
- `config.example.yaml`: a new `trainer:` section (language, folder, editor command, models per
  role, cadences, timeouts, toolchain overrides, hotkeys, layout, timer autostart).
- `requirements.txt` + `packaging/cue.spec`: `pygments`; bundle the `.vsix`.
- `notes.py`: trainer sessions write a session record instead of call notes (no follow-up
  email), learning goes to `profiles/trainer/learned.md`, and nothing goes into the work brief.
- README (new section and screenshots), CHANGELOG, and the `tools/make_screenshots.py` scenes.

---

## 11. Milestones

Each milestone ends with a demo and its tests passing. Order matters: M1 and M2 make it useful
in a mock interview even before code analysis exists.

| # | Milestone | Done when |
|---|---|---|
| **M0** | Foundations: `GlassWindow` refactor, `trainer:` config, language registry + detection, setup dialog, Trainer button, per-mode hotkeys (Tab off), replay mode | Existing modes look and behave identically; Trainer opens the setup dialog, creates the starter file and opens VS Code; a replayed transcript flows into the session |
| **M1** | Listener + Spec board + Ask next + estimate/countdown + pacing nudges | Replaying 3 scripted interviews gives a requirements list that edits in place, questions that tick off and drop, and a sane estimate that starts the timer |
| **M2** | Answers + Narrate, grounded in state (and code once M4 lands) | Interviewer questions about the problem get answers about *this* problem; nothing comes from a different project or problem |
| **M3** | Design tracks: plan overview, approaches, op-count bars, pinning | Brute → optimal cards with correct complexities on the eval problems |
| **M4** | Code window part 1: Blocks, file watcher, My code mirror, real syntax checks, Reviewer (block status, requirement ✓, suspect lines) | Typing a solution ticks blocks and requirements live; a syntax error shows red with the compiler message within ~1 s of saving |
| **M5** | Tests: oracle + differential check, harness for the runnable tier, runner, results table, stress test, measured complexity, auto-run | The eval problems get verified tests; a known-buggy solution fails the right tests; an O(n²) solution is caught by the stress test |
| **M6** | Debug hotkey: shrink, Python trace, stack mapping, Debugger report, line marks | Seeded bugs (off-by-one, wrong init, missed edge case) are pinned to the exact line with the minimal input |
| **M7** | VS Code extension: live buffer, diagnostics in, decorations out, run/debug commands | Unsaved edits update Cue; red/green lines appear inside VS Code |
| **M8** | Session records, eval harness + baseline scores, docs, screenshots, packaging, release **0.4.0-beta** | `cue-cli trainer-eval` gives a scored baseline; installer build works on a clean machine; tagged release |

---

## 12. Risks and how they're handled

| Risk | Handling |
|---|---|
| Model JSON is malformed or invents things | Schema validation, a source quote required for stated requirements, reject-and-log, and the eval set catches regressions |
| Tests with wrong expected outputs | Expected values only come from running the reference, which is differential-checked against brute force |
| Claude usage limits mid-session | Coalescing and cadence limits, a visible call counter, and the Answerer falls back to the local model; JSON roles pause with a status line |
| Too many windows on one screen | Layout presets (right column / around editor / single tabbed window), Ctrl+Alt+W hides them all |
| Keyboard noise transcribed as speech while typing | The existing VAD and `min_rms` filters; an optional mic noise gate in trainer mode |
| Compilers missing / slow (Kotlin, C#) | Detection in the dropdown, cached builds, the clear "reference only" tier |
| Running your code | Your own code, in a temp dir, with time and memory limits and process-tree kill |

---

## 13. Decisions to confirm before building (defaults chosen)

1. **Answer hotkey in trainer mode:** `Ctrl+Alt+A` (Tab indents code, so it can't trigger
   answers here).
2. **Model for the background roles:** Sonnet everywhere (best accuracy); `trainer.fast_review`
   switches the Reviewer to Haiku if usage gets tight.
3. **Windows:** separate glass windows as described, with a "single tabbed window" layout
   preset for small screens.
4. **Timer start:** automatic when the problem is defined (switch to manual with
   `trainer.timer_autostart: false`).

Later (separate section): the **teacher/practicer** mode. Cue plays the interviewer and teaches
with hints and scorecards. It's out of scope here, but the state, oracle, runner and windows
built in this section are designed so it can reuse them.
