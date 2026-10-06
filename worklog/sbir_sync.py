"""Import your SBIR pipeline spreadsheets into profiles/sbir/contacts.md.

    cue-cli sbir-sync              (also runs at the end of the daily job when sbir.tracker_dir is set)

Reads every .xlsx in sbir.tracker_dir read-only (values only, the files are never written).
Headers are matched loosely (Organization/Org, Contact, Email, Status, State...); anything else in
a row is kept as free text under that contact. Rows nobody has been contacted about yet (no
status, or only "drafted") are left out. Writes:

    profiles/sbir/contacts.md      grouped: On a list / Talking / Waiting on reply / Closed
    profiles/sbir/glossary.txt     people and org names, so Whisper stops mis-hearing them

Your own notes go in profiles/sbir/contacts_manual.md, which this never touches.
"""
from __future__ import annotations

import logging
import re
import sys
from datetime import date, datetime
from fnmatch import fnmatch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # running from source

from cue import config  # noqa: E402

SBIR = config.ROOT / "profiles" / "sbir"
OUT = SBIR / "contacts.md"
GLOSSARY = SBIR / "glossary.txt"
log = logging.getLogger("sbir_sync")

SKIP_SHEETS = re.compile(r"read this first|to verify later|summary|phase 0 money|^notes$", re.I)
FIELDS = {  # canonical field -> header patterns (first match wins)
    "org": r"^(organi[sz]ation|org|program|firm|company|name)\b",
    "state": r"^(state|location)\b",
    "contact": r"^(contact|person|lead)\b",
    "email": r"e-?mail",
    "status": r"^(status|outcome|reply)\b",
    "group": r"^(group|tier|type|role|what list|what they could)",
    "next": r"^next",
    "date": r"^(date|last touch|sent)\b",
}
TERMS = ["SBIR", "STTR", "TABA", "SBDC", "I-Corps", "NSF", "NIH", "DOE", "NOAA", "Project Pitch", "Phase I",
         "Phase II", "Phase 0", "FAST", "SAM.gov", "APEX Accelerator"]
STATES = {"AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
          "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
          "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
          "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
          "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
          "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
          "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
          "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
          "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
          "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia", "PR": "Puerto Rico"}
GROUPS = ["On a list", "Talking", "Waiting on reply", "Closed"]
CLOSED = re.compile(r"closed|declin|not a fit|no budget|another consultant|contracted provider|do not contact|"
                    r"dead|passed on|no thanks|unsubscribe|bounced", re.I)
ON_LIST = re.compile(r"on (their |the |an? )?(informal |internal )?(referral |provider |short )*list|added (me|you|jax)"
                     r"|listed|confirmed|catalog", re.I)
TALKING = re.compile(r"call|meeting|booked|replied|talking|asked|offered|live relationship|relationship|"
                     r"interested|passing|intro|scheduled|wants", re.I)


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return f"{v:%Y-%m-%d}"
    if isinstance(v, date):
        return f"{v:%Y-%m-%d}"
    return re.sub(r"\s+", " ", str(v)).strip()


def _map_headers(headers: list[str]) -> dict[int, str]:
    out, taken = {}, set()
    for i, h in enumerate(headers):
        for field, pat in FIELDS.items():
            if field not in taken and re.search(pat, h.strip(), re.I):
                out[i] = field
                taken.add(field)
                break
    return out


def _state(s: str) -> str:
    s = s.strip()
    return STATES.get(s.upper(), s) if s else ""


def read_rows(folder: Path, only: list[str] = ()) -> list[dict]:
    import openpyxl
    rows = []
    for f in sorted(folder.glob("*.xlsx")):
        if f.name.startswith(("~$", ".~")) or (only and not any(fnmatch(f.name, pat) for pat in only)):
            continue
        try:
            wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
        except Exception as e:
            log.warning("skipping %s: %s", f.name, e)
            continue
        for ws in wb.worksheets:
            if SKIP_SHEETS.search(ws.title):
                continue
            data = [[_cell(c) for c in r] for r in ws.iter_rows(values_only=True)]
            data = [r for r in data if any(r)]
            hi = next((i for i, r in enumerate(data[:5])
                       if any(re.search(FIELDS["org"], c, re.I) for c in r)), None)
            if hi is None:
                continue
            headers = data[hi]
            fmap = _map_headers(headers)
            if "org" not in fmap.values():
                continue
            for r in data[hi + 1:]:
                rec = {"sheet": ws.title, "file": f.name, "extra": []}
                for i, v in enumerate(r):
                    if not v:
                        continue
                    field = fmap.get(i)
                    if field and field not in rec:
                        rec[field] = v
                    elif i < len(headers) and headers[i]:
                        rec["extra"].append(f"{headers[i]}: {v}")
                if rec.get("org"):
                    rows.append(rec)
        wb.close()
    return rows


def _group(rec: dict) -> str | None:
    status = " ".join([rec.get("status", ""), rec.get("group", "")])
    everything = " ".join([status, *rec["extra"]])
    if CLOSED.search(rec.get("status", "")):
        return "Closed"
    if re.search(r"on lists?", rec["sheet"], re.I) or ON_LIST.search(rec.get("status", "")):
        return "On a list"
    if not rec.get("status") or re.match(r"\s*drafted\b", rec.get("status", ""), re.I):
        sent = any(re.match(r"(Sent|FU1)\b", x) and re.search(r"\d", x) for x in rec["extra"]) or rec.get("date")
        if not sent:
            return None   # never contacted: not useful on a call
    if TALKING.search(rec.get("status", "")) or re.search(r"pending", rec["sheet"], re.I):
        return "Talking"
    return "Waiting on reply" if everything.strip() else None


