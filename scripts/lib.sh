#!/usr/bin/env bash
# Shared helpers. Sourced by every other script in this directory.
# shellcheck disable=SC2034  # many variables set here are read by the scripts that source this file

# ffmpeg.log ends up containing the YouTube RTMP URL (stream key and all)
# in its normal startup output, so logs stay group-readable-only, never
# world-readable: only zoombot and (via group membership) the dashboard
# user can read them, and the dashboard always redacts before display.
umask 027

APP_DIR="${APP_DIR:-$HOME/zoom-stream}"
# ZOOMBOT_RUNTIME_ENV_FILE overrides where secrets are sourced from - set
# by the systemd unit once Part 0's vault migration (Phase A2) lands, to
# point at the tmpfs-backed file write-runtime-env.sh regenerates from the
# encrypted store (see dashboard's app/runtime_env.py). Unset (the default
# today), this is byte-for-byte the pre-vault behavior: read .env straight
# off persistent disk.
ENV_FILE="${ZOOMBOT_RUNTIME_ENV_FILE:-$APP_DIR/.env}"
SOURCE_ENV_FILE="$APP_DIR/current-source.env"
LOG_DIR="$APP_DIR/logs"
mkdir -p "$LOG_DIR"
chmod 750 "$LOG_DIR"

# Loads KEY=VALUE lines from $1 and exports them, without ever passing the
# file through `source`/eval. Values come from the dashboard, so this must
# not let a stray space or shell metacharacter (&, $, ;, `, ...) in a value
# be re-interpreted as shell syntax - it only ever does a plain assignment.
# Supported line forms: KEY=value, KEY="value", KEY='value' (one matching
# pair of outer quotes is stripped; \" and \\ are unescaped inside double
# quotes). Blank lines and lines starting with # (after leading whitespace)
# are skipped. Any other line is treated as a fatal config error rather
# than silently ignored or executed.
load_env_file() {
  local file="$1" line key val
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "${line//[[:space:]]/}" ]] && continue
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
      key="${BASH_REMATCH[1]}"
      val="${BASH_REMATCH[2]}"
      if [[ "$val" == \"*\" && ${#val} -ge 2 ]]; then
        val="${val:1:${#val}-2}"
        val="${val//\\\"/\"}"
        val="${val//\\\\/\\}"
      elif [[ "$val" == \'*\' && ${#val} -ge 2 ]]; then
        val="${val:1:${#val}-2}"
      fi
      export "$key=$val"
    else
      echo "Malformed line in $file, refusing to load: $line" >&2
      exit 1
    fi
  done < "$file"
}

if [[ -f "$ENV_FILE" ]]; then
  load_env_file "$ENV_FILE"
else
  echo "Missing $ENV_FILE" >&2
  exit 1
fi

# current-source.env (Change 1) - which source is active and its
# type-specific options (URL, playback flags). Not secret, so unlike
# .env it's fine for this to be silently absent on an older install that
# hasn't picked a source yet; SOURCE_TYPE then defaults to "zoom" below,
# matching pre-Source-model behavior.
if [[ -f "$SOURCE_ENV_FILE" ]]; then
  load_env_file "$SOURCE_ENV_FILE"
fi
SOURCE_TYPE="${SOURCE_TYPE:-zoom}"

DISPLAY_NUM="${DISPLAY_NUM:-:99}"
export DISPLAY="${DISPLAY:-$DISPLAY_NUM}"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

# ------------------------------------------------------------ watermark (burned in by the encoder)
#
# ONE implementation: FFmpeg draws the watermark (drawtext / overlay), so it is in the encoded video for every source
# type. The dashboard (app/overlay.py encoder_env()) validates the operator's settings and writes them as WATERMARK_*
# keys to current-source.env; this builds the filter from those values, re-validating every one of them here, because
# this is the last stop before FFmpeg. Called by stream.sh (the broadcast) and test-recording.sh (the local proof), so
# both always draw exactly the same thing.
#
# The filter graph is fixed when FFmpeg starts. A setting changed while a broadcast is running therefore applies to
# the NEXT stream; nothing here (or in the dashboard) restarts a running encoder.
#
#   build_watermark_filter <image_input_index> [out_w out_h]
#       out_w/out_h = the encoded frame size when the caller knows it (capture sources: RESOLUTION). Sizes, margins and
#       decorations are defined on a 1080-line reference frame and scaled to it, so the watermark looks the same at
#       720p and 1080p and matches the dashboard preview. Unknown (direct sources) = FFmpeg scales at run time.
#
# Results:  WATERMARK_FILTER            "" = draw nothing
#           WATERMARK_EXTRA_INPUT_ARGS  the image as an extra -i (image mode only)
#           WATERMARK_STATE             off | on | error
#           WATERMARK_REASON            why, when off/error (also logged) - never a secret
#           WATERMARK_FONT_FILE         the font file actually handed to FFmpeg (text mode)
# An unusable setting (missing font, bad image, empty text) is an ERROR that is reported, never a silent fallback to
# some other font: the stream still starts, without a watermark, and the dashboard shows why.
FONTS_DIR="${WATERMARK_FONTS_DIR:-$APP_DIR/scripts/fonts}"
WATERMARK_IMAGE_DIR="$APP_DIR/watermarks"
WATERMARK_STATUS_FILE="$LOG_DIR/watermark.status"
WATERMARK_FILTER=""; WATERMARK_EXTRA_INPUT_ARGS=(); WATERMARK_STATE="off"; WATERMARK_REASON=""; WATERMARK_FONT_FILE=""

_wm_int() {          # value default min max -> a clamped integer (default when not a plain integer)
  local v="$1"
  [[ "$v" =~ ^-?[0-9]{1,6}$ ]] || v="$2"
  if [[ "$v" == -* ]]; then v=$(( -(10#${v#-}) )); else v=$((10#$v)); fi
  (( v < $3 )) && v=$3
  (( v > $4 )) && v=$4
  echo "$v"
}

_wm_pct() {          # value default -> a number 0..100 with at most 2 decimals
  awk -v v="$1" -v d="$2" 'BEGIN { if (v !~ /^[0-9]+(\.[0-9]+)?$/) v = d; if (v > 100) v = 100; printf "%.2f", v }'
}

_wm_color() {        # "#RRGGBB" -> "0xRRGGBB" (default when malformed)
  local c="${1#\#}"
  [[ "$c" =~ ^[0-9A-Fa-f]{6}$ ]] || c="${2#\#}"
  echo "0x${c^^}"
}

_watermark_alpha() { # opacity 0-100 -> "0.00".."1.00" (fully opaque when unparseable)
  awk -v o="${1:-100}" 'BEGIN {
    if (o !~ /^[0-9]+(\.[0-9]+)?$/) o = 100
    if (o < 0) o = 0; if (o > 100) o = 100
    printf "%.2f", o / 100
  }'
}

_wm_px() {           # reference pixels (1080-line frame) -> pixels in the real frame: a number, or an expression
  if [[ -n "$_WM_OUT_H" ]]; then echo $(( $1 * _WM_OUT_H / 1080 )); else echo "main_h*$1/1080"; fi
}

_wm_px_int() {       # same, for options that only take a plain integer (unknown frame size = reference size)
  if [[ -n "$_WM_OUT_H" ]]; then echo $(( $1 * _WM_OUT_H / 1080 )); else echo "$1"; fi
}

# Position as FFmpeg x=/y= expressions. $1 = text|image, $2 = extra inset in real pixels (the box padding).
# Named anchors sit `margin` away from the edge they name; "custom" puts the point (POS_X%, POS_Y%) of the frame on the
# left/centre/right (top/middle/bottom) of the watermark depending on which third of the frame it is in - the same rule
# the dashboard preview uses, so a dragged position lands where it was dropped.
# Multi-line text is one drawtext per line: $3 = this line's index, $4 = number of lines, $5 = distance between line
# tops. The block of lines is positioned as a whole; each line is aligned (left/centre/right) on its own.
_watermark_xy() {
  local kind="$1" pad="${2:-0}" idx="${3:-0}" count="${4:-1}" pitch="${5:-0}"
  local anchor="${WATERMARK_ANCHOR:-bottom-right}" ow oh yoff=""
  if [[ "$kind" == "image" ]]; then ow="overlay_w"; oh="overlay_h"; else ow="text_w"; oh="text_h"; fi
  if (( count > 1 )); then
    oh="(text_h+$((count - 1))*${pitch})"
    (( idx > 0 )) && yoff="+${idx}*${pitch}"
  fi
  local mx my
  mx="$(_wm_px "$(_wm_int "${WATERMARK_MARGIN_X:-24}" 24 0 2000)")"
  my="$(_wm_px "$(_wm_int "${WATERMARK_MARGIN_Y:-24}" 24 0 2000)")"
  local xl="${mx}+${pad}" xc="(main_w-${ow})/2" xr="main_w-${ow}-${mx}-${pad}"
  local yt="${my}+${pad}${yoff}" ym="(main_h-${oh})/2${yoff}" yb="main_h-${oh}-${my}-${pad}${yoff}"
  case "$anchor" in
    top-left)      echo "x=${xl}:y=${yt}" ;;
    top-center)    echo "x=${xc}:y=${yt}" ;;
    top-right)     echo "x=${xr}:y=${yt}" ;;
    middle-left)   echo "x=${xl}:y=${ym}" ;;
    center)        echo "x=${xc}:y=${ym}" ;;
    middle-right)  echo "x=${xr}:y=${ym}" ;;
    bottom-left)   echo "x=${xl}:y=${yb}" ;;
    bottom-center) echo "x=${xc}:y=${yb}" ;;
    custom)
      local px py fx fy
      px="$(_wm_pct "${WATERMARK_POS_X:-}" 95)"; py="$(_wm_pct "${WATERMARK_POS_Y:-}" 95)"
      fx="$(awk -v p="$px" 'BEGIN { print (p > 70) ? "1" : (p > 30) ? "0.5" : "0" }')"
      fy="$(awk -v p="$py" 'BEGIN { print (p > 70) ? "1" : (p > 30) ? "0.5" : "0" }')"
      echo "x=main_w*${px}/100-(${ow}+2*${pad})*${fx}+${pad}:y=main_h*${py}/100-(${oh}+2*${pad})*${fy}+${pad}${yoff}" ;;
    *)             echo "x=${xr}:y=${yb}" ;;                       # bottom-right, and anything unknown
  esac
}

