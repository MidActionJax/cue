"""macOS audio.

Call audio (what you hear): ScreenCaptureKit's system-audio capture (macOS 13+). It needs the
"Screen & System Audio Recording" permission (System Settings > Privacy & Security); macOS asks
the first time, and Cue has to be restarted after it's granted. Alternatively, set the call-audio
device to a loopback input such as BlackHole and route the meeting's output through it.

Mic: CoreAudio input through PortAudio (sounddevice), which needs the Microphone permission.
"""
from __future__ import annotations

import logging
import threading

import numpy as np

log = logging.getLogger(__name__)

SYSTEM_AUDIO = "System audio"
DEFAULT_LABELS = ("System audio (everything you hear)", "System default microphone")
PERMISSION_HELP = ("Cue needs permission to hear the call: System Settings > Privacy & Security > "
                   "Screen & System Audio Recording > turn on Cue, then quit and reopen Cue")


class _SoundDeviceStream:
    def __init__(self, stream):
        self.stream = stream

    def alive(self) -> bool:
        return bool(self.stream.active)

    def close(self) -> None:
        self.stream.stop()
        self.stream.close()


def _input_devices() -> list[dict]:
    import sounddevice as sd
    return [dict(d, index=i) for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]


def _open_input(cap, want: str | None) -> _SoundDeviceStream:
    """A CoreAudio input device (a mic, or a loopback device like BlackHole) by name; default otherwise."""
    import sounddevice as sd
    dev = None
    if want and want not in ("default", SYSTEM_AUDIO):
        dev = next((d for d in _input_devices() if want.lower() in d["name"].lower()), None)
        if dev is None:
            log.warning("device %r not found, using default", want)
    if dev is None:
        idx = sd.default.device[0]
        dev = dict(sd.query_devices(idx), index=idx) if idx is not None and idx >= 0 else \
            dict(sd.query_devices(kind="input"), index=None)
    rate = int(dev["default_samplerate"])
    channels = max(1, min(2, int(dev["max_input_channels"])))
    cap.device_name = dev["name"]
    cap.begin(rate)

    def callback(indata, frames, time_info, status):
        cap.ingest(indata.mean(axis=1) if channels > 1 else indata[:, 0].copy())

    stream = sd.InputStream(device=dev["index"], channels=channels, samplerate=rate, dtype="float32",
                            blocksize=int(rate * 0.02), callback=callback)
    stream.start()
    log.info("%s capture: %s (%d Hz, %d ch)", cap.name, cap.device_name, rate, channels)
    return _SoundDeviceStream(stream)


# --------------------------------------------------------------------------- ScreenCaptureKit
_OutputClass = None


def _output_class():
    """The Objective-C object ScreenCaptureKit hands audio to (defined once per process)."""
    global _OutputClass
    if _OutputClass is None:
        import objc
        from Foundation import NSObject

        class CueSystemAudioOutput(NSObject, protocols=[objc.protocolNamed("SCStreamOutput"),
                                                        objc.protocolNamed("SCStreamDelegate")]):
            def initWithSink_(self, sink):
                self = objc.super(CueSystemAudioOutput, self).init()
                if self is None:
                    return None
                self.sink = sink
                return self

            def stream_didOutputSampleBuffer_ofType_(self, stream, sample_buffer, kind):
                if kind == 1:   # SCStreamOutputTypeAudio (0 = screen frames, ignored)
                    self.sink(sample_buffer, None)

            def stream_didStopWithError_(self, stream, error):
                self.sink(None, error)

        _OutputClass = CueSystemAudioOutput
    return _OutputClass


def pcm_from_sample_buffer(sample_buffer) -> np.ndarray | None:
    """Float32 samples from a CMSampleBuffer (mono: one plane, so the bytes are the samples)."""
    import CoreMedia as CM
    block = CM.CMSampleBufferGetDataBuffer(sample_buffer)
    if block is None:
        return None
    n = CM.CMBlockBufferGetDataLength(block)
    if not n:
        return None
    status, data = CM.CMBlockBufferCopyDataBytes(block, 0, n, None)
    if status != 0 or data is None:
        return None
    return np.frombuffer(bytes(data), dtype=np.float32)


