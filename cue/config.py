"""Loads config.yaml into attribute-accessible nested dicts."""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


class Cfg(dict):
    """dict with attribute access, recursively."""

    def __getattr__(self, key):
        try:
            value = self[key]
        except KeyError as e:
            raise AttributeError(key) from e
        return Cfg(value) if isinstance(value, dict) else value

    def __setattr__(self, key, value):
        self[key] = value


# Your own copies are created from the shipped templates on first run and stay out of git.
USER_FILES = [
    ("config.example.yaml", "config.yaml"),
    ("profiles/me.example.md", "profiles/me.md"),
    ("profiles/work/notes.example.md", "profiles/work/notes.md"),
    ("profiles/work/workstreams.example.md", "profiles/work/workstreams.md"),
]


def ensure_user_files() -> None:
    import shutil
    for src, dst in USER_FILES:
        s, d = ROOT / src, ROOT / dst
        if s.exists() and not d.exists():
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(s, d)


def load(path: Path | None = None) -> Cfg:
    if path is None:
        ensure_user_files()
    path = path or ROOT / "config.yaml"
    with open(path, encoding="utf-8") as f:
        return Cfg(yaml.safe_load(f))


def resolve(p: str | Path) -> Path:
    p = Path(p)
    return p if p.is_absolute() else ROOT / p
