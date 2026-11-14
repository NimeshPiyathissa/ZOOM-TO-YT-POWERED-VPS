#!/usr/bin/env bash
# Back up this installation (settings, database, encrypted secrets, certificates, service files).
#
#   sudo ./backup.sh                        create a backup in /var/backups/zoom-stream/
#   sudo ./backup.sh --label before-term    name it
#   sudo ./backup.sh --include-master-key   also include the vault key cache (then the file alone can read your
#                                           secrets - keep it as safe as a password)
#   sudo ./backup.sh list                   show existing backups, newest first
#   --no-color                              plain output
#
# Not included: logs, recordings and Chrome sign-ins (sign in again after a restore). Copy backups off the server
# (e.g. scp) - a backup on the same disk does not survive losing the server. Safe during a live stream.
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_args=()
for _a in "$@"; do
  if [[ "$_a" == --no-color ]]; then export NO_COLOR=1; else _args+=("$_a"); fi
done
set -- "${_args[@]+"${_args[@]}"}"
case "${1:-}" in -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;; esac
[[ $EUID -eq 0 ]] || { echo "Run as root: sudo $0" >&2; exit 1; }
if [[ "${1:-}" == "list" ]]; then exec bash "$SRC/scripts/zs-backup.sh" list; fi
label="manual"
args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --label) label="${2:-manual}"; shift ;;
    --include-master-key) args+=(--include-master-key) ;;
    *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done
out="$(bash "$SRC/scripts/zs-backup.sh" create --label "$label" "${args[@]}" | tail -n1)"
echo "Backup written: $out"
echo "Copy it somewhere safe, for example:  scp root@<server>:$out ."
