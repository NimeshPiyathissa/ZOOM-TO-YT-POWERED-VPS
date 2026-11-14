#!/usr/bin/env bash
# Streaming profiles for a FRESH install (sourced by install.sh; also runnable for a dry look:
#   bash scripts/stream-profiles.sh 720p30 [VIDEO_BITRATE_KBPS]).
#
# These are conservative STARTING settings, not a performance guarantee. What a server sustains depends on its CPU,
# the Zoom/Chrome rendering load (often the largest consumer), the encoder preset and the source. Measure on the
# real server (scripts/measure-cpu.sh, ffmpeg speed= in monitor.sh) before raising anything.
#
# An existing installation is never touched by this file: install.sh only writes these values when it creates .env
# on a fresh install. Upgrades keep the operator's RESOLUTION / FPS / bitrates / X264_PRESET exactly as they are.
#
#   720p30   (default) 1280x720, 30 fps, 3000 kbps video, 160 kbps audio, x264 ultrafast
#   1080p30  (advanced, must be chosen explicitly with --profile 1080p30) 1920x1080, 30 fps, 6000 kbps, 192 kbps,
#            x264 superfast. Not proven on 4 vCPU with a real Zoom meeting - verify on your server first.

DEFAULT_STREAM_PROFILE="720p30"
STREAM_PROFILES="720p30 1080p30"
VIDEO_BITRATE_MIN_KBPS=1000          # same range the dashboard enforces (config.VIDEO_BITRATE_RANGE_KBPS)
VIDEO_BITRATE_MAX_KBPS=8000

# stream_profile_env PROFILE [VIDEO_BITRATE_KBPS] -> prints the streaming lines of a fresh .env; returns 1 (and prints
# a reason on stderr) for an unknown profile or an out-of-range bitrate. Prints nothing else, changes nothing.
stream_profile_env() {
  local profile="${1:-$DEFAULT_STREAM_PROFILE}" bitrate="${2:-}" res fps vbr abr preset
  case "$profile" in
    720p30)  res=1280x720;  fps=30; vbr=3000; abr=160; preset=ultrafast ;;
    1080p30) res=1920x1080; fps=30; vbr=6000; abr=192; preset=superfast ;;
    *) echo "Unknown streaming profile '${profile}'. Choose one of: ${STREAM_PROFILES}" >&2; return 1 ;;
  esac
  if [[ -n "$bitrate" ]]; then
    if ! [[ "$bitrate" =~ ^[0-9]+$ ]] || (( bitrate < VIDEO_BITRATE_MIN_KBPS || bitrate > VIDEO_BITRATE_MAX_KBPS )); then
      echo "--video-bitrate must be a whole number of kbps between ${VIDEO_BITRATE_MIN_KBPS} and ${VIDEO_BITRATE_MAX_KBPS}" >&2
      return 1
    fi
    vbr="$bitrate"
  fi
  printf 'RESOLUTION=%s\nFPS=%s\nVIDEO_BITRATE=%s\nAUDIO_BITRATE=%s\nX264_PRESET=%s\n' "$res" "$fps" "$vbr" "$abr" "$preset"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -euo pipefail
  stream_profile_env "$@"
fi
