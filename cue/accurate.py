"""The accurate after-call pass: re-transcribe the whole call and work out who spoke when.

Live transcription has to guess from 2-15 s chunks with no context, which is the worst case for
heavy accents, and it labels speakers one chunk at a time. After the call there's time to do it
properly: the other side's audio (recorded during the call, see audio.CallRecorder) goes through
the large model with full context, and every passage's voice fingerprint is matched against the
people you've named across the whole call at once, with a cost for switching speakers, so one
person isn't split between two similar voices. Your own lines come from the live transcript.

The result is data/calls/<stamp>.accurate.txt, in the same format as the live transcript; the
final notes are written from it. The audio is deleted once the notes are done (notes.finalize).
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta

import numpy as np

from .config import DATA

log = logging.getLogger(__name__)
CALLS = DATA / "calls"
SR = 16000


def pcm_path(stamp: str):
    return CALLS / f"{stamp}.them.pcm"


def out_path(stamp: str):
    return CALLS / f"{stamp}.accurate.txt"


def run(stamp: str, cfg, meta: dict, live: str, mode: str) -> str | None:
    """The accurate transcript (cached), or None to fall back to the live one."""
    cached = out_path(stamp)
    if cached.exists():
        return cached.read_text(encoding="utf-8")
    pcm = pcm_path(stamp)
    started = (meta.get("audio") or {}).get("started")
    if not started and pcm.with_suffix(".start").exists():
        started = float(pcm.with_suffix(".start").read_text(encoding="utf-8"))
    if not pcm.exists() or not started or pcm.stat().st_size < SR * 2 * 10:
        return None
    t0 = time.time()
    audio = np.fromfile(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    chunks = transcribe(audio, cfg, mode)
    if not chunks:
        log.warning("accurate pass: no speech found in %s", pcm.name)
        return None
    labels = diarize(audio, chunks)
    # corrections like "Sam" -> "Priya" carry over; live "Speaker N" numbers don't match ours
    renames = {k: v for k, v in (meta.get("speakers") or {}).items() if v and not k.startswith("Speaker ")}
    them = [(started + s, f"THEM ({renames.get(lab, lab)})", text) for (s, e, text), lab in zip(chunks, labels)]
    text = merge(them, live, datetime.fromisoformat(meta["started"]), started)
    cached.write_text(text, encoding="utf-8")
    names = sorted({renames.get(l, l) for l in labels})
    log.info("accurate pass for %s: %d passages from %.0f min of audio in %.0f s; speakers %s",
             stamp, len(chunks), len(audio) / SR / 60, time.time() - t0, names)
    return text


# ------------------------------------------------------------------ transcription
def transcribe(audio: np.ndarray, cfg, mode: str) -> list[tuple[float, float, str]]:
    """[(start_s, end_s, text)], split at pauses so a passage is (almost always) one speaker."""
    from faster_whisper import WhisperModel

    from .context import load_vocabulary
    from .stt import _HALLUCINATIONS, _has_words

    model_name = (cfg.notes.get("accurate_model") or "large-v3")
    model = WhisperModel(model_name, device=cfg.stt.device, compute_type=cfg.stt.compute_type)
    vocab = load_vocabulary(cfg.stt.vocabulary, mode)[:60]
    segments, _info = model.transcribe(
        audio, language=cfg.stt.language or None, beam_size=5,
        vad_filter=True, vad_parameters={"min_silence_duration_ms": 400, "speech_pad_ms": 200},
        condition_on_previous_text=True, initial_prompt=(", ".join(vocab) + ".") if vocab else None,
        word_timestamps=True, hallucination_silence_threshold=2.0,
        temperature=[0.0, 0.2, 0.4, 0.6], compression_ratio_threshold=2.4, no_speech_threshold=0.6)
    out = []
    for seg in segments:
        words = list(seg.words or [])
        if not words:
            if _has_words(seg.text):
                out.append((seg.start, seg.end, seg.text.strip()))
            continue
        cur = [words[0]]
        for w in words[1:]:
            if w.start - cur[-1].end > 0.7:   # a pause: often where the speaker changes
                out.append(_join(cur))
                cur = []
            cur.append(w)
        out.append(_join(cur))
    keep = []
    for s, e, t in out:
        words = re.findall(r"[a-z0-9']+", t.lower())
        if not _has_words(t) or (_HALLUCINATIONS.match(t) and e - s < 2.5):
            continue
        if len(words) >= 8 and len(set(words)) / len(words) < 0.35:   # a repetition loop
            continue
        keep.append((s, e, t))
    return keep


def _join(words) -> tuple[float, float, str]:
    return words[0].start, words[-1].end, "".join(w.word for w in words).strip()


# ------------------------------------------------------------------ who spoke when
SWITCH_COST = 0.12      # a speaker change has to be worth this much similarity
CLUSTER_SIM = 0.62      # a passage joins a voice group at this similarity (different people ~0.5)
MERGE_SIM = 0.75        # two groups this alike are the same person
MIN_VOICE_S = 4.0       # less speech than this isn't a voice of its own (a cough, a crosstalk blip)
NAME_SIM = 0.55         # a group's average voice must match a saved person this well to get their name


def diarize(audio: np.ndarray, chunks: list[tuple[float, float, str]]) -> list[str]:
    """1) group the whole call into voices, 2) name each group after the saved person it matches
    best (each person at most once), 3) smooth: a speaker change must be worth it."""
    from .speakers import Speakers

    spk = Speakers()
    embs, weights, durs = [], [], []
    for s, e, _t in chunks:
        a, b = int(s * SR), int(e * SR)
        if b - a < SR * 1.5:   # widen short passages a little: fingerprints need ~1.5 s
            mid = (a + b) // 2
            a, b = max(0, mid - int(SR * 0.75)), min(len(audio), mid + int(SR * 0.75))
        v = spk.embed(audio[a:b]) if b - a >= SR else None
        embs.append(v)
        weights.append(0.0 if v is None else min(1.0, (e - s) / 3.0))
        durs.append(e - s)
    have = [i for i, v in enumerate(embs) if v is not None]
    if not have:
        return ["Speaker 1"] * len(chunks)

    # 1) voice groups: seed from the longest (most reliable) passages, then refine k-means style
    groups: list[list[int]] = []
    for i in sorted(have, key=lambda i: -durs[i]):
        sims = [float(_centroid([embs[j] for j in g]) @ embs[i]) for g in groups]
        if sims and max(sims) >= CLUSTER_SIM:
            groups[int(np.argmax(sims))].append(i)
        else:
            groups.append([i])
    for _ in range(4):
        cents = [_centroid([embs[j] for j in g]) for g in groups]
        merged = True
        while merged and len(cents) > 1:      # same person split in two: merge
            merged = False
            for a in range(len(cents)):
                for b in range(a + 1, len(cents)):
                    if float(cents[a] @ cents[b]) >= MERGE_SIM:
                        groups[a] += groups.pop(b)
                        cents = [_centroid([embs[j] for j in g]) for g in groups]
                        merged = True
                        break
                if merged:
                    break
        keep = [c for c, g in zip(cents, groups) if sum(durs[j] for j in g) >= MIN_VOICE_S] or cents
        groups = [[] for _ in keep]
        for i in have:                         # everyone back to their nearest voice
            groups[int(np.argmax([float(c @ embs[i]) for c in keep]))].append(i)
        groups = [g for g in groups if g]
    cents = [_centroid([embs[j] for j in g]) for g in groups]

    # 2) names: best matches first, each saved person and each group used once
    pairs = sorted(((float(v @ c), n, gi) for n, v in spk.known.items() for gi, c in enumerate(cents)), reverse=True)
    names: dict[int, str] = {}
    used: set[str] = set()
    for sim, n, gi in pairs:
        if sim < NAME_SIM:
            break
        if gi not in names and n not in used:
            names[gi] = n
            used.add(n)
    unnamed = iter(range(1, 100))
    labels = [names.get(gi) or f"Speaker {next(unnamed)}" for gi in range(len(cents))]

    # 3) Viterbi over the voice groups: best label sequence = sum of (weighted) similarities minus
    # a cost per switch; a long pause makes a switch cheaper (turns change at pauses)
    k = len(labels)
    names = labels
    V = np.array(cents, dtype=np.float32)
    score = np.full(k, 0.0)
    back = []
    for i, v in enumerate(embs):
        emit = (V @ v) * weights[i] if v is not None else np.zeros(k)
        cost = SWITCH_COST * (0.5 if i and chunks[i][0] - chunks[i - 1][1] > 2.0 else 1.0)
        if i == 0:
            score = emit
            back.append(np.arange(k))
            continue
        stay = score
        switch = score.max() - cost
        best_prev = np.where(stay >= switch, np.arange(k), int(score.argmax()))
        score = np.maximum(stay, switch) + emit
        back.append(best_prev)
    path = [int(score.argmax())]
    for i in range(len(embs) - 1, 0, -1):
        path.append(int(back[i][path[-1]]))
    path.reverse()
    return [names[p] for p in path]


def _centroid(vs) -> np.ndarray:
    c = np.mean(vs, axis=0)
    return c / (np.linalg.norm(c) + 1e-9)


# ------------------------------------------------------------------ merge with your live lines
_LINE = re.compile(r"^(\d\d):(\d\d):(\d\d) (.+?): (.*)$")


def merge(them: list[tuple[float, str, str]], live: str, call_start: datetime, audio_start: float) -> str:
    """Accurate THEM passages + your own live lines (and anything said before recording began)."""
    rows = [(t, who, text) for t, who, text in them]
    for line in live.splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        who, text = m.group(4), m.group(5)
        if "ASSIST" in who:
            continue
        when = call_start.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=int(m.group(3)), microsecond=0)
        if when < call_start - timedelta(hours=1):
            when += timedelta(days=1)            # the call ran past midnight
        ts = when.timestamp()
        if who.startswith("THEM") and ts >= audio_start - 2:
            continue                             # the accurate pass has the other side from here on
        rows.append((ts, who, text))
    rows.sort(key=lambda r: r[0])
    return "\n".join(f"{datetime.fromtimestamp(t):%H:%M:%S} {who}: {text}" for t, who, text in rows) + "\n"
