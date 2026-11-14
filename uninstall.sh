#!/usr/bin/env bash
# Remove Zoom to YouTube Live Streaming from this server.
#
#   sudo ./uninstall.sh            guided: asks before anything is deleted
#   sudo ./uninstall.sh --yes      remove the services and app code, KEEP all data (no questions)
#   sudo ./uninstall.sh --yes --purge-data   also delete the data (database, encrypted secrets, recordings,
#                                            Chrome sign-ins, backups) - irreversible
#
# Always removed: the services, the dashboard and pipeline code, sudoers/logrotate/systemd files, the HTTPS config.
# Data is kept unless you confirm deleting it (a final backup is offered first). Zoom, Chrome, Caddy and other
# system packages are only removed if you say so. The git clone itself is left in place.
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=installer/lib/common.sh
source "$SRC/installer/lib/common.sh"

YES=0 PURGE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes|-y) YES=1 ;;
    --purge-data) PURGE=1 ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) zs_die "Unknown option: $1 (see --help)" ;;
  esac
  shift
done
[[ $EUID -eq 0 ]] || zs_die "Run as root: sudo $0"
if (( PURGE && ! YES )); then zs_die "--purge-data needs --yes (or run without options and answer the questions)."; fi
if (( ! YES )); then zs_have_tty || zs_die "No terminal for the questions: use --yes (keeps data) or --yes --purge-data."; fi

zs_title "Uninstall Zoom to YouTube Live Streaming"
REMOVE_PKGS=0
if (( ! YES )); then
  zs_say "  This stops and removes the services, the dashboard and the streaming scripts."
  answer=""
  zs_read answer "  Type 'uninstall' to continue: "
  [[ "$answer" == "uninstall" ]] || { echo "Cancelled - nothing was changed."; exit 0; }
  if zs_encoder_running; then
    zs_warn "The broadcast encoder is running - a stream may be live and will END."
    zs_confirm "End the stream and continue?" n || { echo "Cancelled - nothing was changed."; exit 0; }
  fi
  echo
  zs_say "  Your data: dashboard database and settings, encrypted secrets, stream settings (.env), recordings,"
  zs_say "  signed-in Chrome profiles, and the backups in /var/backups/zoom-stream."
  if zs_confirm "  Delete this data too? (cannot be undone)" n; then
    PURGE=1
    if zs_confirm "  Make a final backup first (kept in /var/backups/zoom-stream)?" y; then
      out="$(bash "$SRC/scripts/zs-backup.sh" create --label final | tail -n1)"
      zs_ok "Final backup: $out (copy it off the server before deleting the backups folder)"
    fi
  fi
  zs_confirm "  Also remove the Zoom client, Google Chrome and Caddy packages?" n && REMOVE_PKGS=1
elif zs_encoder_running; then
  zs_warn "Ending the running broadcast (--yes)."
fi

zs_title "Removing"
if id "$ZS_SVC_USER" &>/dev/null; then
  units="ffmpeg-stream x11vnc zoom browser-source audio-setup openbox xvfb novnc-proxy zoom-vacuum.timer"
  # shellcheck disable=SC2086
  zs_user_systemctl stop $units 2>/dev/null || true
  # shellcheck disable=SC2086
  zs_user_systemctl disable $units 2>/dev/null || true
  rm -f "$ZS_SVC_HOME"/.config/systemd/user/{xvfb,openbox,audio-setup,zoom,browser-source,x11vnc,novnc-proxy,ffmpeg-stream,zoom-vacuum}.{service,timer}
  zs_user_systemctl daemon-reload 2>/dev/null || true
  loginctl disable-linger "$ZS_SVC_USER" 2>/dev/null || true
  zs_ok "Streaming services stopped and removed"
fi
systemctl disable --now dashboard.service 2>/dev/null || true
rm -f /etc/systemd/system/dashboard.service /etc/sudoers.d/dashboard /etc/logrotate.d/zoom-stream
systemctl daemon-reload
zs_ok "Dashboard service, sudoers rule and log rotation removed"

if [[ -f /etc/caddy/Caddyfile ]] && grep -q '^# managed-by: zoom-stream setup-https.sh' /etc/caddy/Caddyfile; then
  systemctl stop caddy 2>/dev/null || true
  rm -f /etc/caddy/Caddyfile
  rm -rf /etc/caddy/zoom-stream
  zs_ok "HTTPS configuration removed (Caddy stopped)"
fi

rm -rf "$ZS_DASH_HOME/app" "$ZS_DASH_HOME/venv" "$ZS_APP_DIR/scripts" "$ZS_APP_DIR/extensions"
rm -f "$ZS_INSTALLED_FILE" "$ZS_PROGRESS_FILE" "$ZS_ANSWERS_FILE" "$ZS_INSTALL_CONF"
zs_ok "Application code removed"

if (( PURGE )); then
  rm -rf "$ZS_DASH_HOME" "$ZS_SVC_HOME" "$ZS_ETC_DIR" /var/backups/zoom-stream "$ZS_CREDENTIALS_FILE"
  userdel "$ZS_DASH_USER" 2>/dev/null || true
  userdel "$ZS_SVC_USER" 2>/dev/null || true
  zs_ok "Data, backups and the service users deleted"
else
  zs_info "Data kept: $ZS_DASH_HOME/data, $ZS_APP_DIR (.env, logs), /var/backups/zoom-stream, $ZS_ETC_DIR"
  zs_info "The '$ZS_SVC_USER' and '$ZS_DASH_USER' users were kept because they own that data."
fi

if (( REMOVE_PKGS )); then
  DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=600 -y purge zoom google-chrome-stable caddy >/dev/null 2>&1 || true
  rm -f /etc/apt/sources.list.d/google-chrome.list
  zs_ok "Zoom, Google Chrome and Caddy removed"
fi

echo
zs_ok "Uninstall complete. The project files in $SRC were left in place (delete them with: sudo rm -rf $SRC)."
zs_say "  Firewall rules (ufw) were not changed."
