"""Loads the background-context files for the active mode (resume, work log, notes...)."""
from __future__ import annotations

import logging
import re
import zipfile
from pathlib import Path

from .config import resolve

log = logging.getLogger(__name__)

TEXT_EXT = {".md", ".txt"}
DOC_EXT = TEXT_EXT | {".pdf", ".docx"}


def _read(path: Path) -> str:
    ext = path.suffix.lower()
    try:
        if ext in TEXT_EXT:
            return path.read_text(encoding="utf-8", errors="replace")
        if ext == ".pdf":
            from pypdf import PdfReader
            return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
        if ext == ".docx":
            with zipfile.ZipFile(path) as z:
                xml = z.read("word/document.xml").decode("utf-8", errors="replace")
            xml = re.sub(r"</w:p>", "\n", xml)
            return re.sub(r"<[^>]+>", "", xml)
    except Exception as e:
        log.warning("could not read %s: %s", path, e)
    return ""


def _is_placeholder(text: str) -> bool:
    return "<!-- template -->" in text


def load_context(entries: list[str]) -> tuple[str, list[str]]:
    """Returns (combined text, list of files actually used)."""
    blocks, used = [], []
    for entry in entries:
        p = resolve(entry)
        files = sorted(f for f in p.rglob("*") if f.suffix.lower() in DOC_EXT) if p.is_dir() else [p]
        for f in files:
            if not f.exists() or f.name.lower() == "readme.md":
                continue
            text = _read(f).strip()
            if not text or _is_placeholder(text):
                continue
            blocks.append(f"=== {f.name} ===\n{text}")
            used.append(str(f.relative_to(resolve("."))) if f.is_relative_to(resolve(".")) else str(f))
    return "\n\n".join(blocks), used


def load_vocabulary(extra: list[str]) -> list[str]:
    words = list(extra or [])
    glossary = resolve("profiles/work/glossary.txt")
    if glossary.exists():
        for w in re.split(r"[,\n]", glossary.read_text(encoding="utf-8")):
            w = w.strip()
            if w and w not in words:
                words.append(w)
    return words
