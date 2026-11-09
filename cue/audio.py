"""Audio capture (call audio + mic) and VAD-based utterance segmentation.

Both sources are normalized to 16 kHz mono int16 in 30 ms frames, which is what both
webrtcvad and Whisper want. Each source gets its own Segmenter that turns frames into
utterances and reports speaking/silent state changes.

Where the audio comes from is per OS (cue/plat/audio_win.py, audio_mac.py):
    Windows  call audio = WASAPI loopback of the output device; mic = WASAPI input
    macOS    call audio = ScreenCaptureKit system audio (or an input device like BlackHole);
             mic = CoreAudio input via PortAudio
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import soxr
import webrtcvad

log = logging.getLogger(__name__)

SR = 16000
FRAME_MS = 30
FRAME = SR * FRAME_MS // 1000  # 480 samples


def _backend():
    from .plat import IS_MAC
    if IS_MAC:
        from .plat.audio_mac import MacBackend
        return MacBackend()
    from .plat.audio_win import WinBackend
    return WinBackend()


def list_devices() -> None:
    _backend().list_devices()


class Capture:
    """One source as a queue of 16 kHz int16 frames. A backend opens the device, calls begin()
    with its sample rate, then feeds ingest() from its audio callback. Reopenable: the queue (and
    so the Segmenter reading it) survives device switches."""

    def __init__(self, name: str, loopback: bool):
        self.name, self.loopback = name, loopback
        self.frames: queue.Queue[tuple[float, np.ndarray]] = queue.Queue(maxsize=2000)
        self.handle = None        # the backend's open stream: alive() / close()
        self._pending = np.zeros(0, dtype=np.int16)
        self._resampler = None
        self.device_name = "none"
        self.level = 0.0          # RMS of the latest chunk (for the level indicator)
        self.last_frame = 0.0     # monotonic time audio last arrived

    def begin(self, rate: int) -> None:
        self._resampler = soxr.ResampleStream(rate, SR, 1, dtype="float32", quality="HQ")
        self._pending = np.zeros(0, dtype=np.int16)

    def ingest(self, audio: np.ndarray) -> None:
        """float32 mono at the device's rate (from the backend's audio callback)."""
        if self._resampler is None:
            return
        audio = self._resampler.resample_chunk(np.ascontiguousarray(audio, dtype=np.float32))
        now = time.monotonic()
        if len(audio):
            self.level = float(np.sqrt(np.mean(audio ** 2)))
            self.last_frame = now
        pcm = np.clip(audio * 32767, -32768, 32767).astype(np.int16)
        buf = np.concatenate([self._pending, pcm])
        n = len(buf) // FRAME
        for i in range(n):
            try:
                self.frames.put_nowait((now, buf[i * FRAME:(i + 1) * FRAME]))
            except queue.Full:
                pass
        self._pending = buf[n * FRAME:]

    def alive(self) -> bool:
        try:
            return self.handle is not None and self.handle.alive()
        except Exception:
            return False

    def close(self) -> None:
        try:
            if self.handle:
                self.handle.close()
        except Exception:
            pass
        self.handle = None


class AudioManager:
    """Owns both captures. Reopens them when a stream dies or, for devices set to "default",
    when the OS switches the default device (headset plugged in / unplugged mid-call)."""

    def __init__(self, loopback: str = "default", mic: str = "default"):
        self.want = {"them": loopback or "default", "me": mic or "default"}
        self.them = Capture("them", loopback=True)
        self.me = Capture("me", loopback=False)
        self.backend = _backend()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.on_change: Callable[[str], None] | None = None

    def start(self) -> None:
        self._open()
        threading.Thread(target=self._watch, name="audio-watch", daemon=True).start()

    def _open(self) -> None:
        self.backend.start()
        for cap in (self.them, self.me):
            try:
                cap.handle = self.backend.open(cap, self.want[cap.name])
            except Exception as e:
                if cap.device_name in ("none", ""):
                    cap.device_name = "unavailable"
                log.error("could not open %s audio: %s", cap.name, e)

    def restart(self, reason: str) -> None:
        with self._lock:
            log.info("reopening audio devices (%s)", reason)
            for cap in (self.them, self.me):
                cap.close()
            self.backend.stop()  # PortAudio only re-scans devices after a full terminate
            self._open()
        if self.on_change:
            self.on_change(reason)

    def set_device(self, source: str, name: str) -> None:
        self.want[source] = name
        self.restart(f"{source} device -> {name}")

    def devices(self) -> tuple[list[str], list[str]]:
        """(call-audio choices, microphones), by name."""
        with self._lock:
            return self.backend.devices()

    def _watch(self) -> None:
        self.backend.watch_init()
        while not self._stop.wait(3):
            try:
                dead = [c.name for c in (self.them, self.me) if c.handle is not None and not c.alive()]
                if dead:
                    self.restart(f"{', '.join(dead)} stream stopped")
                    continue
                if "default" in self.want.values():
                    out_dev, in_dev = self.backend.defaults()
                    if (out_dev and self.want["them"] == "default" and not self.them.device_name.startswith(out_dev)) or \
                       (in_dev and self.want["me"] == "default" and not self.me.device_name.startswith(in_dev)):
                        self.restart(f"default device changed -> {out_dev} / {in_dev}")
            except Exception as e:
                log.debug("audio watch: %s", e)

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            for cap in (self.them, self.me):
                cap.close()
            self.backend.stop()


class CallRecorder:
    """The other side's audio for the accurate after-call pass: raw 16 kHz int16 PCM appended as
    it arrives (nothing to lose if the app dies), with silence written for the gaps WASAPI
    loopback leaves while nobody talks, so offsets in the file line up with wall-clock time.
    Deleted once the call's final notes are written."""

    MAX_GAP_S = 600

    def __init__(self, path):
        self.path = path
        self.started: float | None = None      # wall clock of sample 0
        self._t0: float | None = None
        self._written = 0
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(path, "ab")

    def write(self, t_mono: float, frame: np.ndarray) -> None:
        with self._lock:
            if self._f is None:
                return
            if self._t0 is None:
                self._t0 = t_mono
                self.started = time.time() - (time.monotonic() - t_mono)
                try:  # so a call whose app died can still be re-transcribed later
                    self.path.with_suffix(".start").write_text(repr(self.started), encoding="utf-8")
                except OSError:
                    pass
            behind = int((t_mono - self._t0) * SR) - self._written
            if behind > SR // 4:   # a silent stretch: keep the timeline
                pad = min(behind, SR * self.MAX_GAP_S)
                self._f.write(b"\x00\x00" * pad)
                self._written += pad
            self._f.write(frame.tobytes())
            self._written += len(frame)

    def close(self) -> None:
        with self._lock:
            if self._f:
                self._f.close()
                self._f = None


