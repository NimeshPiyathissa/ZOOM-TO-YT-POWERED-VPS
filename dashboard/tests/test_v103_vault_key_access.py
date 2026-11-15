"""Regression tests for v1.0.3: the dashboard must be able to reach its vault key cache, and the installer must prove
that the Remote GUI password really logs in (a page that loads proves neither).

The bug: the installer set /etc/zoom-stream to mode 700 (root only) for its own state, but the dashboard keeps its
key cache there (master.key, root:dashboard 640). The dashboard user could not enter the directory, the secret store
never unlocked, and the Remote GUI failed with "VNC authentication failed" on every fresh install.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import socket
import stat
import subprocess
import threading

import pytest

from app import cli, secret_store, vncauth

REPO = pathlib.Path(__file__).resolve().parents[2]
LIB = REPO / "installer" / "lib"
BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="bash not available")
needs_posix_modes = pytest.mark.skipif(os.name == "nt" or BASH is None, reason="needs real POSIX file modes")


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def _posix(p):
    p = pathlib.Path(p).resolve().as_posix()
    return "/" + p[0].lower() + p[2:] if len(p) > 1 and p[1] == ":" else p


def _bash(tmp_path, body: str):
    script = tmp_path / "harness.sh"
    script.write_text("set -uo pipefail\n"
                      f'export ZS_ETC_DIR="{_posix(tmp_path / "etc")}" ZS_LOG_DIR="{_posix(tmp_path / "log")}" NO_COLOR=1\n'
                      f'source "{_posix(LIB / "common.sh")}"\n' + body + "\n", encoding="utf-8", newline="\n")
    return subprocess.run([BASH, _posix(script)], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)


# ------------------------------------------------------------------ installer: directory modes

@needs_posix_modes
def test_saving_answers_keeps_the_shared_directory_enterable_and_the_state_private(tmp_path):
    r = _bash(tmp_path, "ANS_DOMAIN=none; ANS_ADMIN_PASSWORD=Secret-Value-123; zs_save_answers")
    assert r.returncode == 0, r.stderr
    etc, state = tmp_path / "etc", tmp_path / "etc" / "installer"
    assert stat.S_IMODE(etc.stat().st_mode) == 0o755          # the dashboard user must be able to enter it
    assert stat.S_IMODE(state.stat().st_mode) == 0o700        # the installer's own state stays root-only
    assert stat.S_IMODE((state / "answers.conf").stat().st_mode) == 0o600


@needs_posix_modes
def test_an_install_left_closed_by_an_older_version_is_repaired(tmp_path):
    etc = tmp_path / "etc"
    (etc / "installer").mkdir(parents=True)
    etc.chmod(0o700)                                          # what v1.0.0 - v1.0.2 left behind
    r = _bash(tmp_path, "zs_fix_etc_perms")
    assert r.returncode == 0, r.stderr
    assert stat.S_IMODE(etc.stat().st_mode) == 0o755 and stat.S_IMODE((etc / "installer").stat().st_mode) == 0o700


def test_no_installer_script_closes_the_shared_directory_again():
    for rel in ("install.sh", "update.sh", "set-domain.sh", "uninstall.sh", "installer/lib/common.sh",
                "installer/lib/steps.sh", "dashboard/install-dashboard.sh", "scripts/zs-backup.sh"):
        for line in _read(rel).splitlines():
            code = line.split("#", 1)[0]
            assert not ("chmod 700" in code and ("ZS_ETC_DIR" in code or "/etc/zoom-stream\"" in code
                                                 or code.rstrip().endswith("/etc/zoom-stream"))), f"{rel}: {line}"


def test_the_fix_runs_before_the_dashboard_starts_on_install_and_on_update():
    steps = _read("installer/lib/steps.sh")
    dash = steps[steps.index("zs_step_dashboard() {"):steps.index("# ------------------------------------------------------------------ 11.")]
    assert dash.index("zs_fix_etc_perms") < dash.index("install-dashboard.sh")
    assert dash.index("zs_fix_etc_perms") < dash.index('[[ "$ZS_MODE" == "upgrade" ]] && return 0')   # upgrades too
    restore = _read("scripts/zs-backup.sh")
    assert "chmod 755 /etc/zoom-stream" in restore[restore.index("restore() {"):]


def test_health_check_and_doctor_prove_the_remote_gui_login_not_just_a_page_load():
    steps = _read("installer/lib/steps.sh")
    verify = steps[steps.index("zs_step_verify() {"):steps.index("# ------------------------------------------------------------------ database migrations")]
    assert "zs_dashboard_selfcheck --require-vnc" in verify and "return 1" in verify[verify.index("zs_dashboard_selfcheck"):]
    common = _read("installer/lib/common.sh")
    fn = common[common.index("zs_dashboard_selfcheck() {"):common.index("# True when the broadcast encoder is up.")]
    assert 'sudo -u "$ZS_DASH_USER"' in fn and "app.cli selfcheck" in fn          # as the service user, never as root
    doctor = _read("doctor.sh")
    assert "zs_dashboard_selfcheck" in doctor and "chmod 755 /etc/zoom-stream" in doctor
    assert "unlocks automatically at boot" not in doctor      # the old check only looked whether the file existed


# ------------------------------------------------------------------ app.cli selfcheck: the VNC login itself

class FakeX11vnc:
    """A minimal RFB 3.8 server with VNC authentication, for one or more logins."""

    def __init__(self, password: str | None):
        self.password = password
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                try:
                    conn.settimeout(5)
                    conn.sendall(b"RFB 003.008\n")
                    conn.recv(12)
                    if self.password is None:
                        conn.sendall(bytes([1, 1]))                      # one type: none
                        continue
                    conn.sendall(bytes([1, 2]))                          # one type: VNC authentication
                    conn.recv(1)
                    challenge = os.urandom(16)
                    conn.sendall(challenge)
                    answer = conn.recv(16)
                    good = answer == vncauth.challenge_response(challenge, self.password)
                    conn.sendall((0 if good else 1).to_bytes(4, "big"))
                except OSError:
                    pass

    def close(self):
        self.sock.close()


@pytest.fixture
def x11vnc():
    servers = []

    def make(password):
        s = FakeX11vnc(password)
        servers.append(s)
        return s
    yield make
    for s in servers:
        s.close()


def test_vnc_login_accepts_the_right_password_and_rejects_everything_else(x11vnc):
    srv = x11vnc("Tr0ub4dor-long-password")
    ok, why = cli._vnc_login("127.0.0.1", srv.port, "Tr0ub4dor-long-password")
    assert ok and "accepted" in why
    ok, why = cli._vnc_login("127.0.0.1", srv.port, "another-password")
    assert not ok and "rejected" in why
    ok, why = cli._vnc_login("127.0.0.1", srv.port, "")            # what a locked secret store gives the dashboard
    assert not ok and "has none available" in why
    assert "Tr0ub4dor" not in why                                   # a reason never contains a password
    assert cli._vnc_login("127.0.0.1", x11vnc(None).port, "")[0]   # a server without a password is fine


def test_vnc_login_reports_a_server_that_is_not_running():
    free = socket.socket()
    free.bind(("127.0.0.1", 0))
    port = free.getsockname()[1]
    free.close()
    ok, why = cli._vnc_login("127.0.0.1", port, "whatever", timeout=2)
    assert not ok and "not running" in why


def _selfcheck(monkeypatch, capsys, *, meta, unlocks, vnc, args=()):
    monkeypatch.setattr(secret_store, "read_meta", lambda: meta)
    monkeypatch.setattr(secret_store, "try_auto_unlock", lambda: unlocks)
    monkeypatch.setattr(vncauth, "current_password", lambda: "stored-password" if unlocks else "")
    monkeypatch.setattr(cli, "_vnc_login", lambda host, port, pw, timeout=5.0: vnc)
    with pytest.raises(SystemExit) as exit_info:
        cli.cmd_selfcheck(list(args))
    return exit_info.value.code, capsys.readouterr().out


CACHED = {"unlock_mode": "cached", "manually_locked": False}


def test_selfcheck_passes_on_a_healthy_install(monkeypatch, capsys):
    code, out = _selfcheck(monkeypatch, capsys, meta=CACHED, unlocks=True, vnc=(True, "x11vnc accepted the dashboard's stored VNC password"))
    assert code == 0 and "[FAIL]" not in out and "unlocks automatically" in out and "accepted" in out


def test_selfcheck_fails_and_names_the_fix_when_the_key_directory_is_closed(monkeypatch, capsys):
    """The released bug: /etc/zoom-stream at 700 - the dashboard user cannot enter it."""
    monkeypatch.setattr(os, "access", lambda path, mode: False)
    code, out = _selfcheck(monkeypatch, capsys, meta=CACHED, unlocks=False,
                           vnc=(False, "x11vnc asks for a password, but the dashboard has none available"))
    assert code == 1
    assert "[FAIL] secret store" in out and "chmod 755" in out and "restart dashboard" in out
    assert "[FAIL] remote screen" in out and "none available" in out
    assert "stored-password" not in out


def test_selfcheck_does_not_fail_a_store_that_is_locked_on_purpose(monkeypatch, capsys):
    vnc = (False, "x11vnc is not running (nothing answers on its port)")
    code, out = _selfcheck(monkeypatch, capsys, meta={"unlock_mode": "prompt", "manually_locked": False}, unlocks=False, vnc=vnc)
    assert code == 0 and "manual unlock mode" in out and "[warn] remote screen" in out
    code, out = _selfcheck(monkeypatch, capsys, meta={"unlock_mode": "cached", "manually_locked": True}, unlocks=False, vnc=vnc)
    assert code == 0 and "locked on purpose" in out
    code, out = _selfcheck(monkeypatch, capsys, meta=None, unlocks=False, vnc=vnc)
    assert code == 0 and "not created yet" in out


def test_selfcheck_require_vnc_makes_a_stopped_remote_screen_a_failure(monkeypatch, capsys):
    vnc = (False, "x11vnc is not running (nothing answers on its port)")
    assert _selfcheck(monkeypatch, capsys, meta=CACHED, unlocks=True, vnc=vnc)[0] == 0
    code, out = _selfcheck(monkeypatch, capsys, meta=CACHED, unlocks=True, vnc=vnc, args=["--require-vnc"])
    assert code == 1 and "[FAIL] remote screen" in out


def test_selfcheck_fails_when_x11vnc_rejects_the_stored_password(monkeypatch, capsys):
    code, out = _selfcheck(monkeypatch, capsys, meta=CACHED, unlocks=True,
                           vnc=(False, "x11vnc rejected the dashboard's stored VNC password"))
    assert code == 1 and "rejected" in out


def test_remote_gui_error_names_the_locked_store_instead_of_a_missing_password():
    src = _read("dashboard/app/vnc_proxy.py")
    block = src[src.index("if not password:"):src.index("await upstream.send(vncauth.challenge_response")]
    assert "secret_store.is_initialized() and not secret_store.is_unlocked()" in block
    assert "secret store is locked" in block
