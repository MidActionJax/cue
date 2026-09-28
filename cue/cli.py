"""One entry point for everything, so the compiled Cue.exe can do it all:

    Cue.exe                      the app
    Cue.exe worklog [--backfill] rebuild your work brief (what the 7:30 task runs)
    Cue.exe note "text"          quick work note
    Cue.exe prep                 build interview prep
    Cue.exe accent-test FILE     compare Whisper models on a recording
    Cue.exe devices              list audio devices
    Cue.exe notes finalize|recover ...   (internal: finishing a call's notes)

From source the same commands are `python -m cue.cli <command> ...`.
"""
from __future__ import annotations

import sys

from .config import APP_DIR, FROZEN


def self_cmd(*args: str) -> list[str]:
    """argv that re-runs Cue with a subcommand, frozen or from source."""
    if FROZEN:
        return [sys.executable, *args]
    return [sys.executable, "-m", "cue.cli", *args]


def self_cwd() -> str:
    return str(APP_DIR)


def main() -> int:
    args = sys.argv[1:]
    cmd = args[0] if args and not args[0].startswith("-") else "app"
    rest = args[1:] if cmd != "app" else args

    if cmd == "app":
        from .__main__ import main as app_main
        sys.argv = ["cue", *rest]
        return app_main() or 0
    if cmd == "worklog":
        sys.path.insert(0, str(APP_DIR))
        from worklog.__main__ import main as worklog_main
        sys.argv = ["worklog", *rest]
        return worklog_main()
    if cmd == "prep":
        from worklog.interview_prep import main as prep_main
        return prep_main()
    if cmd == "note":
        from .journal import add
        add(" ".join(rest))
        print("noted.")
        return 0
    if cmd == "notes":
        from .notes import cli as notes_cli
        return notes_cli(rest)
    if cmd == "accent-test":
        from .accent_test import main as accent_main
        sys.argv = ["accent_test", *rest]
        accent_main()
        return 0
    if cmd == "devices":
        from .audio import list_devices
        list_devices()
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
