"""The two state-changing actions that used to answer GET (recordings clean,
Zoom clean-feed) are POST-only, CSRF-checked and rate-limited; the dashboard's
own callers keep working unchanged."""
import time
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest
from starlette.testclient import TestClient

from app.main import app
from app import config, db, deps, recording, security


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    deps._rate_buckets.clear()
    db.init_db()
    with db.get_conn() as conn:
        uid = conn.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES ('admin', ?, ?)",
            (security.hash_password("correct-horse-battery-staple"), time.time()),
        ).lastrowid
    sid, csrf = security.create_session(uid, "127.0.0.1")
    c = TestClient(app, cookies={config.COOKIE_NAME: sid})
    c.csrf = csrf
    yield c
    deps._rate_buckets.clear()


CLEAN_OK = {"ok": True, "deleted_count": 0, "freed_mb": 0.0}


@pytest.mark.parametrize("path", ["/api/recordings/clean", "/api/zoom/clean-feed"])
def test_get_no_longer_performs_the_action(client, path):
    with patch.object(recording, "clean_recordings", return_value=CLEAN_OK) as rec, \
         patch("app.zoom_web.apply_cleanfeed_async", new=AsyncMock(return_value={"ok": True})) as cf:
        res = client.get(path + "?force=true")
    assert res.status_code == 405
    rec.assert_not_called()
    cf.assert_not_called()


@pytest.mark.parametrize("path", ["/api/recordings/clean", "/api/zoom/clean-feed"])
def test_post_without_csrf_is_refused(client, path):
    with patch.object(recording, "clean_recordings", return_value=CLEAN_OK) as rec, \
         patch("app.zoom_web.apply_cleanfeed_async", new=AsyncMock(return_value={"ok": True})) as cf:
        res = client.post(path, json={"force": True})
    assert res.status_code == 403
    rec.assert_not_called()
    cf.assert_not_called()


@pytest.mark.parametrize("path", ["/api/recordings/clean", "/api/zoom/clean-feed"])
def test_anonymous_is_refused(path):
    assert TestClient(app).post(path, json={}).status_code == 401


def test_recordings_clean_post_with_csrf_works_like_the_settings_button(client):
    # Exactly what static/js/config.js sends.
    with patch.object(recording, "clean_recordings", return_value=CLEAN_OK) as rec:
        res = client.post("/api/recordings/clean", json={"force": True}, headers={"X-CSRF-Token": client.csrf})
    assert res.status_code == 200 and res.json()["ok"] is True
    rec.assert_called_once_with(max_age_hours=24.0, force=True)


@pytest.mark.parametrize("body,expected_age", [
    ({"max_age_hours": -5}, 0.0), ({"max_age_hours": "x"}, 24.0), ([], 24.0), ("str", 24.0),
])
def test_recordings_clean_input_is_validated(client, body, expected_age):
    with patch.object(recording, "clean_recordings", return_value=CLEAN_OK) as rec:
        res = client.post("/api/recordings/clean", json=body, headers={"X-CSRF-Token": client.csrf})
    assert res.status_code == 200
    assert rec.call_args.kwargs["max_age_hours"] == expected_age


def test_recordings_clean_is_rate_limited(client):
    h = {"X-CSRF-Token": client.csrf}
    with patch.object(recording, "clean_recordings", return_value=CLEAN_OK) as rec:
        codes = [client.post("/api/recordings/clean", json={}, headers=h).status_code for _ in range(4)]
    assert codes[:3] == [200, 200, 200] and codes[3] == 429
    assert rec.call_count == 3


def test_clean_feed_post_with_csrf_works_like_the_studio_button(client):
    # Exactly what static/js/studio.js sends.
    with patch("app.zoom_web.apply_cleanfeed_async", new=AsyncMock(return_value={"ok": False})) as cf:
        res = client.post("/api/zoom/clean-feed", headers={"X-CSRF-Token": client.csrf, "Content-Type": "application/json"})
    assert res.status_code == 200
    cf.assert_awaited_once()


def test_frontend_callers_use_post_with_csrf():
    """Backward-compat guard: if a frontend caller ever switches back to GET
    (or drops the CSRF header) this fails before a deploy does."""
    root = Path(__file__).resolve().parents[1] / "static" / "js"
    cfg = (root / "config.js").read_text(encoding="utf-8")
    studio = (root / "studio.js").read_text(encoding="utf-8")
    i = cfg.index('"/api/recordings/clean"')
    assert 'method: "POST"' in cfg[i:i + 200]
    assert "apiFetch(" in cfg[i - 20:i]  # apiFetch adds X-CSRF-Token to every non-GET
    j = studio.index('"/api/zoom/clean-feed"')
    window = studio[j:j + 250]
    assert 'method: "POST"' in window and "X-CSRF-Token" in window