class SystemAudio:
    """Everything the Mac plays (the meeting app included), minus Cue's own sounds."""

    RATE = 48000

    def __init__(self, cap):
        self.cap = cap
        self.stream = None
        self.output = None
        self._alive = False

    def _wait(self, starter, what: str, timeout: float = 15.0):
        done, box = threading.Event(), {}

        def handler(*args):
            box["args"] = args
            done.set()

        starter(handler)
        if not done.wait(timeout):
            raise TimeoutError(f"{what}: no answer from ScreenCaptureKit")
        return box["args"]

    def start(self) -> "SystemAudio":
        import CoreMedia as CM
        import ScreenCaptureKit as SCK
        content, error = self._wait(SCK.SCShareableContent.getShareableContentWithCompletionHandler_,
                                    "listing displays")
        if error is not None or content is None or not content.displays():
            self.cap.device_name = "System audio: needs permission"
            raise PermissionError(f"{PERMISSION_HELP} ({error.localizedDescription() if error else 'no display'})")
        display = content.displays()[0]
        content_filter = SCK.SCContentFilter.alloc().initWithDisplay_excludingWindows_(display, [])
        conf = SCK.SCStreamConfiguration.alloc().init()
        conf.setCapturesAudio_(True)
        conf.setSampleRate_(self.RATE)
        conf.setChannelCount_(1)
        conf.setExcludesCurrentProcessAudio_(True)
        # a stream always carries video too: keep it as small and slow as allowed
        conf.setWidth_(2)
        conf.setHeight_(2)
        conf.setMinimumFrameInterval_(CM.CMTimeMake(1, 1))
        conf.setShowsCursor_(False)
        self.cap.begin(self.RATE)
        self.output = _output_class().alloc().initWithSink_(self._on_buffer)
        self.stream = SCK.SCStream.alloc().initWithFilter_configuration_delegate_(content_filter, conf, self.output)
        for kind in (1, 0):   # audio, and screen frames (ignored) so ScreenCaptureKit doesn't log every dropped frame
            ok, err = self.stream.addStreamOutput_type_sampleHandlerQueue_error_(self.output, kind, None, None)
            if not ok and kind == 1:
                raise RuntimeError(f"system audio output: {err}")
        (error,) = self._wait(self.stream.startCaptureWithCompletionHandler_, "starting capture")
        if error is not None:
            self.cap.device_name = "System audio: needs permission"
            raise PermissionError(f"{PERMISSION_HELP} ({error.localizedDescription()})")
        self._alive = True
        self.cap.device_name = SYSTEM_AUDIO
        log.info("them capture: system audio via ScreenCaptureKit (%d Hz, mono)", self.RATE)
        return self

    def _on_buffer(self, sample_buffer, error) -> None:
        if sample_buffer is None:
            log.warning("system audio capture stopped: %s", error)
            self._alive = False
            return
        try:
            pcm = pcm_from_sample_buffer(sample_buffer)
            if pcm is not None and len(pcm):
                self.cap.ingest(pcm)
        except Exception:
            log.exception("system audio buffer")

    def alive(self) -> bool:
        return self._alive

    def close(self) -> None:
        self._alive = False
        if self.stream is not None:
            try:
                self.stream.stopCaptureWithCompletionHandler_(None)
            except Exception:
                pass
        self.stream = None


class MacBackend:
    def start(self) -> None:
        import sounddevice as sd
        sd._terminate()     # PortAudio only sees newly plugged-in devices after re-initializing
        sd._initialize()

    def stop(self) -> None:
        pass

    def open(self, cap, want: str):
        if cap.loopback and (not want or want in ("default", SYSTEM_AUDIO)):
            return SystemAudio(cap).start()
        return _open_input(cap, want)

    def devices(self) -> tuple[list[str], list[str]]:
        names = [d["name"] for d in _input_devices()]
        # call audio: system audio is the default; any input (e.g. BlackHole) can be picked too
        return names, names

    def watch_init(self) -> None:
        pass

    def defaults(self) -> tuple[str | None, str | None]:
        return None, None   # PortAudio can't see default-device changes without restarting every stream

    def list_devices(self) -> None:
        print("Call audio (what you hear):")
        print(f"  {SYSTEM_AUDIO} (default; needs Screen & System Audio Recording permission)")
        for d in _input_devices():
            if any(k in d["name"].lower() for k in ("blackhole", "loopback", "soundflower", "aggregate")):
                print(f"  {d['name']}")
        print("Microphones:")
        for d in _input_devices():
            print(f"  {d['name']}")
