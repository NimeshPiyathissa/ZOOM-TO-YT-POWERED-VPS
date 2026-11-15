#!/usr/bin/env bash
# One-frame proof that the encoder's screen capture works with the CURRENT settings - without starting a stream.
#
# It builds the capture exactly the way stream.sh does (same lib.sh build_capture_framing: capture size taken from the
# real screen, same framing/scale filter), grabs ONE frame from the virtual screen into nothing, and checks that the
# audio source the encoder records from exists. Nothing is sent anywhere, no file is written, and it is safe to run
# while a broadcast is live (a second reader of the screen does not disturb the encoder).
#
# Run as the service user (doctor.sh and the installer's health check do this):
#   sudo -u zoombot XDG_RUNTIME_DIR=/run/user/$(id -u zoombot) /home/zoombot/zoom-stream/scripts/capture-check.sh
# Exit status: 0 = the capture works (or the active source needs none), 1 = it does not; the reason is printed.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1
source ./lib.sh >/dev/null

FFMPEG="${ZS_FFMPEG:-/usr/bin/ffmpeg}"          # test hook
RESOLUTION="${RESOLUTION:-1920x1080}"

if [[ "$SOURCE_TYPE" == "direct" ]]; then
  echo "capture check: skipped - the active source is a direct stream (no screen capture)"
  exit 0
fi

screen=""
if command -v xdpyinfo >/dev/null 2>&1; then
  screen="$(xdpyinfo -display "$DISPLAY" 2>/dev/null | awk '/dimensions:/{print $2; exit}' || true)"
fi
if [[ ! "$screen" =~ ^[0-9]+x[0-9]+$ ]]; then
  echo "capture check: FAILED - the virtual screen ${DISPLAY} does not answer (is xvfb running?)"
  exit 1
fi

build_capture_framing "$RESOLUTION"

args=(-f x11grab -video_size "$CAPTURE_SIZE" -framerate 5 -draw_mouse 0 -i "$DISPLAY" -frames:v 1)
if [[ -n "$CROP_FILTER" ]]; then args+=(-vf "$CROP_FILTER"); fi
if ! out="$(timeout 30 "$FFMPEG" -hide_banner -loglevel error -nostdin "${args[@]}" -f null - 2>&1)"; then
  echo "capture check: FAILED - ffmpeg cannot capture ${CAPTURE_SIZE} from the ${screen} screen (output ${RESOLUTION})"
  printf '%s\n' "$out" | tail -n 3 | sed 's/^/  ffmpeg: /'
  exit 1
fi

if ! pactl list short sources 2>/dev/null | awk '{print $2}' | grep -qx "zoom_out.monitor"; then
  echo "capture check: FAILED - the audio source zoom_out.monitor does not exist (is audio-setup running?)"
  exit 1
fi

note=""
if [[ "$CAPTURE_SIZE" != "$RESOLUTION" ]]; then note=", scaled to the output"; fi
if (( ${RESOLUTION%x*} * ${RESOLUTION#*x} > ${screen%x*} * ${screen#*x} )); then
  note="${note} - the output is LARGER than the screen: an enlarged copy, not a sharper picture (docs/configuration.md, Virtual screen size)"
fi
echo "capture check: OK - screen ${screen}, capture ${CAPTURE_SIZE}, output ${RESOLUTION}${note}; audio source zoom_out.monitor present"
