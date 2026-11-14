"""setup-https.sh, zs-backup.sh and stream-profiles.sh, executed against a sandbox root with fake caddy/systemctl/apt.
The guided installer itself (install.sh + installer/lib) is covered by test_install_wizard.py; what only a real
Ubuntu 24.04 server can prove is recorded in docs/testing.md."""
import os
import pathlib
import re
import shutil
import subprocess
import tarfile

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="bash not available - installer scripts NOT executed")


def _posix(p):
    p = pathlib.Path(p).resolve().as_posix()
    return "/" + p[0].lower() + p[2:] if len(p) > 1 and p[1] == ":" else p


def _code(text: str) -> str:
    return "\n".join(l.split("#", 1)[0] if not l.lstrip().startswith("#!") else "" for l in text.splitlines())


# ------------------------------------------------------------------ setup-https.sh

FAKES = {
    "caddy": '#!/usr/bin/env bash\necho "caddy $*" >> "$ZS_CALLS"\n[[ -z "${CADDY_REJECT:-}" ]]\n',
    "systemctl": '#!/usr/bin/env bash\necho "systemctl $*" >> "$ZS_CALLS"\n[[ "$1" == is-active ]] && exit 0\nexit 0\n',
    "apt-get": '#!/usr/bin/env bash\necho "apt-get $*" >> "$ZS_CALLS"\n',
    "apt-cache": '#!/usr/bin/env bash\nexit 0\n',
    "curl": '#!/usr/bin/env bash\necho 203.0.113.7\n',
    "getent": '#!/usr/bin/env bash\n[[ "$1" == ahostsv4 ]] && echo "203.0.113.7 STREAM $2"\nexit 0\n',
    "chgrp": '#!/usr/bin/env bash\nexit 0\n',
}


@pytest.fixture
def https(tmp_path):
    etc, certs, fakebin = tmp_path / "etc", tmp_path / "certs", tmp_path / "bin"
    for d in (etc, certs, fakebin):
        d.mkdir()
    (certs / "cert.pem").write_text("CERT")
    (certs / "key.pem").write_text("KEY")
    for name, body in FAKES.items():
        (fakebin / name).write_text(body, encoding="utf-8", newline="\n")
        os.chmod(fakebin / name, 0o755)
    calls = tmp_path / "calls"
    calls.write_text("")

    def run(*args, **extra):
        env = dict(os.environ, ZS_ETC=_posix(etc), ZS_CERT_SRC=_posix(certs), ZS_CALLS=_posix(calls),
                   PATH=_posix(fakebin) + ":" + os.environ.get("PATH", ""), **extra)
        r = subprocess.run([BASH, _posix(REPO / "scripts" / "setup-https.sh"), *args], capture_output=True, text=True,
                           env=env, timeout=60, stdin=subprocess.DEVNULL)
        return r, calls.read_text()
    run.etc = etc
    return run


@needs_bash
def test_https_domain_config_is_rendered_validated_and_only_caddy_is_reloaded(https):
    r, calls = https("--domain", "Stream.Example.com", "--email", "ops@example.com")
    assert r.returncode == 0, r.stderr
    conf = (https.etc / "caddy" / "Caddyfile").read_text()
    assert conf.startswith("# managed-by: zoom-stream setup-https.sh")
    assert "stream.example.com {" in conf and "email ops@example.com" in conf
    assert "reverse_proxy 127.0.0.1:8000" in conf and "tls " not in conf
    assert "caddy validate" in calls and "systemctl reload caddy" in calls
    for unit in ("dashboard", "ffmpeg-stream", "zoom", "xvfb"):
        assert unit not in calls                                     # nothing but Caddy is touched
    assert "matches this server" in r.stdout


@needs_bash
def test_https_without_domain_serves_the_self_signed_certificate(https):
    r, _ = https()
    assert r.returncode == 0, r.stderr
    conf = (https.etc / "caddy" / "Caddyfile").read_text()
    assert ":443 {" in conf and "tls " in conf and "zoom-stream/cert.pem" in conf
    assert (https.etc / "caddy" / "zoom-stream" / "key.pem").read_text() == "KEY"


@needs_bash
def test_https_is_idempotent_and_backs_up_before_replacing(https):
    https("--domain", "a.example.com")
    r, calls = https("--domain", "a.example.com")
    assert "already up to date" in r.stdout and not list((https.etc / "caddy").glob("Caddyfile.bak.*"))
    r, _ = https("--domain", "b.example.com")
    assert r.returncode == 0
    backups = list((https.etc / "caddy").glob("Caddyfile.bak.*"))
    assert len(backups) == 1 and "a.example.com {" in backups[0].read_text()
    assert "b.example.com {" in (https.etc / "caddy" / "Caddyfile").read_text()


