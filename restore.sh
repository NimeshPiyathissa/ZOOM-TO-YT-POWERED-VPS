#!/usr/bin/env bash
# Restore a backup made by backup.sh (or automatically by update.sh).
#
#   sudo ./restore.sh                 pick a backup from a numbered list
#   sudo ./restore.sh FILE [--yes]    restore that file
#   --no-color                        plain output
#
# Refused while the encoder is running (a stream may be live). A safety backup of the current state is taken first,
# so a restore can itself be undone. Only the dashboard is restarted; nothing is put on air.
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=installer/lib/common.sh
source "$SRC/installer/lib/common.sh"
_args=()
for _a in "$@"; do
  if [[ "$_a" == --no-color ]]; then export NO_COLOR=1; zs_init_colors; else _args+=("$_a"); fi
done
set -- "${_args[@]+"${_args[@]}"}"
case "${1:-}" in -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;; esac
[[ $EUID -eq 0 ]] || zs_die "Run as root: sudo $0"

file="${1:-}"
if [[ -z "$file" ]]; then
  dir="/var/backups/zoom-stream"
  mapfile -t files < <(find "$dir" -maxdepth 1 -name 'zs-*.tar.gz' -printf '%T@ %p\n' 2>/dev/null | sort -rn | cut -d' ' -f2-)
  (( ${#files[@]} )) || zs_die "No backups found in $dir."
  zs_have_tty || zs_die "Name the backup file: sudo $0 FILE --yes"
  zs_title "Backups (newest first)"
  for i in "${!files[@]}"; do printf '  %2d) %s  (%s)\n' "$((i + 1))" "$(basename "${files[$i]}")" "$(du -h "${files[$i]}" | cut -f1)"; done
  pick=""
  while true; do
    zs_read pick "  Restore which one? [1-${#files[@]}, q = cancel]: "
    [[ "${pick,,}" == q ]] && { echo "Cancelled - nothing was changed."; exit 0; }
    if [[ "$pick" =~ ^[0-9]+$ ]] && (( pick >= 1 && pick <= ${#files[@]} )); then file="${files[$((pick - 1))]}"; break; fi
    echo "    Enter a number from the list."
  done
  shift || true
else
  shift
fi
exec bash "$SRC/scripts/zs-backup.sh" restore "$file" "$@"
