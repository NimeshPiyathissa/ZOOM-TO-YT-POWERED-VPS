#!/usr/bin/env bash
# Zoom to YouTube Live Streaming - guided installer for a fresh Ubuntu 24.04 LTS server.
#
#   One command (interactive):
#     curl -fsSL https://raw.githubusercontent.com/NimeshPiyathissa/ZOOM-TO-YT-POWERED-VPS/main/install.sh -o install.sh && sudo bash install.sh
#   or from a clone:
#     git clone https://github.com/NimeshPiyathissa/ZOOM-TO-YT-POWERED-VPS.git /opt/zoom-stream && sudo /opt/zoom-stream/install.sh
#
#   Options:
#     --config FILE   unattended install: read every answer from FILE (see installer/config.example)
#     --fresh         forget the saved answers and progress of an interrupted install and start over
#     --repo URL      git repository to install from when this script runs outside a clone
#     --ref REF       branch or tag to check out in that case (default: main)
#     --staging       get Let's Encrypt STAGING (test) certificates - for repeated test installs; not browser-trusted
#     --no-color      plain output
#     -h, --help      this help
#   Internal (used by update.sh): --upgrade  re-deploy the current checkout on an existing install, no questions.
#
# What it does: checks the server, asks a few questions (or reads --config), shows a summary, then installs
# everything from official sources with numbered progress steps and a full log in /var/log/zoom-stream/.
# If anything fails it says which step, why, and how to continue; running it again resumes from that step with the
# saved answers. It never starts a broadcast: going live always needs a confirmed Go Live in the dashboard.
set -Eeuo pipefail
umask 022

ZS_DEFAULT_REPO="https://github.com/NimeshPiyathissa/ZOOM-TO-YT-POWERED-VPS.git"
ZS_DEFAULT_REF="main"
ZS_CLONE_DIR="/opt/zoom-stream"

ZS_MODE="install"
CONFIG_FILE=""
FRESH=0
STAGING=0
REPO_URL="${ZS_REPO_URL:-$ZS_DEFAULT_REPO}"
REPO_REF="${ZS_REPO_REF:-$ZS_DEFAULT_REF}"
ORIG_ARGS=("$@")

usage() { sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) CONFIG_FILE="${2:-}"; [[ -n "$CONFIG_FILE" ]] || { echo "--config needs a file path" >&2; exit 2; }; shift ;;
    --fresh) FRESH=1 ;;
    --staging) STAGING=1 ;;
    --repo) REPO_URL="${2:-}"; shift ;;
    --ref) REPO_REF="${2:-}"; shift ;;
    --upgrade) ZS_MODE="upgrade" ;;
    --no-color) export NO_COLOR=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done

if [[ $EUID -ne 0 ]]; then
  echo "Please run the installer as root:  sudo bash $0" >&2
  exit 1
fi

# ------------------------------------------------------------------ bootstrap: always install from a git clone

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo /nonexistent)"
if [[ -f "$SELF_DIR/installer/lib/common.sh" && -d "$SELF_DIR/dashboard" ]]; then
  ZS_SRC="$SELF_DIR"
  if ! git -C "$ZS_SRC" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "This copy of the project is not a git clone, so it could not be updated later with update.sh." >&2
    echo "Install from a clone instead:" >&2
    echo "  sudo git clone ${REPO_URL} ${ZS_CLONE_DIR} && sudo ${ZS_CLONE_DIR}/install.sh" >&2
    exit 1
  fi
else
  # Running on its own (downloaded with curl): fetch the project with git, then continue from the clone.
  if ! [[ "$REPO_REF" =~ ^[A-Za-z0-9._/-]+$ ]] || [[ "$REPO_URL" == *OWNER/REPO* ]]; then
    echo "No repository configured. Use: --repo https://github.com/<owner>/<repo>.git" >&2
    exit 1
  fi
  echo "Downloading the project into ${ZS_CLONE_DIR} (git clone)..."
  if ! command -v git >/dev/null; then
    DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=900 update -y >/dev/null
    DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=900 install -y git ca-certificates >/dev/null
  fi
  if [[ -d "$ZS_CLONE_DIR/.git" ]]; then
    git -C "$ZS_CLONE_DIR" fetch --tags origin
  else
    git clone --branch "$REPO_REF" "$REPO_URL" "$ZS_CLONE_DIR"
  fi
  if [[ -r /dev/tty ]] && { true </dev/tty; } 2>/dev/null; then
    exec bash "$ZS_CLONE_DIR/install.sh" "${ORIG_ARGS[@]}" </dev/tty
  fi
  exec bash "$ZS_CLONE_DIR/install.sh" "${ORIG_ARGS[@]}"
