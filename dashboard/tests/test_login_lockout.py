"""Login lockout bookkeeping (app/security.py).

A successful login must clear only the FAILED attempts for that ip/username:
the successful rows are the audit trail of who signed in and must survive."""
from __future__ import annotations

import pytest

from app import config, db, security


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "dashboard.db")
    db.init_db()


def _rows(ip="203.0.113.5", username="admin"):
    with db.get_conn() as conn:
        return [(r["ip"], r["username"], r["success"]) for r in conn.execute(
            "SELECT ip, username, success FROM login_attempts WHERE ip=? AND username=? ORDER BY id", (ip, username))]


def test_clear_login_failures_keeps_successful_attempts():
    ip, user = "203.0.113.5", "admin"
    security.record_login_attempt(ip, user, True)
    for _ in range(3):
        security.record_login_attempt(ip, user, False)
    security.record_login_attempt(ip, user, True)

    security.clear_login_failures(ip, user)

    assert _rows(ip, user) == [(ip, user, 1), (ip, user, 1)]


def test_clear_login_failures_only_touches_that_ip_and_username():
    security.record_login_attempt("203.0.113.5", "admin", False)
    security.record_login_attempt("203.0.113.9", "admin", False)
    security.record_login_attempt("203.0.113.5", "other", False)

    security.clear_login_failures("203.0.113.5", "admin")

    assert _rows("203.0.113.5", "admin") == []
    assert _rows("203.0.113.9", "admin") == [("203.0.113.9", "admin", 0)]
    assert _rows("203.0.113.5", "other") == [("203.0.113.5", "other", 0)]


def test_lockout_is_lifted_after_clearing(monkeypatch):
    monkeypatch.setattr(config, "LOGIN_MAX_FAILURES", 3)
    ip, user = "203.0.113.5", "admin"
    for _ in range(3):
        security.record_login_attempt(ip, user, False)
    assert security.is_locked_out(ip, user) is True

    security.clear_login_failures(ip, user)

    assert security.is_locked_out(ip, user) is False
