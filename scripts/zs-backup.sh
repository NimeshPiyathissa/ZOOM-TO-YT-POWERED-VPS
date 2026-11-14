#!/usr/bin/env bash
# Backup and restore for a Zoom-to-YouTube install. Run as root.
#
#   zs-backup.sh create [--label NAME] [--include-master-key]   write /var/backups/zoom-stream/zs-<label>-<UTC>.tar.gz
#   zs-backup.sh list                                            backups, newest first
#   zs-backup.sh verify FILE                                     checksum + readable archive
#   zs-backup.sh restore FILE [--yes]                            put a backup back (see the rules below)
#
# What a backup holds: the pipeline (scripts, fonts, units, .env, current-source.env, uploaded watermark images),
# the dashboard code, its database, settings and ENCRYPTED secret vault, its TLS certificate, and the system files
# the installer manages (sudoers drop-in, dashboard unit, logrotate and Caddy config).
# NOT included: logs, recordings, Chrome profiles (signed-in sessions - sign in again after a restore), and - unless
# --include-master-key - the cached vault key in /etc/zoom-stream. Without it the vault can only be opened with the
# master password, which is the point: a backup file on its own does not expose the stream key or other secrets.
#
# Restore rules (an active stream is never interrupted):
#   * refused while the encoder (ffmpeg-stream) is running - stop the broadcast yourself first;
#   * needs an interactive "restore" confirmation, or --yes;
#   * takes a safety backup of the current state first, so a restore can itself be undone;
#   * restarts only dashboard.service afterwards. It never starts the encoder: going live again needs a manual Go Live.
#
# Test hooks (never set in production): ZS_ROOT (filesystem root, default /), ZS_BACKUP_DIR.
set -euo pipefail
umask 077

ROOT="${ZS_ROOT:-}"
ROOT="${ROOT%/}"
BACKUP_DIR="${ZS_BACKUP_DIR:-${ROOT}/var/backups/zoom-stream}"
SVC_USER="zoombot"

# Paths relative to the filesystem root. Missing ones are skipped.
INCLUDE=(
  home/zoombot/zoom-stream
  home/zoombot/.config/systemd/user
  home/dashboard/app
  home/dashboard/data
  home/dashboard/certs
  etc/sudoers.d/dashboard
  etc/systemd/system/dashboard.service
  etc/logrotate.d/zoom-stream
  etc/caddy/Caddyfile
  etc/caddy/zoom-stream
)
EXCLUDE=(
  home/zoombot/zoom-stream/logs
  home/dashboard/app/venv
  "home/dashboard/app/__pycache__"
  "*.pyc"
)

say() { echo "[zs-backup] $*"; }
die() { echo "[zs-backup] ERROR: $*" >&2; exit 1; }
[[ $EUID -eq 0 || -n "${ZS_ROOT:-}" ]] || die "run as root"

encoder_running() {
  # True when the broadcast encoder is up. Any doubt (cannot ask systemd) counts as running: refusing a restore is
  # always safer than interrupting a live class.
  if [[ -n "${ZS_ROOT:-}" ]]; then
    [[ -f "${ROOT}/run/zs-encoder-active" ]]
    return
  fi
  local uid
  uid="$(id -u "$SVC_USER" 2>/dev/null)" || return 1                  # no service user: nothing can be running
  local state
  state="$(sudo -u "$SVC_USER" XDG_RUNTIME_DIR="/run/user/${uid}" systemctl --user is-active ffmpeg-stream.service 2>/dev/null || true)"
  case "$state" in
    inactive|failed|"") return 1 ;;
    *) return 0 ;;                                                     # active, activating, deactivating, unknown
  esac
}

