"""Windows audio: WASAPI loopback for the call (what you hear) and WASAPI input for the mic."""
from __future__ import annotations

import logging

import numpy as np
import pyaudiowpatch as pyaudio

log = logging.getLogger(__name__)
DEFAULT_LABELS = ("Windows default (follows headset plug/unplug)", "Windows default (follows headset plug/unplug)")


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


class _Stream:
    def __init__(self, stream):
        self.stream = stream

    def alive(self) -> bool:
        return self.stream.is_active()

    def close(self) -> None:
        self.stream.stop_stream()
        self.stream.close()


class WinBackend:
    def __init__(self):
        self._pa: pyaudio.PyAudio | None = None

    def start(self) -> None:
        self._pa = pyaudio.PyAudio()

    def stop(self) -> None:
        if self._pa:
            self._pa.terminate()
            self._pa = None

    def open(self, cap, want: str) -> _Stream:
        dev = _find_device(self._pa, want, cap.loopback)
        cap.device_name = dev["name"]
        rate = int(dev["defaultSampleRate"])
        channels = max(1, min(2, int(dev["maxInputChannels"])))
        cap.begin(rate)

        def callback(data, frame_count, time_info, status):
            audio = np.frombuffer(data, dtype=np.float32)
            if channels > 1:
                audio = audio.reshape(-1, channels).mean(axis=1)
            cap.ingest(audio)
            return (None, pyaudio.paContinue)

        stream = self._pa.open(format=pyaudio.paFloat32, channels=channels, rate=rate, input=True,
                               input_device_index=dev["index"], frames_per_buffer=int(rate * 0.02),
                               stream_callback=callback)
        log.info("%s capture: %s (%d Hz, %d ch)", cap.name, cap.device_name, rate, channels)
        return _Stream(stream)

    def devices(self) -> tuple[list[str], list[str]]:
        if not self._pa:
            return [], []
        return ([d["name"].replace(" [Loopback]", "") for d in _wasapi_inputs(self._pa, True)],
                [d["name"] for d in _wasapi_inputs(self._pa, False)])

    def watch_init(self) -> None:
        try:
            import comtypes
            comtypes.CoInitialize()
        except Exception:
            pass

    def defaults(self) -> tuple[str | None, str | None]:
        return default_endpoint_names()

    def list_devices(self) -> None:
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