_wm_fail() {
  WATERMARK_STATE="error"; WATERMARK_REASON="$1"; WATERMARK_FILTER=""; WATERMARK_EXTRA_INPUT_ARGS=()
  log "Watermark NOT applied: $1"
}

build_watermark_filter() {
  local watermark_input_idx="${1:-1}"
  _WM_OUT_H=""
  if [[ "${3:-}" =~ ^[0-9]{2,5}$ ]] && (( 10#$3 >= 90 )); then _WM_OUT_H=$((10#$3)); fi
  WATERMARK_FILTER=""; WATERMARK_EXTRA_INPUT_ARGS=(); WATERMARK_STATE="off"; WATERMARK_REASON=""; WATERMARK_FONT_FILE=""

  if [[ "${WATERMARK_ENABLED:-0}" != "1" ]]; then
    WATERMARK_REASON="turned off"
    return 0
  fi

  local safe_path='^[A-Za-z0-9_./-]+$'

  if [[ "${WATERMARK_MODE:-text}" == "image" ]]; then
    local img="${WATERMARK_IMAGE_PATH:-}"
    if [[ -z "$img" ]]; then _wm_fail "image watermark is on but no image has been uploaded"; return 0; fi
    # Only an image the dashboard stored (write-watermark-image.sh) may be handed to FFmpeg as an input.
    if [[ "$img" != "$WATERMARK_IMAGE_DIR"/logo.* || "$img" == *..* || ! "$img" =~ $safe_path ]]; then
      _wm_fail "image path is not inside the watermark folder"; return 0
    fi
    if [[ ! -r "$img" || ! -s "$img" ]]; then _wm_fail "image file is missing or unreadable (${img##*/})"; return 0; fi
    local scale_pct alpha xy
    scale_pct="$(awk -v v="${WATERMARK_SIZE:-15}" 'BEGIN { if (v !~ /^[0-9]+(\.[0-9]+)?$/) v = 15; if (v < 1) v = 1; if (v > 100) v = 100; printf "%.2f", v }')"
    alpha="$(_watermark_alpha "${WATERMARK_OPACITY:-100}")"
    xy="$(_watermark_xy image 0)"
    WATERMARK_EXTRA_INPUT_ARGS=(-i "$img")
    # The image is sized relative to the MAIN video width, so the same logo looks the same at 720p and 1080p.
    # Explicit [vout] label + the caller's own -map: filter_complex does not auto-select an output stream.
    # Both forms reference [0:v] exactly once (stream.sh redirects it to the framed video).
    if [[ "${2:-}" =~ ^[0-9]{2,5}$ ]] && (( 10#$2 >= 160 )); then
      # Frame size known (every capture source): a plain scale to the computed pixel width. Works on every FFmpeg.
      local img_w; img_w="$(awk -v w="$((10#$2))" -v p="$scale_pct" 'BEGIN { v = int(w * p / 100 / 2 + 0.5) * 2; if (v < 2) v = 2; print v }')"
      WATERMARK_FILTER="[${watermark_input_idx}:v]scale=w=${img_w}:h=-2,format=rgba,colorchannelmixer=aa=${alpha}[wma];[0:v][wma]overlay=${xy}[vout]"
    else
      # Frame size unknown (direct sources): scale2ref measures it at run time. Present in FFmpeg 4-8 (6.1 on Ubuntu
      # 24.04); deprecated since 7.0 in favour of scale's reference input, which 6.1 does not have.
      WATERMARK_FILTER="[${watermark_input_idx}:v][0:v]scale2ref=w=iw*${scale_pct}/100:h=ow/mdar[wm][base];[wm]format=rgba,colorchannelmixer=aa=${alpha}[wma];[base][wma]overlay=${xy}[vout]"
    fi
    WATERMARK_STATE="on"
    return 0
  fi

  # ---- text mode
  local text=""
  if [[ -n "${WATERMARK_TEXT_B64:-}" ]]; then
    text="$(printf '%s' "$WATERMARK_TEXT_B64" | base64 -d 2>/dev/null)" || text=""
  fi
  [[ -z "$text" ]] && text="${WATERMARK_TEXT:-}"
  if [[ -z "${text//[[:space:]]/}" ]]; then _wm_fail "text watermark is on but the text is empty"; return 0; fi

  local font_key="${WATERMARK_FONT:-inter}" font_file=""
  if [[ ! "$font_key" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]]; then _wm_fail "font name is not valid"; return 0; fi
  font_file="$FONTS_DIR/${font_key}.ttf"
  if [[ ! -f "$font_file" ]]; then
    case "$font_key" in                                             # file names used before the font catalogue existed
      inter)          [[ -f "$FONTS_DIR/Inter-Variable.ttf" ]] && font_file="$FONTS_DIR/Inter-Variable.ttf" ;;
      jetbrains-mono) [[ -f "$FONTS_DIR/JetBrainsMono.ttf" ]] && font_file="$FONTS_DIR/JetBrainsMono.ttf" ;;
    esac
  fi
  if [[ ! -r "$font_file" || ! -s "$font_file" ]]; then
    _wm_fail "font \"$font_key\" is not installed on this server (${font_key}.ttf missing)"; return 0
  fi
  # A real TrueType/OpenType file? (FFmpeg would otherwise refuse to start the whole encode over a damaged font.)
  local magic; magic="$(head -c 4 "$font_file" | od -An -tx1 | tr -d ' \n')"
  case "$magic" in
    00010000|4f54544f|74727565) ;;
    *) _wm_fail "font \"$font_key\" is not a usable TrueType/OpenType file"; return 0 ;;
  esac

  local textbase; textbase="$LOG_DIR/watermark-text.$(basename "${0:-stream}" .sh)"
  if [[ ! "$font_file" =~ $safe_path || ! "$textbase" =~ $safe_path ]]; then
    _wm_fail "the install path contains characters an FFmpeg filter cannot take"; return 0
  fi
  # One line of text per file and per drawtext filter. (A line break inside one drawtext is drawn as an empty box by
  # FFmpeg's text shaping with any font that has no glyph for it - which is most of them.)
  local -a lines=()
  mapfile -t lines <<< "${text//$'\r'/}"
  lines=("${lines[@]:0:6}")
  while (( ${#lines[@]} > 0 )) && [[ -z "${lines[-1]//[[:space:]]/}" ]]; do unset 'lines[-1]'; done
  local line_count=${#lines[@]}

  local size_ref size color alpha
  size_ref="$(_wm_int "${WATERMARK_SIZE:-42}" 42 8 400)"
  size="$(_wm_px "$size_ref")"
  color="$(_wm_color "${WATERMARK_COLOR:-}" FFFFFF)"
  alpha="$(_watermark_alpha "${WATERMARK_OPACITY:-100}")"

  local extras="" pad=0
  local outline; outline="$(_wm_int "${WATERMARK_OUTLINE_WIDTH:-0}" 0 0 40)"
  if (( outline > 0 )); then
    outline="$(_wm_px_int "$outline")"; (( outline < 1 )) && outline=1
    extras+=":borderw=${outline}:bordercolor=$(_wm_color "${WATERMARK_OUTLINE_COLOR:-}" 000000)@${alpha}"
  fi
  if [[ "${WATERMARK_SHADOW:-0}" == "1" ]]; then
    local sx sy
    sx="$(_wm_px_int "$(_wm_int "${WATERMARK_SHADOW_X:-2}" 2 -60 60)")"
    sy="$(_wm_px_int "$(_wm_int "${WATERMARK_SHADOW_Y:-4}" 4 -60 60)")"
    extras+=":shadowx=${sx}:shadowy=${sy}:shadowcolor=$(_wm_color "${WATERMARK_SHADOW_COLOR:-}" 000000)@${alpha}"
  fi
  if [[ "${WATERMARK_BOX:-0}" == "1" ]]; then
    pad="$(_wm_px_int "$(_wm_int "${WATERMARK_BOX_PADDING:-16}" 16 0 200)")"
    extras+=":box=1:boxcolor=$(_wm_color "${WATERMARK_BOX_COLOR:-}" 000000)@$(_watermark_alpha "${WATERMARK_BOX_OPACITY:-75}"):boxborderw=${pad}"
  fi

  # Distance between line tops: 1.25 x the font size; with a background box each line gets its own strip, so the
  # padding is added too and the strips never overlap (overlapping translucent boxes would show as darker bands).
  local pitch; pitch="$(_wm_px $(( size_ref * 5 / 4 )))"
  (( pad > 0 )) && pitch="(${pitch}+2*${pad})"

  rm -f "$textbase".*.txt "$textbase.txt" 2>/dev/null
  local i textfile chain=""
  for (( i = 0; i < line_count; i++ )); do
    [[ -z "${lines[i]//[[:space:]]/}" ]] && continue                # a blank line only leaves its gap
    textfile="$textbase.$i.txt"
    # textfile= (not text=): arbitrary operator text never has to survive the filter-string escaping rules.
    # expansion=none: a % or \ in the text is drawn as typed, not interpreted.
    if ! printf '%s' "${lines[i]}" > "$textfile"; then _wm_fail "could not write the watermark text file"; return 0; fi
    chain+="${chain:+,}drawtext=fontfile=${font_file}:textfile=${textfile}:expansion=none:fontsize=${size}:fontcolor=${color}@${alpha}${extras}:$(_watermark_xy text "$pad" "$i" "$line_count" "$pitch")"
  done

  WATERMARK_FONT_FILE="$font_file"
  WATERMARK_FILTER="$chain"
  WATERMARK_STATE="on"
}

# What THIS encoder start did with the watermark, for the dashboard (app/overlay.py live_status()). Written by stream.sh
# only. WATERMARK_CONFIG_HASH is the dashboard's own fingerprint of the settings it wrote, echoed back so it can tell
# "the live stream has these settings" from "these are queued for the next stream". No secrets.
write_watermark_status() {
  local tmp="$WATERMARK_STATUS_FILE.$$"
  {
    echo "STATE=${WATERMARK_STATE:-off}"
    echo "REASON=${WATERMARK_REASON:-}"
    echo "MODE=${WATERMARK_MODE:-text}"
    echo "FONT=${WATERMARK_FONT:-}"
    echo "FONT_FILE=${WATERMARK_FONT_FILE##*/}"
    echo "HASH=${WATERMARK_CONFIG_HASH:-}"
    echo "PID=$$"
    echo "STARTED=$(date +%s)"
  } > "$tmp" 2>/dev/null
  if ! mv -f "$tmp" "$WATERMARK_STATUS_FILE" 2>/dev/null; then rm -f "$tmp" 2>/dev/null; fi
  return 0
}

wait_for_x() {
  local tries=0
  until xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; do
    tries=$((tries + 1))
    if [[ $tries -gt 60 ]]; then
      log "X display $DISPLAY never came up after 30s"
      exit 1
    fi
    sleep 0.5
  done
}

# "Meeting only" encoder filter -> $MEETING_ONLY_FILTER (empty when off).
# Args: out_w out_h fit(fit|fill) trim_top trim_bottom [region x:y:w:h].
#
# Zoom keeps drawing its own chrome around the video (top bar with the
# meeting title/REC/Sign in, the bottom toolbar, the side-panel arrow),
# and neither fullscreen nor parking the pointer reliably removes all of
# it, so the top/bottom bands are cut out here - the one place that is
# guaranteed to be hidden in the output. What is left is the meeting
# area, and it is never stretched:
#   fit  - the whole meeting area, scaled to fit, letterboxed with the
#          smallest bars that preserves its shape;
#   fill - the smallest centred crop that makes the meeting area exactly
#          the output shape (no bars), then scaled up.
# `region` (optional) is a MEASURED meeting area (app/framing.py: the
# part of the screen that is not pure-black border). When it is sane it
# replaces the fixed top/bottom band; when it is missing or implausible
# (smaller than a third of the frame, outside it, not numbers) the fixed
# trims are used - a bad measurement can never crop real content away.
# All sizes are rounded to even numbers (yuv420p needs that).
build_meeting_only_filter() {
  local out_w="$1" out_h="$2" fit="${3:-fit}" top="${4:-56}" bottom="${5:-96}" region="${6:-}"
  MEETING_ONLY_FILTER=""
  [[ "$out_w" =~ ^[0-9]+$ && "$out_h" =~ ^[0-9]+$ ]] || return 0
  [[ "$top" =~ ^[0-9]+$ ]] || top=56
  [[ "$bottom" =~ ^[0-9]+$ ]] || bottom=96
  # Never trim away more than a sixth of the frame per edge, whatever was configured.
  local max=$((out_h / 6))
  (( top > max )) && top=$max
  (( bottom > max )) && bottom=$max
  top=$((top / 2 * 2)); bottom=$((bottom / 2 * 2))
  local rx=0 ry=$top rw=$out_w rh=$((out_h - top - bottom))
  if [[ "$region" =~ ^([0-9]+):([0-9]+):([0-9]+):([0-9]+)$ ]]; then
    local mx=$((BASH_REMATCH[1] / 2 * 2)) my=$((BASH_REMATCH[2] / 2 * 2))
    local mw=$((BASH_REMATCH[3] / 2 * 2)) mh=$((BASH_REMATCH[4] / 2 * 2))
    if (( mw * 3 >= out_w && mh * 3 >= out_h && mx + mw <= out_w && my + mh <= out_h )); then
      rx=$mx; ry=$my; rw=$mw; rh=$mh
    fi
  fi
  rh=$((rh / 2 * 2))
  if [[ "$fit" == "fill" ]]; then
    local cw ch
    if (( rw * out_h > rh * out_w )); then   # meeting area wider than output: trim the sides
      ch=$rh; cw=$((rh * out_w / out_h))
    else                                     # taller: trim top/bottom
      cw=$rw; ch=$((rw * out_h / out_w))
    fi
    cw=$((cw / 2 * 2)); ch=$((ch / 2 * 2))
    local cx=$(( rx + (rw - cw) / 2 / 2 * 2 )) cy=$(( ry + (rh - ch) / 2 / 2 * 2 ))
    MEETING_ONLY_FILTER="crop=${cw}:${ch}:${cx}:${cy},scale=${out_w}:${out_h}:flags=lanczos,setsar=1"
  else
    MEETING_ONLY_FILTER="crop=${rw}:${rh}:${rx}:${ry},scale=${out_w}:${out_h}:force_original_aspect_ratio=decrease:flags=lanczos,pad=${out_w}:${out_h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
  fi
}

# "Fullscreen Live" framing (dashboard app/fullscreen_live.py decides the box; its fullscreen_filter() is this
# function's Python twin, pinned to identical strings by tests/test_fullscreen_live.py). The box is the smallest
# output-shaped area containing all non-black content, so the filter only ever removes black and scales uniformly.
# An unusable box falls back to the whole capture - this can never crop content. "" = no filter (native 1:1).
#   args: capture_w capture_h out_w out_h box(x:y:w:h)  ->  $FULLSCREEN_LIVE_FILTER
build_fullscreen_live_filter() {
  local W="$1" H="$2" ow="$3" oh="$4" box="${5:-}" x=0 y=0 w h crop=""
  FULLSCREEN_LIVE_FILTER=""
  [[ "$W" =~ ^[0-9]+$ && "$H" =~ ^[0-9]+$ && "$ow" =~ ^[0-9]+$ && "$oh" =~ ^[0-9]+$ ]] || return 0
  w=$W; h=$H
  if [[ "$box" =~ ^([0-9]+):([0-9]+):([0-9]+):([0-9]+)$ ]]; then
    local bx=$((10#${BASH_REMATCH[1]})) by=$((10#${BASH_REMATCH[2]})) bw=$((10#${BASH_REMATCH[3]})) bh=$((10#${BASH_REMATCH[4]}))
    if (( bw > 0 && bh > 0 && bx + bw <= W && by + bh <= H )); then
      x=$bx; y=$by; w=$bw; h=$bh
    fi
  fi
  if (( x != 0 || y != 0 || w != W || h != H )); then
    crop="crop=${w}:${h}:${x}:${y},"
  elif (( W == ow && H == oh )); then
    return 0
  fi
  local d=$(( w * oh - h * ow )); (( d < 0 )) && d=$(( -d ))
  if (( d * 1000 <= h * ow )); then                  # same shape to within 0.1% (sub-pixel): one uniform scale
    FULLSCREEN_LIVE_FILTER="${crop}scale=${ow}:${oh}:flags=lanczos,setsar=1"
  else
    FULLSCREEN_LIVE_FILTER="${crop}scale=${ow}:${oh}:force_original_aspect_ratio=decrease:flags=lanczos,pad=${ow}:${oh}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
  fi
}

# "Clean Broadcast" filter check (dashboard app/clean_broadcast.py builds ZOOM_CLEAN_FILTER from frames it measured;
# safe_filter() there is this function's twin, pinned by tests/test_clean_broadcast.py). Only the characters and
# filters the planner emits are accepted, so nothing that reads files, the network or commands can arrive via the env
# file. Empty is valid (native capture, no filter).   return 0 = usable.
clean_filter_ok() {
  local vf="$1" bad='[^]A-Za-z0-9=:,;._[-]' low
  [[ "$vf" =~ $bad ]] && return 1
  low="${vf,,}"
  local f
  for f in movie amovie file sendcmd zmq drawtext subtitles 'ass=' lavfi frei0r ladspa lv2 opencl vulkan http; do
    [[ "$low" == *"$f"* ]] && return 1
  done
  return 0
}

# Capture size + framing filter for the x11grab capture, shared by stream.sh and test-recording.sh so a local test
# recording is framed exactly like the broadcast. Reads SOURCE_TYPE and the ZOOM_* framing keys of current-source.env.
#   args: output_size(WxH)  ->  $CAPTURE_SIZE, $CROP_FILTER ("" = none), $FRAMING_NOTE (for the log),
#                               $COLOR_TAG_ARGS (encoder colour tags matching the filter's BT.709 conversion, or empty)
build_capture_framing() {
  local res="${1:-1920x1080}"
  CAPTURE_SIZE="$res"; CROP_FILTER=""; FRAMING_NOTE=""; COLOR_TAG_ARGS=()
  # The REAL screen size, asked from the X server. x11grab refuses to start when asked for more than the screen has
  # (ffmpeg: Capture area outside the screen size / Error opening input: Invalid argument), so the capture size never
  # comes from RESOLUTION or a stored measurement alone. Empty = it could not be asked: configured sizes are used.
  local actual=""
  if command -v xdpyinfo >/dev/null 2>&1; then
    actual="$(xdpyinfo -display "${DISPLAY:-:99}" 2>/dev/null | awk '/dimensions:/{print $2; exit}' || true)"
  fi
  [[ "$actual" =~ ^[0-9]+x[0-9]+$ ]] || actual=""
  _bcf_whole() {   # capture the whole real screen and scale it to the output; $1 = the reason, for the log
    CAPTURE_SIZE="$actual"
    build_fullscreen_live_filter "${actual%x*}" "${actual#*x}" "${res%x*}" "${res#*x}" ""
    CROP_FILTER="$FULLSCREEN_LIVE_FILTER"
    FRAMING_NOTE="$1: ${CROP_FILTER:-no filter}"
  }
  if [[ "${SOURCE_TYPE:-}" == "zoom" && "${ZOOM_CLEAN_BROADCAST:-0}" == "1" ]]; then
    # Clean Broadcast: the desktop the dashboard measured + ITS filter (Zoom UI cut away, one uniform scale). The same
    # string drives the Live Studio preview. A rejected filter falls back to fitting the whole desktop, never a crop.
    [[ "${ZOOM_CLEAN_CAPTURE:-}" =~ ^[0-9]+x[0-9]+$ ]] && CAPTURE_SIZE="$ZOOM_CLEAN_CAPTURE"
    if [[ -n "$actual" && "$actual" != "$CAPTURE_SIZE" ]]; then
      _bcf_whole "Clean Broadcast was measured on ${ZOOM_CLEAN_CAPTURE:-?}, the screen is now ${actual} - whole screen fitted instead (re-measure in the dashboard)"
    elif clean_filter_ok "${ZOOM_CLEAN_FILTER:-}"; then
      CROP_FILTER="${ZOOM_CLEAN_FILTER:-}"
      FRAMING_NOTE="Clean Broadcast (capture ${CAPTURE_SIZE}): ${CROP_FILTER:-no filter, native ${CAPTURE_SIZE}}"
    else
      build_fullscreen_live_filter "${CAPTURE_SIZE%x*}" "${CAPTURE_SIZE#*x}" "${res%x*}" "${res#*x}" ""
      CROP_FILTER="$FULLSCREEN_LIVE_FILTER"
      FRAMING_NOTE="Clean Broadcast filter REJECTED (unexpected characters) - whole desktop fitted instead: ${CROP_FILTER:-no filter}"
    fi
  elif [[ "${SOURCE_TYPE:-}" == "zoom" && "${ZOOM_FULLSCREEN_LIVE:-0}" == "1" ]]; then
    # Fullscreen Live: the WHOLE desktop the dashboard measured, then the smallest 16:9 box around all content.
    [[ "${ZOOM_CAPTURE_SIZE:-}" =~ ^[0-9]+x[0-9]+$ ]] && CAPTURE_SIZE="$ZOOM_CAPTURE_SIZE"
    if [[ -n "$actual" && "$actual" != "$CAPTURE_SIZE" ]]; then
      _bcf_whole "Fullscreen Live was measured on ${CAPTURE_SIZE}, the screen is ${actual} - whole screen fitted instead (re-measure in the dashboard)"
    else
      build_fullscreen_live_filter "${CAPTURE_SIZE%x*}" "${CAPTURE_SIZE#*x}" "${res%x*}" "${res#*x}" "${ZOOM_FRAMING_BOX:-}"
      CROP_FILTER="$FULLSCREEN_LIVE_FILTER"
      FRAMING_NOTE="Fullscreen Live (box ${ZOOM_FRAMING_BOX:-whole screen}): ${CROP_FILTER:-no filter, native ${CAPTURE_SIZE}}"
    fi
  elif [[ "${SOURCE_TYPE:-}" == "zoom" && "${ZOOM_MEETING_ONLY:-0}" == "1" && ( -z "$actual" || "$actual" == "$res" ) ]]; then
    # Meeting only: cut Zoom's top/bottom chrome, fit or fill the rest. Its trims assume a screen of the output size.
    build_meeting_only_filter "${res%x*}" "${res#*x}" "${ZOOM_MEETING_ONLY_FIT:-fit}" "${ZOOM_MEETING_ONLY_TRIM_TOP:-56}" "${ZOOM_MEETING_ONLY_TRIM_BOTTOM:-96}" "${ZOOM_MEETING_ONLY_REGION:-}"
    CROP_FILTER="$MEETING_ONLY_FILTER"
    FRAMING_NOTE="Meeting-only (${ZOOM_MEETING_ONLY_FIT:-fit}${ZOOM_MEETING_ONLY_REGION:+, measured area ${ZOOM_MEETING_ONLY_REGION}}): ${CROP_FILTER}"
  elif [[ -n "$actual" && "$actual" != "$res" ]]; then
    # No framing mode, and the screen differs from the output (e.g. the default 1280x720 screen + a 1080p preset).
    _bcf_whole "The virtual screen is ${actual}, the output is ${res} - the whole screen is scaled to the output"
  fi
  # Last line of defence: never ask x11grab for more than the screen has.
  if [[ -n "$actual" ]] && (( ${CAPTURE_SIZE%x*} > ${actual%x*} || ${CAPTURE_SIZE#*x} > ${actual#*x} )); then
    _bcf_whole "The capture area ${CAPTURE_SIZE} was larger than the ${actual} screen - whole screen fitted instead"
  fi
  unset -f _bcf_whole
  # Clean Broadcast/Output filters end in an explicit BT.709 (limited range) conversion: tag the stream to match.
  if [[ "$CROP_FILTER" == *"out_color_matrix=bt709"* ]]; then
    COLOR_TAG_ARGS=(-colorspace bt709 -color_primaries bt709 -color_trc bt709 -color_range tv)
  fi
  return 0
}

# ------------------------------------------------------------ broadcast authorization + secret-safe logging
#
# The encoder may only start when the dashboard has just written a ONE-TIME permission into current-source.env
# (BROADCAST_AUTH_NONCE / _EXPIRES, see dashboard app/broadcast.py) after an operator confirmed Go Live. This is the
# last line of defence: it stops a direct `systemctl --user start ffmpeg-stream`, systemd's own Restart= policy after a
# crash, a boot, a stale dashboard and every code path that might one day call "start" from starting a broadcast.
# The permission is burned (blanked on disk, under a lock) before ffmpeg starts, so it works exactly once.
#   return 0 = authorized (and burned), 1 = refuse.
broadcast_gate() {
  local nonce="${BROADCAST_AUTH_NONCE:-}" exp="${BROADCAST_AUTH_EXPIRES:-0}" now lock tries=0
  now="$(date +%s)"
  [[ -n "$nonce" && "$exp" =~ ^[0-9]+$ ]] || return 1
  (( now <= exp )) || return 1
  [[ -f "$SOURCE_ENV_FILE" ]] || return 1
  # mkdir is atomic everywhere (no flock dependency): whoever creates the directory holds the lock.
  lock="$SOURCE_ENV_FILE.lockdir"
  until mkdir "$lock" 2>/dev/null; do
    tries=$((tries + 1))
    (( tries > 50 )) && return 1                                   # 5 s: refuse rather than start unauthorized
    if [[ -d "$lock" && -n "$(find "$lock" -maxdepth 0 -mmin +1 2>/dev/null)" ]]; then rmdir "$lock" 2>/dev/null; fi
    sleep 0.1
  done
  local ok=1 tmp="$SOURCE_ENV_FILE.burn.$$"
  # Re-read under the lock: if another instance already burned this nonce, refuse (single use, no double start).
  if grep -q "^BROADCAST_AUTH_NONCE=\"$nonce\"\$" "$SOURCE_ENV_FILE"; then
    if sed -E 's/^(BROADCAST_AUTH_(ID|NONCE|EXPIRES))=.*/\1=""/' "$SOURCE_ENV_FILE" > "$tmp" \
       && chmod 644 "$tmp" && mv -f "$tmp" "$SOURCE_ENV_FILE"; then
      ok=0
    fi
  fi
  rm -f "$tmp" 2>/dev/null
  rmdir "$lock" 2>/dev/null
  (( ok == 0 )) || return 1
  unset BROADCAST_AUTH_NONCE BROADCAST_AUTH_EXPIRES BROADCAST_AUTH_ID
  return 0
}

# Filter for ffmpeg's log output: masks the secret path part of any rtmp(s):// destination (the YouTube stream key).
# Never prints or stores the real value.
# ffmpeg ends its live progress line with \r, not \n, so a line-based filter (sed -u) would hold every progress update
# until the next newline - often until the encoder exits - and ffmpeg.log would look frozen while the stream is healthy
# (no fps/bitrate/dropped-frame metrics on the dashboard). perl (perl-base is part of every Ubuntu install) splits on \r
# instead: each progress update, and any \n lines before it, is written out as soon as it arrives, otherwise unchanged.
redact_stream_secrets() {
  if command -v perl >/dev/null 2>&1; then
    perl -e '$| = 1; $/ = "\r"; while (<STDIN>) { s#(rtmps?://[^/\s\x27"]+/[^/\s\x27"]+/)[^\s\x27"]+#${1}<redacted>#g; print }'
  else
    # fallback: turn \r into \n so sed sees whole lines (progress then lands one per line; the dashboard parses both)
    stdbuf -o0 tr '\r' '\n' | sed -u -E "s#(rtmps?://[^/[:space:]'\"]+/[^/[:space:]'\"]+/)[^[:space:]'\"]+#\1<redacted>#g"
  fi
}
