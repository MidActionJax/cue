"""faster-whisper transcription worker.

One GPU model, one worker thread, prioritized queue:
  finals from the other side  >  your finals  >  live partials (only the newest is kept).
"""
from __future__ import annotations

import itertools
import logging
import queue
import re
import threading
import time
from typing import Callable

from .audio import Utterance

log = logging.getLogger(__name__)

# Things Whisper hallucinates on noise/silence.
_HALLUCINATIONS = re.compile(
    r"^\s*(thank you\.?|thanks( for watching)?[.!]?|you|bye\.?|okay\.?|so\.?|"
    r"please subscribe.*|subtitles by.*|i'm going to show you how to.*|\.+|\[.*\]|\(.*\))\s*$",
    re.I,
)


# A lone "you" / "thank you" utterance is Whisper filling silence, however long the clip.
_FILLER_ONLY = re.compile(r"^\s*(you|thank you|thanks)[.!]?\s*$", re.I)


def _normalize(audio):
    """Bring quiet/far-from-mic speakers up to a consistent level (capped at 10x gain so
    near-silence isn't turned into loud noise). Whisper is noticeably worse on quiet audio."""
    import numpy as np
    peak = float(np.percentile(np.abs(audio), 99.5)) if len(audio) else 0.0
    if peak < 1e-4:
        return audio
    gain = min(10.0, 0.6 / peak)
    return np.clip(audio * gain, -1.0, 1.0).astype("float32")


class Transcriber:
    def __init__(self, cfg, on_text: Callable[[Utterance, str], None], vocabulary: list[str], speakers=None):
        self.cfg = cfg
        self.speakers = speakers           # voice fingerprints (optional)
        self.accurate = None               # slower, more accurate model for hard-to-understand speakers
        self.on_text = on_text
        self.vocabulary = vocabulary
        self.q: queue.PriorityQueue = queue.PriorityQueue()
        self._counter = itertools.count()
        self._latest_partial: dict[str, int] = {}
        self._context = ""  # recent transcript text, fed back as the prompt for continuity
        self.model = None
        self.ready = threading.Event()

    def load(self) -> None:
        from faster_whisper import WhisperModel

        t = time.time()
        try:
            self.model = WhisperModel(self.cfg.model, device=self.cfg.device, compute_type=self.cfg.compute_type)
        except Exception as e:  # CUDA libs missing etc. — still work, just slower
            log.error("Whisper on %s failed (%s); falling back to CPU int8 'small'", self.cfg.device, e)
            self.model = WhisperModel("small", device="cpu", compute_type="int8")
        log.info("Whisper %s loaded in %.1fs", self.cfg.model, time.time() - t)
        # warm-up so the first real utterance isn't slow
        import numpy as np
        list(self.model.transcribe(np.zeros(16000, dtype="float32"), language=self.cfg.language)[0])
        self.ready.set()

    def submit(self, utt: Utterance) -> None:
        if utt.final:
            prio = 0 if utt.source == "them" else 1
        else:
            prio = 2
            self._latest_partial[utt.source] = utt.utt_id
        self.q.put((prio, next(self._counter), utt))

    def start(self) -> None:
        threading.Thread(target=self._run, name="stt", daemon=True).start()

    def _prompt(self) -> str:
        # Written like earlier transcript text, with no label word: on noise Whisper tends to
        # repeat prompt words, and a label like "Glossary:" came back as "Glossary. Glossary."
        vocab = ", ".join(self.vocabulary[:60])
        return " ".join(p for p in (f"{vocab}." if vocab else "", self._context[-300:]) if p)

    def _run(self) -> None:
        self.load()
        while True:
            _, _, utt = self.q.get()
            if not utt.final and self._latest_partial.get(utt.source) != utt.utt_id:
                continue  # stale partial; a newer one (or the final) is coming
            if not utt.final and self.q.qsize() > 3:
                continue  # falling behind — skip partials, keep finals
            try:
                if utt.final and self.speakers:
                    self._label_speaker(utt)
                text = self._transcribe(utt)
            except Exception:
                log.exception("transcription failed")
                continue
            if text:
                if utt.final and utt.source == "them":
                    self._context = (self._context + " " + text)[-600:]
                self.on_text(utt, text)

    def _label_speaker(self, utt: Utterance) -> None:
        if utt.source == "me":
            ok, sim, utt.emb = self.speakers.is_me(utt.audio)
            utt.who = "me" if ok else "room"
            if not ok:
                log.info("mic speech doesn't match your voice (%.2f) — labelled as someone in the room", sim)
        else:
            utt.who = self.speakers.who(utt.audio)

    def _model_for(self, utt: Utterance):
        """Speakers listed in stt.accurate_speakers get the big model (finals only)."""
        wanted = [s.lower() for s in (self.cfg.accurate_speakers or [])]
        if not (utt.final and utt.who and utt.who.lower() in wanted):
            return self.model
        if self.accurate is None:
            from faster_whisper import WhisperModel
            t = time.time()
            self.accurate = WhisperModel(self.cfg.accurate_model, device=self.cfg.device,
                                         compute_type=self.cfg.compute_type)
            log.info("accurate model %s loaded in %.1fs (for %s)", self.cfg.accurate_model, time.time() - t, utt.who)
        return self.accurate

    def _transcribe(self, utt: Utterance) -> str:
        import numpy as np
        dur = len(utt.audio) / 16000
        if dur < (0.6 if utt.source == "me" else 0.3):
            return ""
        # too quiet to be speech aimed at the mic (keyboard, fan, someone across the room)
        if float(np.sqrt(np.mean(utt.audio ** 2))) < self.cfg.min_rms:
            return ""
        segments, _info = self._model_for(utt).transcribe(
            _normalize(utt.audio),
            language=self.cfg.language or None,
            beam_size=self.cfg.beam_size if utt.final else 1,
            # names/jargon + recent context help with the other side's accents; your own mic gets
            # no prompt, since on room noise Whisper just echoes the prompt back
            initial_prompt=(self._prompt() or None) if utt.source == "them" else None,
            condition_on_previous_text=False,
            without_timestamps=True,
            vad_filter=False,
            temperature=0.0 if not utt.final else [0.0, 0.2, 0.4],
        )
        out = []
        for s in segments:
            if (s.no_speech_prob > 0.6 and s.avg_logprob < -0.8) or s.avg_logprob < -1.2 or s.compression_ratio > 2.4:
                continue
            out.append(s.text.strip())
        text = " ".join(out).strip()
        if _HALLUCINATIONS.match(text) and (dur < 2.5 or _FILLER_ONLY.match(text)):
            return ""
        if len(text.split()) / dur > 5.5:  # faster than anyone talks: invented from noise
            return ""
        if self._looks_invented(text):
            return ""
        return text

    def _looks_invented(self, text: str) -> bool:
        """Repetition loops ("Glossary. Glossary. Glossary.") and stretches copied straight out of the prompt.
        (A lone name like "Alex?" is real speech — the name trigger depends on it.)"""
        words = re.findall(r"[a-z0-9_']+", text.lower())
        if len(words) >= 3 and len(set(words)) / len(words) < 0.45:
            return True
        prompt = " " + " ".join(re.findall(r"[a-z0-9_']+", " ".join(self.vocabulary).lower())) + " "
        return len(words) >= 3 and f" {' '.join(words)} " in prompt
