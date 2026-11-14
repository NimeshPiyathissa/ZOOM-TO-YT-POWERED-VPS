#!/usr/bin/env bash
# Update an installation to a newer release, with an automatic rollback if anything goes wrong.
#
#   sudo ./update.sh                 update to the newest release tag (or the tracked branch if there are no tags)
#   sudo ./update.sh --ref v1.1.0    update (or move) to a specific tag or branch
#   sudo ./update.sh --check         only show whether an update is available
#   sudo ./update.sh --yes           do not ask for confirmation
#
# Order: refuse during a live stream -> backup -> git checkout -> redeploy (packages, scripts, services, dashboard)
# -> database migration -> restart -> health check. On any failure: the previous version's code is checked out
# again, the backup is restored and redeployed, and the reason is shown. It never starts a broadcast.
set -Eeuo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=installer/lib/common.sh
source "$SRC/installer/lib/common.sh"

REF="" CHECK=0 YES=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ref) REF="${2:-}"; shift ;;
    --check) CHECK=1 ;;
    --yes|-y) YES=1 ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) zs_die "Unknown option: $1 (see --help)" ;;
  esac
  shift
done
[[ $EUID -eq 0 ]] || zs_die "Run as root: sudo $0"
[[ -f "$ZS_INSTALLED_FILE" ]] || zs_die "No completed installation found on this server. Run install.sh first."
git -C "$SRC" rev-parse --is-inside-work-tree >/dev/null 2>&1 || zs_die "$SRC is not a git clone; update.sh needs one."
if [[ -n "$REF" && ! "$REF" =~ ^[A-Za-z0-9._/-]+$ ]]; then zs_die "Invalid --ref value."; fi

mkdir -p "$ZS_LOG_DIR"; chmod 700 "$ZS_LOG_DIR"
ZS_LOG_FILE="$ZS_LOG_DIR/update-$(date -u +%Y%m%d-%H%M%S).log"
: >"$ZS_LOG_FILE"; chmod 600 "$ZS_LOG_FILE"

zs_title "Checking for updates"
if [[ -n "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]]; then
  zs_die "Files in $SRC were edited locally. Keep a copy of your changes, then run: sudo git -C $SRC checkout -- . (nothing was changed)"
fi
zs_retry 3 5 git -C "$SRC" fetch --tags --prune origin >>"$ZS_LOG_FILE" 2>&1 || zs_die "Could not reach the git repository (network?). Nothing was changed."

CURRENT="$(git -C "$SRC" rev-parse HEAD)"
CURRENT_NAME="$(git -C "$SRC" describe --tags --always)"
if [[ -z "$REF" ]]; then
  REF="$(git -C "$SRC" tag --list 'v[0-9]*' --sort=-v:refname | head -n1)"
  if [[ -z "$REF" ]]; then
    branch="$(git -C "$SRC" rev-parse --abbrev-ref '@{upstream}' 2>/dev/null || echo origin/main)"
    REF="$branch"
  fi
fi
if git -C "$SRC" rev-parse -q --verify "origin/${REF}^{commit}" >/dev/null; then TARGET_REF="origin/${REF}"; else TARGET_REF="$REF"; fi
TARGET="$(git -C "$SRC" rev-parse -q --verify "${TARGET_REF}^{commit}")" || zs_die "Unknown version '${REF}'."
zs_info "Installed: ${CURRENT_NAME}"
zs_info "Available: ${REF} ($(git -C "$SRC" describe --tags --always "$TARGET"))"
if [[ "$CURRENT" == "$TARGET" ]]; then zs_ok "Already up to date."; exit 0; fi
if (( CHECK )); then
  echo; git -C "$SRC" log --oneline "${CURRENT}..${TARGET}" | head -n 20 | sed 's/^/    /'
  exit 0
fi

if zs_encoder_running; then
  zs_die "The broadcast encoder is running (a stream may be live). Stop the broadcast first - nothing was changed."
fi
if (( ! YES )); then
  zs_have_tty || zs_die "Confirmation needed: run in a terminal, or add --yes."
  zs_confirm "Update now? A backup is taken first, and the update is rolled back automatically if it fails." y \
    || { echo "Cancelled - nothing was changed."; exit 0; }
fi

zs_title "1/4 Backup"
BACKUP="$(bash "$SRC/scripts/zs-backup.sh" create --label pre-update 2>>"$ZS_LOG_FILE" | tail -n1)" \
  || zs_die "The backup failed, so the update was not started. Nothing was changed. Log: $ZS_LOG_FILE"
[[ -f "$BACKUP" ]] || zs_die "The backup file was not created. Nothing was changed."
zs_ok "Backup: $BACKUP"

rollback() {
  local why="$1"
  trap - ERR
  zs_fail "Update failed: ${why}"
  zs_title "Rolling back to ${CURRENT_NAME}"
  git -C "$SRC" -c advice.detachedHead=false checkout -q --force "$CURRENT" >>"$ZS_LOG_FILE" 2>&1 || true
  if bash "$SRC/scripts/zs-backup.sh" restore "$BACKUP" --yes >>"$ZS_LOG_FILE" 2>&1 \
     && bash "$SRC/install.sh" --upgrade --no-color >>"$ZS_LOG_FILE" 2>&1; then
    zs_ok "Rolled back: ${CURRENT_NAME} is running again."
  else
    zs_fail "The automatic rollback did not finish. Restore by hand: sudo bash $SRC/restore.sh $BACKUP"
  fi
  zs_hint "$(zs_explain_failure "$ZS_LOG_FILE")"
  zs_hint "Log: $ZS_LOG_FILE"
  exit 1
}
trap 'rollback "unexpected error (line $LINENO)"' ERR

zs_title "2/4 Downloading ${REF}"
git -C "$SRC" -c advice.detachedHead=false checkout -q "$TARGET" >>"$ZS_LOG_FILE" 2>&1 || rollback "could not check out ${REF}"
zs_ok "Code is now at $(git -C "$SRC" describe --tags --always)"

zs_title "3/4 Deploying (packages, scripts, services, dashboard, database)"
if ! bash "$SRC/install.sh" --upgrade 2>&1 | tee -a "$ZS_LOG_FILE"; then
  rollback "the new version could not be deployed"
fi

zs_title "4/4 Final check"
ok=0
for _ in $(seq 1 20); do
  if curl -fsS --max-time 3 http://127.0.0.1:8000/health >/dev/null 2>&1; then ok=1; break; fi
  sleep 2
done
(( ok )) || rollback "the dashboard did not come back after the update"
sed -i "s/^VERSION=.*/VERSION=$(git -C "$SRC" describe --tags --always)/" "$ZS_INSTALL_CONF" 2>/dev/null || true
trap - ERR
zs_ok "Updated ${CURRENT_NAME} -> $(git -C "$SRC" describe --tags --always). The backup stays in $(dirname "$BACKUP")."
zs_info "Running pipeline services pick up new scripts the next time they start; nothing was started or stopped."
