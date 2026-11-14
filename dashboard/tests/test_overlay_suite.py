"""Comprehensive unit tests for:
- OBS-Style Text Overlay Studio (/overlay, /api/overlay, /api/overlay/toggle)
- Emergency Failover BRB Slate (/api/slate/brb)
- Telegram Broadcast Alerts (telegram.py)
- Local Stream Recording & Safety Halt (/api/record)
"""
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from app.main import app
from app import config, db, overlay, slate, telegram, control, env_store
from app import sources as sources_mod


@pytest.fixture
def client(tmp_path, monkeypatch):
    test_db = tmp_path / "test_dashboard.db"
    monkeypatch.setattr(config, "DB_PATH", test_db)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OVERLAY_CONFIG_FILE", tmp_path / "data" / "overlay.json")
    monkeypatch.setattr(config, "BRB_SLATE_FILE", tmp_path / "data" / "brb_slate.json")
    db.init_db()

    from app import security
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            ("admin", security.hash_password("correct-horse-battery-staple"), time.time()),
        )
        user_id = cur.lastrowid
    session_id, csrf_token = security.create_session(user_id, "127.0.0.1")

    client = TestClient(app, cookies={config.COOKIE_NAME: session_id})
    client.session = {"id": session_id, "csrf_token": csrf_token, "username": "admin"}
    return client


# --- 1. Watermark Studio (page + API) ---
# The end-to-end behaviour (settings -> env file -> shell filter -> real FFmpeg frames, ON/OFF, fonts, Go Live safety)
# is covered by tests/test_watermark_pipeline.py. These are the page/API contract checks.

@pytest.fixture(autouse=True)
def _no_real_units(monkeypatch):
    """Nothing in this file may reach sudo/systemd: the encoder is reported as not running unless a test says so."""
    monkeypatch.setattr(control, "unit_show", lambda unit: {"unit": unit, "phase": "IDLE", "main_pid": 0})
    monkeypatch.setattr(control, "watermark_is_running", lambda: False)


def _post(client, path, body=None):
    return client.post(path, json=body or {}, headers={"X-CSRF-Token": client.session["csrf_token"]})


def test_overlay_page_authenticated(client):
    res = client.get("/overlay")
    assert res.status_code == 200
    html = res.text
    assert "Watermark Studio" in html
    assert "16:9 Broadcast Preview" in html
    assert "TURN WATERMARK ON" in html or "TURN WATERMARK OFF" in html
    assert "Font Family" in html
    assert "Text Outline / Stroke" in html
    assert "Background Holding Box" in html
    assert "Drop Shadow" in html
    assert "Changes will apply to the next stream." in html


def test_overlay_api_get_and_save(client, monkeypatch):
    monkeypatch.setattr(control, "write_watermark_config", lambda updated: {})
    monkeypatch.setattr(sources_mod, "get_active_source", lambda: None)
    res = client.get("/api/overlay")
    assert res.status_code == 200
    data = res.json()
    assert "text" in data
    assert "font_family" in data
    assert "pos_x" in data

    updates = {
        "text": "BREAKING NEWS",
        "font_family": "Bebas Neue",
        "font_size": 54,
        "font_color": "#FF0000",
        "outline_enabled": True,
        "outline_color": "#FFFFFF",
        "pos_x": 10.0,
        "pos_y": 85.0,
        "visible": True,                # deliberately ignored by a settings save - only the switch changes it
    }
    res = _post(client, "/api/overlay", updates)
    assert res.status_code == 200
    saved = res.json()["state"]
    assert saved["text"] == "BREAKING NEWS"
    assert saved["font_family"] == "Bebas Neue"
    assert saved["font_size"] == 54
    assert saved["visible"] is False
    assert res.json()["rejected"] == {}


def test_overlay_api_toggle(client, monkeypatch):
    monkeypatch.setattr(control, "write_watermark_config", lambda updated: {})
    monkeypatch.setattr(sources_mod, "get_active_source", lambda: None)
    first_state = _post(client, "/api/overlay/toggle").json()["state"]["visible"]
    second_state = _post(client, "/api/overlay/toggle").json()["state"]["visible"]
    assert first_state != second_state
    assert _post(client, "/api/overlay/toggle", {"enabled": True}).json()["state"]["visible"] is True
    assert _post(client, "/api/overlay/toggle", {"enabled": True}).json()["state"]["visible"] is True


