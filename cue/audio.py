"""Audio capture (WASAPI loopback + mic) and VAD-based utterance segmentation.

Both sources are normalized to 16 kHz mono int16 in 30 ms frames, which is what both
webrtcvad and Whisper want. Each source gets its own Segmenter that turns frames into
utterances and reports speaking/silent state changes.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pyaudiowpatch as pyaudio
import soxr
import webrtcvad

log = logging.getLogger(__name__)

SR = 16000
FRAME_MS = 30
FRAME = SR * FRAME_MS // 1000  # 480 samples


def _wasapi_inputs(p: pyaudio.PyAudio, loopback: bool) -> list[dict]:
    wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)["index"]
    devs = p.get_loopback_device_info_generator() if loopback else \
        (p.get_device_info_by_index(i) for i in range(p.get_device_count()))
    return [d for d in devs if d["hostApi"] == wasapi and d["maxInputChannels"] > 0
            and bool(d.get("isLoopbackDevice")) == loopback]


def _find_device(p: pyaudio.PyAudio, want: str, loopback: bool) -> dict:
    if want and want != "default":
        for dev in _wasapi_inputs(p, loopback):
            if want.lower() in dev["name"].lower():
                return dev
        log.warning("device %r not found, using default", want)
    if loopback:
        return p.get_default_wasapi_loopback()
    wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
    return p.get_device_info_by_index(wasapi["defaultInputDevice"])


def default_endpoint_names() -> tuple[str, str]:
    """Windows' current default output and input device names (they change when you plug in a
    headset). Needs COM initialized on the calling thread."""
    from pycaw.pycaw import AudioUtilities

    def name(dev):
        return getattr(dev, "FriendlyName", None) or AudioUtilities.CreateDevice(dev).FriendlyName

    return name(AudioUtilities.GetSpeakers()), name(AudioUtilities.GetMicrophone())


def list_devices() -> None:
    p = pyaudio.PyAudio()
    try:
        print("Call audio / loopback (what you hear):")
        for d in _wasapi_inputs(p, True):
            print(f"  {d['name']}")
        print("Microphones:")
        for d in _wasapi_inputs(p, False):
            print(f"  {d['name']}")
        print("Default loopback:", p.get_default_wasapi_loopback()["name"])
    finally:
        p.terminate()


class Capture:
    """Streams one device into a queue of 16 kHz int16 frames. Reopenable: the queue (and so
    the Segmenter reading it) survives device switches."""

    def __init__(self, name: str, loopback: bool):
        self.name, self.loopback = name, loopback
        self.frames: queue.Queue[tuple[float, np.ndarray]] = queue.Queue(maxsize=2000)
        self._stream = None
        self._pending = np.zeros(0, dtype=np.int16)
        self.device_name = "none"
        self.level = 0.0          # RMS of the latest chunk (for the level indicator)
        self.last_frame = 0.0     # monotonic time audio last arrived

    def open(self, pa: pyaudio.PyAudio, dev: dict) -> None:
        self.device_name = dev["name"]
        rate = int(dev["defaultSampleRate"])
        self._channels = max(1, min(2, int(dev["maxInputChannels"])))
        self._resampler = soxr.ResampleStream(rate, SR, 1, dtype="float32", quality="HQ")
        self._pending = np.zeros(0, dtype=np.int16)
        self._stream = pa.open(
            format=pyaudio.paFloat32, channels=self._channels, rate=rate, input=True,
            input_device_index=dev["index"], frames_per_buffer=int(rate * 0.02),
            stream_callback=self._callback,
        )
        log.info("%s capture: %s (%d Hz, %d ch)", self.name, self.device_name, rate, self._channels)

    def alive(self) -> bool:
        try:
            return self._stream is not None and self._stream.is_active()
        except Exception:
            return False

    def _callback(self, data, frame_count, time_info, status):
        audio = np.frombuffer(data, dtype=np.float32)
        if self._channels > 1:
            audio = audio.reshape(-1, self._channels).mean(axis=1)
        audio = self._resampler.resample_chunk(audio)
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
        return (None, pyaudio.paContinue)

    def close(self) -> None:
        try:
            if self._stream:
                self._stream.stop_stream()
                self._stream.close()
        except Exception:
            pass
        self._stream = None


class AudioManager:
    """Owns both captures. Reopens them when a stream dies or, for devices set to "default",
    when Windows switches the default device (headset plugged in / unplugged mid-call)."""

    def __init__(self, loopback: str = "default", mic: str = "default"):
        self.want = {"them": loopback or "default", "me": mic or "default"}
        self.them = Capture("them", loopback=True)
        self.me = Capture("me", loopback=False)
        self._pa: pyaudio.PyAudio | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.on_change: Callable[[str], None] | None = None

    def start(self) -> None:
        self._open()
        threading.Thread(target=self._watch, name="audio-watch", daemon=True).start()

    def _open(self) -> None:
        self._pa = pyaudio.PyAudio()
        for cap in (self.them, self.me):
            try:
                cap.open(self._pa, _find_device(self._pa, self.want[cap.name], cap.loopback))
            except Exception as e:
                cap.device_name = "unavailable"
                log.error("could not open %s audio: %s", cap.name, e)

    def restart(self, reason: str) -> None:
        with self._lock:
            log.info("reopening audio devices (%s)", reason)
            for cap in (self.them, self.me):
                cap.close()
            if self._pa:
                self._pa.terminate()  # PortAudio only re-scans devices after a full terminate
            self._open()
        if self.on_change:
            self.on_change(reason)

    def set_device(self, source: str, name: str) -> None:
        self.want[source] = name
        self.restart(f"{source} device -> {name}")

    def devices(self) -> tuple[list[str], list[str]]:
        with self._lock:
            if not self._pa:
                return [], []
            return ([d["name"] for d in _wasapi_inputs(self._pa, True)],
                    [d["name"] for d in _wasapi_inputs(self._pa, False)])

    def _watch(self) -> None:
        try:
            import comtypes
            comtypes.CoInitialize()
        except Exception:
            pass
        while not self._stop.wait(3):
            try:
                dead = [c.name for c in (self.them, self.me) if c.device_name != "unavailable" and not c.alive()]
                if dead:
                    self.restart(f"{', '.join(dead)} stream stopped")
                    continue
                if "default" in self.want.values():
                    out_dev, in_dev = default_endpoint_names()
                    if (self.want["them"] == "default" and not self.them.device_name.startswith(out_dev)) or \
                       (self.want["me"] == "default" and not self.me.device_name.startswith(in_dev)):
                        self.restart(f"Windows default changed -> {out_dev} / {in_dev}")
            except Exception as e:
                log.debug("audio watch: %s", e)

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            for cap in (self.them, self.me):
                cap.close()
            if self._pa:
                self._pa.terminate()


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
