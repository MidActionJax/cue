"""Runs `claude -p` (headless Claude Code on your subscription) to write the work-log docs."""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import time

from . import prompts

log = logging.getLogger(__name__)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ClaudeError(RuntimeError):
    pass


def claude_p(prompt: str, system: str, model: str = "sonnet", timeout: int = 900, retries: int = 2) -> str:
    exe = shutil.which("claude")
    if not exe:
        raise ClaudeError("`claude` CLI not found on PATH")
    args = [exe, "-p", "--model", model, "--output-format", "text", "--tools", "",
            "--no-session-persistence", "--strict-mcp-config", "--setting-sources", "",
            "--system-prompt", system]
    last = ""
    for attempt in range(retries + 1):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as cwd:  # empty cwd: no project CLAUDE.md, no side effects
            r = subprocess.run(args, input=prompt, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=timeout, cwd=cwd, creationflags=_NO_WINDOW)
        out = r.stdout.strip()
        if r.returncode == 0 and out and not out.startswith(("Failed to authenticate", "Invalid API key")):
            return out
        last = (out or r.stderr.strip())[:500]
        if "limit" in last.lower() and ("hit" in last.lower() or "reset" in last.lower()):
            raise ClaudeError(f"Claude usage limit reached — {last}. Everything done so far is saved; rerun after the reset.")
        if "auth" in last.lower() or "log in" in last.lower() or "login" in last.lower():
            raise ClaudeError(f"Claude CLI is not logged in — run `claude` in a terminal and /login. ({last})")
        log.warning("claude -p failed (attempt %d): %s", attempt + 1, last)
        time.sleep(5 * (attempt + 1))
    raise ClaudeError(last or "claude -p failed")


class Writer:
    def __init__(self, name: str, work_projects: list[str], background: str, model: str, daily_model: str,
                 workstreams: str = ""):
        self.name, self.model, self.daily_model = name, model, daily_model
        self.system = prompts.SYSTEM.format(name=name)
        self.bg = prompts.BACKGROUND.format(name=name, background=background.strip() or "(none yet)",
                                            work_projects=", ".join(work_projects),
                                            workstreams=workstreams.strip() or "(none defined)")

    def _run(self, template: str, model: str | None = None, **kw) -> str:
        return claude_p(template.format(name=self.name, background_block=self.bg, **kw), self.system,
                        model or self.model)

    def daily(self, day, digest: str) -> str:
        return self._run(prompts.DAILY, self.daily_model, day=f"{day:%A %Y-%m-%d}", digest=digest)

    def weekly(self, week: str, start, end, notes: str) -> str:
        return self._run(prompts.WEEKLY, week=week, start=start, end=end, notes=notes)

    def last_week(self, today, prev_start, prev_end, this_start, notes: str) -> str:
        draft = self._run(prompts.LAST_WEEK, today=today, weekday=f"{today:%A}", prev_start=prev_start,
                          prev_end=prev_end, this_start=this_start, notes=notes)
        # second pass: fact-check and re-file the draft against the notes it came from
        checked = self._run(prompts.VERIFY, draft=draft, notes=notes, prev_start=prev_start, prev_end=prev_end)
        return checked if "## Completed last week" in checked else draft

    def all_time(self, today, weeks: str, current: str) -> str:
        return self._run(prompts.ALL_TIME, today=today, weeks=weeks, current=current or "(none)")

    def glossary(self, notes: str) -> str:
        return claude_p(prompts.GLOSSARY.format(notes=notes), "Output only what is asked.", "haiku").splitlines()[0]
