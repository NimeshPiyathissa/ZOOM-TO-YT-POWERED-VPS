"""`python -m app.cli set-password` (app/cli.py).

Regression: the subcommand name used to be parsed as the username, so
`python -m app.cli set-password` created a user literally called
"set-password" and left the real admin's password unchanged. Also covers the
non-interactive modes used by the installer (environment variable and stdin)."""
from __future__ import annotations

import io
import sys

import pytest

from app import cli, config, db, security


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "dashboard.db")
    monkeypatch.delenv("ZOOMBOT_ADMIN_PASSWORD", raising=False)
    db.init_db()


def _run(monkeypatch, *argv, stdin=None):
    monkeypatch.setattr(sys, "argv", ["app.cli", *argv])
    if stdin is not None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    cli.main()


def _users():
    with db.get_conn() as conn:
        return {r["username"]: r["password_hash"] for r in conn.execute("SELECT username, password_hash FROM users")}


def _no_prompt(*a, **k):
    raise AssertionError("must not prompt in non-interactive mode")


def test_set_password_subcommand_targets_admin_not_a_user_named_set_password(monkeypatch):
    monkeypatch.setenv("ZOOMBOT_ADMIN_PASSWORD", "correct horse battery")
    monkeypatch.setattr(cli.getpass, "getpass", _no_prompt)
    _run(monkeypatch, "set-password")
    users = _users()
    assert "set-password" not in users
    assert set(users) == {"admin"}
    assert security.verify_password("correct horse battery", users["admin"])


def test_set_password_with_explicit_username(monkeypatch):
    monkeypatch.setenv("ZOOMBOT_ADMIN_PASSWORD", "correct horse battery")
    _run(monkeypatch, "set-password", "operator")
    assert set(_users()) == {"operator"}
    _run(monkeypatch, "set-password", "--username", "second")
    assert set(_users()) == {"operator", "second"}


def test_set_password_updates_existing_user_and_revokes_sessions(monkeypatch):
    monkeypatch.setenv("ZOOMBOT_ADMIN_PASSWORD", "first password 123")
    _run(monkeypatch, "set-password")
    with db.get_conn() as conn:
        uid = conn.execute("SELECT id FROM users WHERE username='admin'").fetchone()["id"]
    security.create_session(uid, "203.0.113.5")
    monkeypatch.setenv("ZOOMBOT_ADMIN_PASSWORD", "second password 456")
    _run(monkeypatch, "set-password", "admin")
    users = _users()
    assert set(users) == {"admin"}
    assert security.verify_password("second password 456", users["admin"])
    assert not security.verify_password("first password 123", users["admin"])
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM sessions WHERE user_id=?", (uid,)).fetchone()["n"] == 0


def test_set_password_from_stdin(monkeypatch):
    monkeypatch.setattr(cli.getpass, "getpass", _no_prompt)
    _run(monkeypatch, "set-password", "--password-stdin", stdin="from stdin pass 99\n")
    assert security.verify_password("from stdin pass 99", _users()["admin"])


def test_set_password_rejects_short_password_non_interactively(monkeypatch):
    monkeypatch.setenv("ZOOMBOT_ADMIN_PASSWORD", "short")
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, "set-password")
    assert exc.value.code == 1
    assert _users() == {}


def test_set_password_rejects_invalid_username(monkeypatch):
    monkeypatch.setenv("ZOOMBOT_ADMIN_PASSWORD", "correct horse battery")
    for bad in ("", "has space", "-flag", "x" * 65):
        with pytest.raises(SystemExit):
            _run(monkeypatch, "set-password", "--username", bad)
    assert _users() == {}


def test_interactive_prompt_still_works(monkeypatch):
    answers = iter(["interactive pass 1", "interactive pass 1"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
    _run(monkeypatch, "set-password")
    assert security.verify_password("interactive pass 1", _users()["admin"])


def test_no_subcommand_keeps_legacy_behaviour(monkeypatch):
    """`python -m app.cli` and `python -m app.cli <username>` predate subcommands."""
    monkeypatch.setenv("ZOOMBOT_ADMIN_PASSWORD", "correct horse battery")
    _run(monkeypatch)
    _run(monkeypatch, "legacyuser")
    assert set(_users()) == {"admin", "legacyuser"}
