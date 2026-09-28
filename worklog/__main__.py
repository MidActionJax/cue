"""Work-history pipeline.

    python -m worklog              incremental update (what the daily scheduled task runs)
    python -m worklog --backfill   process all history since worklog.since in config.yaml
    python -m worklog --extract-only   just write the raw per-day digests (no LLM)

Pipeline (each step cached by a hash of its inputs, so reruns only redo what changed):
    transcripts + git  ->  data/worklog/digests/DAY.md     (extract.py, no LLM)
                       ->  data/worklog/daily/DAY.md       (one claude -p per day)
                       ->  data/worklog/weekly/WEEK.md     (per finished ISO week)
                       ->  profiles/work/last_week.md      (call brief: last Mon–Sun by workstream)
                       ->  profiles/work/all_time.md       (full work history / interview stories)
                       ->  profiles/work/glossary.txt      (names & jargon for Whisper)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cue import config  # noqa: E402
from worklog import prompts, workstreams  # noqa: E402
from worklog.extract import extract  # noqa: E402
from worklog.summarize import ClaudeError, Writer  # noqa: E402

WL = ROOT / "data" / "worklog"
OUT = ROOT / "profiles" / "work"
STATE = WL / "state.json"
log = logging.getLogger("worklog")


def _h(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()[:16]


def _week_id(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _week_bounds(d: date) -> tuple[date, date]:
    start = d - timedelta(days=d.weekday())
    return start, start + timedelta(days=6)


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text.strip() + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(prog="worklog")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--since", type=date.fromisoformat)
    ap.add_argument("--extract-only", action="store_true")
    ap.add_argument("--force", action="store_true", help="ignore caches")
    args = ap.parse_args()

    WL.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname).1s %(message)s", datefmt="%H:%M:%S",
                        handlers=[logging.StreamHandler(), logging.FileHandler(WL / "run.log", encoding="utf-8")])
    # one run at a time (the 7:30 task and the app's on-open refresh can overlap)
    lock = WL / "run.lock"
    if lock.exists() and time.time() - lock.stat().st_mtime < 3600:
        log.info("another worklog run is in progress — skipping")
        return 0
    lock.write_text(str(os.getpid()))
    try:
        return _run(args)
    finally:
        lock.unlink(missing_ok=True)


def _run(args) -> int:
    # finish any call whose app was closed before its notes were saved, so today's digest has them
    try:
        from cue.notes import recover
        recover()
    except Exception as e:
        log.warning("call recovery skipped: %s", e)
    cfg = config.load()
    wcfg = cfg.worklog
    state = json.loads(_read(STATE) or "{}")
    today = date.today()

    first_run = not state.get("daily")
    since = args.since or (date.fromisoformat(str(wcfg.since)) if (args.backfill or first_run) else today - timedelta(days=9))
    log.info("extracting transcripts + git since %s", since)
    digests = extract(since, today)
    for day, text in digests.items():
        _write(WL / "digests" / f"{day}.md", text)
    log.info("%d day digests written", len(digests))
    if args.extract_only:
        return 0

    background = "\n\n".join(_read(config.resolve(p)) for p in wcfg.background_files)
    writer = Writer(cfg.user.name, wcfg.work_projects, background, wcfg.model, wcfg.daily_model,
                    workstreams.text())
    state.setdefault("daily", {})
    state.setdefault("weekly", {})

    def save_state():
        _write(STATE, json.dumps(state, indent=1, sort_keys=True))

    # ---- daily notes
    todo = []
    for day, digest in sorted(digests.items()):
        h = _h(digest)
        path = WL / "daily" / f"{day}.md"
        if not args.force and state["daily"].get(str(day)) == h and path.exists():
            continue
        if "## Session" not in digest and len(digest) < 3000:
            _write(path, digest.replace(f"# {day:%A %Y-%m-%d}", f"## {day:%A %Y-%m-%d}"))  # commits only: no LLM needed
            state["daily"][str(day)] = h
            continue
        todo.append((day, digest, h, path))
    save_state()
    if todo:
        log.info("summarizing %d days with claude -p (%s)…", len(todo), wcfg.daily_model)
    try:
        with ThreadPoolExecutor(max_workers=wcfg.parallel) as pool:
            futs = {pool.submit(writer.daily, day, digest): (day, h, path) for day, digest, h, path in todo}
            for fut in as_completed(futs):
                day, h, path = futs[fut]
                _write(path, fut.result())
                state["daily"][str(day)] = h
                save_state()
                log.info("  daily %s done", day)

        daily_files = sorted((WL / "daily").glob("*.md"))
        daily = {date.fromisoformat(p.stem): _read(p) for p in daily_files}
        this_week_start, _ = _week_bounds(today)

        # ---- weekly notes (finished weeks only)
        weeks: dict[str, list[date]] = {}
        for d in daily:
            if d < this_week_start:
                weeks.setdefault(_week_id(d), []).append(d)
        todo_w = []
        for wid, days in sorted(weeks.items()):
            notes = "\n\n".join(daily[d] for d in sorted(days))
            h = _h(notes)
            path = WL / "weekly" / f"{wid}.md"
            if not args.force and state["weekly"].get(wid) == h and path.exists():
                continue
            if "**Did:**" not in notes:  # commits-only week: nothing to condense
                s, e = _week_bounds(days[0])
                _write(path, f"## Week {wid} ({s} – {e}) — git activity only\n\n{notes}")
                state["weekly"][wid] = h
                continue
            todo_w.append((wid, days, notes, h, path))
        with ThreadPoolExecutor(max_workers=wcfg.parallel) as pool:
            futs = {}
            for wid, days, notes, h, path in todo_w:
                s, e = _week_bounds(days[0])
                futs[pool.submit(writer.weekly, wid, s, e, notes)] = (wid, h, path)
            for fut in as_completed(futs):
                wid, h, path = futs[fut]
                _write(path, fut.result())
                state["weekly"][wid] = h
                save_state()
                log.info("  weekly %s done", wid)

        # ---- weekly brief: last Mon–Sun + this week so far (calls are Monday mornings)
        prev_start = this_week_start - timedelta(days=7)
        prev_end = this_week_start - timedelta(days=1)
        brief_notes = "\n\n".join(daily[d] for d in sorted(daily) if d >= prev_start)
        ws = workstreams.text()
        hb = _h(brief_notes, background, ws, prompts.LAST_WEEK, prompts.VERIFY, str(prev_start))
        if args.force or state.get("last_week") != hb or not (OUT / "last_week.md").exists():
            log.info("writing last_week.md (%s – %s + this week)", prev_start, prev_end)
            brief = writer.last_week(today, prev_start, prev_end, this_week_start,
                                     brief_notes or "(no activity logged)")
            _write(OUT / "last_week.md", brief)
            added = workstreams.add_new(brief)
            if added:
                log.info("new workstreams added to workstreams.md: %s", ", ".join(added))
            state["last_week"] = hb
            save_state()

        # ---- all-time history
        weekly_text = "\n\n".join(_read(p) for p in sorted((WL / "weekly").glob("*.md")))
        current = "\n\n".join(daily[d] for d in sorted(daily) if d >= this_week_start)
        ws = workstreams.text()
        ha = _h(weekly_text, current, background, ws, prompts.ALL_TIME)
        if args.force or state.get("all_time") != ha or not (OUT / "all_time.md").exists():
            log.info("writing all_time.md")
            _write(OUT / "all_time.md", writer.all_time(today, weekly_text, current))
            state["all_time"] = ha
            log.info("writing glossary.txt")
            _write(OUT / "glossary.txt", writer.glossary(_read(OUT / "all_time.md") + "\n\n" + _read(OUT / "last_week.md")))
            save_state()
    except ClaudeError as e:
        log.error("%s", e)
        return 2

    state["last_run"] = datetime.now().isoformat(timespec="seconds")
    save_state()
    log.info("done → %s", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