def test_dom_overlay_js_only_removes():
    js = overlay.generate_overlay_js({"text": "TEST OVERLAY", "visible": True})
    assert "livestream-watermark-overlay" in js and "obs-text-overlay" in js and ".remove()" in js
    assert "TEST OVERLAY" not in js and "createElement" not in js


def test_extended_typography_suite(client):
    assert len(overlay.GOOGLE_FONTS) >= 60
    for cat in ("High-Impact & Broadcast Titles", "Modern & Clean Sans-Serif", "Condensed & Tall", "Tech, Sci-Fi & Gaming",
                "Elegant & Editorial Serif", "Handwritten & Script", "Sri Lankan / Sinhala Unicode Support"):
        assert cat in overlay.GOOGLE_FONT_CATEGORIES

    sinhala_fonts = overlay.GOOGLE_FONT_CATEGORIES["Sri Lankan / Sinhala Unicode Support"]
    assert "Noto Sans Sinhala" in sinhala_fonts
    assert "Noto Serif Sinhala" in sinhala_fonts
    assert "Abhaya Libre" in sinhala_fonts
    for fam in sinhala_fonts:                       # really contain Sinhala letters, per the verified manifest
        assert "sinhala" in overlay.font_entry(fam)["scripts"]

    assert overlay.font_key("Noto Sans Sinhala") == "noto-sans-sinhala"
    assert overlay.font_key("Press Start 2P") == "press-start-2p"

    res = client.get("/overlay")
    assert res.status_code == 200
    html = res.text
    assert '<optgroup label="High-Impact &amp; Broadcast Titles">' in html or '<optgroup label="High-Impact & Broadcast Titles">' in html
    assert '<optgroup label="Sri Lankan / Sinhala Unicode Support">' in html
    assert "Noto Sans Sinhala" in html


def test_visual_font_picker_suite(client):
    res = client.get("/overlay")
    assert res.status_code == 200
    html = res.text
    assert "custom-font-picker" in html
    assert "font-picker-trigger" in html
    assert "font-picker-search" in html
    assert "specimen-badge" in html
    assert "අආ ශ්‍රී" in html
    assert "Ag 123" in html
    assert 'data-font="Noto Sans Sinhala"' in html
    assert 'data-font="Anton"' in html
    assert 'name="fontFamily"' in html
    assert html.count('class="font-picker-item') == len(overlay.GOOGLE_FONTS)
    assert html.count("@font-face") == len(overlay.GOOGLE_FONTS)          # the preview uses the installed font files
    assert "fonts.googleapis.com" not in html


def test_overlay_api_status(client):
    res = client.get("/api/overlay/status")
    assert res.status_code == 200
    data = res.json()
    assert data["encoder_live"] is False and data["pending"] is False
    assert data["configured_enabled"] is False
    assert "OFF" in data["message"]


def test_overlay_api_reinject_only_cleans_up(client, monkeypatch):
    from app import cdp
    seen = []

    async def mock_evaluate(expression, user_gesture=False):
        seen.append(expression)
        return {"value": {"removed": 2}}

    monkeypatch.setattr(cdp, "evaluate", mock_evaluate)
    res = _post(client, "/api/overlay/reinject")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["status"]["connected"] is True and data["status"]["removed"] == 2
    assert len(seen) == 1 and "createElement" not in seen[0]


# --- 1b. the encoder-burned watermark wiring ---

def test_overlay_save_writes_real_watermark_config(client, monkeypatch):
    """Saving must reach the encoder-consumed config (current-source.env's WATERMARK_* keys), not just overlay.json."""
    calls = []
    monkeypatch.setattr(control, "write_watermark_config", lambda updated: calls.append(updated) or {})
    monkeypatch.setattr(sources_mod, "get_active_source", lambda: None)

    res = _post(client, "/api/overlay", {"text": "ACME"})
    assert res.status_code == 200
    assert len(calls) == 1 and calls[0]["text"] == "ACME" and calls[0]["visible"] is False
    assert res.json()["pipeline_synced"] is True

    res = _post(client, "/api/overlay/toggle", {"enabled": True})
    assert len(calls) == 2 and calls[1]["visible"] is True and calls[1]["text"] == "ACME"
    assert res.json()["requires_restart"] is False            # nothing is live: there is nothing to wait for


