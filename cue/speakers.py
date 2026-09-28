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
    def who(self, audio: np.ndarray) -> str:
        """Name (if known), or 'Speaker N' for a voice new to this call."""
        e = self.embed(audio)
        if e is None:
            return self._last_them  # too short to tell: most likely the person already talking
        with self._lock:
            best, score = None, -1.0
            for name, c in self.known.items():
                s = float(c @ e)
                if s > score:
                    best, score = name, s
            if best and score >= self.match_threshold:
                label = best
            else:
                best, score = None, -1.0
                for label_, (total, n) in self._clusters.items():
                    s = float(_unit(total) @ e)
                    if s > score:
                        best, score = label_, s
                if best and score >= self.new_threshold:
                    label = best
                    self._clusters[label][0] = self._clusters[label][0] + e
                    self._clusters[label][1] += 1
                elif best and len(audio) < 16000 * self.NEW_SPEAKER_MIN_S:
                    # a short fragment is too little evidence for a new person (real call: six
                    # one-line "speakers" appeared that way) — file it with the closest voice
                    label = best
                else:
                    label = f"Speaker {len(self._clusters) + 1}"
                    self._clusters[label] = [e.copy(), 1]
            self._last_them = label
            return label

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
