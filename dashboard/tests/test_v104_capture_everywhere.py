"""v1.0.4: every script that captures the screen takes the capture size from the REAL screen.

v1.0.3 fixed the broadcast and the test recording. The audio self-test and the local recorder still asked x11grab for
RESOLUTION and failed the same way ("Error opening input files: Invalid argument") with an output larger than the screen.
"""
from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"


def _code(name: str) -> str:
    return "\n".join(line for line in (SCRIPTS / name).read_text(encoding="utf-8").splitlines()
                     if not line.lstrip().startswith("#"))


def test_no_script_hands_the_output_resolution_to_x11grab():
    offenders = []
    for f in sorted(SCRIPTS.glob("*.sh")):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if "x11grab" in line and re.search(r'-video_size\s+"?\$\{?RESOLUTION', line):
                offenders.append(f"{f.name}:{n}")
    assert not offenders, f"x11grab is given RESOLUTION (the output size, not the screen size) in: {offenders}"


def test_audio_selftest_uses_the_shared_capture_logic():
    src = _code("audio-selftest.sh")
    assert 'build_capture_framing "$RESOLUTION"' in src
    assert src.index("build_capture_framing") < src.index("-f x11grab")
    assert '-video_size "$CAPTURE_SIZE"' in src and '-vf "$CROP_FILTER"' in src


def test_local_recorder_prefers_the_real_screen_size():
    src = _code("record-stream.sh")
    detect = src.index("xdpyinfo")
    fallback = src.index('resolution="${RESOLUTION:-1280x720}"')
    assert detect < fallback                                          # the screen is asked first; RESOLUTION is the fallback
    assert 'resolution="$detected_res"' in src
    assert '-video_size "$resolution"' in src
