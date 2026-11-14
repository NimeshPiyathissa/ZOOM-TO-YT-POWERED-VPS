"""Settings the installer writes and the app must honour:

* YT_RTMP_URL in the pipeline .env selects the ingest server (stream.sh), and a malformed value is refused.
* SCHEDULE_TIMEZONE in the dashboard environment overrides the server timezone; an invalid value is ignored.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import time

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
DASHBOARD = REPO / "dashboard"
BASH = shutil.which("bash")


def _posix(p):
    p = pathlib.Path(p).resolve().as_posix()
    return "/" + p[0].lower() + p[2:] if len(p) > 1 and p[1] == ":" else p


def _run_stream(tmp_path, env_lines: str):
    """Run the real scripts/stream.sh with a fake ffmpeg; returns (argv list or None, log text)."""
    app_dir = tmp_path / "zs"
    (app_dir / "scripts").mkdir(parents=True)
    (app_dir / "bin").mkdir()
    (app_dir / "logs").mkdir()
    for n in ("lib.sh", "stream.sh"):
        text = (REPO / "scripts" / n).read_text(encoding="utf-8").replace("\r\n", "\n")
        (app_dir / "scripts" / n).write_text(text.replace("/usr/bin/ffmpeg", _posix(app_dir / "fake-ffmpeg")),
                                             encoding="utf-8", newline="\n")
    (app_dir / ".env").write_text('YT_STREAM_KEY="abcd-efgh-ijkl"\nRESOLUTION="1280x720"\nFPS="30"\n' + env_lines,
                                  encoding="utf-8", newline="\n")
    (app_dir / "fake-ffmpeg").write_text(f"#!/usr/bin/env bash\nprintf '%s\\n' \"$@\" > '{_posix(app_dir)}/argv'\nexit 0\n",
                                         encoding="utf-8", newline="\n")
    (app_dir / "bin" / "xdpyinfo").write_text("#!/usr/bin/env bash\necho 'dimensions:    1280x720 pixels'\n",
                                              encoding="utf-8", newline="\n")
    for f in (app_dir / "fake-ffmpeg", app_dir / "bin" / "xdpyinfo"):
        os.chmod(f, 0o755)
    exp = int(time.time()) + 60
    (app_dir / "current-source.env").write_text(
        f'SOURCE_TYPE="zoom"\nBROADCAST_AUTH_ID="g"\nBROADCAST_AUTH_NONCE="n0nce"\nBROADCAST_AUTH_EXPIRES="{exp}"\n',
        encoding="utf-8", newline="\n")
    env = dict(os.environ, APP_DIR=_posix(app_dir), HOME=_posix(app_dir), DISPLAY=":99", LOG_DIR=_posix(app_dir / "logs"),
               PATH=_posix(app_dir / "bin") + os.pathsep + os.environ.get("PATH", ""))
    proc = subprocess.run([BASH, _posix(app_dir / "scripts" / "stream.sh")], capture_output=True, text=True, env=env,
                          timeout=60)
    argv_file = app_dir / "argv"
    logs = proc.stdout + proc.stderr + "".join(p.read_text(encoding="utf-8", errors="replace")
                                               for p in (app_dir / "logs").glob("*"))
    return (argv_file.read_text().split("\n") if argv_file.exists() else None), logs


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_default_ingest_is_youtube_rtmps(tmp_path):
    argv, _ = _run_stream(tmp_path, "")
    assert argv is not None
    assert "rtmps://a.rtmps.youtube.com:443/live2/abcd-efgh-ijkl" in argv


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_custom_ingest_url_from_env(tmp_path):
    argv, _ = _run_stream(tmp_path, 'YT_RTMP_URL="rtmp://b.rtmp.youtube.com/live2/"\n')
    assert argv is not None
    assert "rtmp://b.rtmp.youtube.com/live2/abcd-efgh-ijkl" in argv


@pytest.mark.skipif(BASH is None, reason="bash not available")
@pytest.mark.parametrize("bad", ["http://evil.example/live2", "rtmp://host/live2?x=$(id)", "rtmps://", "file:///etc/passwd"])
def test_malformed_ingest_url_is_refused(tmp_path, bad):
    argv, logs = _run_stream(tmp_path, f"YT_RTMP_URL='{bad}'\n")
    assert argv is None
    assert "YT_RTMP_URL" in logs and "abcd-efgh-ijkl" not in logs


def _schedule_tz(value: str | None) -> str:
    env = dict(os.environ)
    env.pop("SCHEDULE_TIMEZONE", None)
    if value is not None:
        env["SCHEDULE_TIMEZONE"] = value
    out = subprocess.run([sys.executable, "-c", "from app import config; print(config.SCHEDULE_TIMEZONE, config.TIMEZONE)"],
                         cwd=DASHBOARD, env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.split()


def test_schedule_timezone_defaults_to_server_timezone():
    sched, server = _schedule_tz(None)
    assert sched == server


def test_schedule_timezone_override():
    assert _schedule_tz("Europe/Berlin")[0] == "Europe/Berlin"


def test_invalid_schedule_timezone_is_ignored():
    sched, server = _schedule_tz("Not/AZone")
    assert sched == server


def test_rtmp_url_is_kept_on_config_saves_and_validated():
    from app import config, env_store
    assert "YT_RTMP_URL" in config.ALL_ENV_KEYS                     # env_store.write_updates keeps only these keys
    env_store.validate_updates({"YT_RTMP_URL": "rtmps://a.rtmps.youtube.com:443/live2"})
    env_store.validate_updates({"YT_RTMP_URL": ""})
    for bad in ("http://x/live2", "rtmp://host/live2?k=v", "rtmp://host/$(id)", "rtmps://"):
        with pytest.raises(env_store.ValidationError):
            env_store.validate_updates({"YT_RTMP_URL": bad})
