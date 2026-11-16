"""v1.0.4: Go Live failed on the test VPS with "Job for ffmpeg-stream.service failed because the control process exited
with error code" (plus "sudo: unable to send audit message"), and "Restart All Services" seemed to fix it.

Root cause (journal + ffmpeg.log + audit log, 2026-11-16): the Live Studio stream-key field had been saved with the Zoom
meeting link copied from the browser's address bar ("https://us05web.zoom.us/j/<id>#success"). stream.sh appended it to
rtmps://a.rtmps.youtube.com:443/live2/, both inputs opened fine, and YouTube closed the connection ("Error opening output
files: Input/output error", exit 251). After 5 tries systemd's start limit refused further starts and systemctl printed
the misleading "control process exited" message, prefixed by sudo's harmless audit warning. Saving the real key fixed
it; "Restart All" only happened to come next. Nothing was stale: every attempt captured the real 1920x1080 screen.

These tests reproduce each piece with fakes (no systemd, no network, no real ffmpeg)."""
from __future__ import annotations

import os
import time

import pytest
from starlette.testclient import TestClient

from app import broadcast, config, control, db, security, sources, stats, stream_key
from app.main import app

INCIDENT_KEY = "https://us05web.zoom.us/j/81234567890#success"     # same shape as the value found on the test VPS
GOOD_KEY = "abcd-efgh-ijkl-mnop-qrst"
ZOOM_URL = "https://us06web.zoom.us/j/81234567890?pwd=abc"
AUDIT_NOISE = "sudo: unable to send audit message: Operation not permitted"
START_LIMIT_MSG = ("Job for ffmpeg-stream.service failed because the control process exited with error code.\n"
                   "See \"systemctl --user status ffmpeg-stream.service\" and \"journalctl --user -xeu ffmpeg-stream.service\" for details.")


# ------------------------------------------------------------------ the validator

@pytest.mark.parametrize("bad", [
    INCIDENT_KEY,
    "https://us05web.zoom.us/j/81234567890",
    "us05web.zoom.us/j/81234567890",
    "rtmps://a.rtmps.youtube.com:443/live2/abcd-efgh-ijkl-mnop-qrst",
    "https://youtube.com/live/abcdefghijk",
    "www.youtube.com/watch?v=abc",
    "abcd-efgh ijkl-mnop-qrst",
    "abcd/efgh",
    "abcd#efgh",
    'abcd"efgh',
    "ab",
])
def test_links_and_malformed_values_are_not_stream_keys(bad):
    assert stream_key.problem(bad)


def test_the_incident_value_is_named_as_a_zoom_link():
    msg = stream_key.problem(INCIDENT_KEY)
    assert "Zoom meeting link" in msg and "YouTube Studio" in msg
    assert INCIDENT_KEY not in msg and "81234567890" not in msg                     # never echo the value back


@pytest.mark.parametrize("good", [
    GOOD_KEY,
    "x1y2-z3w4-a5b6-c7d8-e9f0",
    "live_123456789_AbCdEfGhIjKlMnOpQrStUvWxYz",                 # other RTMP services (custom YT_RTMP_URL)
    "FB-1234567890-0-AbCdEf?s_bl=1&s_ps=1&s_sw=0&s_vt=api-s&a=AbC",
])
def test_real_keys_are_accepted(good):
    assert stream_key.problem(good) is None


# ------------------------------------------------------------------ API fixture (same shape as test_broadcast_authorization)

async def _no_sleep(*a, **k):
    return None


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "k.db")
    db.init_db()
    broadcast.reset_for_tests()
    with db.get_conn() as conn:
        uid = conn.execute("INSERT INTO users (username, password_hash, created_at) VALUES (?,?,?)",
                           ("admin", security.hash_password("correct-horse-battery-staple"), time.time())).lastrowid
    sid, csrf = security.create_session(uid, "127.0.0.1")
    c = TestClient(app, cookies={config.COOKIE_NAME: sid})
    c.csrf = {"x-csrf-token": csrf}
    from app import deps, env_store, status
    deps._rate_buckets.clear()
    status._CACHE.update(at=0.0, value=None)
    monkeypatch.setattr(status, "_is_active", lambda unit: "active")
    c.env = {"YT_STREAM_KEY": GOOD_KEY}
    monkeypatch.setattr(env_store, "read_parsed", lambda: dict(c.env))
    c.env_writes = []
    monkeypatch.setattr(env_store, "run_as_zoombot",
                        lambda argv, **k: c.env_writes.append(k.get("input_bytes")) or type("R", (), {"returncode": 0, "stdout": b"", "stderr": b""})())
    monkeypatch.setattr(control, "write_broadcast_auth", lambda env: None)
    monkeypatch.setattr(control, "clear_broadcast_auth", lambda: None)
    monkeypatch.setattr(control, "zoom_meeting_status", lambda: {"status": "in_meeting", "detail": ""})
    monkeypatch.setattr(control, "read_current_source", lambda: {})
    c.calls = []
    c.encoder = {"phase": "STOPPED", "after_start": "LIVE"}

    def unit_show(unit):
        phase = c.encoder["phase"] if unit == "ffmpeg-stream" else "LIVE"
        return {"unit": unit, "phase": phase, "active_state": "failed" if phase == "FAILED" else "active",
                "sub_state": "running", "result": "exit-code" if phase == "FAILED" else "success", "restart_count": 0,
                "uptime_seconds": 5, "main_pid": 7, "exec_main_status": "", "exec_main_code": "", "unit_file_state": "static"}
    monkeypatch.setattr(control, "unit_show", unit_show)

    def unit_action(unit, verb, *, grant=None):
        c.calls.append((unit, verb, bool(grant)))
        if unit == "ffmpeg-stream" and verb in ("start", "restart"):
            assert isinstance(grant, broadcast.Grant) and grant.valid()
            if c.encoder.get("start_limit"):
                return {"ok": False, "unit": unit, "verb": verb, "stderr": START_LIMIT_MSG}
            c.encoder["phase"] = c.encoder["after_start"]
        if unit == "ffmpeg-stream" and verb == "reset-failed":
            c.encoder["start_limit"] = False
            if c.encoder["phase"] == "FAILED":
                c.encoder["phase"] = "STOPPED"
        return {"ok": True, "unit": unit, "verb": verb, "stderr": ""}
    monkeypatch.setattr(control, "unit_action", unit_action)
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    s = sources.create_source("Meeting A", "zoom", ZOOM_URL, {})
    sources.set_active_source_id(s)
    return c