def test_overlay_save_auto_switches_copy_mode_direct_source(client, monkeypatch):
    """Turning the watermark on for a copy-mode direct source switches it to re-encode, since a burned-in filter
    can't ride along with stream copy."""
    active_source = {
        "id": 7, "name": "My Stream", "type": "direct", "url": "https://example.com/live.m3u8",
        "options": {"mode": "copy"}, "account_id": None,
    }
    monkeypatch.setattr(sources_mod, "get_active_source", lambda: active_source)
    update_calls = []
    monkeypatch.setattr(
        sources_mod, "update_source",
        lambda sid, name, type_, url, options, account_id=None: update_calls.append((sid, options)),
    )
    monkeypatch.setattr(control, "write_watermark_config", lambda updated: {})
    applied = []
    monkeypatch.setattr(control, "write_current_source", lambda source: applied.append(source))

    res = _post(client, "/api/overlay/toggle", {"enabled": True})
    assert res.status_code == 200
    assert len(update_calls) == 1
    assert update_calls[0][0] == 7
    assert update_calls[0][1]["mode"] == "reencode"
    assert len(applied) == 1


def test_overlay_save_leaves_reencode_direct_source_alone(client, monkeypatch):
    active_source = {
        "id": 7, "name": "My Stream", "type": "direct", "url": "https://example.com/live.m3u8",
        "options": {"mode": "reencode"}, "account_id": None,
    }
    monkeypatch.setattr(sources_mod, "get_active_source", lambda: active_source)
    update_calls, applied = [], []
    monkeypatch.setattr(sources_mod, "update_source", lambda *a, **k: update_calls.append((a, k)))
    monkeypatch.setattr(control, "write_watermark_config", lambda updated: {})
    monkeypatch.setattr(control, "write_current_source", lambda source: applied.append(source))

    _post(client, "/api/overlay/toggle", {"enabled": True})
    assert update_calls == []  # already re-encode - nothing to switch
    assert applied == []       # ...and the source itself is not re-applied for a watermark change


def test_overlay_save_reports_a_failed_pipeline_write(client, monkeypatch):
    """A locked-down/undeployed pipeline must not turn saving into a 500 - but it must not be hidden either."""
    def boom(updated):
        raise control.ControlError("sudo not available")
    monkeypatch.setattr(control, "write_watermark_config", boom)
    monkeypatch.setattr(sources_mod, "get_active_source", lambda: None)

    res = _post(client, "/api/overlay", {"text": "still saved"})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True and body["state"]["text"] == "still saved"
    assert body["pipeline_synced"] is False and "could not be updated" in body["sync_error"]


def test_overlay_status_reports_what_is_live(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "STREAM_LOGS_DIR", tmp_path)
    overlay.set_enabled(False)
    want = overlay.encoder_env(overlay.get_overlay_state())["WATERMARK_CONFIG_HASH"]
    (tmp_path / "watermark.status").write_text("STATE=on\nHASH=some-older-settings\nFONT=anton\nPID=321\n", encoding="utf-8")
    monkeypatch.setattr(control, "unit_show", lambda unit: {"unit": unit, "phase": "LIVE", "main_pid": 321})

    data = client.get("/api/overlay/status").json()
    assert data["encoder_live"] is True and data["live_known"] is True
    assert data["encoder_active"] is True            # the running encoder still draws the watermark...
    assert data["configured_enabled"] is False       # ...although it has been switched off since
    assert data["pending"] is True and data["requires_restart"] is True
    assert data["message"].endswith("Changes will apply to the next stream.")

    (tmp_path / "watermark.status").write_text(f"STATE=off\nHASH={want}\nPID=321\n", encoding="utf-8")
    data = client.get("/api/overlay/status").json()
    assert data["pending"] is False and data["encoder_active"] is False


def test_overlay_image_upload_rejects_bad_extension(client):
    res = client.post(
        "/api/overlay/image",
        files={"image": ("logo.gif", b"not-really-a-gif", "image/gif")},
        headers={"X-CSRF-Token": client.session["csrf_token"]},
    )
    assert res.status_code == 400


def test_overlay_image_upload_rejects_oversized_file(client):
    big = b"\x00" * (5 * 1024 * 1024 + 1)
    res = client.post(
        "/api/overlay/image",
        files={"image": ("logo.png", big, "image/png")},
        headers={"X-CSRF-Token": client.session["csrf_token"]},
    )
    assert res.status_code == 400


