"""join-zoom.sh (zoom.service) must exit cleanly when the active source is not a Zoom meeting.

Found on the v1.0.0 test server (check A10): zoom.service is enabled at boot, the default source after an install is a
web page, and join-zoom.sh had no guard for that - it failed with "Could not parse a meeting ID", systemd retried, and
after every reboot `doctor.sh` reported "zoom failed". browser-source.sh already has the mirror-image guard."""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="bash not available - script NOT executed")

STUB_LIB = r'''
log() { echo "LOG: $*"; }
wait_for_x() { echo "WAITED_FOR_X"; }
'''


def _posix(p):
    p = pathlib.Path(p).resolve().as_posix()
    return "/" + p[0].lower() + p[2:] if len(p) > 1 and p[1] == ":" else p


def _run(tmp_path, **env):
    d = tmp_path / "scripts"
    d.mkdir()
    shutil.copy(REPO / "scripts" / "join-zoom.sh", d / "join-zoom.sh")
    (d / "lib.sh").write_text(STUB_LIB, encoding="utf-8", newline="\n")
    full = {k: v for k, v in os.environ.items() if not k.startswith(("SOURCE_TYPE", "ZOOM_"))}
    full.update(env)
    return subprocess.run([BASH, _posix(d / "join-zoom.sh")], capture_output=True, text=True, timeout=30, env=full,
                          stdin=subprocess.DEVNULL)


@needs_bash
@pytest.mark.parametrize("source_type", ["webpage", "direct", "youtube"])
def test_non_zoom_source_exits_cleanly_without_touching_zoom(tmp_path, source_type):
    r = _run(tmp_path, SOURCE_TYPE=source_type, ZOOM_LINK="")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "not a Zoom meeting" in r.stdout and "WAITED_FOR_X" not in r.stdout


@needs_bash
def test_zoom_source_without_a_link_still_fails(tmp_path):
    # The guard must not hide a real problem: a Zoom source with no usable link is still an error.
    r = _run(tmp_path, SOURCE_TYPE="zoom", ZOOM_LINK="", PATH=_posix(tmp_path) + ":" + os.environ.get("PATH", ""))
    assert r.returncode == 1
    assert "not a Zoom meeting" not in r.stdout
