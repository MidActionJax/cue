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


def load_context(entries: list[str], exclude: list[str] = ()) -> tuple[str, list[str]]:
    """Returns (combined text, list of files actually used)."""
    blocks, used = [], []
    skip = {resolve(e).resolve() for e in exclude}
    for entry in entries:
        p = resolve(entry)
        # a folder's glossary.txt is for Whisper, not the answer model
        files = sorted(f for f in p.rglob("*") if f.suffix.lower() in DOC_EXT and f.name != "glossary.txt") \
            if p.is_dir() else [p]
        for f in files:
            if not f.exists() or f.name.lower() == "readme.md" or f.resolve() in skip:
                continue
            text = _read(f).strip()
            if not text or _is_placeholder(text):
                continue
            blocks.append(f"=== {f.name} ===\n{text}")
            used.append(str(f.relative_to(resolve("."))) if f.is_relative_to(resolve(".")) else str(f))
    return "\n\n".join(blocks), used


GLOSSARIES = ("profiles/work/glossary.txt",   # written by the worklog job
              "profiles/sbir/glossary.txt")   # names from your SBIR trackers (cue-cli sbir-sync)


def _prep_names(path: str) -> list[str]:
    """Proper names from a prep sheet's title and 'Who they are' section: the people on the next call."""
    p = resolve(path)
    if not p.exists():
        return []
    text = p.read_text(encoding="utf-8")
    m = re.search(r"\A(.*?)^## [^\n]*who they are[^\n]*$(.*?)(?=^## |\Z)", text, re.M | re.S | re.I)
    head = (m.group(1) + m.group(2)) if m else text[:600]
    names = re.findall(r"\b(?:[A-Z][a-zA-Z'-]+|[A-Z]{2,})(?:[ \t]+(?:[A-Z][a-zA-Z'-]+|[A-Z]{2,}))*", head)
    skip = {"Prep", "Who", "Contact", "This", "The", "Phase", "Nothing", "Warm", "I"}
    return [n for n in dict.fromkeys(names) if n not in skip and len(n) > 2 and "'" not in n]


def load_vocabulary(extra: list[str], mode: str | None = None) -> list[str]:
    """Words to prime Whisper with. Only the first ~60 fit, so in SBIR mode the people on the
    prepped call and your tracker's names go before everything else."""
    mode_first = mode == "sbir"
    words = _prep_names("profiles/sbir/prep.md")[:15] if mode_first else []
    order = sorted(GLOSSARIES, key=lambda g: f"/{mode}/" not in g) if mode else GLOSSARIES
    sources = []
    for g in order:
        glossary = resolve(g)
        if glossary.exists():
            sources.append([w.strip() for w in re.split(r"[,\n]", glossary.read_text(encoding="utf-8"))])
    if mode_first and sources:
        sources.insert(1, list(extra or []))
    else:
        sources.insert(0, list(extra or []))
    for src in sources:
        for w in src:
            if w and w not in words:
                words.append(w)
    return words