@dataclass
class Utterance:
    source: str            # "them" | "me"
    utt_id: int
    audio: np.ndarray      # float32 16 kHz
    t_start: float         # time.time() wall clock
    t_end: float
    final: bool
    who: str = ""          # "me" / "room" for your mic; speaker name or "Speaker N" for call audio
    emb: object = None     # voice fingerprint (numpy) when one was computed


@dataclass
class Segmenter:
    """Frames -> utterances, with speaking-state callbacks. Runs in its own thread."""

    source: str
    capture: Capture
    on_utterance: Callable[[Utterance], None]
    on_speaking: Callable[[str, bool], None]
    aggressiveness: int = 2
    silence_s: float = 0.6
    max_utt_s: float = 15.0
    partial_every_s: float = 1.5
    emit_utterances: bool = True
    gate: Callable[[], bool] | None = None   # return True to ignore speech right now (echo guard)
    tap: Callable[[float, np.ndarray], None] | None = None   # every frame, e.g. CallRecorder.write

    _stop: threading.Event = field(default_factory=threading.Event)

    def start(self) -> None:
        threading.Thread(target=self._run, name=f"seg-{self.source}", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        vad = webrtcvad.Vad(self.aggressiveness)
        preroll: list[np.ndarray] = []
        cur: list[np.ndarray] = []
        recent: list[bool] = []
        speaking = False
        in_utt = False
        utt_id = 0
        utt_start_wall = 0.0
        silence_frames = 0
        last_partial = 0.0
        last_frame_t = time.monotonic()
        silence_limit = int(self.silence_s * 1000 / FRAME_MS)
        max_frames = int(self.max_utt_s * 1000 / FRAME_MS)

        def finish(final: bool):
            nonlocal in_utt, cur, silence_frames
            if cur and self.emit_utterances:
                audio = np.concatenate(cur).astype(np.float32) / 32768.0
                self.on_utterance(Utterance(self.source, utt_id, audio, utt_start_wall, time.time(), final))
            if final:
                in_utt, cur, silence_frames = False, [], 0

        while not self._stop.is_set():
            try:
                t, frame = self.capture.frames.get(timeout=0.1)
            except queue.Empty:
                # WASAPI loopback delivers nothing while the system is silent, so time
                # passing without frames counts as silence.
                if in_utt and time.monotonic() - last_frame_t > self.silence_s:
                    finish(final=True)
                if speaking and time.monotonic() - last_frame_t > 0.3:
                    speaking = False
                    self.on_speaking(self.source, False)
                continue
            last_frame_t = time.monotonic()
            tap = self.tap
            if tap:
                try:
                    tap(t, frame)
                except Exception as e:  # recording must never stop the live transcript
                    log.warning("call recording stopped: %s", e)
                    self.tap = None

            is_speech = vad.is_speech(frame.tobytes(), SR)
            if is_speech and self.gate and self.gate():
                is_speech = False
            recent.append(is_speech)
            if len(recent) > 10:
                recent.pop(0)
            # speaking = majority of the last 300 ms, which ignores clicks and breaths
            now_speaking = sum(recent) >= 6 if speaking else sum(recent[-5:]) >= 4
            if now_speaking != speaking:
                speaking = now_speaking
                self.on_speaking(self.source, speaking)

            if not in_utt:
                preroll.append(frame)
                if len(preroll) > 10:
                    preroll.pop(0)
                if speaking:
                    in_utt = True
                    utt_id += 1
                    utt_start_wall = time.time() - len(preroll) * FRAME_MS / 1000
                    cur = list(preroll)
                    preroll = []
                    silence_frames = 0
                    last_partial = time.monotonic()
                continue

            cur.append(frame)
            silence_frames = 0 if is_speech else silence_frames + 1
            if silence_frames >= silence_limit:
                cur = cur[: len(cur) - silence_frames + 5]  # trim trailing silence, keep a little
                finish(final=True)
            elif len(cur) >= max_frames:
                finish(final=True)
                in_utt = True  # continue the monologue as a new utterance
                utt_id += 1
                utt_start_wall = time.time()
                last_partial = time.monotonic()
            elif time.monotonic() - last_partial >= self.partial_every_s:
                last_partial = time.monotonic()
                finish(final=False)