@needs_bash
def test_https_never_overwrites_a_foreign_caddyfile_without_yes(https):
    (https.etc / "caddy").mkdir()
    mine = "# hand-written by the customer\nother.site { respond \"hi\" }\n"
    (https.etc / "caddy" / "Caddyfile").write_text(mine)
    r, calls = https("--domain", "a.example.com")
    assert r.returncode == 0 and "left unchanged" in r.stdout
    assert (https.etc / "caddy" / "Caddyfile").read_text() == mine and "reload" not in calls
    r, _ = https("--domain", "a.example.com", "--yes")
    assert r.returncode == 0 and "a.example.com {" in (https.etc / "caddy" / "Caddyfile").read_text()
    assert list((https.etc / "caddy").glob("Caddyfile.bak.*"))[0].read_text() == mine


@needs_bash
def test_https_config_rejected_by_caddy_is_never_installed(https):
    https("--domain", "a.example.com")
    before = (https.etc / "caddy" / "Caddyfile").read_text()
    r, calls = https("--domain", "b.example.com", CADDY_REJECT="1")
    assert r.returncode == 1 and "nothing was changed" in r.stderr
    assert (https.etc / "caddy" / "Caddyfile").read_text() == before
    assert calls.count("reload") == 1                                # only the first, successful run reloaded


@needs_bash
@pytest.mark.parametrize("bad", ["a b.com", "x.com{", "evil.com\nrespond", "-x.com", "localhost", "a" * 64 + ".com"])
def test_https_rejects_anything_that_is_not_a_plain_domain(https, bad, tmp_path):
    # Passed through an environment variable: Windows' argv conversion into bash would otherwise strip the very
    # characters (newline, braces) this test is about.
    wrapper = tmp_path / "w.sh"
    wrapper.write_text('exec bash "$SETUP" --domain "$BAD_DOMAIN"\n', encoding="utf-8", newline="\n")
    env = dict(os.environ, BAD_DOMAIN=bad, SETUP=_posix(REPO / "scripts" / "setup-https.sh"),
               ZS_ETC=_posix(https.etc), ZS_CERT_SRC=_posix(tmp_path))
    r = subprocess.run([BASH, _posix(wrapper)], capture_output=True, text=True, env=env, timeout=60, stdin=subprocess.DEVNULL)
    assert r.returncode == 1 and "not a valid domain" in r.stderr
    assert not (https.etc / "caddy" / "Caddyfile").exists()


# ------------------------------------------------------------------ zs-backup.sh

@pytest.fixture
def box(tmp_path):
    root = tmp_path / "root"
    files = {
        "home/zoombot/zoom-stream/scripts/stream.sh": "v1-stream",
        "home/zoombot/zoom-stream/current-source.env": 'SOURCE_TYPE="zoom"\n',
        "home/zoombot/zoom-stream/logs/ffmpeg.log": "big log",
        "home/dashboard/app/app/main.py": "v1-main",
        "home/dashboard/data/secrets.enc.json": "{ciphertext}",
        "etc/zoom-stream/master.key": "KEY-NEVER-BY-DEFAULT",
        "etc/sudoers.d/dashboard": "rules",
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)

    def run(*args):
        env = dict(os.environ, ZS_ROOT=_posix(root), ZS_BACKUP_DIR=_posix(tmp_path / "backups"))
        return subprocess.run([BASH, _posix(REPO / "scripts" / "zs-backup.sh"), *args], capture_output=True, text=True,
                              env=env, timeout=120, stdin=subprocess.DEVNULL)
    run.root, run.dir = root, tmp_path / "backups"
    return run


def _members(path):
    with tarfile.open(path) as t:
        return [m.name for m in t.getmembers() if m.isfile()]


@needs_bash
def test_backup_contents_exclude_logs_and_the_vault_key(box):
    r = box("create", "--label", "t1")
    assert r.returncode == 0, r.stderr
    out = pathlib.Path(r.stdout.strip().splitlines()[-1].replace("/", os.sep) if os.name != "nt" else
                       next(box.dir.glob("zs-t1-*.tar.gz")))
    names = _members(out)
    assert "home/dashboard/data/secrets.enc.json" in names and "home/zoombot/zoom-stream/scripts/stream.sh" in names
    assert not any("logs/" in n for n in names) and not any("master.key" in n for n in names)
    assert (out.parent / (out.name + ".sha256")).exists()
    r = box("create", "--label", "t2", "--include-master-key")
    assert "WARNING" in r.stdout
    assert "etc/zoom-stream/master.key" in _members(next(box.dir.glob("zs-t2-*.tar.gz")))


@needs_bash
def test_restore_round_trip_with_a_safety_backup(box):
    box("create", "--label", "good")
    good = next(box.dir.glob("zs-good-*.tar.gz"))
    (box.root / "home/dashboard/app/app/main.py").write_text("BROKEN UPGRADE")
    r = box("restore", str(good) if os.name != "nt" else _posix(good))
    assert r.returncode == 1 and "needs confirmation" in r.stderr     # no tty, no --yes -> refused
    assert (box.root / "home/dashboard/app/app/main.py").read_text() == "BROKEN UPGRADE"
    r = box("restore", _posix(good), "--yes")
    assert r.returncode == 0, r.stderr
    assert (box.root / "home/dashboard/app/app/main.py").read_text() == "v1-main"
    assert "encoder was NOT started" in r.stdout
    safety = next(box.dir.glob("zs-pre-restore-*.tar.gz"))             # the broken state can be got back too
    with tarfile.open(safety) as t:
        assert t.extractfile("home/dashboard/app/app/main.py").read() == b"BROKEN UPGRADE"


