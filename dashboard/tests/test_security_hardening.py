"""Security fixes ported from security/post-only-destructive-actions (f209a40, scripts half of 1bda189).

* sudoers: the two READ-ONLY grants (systemctl --user show, loginctl show-user) carry NOEXEC so they can never launch a
  pager/helper; grants that legitimately run other programs (the zoombot scripts, which call python3/ffmpeg/etc.) must
  NOT get it, or they would break. The real file is still validated with `visudo -cf` at install time (Linux only).
* auto-vacuum.sh: never prunes caches of a Chrome that is running (the live stream kiosk), never touches cookies or
  sign-ins, never needs root (no journal vacuum), and only deletes this user's own stale ffmpeg temp chunks.
"""
import os
import pathlib
import re
import shutil
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
INSTALL = REPO / "dashboard" / "install-dashboard.sh"
VACUUM = REPO / "scripts" / "auto-vacuum.sh"
BASH = shutil.which("bash")


def sudoers_lines() -> list[str]:
    """Every sudoers rule install-dashboard.sh echoes into the drop-in (the quoted text of each echo)."""
    out = []
    for line in INSTALL.read_text(encoding="utf-8").splitlines():
        m = re.match(r'\s*echo "(\$\{DASH_USER\} ALL=.*)"\s*$', line)
        if m:
            out.append(m.group(1))
    return out


def test_read_only_grants_are_noexec():
    rules = sudoers_lines()
    show = [r for r in rules if "/usr/bin/systemctl --user show " in r]
    linger = [r for r in rules if "/usr/bin/loginctl show-user" in r]
    assert len(show) == 1 and len(linger) == 1
    for r in show + linger:
        assert "NOPASSWD: NOEXEC: /usr/bin/" in r, r


def test_noexec_is_not_on_grants_that_must_run_other_programs():
    for r in sudoers_lines():
        if "NOEXEC" in r:
            assert re.search(r"NOEXEC: /usr/bin/(systemctl --user show|loginctl show-user) ", r), \
                f"NOEXEC would break this grant: {r}"
    rules = " ".join(sudoers_lines())
    # spot-check the grants that start the pipeline / write config are unchanged
    for needed in ("NOPASSWD: ${STREAM_APP_DIR}/scripts/write-source.sh",
                   "NOPASSWD: ${STREAM_APP_DIR}/scripts/test-recording.sh",
                   "NOPASSWD: /usr/bin/systemctl --user ${verb} ${unit}.service"):
        assert needed in rules, needed


def test_sudoers_drop_in_is_still_validated_before_install():
    sh = INSTALL.read_text(encoding="utf-8")
    assert 'visudo -cf "${SUDOERS_TMP}"' in sh
    assert sh.index('visudo -cf "${SUDOERS_TMP}"') < sh.index("/etc/sudoers.d/dashboard\n")


# ------------------------------------------------------------------ auto-vacuum.sh


def test_vacuum_never_needs_root_or_touches_the_journal_or_other_users():
    code = "\n".join(l.split("#", 1)[0] for l in VACUUM.read_text(encoding="utf-8").splitlines())
    assert "journalctl" not in code and "sudo" not in code
    assert "/home/zoombot" not in code                                # works on its own $HOME only
    assert '-user "$(id -un)"' in code                                # /tmp: only this user's files
    for forbidden in ("Cookies", "Login Data", "systemctl", "kill"):
        assert forbidden not in code


def _posix(p):
    p = pathlib.Path(p).resolve().as_posix()
    return "/" + p[0].lower() + p[2:] if len(p) > 1 and p[1] == ":" else p


def _profile(root: pathlib.Path, running: bool) -> pathlib.Path:
    root.mkdir(parents=True)
    (root / "Local State").write_text("{}")
    for d in ("Default/Cache/Cache_Data", "Default/Code Cache/js", "GPUCache"):
        (root / d).mkdir(parents=True)
        (root / d / "blob").write_bytes(b"x" * 64)
    (root / "Default" / "Cookies").write_bytes(b"session cookies")
    (root / "Default" / "Login Data").write_bytes(b"sign-in")
    if running:
        (root / "SingletonLock").write_text("host-1234")             # what a running Chrome holds
    return root


@pytest.mark.skipif(BASH is None, reason="bash not available - auto-vacuum.sh behaviour NOT tested")
def test_vacuum_prunes_idle_profiles_and_leaves_the_running_kiosk_alone(tmp_path):
    home = tmp_path / "home"
    live = _profile(home / ".config" / "stream-chrome-profile", running=True)
    idle = _profile(home / ".config" / "chrome-profiles" / "acct-1", running=False)
    (home / ".config" / "not-chrome" / "Cache").mkdir(parents=True)   # no "Local State": not a Chrome profile
    env = dict(os.environ, HOME=_posix(home))
    r = subprocess.run([BASH, _posix(VACUUM)], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "Skipping" in r.stdout and "stream-chrome-profile" in r.stdout
    for d in ("Default/Cache", "Default/Code Cache", "GPUCache"):
        assert (live / d).exists(), f"cache of the RUNNING Chrome was deleted: {d}"
        assert not (idle / d).exists(), f"idle profile cache not pruned: {d}"
    for p in (live, idle):                                            # cookies and sign-ins always survive
        assert (p / "Default" / "Cookies").read_bytes() == b"session cookies"
        assert (p / "Default" / "Login Data").read_bytes() == b"sign-in"
    assert (home / ".config" / "not-chrome" / "Cache").exists()
    assert "completed successfully" in r.stdout
