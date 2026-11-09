"""Speech-to-text engines behind one interface.

    model = load("large-v3-turbo", device="cuda", compute_type="float16")
    segments, info = model.transcribe(audio_float32_16k, language="en", beam_size=5, ...)
    for s in segments: s.start, s.end, s.text, s.avg_logprob, s.no_speech_prob, s.compression_ratio, s.words

Windows / Linux: faster-whisper (CTranslate2; CUDA when there's an NVIDIA GPU).
macOS (Apple Silicon): MLX Whisper on the GPU (there's no CUDA on a Mac, and faster-whisper on
the CPU is far too slow for large models live). Same Whisper models, converted to MLX.
"""
from __future__ import annotations

import logging
import sys
import threading
from dataclasses import dataclass, field

import numpy as np

log = logging.getLogger(__name__)
IS_MAC = sys.platform == "darwin"

# Whisper model names -> MLX conversions on Hugging Face (mlx-community)
MLX_REPOS = {
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "turbo": "mlx-community/whisper-large-v3-turbo",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "large-v2": "mlx-community/whisper-large-v2-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "medium.en": "mlx-community/whisper-medium.en-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "small.en": "mlx-community/whisper-small.en-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "base.en": "mlx-community/whisper-base.en-mlx",
    "tiny": "mlx-community/whisper-tiny-mlx",
    "tiny.en": "mlx-community/whisper-tiny.en-mlx",
}


def engine() -> str:
    return "mlx" if IS_MAC else "faster-whisper"


def load(name: str, device: str = "cuda", compute_type: str = "float16"):
    """A model with faster-whisper's transcribe() interface."""
    if IS_MAC:
        return MlxWhisper(name)
    from faster_whisper import WhisperModel
    return WhisperModel(name, device=device, compute_type=compute_type)


def load_cpu_fallback():
    """When the GPU model can't load: something small that runs anywhere."""
    if IS_MAC:
        return MlxWhisper("small")
    from faster_whisper import WhisperModel
    return WhisperModel("small", device="cpu", compute_type="int8")


@dataclass
class Word:
    start: float
    end: float
    word: str
    probability: float = 0.0


@dataclass
class Segment:
    start: float
    end: float
    text: str
    avg_logprob: float = 0.0
    no_speech_prob: float = 0.0
    compression_ratio: float = 1.0
    words: list[Word] = field(default_factory=list)


@dataclass
class Info:
    language: str = "en"
    duration: float = 0.0


class MlxWhisper:
    """MLX Whisper with faster-whisper's call shape. The weights are loaded once and kept (the
    library's own cache only holds one model), and calls are serialized: MLX runs one graph at a
    time on the GPU anyway."""

    _lock = threading.Lock()

    def __init__(self, name: str):
        import mlx.core as mx
        from mlx_whisper.load_models import load_model
        self.name = name
        self.repo = MLX_REPOS.get(name, name)
        # downloads into the Hugging Face cache the first time (HF_HOME, in Cue's data folder), offline after
        self.model = load_model(self.repo, dtype=mx.float16)

    def transcribe(self, audio, language=None, beam_size=None, initial_prompt=None,
                   condition_on_previous_text=True, without_timestamps=False, vad_filter=False,
                   vad_parameters=None, temperature=0.0, word_timestamps=False,
                   hallucination_silence_threshold=None, compression_ratio_threshold=2.4,
                   no_speech_threshold=0.6, **_ignored):
        """Same arguments as faster-whisper's (beam search, VAD and without_timestamps aren't
        available in MLX Whisper: greedy decoding with temperature fallback instead)."""
        import importlib
        # the package's `transcribe` attribute is the function, which hides the module of the same name
        tmod = importlib.import_module("mlx_whisper.transcribe")
        mlx_transcribe = tmod.transcribe

        audio = np.asarray(audio, dtype=np.float32)
        kwargs = dict(language=language, initial_prompt=initial_prompt,
                      condition_on_previous_text=condition_on_previous_text,
                      temperature=tuple(temperature) if isinstance(temperature, (list, tuple)) else temperature,
                      word_timestamps=word_timestamps, compression_ratio_threshold=compression_ratio_threshold,
                      no_speech_threshold=no_speech_threshold, verbose=None)
        if hallucination_silence_threshold is not None:
            kwargs["hallucination_silence_threshold"] = hallucination_silence_threshold
        with self._lock:
            # hand our loaded model to the library's one-slot cache, so nothing is reloaded
            tmod.ModelHolder.model, tmod.ModelHolder.model_path = self.model, self.repo
            result = mlx_transcribe(audio, path_or_hf_repo=self.repo, **kwargs)
        segs = []
        for s in result.get("segments", []):
            words = [Word(w.get("start", 0.0), w.get("end", 0.0), w.get("word", ""), w.get("probability", 0.0))
                     for w in s.get("words", []) or []]
            segs.append(Segment(s.get("start", 0.0), s.get("end", 0.0), s.get("text", ""), s.get("avg_logprob", 0.0),
                                s.get("no_speech_prob", 0.0), s.get("compression_ratio", 1.0), words))
        return iter(segs), Info(result.get("language") or language or "en", len(audio) / 16000)