def _authorize(c):
    return c.post("/api/broadcast/authorize", json={"purpose": "go_live"}, headers=c.csrf)


def _go_live(c):
    a = _authorize(c)
    assert a.status_code == 200, a.text
    return c.post("/api/stream/go-live", json={"grant": a.json()["grant"]}, headers=c.csrf)


# ------------------------------------------------------------------ saving the key

def test_live_studio_refuses_to_save_a_zoom_link_as_the_stream_key(client):
    r = client.post("/api/studio/stream-key", json={"stream_key": INCIDENT_KEY}, headers=client.csrf)
    assert r.status_code == 400 and "Zoom meeting link" in r.json()["error"]
    assert client.env_writes == []                                                    # .env untouched
    assert INCIDENT_KEY not in r.text


def test_live_studio_still_saves_a_real_key(client):
    client.env = {"YT_STREAM_KEY": INCIDENT_KEY}                       # the bad value saved before this fix
    r = client.post("/api/studio/stream-key", json={"stream_key": GOOD_KEY}, headers=client.csrf)
    assert r.status_code == 200 and r.json()["ok"] is True
    assert client.env_writes and GOOD_KEY.encode() in client.env_writes[0]


def test_the_settings_path_refuses_it_too():
    from app import env_store
    with pytest.raises(env_store.ValidationError) as e:
        env_store.validate_updates({"YT_STREAM_KEY": INCIDENT_KEY})
    assert "Zoom meeting link" in str(e.value)


# ------------------------------------------------------------------ a bad key that is ALREADY saved (written before this fix)

def test_go_live_is_blocked_with_a_clear_message_when_the_saved_key_is_a_link(client):
    client.env = {"YT_STREAM_KEY": INCIDENT_KEY}
    pre = client.get("/api/broadcast/preflight").json()
    assert pre["destination"]["stream_key"] == "invalid"
    assert any(w["code"] == "bad_stream_key" and "Zoom meeting link" in w["text"] for w in pre["warnings"])
    assert INCIDENT_KEY not in str(pre)
    r = _authorize(client)
    assert r.status_code == 409 and r.json()["code"] == "bad_stream_key"
    r = client.post("/api/broadcast/authorize", json={"purpose": "go_live", "ack_warnings": True}, headers=client.csrf)
    assert r.status_code == 409 and r.json()["code"] == "bad_stream_key"               # cannot be acknowledged away
    assert [c for c in client.calls if c[0] == "ffmpeg-stream"] == []                  # nothing was started


# ------------------------------------------------------------------ what the operator sees when YouTube refuses anyway

def test_youtube_refusing_the_connection_is_explained(client, monkeypatch):
    client.encoder["after_start"] = "FAILED"
    monkeypatch.setattr(stats, "stream_state", lambda show: {"last_error": None})
    monkeypatch.setattr(stats, "last_ffmpeg_log_error", lambda: "Error opening output files: Input/output error")
    r = _go_live(client)
    err = r.json()["error"]
    assert r.status_code >= 400
    assert "YouTube closed the connection" in err and "stream key" in err


def test_a_go_live_after_repeated_failures_is_not_blocked_by_the_start_limit(client):
    # 5 failed starts inside 5 minutes: systemd refuses the 6th ("start request repeated too quickly") and systemctl
    # prints the misleading "control process exited" message. The operator fixed the key and pressed Go Live again.
    client.encoder.update(phase="FAILED", start_limit=True)
    r = _go_live(client)
    assert r.status_code == 200, r.text
    enc = [(v, g) for u, v, g in client.calls if u == "ffmpeg-stream"]
    assert enc == [("reset-failed", False), ("start", True)]                           # cleared first, then the authorized start