fi

# shellcheck source=installer/lib/common.sh
source "$ZS_SRC/installer/lib/common.sh"
# shellcheck source=installer/lib/validate.sh
source "$ZS_SRC/installer/lib/validate.sh"
# shellcheck source=installer/lib/domain.sh
source "$ZS_SRC/installer/lib/domain.sh"
# shellcheck source=installer/lib/preflight.sh
source "$ZS_SRC/installer/lib/preflight.sh"
# shellcheck source=installer/lib/questions.sh
source "$ZS_SRC/installer/lib/questions.sh"
# shellcheck source=installer/lib/steps.sh
source "$ZS_SRC/installer/lib/steps.sh"
zs_init_colors

# ------------------------------------------------------------------ log

mkdir -p "$ZS_LOG_DIR"
chmod 700 "$ZS_LOG_DIR"
ZS_LOG_FILE="$ZS_LOG_DIR/install-$(date -u +%Y%m%d-%H%M%S).log"
: >"$ZS_LOG_FILE"
chmod 600 "$ZS_LOG_FILE"
ln -sfn "$ZS_LOG_FILE" "$ZS_LOG_DIR/install.log"
{
  echo "zoom-stream installer, mode=${ZS_MODE}, started $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "source: ${ZS_SRC} @ $(git -C "$ZS_SRC" describe --tags --always --dirty 2>/dev/null || echo unknown)"
} >>"$ZS_LOG_FILE"

# ------------------------------------------------------------------ failure handling

CURRENT_STEP_TITLE=""
CURRENT_STEP_NO=0
STEP_TOTAL=0
STEP_PID=""

on_unexpected_error() {
  local rc="$1" line="$2"
  trap - ERR
  echo >&2
  zs_fail "The installer stopped unexpectedly (exit code ${rc}, line ${line})."
  _zs_log_plain "unexpected error: rc=${rc} line=${line}"   # the command text is not logged: it may hold a secret
  zs_hint "$(zs_explain_failure "$ZS_LOG_FILE")"
  zs_hint "Full log: ${ZS_LOG_FILE}"
  zs_hint "Fix the problem, then run the installer again - it continues where it stopped."
  exit "$rc"
}
trap 'on_unexpected_error $? $LINENO' ERR

on_interrupt() {
  trap - INT TERM ERR
  if [[ -n "$STEP_PID" ]]; then kill "$STEP_PID" 2>/dev/null || true; fi
  echo >&2
  zs_warn "Installation interrupted${CURRENT_STEP_TITLE:+ during step ${CURRENT_STEP_NO}/${STEP_TOTAL} (${CURRENT_STEP_TITLE})}."
  zs_hint "Nothing is lost: run the installer again and it resumes from this step with your saved answers."
  zs_hint "Log: ${ZS_LOG_FILE}"
  exit 130
}
trap on_interrupt INT TERM

step_failed() {
  local no="$1" title="$2" rc="$3"
  echo
  zs_fail "Step ${no}/${STEP_TOTAL} failed: ${title} (exit code ${rc})"
  zs_say "  ${C_BOLD}Why:${C_RESET} $(zs_explain_failure "$ZS_LOG_FILE")"
  echo "  Last lines of the log:"
  tail -n 12 "$ZS_LOG_FILE" | sed 's/^/    | /'
  echo
  zs_say "  Full log:   ${ZS_LOG_FILE}"
  if [[ "$ZS_MODE" == "install" ]]; then
    zs_say "  To resume:  sudo bash ${ZS_SRC}/install.sh   (continues from this step with your saved answers)"
  fi
  zs_say "  Diagnose:   sudo bash ${ZS_SRC}/doctor.sh"
}