def test_overlay_image_upload_writes_via_zoombot_bridge(client, monkeypatch):
    monkeypatch.setattr(control, "write_watermark_image", lambda content, ext: "/home/zoombot/zoom-stream/watermarks/logo.png")
    monkeypatch.setattr(control, "write_watermark_config", lambda updated: {})
    monkeypatch.setattr(sources_mod, "get_active_source", lambda: None)

    res = client.post(
        "/api/overlay/image",
        files={"image": ("logo.png", b"\x89PNG\r\n fake but under the size limit", "image/png")},
        headers={"X-CSRF-Token": client.session["csrf_token"]},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["image_path"] == "/home/zoombot/zoom-stream/watermarks/logo.png"
    assert data["state"]["mode"] == "image"
    assert data["state"]["image_path"] == "/home/zoombot/zoom-stream/watermarks/logo.png"


# --- 2. Emergency Failover BRB Slate Tests ---

def test_slate_brb_get_and_post(client):
    res = client.get("/api/slate/brb")
    assert res.status_code == 200
    assert "active" in res.json()

    # Show BRB slate
    res = client.post(
        "/api/slate/brb",
        json={"action": "show", "title": "Be Right Back", "subtitle": "Stream resuming in 5 mins"},
        headers={"X-CSRF-Token": client.session["csrf_token"]},
    )
    assert res.status_code == 200
    assert res.json()["state"]["active"] is True
    assert res.json()["state"]["title"] == "Be Right Back"

    # Hide BRB slate
    res = client.post(
        "/api/slate/brb",
        json={"action": "hide"},
        headers={"X-CSRF-Token": client.session["csrf_token"]},
    )
    assert res.status_code == 200
    assert res.json()["state"]["active"] is False


def test_slate_brb_js_generation():
    st = {"active": True, "title": "Stream Will Resume Shortly", "subtitle": "Please stand by"}
    js = slate.generate_brb_js(st)
    assert "stream-brb-holding-card" in js
    assert "Stream Will Resume Shortly" in js


# --- 3. Telegram Alerts Tests ---

def test_telegram_alert_unconfigured(monkeypatch):
    monkeypatch.setattr(env_store, "read_parsed", lambda: {})
    token, chat_id = telegram.get_telegram_config()
    assert token == ""
    assert chat_id == ""
    assert telegram.send_alert("Test message") is False


def test_telegram_alert_configured_success(monkeypatch):
    monkeypatch.setattr(env_store, "read_parsed", lambda: {
        "TELEGRAM_BOT_TOKEN": "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
        "TELEGRAM_CHAT_ID": "987654321",
    })
    token, chat_id = telegram.get_telegram_config()
    assert token == "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
    assert chat_id == "987654321"

    with patch("httpx.Client.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        assert telegram.alert_stream_started("Zoom Meeting") is True
        assert mock_post.called
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["chat_id"] == "987654321"
        assert "STREAM ON-AIR" in call_kwargs["json"]["text"]


def test_telegram_alert_high_dropped_frames_cooldown(monkeypatch):
    monkeypatch.setattr(env_store, "read_parsed", lambda: {
        "TELEGRAM_BOT_TOKEN": "fake_token",
        "TELEGRAM_CHAT_ID": "fake_chat",
    })
    with patch("httpx.Client.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        # First alert fires
        ok1 = telegram.alert_high_dropped_frames(10, 100, 10.0, 25.0, 5000.0, force=True)
        assert ok1 is True

        # Second alert in cooldown without force is suppressed
        ok2 = telegram.alert_high_dropped_frames(15, 100, 15.0, 25.0, 5000.0, force=False)
        assert ok2 is False


# --- 4. Local MP4 Recording Tests ---

def test_record_stream_api_status(client, monkeypatch):
    mock_status = {
        "recording": True,
        "file": "/home/zoombot/recordings/record-20260921-060000.mp4",
        "duration": 120,
        "size_mb": 45.2,
        "free_gb": 18.5,
        "halted_reason": "",
    }
    monkeypatch.setattr(control, "record_stream_action", lambda act: mock_status)

    res = client.get("/api/record")
    assert res.status_code == 200
    data = res.json()
    assert data["recording"] is True
    assert data["size_mb"] == 45.2


def test_record_stream_api_start_stop(client, monkeypatch):
    monkeypatch.setattr(control, "record_stream_action", lambda act: {"recording": act == "start"})

    res_start = client.post(
        "/api/record",
        json={"action": "start"},
        headers={"X-CSRF-Token": client.session["csrf_token"]},
    )
    assert res_start.status_code == 200
    assert res_start.json()["recording"] is True

    res_stop = client.post(
        "/api/record",
        json={"action": "stop"},
        headers={"X-CSRF-Token": client.session["csrf_token"]},
    )
    assert res_stop.status_code == 200
    assert res_stop.json()["recording"] is False