@needs_bash
def test_restore_is_refused_while_the_encoder_is_running(box):
    box("create", "--label", "x")
    good = next(box.dir.glob("zs-x-*.tar.gz"))
    (box.root / "run").mkdir()
    (box.root / "run" / "zs-encoder-active").write_text("1")
    (box.root / "home/dashboard/app/app/main.py").write_text("CURRENT")
    r = box("restore", _posix(good), "--yes")
    assert r.returncode == 1 and "never interrupts a stream" in r.stderr
    assert (box.root / "home/dashboard/app/app/main.py").read_text() == "CURRENT"


@needs_bash
def test_restore_refuses_a_damaged_or_crafted_archive(box, tmp_path):
    box("create", "--label", "x")
    good = next(box.dir.glob("zs-x-*.tar.gz"))
    data = bytearray(good.read_bytes())
    data[-40] ^= 0xFF
    good.write_bytes(bytes(data))
    r = box("restore", _posix(good), "--yes")
    assert r.returncode == 1 and "CHECKSUM MISMATCH" in r.stderr
    evil = box.dir / "zs-evil-20260101-000000.tar.gz"                  # valid checksum, but writes outside the install
    import io
    payload = b"* * * * * root sh\n"
    info = tarfile.TarInfo("etc/cron.d/x")
    info.size = len(payload)
    with tarfile.open(evil, "w:gz") as t:
        t.addfile(info, io.BytesIO(payload))
    import hashlib
    (box.dir / (evil.name + ".sha256")).write_text(f"{hashlib.sha256(evil.read_bytes()).hexdigest()}  {evil.name}\n", newline="\n")
    r = box("restore", _posix(evil), "--yes")
    assert r.returncode == 1 and "unexpected paths" in r.stderr
    assert not (box.root / "etc" / "cron.d").exists()


# ------------------------------------------------------------------ streaming profiles (fresh install only)

PROFILES = REPO / "scripts" / "stream-profiles.sh"
STARTER = {"RESOLUTION": "1280x720", "FPS": "30", "VIDEO_BITRATE": "3000", "AUDIO_BITRATE": "160",
           "X264_PRESET": "ultrafast"}
ADVANCED_1080 = {"RESOLUTION": "1920x1080", "FPS": "30", "VIDEO_BITRATE": "6000", "AUDIO_BITRATE": "192",
                 "X264_PRESET": "superfast"}


def _env_lines(text):
    return dict(line.split("=", 1) for line in text.strip().splitlines() if "=" in line)


@needs_bash
@pytest.mark.parametrize("args, expected", [
    ([], STARTER),
    (["720p30"], STARTER),
    (["720p30", "2500"], {**STARTER, "VIDEO_BITRATE": "2500"}),
    (["1080p30"], ADVANCED_1080),
])
def test_stream_profiles(args, expected):
    r = subprocess.run([BASH, _posix(PROFILES), *args], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and _env_lines(r.stdout) == expected


@needs_bash
@pytest.mark.parametrize("args", [["4k"], ["720p30", "999"], ["720p30", "8001"], ["720p30", "3000k"], ["1080p60"]])
def test_stream_profiles_reject_unknown_profiles_and_bad_bitrates(args):
    r = subprocess.run([BASH, _posix(PROFILES), *args], capture_output=True, text=True, timeout=30)
    assert r.returncode == 1 and r.stdout == "" and r.stderr.strip()


def test_dashboard_starter_preset_matches_the_install_default_and_1080p_is_advanced():
    from app import config
    starter = config.OUTPUT_PRESETS["720p30-starter"]
    keys = ("RESOLUTION", "FPS", "VIDEO_BITRATE", "X264_PRESET")
    assert {k: starter[k] for k in keys} == {k: STARTER[k] for k in keys}
    for key, preset in config.OUTPUT_PRESETS.items():
        if preset["RESOLUTION"] == "1920x1080":
            assert "advanced" in preset["label"], key


def test_runtime_fallbacks_are_unchanged_so_existing_installs_keep_their_behaviour():
    """An old .env without RESOLUTION etc. relies on the scripts' fallbacks; changing them would silently change an
    existing installation. The new default lives only in what a fresh install writes."""
    stream = (REPO / "scripts" / "stream.sh").read_text(encoding="utf-8")
    for line in ('RESOLUTION="${RESOLUTION:-1920x1080}"', 'FPS="${FPS:-30}"', 'VIDEO_BITRATE="${VIDEO_BITRATE:-6000}"',
                 'X264_PRESET="${X264_PRESET:-veryfast}"'):
        assert line in stream