_spin() {
  local pid="$1" label="$2" frames="|/-\\" i=0 start=$SECONDS
  if [[ -t 1 ]]; then
    while kill -0 "$pid" 2>/dev/null; do
      printf '\r  %s %s %s(%ds)%s ' "${frames:i++%4:1}" "$label" "$C_DIM" "$((SECONDS - start))" "$C_RESET"
      sleep 0.2
    done
    printf '\r\033[K'
  else
    printf '  ... %s\n' "$label"
    while kill -0 "$pid" 2>/dev/null; do sleep 1; done
  fi
}

# run_step ID TITLE FUNCTION
run_step() {
  local id="$1" title="$2" fn="$3" rc=0
  CURRENT_STEP_NO=$((CURRENT_STEP_NO + 1))
  CURRENT_STEP_TITLE="$title"
  local label="[${CURRENT_STEP_NO}/${STEP_TOTAL}] ${title}"
  if [[ "$ZS_MODE" == "install" ]] && zs_step_done "$id"; then
    zs_ok "${label} ${C_DIM}(already done)${C_RESET}"
    return 0
  fi
  echo "===== step ${id}: ${title} - $(date -u +%H:%M:%S) =====" >>"$ZS_LOG_FILE"
  # Background + wait (not `if ( ... )`): strict mode inside the step only works outside a condition.
  ( set -Eeuo pipefail; trap - ERR INT TERM; "$fn" ) >>"$ZS_LOG_FILE" 2>&1 </dev/null &
  STEP_PID=$!
  _spin "$STEP_PID" "$label"
  wait "$STEP_PID" || rc=$?
  STEP_PID=""
  if (( rc != 0 )); then
    echo "===== step ${id} FAILED rc=${rc} =====" >>"$ZS_LOG_FILE"
    trap - ERR
    step_failed "$CURRENT_STEP_NO" "$title" "$rc"
    exit 1
  fi
  echo "===== step ${id} ok =====" >>"$ZS_LOG_FILE"
  [[ "$ZS_MODE" == "install" ]] && zs_mark_done "$id"
  zs_ok "$label"
}

# ------------------------------------------------------------------ guards

if zs_encoder_running; then
  zs_die "The broadcast encoder is running (a stream may be live). Nothing was changed. Stop the broadcast in the dashboard first."
fi

