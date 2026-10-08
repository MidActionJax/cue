"""Your documents: PDFs, Word files and text you add so answers can draw on them.

    profiles/docs/             used on every call
    profiles/docs/work/        only on Work calls        (same for interview/ and sbir/)

Add them from the tray (Add documents…), by dragging files onto the panel, or with
`cue-cli docs add <files> [--scope work|interview|sbir]`. Text is extracted once and cached in
data/docs_cache/, so switching modes stays instant. Documents that fit the budget go into the
answer model's context whole; bigger ones are split into passages and the ones that match what
was just asked are attached to each request.
"""
from __future__ import annotations

import logging
import math
import re
import shutil
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .config import DATA, ROOT

log = logging.getLogger(__name__)

DOCS = ROOT / "profiles" / "docs"
CACHE = DATA / "docs_cache"
SCOPES = ("all", "work", "interview", "sbir")
SUPPORTED = {".pdf", ".docx", ".txt", ".md"}
CANT_READ = {
    ".doc": "old Word format: open it in Word and Save As .docx (or PDF), then add that",
    ".rtf": "rich text: open it in Word and Save As .docx, then add that",
    ".pages": "Apple Pages: export it as PDF or .docx, then add that",
    ".pptx": "PowerPoint isn't supported yet: export it as PDF, then add that",
    ".xlsx": "spreadsheets aren't supported: export the sheet you need as PDF, then add that",
}
MAX_BYTES = 200 * 1024 * 1024
WORD = re.compile(r"[a-z0-9]+(?:['.-][a-z0-9]+)*")
STOP = set("""a an and are as at be been but by can could did do does for from had has have how i if in into
is it its just me my no not of on or our out so than that the their them then there these they this to
up us was we were what when where which who why will with would you your yeah okay ok um uh like also
about know think going get got one some any really very more""".split())


class DocError(Exception):
    """A document that can't be used, with a message meant for the user."""


@dataclass
class Doc:
    name: str
    scope: str
    path: Path

    @property
    def label(self) -> str:
        return self.name if self.scope == "all" else f"{self.name} ({self.scope} calls)"


def folder(scope: str) -> Path:
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES}")
    return DOCS if scope == "all" else DOCS / scope


# ------------------------------------------------------------------ text extraction
def extract(path: Path) -> str:
    """Plain text of a document. Raises DocError with a user-facing reason when it can't."""
    ext = path.suffix.lower()
    if ext in CANT_READ:
        raise DocError(f"{path.name}: {CANT_READ[ext]}")
    if ext not in SUPPORTED:
        raise DocError(f"{path.name}: only PDF, Word (.docx) and text files can be added")
    if not path.is_file():
        raise DocError(f"{path.name}: file not found")
    if path.stat().st_size > MAX_BYTES:
        raise DocError(f"{path.name}: larger than {MAX_BYTES // 2**20} MB")
    if ext == ".pdf":
        text = _pdf(path)
    elif ext == ".docx":
        text = _docx(path)
    else:
        text = _plain(path)
    text = _tidy(text)
    if not re.search(r"[A-Za-z]{2,}", text):
        if ext == ".pdf":
            raise DocError(f"{path.name}: no selectable text. It's probably a scan or a picture of "
                           "text; export it with text (or run OCR) and add it again")
        raise DocError(f"{path.name}: the file has no readable text")
    return text


def _pdf(path: Path) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            try:
                ok = reader.decrypt("")  # many "protected" PDFs only restrict printing/editing
            except Exception:
                ok = 0
            if not ok:
                raise DocError(f"{path.name}: password-protected. Save an unprotected copy and add that")
        pages = []
        for i, page in enumerate(reader.pages, 1):
            try:
                t = page.extract_text() or ""
            except Exception as e:  # one bad page shouldn't sink the document
                log.warning("%s page %d unreadable: %s", path.name, i, e)
                t = ""
            if t.strip():
                pages.append(f"[page {i}]\n{t}")
        return "\n\n".join(pages)
    except DocError:
        raise
    except (PdfReadError, ValueError, KeyError, OSError) as e:
        raise DocError(f"{path.name}: couldn't open this PDF ({e.__class__.__name__}); it may be damaged") from e


