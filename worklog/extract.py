"""Reads Claude Code + Cowork transcripts (and git history) into compact per-day digests.

Transcripts are JSONL; the big ones are hundreds of MB, almost all tool output. We keep
what says what was *done*: your prompts, Claude's prose, which tools touched which files,
and commit messages. Resumed/forked sessions repeat history, so events dedupe on uuid.
"""
from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

HOME = Path.home()
from cue.config import ROOT as _HOME

MEETINGS = _HOME / "meetings"


def transcript_roots() -> list[Path]:
    roots = [HOME / ".claude" / "projects", HOME / "AppData" / "Roaming" / "Claude" / "local-agent-mode-sessions"]
    # The Claude desktop app is an MSIX package; some of its data is redirected here.
    roots += (HOME / "AppData" / "Local" / "Packages").glob("Claude_*/LocalCache/Roaming/Claude/local-agent-mode-sessions")
    return [r for r in roots if r.exists()]


@dataclass
class Event:
    ts: datetime          # local time
    session: str
    project: str
    kind: str             # prompt | say | tool | sched
    text: str


@dataclass
class Session:
    id: str
    project: str
    title: str = ""
    scheduled: str = ""   # scheduled-task name if this is an automated run
    events: list[Event] = field(default_factory=list)


_SKIP_TYPES = ('"type":"attachment"', '"type":"file-history-snapshot"', '"type":"queue-operation"',
               '"type":"progress"', '"type":"last-prompt"', '"type":"atis-latch"')
_NOISE_PREFIXES = ("<command-name>", "<local-command", "<command-message>", "Caveat:", "[Request interrupted")
_SCHED = re.compile(r'<scheduled-task name="([^"]+)"')
_SYS_TAGS = re.compile(r"<(system-reminder|command-[a-z]+|local-command-[a-z]+)>.*?</\1>", re.S)

_project_cache: dict[str, str] = {}


def project_of(cwd: str) -> str:
    if not cwd:
        return "Unknown"
    if cwd in _project_cache:
        return _project_cache[cwd]
    low = cwd.lower()
    if "local-agent-mode-sessions" in low:
        name = "Cowork"
    elif "scratch-workspaces" in low:
        name = "Scratch"
    else:
        p, name = Path(cwd), None
        for parent in [p, *p.parents]:
            if (parent / ".git").exists():
                name = parent.name
                break
        name = name or p.name or "Unknown"
    _project_cache[cwd] = name
    return name


def _tool_summary(name: str, inp: dict) -> str:
    for key in ("file_path", "notebook_path", "path", "url", "query", "pattern"):
        if isinstance(inp.get(key), str):
            return f"{name} {inp[key][-90:]}"
    for key in ("command", "description", "prompt"):
        if isinstance(inp.get(key), str):
            return f"{name}: {inp[key].strip().splitlines()[0][:140] if inp[key].strip() else ''}"
    return name