if [[ "$ZS_MODE" == "upgrade" ]]; then
  [[ -f "$ZS_INSTALLED_FILE" || -d "$ZS_DASH_HOME/app" ]] || zs_die "No existing installation found - run the installer without --upgrade."
  zs_title "Updating the installation from $(git -C "$ZS_SRC" describe --tags --always 2>/dev/null)"
  # shellcheck disable=SC2034  # read indirectly by zs_get in the steps
  ANS_SSL_MODE="" ANS_OUTPUT_PRESET="" ANS_DOMAIN=""
  STEPS=(
    "packages|Installing/updating system packages|zs_step_packages"
    "pipeline|Deploying the streaming scripts|zs_step_pipeline"
    "units|Installing the background services|zs_step_units"
    "dashboard|Deploying the dashboard|zs_step_dashboard"
    "migrate|Updating the database schema|zs_step_migrate"
    "https|Checking HTTPS|zs_step_https"
    "verify|Health check|zs_step_verify"
  )
  STEP_TOTAL=${#STEPS[@]}
  for s in "${STEPS[@]}"; do IFS='|' read -r sid stitle sfn <<<"$s"; run_step "$sid" "$stitle" "$sfn"; done
  zs_ok "Update deployed."
  exit 0
fi

if [[ -f "$ZS_INSTALLED_FILE" && "$FRESH" -eq 0 ]]; then
  zs_title "Zoom to YouTube Live Streaming is already installed on this server."
  zs_say "  Update:      sudo bash ${ZS_SRC}/update.sh"
  zs_say "  Diagnose:    sudo bash ${ZS_SRC}/doctor.sh"
  zs_say "  Remove:      sudo bash ${ZS_SRC}/uninstall.sh"
  exit 0
fi

INTERACTIVE=1
if [[ -n "$CONFIG_FILE" ]]; then INTERACTIVE=0
elif ! zs_have_tty; then
  zs_die "No terminal available for the questions. Run it in an SSH session, or use --config FILE for an unattended install."
fi

RESUMING=0
if [[ "$FRESH" -eq 1 ]]; then
  rm -f "$ZS_ANSWERS_FILE" "$ZS_PROGRESS_FILE" "$ZS_INSTALLED_FILE"
elif [[ -f "$ZS_ANSWERS_FILE" ]]; then
  RESUMING=1
fi

# ------------------------------------------------------------------ welcome

if (( INTERACTIVE )) && (( ! RESUMING )); then
  cat <<EOF

${C_BOLD}Zoom to YouTube Live Streaming - installer${C_RESET}

  This sets up a server that joins Zoom meetings (or shows a web page) on a virtual screen and
  streams it to YouTube Live, with a web dashboard to control everything.

  ${C_BOLD}What will be installed${C_RESET}
    - Zoom (Linux client) and Google Chrome, from their official download servers
    - FFmpeg, a virtual display (Xvfb), audio routing (PipeWire) and a remote screen (x11vnc/noVNC)
    - The web dashboard (Python), served over HTTPS by Caddy
    - Background services that start automatically after a reboot

  ${C_BOLD}Requirements${C_RESET}   Ubuntu 24.04 LTS, x86_64, 4+ CPU cores, 8 GB memory, 20 GB free disk,
                 ports 80/443 reachable, a YouTube channel with live streaming enabled.
  ${C_BOLD}Time${C_RESET}           about 10-20 minutes, mostly downloads.

  Nothing is streamed until you press Go Live in the dashboard and confirm.
EOF
  echo
  zs_confirm "Start?" y || { echo "Cancelled - nothing was changed."; exit 0; }
fi

# ------------------------------------------------------------------ preflight

if ! zs_run_preflight; then
  echo "  Fix the failed checks above and run the installer again."
  exit 1
fi

# ------------------------------------------------------------------ answers

if (( RESUMING )); then
  zs_parse_kv_file "$ZS_ANSWERS_FILE" >/dev/null || true
  (( STAGING )) && zs_set LE_STAGING yes
  zs_title "Resuming the previous installation"
  zs_info "Saved answers from the interrupted run are reused (${ZS_ANSWERS_FILE})."
  zs_info "To start over with new answers instead: sudo bash ${ZS_SRC}/install.sh --fresh"
  if (( INTERACTIVE )); then
    zs_confirm "Continue the installation?" y || { echo "Cancelled - nothing else was changed."; exit 0; }
  fi
elif (( ! INTERACTIVE )); then
  [[ -f "$CONFIG_FILE" ]] || zs_die "Config file not found: ${CONFIG_FILE}"
  perms="$(stat -c '%a' "$CONFIG_FILE")"
  if (( (8#$perms & 8#077) != 0 )); then
    zs_die "${CONFIG_FILE} can be read by other users but contains passwords. Run: chmod 600 ${CONFIG_FILE}"
  fi
  zs_title "Reading answers from ${CONFIG_FILE}"
  errfile="$(mktemp)"
  if ! zs_parse_kv_file "$CONFIG_FILE" >"$errfile"; then
    sed 's/^/  /' "$errfile"; rm -f "$errfile"
    zs_die "The config file has errors (see above). Nothing was changed."
  fi
  rm -f "$errfile"
  if ! errs="$(zs_validate_config_answers)"; then
    printf '%s\n' "$errs"
    zs_die "Some settings in ${CONFIG_FILE} are not valid (see above). Nothing was changed."
  fi
  zs_validate_config_answers >/dev/null    # again in this shell: the check above ran in a subshell, keep its defaults
  (( STAGING )) && zs_set LE_STAGING yes
  zs_config_dns_check || true
  zs_print_summary
else
  if (( PF_SOFT > 0 )); then
    zs_confirm "Continue despite the warnings above?" n || { echo "Cancelled - nothing was changed."; exit 1; }
  fi
  zs_say ""
  zs_say "${C_BOLD}A few questions${C_RESET} (press Enter to accept the value in [brackets])."
  if (( STAGING )); then zs_set LE_STAGING yes; zs_warn "--staging: Let's Encrypt TEST certificates (not browser-trusted)."
  else zs_set LE_STAGING no; fi
  zs_ask_all
  if ! zs_summary_loop; then echo "Cancelled - nothing was changed."; exit 1; fi
fi

if (( ! INTERACTIVE )) && (( ! RESUMING )) && (( PF_SOFT > 0 )); then
  if [[ "$(zs_get ACCEPT_WARNINGS)" != "yes" ]]; then
    zs_die "There were ${PF_SOFT} warning(s). Fix them, or set ACCEPT_WARNINGS=yes in the config file to continue anyway."
  fi
  zs_warn "Continuing despite ${PF_SOFT} warning(s) (ACCEPT_WARNINGS=yes)."
fi

# Generate requested passwords once, before anything is installed, and keep them with the answers so a resumed
# run uses exactly the same values. Only generated passwords go to the root-only credentials file.
if (( ! RESUMING )); then
  generated=()
  for key in VNC_PASSWORD MASTER_PASSWORD; do
    if [[ "$(zs_get "$key")" == "auto" ]]; then zs_set "$key" "$(zs_gen_password)"; generated+=("$key"); fi
  done
  if (( ${#generated[@]} )); then
    ( umask 077
      {
        echo "# Zoom to YouTube Live Streaming - generated passwords ($(date -u +%Y-%m-%d))."
        echo "# Store them in a password manager, then delete this file:  sudo rm ${ZS_CREDENTIALS_FILE}"
        for key in "${generated[@]}"; do
          case "$key" in
            VNC_PASSWORD) printf 'VNC (remote screen) password: %s\n' "$(zs_get VNC_PASSWORD)" ;;
            MASTER_PASSWORD) printf 'Master encryption password:   %s\n' "$(zs_get MASTER_PASSWORD)" ;;
          esac
        done
      } >"$ZS_CREDENTIALS_FILE" )
    chmod 600 "$ZS_CREDENTIALS_FILE"
  fi
  zs_save_answers
fi

# ------------------------------------------------------------------ install

zs_title "Installing"
zs_say "  Progress is shown below; every detail goes to ${ZS_LOG_FILE}"
STEPS=(
  "packages|Installing system packages|zs_step_packages"
  "chrome|Installing Google Chrome|zs_step_chrome"
  "zoom|Installing the Zoom client|zs_step_zoom"
  "users|Creating the service user|zs_step_users"
  "pipeline|Deploying the streaming scripts and settings|zs_step_pipeline"
  "units|Installing the background services|zs_step_units"
  "timezone|Setting the timezone|zs_step_timezone"
  "firewall|Configuring the firewall|zs_step_firewall"
  "autoupdates|Configuring automatic security updates|zs_step_autoupdates"
  "dashboard|Installing the dashboard and secret store|zs_step_dashboard"
  "https|Setting up HTTPS|zs_step_https"
  "services|Starting the display and remote-screen services|zs_step_services"
  "verify|Health check|zs_step_verify"
)
STEP_TOTAL=${#STEPS[@]}
for s in "${STEPS[@]}"; do
  IFS='|' read -r sid stitle sfn <<<"$s"
  run_step "$sid" "$stitle" "$sfn"
done
CURRENT_STEP_TITLE=""

# ------------------------------------------------------------------ HTTPS: ports, certificate, the site really loads

[[ -z "$ZS_PUBLIC_IP" ]] && ZS_PUBLIC_IP="$(zs_public_ip || true)"
zs_title "Checking HTTPS"
HTTPS_OK=1
zs_ports_check "$ZS_PUBLIC_IP" "$(zs_get SSL_MODE)" || HTTPS_OK=0
if [[ "$(zs_get SSL_MODE)" == "letsencrypt" ]]; then
  if ! zs_dns_check "$(zs_get DOMAIN)" "$ZS_PUBLIC_IP"; then zs_dns_explain "$(zs_get DOMAIN)" "$ZS_PUBLIC_IP"; fi
  zs_https_verify "$(zs_get DOMAIN)" "$(zs_get LE_STAGING)" || HTTPS_OK=0
else
  zs_https_verify_ip "$ZS_PUBLIC_IP" || HTTPS_OK=0
fi

# ------------------------------------------------------------------ done

zs_scrub_saved_secrets
date -u +%Y-%m-%dT%H:%M:%SZ >"$ZS_INSTALLED_FILE"
chmod 600 "$ZS_INSTALLED_FILE"
{
  echo "# Written by install.sh - read by update.sh and doctor.sh."
  echo "SRC_DIR=${ZS_SRC}"
  echo "VERSION=$(git -C "$ZS_SRC" describe --tags --always 2>/dev/null || echo unknown)"
  echo "DOMAIN=$(zs_get DOMAIN)"
  echo "SSL_MODE=$(zs_get SSL_MODE)"
  echo "LE_EMAIL=$(zs_get LE_EMAIL)"
  echo "LE_STAGING=$(zs_get LE_STAGING)"
  echo "INSTALLED_AT=$(cat "$ZS_INSTALLED_FILE")"
} >"$ZS_INSTALL_CONF"
chmod 644 "$ZS_INSTALL_CONF"

if [[ "$(zs_get DOMAIN)" != "none" ]]; then
  URL="https://$(zs_get DOMAIN)"
else
  URL="https://${ZS_PUBLIC_IP:-<server-ip>}"
fi

zs_title "Installation complete"
echo
printf '  %-26s %s\n' "Service" "Status"
for u in xvfb openbox audio-setup x11vnc novnc-proxy browser-source; do
  printf '  %-26s %s\n' "$u" "$(zs_user_systemctl is-active "$u" 2>/dev/null || true)"
done
printf '  %-26s %s\n' "ffmpeg-stream (encoder)" "stopped - starts only when you press Go Live"
printf '  %-26s %s\n' "zoom-vacuum.timer" "$(zs_user_systemctl is-active zoom-vacuum.timer 2>/dev/null || true)"
printf '  %-26s %s\n' "dashboard" "$(systemctl is-active dashboard 2>/dev/null || true)"
printf '  %-26s %s\n' "caddy (HTTPS)" "$(systemctl is-active caddy 2>/dev/null || true)"
echo
zs_say "  ${C_BOLD}Dashboard${C_RESET}       ${URL}"
zs_say "  ${C_BOLD}Sign in as${C_RESET}      $(zs_get ADMIN_USERNAME)  (the password you chose)"
[[ "$(zs_get SSL_MODE)" == "self-signed" ]] && zs_say "                  Your browser will warn about the self-signed certificate once - accept it."
[[ "$(zs_get LE_STAGING)" == "yes" ]] && zs_say "                  STAGING certificate (test only, browsers warn). Real one: sudo bash ${ZS_SRC}/set-domain.sh $(zs_get DOMAIN)"
[[ "$(zs_get DOMAIN)" == "none" ]] && zs_say "                  Add a domain + trusted certificate later: sudo bash ${ZS_SRC}/set-domain.sh stream.example.com"
if (( ! HTTPS_OK )); then
  zs_warn "Everything is installed, but HTTPS is not working yet - see 'Checking HTTPS' above for the reason and fix."
  zs_hint "Then re-check with: sudo bash ${ZS_SRC}/set-domain.sh --check   (guide: docs/domain-and-https.md)"
fi
[[ -f "$ZS_CREDENTIALS_FILE" ]] && zs_say "  ${C_BOLD}Generated${C_RESET}       passwords are in ${ZS_CREDENTIALS_FILE} (root only) - store them, then delete the file."
zs_say "  ${C_BOLD}Settings${C_RESET}        ${ZS_APP_DIR}/.env (stream settings), ${ZS_DASH_HOME}/data (dashboard data, encrypted secrets)"
zs_say "  ${C_BOLD}Logs${C_RESET}            ${ZS_APP_DIR}/logs/, journalctl -u dashboard, install log ${ZS_LOG_FILE}"
zs_say "  ${C_BOLD}Tools${C_RESET}           ${ZS_SRC}/{update,backup,restore,doctor,set-domain,uninstall}.sh"
echo
zs_say "  ${C_BOLD}Next steps${C_RESET}"
zs_say "    1. Open the dashboard and sign in."
[[ -z "$(zs_get YT_STREAM_KEY)" ]] && zs_say "    2. Live Studio: paste your YouTube stream key."
zs_say "    3. Zoom page: add a meeting link. Remote GUI shows the bot's screen."
zs_say "    4. Make a short test recording (Live Studio) before your first real stream."
zs_say "    5. Press Go Live and confirm when you are ready."
if [[ "$(zs_get FIREWALL)" == "yes" ]]; then
  zs_say "    If your provider has a cloud firewall / security group, allow TCP 443$([[ "$(zs_get SSL_MODE)" == letsencrypt ]] && echo ' and 80') there too."
fi
echo
