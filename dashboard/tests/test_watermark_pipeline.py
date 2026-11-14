"""Watermark regression suite: dashboard API -> validation -> saved settings -> current-source.env -> the REAL shell
filter builder (scripts/lib.sh) -> a REAL FFmpeg encode -> the decoded output frames.

Nothing here trusts a screenshot or the UI's own idea of its state: the assertions are on pixels FFmpeg produced, on
the command line stream.sh hands to the encoder, and on the files the pipeline reads. Synthetic video only (lavfi colour
source); no network, no X display, no real encoder unit, no YouTube.

Needs bash and an ffmpeg with drawtext; the tests skip (loudly) when either is missing, so a skip is NOT a pass.
What this suite cannot cover is listed in docs/watermark.md ("Verification status"): a real x11grab capture of a real
Zoom meeting on the VPS's own FFmpeg build.
"""
import base64
import json
import os
import pathlib
import shutil
import subprocess
import time

import pytest
from starlette.testclient import TestClient

from app import broadcast, config, control, db, overlay
from app import sources as sources_mod
from app.main import app

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
FONTS = SCRIPTS / "fonts"
BASH = shutil.which("bash")
FFMPEG = shutil.which("ffmpeg")


def _has_drawtext() -> bool:
    if not FFMPEG:
        return False
    try:
        out = subprocess.run([FFMPEG, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return False
    return " drawtext " in out and " overlay " in out


needs_bash = pytest.mark.skipif(BASH is None, reason="bash not available - shell filter builder NOT tested")
needs_ffmpeg = pytest.mark.skipif(BASH is None or not _has_drawtext(),
                                  reason="bash + ffmpeg(drawtext) not available - real encoder output NOT verified")

BG = (0x20, 0x40, 0x60)
W, H = 1280, 720


def _posix(p):
    p = pathlib.Path(p).resolve().as_posix()
    if len(p) > 1 and p[1] == ":":
        p = "/" + p[0].lower() + p[2:]
    return p


def env_text(values: dict) -> str:
    """Exactly the format scripts/write-source.sh writes (and lib.sh load_env_file reads)."""
    return "".join('{}="{}"\n'.format(k, str(v).replace("\\", "\\\\").replace('"', '\\"')) for k, v in values.items())


def parse_env(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            out[k] = control._unquote_env_value(v.strip())
    return out


class Rig:
    """A sandbox install: scripts/lib.sh + stream.sh, a font folder, logs/, current-source.env. Filters are built by
    the real shell code with relative paths (cwd = the sandbox) so the same string works for FFmpeg on any platform."""

    def __init__(self, tmp_path, fonts=("montserrat", "anton", "inter", "noto-sans-sinhala", "press-start-2p")):
        self.app = tmp_path / "zs"
        (self.app / "scripts" / "fonts").mkdir(parents=True)
        (self.app / "logs").mkdir()
        (self.app / "watermarks").mkdir()
        for n in ("lib.sh", "stream.sh", "test-recording.sh"):
            text = (SCRIPTS / n).read_text(encoding="utf-8").replace("\r\n", "\n")
            text = text.replace("/usr/bin/ffmpeg", _posix(self.app / "fake-ffmpeg"))
            (self.app / "scripts" / n).write_text(text, encoding="utf-8", newline="\n")
        for f in fonts:
            shutil.copy(FONTS / f"{f}.ttf", self.app / "scripts" / "fonts" / f"{f}.ttf")
        shutil.copy(FONTS / "fonts.json", self.app / "scripts" / "fonts" / "fonts.json")
        (self.app / ".env").write_text('YT_STREAM_KEY="abcd-efgh-ijkl-mnop"\nRESOLUTION="1280x720"\nFPS="30"\n',
                                       encoding="utf-8", newline="\n")
        (self.app / "fake-ffmpeg").write_text(
            "#!/usr/bin/env bash\n"
            f"echo invoked >> '{_posix(self.app)}/ffmpeg-ran'\n"
            f"printf '%s\\n' \"$*\" > '{_posix(self.app)}/ffmpeg-argv'\n"
            "exit 0\n", encoding="utf-8", newline="\n")
        os.chmod(self.app / "fake-ffmpeg", 0o755)
        self.env_path = self.app / "current-source.env"
        self.base_env = {"SOURCE_TYPE": "direct", "DIRECT_URL": "https://example.com/live.m3u8", "DIRECT_MODE": "reencode"}
        self.write_env({})

    # ---- current-source.env
    def write_env(self, values: dict):
        self.env_path.write_text(env_text({**self.base_env, **values}), encoding="utf-8", newline="\n")

    def read_env(self) -> dict:
        return parse_env(self.env_path.read_text(encoding="utf-8"))

    def set_watermark(self, **state):
        """Settings -> the dashboard's own encoder_env() -> the env file, as a save would do."""
        full = dict(overlay.DEFAULT_OVERLAY_STATE, outline_enabled=False, box_enabled=False, shadow_enabled=False,
                    visible=True, anchor="bottom-right")
        full.update(state)
        env = overlay.encoder_env(full)
        self.write_env({**self.read_env(), **env})
        return env

    # ---- the real shell builder
    def build(self, w=W, h=H) -> dict:
        script = (
            "source scripts/lib.sh >/dev/null\n"
            f"build_watermark_filter 1 {w or ''} {h or ''} >/dev/null\n"
            'printf "STATE=%s\\nREASON=%s\\nFONTFILE=%s\\nFILTER=%s\\n" "$WATERMARK_STATE" "$WATERMARK_REASON" '
            '"$WATERMARK_FONT_FILE" "$WATERMARK_FILTER"\n'
            'printf "INPUT=%s\\n" "${WATERMARK_EXTRA_INPUT_ARGS[@]}"\n'
        )
        env = dict(os.environ, APP_DIR=".")
        for k in list(env):
            if k.startswith("WATERMARK_"):
                env.pop(k)
        r = subprocess.run([BASH, "-c", script], cwd=self.app, capture_output=True, text=True, env=env, timeout=60,
                           encoding="utf-8")
        assert r.returncode == 0, r.stderr
        out = {"inputs": []}
        for line in r.stdout.splitlines():
            k, _, v = line.partition("=")
            if k == "INPUT":
                if v:
                    out["inputs"].append(v)
            else:
                out[k.lower()] = v
        return out

    # ---- real FFmpeg
    def _ffmpeg_args(self, built, w, h):
        args = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                f"color=c=0x{BG[0]:02x}{BG[1]:02x}{BG[2]:02x}:s={w}x{h}:r=5"]
        flt = built.get("filter") or ""
        if flt.startswith("["):                                     # image mode: filter_complex graph
            args += built["inputs"] + ["-filter_complex", flt, "-map", "[vout]"]
        elif flt:
            args += ["-vf", flt]
        return args

    def frame(self, built, w=W, h=H) -> bytes:
        """One frame straight out of the filter graph (no codec) as rgb24."""
        r = subprocess.run(self._ffmpeg_args(built, w, h) + ["-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                           cwd=self.app, capture_output=True, timeout=120)
        assert r.returncode == 0, r.stderr.decode(errors="replace")
        assert len(r.stdout) == w * h * 3
        return r.stdout

    def encoded_frame(self, built, w=W, h=H) -> bytes:
        """A real H.264 encode with the broadcast's own codec settings, then the decoded frame: what a viewer gets."""
        out = self.app / "out.mp4"
        r = subprocess.run(self._ffmpeg_args(built, w, h) + [
            "-frames:v", "5", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-b:v", "4000k",
            "-an", "out.mp4"], cwd=self.app, capture_output=True, timeout=180)
        assert r.returncode == 0, r.stderr.decode(errors="replace")
        r = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-i", "out.mp4", "-vf", "select=eq(n\\,3)",
                            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                           cwd=self.app, capture_output=True, timeout=120)
        assert r.returncode == 0 and len(r.stdout) == w * h * 3, r.stderr.decode(errors="replace")
        out.unlink()
        return r.stdout

    # ---- stream.sh with a fake ffmpeg (records the argv it was started with)
    def authorize(self):
        self.write_env({**self.read_env(), "BROADCAST_AUTH_ID": "g", "BROADCAST_AUTH_NONCE": "n0nce",
                        "BROADCAST_AUTH_EXPIRES": str(int(time.time()) + 30)})

    def run_stream(self):
        env = dict(os.environ, APP_DIR=_posix(self.app), HOME=_posix(self.app))
        env.pop("DISPLAY", None)
        for f in ("ffmpeg-ran", "ffmpeg-argv"):
            (self.app / f).unlink(missing_ok=True)
        r = subprocess.run([BASH, _posix(self.app / "scripts" / "stream.sh")], capture_output=True, text=True, env=env,
                           timeout=60)
        ran = (self.app / "ffmpeg-ran").exists()
        argv = (self.app / "ffmpeg-argv").read_text(encoding="utf-8") if ran else ""
        return r, ran, argv

    def status(self) -> dict:
        p = self.app / "logs" / "watermark.status"
        return dict(l.split("=", 1) for l in p.read_text(encoding="utf-8").splitlines() if "=" in l) if p.exists() else {}


def changed(base: bytes, frame: bytes, w=W, h=H, thr=40):
    """Pixels that differ visibly from the reference frame -> (count, (x0, y0, x1, y1) or None, mean (r, g, b))."""
    count, x0, y0, x1, y1 = 0, w, h, -1, -1
    sr = sg = sb = 0
    row = w * 3
    for y in range(h):
        a, b = base[y * row:(y + 1) * row], frame[y * row:(y + 1) * row]
        if a == b:
            continue
        for x in range(w):
            i = x * 3
            if abs(a[i] - b[i]) + abs(a[i + 1] - b[i + 1]) + abs(a[i + 2] - b[i + 2]) > thr:
                count += 1
                sr += b[i]; sg += b[i + 1]; sb += b[i + 2]
                if x < x0: x0 = x
                if x > x1: x1 = x
                if y < y0: y0 = y
                if y > y1: y1 = y
    if not count:
        return 0, None, (0, 0, 0)
    return count, (x0, y0, x1, y1), (sr // count, sg // count, sb // count)


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


@pytest.fixture
def clean(rig):
    """The untouched frame (no filter at all) - the reference every comparison is made against."""
    return rig.frame({"filter": ""})


# =====================================================================================================================
# 1-2  ON / OFF in the actual output
# =====================================================================================================================

@needs_ffmpeg
def test_01_enabled_watermark_is_in_the_encoded_video(rig):
    rig.set_watermark(text="MY CLASS", font_family="Montserrat", font_size=60)
    built = rig.build()
    assert built["state"] == "on" and built["filter"].startswith("drawtext=")
    base = rig.encoded_frame({"filter": ""})
    n, box, _ = changed(base, rig.encoded_frame(built))
    assert n > 300, "the encoded frames show no watermark"
    assert box[0] > W * 0.6 and box[1] > H * 0.8                    # bottom-right, where it was put


@needs_ffmpeg
def test_02_disabled_watermark_leaves_the_encoded_video_untouched(rig):
    rig.set_watermark(text="MY CLASS", font_size=60, visible=False)
    built = rig.build()
    assert built["state"] == "off" and built["filter"] == ""
    assert not built["inputs"]
    base = rig.encoded_frame({"filter": ""})
    assert rig.encoded_frame(built) == base                          # byte-identical decoded frame: nothing was drawn


@needs_ffmpeg
def test_02b_off_wins_over_every_other_setting(rig):
    """OFF must mean off even when every other key still describes a complete watermark (the stale-settings case)."""
    env = rig.set_watermark(text="STALE", font_size=80, box_enabled=True, outline_enabled=True)
    env["WATERMARK_ENABLED"] = "0"
    rig.write_env({**rig.read_env(), **env})
    for mode in ("text", "image"):
        rig.write_env({**rig.read_env(), "WATERMARK_MODE": mode})
        built = rig.build()
        assert built["filter"] == "" and built["state"] == "off"
    for bad in ("", "true", "yes", "01", " 1", "2"):                 # only the exact value "1" turns it on
        rig.write_env({**rig.read_env(), "WATERMARK_ENABLED": bad})
        assert rig.build()["filter"] == "", bad


# =====================================================================================================================
# 3  the selected font is the font FFmpeg draws with
# =====================================================================================================================

@needs_ffmpeg
def test_03_selected_font_is_the_one_rendered(rig, clean):
    frames = {}
    for family, key in (("Montserrat", "montserrat"), ("Anton", "anton"), ("Press Start 2P", "press-start-2p")):
        rig.set_watermark(text="Broadcast 123", font_family=family, font_size=72, anchor="center")
        built = rig.build()
        assert built["state"] == "on"
        assert built["fontfile"].endswith(f"scripts/fonts/{key}.ttf")
        assert f"fontfile=./scripts/fonts/{key}.ttf" in built["filter"]
        frames[key] = rig.frame(built)
        # ground truth: FFmpeg asked directly for THAT font file, nothing of ours in between
        ref = rig.frame({"filter": f"drawtext=fontfile=./scripts/fonts/{key}.ttf:text=Broadcast 123:fontsize=48:"
                                   f"fontcolor=0xFFFFFF@1.00:x=(main_w-text_w)/2:y=(main_h-text_h)/2"})
        assert frames[key] == ref, f"{family}: output is not what FFmpeg draws with {key}.ttf"
    assert frames["montserrat"] != frames["anton"] != frames["press-start-2p"] != frames["montserrat"]
    widths = {k: changed(clean, f)[1][2] - changed(clean, f)[1][0] for k, f in frames.items()}
    assert widths["anton"] < widths["montserrat"] < widths["press-start-2p"]     # condensed < normal < pixel-wide


@needs_ffmpeg
def test_03b_every_catalogue_font_loads_in_ffmpeg_and_draws_its_own_shape(tmp_path):
    """All 66 fonts: FFmpeg opens each file and each produces a distinct rendering (no font silently maps to another)."""
    manifest = json.loads((FONTS / "fonts.json").read_text(encoding="utf-8"))["fonts"]
    rig = Rig(tmp_path, fonts=[f["key"] for f in manifest])
    seen = {}
    blank = rig.frame({"filter": ""}, 480, 120)
    for f in manifest:
        rig.set_watermark(text="Ag 123 Live", font_family=f["family"], font_size=90, anchor="center")
        built = rig.build(480, 120)
        assert built["state"] == "on", (f["family"], built["reason"])
        frame = rig.frame(built, 480, 120)
        assert frame != blank, f"{f['family']} drew nothing"
        assert frame not in seen, f"{f['family']} renders identically to {seen.get(frame)}"
        seen[frame] = f["family"]
    assert len(seen) == len(manifest) >= 60


@needs_bash
def test_03c_missing_font_is_an_error_not_a_silent_default(rig):
    rig.set_watermark(text="X", font_family="Poppins")              # in the catalogue, but not installed in this sandbox
    built = rig.build()
    assert built["state"] == "error" and built["filter"] == ""
    assert "poppins" in built["reason"] and "not installed" in built["reason"]


@needs_bash
def test_03d_damaged_font_file_is_reported(rig):
    (rig.app / "scripts" / "fonts" / "anton.ttf").write_bytes(b"<html>not a font</html>")
    rig.set_watermark(text="X", font_family="Anton")
    built = rig.build()
    assert built["state"] == "error" and "not a usable TrueType/OpenType file" in built["reason"]


def test_03e_dashboard_refuses_and_reports_an_unusable_font(tmp_path, monkeypatch):
    d = tmp_path / "fonts"
    d.mkdir()
    shutil.copy(FONTS / "fonts.json", d / "fonts.json")
    shutil.copy(FONTS / "anton.ttf", d / "anton.ttf")
    (d / "poppins.ttf").write_bytes(b"\x00\x01\x00\x00 truncated, not the released file")
    monkeypatch.setattr(config, "WATERMARK_FONTS_DIR", d)
    cat = {f["family"]: f for f in overlay.font_catalogue()}
    assert cat["Anton"]["installed"] is True
    assert cat["Roboto"]["installed"] is False and "not installed" in cat["Roboto"]["error"]
    assert cat["Poppins"]["installed"] is False and "does not match" in cat["Poppins"]["error"]
    state, rejected = overlay.normalize({"font_family": "Roboto"}, overlay.DEFAULT_OVERLAY_STATE)
    assert state["font_family"] == "Montserrat" and "cannot be used" in rejected["font_family"]
    state, rejected = overlay.normalize({"font_family": "Comic Sans MS"}, overlay.DEFAULT_OVERLAY_STATE)
    assert "not in the font catalogue" in rejected["font_family"]
    problems = overlay.config_problems(dict(overlay.DEFAULT_OVERLAY_STATE, font_family="Roboto"))
    assert problems and problems[0]["level"] == "error" and "cannot be loaded" in problems[0]["message"]
    assert overlay.font_categories(installed_only=True) == {"High-Impact & Broadcast Titles": ["Anton"]}


def test_03f_manifest_matches_the_font_files_on_disk():
    import hashlib
    manifest = json.loads((FONTS / "fonts.json").read_text(encoding="utf-8"))["fonts"]
    assert len(manifest) >= 60
    for f in manifest:
        data = (FONTS / f["file"]).read_bytes()
        assert data[:4] in (b"\x00\x01\x00\x00", b"OTTO", b"true"), f["file"]
        assert hashlib.sha256(data).hexdigest() == f["sha256"], f["file"]
        assert f["key"] == overlay.font_key(f["family"]) and f["file"] == f["key"] + ".ttf"
    on_disk = {p.name for p in FONTS.glob("*.ttf")}
    assert on_disk == {f["file"] for f in manifest}, "a font file without a manifest entry (or the reverse)"
    assert all(f["installed"] for f in overlay.font_catalogue())


# =====================================================================================================================
# 4-7  size, position, opacity, colour - measured on the frame
# =====================================================================================================================

@needs_ffmpeg
def test_04_font_size_changes_the_rendered_size(rig, clean):
    heights = []
    for size in (30, 60, 120):
        rig.set_watermark(text="HEIGHT", font_family="Anton", font_size=size, anchor="center")
        _, box, _ = changed(clean, rig.frame(rig.build()))
        heights.append(box[3] - box[1])
    assert heights[0] < heights[1] < heights[2]
    assert 1.6 < heights[1] / heights[0] < 2.4 and 1.6 < heights[2] / heights[1] < 2.4


@needs_ffmpeg
def test_04b_size_is_relative_to_the_frame_so_720p_and_1080p_look_the_same(rig):
    rig.set_watermark(text="SCALE", font_family="Anton", font_size=90, anchor="top-left", margin_x=60, margin_y=60)
    boxes = {}
    for w, h in ((1280, 720), (1920, 1080)):
        boxes[h] = changed(rig.frame({"filter": ""}, w, h), rig.frame(rig.build(w, h), w, h), w, h)[1]
    for i in range(4):                                               # same place and size as a fraction of the frame
        assert abs(boxes[720][i] / 720 - boxes[1080][i] / 1080) < 0.012
    # frame size unknown (direct source): FFmpeg scales it itself, to the same result
    assert "fontsize=main_h*90/1080" in rig.build(0, 0)["filter"]
    unknown = changed(rig.frame({"filter": ""}), rig.frame(rig.build(0, 0)))[1]
    assert all(abs(unknown[i] - boxes[720][i]) <= 2 for i in range(4))


@needs_ffmpeg
def test_05_position_every_anchor_lands_where_it_says(rig, clean):
    centres = {}
    for anchor in overlay.ENCODER_ANCHORS:
        rig.set_watermark(text="POS", font_family="Anton", font_size=60, anchor=anchor, margin_x=30, margin_y=30)
        _, box, _ = changed(clean, rig.frame(rig.build()))
        centres[anchor] = ((box[0] + box[2]) / 2 / W, (box[1] + box[3]) / 2 / H)
        vert, _, horiz = anchor.partition("-")
        if anchor == "center":
            vert, horiz = "middle", "center"
        if horiz == "left":
            assert abs(box[0] - 20) <= 3                             # margin 30 on a 1080 frame = 20 px at 720p
        if horiz == "right":
            assert abs((W - 1 - box[2]) - 20) <= 3
        if horiz == "center":
            assert abs(centres[anchor][0] - 0.5) < 0.01
        if vert == "top":
            assert abs(box[1] - 20) <= 6
        if vert == "bottom":
            assert abs((H - 1 - box[3]) - 20) <= 6
        if vert == "middle":
            assert abs(centres[anchor][1] - 0.5) < 0.02
    assert len({(round(x, 1), round(y, 1)) for x, y in centres.values()}) == 9


@needs_ffmpeg
def test_05b_dragged_position_lands_where_it_was_dropped(rig, clean):
    for px, py in ((50, 50), (10, 20), (90, 85)):
        rig.set_watermark(text="DRAG", font_family="Anton", font_size=60, anchor="custom", pos_x=px, pos_y=py)
        built = rig.build()
        assert "main_w*%.2f/100" % px in built["filter"]
        _, box, _ = changed(clean, rig.frame(built))
        if px == 50:
            assert abs((box[0] + box[2]) / 2 / W - 0.5) < 0.01 and abs((box[1] + box[3]) / 2 / H - 0.5) < 0.02
        elif px < 30:
            assert abs(box[0] / W - px / 100) < 0.01 and abs(box[1] / H - py / 100) < 0.02      # left/top edge on the point
        else:
            assert abs(box[2] / W - px / 100) < 0.01 and abs(box[3] / H - py / 100) < 0.02      # right/bottom edge on it


@needs_ffmpeg
def test_06_opacity_changes_how_strongly_it_is_drawn(rig, clean):
    def strength(opacity):
        rig.set_watermark(text="OPACITY", font_family="Anton", font_size=120, anchor="center", font_opacity=opacity)
        built = rig.build()
        assert "fontcolor=0xFFFFFF@%.2f" % (opacity / 100) in built["filter"]
        frame = rig.frame(built)
        i = ((H // 2) * W) * 3
        row = frame[i:i + W * 3]
        return max(row[0::3]), changed(clean, frame, thr=6)[0]
    full, half, faint, none = strength(100), strength(50), strength(20), strength(0)
    assert full[0] > 240                                             # pure white at 100 %
    assert BG[0] + 90 < half[0] < 170                                # about half way between background and white
    assert BG[0] < faint[0] < half[0]
    assert none[1] == 0, "opacity 0 must draw nothing visible"


@needs_ffmpeg
def test_07_colour_is_the_selected_colour(rig, clean):
    for hexcol, want in (("#FF0000", (255, 0, 0)), ("#00FF00", (0, 255, 0)), ("#1E90FF", (30, 144, 255)), ("#FFFFFF", (255, 255, 255))):
        rig.set_watermark(text="COLOUR", font_family="Anton", font_size=140, anchor="center", font_color=hexcol)
        built = rig.build()
        assert f"fontcolor=0x{hexcol[1:]}@1.00" in built["filter"]
        frame = rig.frame(built)
        # the most saturated drawn pixel is the solid fill of a glyph (anti-aliased edges blend with the background)
        best = max((tuple(frame[i:i + 3]) for i in range((H // 2) * W * 3, (H // 2 + 1) * W * 3, 3)),
                   key=lambda px: sum(abs(px[c] - BG[c]) for c in range(3)))
        assert all(abs(best[c] - want[c]) <= 6 for c in range(3)), (hexcol, best)


@needs_ffmpeg
def test_07b_outline_box_and_shadow_reach_the_encoder(rig, clean):
    rig.set_watermark(text="DECOR", font_family="Anton", font_size=90, anchor="center")
    plain = changed(clean, rig.frame(rig.build()))
    rig.set_watermark(text="DECOR", font_family="Anton", font_size=90, anchor="center", box_enabled=True,
                      box_color="#FF0000", box_opacity=100, box_padding=30)
    built = rig.build()
    assert "box=1:boxcolor=0xFF0000@1.00:boxborderw=20" in built["filter"]
    boxed = changed(clean, rig.frame(built))
    assert boxed[1][0] < plain[1][0] - 15 and boxed[1][2] > plain[1][2] + 15      # the box extends past the text by the padding
    rig.set_watermark(text="DECOR", font_family="Anton", font_size=90, anchor="center", outline_enabled=True,
                      outline_width=6, outline_color="#00FF00", shadow_enabled=True, shadow_x=9, shadow_y=9)
    built = rig.build()
    assert "borderw=4:bordercolor=0x00FF00@1.00" in built["filter"] and "shadowx=6:shadowy=6" in built["filter"]
    assert changed(clean, rig.frame(built))[0] > plain[0]
    rig.set_watermark(text="DECOR", font_family="Anton", font_size=90, anchor="center")           # all three off again
    built = rig.build()
    assert not any(k in built["filter"] for k in ("borderw", "box=", "shadow"))
    assert changed(clean, rig.frame(built)) == plain


@needs_ffmpeg
def test_07c_image_watermark_position_size_and_opacity(rig, clean):
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=200x100",
                    "-frames:v", "1", "watermarks/logo.png"], cwd=rig.app, check=True, timeout=60)
    rig.set_watermark(mode="image", image_path="./watermarks/logo.png", image_scale_pct=25, image_opacity=100,
                      anchor="top-left", margin_x=30, margin_y=30)
    built = rig.build()
    assert built["state"] == "on" and built["inputs"] == ["-i", "./watermarks/logo.png"]
    n, box, colour = changed(clean, rig.frame(built))
    assert abs(box[0] - 20) <= 1 and abs(box[1] - 20) <= 1
    assert abs((box[2] - box[0] + 1) - W * 0.25) <= 3 and abs((box[3] - box[1] + 1) - W * 0.125) <= 3
    assert colour[0] > 200 and colour[1] < 40
    rig.set_watermark(mode="image", image_path="./watermarks/logo.png", image_scale_pct=25, image_opacity=40, anchor="top-left")
    assert changed(clean, rig.frame(rig.build()))[2][0] < 160
    rig.set_watermark(mode="image", image_path="./watermarks/logo.png", visible=False)
    assert rig.build()["filter"] == "" and rig.build()["inputs"] == []


@needs_bash
def test_07d_image_must_be_the_uploaded_logo_and_exist(rig):
    for path, why in (("", "no image has been uploaded"), ("/etc/passwd", "not inside the watermark folder"),
                      ("./watermarks/../.env", "not inside the watermark folder"),
                      ("./watermarks/logo.png", "missing or unreadable")):
        rig.set_watermark(mode="image", image_path=path)
        built = rig.build()
        assert built["state"] == "error" and why in built["reason"] and built["filter"] == "" and not built["inputs"]


# =====================================================================================================================
# text handling
# =====================================================================================================================

@needs_ffmpeg
def test_text_with_special_characters_is_drawn_literally(rig, clean):
    nasty = "100% it's \"a\": b\\c, [x]; %{pts} $HOME `id` 'q'"
    rig.set_watermark(text=nasty, font_family="Montserrat", font_size=40, anchor="center")
    built = rig.build()
    assert built["state"] == "on" and "expansion=none" in built["filter"]
    assert "{pts}" not in built["filter"] and "HOME" not in built["filter"] and "100" not in built["filter"]       # the text never enters the filter string
    assert (rig.app / "logs" / "watermark-text.bash.0.txt").read_text(encoding="utf-8") == nasty
    n, box, _ = changed(clean, rig.frame(built))
    assert n > 500 and (box[2] - box[0]) > W * 0.4                   # a long line was really drawn (no expansion error)


@needs_ffmpeg
def test_multiline_text_is_one_filter_per_line_without_stray_glyphs(rig, clean):
    rig.set_watermark(text="LINE ONE\nLINE ONE", font_family="Anton", font_size=60, anchor="center")
    built = rig.build()
    assert built["filter"].count("drawtext=") == 2
    files = sorted(p.name for p in (rig.app / "logs").glob("watermark-text.*.txt"))
    assert files == ["watermark-text.bash.0.txt", "watermark-text.bash.1.txt"]
    assert all("\n" not in (rig.app / "logs" / f).read_text(encoding="utf-8") for f in files)
    two = changed(clean, rig.frame(built))[1]
    rig.set_watermark(text="LINE ONE", font_family="Anton", font_size=60, anchor="center")
    built = rig.build()
    one = changed(clean, rig.frame(built))[1]
    assert (two[3] - two[1]) > 1.8 * (one[3] - one[1])               # two lines tall
    assert abs((two[2] - two[0]) - (one[2] - one[0])) <= 4           # ...and no wider: no "missing glyph" box at the break
    assert sorted(p.name for p in (rig.app / "logs").glob("watermark-text.*.txt")) == ["watermark-text.bash.0.txt"]


@needs_ffmpeg
def test_sinhala_text_renders_with_a_sinhala_font(rig, clean):
    rig.set_watermark(text="සජීවී විකාශය", font_family="Noto Sans Sinhala", font_size=80, anchor="center")
    built = rig.build()
    assert built["state"] == "on"
    n, box, _ = changed(clean, rig.frame(built))
    assert n > 800 and (box[2] - box[0]) > 150
    warn = overlay.config_problems(dict(overlay.DEFAULT_OVERLAY_STATE, text="සජීවී", font_family="Anton"))
    assert warn and warn[0]["level"] == "warning" and "Sinhala" in warn[0]["message"]
    assert overlay.config_problems(dict(overlay.DEFAULT_OVERLAY_STATE, text="සජීවී", font_family="Noto Sans Sinhala")) == []


@needs_bash
def test_empty_text_is_reported(rig):
    for text in ("", "   ", "\n\n"):
        rig.set_watermark(text=text)
        built = rig.build()
        assert built["state"] == "error" and "text is empty" in built["reason"] and built["filter"] == ""


@needs_bash
def test_hostile_values_in_the_env_file_cannot_reach_the_filter(rig):
    """The env file is re-validated by the shell: nothing but numbers, hex colours, known anchors and a font KEY gets
    into the filter string, so a tampered file cannot add a filter, an option or a path."""
    env = rig.set_watermark(text="SAFE", font_family="Montserrat")
    rig.write_env({**rig.read_env(), **env,
                   "WATERMARK_SIZE": "9;movie=/etc/passwd", "WATERMARK_COLOR": "red:box=1:boxcolor=white",
                   "WATERMARK_OPACITY": "1:text=pwn", "WATERMARK_ANCHOR": "x=0:y=0,movie=/etc/passwd",
                   "WATERMARK_MARGIN_X": "$(id)", "WATERMARK_MARGIN_Y": "1,drawbox", "WATERMARK_POS_X": "50:fontfile=/x",
                   "WATERMARK_OUTLINE_WIDTH": "3:textfile=/etc/shadow", "WATERMARK_OUTLINE_COLOR": "0x00;rm",
                   "WATERMARK_BOX": "1", "WATERMARK_BOX_COLOR": "[in]", "WATERMARK_BOX_OPACITY": "x", "WATERMARK_BOX_PADDING": "9:reload=1",
                   "WATERMARK_SHADOW": "1", "WATERMARK_SHADOW_X": "1:y=0", "WATERMARK_SHADOW_Y": "--5", "WATERMARK_SHADOW_COLOR": "'"})
    flt = rig.build()["filter"]
    assert flt.startswith("drawtext=fontfile=./scripts/fonts/montserrat.ttf:textfile=./logs/watermark-text.bash.0.txt:")
    for bad in ("movie", "passwd", "shadow=/", "pwn", "drawbox", "reload", "$(", "id)", ";", "'", "[in]", "red", "/etc", "/x"):
        assert bad not in flt, bad
    assert flt.count("drawtext=") == 1 and flt.count("textfile=") == 1 and flt.count("fontfile=") == 1
    for font in ("a.ttf:text=pwn", "../../etc/passwd", "Montserrat", "mont serrat", "-x", "x-", ""):
        rig.write_env({**rig.read_env(), "WATERMARK_FONT": font})
        built = rig.build()
        if font == "":
            assert built["state"] == "on" and "/inter.ttf" in built["filter"]      # unset = the documented default font
        else:
            assert built["state"] == "error" and built["filter"] == "", font


@needs_bash
def test_settings_written_by_the_previous_version_still_work(rig):
    """An install upgraded mid-life: only the old keys exist in current-source.env."""
    rig.write_env({"WATERMARK_ENABLED": "1", "WATERMARK_MODE": "text", "WATERMARK_TEXT": "OLD", "WATERMARK_FONT": "inter",
                   "WATERMARK_ANCHOR": "top-left", "WATERMARK_MARGIN_X": "24", "WATERMARK_MARGIN_Y": "24",
                   "WATERMARK_SIZE": "28", "WATERMARK_OPACITY": "100", "WATERMARK_IMAGE_PATH": ""})
    built = rig.build()
    assert built["state"] == "on" and "inter.ttf" in built["filter"] and "fontcolor=0xFFFFFF@1.00" in built["filter"]
    assert (rig.app / "logs" / "watermark-text.bash.0.txt").read_text(encoding="utf-8") == "OLD"


# =====================================================================================================================
# 8  save -> reload: API, overlay.json, env file, shell
# =====================================================================================================================

class Api:
    def __init__(self, client, csrf, rig, calls):
        self.client, self.csrf, self.rig, self.calls = client, csrf, rig, calls
        self.unit = {"phase": "IDLE", "main_pid": 0}

    def post(self, path, body=None, **kw):
        return self.client.post(path, json=body if body is not None else {}, headers={"X-CSRF-Token": self.csrf}, **kw)

    def save(self, **settings):
        r = self.post("/api/overlay", settings)
        assert r.status_code == 200, r.text
        return r.json()

    def enable(self, on: bool):
        r = self.post("/api/overlay/toggle", {"enabled": on})
        assert r.status_code == 200, r.text
        return r.json()


@pytest.fixture
def api(tmp_path, monkeypatch, rig):
    """The real FastAPI app, with the two sudo bridges (read/write current-source.env) pointed at the sandbox file and
    every way of touching a systemd unit replaced by a recorder."""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test_dashboard.db")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OVERLAY_CONFIG_FILE", tmp_path / "data" / "overlay.json")
    monkeypatch.setattr(config, "STREAM_LOGS_DIR", rig.app / "logs")
    monkeypatch.setattr(config, "WATERMARK_FONTS_DIR", rig.app / "scripts" / "fonts")
    db.init_db()
    broadcast.reset_for_tests()
    control._pending_auth.clear()
    from app import security
    with db.get_conn() as conn:
        uid = conn.execute("INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                           ("admin", security.hash_password("correct-horse-battery-staple"), time.time())).lastrowid
    sid, csrf = security.create_session(uid, "127.0.0.1")

    calls = {"unit_action": [], "sudo": [], "writes": 0}
    holder = {}

    def fake_write(values):
        calls["writes"] += 1
        bad = [k for k, v in values.items() if "\n" in v]
        assert not bad, f"newline in {bad} - write-source.sh would refuse this"
        rig.env_path.write_text(env_text(values), encoding="utf-8", newline="\n")

    def fake_unit_action(unit, verb, *, grant=None):
        calls["unit_action"].append((unit, verb))
        return {"ok": True, "unit": unit, "verb": verb, "stderr": ""}

    def fake_sudo(argv, input_bytes=None, timeout=15):
        calls["sudo"].append(list(argv))
        raise control.ControlError("no sudo in tests")

    monkeypatch.setattr(control, "read_current_source", lambda: rig.read_env())
    monkeypatch.setattr(control, "_write_source_env_values", fake_write)
    monkeypatch.setattr(control, "unit_action", fake_unit_action)
    monkeypatch.setattr(control, "run_as_zoombot", fake_sudo)
    monkeypatch.setattr(control, "unit_show", lambda unit: dict(holder["api"].unit, unit=unit))
    monkeypatch.setattr(control, "watermark_is_running", lambda: False)
    monkeypatch.setattr(sources_mod, "get_active_source", lambda: None)

    async def no_kiosk(expression, user_gesture=False):
        raise RuntimeError("no kiosk in tests")
    from app import cdp
    monkeypatch.setattr(cdp, "evaluate", no_kiosk)

    client = TestClient(app, cookies={config.COOKIE_NAME: sid})
    holder["api"] = Api(client, csrf, rig, calls)
    yield holder["api"]
    control._pending_auth.clear()


def test_08_save_and_reload_round_trip(api):
    text = "Grade 12 \"Physics\" 100% - it's\\live\nසජීවී"
    res = api.save(text=text, font_family="Noto Sans Sinhala", font_size=57, font_color="#12ab9f", font_opacity=64,
                   anchor="custom", pos_x=33.3, pos_y=71.25, margin_x=40, margin_y=12, outline_enabled=True,
                   outline_width=5, outline_color="#010203", box_enabled=False, shadow_enabled=True, shadow_x=-7, shadow_y=9)
    assert res["ok"] and res["pipeline_synced"] is True and res["rejected"] == {}
    api.enable(True)

    # 1. the API reads back exactly what was saved
    got = api.client.get("/api/overlay").json()
    want = dict(text=text, font_family="Noto Sans Sinhala", font_size=57, font_color="#12AB9F", font_opacity=64,
                anchor="custom", pos_x=33.3, pos_y=71.25, margin_x=40, margin_y=12, outline_enabled=True, outline_width=5,
                outline_color="#010203", box_enabled=False, shadow_enabled=True, shadow_x=-7, shadow_y=9, visible=True)
    assert {k: got[k] for k in want} == want

    # 2. a fresh process (nothing cached) reads the same from disk
    on_disk = json.loads(config.OVERLAY_CONFIG_FILE.read_text(encoding="utf-8"))
    assert {k: on_disk[k] for k in want} == want
    assert overlay.get_overlay_state() == got

    # 3. the encoder's file holds the same settings, every value on one line
    env = api.rig.read_env()
    assert env["WATERMARK_ENABLED"] == "1" and env["WATERMARK_FONT"] == "noto-sans-sinhala"
    assert base64.b64decode(env["WATERMARK_TEXT_B64"]).decode("utf-8") == text
    assert (env["WATERMARK_SIZE"], env["WATERMARK_COLOR"], env["WATERMARK_OPACITY"]) == ("57", "#12AB9F", "64")
    assert (env["WATERMARK_ANCHOR"], env["WATERMARK_POS_X"], env["WATERMARK_POS_Y"]) == ("custom", "33.30", "71.25")
    assert (env["WATERMARK_OUTLINE_WIDTH"], env["WATERMARK_BOX"], env["WATERMARK_SHADOW_X"]) == ("5", "0", "-7")
    assert all("\n" not in v for v in env.values())
    assert env["SOURCE_TYPE"] == "direct" and env["DIRECT_URL"] == "https://example.com/live.m3u8"   # source keys untouched


@needs_bash
def test_08b_reloaded_settings_reach_the_shell_unchanged(api):
    text = "Grade 12 \"Physics\" 100% - it's\\live\nසජීවී"
    api.save(text=text, font_family="Noto Sans Sinhala", font_size=57, font_color="#12ab9f", font_opacity=64,
             anchor="top-center", outline_enabled=False, box_enabled=False, shadow_enabled=False)
    api.enable(True)
    built = api.rig.build()
    assert built["state"] == "on"
    assert "fontfile=./scripts/fonts/noto-sans-sinhala.ttf" in built["filter"]
    assert "fontsize=38:fontcolor=0x12AB9F@0.64" in built["filter"]             # 57 px on 1080 lines = 38 px at 720p
    logs = api.rig.app / "logs"
    assert (logs / "watermark-text.bash.0.txt").read_text(encoding="utf-8") == text.split("\n")[0]
    assert (logs / "watermark-text.bash.1.txt").read_text(encoding="utf-8") == "සජීවී"


def test_08c_invalid_values_are_rejected_and_reported_not_silently_dropped(api):
    before = api.client.get("/api/overlay").json()
    res = api.save(font_color="red", font_size="huge", anchor="somewhere", mode="video", font_family="Wingdings",
                   outline_enabled="maybe", font_opacity=250, margin_x=-5)
    assert set(res["rejected"]) == {"font_color", "font_size", "anchor", "mode", "font_family", "outline_enabled"}
    after = res["state"]
    for k in ("font_color", "font_size", "anchor", "mode", "font_family", "outline_enabled"):
        assert after[k] == before[k]
    assert after["font_opacity"] == 100 and after["margin_x"] == 0              # out of range = clamped to the limit
    assert api.post("/api/overlay", ["not", "an", "object"]).status_code == 400


def test_08d_settings_save_cannot_set_the_image_path_or_the_switch(api):
    res = api.save(image_path="/etc/shadow", visible=True, mode="image")
    assert res["state"]["image_path"] == "" and res["state"]["visible"] is False
    env = api.rig.read_env()
    assert env["WATERMARK_IMAGE_PATH"] == "" and env["WATERMARK_ENABLED"] == "0"


def test_08e_unreadable_or_tampered_overlay_json_falls_back_safely(api):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.OVERLAY_CONFIG_FILE.write_text("{ this is not json", encoding="utf-8")
    assert overlay.get_overlay_state()["visible"] is False           # unreadable = OFF, never a guessed ON
    config.OVERLAY_CONFIG_FILE.write_text(json.dumps({"visible": "false", "font_size": 99999, "font_color": "x",
                                                      "anchor": "nowhere", "text": "ok\x00\x07", "bogus": 1}), encoding="utf-8")
    st = overlay.get_overlay_state()
    assert st["visible"] is False and st["font_size"] == 400 and st["font_color"] == "#FFFFFF"
    assert st["anchor"] == "bottom-left" and st["text"] == "ok" and "bogus" not in st


# =====================================================================================================================
# ON/OFF reliability at the API
# =====================================================================================================================

def test_switch_is_explicit_and_idempotent(api):
    assert api.enable(True)["state"]["visible"] is True
    assert api.enable(True)["state"]["visible"] is True             # a double click / retry does not flip it back
    assert api.rig.read_env()["WATERMARK_ENABLED"] == "1"
    assert api.enable(False)["state"]["visible"] is False
    assert api.enable(False)["state"]["visible"] is False
    assert api.rig.read_env()["WATERMARK_ENABLED"] == "0"
    assert api.post("/api/overlay/toggle", {"enabled": "yes"}).status_code == 400
    assert api.post("/api/overlay/toggle").json()["state"]["visible"] is True   # legacy body-less call still flips


def test_a_stale_autosave_cannot_turn_the_watermark_back_on(api):
    """The race behind "I turned it off and it came back": a settings autosave that was composed while the watermark
    was still ON (another tab, or a debounce timer firing after the OFF click) carries visible=true."""
    api.enable(True)
    stale_page_state = api.client.get("/api/overlay").json()        # visible: true
    api.enable(False)
    res = api.save(**dict(stale_page_state, text="edited in a stale tab"))
    assert res["state"]["visible"] is False and res["state"]["text"] == "edited in a stale tab"
    assert api.rig.read_env()["WATERMARK_ENABLED"] == "0"
    assert json.loads(config.OVERLAY_CONFIG_FILE.read_text(encoding="utf-8"))["visible"] is False


def test_multiline_text_no_longer_blocks_the_switch(api):
    """Before: a line break in the text made the env write fail (newline refused), the failure was swallowed, and the
    encoder kept whatever it had - including ON after the operator chose OFF."""
    api.save(text="two\nlines")
    assert api.enable(True)["pipeline_synced"] is True
    assert api.rig.read_env()["WATERMARK_ENABLED"] == "1"
    assert api.enable(False)["pipeline_synced"] is True
    assert api.rig.read_env()["WATERMARK_ENABLED"] == "0"


def test_a_failed_pipeline_write_is_reported_not_hidden(api, monkeypatch):
    api.enable(True)

    def boom(values):
        raise control.ControlError("sudo: a password is required")
    monkeypatch.setattr(control, "_write_source_env_values", boom)
    res = api.enable(False)
    assert res["ok"] is True and res["state"]["visible"] is False     # the choice itself is saved...
    assert res["pipeline_synced"] is False and "could not be updated" in res["sync_error"]   # ...and the failure is said
    assert api.rig.read_env()["WATERMARK_ENABLED"] == "1"             # the encoder's file is stale at this point


@needs_bash
def test_11b_a_stale_env_file_cannot_put_a_disabled_watermark_on_air(api, monkeypatch):
    """...and even then the next broadcast is right: the authorized start re-reads the saved settings and writes them
    in the same step as the start permission."""
    api.save(text="STALE", font_family="Anton")
    api.enable(True)
    real_write = control._write_source_env_values
    monkeypatch.setattr(control, "_write_source_env_values", lambda v: (_ for _ in ()).throw(control.ControlError("down")))
    assert api.enable(False)["pipeline_synced"] is False
    assert api.rig.read_env()["WATERMARK_ENABLED"] == "1"             # stale: still says ON
    monkeypatch.setattr(control, "_write_source_env_values", real_write)

    control.write_broadcast_auth({"BROADCAST_AUTH_ID": "g", "BROADCAST_AUTH_NONCE": "n0nce",
                                  "BROADCAST_AUTH_EXPIRES": str(int(time.time()) + 30)})
    assert api.rig.read_env()["WATERMARK_ENABLED"] == "0"
    r, ran, argv = api.rig.run_stream()
    assert ran and "drawtext" not in argv and "overlay=" not in argv
    assert api.rig.status()["STATE"] == "off"


# =====================================================================================================================
# 9, 11  service restarts in the sandbox: no stale filter
# =====================================================================================================================

@needs_bash
def test_09_restart_picks_up_exactly_the_saved_settings_each_time(api):
    rig = api.rig
    api.save(text="FIRST", font_family="Anton", font_size=60, box_enabled=False, outline_enabled=False, shadow_enabled=False)
    api.enable(True)
    rig.authorize()
    r, ran, argv = rig.run_stream()
    assert ran and argv.count("drawtext=") == 1 and "anton.ttf" in argv
    first = rig.status()
    assert first["STATE"] == "on" and first["FONT"] == "anton" and first["FONT_FILE"] == "anton.ttf"
    assert first["HASH"] == rig.read_env()["WATERMARK_CONFIG_HASH"]
    assert "Watermark: on, font anton.ttf" in r.stdout

    # "restart": changed settings + a new authorized start
    api.save(font_family="Montserrat", font_color="#FF0000")
    rig.authorize()
    r, ran, argv = rig.run_stream()
    assert ran and "montserrat.ttf" in argv and "anton.ttf" not in argv and "fontcolor=0xFF0000@1.00" in argv
    second = rig.status()
    assert second["FONT"] == "montserrat" and second["HASH"] != first["HASH"]
    assert second["HASH"] == rig.read_env()["WATERMARK_CONFIG_HASH"]


@needs_bash
def test_11_no_stale_watermark_filter_after_it_is_turned_off(api):
    rig = api.rig
    api.save(text="GOING AWAY", font_family="Anton")
    api.enable(True)
    rig.authorize()
    _, ran, argv = rig.run_stream()
    assert ran and "drawtext=" in argv and rig.status()["STATE"] == "on"
    assert list((rig.app / "logs").glob("watermark-text.stream.*.txt"))

    api.enable(False)
    rig.authorize()
    r, ran, argv = rig.run_stream()
    assert ran, r.stdout
    assert "drawtext" not in argv and "overlay" not in argv and "-vf" not in argv and "filter_complex" not in argv
    assert "watermark" not in argv.lower() and "fontfile" not in argv
    st = rig.status()
    assert st["STATE"] == "off" and st["FONT_FILE"] == ""
    assert "Watermark: off (turned off)" in r.stdout

    # image -> off as well (the image input must be gone too)
    subprocess.run([BASH, "-c", "printf 'x' > watermarks/logo.png"], cwd=rig.app, check=True)
    overlay.set_image(_posix(rig.app) + "/watermarks/logo.png")
    api.enable(True)
    rig.authorize()
    _, ran, argv = rig.run_stream()
    assert ran and "overlay=" in argv and "logo.png" in argv
    api.enable(False)
    rig.authorize()
    _, ran, argv = rig.run_stream()
    assert ran and "overlay" not in argv and "logo.png" not in argv


@needs_bash
def test_11c_missing_font_at_start_is_recorded_and_the_stream_still_starts(api):
    rig = api.rig
    api.save(text="X", font_family="Anton")
    api.enable(True)
    (rig.app / "scripts" / "fonts" / "anton.ttf").unlink()
    rig.authorize()
    r, ran, argv = rig.run_stream()
    assert ran and "drawtext" not in argv                            # the broadcast is not blocked by a cosmetic fault
    st = rig.status()
    assert st["STATE"] == "error" and "anton" in st["REASON"] and "not installed" in st["REASON"]
    assert "Watermark NOT applied" in r.stdout
    # ...and the dashboard says so instead of showing "ON"
    api.unit = {"phase": "LIVE", "main_pid": int(st["PID"])}
    live = api.client.get("/api/overlay/status").json()
    assert live["live_state"] == "error" and live["encoder_active"] is False
    assert "NO watermark" in live["message"] and "not installed" in live["message"]
    assert any(p["level"] == "error" for p in live["problems"])


# =====================================================================================================================
# live status + "Changes will apply to the next stream."
# =====================================================================================================================

def _write_status(rig, **kv):
    (rig.app / "logs" / "watermark.status").write_text("".join(f"{k}={v}\n" for k, v in kv.items()), encoding="utf-8")


def test_live_changes_are_queued_for_the_next_stream_and_say_so(api):
    api.save(text="LIVE", font_family="Anton")
    res = api.enable(True)
    assert res["status"]["encoder_live"] is False and res["status"]["pending"] is False
    assert "next stream" in res["message"] and overlay.NEXT_STREAM_MESSAGE not in res["message"]

    # the encoder is now live with exactly these settings
    live_hash = api.rig.read_env()["WATERMARK_CONFIG_HASH"]
    _write_status(api.rig, STATE="on", REASON="", FONT="anton", HASH=live_hash, PID=4242)
    api.unit = {"phase": "LIVE", "main_pid": 4242}
    st = api.client.get("/api/overlay/status").json()
    assert st["encoder_live"] and st["live_known"] and st["live_state"] == "on" and st["pending"] is False
    assert "matches the saved settings" in st["message"]

    # change the font while live: saved + queued, nothing restarted, the exact sentence shown
    res = api.save(font_family="Montserrat")
    assert res["status"]["pending"] is True and res["requires_restart"] is True
    assert overlay.NEXT_STREAM_MESSAGE == "Changes will apply to the next stream."
    assert res["message"].endswith("Changes will apply to the next stream.")
    assert res["status"]["live_font"] == "anton"                     # what is on air is still the old font - and it says so
    res = api.enable(False)
    assert res["status"]["pending"] is True and res["status"]["live_state"] == "on"
    assert "The live stream has the watermark." in res["message"] and res["message"].endswith(overlay.NEXT_STREAM_MESSAGE)
    assert api.calls["unit_action"] == []

    # back to what is live -> nothing pending any more
    api.save(font_family="Anton")
    assert api.enable(True)["status"]["pending"] is False


def test_live_status_never_trusts_a_record_from_another_run(api):
    api.enable(True)
    _write_status(api.rig, STATE="on", HASH=api.rig.read_env()["WATERMARK_CONFIG_HASH"], PID=111)
    api.unit = {"phase": "LIVE", "main_pid": 999}                    # a different encoder process than the record's
    st = api.client.get("/api/overlay/status").json()
    assert st["live_known"] is False and st["pending"] is True and "could be verified" in st["message"]
    api.unit = {"phase": "IDLE", "main_pid": 0}                      # nothing running: the old record means nothing
    st = api.client.get("/api/overlay/status").json()
    assert st["encoder_live"] is False and st["pending"] is False and st["encoder_active"] is False


def test_editing_a_hidden_watermark_is_not_a_pending_change(api):
    api.enable(False)
    _write_status(api.rig, STATE="off", HASH=api.rig.read_env()["WATERMARK_CONFIG_HASH"], PID=7)
    api.unit = {"phase": "LIVE", "main_pid": 7}
    res = api.save(text="draft for later", font_family="Anton", font_size=99)
    assert res["status"]["pending"] is False and "no watermark" in res["message"]


# =====================================================================================================================
# 12  watermark changes never start, restart or authorize a broadcast
# =====================================================================================================================

def test_12_watermark_changes_never_trigger_go_live(api):
    api.unit = {"phase": "IDLE", "main_pid": 0}
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    for phase in ("IDLE", "LIVE"):
        api.unit = {"phase": phase, "main_pid": 0 if phase == "IDLE" else 5}
        api.save(text="A", font_family="Anton", font_size=80, font_color="#00FF00", anchor="center")
        api.enable(True)
        api.save(text="B\nC", mode="text")
        api.enable(False)
        api.post("/api/overlay/toggle")
        api.post("/api/overlay/reinject")
        api.client.post("/api/overlay/image", files={"image": ("logo.png", png, "image/png")},
                        headers={"X-CSRF-Token": api.csrf})
        api.client.get("/api/overlay/status")
        api.client.get("/overlay")

    assert api.calls["unit_action"] == [], "a watermark action touched a systemd unit"
    for argv in api.calls["sudo"]:                                   # the only privileged call attempted: the image writer
        assert "systemctl" not in " ".join(argv), argv
        assert argv[-2].endswith("write-watermark-image.sh"), argv
    assert broadcast.outstanding_grants() == 0                       # no authorization was minted
    assert broadcast.get_session() is None                           # no broadcast session was begun
    env = api.rig.read_env()
    assert env.get("BROADCAST_AUTH_NONCE", "") == "" and env.get("BROADCAST_AUTH_ID", "") == ""
    assert control._pending_auth == {}


@needs_bash
def test_12b_encoder_refuses_to_start_after_watermark_changes_alone(api):
    api.save(text="NO GO LIVE", font_family="Anton")
    api.enable(True)
    api.enable(False)
    api.enable(True)
    r, ran, _ = api.rig.run_stream()                                 # what a stray `systemctl start ffmpeg-stream` would do
    assert not ran and r.returncode == 0
    assert "no valid one-time broadcast authorization" in r.stdout


def test_12c_a_watermark_save_cannot_resurrect_a_used_start_permission(api, public_dns):
    """current-source.env is rewritten whole. A save that read the file while a start permission was in it must not
    write that permission back after stream.sh has burned it - the permission is single use."""
    rig = api.rig
    grant_env = {"BROADCAST_AUTH_ID": "g1", "BROADCAST_AUTH_NONCE": "single-use-nonce",
                 "BROADCAST_AUTH_EXPIRES": str(int(time.time()) + 30)}
    control.write_broadcast_auth(grant_env)                          # Go Live: permission written
    assert rig.read_env()["BROADCAST_AUTH_NONCE"] == "single-use-nonce"
    control.clear_broadcast_auth()                                   # start attempt finished: the dashboard blanks it
    assert rig.read_env()["BROADCAST_AUTH_NONCE"] == ""

    # a writer that read the file at the worst moment (permission still on disk)
    stale_read = {**rig.read_env(), **grant_env}
    api.client  # noqa: B018
    import app.control as c
    orig = c.read_current_source
    c.read_current_source = lambda: stale_read
    try:
        api.save(text="saved at the wrong moment")
        api.enable(True)
        control.write_current_source({"type": "direct", "url": "https://example.com/live.m3u8", "options": {}})
    finally:
        c.read_current_source = orig
    env = rig.read_env()
    assert env["BROADCAST_AUTH_NONCE"] == "" and env["BROADCAST_AUTH_ID"] == "" and env["BROADCAST_AUTH_EXPIRES"] == ""


def test_12d_a_pending_start_permission_survives_a_concurrent_save_but_expires(api):
    grant_env = {"BROADCAST_AUTH_ID": "g2", "BROADCAST_AUTH_NONCE": "fresh", "BROADCAST_AUTH_EXPIRES": str(int(time.time()) + 30)}
    control.write_broadcast_auth(grant_env)
    api.save(text="saved while a start is in flight")               # must not wipe the permission mid-start
    assert api.rig.read_env()["BROADCAST_AUTH_NONCE"] == "fresh"
    control._pending_auth["BROADCAST_AUTH_EXPIRES"] = str(int(time.time()) - 1)      # ...but never past its expiry
    api.save(text="saved later")
    assert api.rig.read_env()["BROADCAST_AUTH_NONCE"] == ""


def test_watermark_save_does_not_touch_the_active_source(api, monkeypatch):
    """Saving a cosmetic setting used to re-apply the whole source (resetting a Zoom meeting's rejoin counter)."""
    zoom = {"id": 3, "name": "Class", "type": "zoom", "url": "", "options": {}, "account_id": None}
    monkeypatch.setattr(sources_mod, "get_active_source", lambda: zoom)
    applied = []
    monkeypatch.setattr(control, "write_current_source", lambda s: applied.append(s))
    api.rig.write_env({"SOURCE_TYPE": "zoom", "ZOOM_JOIN_EPOCH": "1700000000", "ZOOM_FULLSCREEN_LIVE": "1"})
    api.save(text="cosmetic")
    api.enable(True)
    assert applied == []
    env = api.rig.read_env()
    assert env["ZOOM_JOIN_EPOCH"] == "1700000000" and env["ZOOM_FULLSCREEN_LIVE"] == "1" and env["SOURCE_TYPE"] == "zoom"


# =====================================================================================================================
# one implementation only
# =====================================================================================================================

def test_dom_watermark_is_retired_everywhere(api):
    js = overlay.generate_overlay_js(dict(overlay.DEFAULT_OVERLAY_STATE, visible=True, text="MUST NOT BE INJECTED"))
    assert "MUST NOT BE INJECTED" not in js and "createElement" not in js and "appendChild" not in js
    assert ".remove()" in js and "livestream-watermark-overlay" in js
    sched = (REPO / "dashboard" / "app" / "scheduler.py").read_text(encoding="utf-8")
    assert "push_overlay_to_kiosk" not in sched
    page = api.client.get("/overlay").text
    assert "fonts.googleapis.com" not in page and "fonts.gstatic.com" not in page    # the preview uses the installed files
    assert "Force Re-Inject" not in page


def test_preview_serves_the_same_font_file_the_encoder_loads(api):
    r = api.client.get("/overlay/fonts/anton.ttf")
    assert r.status_code == 200 and r.headers["content-type"].startswith("font/ttf")
    assert r.content == (api.rig.app / "scripts" / "fonts" / "anton.ttf").read_bytes()
    assert api.client.get("/overlay/fonts/roboto.ttf").status_code == 404        # in the catalogue, not installed here
    assert api.client.get("/overlay/fonts/..%2F..%2F.env").status_code == 404
    assert api.client.get("/overlay/fonts/nope.ttf").status_code == 404
    page = api.client.get("/overlay").text
    assert "/overlay/fonts/anton.ttf" in page and 'data-font="Anton"' in page
    assert 'data-font="Roboto"' not in page                           # only installed fonts are offered
    anon = TestClient(app)
    assert anon.get("/overlay/fonts/anton.ttf").status_code in (401, 403)


# =====================================================================================================================
# FFmpeg compatibility + test-recording parity (Stage 4 audit findings)
# =====================================================================================================================

@needs_ffmpeg
def test_image_watermark_avoids_deprecated_scale2ref_when_the_frame_size_is_known(rig, clean):
    """scale2ref is deprecated since FFmpeg 7. Capture sources (Zoom, webpage) always know their frame size, so they
    get a plain scale; only a direct source of unknown size still needs scale2ref. Both must draw the same picture."""
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=200x100",
                    "-frames:v", "1", "watermarks/logo.png"], cwd=rig.app, check=True, timeout=60)
    rig.set_watermark(mode="image", image_path="./watermarks/logo.png", image_scale_pct=25, anchor="bottom-right")
    known, unknown = rig.build(W, H), rig.build(0, 0)
    assert "scale2ref" not in known["filter"] and "scale=w=320:h=-2" in known["filter"]
    assert "scale2ref" in unknown["filter"]
    for built in (known, unknown):
        assert built["filter"].count("[0:v]") == 1                  # stream.sh redirects exactly this one reference
        assert built["filter"].endswith("[vout]") and built["inputs"] == ["-i", "./watermarks/logo.png"]
    a, b = changed(clean, rig.frame(known))[1], changed(clean, rig.frame(unknown))[1]
    assert all(abs(a[i] - b[i]) <= 2 for i in range(4)), (a, b)


@needs_bash
def test_test_recording_always_resamples_audio_like_the_broadcast(rig):
    """A test recording with the watermark ON used to drop the audio drift correction the broadcast always applies."""
    fake_bin = rig.app / "bin"
    fake_bin.mkdir()
    (fake_bin / "xdpyinfo").write_text("#!/usr/bin/env bash\necho 'dimensions:    1280x720 pixels'\nexit 0\n",
                                      encoding="utf-8", newline="\n")
    os.chmod(fake_bin / "xdpyinfo", 0o755)
    env = dict(os.environ, APP_DIR=_posix(rig.app), HOME=_posix(rig.app), DISPLAY=":99", TEST_DURATION="1",
               PATH=_posix(fake_bin) + ":" + os.environ.get("PATH", ""))
    seen = {}
    for label, state in (("off", dict(visible=False)), ("text", dict(text="REC", font_family="Anton")),
                         ("image", dict(mode="image", image_path=_posix(rig.app) + "/watermarks/logo.png"))):
        (rig.app / "watermarks" / "logo.png").write_bytes(b"x")
        rig.set_watermark(**state)
        (rig.app / "ffmpeg-argv").unlink(missing_ok=True)
        r = subprocess.run([BASH, _posix(rig.app / "scripts" / "test-recording.sh")], capture_output=True, text=True,
                           env=env, timeout=90)
        argv = (rig.app / "ffmpeg-argv").read_text(encoding="utf-8")
        assert argv.count("-af aresample=async=1:first_pts=0") == 1, (label, r.stdout, argv)
        seen[label] = argv
    assert "drawtext" not in seen["off"] and "overlay" not in seen["off"]
    assert "drawtext=" in seen["text"] and "anton.ttf" in seen["text"]
    assert "overlay=" in seen["image"] and "-map [vout] -map 1:a" in seen["image"]
    assert not (rig.app / "logs" / "watermark.status").exists()     # only the real broadcast reports live status
