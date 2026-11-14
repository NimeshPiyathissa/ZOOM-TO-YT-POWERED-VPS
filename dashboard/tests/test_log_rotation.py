"""Log rotation for ffmpeg.log / zoom.log / browser.log (scripts/logrotate-zoom-stream.conf, installed by install.sh).

The property that matters: rotating a log must never interrupt a live stream. That is checked three ways -
  * the config itself: copytruncate and nothing that signals, restarts or starts a process;
  * the writers: every one of them appends (>>), which is what makes truncating in place safe;
  * behaviour: a process that keeps a log open keeps writing, uninterrupted and uncorrupted, across a copy + truncate.
The retention test runs the real `logrotate` binary and is SKIPPED where it is not installed (e.g. Windows): there,
retention is NOT verified here - see docs/testing.md.
"""
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
CONF = REPO / "scripts" / "logrotate-zoom-stream.conf"
BASH = shutil.which("bash")
LOGROTATE = shutil.which("logrotate") if sys.platform.startswith("linux") else None
LOGS = ("ffmpeg.log", "zoom.log", "browser.log")


def directives() -> list[str]:
    return [l.strip() for l in CONF.read_text(encoding="utf-8").splitlines() if l.strip() and not l.strip().startswith("#")]


def render(log_dir: str, user: str | None) -> str:
    text = CONF.read_text(encoding="utf-8").replace("@LOG_DIR@", log_dir)
    if user is None:                                                # a non-root test run cannot switch user
        text = "\n".join(l for l in text.splitlines() if not l.strip().startswith("su ")) + "\n"
    return text.replace("@SVC_USER@", user or "")


def test_config_covers_the_three_logs_and_only_them():
    head = directives()[0]
    assert head.endswith("{")
    assert head[:-1].split() == [f"@LOG_DIR@/{n}" for n in LOGS]
    assert "*" not in head                                           # never a glob: only the named pipeline logs


def test_config_cannot_interrupt_a_stream():
    d = directives()
    assert "copytruncate" in d
    body = " ".join(d).lower()
    for forbidden in ("postrotate", "prerotate", "firstaction", "lastaction", "preremove", "sharedscripts", "endscript",
                      "systemctl", "kill", "pkill", "reload", "restart", "ffmpeg-stream", "create "):
        assert forbidden not in body, f"'{forbidden}' in the logrotate config could touch a running process"
    assert "nocopytruncate" not in body


def test_config_retention_and_permissions():
    d = directives()
    assert "su @SVC_USER@ @SVC_USER@" in d                           # rotate as the log owner, never as root
    assert {"weekly", "compress", "delaycompress", "missingok", "notifempty"} <= set(d)
    values = dict(l.split(None, 1) for l in d if re.match(r"^(rotate|maxage|maxsize)\s", l))
    assert int(values["rotate"]) == 8 and int(values["maxage"]) == 60 and values["maxsize"] == "5M"
    rendered = render("/home/zoombot/zoom-stream/logs", "zoombot")
    assert "@" not in "".join(l for l in rendered.splitlines() if not l.startswith("#"))
    assert "/home/zoombot/zoom-stream/logs/ffmpeg.log" in rendered and "su zoombot zoombot" in rendered


def test_every_writer_appends_so_truncating_in_place_is_safe():
    scripts = REPO / "scripts"
    stream = (scripts / "stream.sh").read_text(encoding="utf-8")
    assert '>>"$LOG_FILE" 2> >(redact_stream_secrets >>"$LOG_FILE")' in stream
    assert '>>"$LOG_DIR/zoom.log"' in (scripts / "join-zoom.sh").read_text(encoding="utf-8")
    assert '>>"$LOG_DIR/browser.log"' in (scripts / "browser-source.sh").read_text(encoding="utf-8")
    for path in scripts.glob("*.sh"):                               # nobody opens one of these logs for overwrite
        for line in path.read_text(encoding="utf-8").splitlines():
            code = line.split("#", 1)[0]
            for name in LOGS:
                assert not re.search(r'(?<![>&0-9])>\s*"?[^>|]*' + re.escape(name), code), f"{path.name}: {line.strip()}"


def test_installer_installs_and_removes_the_rotation_config():
    sh = (REPO / "installer" / "lib" / "steps.sh").read_text(encoding="utf-8")
    uninstall = (REPO / "uninstall.sh").read_text(encoding="utf-8")
    pkgs = sh[sh.index("zs_step_packages() {"):sh.index("zs_step_chrome() {")]
    assert " logrotate" in pkgs
    fn = sh[sh.index("# Log rotation"):sh.index("zs_step_units() {")]
    assert 'sed -e "s|@LOG_DIR@|${ZS_APP_DIR}/logs|g" -e "s|@SVC_USER@|${ZS_SVC_USER}|g"' in fn
    assert 'install -o root -g root -m 644 "$out" /etc/logrotate.d/zoom-stream' in fn
    assert fn.index("logrotate -d") < fn.index("install -o root")   # validated (dry run) before it is installed
    assert "/etc/logrotate.d/zoom-stream" in uninstall and "rm -f" in uninstall   # uninstall cleans it up
    fn = "\n".join(l for l in fn.splitlines() if not l.strip().startswith(("echo ", "#")))      # commands, not messages
    for forbidden in ("systemctl", "restart", "logrotate -f", "kill"):
        assert forbidden not in fn, f"installing log rotation must not {forbidden} anything"