def _to_local(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
    except (ValueError, AttributeError):
        return None


def parse_file(path: Path, since: datetime, seen: set, sessions: dict[str, Session]) -> None:
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            head = line[:600]
            if any(t in head for t in _SKIP_TYPES):
                continue
            if '"tool_result"' in line[:3000] and '"type":"user"' in head:
                continue  # tool output: huge and not needed
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            typ = d.get("type")
            sid = d.get("sessionId") or path.stem
            if typ in ("custom-title", "ai-title", "summary"):
                title = d.get("customTitle") or d.get("aiTitle") or d.get("summary") or ""
                if sid in sessions and title and (typ == "custom-title" or not sessions[sid].title):
                    sessions[sid].title = title
                elif title:
                    sessions.setdefault(sid, Session(sid, "Unknown")).title = title
                continue
            if typ not in ("user", "assistant") or d.get("isSidechain") or d.get("isMeta"):
                continue
            uuid = d.get("uuid")
            if uuid in seen:
                continue
            seen.add(uuid)
            # a session belongs to the project it started in — decided before the date filter so
            # the label doesn't depend on how far back this run looks
            s = sessions.setdefault(sid, Session(sid, project_of(d.get("cwd", ""))))
            if s.project == "Unknown":
                s.project = project_of(d.get("cwd", ""))
            msg = d.get("message") or {}
            content = msg.get("content")
            if typ == "user" and isinstance(content, str) and (m := _SCHED.search(content[:500])):
                s.scheduled = m.group(1)  # also before the date filter: an automated run stays automated
                continue
            ts = _to_local(d.get("timestamp", ""))
            if ts is None or ts < since:
                continue
            if typ == "user":
                if d.get("isCompactSummary"):
                    continue
                if isinstance(content, list):
                    content = "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
                if not isinstance(content, str):
                    continue
                text = _SYS_TAGS.sub("", content).strip()
                if not text or text.startswith(_NOISE_PREFIXES):
                    continue
                s.events.append(Event(ts, sid, s.project, "prompt", text))
            else:
                for b in content or []:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text" and b.get("text", "").strip():
                        s.events.append(Event(ts, sid, s.project, "say", b["text"].strip()))
                    elif b.get("type") == "tool_use":
                        s.events.append(Event(ts, sid, s.project, "tool", _tool_summary(b.get("name", "?"), b.get("input") or {})))


def load_sessions(since: datetime) -> dict[str, Session]:
    sessions: dict[str, Session] = {}
    seen: set = set()
    files = []
    for root in transcript_roots():
        for p in root.rglob("*.jsonl"):
            if p.name == "audit.jsonl" or "subagents" in p.parts:
                continue
            try:
                if datetime.fromtimestamp(p.stat().st_mtime) < since:
                    continue  # nothing new in this file
            except OSError:
                continue
            files.append(p)
    # oldest first, so the original copy of resumed history wins the uuid dedupe
    for p in sorted(files, key=lambda p: p.stat().st_mtime):
        parse_file(p, since, seen, sessions)
    return sessions


def git_commits(repos: set[Path], since: date, until: date) -> dict[date, list[str]]:
    out: dict[date, list[str]] = defaultdict(list)
    for repo in sorted(repos):
        try:
            me = subprocess.run(["git", "-C", str(repo), "config", "user.email"], capture_output=True, text=True).stdout.strip().lower()
            r = subprocess.run(["git", "-C", str(repo), "log", "--all", f"--since={since.isoformat()} 00:00:00",
                                f"--until={(until + timedelta(days=1)).isoformat()} 00:00:00", "--date=format-local:%Y-%m-%d %H:%M",
                                "--format=%ad\t%an\t%ae\t%h\t%s", "--shortstat"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
        except OSError:
            continue
        cur = None
        for line in r.stdout.splitlines():
            if "\t" in line:
                ad, an, ae, h, subj = line.split("\t", 4)
                who = "me" if ae.lower() == me and me else an
                cur = (datetime.strptime(ad, "%Y-%m-%d %H:%M").date(), f"{repo.name} {h} ({who}) {subj}")
                out[cur[0]].append(cur[1])
            elif line.strip() and cur:
                out[cur[0]][-1] += f" [{line.strip()}]"
    for day in out:
        out[day].sort()
    return out


KNOWN_REPOS = _HOME / "data" / "worklog" / "repos.json"


def repo_roots(sessions: dict[str, Session]) -> set[Path]:
    """Repos seen in any run so far, so teammates' commits show up even in weeks you
    didn't open that repo with Claude."""
    roots = set()
    try:
        roots |= {Path(p) for p in json.loads(KNOWN_REPOS.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        pass
    for cwd in _project_cache:
        for parent in [Path(cwd), *Path(cwd).parents]:
            if (parent / ".git").exists():
                roots.add(parent)
                break
    roots = {r for r in roots if (r / ".git").exists()}
    KNOWN_REPOS.parent.mkdir(parents=True, exist_ok=True)
    KNOWN_REPOS.write_text(json.dumps(sorted(str(r) for r in roots), indent=1), encoding="utf-8")
    return roots


def _clip(text: str, n: int) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    return text if len(text) <= n else text[:n].rstrip() + " …[cut]"


def meeting_notes(since: date, until: date) -> dict[date, list[str]]:
    """Call notes written by Cue (meetings/YYYY-MM-DD_HHMM.md)."""
    out: dict[date, list[str]] = defaultdict(list)
    for p in sorted(MEETINGS.glob("*.md")) if MEETINGS.exists() else []:
        try:
            d = date.fromisoformat(p.stem[:10])
        except ValueError:
            continue
        if since <= d <= until:
            out[d].append(_clip(p.read_text(encoding="utf-8", errors="replace"), 6000))
    return out


def build_digests(sessions: dict[str, Session], commits: dict[date, list[str]],
                  meetings: dict[date, list[str]] | None = None,
                  journal: dict[date, list[str]] | None = None,
                  day_cap_chars: int = 110_000) -> dict[date, str]:
    meetings = meetings or {}
    journal = journal or {}
    by_day: dict[date, dict[str, list[Event]]] = defaultdict(lambda: defaultdict(list))
    sched: dict[date, dict[tuple, list[Event]]] = defaultdict(lambda: defaultdict(list))
    for s in sessions.values():
        for e in s.events:
            if s.scheduled:
                sched[e.ts.date()][(s.scheduled, s.project)].append(e)
            else:
                by_day[e.ts.date()][s.id].append(e)

    digests = {}
    for day in sorted(set(by_day) | set(sched) | set(commits) | set(meetings) | set(journal)):
        # shrink progressively until the day fits the cap
        for say_n, prompt_n, tools_n in ((700, 2500, 10), (400, 1500, 6), (220, 900, 3), (120, 500, 0)):
            text = _render_day(day, by_day.get(day, {}), sessions, sched.get(day, {}), commits.get(day, []),
                               say_n, prompt_n, tools_n, meetings.get(day, []), journal.get(day, []))
            if len(text) <= day_cap_chars:
                break
        digests[day] = text[:day_cap_chars]
    return digests


def _render_day(day, day_sessions, sessions, sched, commits, say_n, prompt_n, tools_n, meetings=(),
                journal=()) -> str:
    out = [f"# {day:%A %Y-%m-%d}"]
    if journal:
        out.append("\n## Session: My own quick notes (typed by me — first-hand, trust these)")
        out += [f"- {j}" for j in journal]
    for m in meetings:
        out.append("\n## Session: Call notes (live call I was on, auto-transcribed)\n" + m)
    for sid, events in sorted(day_sessions.items(), key=lambda kv: kv[1][0].ts):
        s = sessions[sid]
        events.sort(key=lambda e: e.ts)
        out.append(f"\n## Session: {s.title or '(untitled)'} — project {s.project} — "
                   f"{events[0].ts:%H:%M}–{events[-1].ts:%H:%M}")
        tools: list[str] = []

        def flush_tools():
            if tools and tools_n:
                uniq = list(dict.fromkeys(tools))
                more = f" (+{len(uniq) - tools_n} more)" if len(uniq) > tools_n else ""
                out.append("  tools: " + "; ".join(uniq[:tools_n]) + more)
            tools.clear()

        for i, e in enumerate(events):
            if e.kind == "tool":
                tools.append(e.text)
                continue
            flush_tools()
            if e.kind == "prompt":
                out.append(f"{e.ts:%H:%M} ME: {_clip(e.text, prompt_n)}")
            else:
                # Claude's last message before the next prompt is the conclusion: keep more of it
                nxt = next((x for x in events[i + 1:] if x.kind != "tool"), None)
                n = say_n * 3 if (nxt is None or nxt.kind == "prompt") else say_n
                out.append(f"{e.ts:%H:%M} CLAUDE: {_clip(e.text, n)}")
        flush_tools()
    if sched:
        out.append("\n## Automated scheduled tasks (ran without me)")
        for (name, project), events in sched.items():
            says = [e for e in events if e.kind == "say"]
            last = _clip(says[-1].text, 300) if says else ""
            runs = len({e.session for e in events})
            out.append(f"- {name} [{project}] ran {runs}x. Last result: {last}")
    if commits:
        out.append("\n## Git commits")
        out += [f"- {c}" for c in commits]
    return "\n".join(out)


def extract(since_day: date, until_day: date | None = None) -> dict[date, str]:
    until_day = until_day or date.today()
    since = datetime.combine(since_day, datetime.min.time())
    sessions = load_sessions(since)
    commits = git_commits(repo_roots(sessions), since_day, until_day)
    from cue.journal import by_day as journal_by_day
    digests = build_digests(sessions, commits, meeting_notes(since_day, until_day),
                            journal_by_day(since_day, until_day))
    return {d: t for d, t in digests.items() if since_day <= d <= until_day}
