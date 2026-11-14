"""Watermark settings: validation, persistence, the encoder contract and honest live status.

There is exactly ONE watermark implementation: FFmpeg burns it into the encoded video (scripts/lib.sh
build_watermark_filter). This module owns everything on the dashboard side of that:

    operator input --normalize()--> overlay.json (the saved truth) --encoder_env()--> WATERMARK_* keys in
    current-source.env --stream.sh--> FFmpeg filter --> logs/watermark.status --live_status()--> what the UI says

Rules that keep the UI, the saved settings and the video in agreement:
  * A font can only be chosen if its file is really installed (scripts/fonts + fonts.json). The preview draws with
    that same file, so what is previewed is what FFmpeg loads. A missing/damaged font is reported, never substituted.
  * The filter graph is fixed when FFmpeg starts. Saving NEVER restarts or starts anything; while a broadcast is
    running a change is queued and the UI says "Changes will apply to the next stream."
  * What is live is read back from the encoder's own status file (matched to the running process), not assumed.

An older version also injected a DOM element into the Chrome kiosk page. That second implementation is retired: it
could not reach a Zoom-client source at all, doubled the watermark on webpage sources and toggled independently of the
encoder. Only its clean-up remains (remove_legacy_dom_overlay).
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import threading
from pathlib import Path

from . import cdp, config

logger = logging.getLogger("zoom-stream.overlay")

# ------------------------------------------------------------------ font catalogue (real files only)

SINHALA_CATEGORY = "Sri Lankan / Sinhala Unicode Support"
_FONT_MAGIC = (b"\x00\x01\x00\x00", b"OTTO", b"true")
_KEY_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_catalogue_cache: dict = {"stamp": None, "fonts": []}
_verify_cache: dict[str, tuple] = {}


def fonts_dir() -> Path:
    return Path(config.WATERMARK_FONTS_DIR)


def font_key(family: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (family or "").lower()).strip("-")


def _load_manifest() -> list[dict]:
    path = fonts_dir() / "fonts.json"
    try:
        st = path.stat()
        stamp = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        _catalogue_cache.update(stamp=None, fonts=[])
        return []
    if _catalogue_cache["stamp"] != stamp:
        try:
            raw = json.loads(path.read_text(encoding="utf-8")).get("fonts", [])
            fonts = [f for f in raw if isinstance(f, dict) and _KEY_RE.match(str(f.get("key", "")))
                     and f.get("family") and f.get("file") == f"{f['key']}.ttf"]
        except Exception as exc:                                    # noqa: BLE001
            logger.error("watermark font manifest %s is unreadable: %s", path, exc)
            fonts = []
        _catalogue_cache.update(stamp=stamp, fonts=fonts)
    return _catalogue_cache["fonts"]


def verify_font(entry: dict) -> tuple[bool, str]:
    """Is this catalogue font really loadable by the encoder: the file exists, is readable, is a TrueType/OpenType
    file and is byte-identical to the one the manifest describes. Cached per (mtime, size)."""
    path = fonts_dir() / entry["file"]
    try:
        st = path.stat()
    except OSError:
        return False, f"{entry['file']} is not installed on this server"
    stamp = (st.st_mtime_ns, st.st_size)
    cached = _verify_cache.get(str(path))
    if cached and cached[0] == stamp:
        return cached[1], cached[2]
    try:
        data = path.read_bytes()
    except OSError as exc:
        return False, f"{entry['file']} cannot be read ({exc.strerror or exc})"
    if data[:4] not in _FONT_MAGIC:
        res = (False, f"{entry['file']} is not a TrueType/OpenType font file")
    elif entry.get("sha256") and hashlib.sha256(data).hexdigest() != entry["sha256"]:
        res = (False, f"{entry['file']} does not match the released font file (damaged or replaced)")
    else:
        res = (True, "")
    _verify_cache[str(path)] = (stamp, res[0], res[1])
    return res


def font_catalogue() -> list[dict]:
    """Every catalogue font with `installed` (True only when verify_font passes) and `error`."""
    out = []
    for f in _load_manifest():
        ok, err = verify_font(f)
        out.append({**f, "installed": ok, "error": err})
    return out


def font_entry(family: str) -> dict | None:
    key = font_key(family)
    for f in font_catalogue():
        if f["family"] == family or f["key"] == key:
            return f
    return None


def font_categories(installed_only: bool = True) -> dict[str, list[str]]:
    cats: dict[str, list[str]] = {}
    for f in font_catalogue():
        if installed_only and not f["installed"]:
            continue
        cats.setdefault(f.get("category") or "Other", []).append(f["family"])
    return cats


def _all_categories() -> dict[str, list[str]]:
    cats: dict[str, list[str]] = {}
    for f in _load_manifest():
        cats.setdefault(f.get("category") or "Other", []).append(f["family"])
    return cats


# Kept under their historical names (templates/tests import them): the catalogue as shipped. Use font_catalogue() /
# font_categories() for what is installed right now.
GOOGLE_FONT_CATEGORIES = _all_categories()
GOOGLE_FONTS = [font for fonts in GOOGLE_FONT_CATEGORIES.values() for font in fonts]


# ------------------------------------------------------------------ settings: defaults, validation, persistence

ENCODER_ANCHORS = (
    "top-left", "top-center", "top-right",
    "middle-left", "center", "middle-right",
    "bottom-left", "bottom-center", "bottom-right",
)
ANCHORS = ENCODER_ANCHORS + ("custom",)
MODES = ("text", "image")
MAX_TEXT_CHARS = 300
MAX_TEXT_LINES = 6

DEFAULT_OVERLAY_STATE = {
    "visible": False,               # the master ON/OFF. Only /api/overlay/toggle changes it.
    "mode": "text",                 # "text" | "image"
    "text": "LIVE BROADCAST",
    "font_family": "Montserrat",    # a family from the installed catalogue
    "font_size": 42,                # px on a 1080-line frame (scaled to the real output height)
    "font_color": "#FFFFFF",
    "font_opacity": 100,
    "outline_enabled": True,
    "outline_color": "#000000",
    "outline_width": 3,
    "box_enabled": True,
    "box_color": "#000000",
    "box_opacity": 75,
    "box_padding": 16,
    "shadow_enabled": True,
    "shadow_color": "#000000",
    "shadow_x": 2,
    "shadow_y": 4,
    "anchor": "bottom-left",        # one of ANCHORS; "custom" uses pos_x/pos_y
    "margin_x": 24,                 # px on a 1080-line frame, from the anchored edge
    "margin_y": 24,
    "pos_x": 5.0,                   # percent of the frame, only for anchor "custom"
    "pos_y": 88.0,
    "image_path": "",               # set only by the image upload
    "image_opacity": 100,
    "image_scale_pct": 15.0,        # image width as a percentage of the video width
}

# (min, max) for every numeric setting. The slider ranges in the UI are narrower; these are the hard limits.
_RANGES = {
    "font_size": (8, 400), "font_opacity": (0, 100), "outline_width": (1, 40), "box_opacity": (0, 100),
    "box_padding": (0, 200), "shadow_x": (-60, 60), "shadow_y": (-60, 60), "margin_x": (0, 2000),
    "margin_y": (0, 2000), "pos_x": (0.0, 100.0), "pos_y": (0.0, 100.0), "image_opacity": (0, 100),
    "image_scale_pct": (1.0, 100.0),
}
_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_COLORS = ("font_color", "outline_color", "box_color", "shadow_color")
_BOOLS = ("outline_enabled", "box_enabled", "shadow_enabled")
# What a settings save may change. NOT "visible" (a stale tab's autosave must never flip the watermark back on - only
# the explicit ON/OFF action does) and NOT "image_path" (only the upload sets it; it is a server-side file path).
CLIENT_KEYS = tuple(k for k in DEFAULT_OVERLAY_STATE if k not in ("visible", "image_path"))

_state_lock = threading.RLock()


class OverlayError(Exception):
    """The settings could not be saved."""


def _as_bool(v) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) and v in (0, 1):
        return bool(v)
    if isinstance(v, str) and v.strip().lower() in ("true", "1", "on", "yes"):
        return True
    if isinstance(v, str) and v.strip().lower() in ("false", "0", "off", "no", ""):
        return False
    return None


def clean_text(v) -> str:
    """Operator text as it will be drawn: no control characters, CRLF -> LF, bounded length and line count."""
    s = str(v if v is not None else "").replace("\r\n", "\n").replace("\r", "\n")
    s = "".join(ch for ch in s if ch == "\n" or (ord(ch) >= 32 and ord(ch) != 127))
    lines = s.split("\n")[:MAX_TEXT_LINES]
    return "\n".join(lines)[:MAX_TEXT_CHARS]


def normalize(updates: dict, current: dict, allowed: tuple = CLIENT_KEYS) -> tuple[dict, dict]:
    """Applies `updates` to a copy of `current`. Returns (state, rejected) where rejected maps a field to the reason
    its value was NOT applied - reported to the operator, never silently dropped."""
    state = dict(current)
    rejected: dict[str, str] = {}
    for k, v in (updates or {}).items():
        if k not in DEFAULT_OVERLAY_STATE:
            continue                                               # unknown / retired fields from an older page
        if k not in allowed:
            if state.get(k) != v:
                rejected[k] = "cannot be changed by saving settings"
            continue
        if k in _RANGES:
            lo, hi = _RANGES[k]
            try:
                num = float(v)
                if num != num or num in (float("inf"), float("-inf")):
                    raise ValueError
            except (TypeError, ValueError):
                rejected[k] = "not a number"
                continue
            num = max(lo, min(hi, num))
            state[k] = round(num, 2) if isinstance(lo, float) else int(round(num))
        elif k in _COLORS:
            if isinstance(v, str) and _COLOR_RE.match(v.strip()):
                state[k] = v.strip().upper()
            else:
                rejected[k] = "not a #RRGGBB colour"
        elif k in _BOOLS or k == "visible":
            b = _as_bool(v)
            if b is None:
                rejected[k] = "not true/false"
            else:
                state[k] = b
        elif k == "anchor":
            if v in ANCHORS:
                state[k] = v
            else:
                rejected[k] = "unknown position"
        elif k == "mode":
            if v in MODES:
                state[k] = v
            else:
                rejected[k] = "unknown watermark type"
        elif k == "font_family":
            entry = font_entry(str(v))
            if entry is None:
                rejected[k] = f"\"{str(v)[:60]}\" is not in the font catalogue"
            elif not entry["installed"]:
                rejected[k] = f"font cannot be used: {entry['error']}"
            else:
                state[k] = entry["family"]
        elif k == "text":
            state[k] = clean_text(v)
        elif k == "image_path":
            state[k] = str(v or "")
    return state, rejected


def get_overlay_path() -> Path:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    return config.OVERLAY_CONFIG_FILE


def get_overlay_state() -> dict:
    """The saved settings, with defaults for anything missing and every stored value re-validated (a hand-edited or
    older overlay.json can never hand the encoder something out of range)."""
    p = get_overlay_path()
    data: dict = {}
    if p.is_file():
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except Exception as exc:                                    # noqa: BLE001
            logger.error("overlay config %s is unreadable (%s) - using defaults, watermark OFF", p, exc)
    state, _ = normalize({k: v for k, v in data.items() if k != "font_family"}, DEFAULT_OVERLAY_STATE,
                         allowed=tuple(DEFAULT_OVERLAY_STATE))
    # The saved font is kept even when its file has gone missing: that must surface as an error, not turn into a
    # different font behind the operator's back.
    fam = data.get("font_family")
    if isinstance(fam, str) and fam.strip():
        state["font_family"] = fam.strip()[:80]
    return state


def _write_state(state: dict) -> None:
    p = get_overlay_path()
    tmp = p.with_name(p.name + f".tmp.{os.getpid()}.{threading.get_ident()}")
    try:
        tmp.write_text(json.dumps({k: state[k] for k in DEFAULT_OVERLAY_STATE}, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except OSError as exc:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise OverlayError(f"could not save the watermark settings: {exc.strerror or exc}") from exc


def save_overlay_state(updates: dict, allowed: tuple = CLIENT_KEYS) -> tuple[dict, dict]:
    """Validates and saves. Returns (state, rejected). Raises OverlayError when the file cannot be written."""
    with _state_lock:
        state, rejected = normalize(updates, get_overlay_state(), allowed)
        _write_state(state)
        return state, rejected


def set_enabled(enabled: bool) -> dict:
    """The master ON/OFF, set to an explicit value (idempotent: a double click or a retry cannot flip it back)."""
    with _state_lock:
        state = get_overlay_state()
        state["visible"] = bool(enabled)
        _write_state(state)
        return state


def toggle_overlay_visibility() -> dict:
    with _state_lock:
        return set_enabled(not get_overlay_state().get("visible", False))


def set_image(image_path: str) -> dict:
    with _state_lock:
        state = get_overlay_state()
        state["image_path"], state["mode"] = str(image_path), "image"
        _write_state(state)
        return state


# ------------------------------------------------------------------ the encoder contract

ENV_KEYS = (
    "WATERMARK_ENABLED", "WATERMARK_MODE", "WATERMARK_TEXT", "WATERMARK_TEXT_B64", "WATERMARK_FONT",
    "WATERMARK_SIZE", "WATERMARK_COLOR", "WATERMARK_OPACITY",
    "WATERMARK_ANCHOR", "WATERMARK_MARGIN_X", "WATERMARK_MARGIN_Y", "WATERMARK_POS_X", "WATERMARK_POS_Y",
    "WATERMARK_OUTLINE_WIDTH", "WATERMARK_OUTLINE_COLOR",
    "WATERMARK_BOX", "WATERMARK_BOX_COLOR", "WATERMARK_BOX_OPACITY", "WATERMARK_BOX_PADDING",
    "WATERMARK_SHADOW", "WATERMARK_SHADOW_COLOR", "WATERMARK_SHADOW_X", "WATERMARK_SHADOW_Y",
    "WATERMARK_IMAGE_PATH", "WATERMARK_CONFIG_HASH",
)


def _num(v) -> str:
    f = float(v)
    return str(int(f)) if f == int(f) else f"{f:.2f}"


def encoder_env(state: dict) -> dict[str, str]:
    """The WATERMARK_* keys scripts/lib.sh reads, from a validated state. Every value is a plain single-line string.
    WATERMARK_CONFIG_HASH fingerprints what the encoder would draw; stream.sh echoes it into its status file so
    live_status() can tell "live" from "queued". With the watermark OFF the fingerprint ignores every other setting:
    editing a hidden watermark is not a pending change to a running stream."""
    s, _ = normalize(state, DEFAULT_OVERLAY_STATE, allowed=tuple(DEFAULT_OVERLAY_STATE))
    s["font_family"] = state.get("font_family", s["font_family"])
    image = s["mode"] == "image"
    text = clean_text(state.get("text", ""))
    env = {
        "WATERMARK_ENABLED": "1" if s["visible"] else "0",
        "WATERMARK_MODE": s["mode"],
        "WATERMARK_TEXT": " ".join(text.split("\n")),
        "WATERMARK_TEXT_B64": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "WATERMARK_FONT": font_key(str(s["font_family"])),
        "WATERMARK_SIZE": _num(s["image_scale_pct"] if image else s["font_size"]),
        "WATERMARK_COLOR": s["font_color"],
        "WATERMARK_OPACITY": _num(s["image_opacity"] if image else s["font_opacity"]),
        "WATERMARK_ANCHOR": s["anchor"],
        "WATERMARK_MARGIN_X": _num(s["margin_x"]),
        "WATERMARK_MARGIN_Y": _num(s["margin_y"]),
        "WATERMARK_POS_X": _num(s["pos_x"]),
        "WATERMARK_POS_Y": _num(s["pos_y"]),
        "WATERMARK_OUTLINE_WIDTH": _num(s["outline_width"]) if s["outline_enabled"] else "0",
        "WATERMARK_OUTLINE_COLOR": s["outline_color"],
        "WATERMARK_BOX": "1" if s["box_enabled"] else "0",
        "WATERMARK_BOX_COLOR": s["box_color"],
        "WATERMARK_BOX_OPACITY": _num(s["box_opacity"]),
        "WATERMARK_BOX_PADDING": _num(s["box_padding"]),
        "WATERMARK_SHADOW": "1" if s["shadow_enabled"] else "0",
        "WATERMARK_SHADOW_COLOR": s["shadow_color"],
        "WATERMARK_SHADOW_X": _num(s["shadow_x"]),
        "WATERMARK_SHADOW_Y": _num(s["shadow_y"]),
        "WATERMARK_IMAGE_PATH": str(s.get("image_path") or ""),
    }
    basis = env if s["visible"] else {"WATERMARK_ENABLED": "0"}
    env["WATERMARK_CONFIG_HASH"] = hashlib.sha256(
        json.dumps(basis, sort_keys=True, ensure_ascii=True).encode("ascii")).hexdigest()[:16]
    return env


def _has_sinhala(text: str) -> bool:
    return any(0x0D80 <= ord(ch) <= 0x0DFF for ch in text)


def config_problems(state: dict) -> list[dict]:
    """Everything that would stop the watermark being drawn as configured, in plain words. Empty = nothing wrong.
    level "error": the watermark will NOT appear;  "warning": it will appear but probably not as intended."""
    out: list[dict] = []
    if state.get("mode") == "image":
        if not state.get("image_path"):
            out.append({"level": "error", "field": "image_path", "message": "No watermark image has been uploaded yet."})
        return out
    if not clean_text(state.get("text", "")).strip():
        out.append({"level": "error", "field": "text", "message": "The watermark text is empty."})
    fam = str(state.get("font_family", ""))
    entry = font_entry(fam)
    if entry is None:
        out.append({"level": "error", "field": "font_family",
                    "message": f"The font \"{fam}\" is not in the installed font catalogue. Choose another font."})
    elif not entry["installed"]:
        out.append({"level": "error", "field": "font_family",
                    "message": f"The font \"{fam}\" cannot be loaded: {entry['error']}. The watermark will not be "
                               f"drawn until the font is reinstalled or another font is chosen."})
    elif _has_sinhala(str(state.get("text", ""))) and "sinhala" not in (entry.get("scripts") or []):
        out.append({"level": "warning", "field": "font_family",
                    "message": f"The text contains Sinhala letters but \"{fam}\" has none - they would show as empty "
                               f"boxes. Choose a font from \"{SINHALA_CATEGORY}\"."})
    return out


# ------------------------------------------------------------------ what is live right now

LIVE_PHASES = ("LIVE", "STARTING", "RECONNECTING")
NEXT_STREAM_MESSAGE = "Changes will apply to the next stream."


def read_encoder_status() -> dict | None:
    """logs/watermark.status as written by the last stream.sh start (KEY=VALUE lines), or None."""
    try:
        raw = (config.STREAM_LOGS_DIR / "watermark.status").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    out = {}
    for line in raw.splitlines()[:40]:
        k, sep, v = line.partition("=")
        if sep and re.match(r"^[A-Z_]+$", k):
            out[k] = v.strip()[:300]
    return out or None


def live_status(state: dict | None = None, show: dict | None = None) -> dict:
    """Saved settings vs. what the running encoder actually drew. Never guesses: when the running encoder cannot be
    matched to a status record (started by an older version), it says so."""
    from . import control
    state = state or get_overlay_state()
    env = encoder_env(state)
    desired_on = env["WATERMARK_ENABLED"] == "1"
    problems = config_problems(state) if desired_on else []
    if show is None:
        try:
            show = control.unit_show("ffmpeg-stream")
        except Exception:                                           # noqa: BLE001
            show = {}
    phase = show.get("phase")
    encoder_live = phase in LIVE_PHASES
    res = {
        "configured_enabled": desired_on, "configured_visible": desired_on, "desired_hash": env["WATERMARK_CONFIG_HASH"],
        "encoder_live": encoder_live, "encoder_phase": phase, "live_known": False, "live_state": None,
        "live_reason": "", "live_font": "", "pending": False, "problems": problems,
    }
    if not encoder_live:
        blocked = next((p for p in problems if p["level"] == "error"), None)
        if not desired_on:
            res["message"] = "Watermark is OFF. The next stream will have no watermark."
        elif blocked:
            res["message"] = "Watermark is ON but cannot be drawn: " + blocked["message"]
        else:
            res["message"] = "Watermark is ON. It will be burned into the next stream."
        res["encoder_active"], res["requires_restart"] = False, False
        return res

    st = read_encoder_status()
    pid = int(show.get("main_pid") or 0)
    if st and pid and st.get("PID") == str(pid):
        res.update(live_known=True, live_state=st.get("STATE", "off"), live_reason=st.get("REASON", ""),
                   live_font=st.get("FONT", ""))
        res["pending"] = st.get("HASH", "") != env["WATERMARK_CONFIG_HASH"]
        if res["live_state"] == "on":
            now = "The live stream has the watermark."
        elif res["live_state"] == "error":
            now = "The live stream has NO watermark: " + (res["live_reason"] or "it could not be drawn") + "."
        else:
            now = "The live stream has no watermark."
        res["encoder_active"] = res["live_state"] == "on"
    else:
        # Started before status reporting existed, or the record is unreadable: fall back to looking for a watermark
        # filter in the running command line, and treat the saved settings as not yet confirmed live.
        res["encoder_active"] = bool(control.watermark_is_running())
        res["pending"] = True
        now = ("The live stream appears to have a watermark" if res["encoder_active"] else
               "The live stream appears to have no watermark") + " (started before this could be verified)."
    res["requires_restart"] = res["pending"]
    res["message"] = now + (" " + NEXT_STREAM_MESSAGE if res["pending"] else " It matches the saved settings.")
    return res


# ------------------------------------------------------------------ retired DOM overlay: clean-up only

_REMOVE_DOM_JS = """(() => {
  try { if (window.__zoom_watermark_observer) { window.__zoom_watermark_observer.disconnect(); window.__zoom_watermark_observer = null; } } catch (e) {}
  window.__zoom_reinject_watermark = null;
  let removed = 0;
  for (const id of ['livestream-watermark-overlay', 'livestream-watermark-inner', 'obs-text-overlay']) {
    const el = document.getElementById(id);
    if (el) { el.remove(); removed++; }
  }
  return { removed: removed };
})()"""


def generate_overlay_js(state: dict | None = None) -> str:
    """JavaScript that REMOVES the retired DOM watermark from the kiosk page. It never draws one, whatever `state`
    says - the encoder is the only thing that draws the watermark."""
    return _REMOVE_DOM_JS


async def remove_legacy_dom_overlay() -> dict:
    """Best effort: takes a DOM watermark injected by an older version out of the kiosk page (it would otherwise sit
    on top of the capture until that page reloads). No kiosk / no DevTools is not an error."""
    try:
        res = await cdp.evaluate(_REMOVE_DOM_JS)
        return {"connected": True, "removed": int((res.get("value") or {}).get("removed", 0))}
    except Exception as exc:                                        # noqa: BLE001
        logger.debug("legacy DOM overlay clean-up skipped (no kiosk page): %s", exc)
        return {"connected": False, "removed": 0}


async def push_overlay_to_kiosk(state: dict | None = None) -> bool:
    """Historical name, kept for callers: now only removes the retired DOM watermark."""
    return (await remove_legacy_dom_overlay())["connected"]

