"""Regression tests for v1.0.3: the encoder's capture size follows the REAL virtual screen.

The bug: with an output preset larger than the screen (the default 1280x720 screen + a 1080p preset) stream.sh asked
x11grab for 1920x1080. ffmpeg answered "Capture area 1920x1080 at position 0.0 outside the screen size 1280x720 /
Error opening input files: Invalid argument" and Go Live failed. With an output SMALLER than the screen it silently
captured only the top-left corner.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import time

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
LIB = REPO / "scripts" / "lib.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")

UP = "scale=1920:1080:flags=lanczos,setsar=1"          # 1280x720 -> 1920x1080, same shape: one uniform scale
DOWN = "scale=1280:720:flags=lanczos,setsar=1"


def _posix(p):
    p = pathlib.Path(p).resolve().as_posix()
    return "/" + p[0].lower() + p[2:] if len(p) > 1 and p[1] == ":" else p


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def _framing(tmp_path, res, screen, env=None):
    """build_capture_framing RES on a (fake) X screen of size SCREEN; '' = xdpyinfo cannot ask the server."""
    body = LIB.read_text(encoding="utf-8").replace("\r", "")
    funcs = []
    for name in ("clean_filter_ok", "build_meeting_only_filter", "build_fullscreen_live_filter", "build_capture_framing"):
        start = body.index(f"{name}() {{")
        funcs.append(body[start:body.index("\n}\n", start) + 3])
    if screen:
        fake = 'xdpyinfo() { echo "  dimensions:    ' + screen + ' pixels (325x183 millimeters)"; }'
    else:
        fake = "xdpyinfo() { return 1; }"
    tail = 'build_capture_framing ' + res + '; printf "%s|%s|%s" "$CAPTURE_SIZE" "$CROP_FILTER" "$FRAMING_NOTE"'
    script = tmp_path / "framing.sh"
    script.write_text("\n".join([fake, *funcs, tail]), encoding="utf-8", newline="\n")
    keep = {k: v for k, v in os.environ.items() if not k.startswith(("ZOOM_", "SOURCE_"))}
    r = subprocess.run([BASH, _posix(script)], capture_output=True, text=True, env={**keep, **(env or {})}, timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout.split("|")


def test_output_larger_than_the_screen_captures_the_screen_and_scales_up(tmp_path):
    """The reported failure: 1280x720 screen, 1920x1080 output."""
    size, vf, note = _framing(tmp_path, "1920x1080", "1280x720", {"SOURCE_TYPE": "zoom"})
    assert size == "1280x720" and vf == UP
    assert "1280x720" in note and "1920x1080" in note               # the log says what happened
    assert _framing(tmp_path, "1920x1080", "1280x720", {"SOURCE_TYPE": "webpage"})[:2] == ["1280x720", UP]


def test_output_smaller_than_the_screen_captures_the_whole_screen_not_a_corner(tmp_path):
    assert _framing(tmp_path, "1280x720", "1920x1080", {"SOURCE_TYPE": "zoom"})[:2] == ["1920x1080", DOWN]


def test_a_differently_shaped_screen_is_fitted_with_bars_never_stretched(tmp_path):
    size, vf, _ = _framing(tmp_path, "1920x1080", "1928x1225", {"SOURCE_TYPE": "webpage"})
    assert size == "1928x1225" and "force_original_aspect_ratio=decrease" in vf and "pad=1920:1080" in vf


def test_matching_screen_and_output_are_untouched(tmp_path):
    for src in ("zoom", "webpage"):
        assert _framing(tmp_path, "1920x1080", "1920x1080", {"SOURCE_TYPE": src}) == ["1920x1080", "", ""]
    assert _framing(tmp_path, "1280x720", "1280x720", {"SOURCE_TYPE": "zoom"}) == ["1280x720", "", ""]


def test_when_the_screen_cannot_be_asked_the_configured_size_is_used_as_before(tmp_path):
    assert _framing(tmp_path, "1920x1080", "", {"SOURCE_TYPE": "zoom"}) == ["1920x1080", "", ""]


@pytest.mark.parametrize("mode", [
    {"ZOOM_MEETING_ONLY": "1", "ZOOM_MEETING_ONLY_FIT": "fill"},
    {"ZOOM_FULLSCREEN_LIVE": "1", "ZOOM_CAPTURE_SIZE": "1920x1080", "ZOOM_FRAMING_BOX": "160:90:1600:900"},
    {"ZOOM_CLEAN_BROADCAST": "1", "ZOOM_CLEAN_CAPTURE": "1920x1080", "ZOOM_CLEAN_FILTER": "crop=1912:935:4:69"},
])
def test_every_framing_mode_measured_on_another_screen_falls_back_to_the_whole_real_screen(tmp_path, mode):
    size, vf, note = _framing(tmp_path, "1920x1080", "1280x720", {"SOURCE_TYPE": "zoom", **mode})
    assert size == "1280x720" and vf == UP                          # never a crop made for a different screen
    assert "crop=" not in vf and note


def test_framing_modes_still_apply_on_the_screen_they_were_made_for(tmp_path):
    size, vf, _ = _framing(tmp_path, "1920x1080", "1920x1080",
                           {"SOURCE_TYPE": "zoom", "ZOOM_FULLSCREEN_LIVE": "1", "ZOOM_CAPTURE_SIZE": "1920x1080",
                            "ZOOM_FRAMING_BOX": "160:90:1600:900"})
    assert size == "1920x1080" and vf == "crop=1600:900:160:90,scale=1920:1080:flags=lanczos,setsar=1"
    size, vf, _ = _framing(tmp_path, "1920x1080", "1920x1080",
                           {"SOURCE_TYPE": "zoom", "ZOOM_MEETING_ONLY": "1", "ZOOM_MEETING_ONLY_FIT": "fit"})
    assert size == "1920x1080" and vf.startswith("crop=")


def test_the_capture_is_never_larger_than_the_screen_whatever_is_stored(tmp_path):
    modes = ({}, {"ZOOM_MEETING_ONLY": "1"}, {"ZOOM_FULLSCREEN_LIVE": "1", "ZOOM_CAPTURE_SIZE": "3840x2160"},
             {"ZOOM_CLEAN_BROADCAST": "1", "ZOOM_CLEAN_CAPTURE": "1928x1225"})
    for screen in ("1280x720", "1024x768", "1920x1080"):
        for res in ("1280x720", "1920x1080"):
            for mode in modes:
                size = _framing(tmp_path, res, screen, {"SOURCE_TYPE": "zoom", **mode})[0]
                w, h = (int(v) for v in size.split("x"))
                sw, sh = (int(v) for v in screen.split("x"))
                assert w <= sw and h <= sh, (screen, res, mode, size)


# ------------------------------------------------------------------ the real scripts, with a fake screen and ffmpeg

RECORD_ARGV = """printf '%s\\n' "$@" > '@APP@/argv'
exit 0
"""
# Behaves like the real x11grab: refuses a capture area larger than the 1280x720 screen.
STRICT_X11GRAB = """printf '%s\\n' "$@" > '@APP@/argv'
size=''
while [[ $# -gt 0 ]]; do if [[ "$1" == -video_size ]]; then size="$2"; fi; shift; done
if (( ${size%x*} > 1280 || ${size#*x} > 720 )); then
  echo "[x11grab] Capture area $size at position 0.0 outside the screen size 1280x720" >&2
  echo 'Error opening input files: Invalid argument' >&2
  exit 234
fi
exit 0
"""
REFUSE = """echo '[x11grab] Capture area 1280x720 at position 0.0 outside the screen size 640x480' >&2
exit 234
"""


def _sandbox(tmp_path, screen, resolution, ffmpeg_body):
    app = tmp_path / "zs"
    (app / "scripts").mkdir(parents=True)
    (app / "bin").mkdir()
    for n in ("lib.sh", "stream.sh", "test-recording.sh", "capture-check.sh"):
        text = _read(f"scripts/{n}").replace("\r\n", "\n").replace("/usr/bin/ffmpeg", _posix(app / "fake-ffmpeg"))
        (app / "scripts" / n).write_text(text, encoding="utf-8", newline="\n")
    (app / ".env").write_text(f'YT_STREAM_KEY="abcd-efgh-ijkl"\nRESOLUTION="{resolution}"\nFPS="30"\n'
                              'VIDEO_BITRATE="6000"\nAUDIO_BITRATE="160"\nX264_PRESET="superfast"\n',
                              encoding="utf-8", newline="\n")
    exp = int(time.time()) + 60
    (app / "current-source.env").write_text(
        'SOURCE_TYPE="zoom"\nZOOM_MEETING_ONLY="0"\nZOOM_FULLSCREEN_LIVE="0"\nZOOM_CLEAN_BROADCAST="0"\n'
        f'BROADCAST_AUTH_ID="g"\nBROADCAST_AUTH_NONCE="n0nce"\nBROADCAST_AUTH_EXPIRES="{exp}"\n',
        encoding="utf-8", newline="\n")
    files = {
        app / "fake-ffmpeg": "#!/usr/bin/env bash\n" + ffmpeg_body.replace("@APP@", _posix(app)),
        app / "bin" / "xdpyinfo": f"#!/usr/bin/env bash\necho '  dimensions:    {screen} pixels'\n",
        app / "bin" / "pactl": "#!/usr/bin/env bash\nprintf '57\\tzoom_out.monitor\\tPipeWire\\tfloat32le 2ch 48000Hz\\tRUNNING\\n'\n",
    }
    for f, text in files.items():
        f.write_text(text, encoding="utf-8", newline="\n")
        os.chmod(f, 0o755)
    env = dict(os.environ, APP_DIR=_posix(app), HOME=_posix(app), DISPLAY=":99", ZS_FFMPEG=_posix(app / "fake-ffmpeg"),
               PATH=_posix(app / "bin") + os.pathsep + os.environ.get("PATH", ""))
    return app, env


def _run(app, env, script, **extra):
    return subprocess.run([BASH, _posix(app / "scripts" / script)], capture_output=True, text=True,
                          env={**env, **extra}, timeout=60)


@pytest.mark.parametrize("script", ["stream.sh", "test-recording.sh"])
def test_go_live_and_test_recording_capture_the_real_screen_with_a_larger_output(tmp_path, script):
    app, env = _sandbox(tmp_path, "1280x720", "1920x1080", STRICT_X11GRAB)
    r = _run(app, env, script, TEST_DURATION="1")
    argv = (app / "argv").read_text().split("\n")
    assert argv[argv.index("-video_size") + 1] == "1280x720", r.stdout + r.stderr     # was 1920x1080 -> ffmpeg refused
    assert argv[argv.index("-vf") + 1] == UP
    assert argv[argv.index("-i") + 1] == ":99" and "zoom_out.monitor" in argv
    if script == "stream.sh":            # test-recording.sh goes on to link the (here non-existent) output file
        assert r.returncode == 0, r.stdout + r.stderr


def test_capture_check_passes_and_says_when_the_output_is_only_enlarged(tmp_path):
    app, env = _sandbox(tmp_path, "1280x720", "1920x1080", STRICT_X11GRAB)
    r = _run(app, env, "capture-check.sh")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "capture check: OK - screen 1280x720, capture 1280x720, output 1920x1080" in r.stdout
    assert "LARGER than the screen" in r.stdout and "zoom_out.monitor present" in r.stdout
    argv = [a for a in (app / "argv").read_text().split("\n") if a]
    assert argv[argv.index("-frames:v") + 1] == "1" and argv[-3:] == ["-f", "null", "-"]   # one frame, to nowhere
    assert not any(a.startswith(("rtmp://", "rtmps://")) for a in argv)                    # never a stream


def test_capture_check_fails_with_the_reason_when_ffmpeg_cannot_capture(tmp_path):
    app, env = _sandbox(tmp_path, "1280x720", "1280x720", REFUSE)
    r = _run(app, env, "capture-check.sh")
    assert r.returncode == 1
    assert "capture check: FAILED" in r.stdout and "outside the screen size" in r.stdout


def test_capture_check_fails_when_the_screen_or_the_audio_source_is_missing(tmp_path):
    app, env = _sandbox(tmp_path, "1280x720", "1280x720", RECORD_ARGV)
    (app / "bin" / "pactl").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8", newline="\n")
    r = _run(app, env, "capture-check.sh")
    assert r.returncode == 1 and "zoom_out.monitor does not exist" in r.stdout
    (app / "bin" / "xdpyinfo").write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8", newline="\n")
    r = _run(app, env, "capture-check.sh")
    assert r.returncode == 1 and "does not answer" in r.stdout


def test_installer_health_check_and_doctor_run_the_capture_check():
    steps = _read("installer/lib/steps.sh")
    verify = steps[steps.index("zs_step_verify() {"):steps.index("# ------------------------------------------------------------------ database migrations")]
    assert "zs_capture_check ||" in verify and "return 1" in verify[verify.index("zs_capture_check"):]
    common = _read("installer/lib/common.sh")
    fn = common[common.index("zs_capture_check() {"):common.index("# True when the broadcast encoder is up.")]
    assert 'sudo -u "$ZS_SVC_USER"' in fn and "scripts/capture-check.sh" in fn
    doctor = _read("doctor.sh")
    assert "zs_capture_check" in doctor and "Go Live would fail" in doctor
