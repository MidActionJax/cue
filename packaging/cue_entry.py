"""PyInstaller entry point for Cue.exe / cue-cli.exe (see cue/cli.py for the commands)."""
import sys

from cue.cli import main

if __name__ == "__main__":
    sys.exit(main())
