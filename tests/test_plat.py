"""The platform layer (cue/plat, cue/asr, the audio Capture) on whichever OS runs the tests."""
from __future__ import annotations

import plistlib
import sys

import numpy as np
import pytest

from cue import asr, plat
from cue.audio import FRAME, Capture
from cue.plat import hotkeys
from cue.plat.schedule import LABEL, agent_plist
from cue.trainer import langs, runner


def test_platform_flags():
    assert plat.IS_WIN == (sys.platform == "win32")
    assert plat.IS_MAC == (sys.platform == "darwin")
    kw = plat.popen_kwargs(detached=True)
    assert ("creationflags" in kw) if plat.IS_WIN else kw == {"start_new_session": True}


@pytest.mark.parametrize("rate", [16000, 44100, 48000])
def test_capture_frames_any_rate(rate):
    cap = Capture("them", loopback=True)
    cap.begin(rate)
    t = np.arange(rate) / rate                       # one second of a 440 Hz tone, in 20 ms chunks
    tone = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    step = rate // 50
    for i in range(0, rate, step):
        cap.ingest(tone[i:i + step])
    frames = []
    while not cap.frames.empty():
        frames.append(cap.frames.get_nowait()[1])
    assert all(f.dtype == np.int16 and len(f) == FRAME for f in frames)
    assert 28 <= len(frames) <= 34                   # ~1 s of 30 ms frames (the resampler holds a little back)
    assert 0.15 < cap.level < 0.3


def test_capture_ignores_audio_before_begin():
    cap = Capture("me", loopback=False)
    cap.ingest(np.zeros(800, dtype=np.float32))
    assert cap.frames.empty()


@pytest.mark.parametrize("combo,want", [
    ("tab", (frozenset(), "tab")),
    ("shift+Tab", (frozenset({"shift"}), "tab")),
    ("ctrl+alt+m", (frozenset({"ctrl", "alt"}), "m")),
    ("option+command+space", (frozenset({"alt", "cmd"}), "space")),
])
def test_hotkey_parse(combo, want):
    assert hotkeys.parse(combo) == want


def test_hotkey_parse_rejects_two_keys():
    with pytest.raises(ValueError):
        hotkeys.parse("a+b")


def test_asr_engine_matches_platform():
    assert asr.engine() == ("mlx" if plat.IS_MAC else "faster-whisper")
    assert asr.MLX_REPOS["large-v3-turbo"].startswith("mlx-community/")


def test_launch_agent_plist_roundtrip(tmp_path):
    p = agent_plist(["/Applications/Cue.app/Contents/MacOS/Cue", "worklog"], tmp_path / "w.log")
    again = plistlib.loads(plistlib.dumps(p))
    assert again == p and again["Label"] == LABEL
    assert again["StartCalendarInterval"] == {"Hour": 7, "Minute": 30}


def test_mac_cpp_toolchain_drops_static_and_adds_shim(monkeypatch):
    monkeypatch.setattr(langs, "IS_WIN", False)
    monkeypatch.setattr(langs, "IS_MAC", True)
    monkeypatch.setattr(langs, "which", lambda t: f"/usr/bin/{t}")
    tc = langs.toolchain(langs.get("cpp"))
    assert "-static" not in tc.build and "-I{dir}/" + langs.SHIM_DIR in tc.build
    assert "-I{dir}/" + langs.SHIM_DIR in tc.check


@pytest.mark.skipif(not langs.runnable(langs.get("cpp")), reason="no C++ compiler")
def test_cpp_program_with_bits_header():
    code = "#include <bits/stdc++.h>\nusing namespace std;\nint main(){vector<int> v{3,1,2};" \
           "sort(v.begin(),v.end());for(int x:v)cout<<x;cout<<endl;}\n"
    b = runner.prepare(langs.get("cpp"), code, None)
    assert b.ok, b.output
    r = runner.run(b.run_cmd, b.dir, timeout=10)
    assert r.ok and r.stdout.strip() == "123"
