"""Voice fingerprints (speaker embeddings, sherpa-onnx + WeSpeaker ResNet34, CPU ~50 ms).

Two jobs:
  * YOUR mic: is this utterance really you, or someone else in the room? Only your own speech
    counts as "you" for learning and the transcript.
  * CALL audio: who's talking? Online clustering into Speaker 1, 2, ... and matching against
    people you've named before (profiles/speakers/<Name>.npy), so notes say "Sam asked…".
"""
from __future__ import annotations

import logging
import re
import threading

import numpy as np

from .config import BUNDLED_MODELS, DATA, ROOT

log = logging.getLogger(__name__)
_MODEL_NAME = "wespeaker_en_voxceleb_resnet34_LM.onnx"
# shipped inside the app; older setups downloaded it into the data folder
MODEL = next((p for p in (BUNDLED_MODELS / _MODEL_NAME, DATA / "models" / _MODEL_NAME) if p.exists()),
             BUNDLED_MODELS / _MODEL_NAME)
PROFILES = ROOT / "profiles" / "speakers"
ME_FILE = PROFILES / "_me.npy"


def _unit(v: np.ndarray) -> np.ndarray:
    return v / (np.linalg.norm(v) + 1e-9)


def saved_voices() -> list[str]:
    return sorted(f.stem.replace("_", " ") for f in PROFILES.glob("*.npy") if not f.stem.startswith("_")) \
        if PROFILES.exists() else []


def forget_voice(name: str) -> bool:
    """Move a saved voice print aside (data/voice_backup/), so it's no longer matched but can be restored."""
    import shutil
    import time as _time
    f = PROFILES / f"{name.replace(' ', '_')}.npy"
    if not f.exists():
        return False
    backup = DATA / "voice_backup"
    backup.mkdir(parents=True, exist_ok=True)
    shutil.move(str(f), str(backup / f"{f.stem}-{_time.strftime('%Y%m%d-%H%M%S')}.npy"))
    log.info("forgot the saved voice for %s (backup in %s)", name, backup)
    return True