create() {
  local label="manual" with_key=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --label) label="${2:-manual}"; shift ;;
      --include-master-key) with_key=1 ;;
      *) die "create: unknown option $1" ;;
    esac
    shift
  done
  [[ "$label" =~ ^[A-Za-z0-9._-]{1,40}$ ]] || die "label may only contain letters, digits, . _ -"
  local -a items=() excl=()
  local p
  for p in "${INCLUDE[@]}"; do [[ -e "${ROOT}/${p}" ]] && items+=("$p"); done
  if (( with_key )) && [[ -e "${ROOT}/etc/zoom-stream" ]]; then
    items+=("etc/zoom-stream")
    say "WARNING: this backup contains the cached vault key - anyone with the file can read your secrets. Keep it safe."
  fi
  (( ${#items[@]} )) || die "nothing to back up under ${ROOT:-/} - is this an installed system?"
  for p in "${EXCLUDE[@]}"; do excl+=(--exclude="$p"); done
  mkdir -p "$BACKUP_DIR"
  chmod 700 "$BACKUP_DIR"
  local out
  out="${BACKUP_DIR}/zs-${label}-$(date -u +%Y%m%d-%H%M%S).tar.gz"
  tar -C "${ROOT:-/}" "${excl[@]}" -czpf "$out.part" "${items[@]}"
  mv "$out.part" "$out"
  chmod 600 "$out"
  (cd "$BACKUP_DIR" && sha256sum "$(basename "$out")" > "$(basename "$out").sha256")
  chmod 600 "$out.sha256"
  say "created $out ($(du -h "$out" | cut -f1))"
  echo "$out"
}

verify() {
  local f="${1:-}"
  [[ -f "$f" ]] || die "no such backup: $f"
  [[ -f "$f.sha256" ]] || die "checksum file $f.sha256 missing - refusing to trust this backup"
  (cd "$(dirname "$f")" && sha256sum -c --quiet "$(basename "$f").sha256") || die "CHECKSUM MISMATCH: $f is damaged or was modified"
  tar -tzf "$f" >/dev/null || die "$f is not a readable archive"
  # Only the known install locations may be inside - a crafted archive must not write anywhere else.
  local bad
  bad="$(tar -tzf "$f" | grep -Ev '^(home/zoombot/zoom-stream|home/zoombot/\.config/systemd/user|home/dashboard/(app|data|certs)|etc/(sudoers\.d/dashboard|systemd/system/dashboard\.service|logrotate\.d/zoom-stream|caddy/Caddyfile|caddy/zoom-stream|zoom-stream))(/|$)|^(home/zoombot/\.config/systemd|home/zoombot/\.config|home/zoombot|home/dashboard|home|etc|etc/caddy|etc/sudoers\.d|etc/systemd|etc/systemd/system|etc/logrotate\.d)/$' || true)"
  [[ -z "$bad" ]] || die "unexpected paths in $f - refusing: $(echo "$bad" | head -3 | tr '\n' ' ')"
  if tar -tzf "$f" | grep -Eq '(^|/)\.\.(/|$)|^/'; then die "unsafe path in $f - refusing"; fi
  say "OK: $f"
}

restore() {
  local f="" yes=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --yes) yes=1 ;;
      *) f="$1" ;;
    esac
    shift
  done
  verify "$f"
  if encoder_running; then
    die "the encoder is running (a broadcast may be live). Stop the broadcast from the dashboard first - a restore never interrupts a stream."
  fi
  if (( ! yes )); then
    [[ -t 0 ]] || die "restore needs confirmation: run it in a terminal, or add --yes"
    read -r -p "[zs-backup] Restore $(basename "$f") over the current install? Type 'restore' to continue: " answer
    [[ "$answer" == "restore" ]] || { say "cancelled - nothing changed"; exit 1; }
  fi
  local safety
  safety="$(create --label pre-restore | tail -1)"
  say "current state saved first: $safety"
  tar -C "${ROOT:-/}" -xzpf "$f"                    # as root: owners and modes exactly as they were backed up
  if [[ -z "${ZS_ROOT:-}" ]]; then
    chown -R zoombot:zoombot /home/zoombot/zoom-stream /home/zoombot/.config/systemd/user 2>/dev/null || true
    chown -R dashboard:dashboard /home/dashboard/app /home/dashboard/data /home/dashboard/certs 2>/dev/null || true
    [[ -f /etc/sudoers.d/dashboard ]] && { chown root:root /etc/sudoers.d/dashboard; chmod 440 /etc/sudoers.d/dashboard;
      visudo -cf /etc/sudoers.d/dashboard >/dev/null || say "WARNING: restored sudoers file does not validate - check it with visudo"; }
    systemctl daemon-reload
    systemctl restart dashboard.service || say "WARNING: dashboard.service did not restart - check: journalctl -u dashboard"
    if systemctl is-enabled --quiet caddy 2>/dev/null; then systemctl reload caddy || true; fi
  fi
  say "restored $f. The encoder was NOT started - use Go Live in the dashboard when you are ready."
  say "To undo this restore: $0 restore $safety"
}

list() {
  [[ -d "$BACKUP_DIR" ]] || { say "no backups yet ($BACKUP_DIR)"; return 0; }
  ls -1t "$BACKUP_DIR"/zs-*.tar.gz 2>/dev/null || say "no backups yet"
}

cmd="${1:-}"
shift || true
case "$cmd" in
  create) create "$@" ;;
  list) list ;;
  verify) verify "${1:-}" ;;
  restore) restore "$@" ;;
  *) sed -n '2,24p' "$0"; exit 2 ;;
esac