WRITER = r'''
exec 3>>"$1"                      # opened once, append mode, kept open - like ffmpeg/Zoom/Chrome hold their log
i=0
while [ ! -e "$2" ]; do i=$((i + 1)); printf 'line %06d %s\n' "$i" "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" >&3 || exit 7; done
echo "$i"
'''


@pytest.mark.skipif(BASH is None, reason="bash not available - copytruncate behaviour NOT tested")
def test_a_writer_keeps_writing_across_copy_and_truncate(tmp_path):
    """What copytruncate does, done by hand around a process that never closes its log. The writer must not die,
    must not have to reopen the file, and the log must not be corrupted (no NUL padding from a stale file offset)."""
    log, stop = tmp_path / "ffmpeg.log", tmp_path / "stop"
    px = lambda p: p.as_posix() if not (len(str(p)) > 1 and str(p)[1] == ":") else "/" + str(p)[0].lower() + p.as_posix()[2:]
    proc = subprocess.Popen([BASH, "-c", WRITER, "writer", px(log), px(stop)], stdout=subprocess.PIPE, text=True)
    try:
        deadline = time.time() + 20
        while (not log.exists() or log.stat().st_size < 60_000) and time.time() < deadline:
            time.sleep(0.05)
        assert log.exists() and log.stat().st_size >= 60_000, "writer never produced output"
        rotated = []
        for n in (1, 2, 3):                                          # three rotations while the writer is running
            copy = tmp_path / f"ffmpeg.log.{n}"
            shutil.copyfile(log, copy)                              # "copy"
            with open(log, "r+b") as fh:                            # "truncate" - same file, same inode
                fh.truncate(0)
            rotated.append(copy)
            assert proc.poll() is None, "the writer died when its log was rotated"
            before = log.stat().st_size
            t0 = time.time()
            while log.stat().st_size < before + 20_000 and time.time() - t0 < 10:
                time.sleep(0.05)
            assert log.stat().st_size >= 20_000, "the writer stopped writing after rotation"
    finally:
        stop.write_text("x")
        out, _ = proc.communicate(timeout=30)
    assert proc.returncode == 0                                       # every write succeeded (exit 7 = a failed write)
    total = int(out.strip())

    live = log.read_bytes()
    assert b"\x00" not in live, "sparse hole after truncate: a writer is not in append mode"
    assert len(live) < 40 * 1024 * 1024
    seen = []
    for part in rotated + [log]:
        data = part.read_bytes()
        assert b"\x00" not in data
        nums = [int(m) for m in re.findall(rb"^line (\d{6}) x{40}$", data, flags=re.M)]
        assert nums == sorted(nums) and len(nums) == len(set(nums))
        seen += nums
    assert seen == sorted(seen) and len(seen) == len(set(seen)), "a line was duplicated across rotated copies"
    assert seen[-1] == total                                          # the newest line is in the live log
    lost = total - len(seen)
    assert 0 <= lost <= total * 0.2, f"{lost} of {total} lines lost"  # only the copy->truncate window may lose lines
    last_live = [int(m) for m in re.findall(rb"^line (\d{6})", live, flags=re.M)]
    assert last_live and last_live[0] > max(int(m) for m in re.findall(rb"^line (\d{6})", rotated[-1].read_bytes(), flags=re.M))


@pytest.mark.skipif(LOGROTATE is None or BASH is None,
                    reason="needs the real logrotate binary on Linux - retention is NOT verified on this machine")
def test_real_logrotate_rotates_keeps_eight_and_never_stops_the_writer(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    conf, state, stop = tmp_path / "zoom-stream.conf", tmp_path / "state", tmp_path / "stop"
    conf.write_text(render(str(logs), None), encoding="utf-8")
    os.chmod(conf, 0o644)
    (logs / "zoom.log").write_text("zoom\n")
    proc = subprocess.Popen([BASH, "-c", WRITER, "writer", str(logs / "ffmpeg.log"), str(stop)], stdout=subprocess.PIPE, text=True)
    try:
        time.sleep(0.5)
        check = subprocess.run([LOGROTATE, "-d", "-s", str(state), str(conf)], capture_output=True, text=True)
        assert check.returncode == 0, check.stderr                    # the shipped config parses
        for _ in range(11):                                           # more forced rotations than copies kept
            time.sleep(0.25)
            r = subprocess.run([LOGROTATE, "-f", "-s", str(state), str(conf)], capture_output=True, text=True)
            assert r.returncode == 0, r.stderr
            assert proc.poll() is None, "logrotate interrupted the writer"
        size_after = (logs / "ffmpeg.log").stat().st_size
        time.sleep(0.5)
        assert (logs / "ffmpeg.log").stat().st_size > size_after      # still being written to
    finally:
        stop.write_text("x")
        proc.communicate(timeout=30)
    assert proc.returncode == 0
    old = sorted(p.name for p in logs.glob("ffmpeg.log.*"))
    assert len(old) == 8, old                                         # rotate 8: never more
    assert "ffmpeg.log.1" in old and all(n.endswith(".gz") for n in old if n != "ffmpeg.log.1")   # delaycompress
    assert b"\x00" not in (logs / "ffmpeg.log").read_bytes()
    assert not (logs / "browser.log").exists()                       # missingok: an absent log is not an error