class Speakers:
    MIN_S = 1.0          # shorter clips give unreliable fingerprints
    NEW_SPEAKER_MIN_S = 3.0   # need this much speech before deciding it's someone new

    # cosine similarity; same person ~0.7-0.95, different people usually < 0.5
    def __init__(self, me_threshold: float = 0.55, match_threshold: float = 0.6, new_threshold: float = 0.55):
        import sherpa_onnx
        self._ex = sherpa_onnx.SpeakerEmbeddingExtractor(
            sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(MODEL), num_threads=2))
        self.me_threshold, self.match_threshold, self.new_threshold = me_threshold, match_threshold, new_threshold
        self._lock = threading.Lock()
        self.me: np.ndarray | None = _unit(np.load(ME_FILE)) if ME_FILE.exists() else None
        self._me_samples: list[np.ndarray] = []
        self._me_seconds = 0.0
        # known people from earlier calls
        self.known: dict[str, np.ndarray] = {}
        for f in PROFILES.glob("*.npy") if PROFILES.exists() else []:
            if not f.stem.startswith("_"):
                self.known[f.stem.replace("_", " ")] = _unit(np.load(f))
        # this call's unnamed voices: label -> (centroid sum, count)
        self._clusters: dict[str, list] = {}
        self._last_them = "Speaker 1"
        self._fixed: dict[str, str] = {}     # corrections for this call: matched name -> real person

    @staticmethod
    def available() -> bool:
        try:
            import sherpa_onnx  # noqa: F401
            return MODEL.exists()
        except ImportError:
            return False

    def embed(self, audio: np.ndarray) -> np.ndarray | None:
        if len(audio) < 16000 * self.MIN_S:
            return None
        s = self._ex.create_stream()
        s.accept_waveform(16000, audio.astype(np.float32))
        s.input_finished()
        return _unit(np.array(self._ex.compute(s), dtype=np.float32))

    # ------------------------------------------------------------------ your mic
    def is_me(self, audio: np.ndarray) -> tuple[bool, float | None, np.ndarray | None]:
        """(is it you?, similarity, embedding). Without a voice profile everything counts as you."""
        e = self.embed(audio)
        if e is None or self.me is None:
            return True, None, e
        sim = float(self.me @ e)
        return sim >= self.me_threshold, sim, e

    def add_me_sample(self, e: np.ndarray, seconds: float) -> None:
        with self._lock:
            self._me_samples.append(e)
            self._me_seconds += seconds

    def me_seconds_collected(self) -> float:
        return self._me_seconds

    def save_me(self, blend_existing: bool = True) -> bool:
        """Average collected samples into your voice profile (blended with the old one)."""
        with self._lock:
            if not self._me_samples:
                return False
            new = _unit(np.mean(self._me_samples, axis=0))
            if blend_existing and self.me is not None:
                new = _unit(0.7 * self.me + 0.3 * new)
            self.me = new
            self._me_samples, self._me_seconds = [], 0.0
        PROFILES.mkdir(parents=True, exist_ok=True)
        np.save(ME_FILE, self.me)
        return True

    # ------------------------------------------------------------------ call audio
    # Switching away from whoever is talking needs this much better a match (a 2-5 s clip is a
    # noisy fingerprint; two similar voices used to flip back and forth mid-sentence).
    SWITCH_MARGIN = 0.08
    SWITCH_MARGIN_SHORT = 0.15   # clips under 2.5 s
    CONTINUE_SIM = 0.5           # at least this like the current speaker = could still be them
                                 # (different people score well under 0.5 against each other)

    def correct(self, wrong: str, right: str) -> None:
        """A saved voice matched the wrong person on this call: label it as `right` from now on."""
        with self._lock:
            self._fixed[wrong] = right
            if self._last_them == wrong:
                self._last_them = right

    def _score(self, label: str, e: np.ndarray) -> float | None:
        if label not in self.known and label not in self._clusters:
            # a corrected name with no saved voice of its own: score it by the voice it replaced
            label = next((k for k, v in self._fixed.items() if v == label and k in self.known), label)
        if label in self.known:
            return float(self.known[label] @ e)
        if label in self._clusters:
            return float(_unit(self._clusters[label][0]) @ e)
        return None

    def who(self, audio: np.ndarray) -> str:
        """Name (if known), or 'Speaker N' for a voice new to this call."""
        e = self.embed(audio)
        if e is None:
            return self._last_them  # too short to tell: most likely the person already talking
        with self._lock:
            seconds = len(audio) / 16000
            prev = self._last_them
            ps = self._score(prev, e)
            if ps is not None and ps >= self.CONTINUE_SIM:
                # whoever is talking probably still is: only switch to a voice that's clearly better
                # (checked before a new "Speaker N" can be created, which would match itself perfectly)
                margin = self.SWITCH_MARGIN_SHORT if seconds < 2.5 else self.SWITCH_MARGIN
                rivals = [(float(c @ e), n) for n, c in self.known.items() if self._fixed.get(n, n) != prev]
                rivals += [(float(_unit(t) @ e), n) for n, (t, _c) in self._clusters.items() if n != prev]
                best = max(rivals, default=(-1.0, None))
                if best[0] - ps < margin:
                    if prev in self._clusters:
                        self._clusters[prev][0] = self._clusters[prev][0] + e
                        self._clusters[prev][1] += 1
                    return prev
            label = self._who(e, seconds)
            label = self._fixed.get(label, label)   # "that's actually Priya", corrected this call
            self._last_them = label
            return label

    def _who(self, e: np.ndarray, seconds: float) -> str:
        """Best label for one fingerprint, ignoring who was talking before (caller holds the lock)."""
        best, score = None, -1.0
        for name, c in self.known.items():
            s = float(c @ e)
            if s > score:
                best, score = name, s
        if best and score >= self.match_threshold:
            return best
        best, score = None, -1.0
        for label, (total, _n) in self._clusters.items():
            s = float(_unit(total) @ e)
            if s > score:
                best, score = label, s
        if best and score >= self.new_threshold:
            self._clusters[best][0] = self._clusters[best][0] + e
            self._clusters[best][1] += 1
            return best
        if best and seconds < self.NEW_SPEAKER_MIN_S:
            # a short fragment is too little evidence for a new person (real call: six
            # one-line "speakers" appeared that way) — file it with the closest voice
            return best
        label = f"Speaker {len(self._clusters) + 1}"
        self._clusters[label] = [e.copy(), 1]
        return label

    def forget(self, name: str) -> bool:
        """Drop a saved voice (e.g. it was learned from the wrong person). Kept in data/voice_backup."""
        with self._lock:
            self.known.pop(name, None)
        return forget_voice(name)

    def name_speaker(self, label: str, name: str) -> None:
        """You told us 'Speaker 2' is Sam: remember the voice for future calls."""
        name = re.sub(r"[^\w .'-]", "", name).strip()
        with self._lock:
            cluster = self._clusters.pop(label, None)
            if cluster is None or not name:
                return
            centroid = _unit(cluster[0])
            if name in self.known:
                centroid = _unit(self.known[name] + centroid)
            self.known[name] = centroid
        PROFILES.mkdir(parents=True, exist_ok=True)
        np.save(PROFILES / f"{name.replace(' ', '_')}.npy", centroid)
        log.info("saved voice for %s", name)
