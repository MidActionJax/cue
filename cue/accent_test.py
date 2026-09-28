"""Compare Whisper models on a real recording of a hard-to-understand speaker.

    accent_test.cmd meeting.mp4                      # any audio/video file (Teams/Zoom recordings work)
    accent_test.cmd meeting.mp4 --start 300 --length 60
    accent_test.cmd --record 60                      # capture 60 s of call audio (what you hear) first

Prints each model's transcript and speed, and saves them to data/accent_test.md. Pick the
best one that keeps "speed" under ~0.3x real time and set stt.model in config.yaml to it.
Models not downloaded yet are fetched on first use (turbo 1.6 GB, large-v3 3 GB, medium 1.5 GB).
"""
from __future__ import annotations

import argparse
import time
import wave
from datetime import datetime

from .cuda_setup import add_cuda_dlls

add_cuda_dlls()

import numpy as np  # noqa: E402

from . import config  # noqa: E402
from .context import load_vocabulary  # noqa: E402
from .stt import _normalize  # noqa: E402

DEFAULT_MODELS = ["large-v3-turbo", "large-v3", "medium"]


def record(seconds: int, device: str) -> str:
    from .audio import SR, Capture
    cap = Capture("them", device, loopback=True)
    cap.start()
    print(f"Recording {seconds}s of call audio from {cap.device_name} …")
    frames, t0 = [], time.time()
    while time.time() - t0 < seconds:
        try:
            frames.append(cap.frames.get(timeout=0.2)[1])
        except Exception:
            pass
    cap.stop()
    out = config.DATA / "recordings" / f"{datetime.now():%Y-%m-%d_%H%M%S}.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(np.concatenate(frames).tobytes() if frames else b"")
    print("saved", out)
    return str(out)


def main():
    ap = argparse.ArgumentParser(prog="accent_test")
    ap.add_argument("file", nargs="?")
    ap.add_argument("--record", type=int, metavar="SECONDS")
    ap.add_argument("--start", type=float, default=0)
    ap.add_argument("--length", type=float, default=90)
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    args = ap.parse_args()
    cfg = config.load()
    path = record(args.record, cfg.audio.loopback_device) if args.record else args.file
    if not path:
        ap.error("give a file or --record SECONDS")

    from faster_whisper import WhisperModel
    from faster_whisper.audio import decode_audio

    audio = decode_audio(path, sampling_rate=16000)
    audio = _normalize(audio[int(args.start * 16000): int((args.start + args.length) * 16000)])
    dur = len(audio) / 16000
    vocab = ", ".join(load_vocabulary(cfg.stt.vocabulary)[:60])
    report = [f"# Accent test — {path} ({dur:.0f}s from {args.start:.0f}s)\n"]
    for name in args.models:
        print(f"\n=== {name} (loading…)", flush=True)
        model = WhisperModel(name, device=cfg.stt.device, compute_type=cfg.stt.compute_type)
        t = time.time()
        segs, _ = model.transcribe(audio, language=cfg.stt.language or None, beam_size=cfg.stt.beam_size,
                                   initial_prompt=f"Glossary: {vocab}." if vocab else None,
                                   vad_filter=True, condition_on_previous_text=True)
        text = " ".join(s.text.strip() for s in segs)
        el = time.time() - t
        line = f"speed {el / dur:.2f}x real time ({el:.1f}s for {dur:.0f}s of audio)"
        print(line)
        print(text)
        report.append(f"## {name}\n_{line}_\n\n{text}\n")
        del model
    out = config.DATA / "accent_test.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(report), encoding="utf-8")
    print(f"\nSaved side-by-side to {out}")


if __name__ == "__main__":
    main()