def _docx(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            parts = ["word/document.xml"] + sorted(n for n in names if re.match(r"word/(header|footer|footnotes)\d*\.xml", n))
            xmls = [z.read(p).decode("utf-8", errors="replace") for p in parts if p in names]
    except (zipfile.BadZipFile, KeyError, OSError) as e:
        raise DocError(f"{path.name}: couldn't open this Word file; it may be damaged or not really .docx") from e
    if not xmls:
        raise DocError(f"{path.name}: no document body found")
    out = []
    for xml in xmls:
        xml = re.sub(r"<w:tab/>", "\t", xml)
        # a table cell's paragraphs stay on one line, so each row reads "a | b | c"
        xml = re.sub(r"<w:tc[ >].*?</w:tc>", lambda m: re.sub(r"</w:p>|<w:br[^>]*/>", " ", m.group(0)) + "\x1f",
                     xml, flags=re.S)
        xml = re.sub(r"<w:br[^>]*/>|</w:p>|</w:tr>", "\n", xml)
        text = _unescape(re.sub(r"<[^>]+>", "", xml))
        text = re.sub(r"[ \t]*\x1f[ \t]*(?=\n|$)", "", text)      # no separator after a row's last cell
        out.append(text.replace("\x1f", " | "))
    return "\n".join(out)


def _unescape(s: str) -> str:
    import html
    return html.unescape(s)


def _plain(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _tidy(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ------------------------------------------------------------------ cache + library
def _cache_path(path: Path) -> Path:
    st = path.stat()
    key = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{path.parent.name}_{path.name}")
    return CACHE / f"{key}-{st.st_size}-{int(st.st_mtime)}.txt"


def cached_text(path: Path) -> str | None:
    try:
        c = _cache_path(path)
    except OSError:
        return None
    return c.read_text(encoding="utf-8") if c.exists() else None


def ensure_cached(path: Path) -> str:
    """Extract (once) and cache. Raises DocError."""
    text = cached_text(path)
    if text is None:
        text = extract(path)
        CACHE.mkdir(parents=True, exist_ok=True)
        stale = re.sub(r"-\d+-\d+\.txt$", "", _cache_path(path).name)
        for old in CACHE.glob(f"{stale}-*.txt"):   # older versions of the same file
            old.unlink(missing_ok=True)
        _cache_path(path).write_text(text, encoding="utf-8")
    return text


def list_docs() -> list[Doc]:
    out = []
    for scope in SCOPES:
        d = folder(scope)
        if d.is_dir():
            out += [Doc(f.name, scope, f) for f in sorted(d.iterdir(), key=lambda f: f.name.lower())
                    if f.is_file() and f.suffix.lower() in SUPPORTED and f.name.lower() != "readme.md"
                    and not f.name.startswith(("~$", "."))]   # Word's lock files, hidden files
    return out


def for_mode(mode: str | None) -> list[Doc]:
    return [d for d in list_docs() if d.scope == "all" or d.scope == mode]


def add(src: Path, scope: str = "all") -> tuple[Doc, str]:
    """Copy a file into the library and extract its text. Returns (doc, note). Raises DocError;
    a file that can't be read is not left in the library."""
    src = Path(src)
    text = extract(src)                      # fail before copying anything
    dest_dir = folder(scope)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    note = "added"
    if dest.exists():
        if dest.read_bytes() == src.read_bytes():
            ensure_cached(dest)
            return Doc(dest.name, scope, dest), "already added"
        note = "updated (replaced the older version)"
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)
    CACHE.mkdir(parents=True, exist_ok=True)
    stale = re.sub(r"-\d+-\d+\.txt$", "", _cache_path(dest).name)
    for old in CACHE.glob(f"{stale}-*.txt"):
        old.unlink(missing_ok=True)
    _cache_path(dest).write_text(text, encoding="utf-8")
    pages = len(re.findall(r"^\[page \d+\]$", text, re.M))
    size = f"{pages} pages" if pages else f"{len(text.split()):,} words"
    return Doc(dest.name, scope, dest), f"{note}, {size}"


def remove(doc: Doc) -> None:
    try:
        stale = re.sub(r"-\d+-\d+\.txt$", "", _cache_path(doc.path).name)
        for old in CACHE.glob(f"{stale}-*.txt"):
            old.unlink(missing_ok=True)
    except OSError:
        pass
    doc.path.unlink(missing_ok=True)


# ------------------------------------------------------------------ context for the answer model
@dataclass
class DocContext:
    whole: str                 # documents included in full (system prompt)
    retriever: "Retriever | None"
    used: list[str]            # labels, for the log
    missing: list[Doc]         # not extracted yet (dropped into the folder by hand)
    problems: list[str]        # files that can't be read, with the reason


def build(mode: str | None, budget_chars: int) -> DocContext:
    """Whole documents, smallest first, until the budget is used; the rest are searchable."""
    texts, missing, problems = [], [], []
    for d in for_mode(mode):
        t = cached_text(d.path)
        if t is None:
            missing.append(d)
        else:
            texts.append((d, t))
    texts.sort(key=lambda dt: len(dt[1]))
    whole, big, used = [], [], []
    left = budget_chars
    for d, t in texts:
        if len(t) <= left:
            whole.append(f"=== {d.name} ===\n{t}")
            left -= len(t)
            used.append(d.label)
        else:
            big.append((d, t))
            used.append(f"{d.label} [searched]")
    return DocContext("\n\n".join(whole), Retriever(big) if big else None, used, missing, problems)


class Retriever:
    """BM25 over ~1,200-character passages of the documents too big to include whole."""

    def __init__(self, docs: list[tuple[Doc, str]], size: int = 1200, overlap: int = 200):
        from bisect import bisect_right
        self.chunks: list[tuple[str, str]] = []
        for d, text in docs:
            marks = [(m.start(), m.group(1)) for m in re.finditer(r"\[page (\d+)\]", text)]
            starts = [p for p, _ in marks]
            for start in range(0, len(text), size - overlap):
                piece = text[start:start + size]
                # the page this passage starts on (or the first page that begins inside it)
                i = bisect_right(starts, start + size // 2) - 1
                page = f", page {marks[i][1]}" if i >= 0 else ""
                self.chunks.append((f"{d.name}{page}", piece))
        self.tf = [Counter(w for w in WORD.findall(c.lower()) if w not in STOP) for _, c in self.chunks]
        self.lens = [sum(t.values()) for t in self.tf]
        self.avg = (sum(self.lens) / len(self.lens)) if self.lens else 1
        df = Counter(w for t in self.tf for w in t)
        n = len(self.tf)
        self.idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}
        self.names = sorted({d.name for d, _ in docs})

    def query(self, text: str, k: int = 4, max_chars: int = 5000, min_score: float = 2.0) -> str:
        q = [w for w in WORD.findall(text.lower()) if w not in STOP and w in self.idf]
        if not q:
            return ""
        qc = Counter(q)
        scores = []
        for i, tf in enumerate(self.tf):
            s = 0.0
            for w, qn in qc.items():
                f = tf.get(w)
                if f:
                    s += self.idf[w] * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * self.lens[i] / self.avg))
            if s >= min_score:
                scores.append((s, i))
        out, used = [], 0
        for _, i in sorted(scores, reverse=True)[:k]:
            where, piece = self.chunks[i]
            if used + len(piece) > max_chars:
                break
            out.append(f"[{where}]\n{piece.strip()}")
            used += len(piece)
        return "\n\n".join(out)