def test_a_go_live_from_a_stopped_state_also_clears_the_counter_first(client):
    # the start limit counts starts, not failures of a "failed" unit: an inactive encoder can be rate-limited too
    client.encoder.update(start_limit=True)
    assert _go_live(client).status_code == 200
    assert [(u, v) for u, v, g in client.calls if u == "ffmpeg-stream"] == [("ffmpeg-stream", "reset-failed"),
                                                                           ("ffmpeg-stream", "start")]


def test_restart_while_live_never_resets(client):
    assert _go_live(client).status_code == 200
    client.calls.clear()
    b = client.post("/api/broadcast/authorize", json={"purpose": "restart"}, headers=client.csrf).json()["grant"]
    assert client.post("/api/stream/restart", json={"grant": b}, headers=client.csrf).status_code == 200
    assert [(u, v) for u, v, g in client.calls if u == "ffmpeg-stream"] == [("ffmpeg-stream", "restart")]


def test_start_limit_message_is_explained_instead_of_echoed(client, monkeypatch):
    # if systemd still refuses (reset-failed refused or the counter came back), say what it means
    client.encoder.update(start_limit=True)
    real = control.unit_action

    def no_reset(unit, verb, *, grant=None):
        if verb == "reset-failed":
            client.calls.append((unit, verb, False))
            return {"ok": False, "unit": unit, "verb": verb, "stderr": "Access denied"}
        return real(unit, verb, grant=grant)
    monkeypatch.setattr(control, "unit_action", no_reset)
    r = _go_live(client)
    err = r.json()["error"]
    assert "control process exited" not in err and "audit" not in err
    assert "several failed starts" in err


def test_oneshot_units_keep_their_real_control_process_error():
    msg = "Job for audio-setup.service failed because the control process exited with error code."
    assert control.explain_systemctl_error("audio-setup", "start", msg) == msg
    assert "several failed starts" in control.explain_systemctl_error(
        "audio-setup", "start", "Job for audio-setup.service failed because start of the service was attempted too frequently.")


# ------------------------------------------------------------------ sudo's audit warning

def test_sudo_audit_warning_is_dropped_from_systemctl_errors(monkeypatch):
    def fake_run(argv, **k):
        return type("R", (), {"returncode": 1, "stdout": b"", "stderr": (AUDIT_NOISE + "\n" + START_LIMIT_MSG).encode()})()
    monkeypatch.setattr(control, "run_as_zoombot", fake_run)
    res = control.unit_action("xvfb", "start")
    assert res["ok"] is False and "audit" not in res["stderr"] and res["stderr"].startswith("Job for")


def test_sudo_audit_warning_alone_leaves_an_empty_stderr(monkeypatch):
    monkeypatch.setattr(control, "run_as_zoombot",
                        lambda argv, **k: type("R", (), {"returncode": 0, "stdout": b"", "stderr": (AUDIT_NOISE + "\n").encode()})())
    assert control.unit_action("xvfb", "start")["stderr"] == ""


# ------------------------------------------------------------------ the status panel's cause line

def test_status_cause_names_the_stream_key_for_an_output_open_failure():
    assert "stream key" in stats._classify_cause("Error opening output files: Input/output error")
    assert "stream key" in stats._classify_cause(
        "[out#0/flv @ 0x5d] Error opening output rtmps://a.rtmps.youtube.com:443/live2/<redacted> Input/output error")


# ------------------------------------------------------------------ stream.sh refuses before ffmpeg (defense in depth)

def test_stream_sh_refuses_a_link_as_the_key_before_ffmpeg(tmp_path):
    from test_stream_gate import BASH, make_sandbox, run_stream, write_source
    if BASH is None:
        pytest.skip("bash not available")
    app_dir = make_sandbox(tmp_path)
    (app_dir / ".env").write_text(f'YT_STREAM_KEY="{INCIDENT_KEY}"\nRESOLUTION="1280x720"\nFPS="30"\n', encoding="utf-8")
    write_source(app_dir, "n0nce", int(time.time()) + 30)
    r = run_stream(app_dir)
    assert r.returncode == 1
    assert not (app_dir / "ffmpeg-ran").exists()
    out = r.stdout + r.stderr
    assert "link, not a stream key" in out
    assert INCIDENT_KEY not in out and "81234567890" not in out


def test_stream_sh_still_starts_with_a_real_key(tmp_path):
    from test_stream_gate import BASH, make_sandbox, run_stream, write_source
    if BASH is None:
        pytest.skip("bash not available")
    app_dir = make_sandbox(tmp_path)
    write_source(app_dir, "n0nce", int(time.time()) + 30)
    r = run_stream(app_dir)
    assert r.returncode == 0 and (app_dir / "ffmpeg-ran").exists(), r.stdout + r.stderr
