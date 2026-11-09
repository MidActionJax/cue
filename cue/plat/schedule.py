"""The daily 7:30 work-brief refresh on macOS: a launchd agent in ~/Library/LaunchAgents.

(On Windows the installer registers a Task Scheduler task instead: packaging/register_task.ps1.)

The installed app keeps the agent in step with itself on every start: it's written when missing
or when Cue.app has moved, and removed when `worklog.daily_job` is false. A Mac that was asleep
at 7:30 runs the job when it wakes; one that was off is covered by the app's own catch-up
refresh when it opens.
"""
from __future__ import annotations

import logging
import os
import plistlib
import subprocess
from pathlib import Path

from . import IS_MAC

log = logging.getLogger(__name__)
LABEL = "com.midactionjax.cue.worklog"
AGENT = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def agent_plist(argv: list[str], log_file: Path, hour: int = 7, minute: int = 30) -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": list(argv),
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "StandardOutPath": str(log_file),
        "StandardErrorPath": str(log_file),
        # launchd starts jobs with a bare PATH; the job needs `claude` and `git`
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")},
    }


def _launchctl(*args: str) -> int:
    try:
        return subprocess.run(["launchctl", *args], capture_output=True, timeout=15).returncode
    except Exception as e:
        log.debug("launchctl %s: %s", args, e)
        return -1


def sync_daily_job(enabled: bool) -> str:
    """Install, update or remove the agent so it matches `enabled`. Returns what was done."""
    if not IS_MAC:
        return "not macOS"
    from ..cli import self_cmd
    from ..config import DATA, FROZEN
    if not FROZEN:
        return "running from source: not scheduled (run `python -m cue.cli worklog` yourself)"
    domain = f"gui/{os.getuid()}"
    if not enabled:
        if AGENT.exists():
            _launchctl("bootout", domain, str(AGENT))
            AGENT.unlink(missing_ok=True)
            return "removed"
        return "off"
    want = agent_plist(self_cmd("worklog"), DATA / "logs" / "worklog-daily.log")
    try:
        have = plistlib.loads(AGENT.read_bytes()) if AGENT.exists() else None
    except Exception:
        have = None
    if have == want:
        return "up to date"
    AGENT.parent.mkdir(parents=True, exist_ok=True)
    AGENT.write_bytes(plistlib.dumps(want))
    _launchctl("bootout", domain, str(AGENT))
    rc = _launchctl("bootstrap", domain, str(AGENT))
    log.info("daily work-brief job %s (launchctl rc=%s)", "updated" if have else "installed", rc)
    return "installed" if rc == 0 else f"written, launchctl rc={rc}"
