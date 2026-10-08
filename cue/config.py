"""Paths + config loading.

Two places matter:
  APP_DIR  where Cue's code and bundled files live (the repo, or the installed app folder).
           Read-only: assets, templates, bundled models.
  ROOT     your data home: config.yaml, profiles/, meetings/, data/ (transcripts, logs, caches),
           models/ (Whisper downloads). Chosen by, in order:
             1. the CUE_HOME environment variable
             2. a cue_home.txt file next to Cue.exe / in the repo, containing a folder path
             3. the repo itself (running from source) or %USERPROFILE%\\Cue (installed app)
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import yaml

FROZEN = bool(getattr(sys, "frozen", False))
APP_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
EXE_DIR = Path(sys.executable).resolve().parent if FROZEN else APP_DIR


def _find_home() -> Path:
    if os.environ.get("CUE_HOME"):
        return Path(os.environ["CUE_HOME"])
    for d in (EXE_DIR, APP_DIR):
        pointer = d / "cue_home.txt"
        if pointer.exists():
            text = pointer.read_text(encoding="utf-8-sig").strip()  # the installer writes a BOM
            if text:
                return Path(text)
    return Path.home() / "Cue" if FROZEN else APP_DIR


ROOT = _find_home()
DATA = ROOT / "data"
ASSETS = APP_DIR / "assets"
BUNDLED_MODELS = APP_DIR / "models"
# Whisper models download into the data home (can be GBs; keeps them off the system drive)
os.environ.setdefault("HF_HOME", str(ROOT / "models" / "hf"))


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
    ("profiles/interview/README.md", "profiles/interview/README.md"),
    ("profiles/sbir/README.md", "profiles/sbir/README.md"),
    ("profiles/docs/README.md", "profiles/docs/README.md"),
    ("profiles/sbir/offer.example.md", "profiles/sbir/offer.md"),
    ("profiles/sbir/proof.example.md", "profiles/sbir/proof.md"),
    ("profiles/sbir/rules.example.md", "profiles/sbir/rules.md"),
    ("profiles/sbir/faq.example.md", "profiles/sbir/faq.md"),
]


def _fill_missing(mine: dict, defaults: dict) -> dict:
    """Settings added in newer versions (e.g. a new mode) come from the shipped example, so an
    older config.yaml keeps working. Your values always win; lists aren't merged."""
    for k, v in defaults.items():
        if k not in mine:
            mine[k] = v
        elif isinstance(v, dict) and isinstance(mine[k], dict):
            _fill_missing(mine[k], v)
    return mine


def ensure_user_files() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    for src, dst in USER_FILES:
        s, d = APP_DIR / src, ROOT / dst
        if s.exists() and not d.exists():
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(s, d)


def load(path: Path | None = None) -> Cfg:
    if path is None:
        ensure_user_files()
    path = path or ROOT / "config.yaml"
    with open(path, encoding="utf-8") as f:
        mine = yaml.safe_load(f) or {}
    example = APP_DIR / "config.example.yaml"
    if example.exists() and Path(path).resolve() != example.resolve():
        with open(example, encoding="utf-8") as f:
            _fill_missing(mine, yaml.safe_load(f) or {})
    return Cfg(mine)


def resolve(p: str | Path) -> Path:
    """Paths in config.yaml are relative to your data home."""
    p = Path(p)
    return p if p.is_absolute() else ROOT / p
