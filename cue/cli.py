"""One entry point for everything, so the compiled Cue.exe can do it all:

    Cue.exe                      the app
    Cue.exe worklog [--backfill] rebuild your work brief (what the 7:30 task runs)
    Cue.exe note "text"          quick work note
    Cue.exe prep                 build interview prep
    Cue.exe prep-sbir "Pat Lee"   prep sheet for an SBIR call (person or organization)
    Cue.exe sbir-sync            import your SBIR tracker spreadsheets into contacts.md
    Cue.exe docs add FILE...     add PDFs / Word / text as answer context (--scope work|interview|sbir)
    Cue.exe docs list | remove NAME
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
    if cmd == "prep-sbir":
        from worklog.sbir_prep import main as sbir_prep_main
        return sbir_prep_main(rest)
    if cmd == "sbir-sync":
        from worklog.sbir_sync import main as sbir_sync_main
        return sbir_sync_main(rest)
    if cmd == "docs":
        return _docs(rest)
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


def _docs(args: list[str]) -> int:
    """cue-cli docs list | add FILE... [--scope all|work|interview|sbir] | remove NAME"""
    from pathlib import Path

    from . import docs
    if not args or args[0] == "list":
        found = docs.list_docs()
        for d in found:
            text = docs.cached_text(d.path)
            print(f"  {d.label:<60} {len(text):>9,} chars" if text is not None else f"  {d.label:<60} (not read yet)")
        print(f"{len(found)} document(s) in {docs.DOCS}")
        return 0
    if args[0] == "add":
        scope, files = "all", []
        it = iter(args[1:])
        for a in it:
            if a == "--scope":
                scope = next(it, "all")
            else:
                files.append(a)
        if scope not in docs.SCOPES or not files:
            print(_docs.__doc__)
            return 2
        bad = 0
        for f in files:
            try:
                d, note = docs.add(Path(f), scope)
                print(f"  {d.label}: {note}")
            except docs.DocError as e:
                bad += 1
                print(f"  NOT ADDED: {e}")
        return 1 if bad else 0
    if args[0] == "remove" and len(args) > 1:
        name = " ".join(args[1:]).lower()
        hits = [d for d in docs.list_docs() if d.name.lower() == name] or \
               [d for d in docs.list_docs() if name in d.name.lower()]
        if len(hits) != 1:
            print(f"{'No' if not hits else 'More than one'} document matches {name!r}")
            return 1
        docs.remove(hits[0])
        print(f"  removed {hits[0].label}")
        return 0
    print(_docs.__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