def _keys(rec: dict) -> set[str]:
    """Ways the same org shows up across sheets: its name, its email, or an acronym like (ABC)."""
    org = rec["org"].lower()
    keys = {re.sub(r"[^a-z0-9]+", " ", re.sub(r"\(.*?\)", "", org)).strip()}
    keys |= {f"acr:{a.strip()}" for a in re.findall(r"\(([a-z][a-z .-]{1,10})\)", org) if a.strip() not in STATES_LC}
    if rec.get("email") and "@" in rec["email"]:
        keys.add(f"mail:{rec['email'].lower()}")
    return {k for k in keys if k}


STATES_LC = {s.lower() for s in STATES.values()} | {s.lower() for s in STATES}


def build(rows: list[dict]) -> tuple[str, list[str]]:
    merged: dict[int, dict] = {}
    index: dict[str, int] = {}
    rank = {g: i for i, g in enumerate(GROUPS)}
    for rec in rows:
        g = _group(rec)
        if g is None:
            continue
        keys = _keys(rec)
        mid = next((index[k] for k in keys if k in index), None)
        if mid is None:
            mid = len(merged)
            merged[mid] = {"org": rec["org"], "group": g, "lines": [], "sources": set(), "state": "",
                           "contact": "", "email": ""}
        for k in keys:
            index.setdefault(k, mid)
        m = merged[mid]
        if rec.get("state", "").strip("- "):
            rec["state"] = rec["state"].strip()
        else:
            rec.pop("state", None)
        if rank[g] < rank[m["group"]] or g == "Closed":   # on a list beats talking; closed is final
            m["group"] = g if g != "Closed" or m["group"] != "On a list" else m["group"]
        for f in ("state", "contact", "email"):
            if rec.get(f) and not m[f]:
                m[f] = rec[f]
        m["sources"].add(f"{rec['file']} › {rec['sheet']}")
        for label, f in (("Status", "status"), ("Role / list", "group"), ("Next", "next"), ("Date", "date")):
            if rec.get(f):
                line = f"{label}: {rec[f]}"
                if line not in m["lines"]:
                    m["lines"].append(line)
        for x in rec["extra"]:
            if x not in m["lines"]:
                m["lines"].append(x)

    out = [f"# SBIR contacts (generated {date.today()} by cue-cli sbir-sync, do not edit)",
           "", "Edit your own notes in contacts_manual.md; this file is rebuilt from the spreadsheets.", ""]
    names: list[str] = []
    for g in GROUPS:
        items = sorted((m for m in merged.values() if m["group"] == g), key=lambda m: (_state(m["state"]), m["org"]))
        if not items:
            continue
        out += [f"## {g}", ""]
        for m in items:
            st = _state(m["state"])
            out.append(f"### {m['org']}" + (f" ({st})" if st and st.lower() not in m["org"].lower() else ""))
            if m["contact"]:
                out.append(f"- Contact: {m['contact']}" + (f" <{m['email']}>" if m["email"] else ""))
            elif m["email"]:
                out.append(f"- Email: {m['email']}")
            out += [f"- {l[:400]}" for l in m["lines"]]
            out.append("")
            if g != "Closed":
                names.append(re.sub(r"\s*\(.*?\)\s*", " ", m["org"]).strip())
                contact = re.sub(r"\(.*?\)|\S+@\S+", " ", m["contact"])
                for person in re.split(r"[;/]| and |\bcc\b", contact):
                    person = re.sub(r"\s+", " ", person.split(",")[0]).strip()
                    # a real name: 2-4 capitalized words (not "Hub inbox", "replied", job titles)
                    if 2 <= len(person.split()) <= 4 and all(w[:1].isupper() for w in person.split()) \
                            and not re.search(r"inbox|sales|general|info|office|team", person, re.I):
                        names.append(person)
    seen, vocab = set(), []
    for w in TERMS + names:
        if w and w.lower() not in seen:
            seen.add(w.lower())
            vocab.append(w)
    return "\n".join(out).rstrip() + "\n", vocab


def sync(folder: str | Path) -> int:
    folder = Path(folder)
    if not folder.is_dir():
        log.error("tracker folder not found: %s", folder)
        return 1
    only = list((config.load().get("sbir") or {}).get("tracker_files") or [])
    rows = read_rows(folder, only)
    md, vocab = build(rows)
    SBIR.mkdir(parents=True, exist_ok=True)
    OUT.write_text(md, encoding="utf-8")
    GLOSSARY.write_text("\n".join(vocab) + "\n", encoding="utf-8")
    counts = {g: md.count(f"\n## {g}\n") and len(re.findall(r"^### ", md.split(f"## {g}", 1)[1].split("\n## ", 1)[0], re.M))
              for g in GROUPS if f"## {g}" in md}
    log.info("contacts.md from %d rows: %s; %d glossary words", len(rows), counts, len(vocab))
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname).1s %(message)s", datefmt="%H:%M:%S")
    argv = sys.argv[1:] if argv is None else argv
    cfg = config.load()
    folder = argv[0] if argv else (cfg.get("sbir") or {}).get("tracker_dir", "")
    if not folder:
        print("Set sbir.tracker_dir in config.yaml (the folder with your tracker .xlsx files), "
              "or pass it: cue-cli sbir-sync <folder>")
        return 2
    return sync(folder)


if __name__ == "__main__":
    sys.exit(main())
