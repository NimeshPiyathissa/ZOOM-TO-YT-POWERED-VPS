#!/usr/bin/env bash
# Shared helpers for install.sh, update.sh, backup.sh, restore.sh, uninstall.sh and doctor.sh:
# terminal output, the install log, saved answers, step progress, retries and failure explanations.
#
# Secrets rule: nothing in here ever prints or logs an answer whose key is listed in ZS_SECRET_KEYS.
# shellcheck disable=SC2034  # many of these paths are used by the scripts that source this file

# ------------------------------------------------------------------ locale

# Every tool these scripts read output from (ufw, apt, dpkg, systemctl, timedatectl ...) answers in plain English,
# whatever language the server is set to: "Status: active" and "Could not get lock" are matched as text.
# C.UTF-8 is built into Ubuntu's C library, so it always exists.
export LC_ALL=C.UTF-8 LANG=C.UTF-8 LANGUAGE=

# ------------------------------------------------------------------ locations

ZS_ETC_DIR="${ZS_ETC_DIR:-/etc/zoom-stream}"
ZS_STATE_DIR="${ZS_STATE_DIR:-${ZS_ETC_DIR}/installer}"
ZS_ANSWERS_FILE="${ZS_STATE_DIR}/answers.conf"
ZS_PROGRESS_FILE="${ZS_STATE_DIR}/progress"
ZS_INSTALLED_FILE="${ZS_STATE_DIR}/installed"
ZS_INSTALL_CONF="${ZS_ETC_DIR}/install.conf"
ZS_VERSIONS_FILE="${ZS_STATE_DIR}/versions.txt"
ZS_LOG_DIR="${ZS_LOG_DIR:-/var/log/zoom-stream}"
ZS_CREDENTIALS_FILE="${ZS_CREDENTIALS_FILE:-/root/zoom-stream-credentials.txt}"
ZS_SVC_USER="zoombot"
ZS_SVC_HOME="/home/${ZS_SVC_USER}"
ZS_APP_DIR="${ZS_SVC_HOME}/zoom-stream"
ZS_DASH_USER="dashboard"
ZS_DASH_HOME="/home/${ZS_DASH_USER}"

ZS_ANSWER_KEYS=(DOMAIN SSL_MODE LE_EMAIL LE_STAGING ADMIN_USERNAME ADMIN_PASSWORD YT_RTMP_URL YT_STREAM_KEY BOT_NAME OUTPUT_PRESET
                TIMEZONE VNC_PASSWORD MASTER_PASSWORD FIREWALL AUTO_UPDATES)
ZS_SECRET_KEYS=" ADMIN_PASSWORD YT_STREAM_KEY VNC_PASSWORD MASTER_PASSWORD "

# ------------------------------------------------------------------ terminal output

zs_init_colors() {
  if [[ -t 1 && -z "${NO_COLOR:-}" && "${TERM:-dumb}" != "dumb" ]]; then
    C_RESET=$'\e[0m' C_BOLD=$'\e[1m' C_DIM=$'\e[2m' C_RED=$'\e[31m' C_GREEN=$'\e[32m' C_YELLOW=$'\e[33m'
    C_CYAN=$'\e[36m'
  else
    C_RESET="" C_BOLD="" C_DIM="" C_RED="" C_GREEN="" C_YELLOW="" C_CYAN=""
  fi
}
zs_init_colors

# The log file, once known. Everything printed through these helpers is also appended to it (never secrets).
ZS_LOG_FILE="${ZS_LOG_FILE:-}"

_zs_log_plain() { [[ -n "$ZS_LOG_FILE" ]] && printf '%s\n' "$*" >>"$ZS_LOG_FILE" 2>/dev/null; return 0; }
zs_say()   { printf '%s\n' "$*"; _zs_log_plain "$*"; }
zs_title() { printf '\n%s%s%s\n' "$C_BOLD" "$*" "$C_RESET"; _zs_log_plain "== $*"; }
zs_ok()    { printf '  %s✓%s %s\n' "$C_GREEN" "$C_RESET" "$*"; _zs_log_plain "  [ok] $*"; }
zs_warn()  { printf '  %s!%s %s\n' "$C_YELLOW" "$C_RESET" "$*"; _zs_log_plain "  [warn] $*"; }
zs_fail()  { printf '  %s✗%s %s\n' "$C_RED" "$C_RESET" "$*"; _zs_log_plain "  [fail] $*"; }
zs_hint()  { printf '      %s%s%s\n' "$C_DIM" "$*" "$C_RESET"; _zs_log_plain "        hint: $*"; }
zs_info()  { printf '  %s•%s %s\n' "$C_CYAN" "$C_RESET" "$*"; _zs_log_plain "  [info] $*"; }
zs_die()   { printf '\n%sError:%s %s\n' "$C_RED" "$C_RESET" "$*" >&2; _zs_log_plain "ERROR: $*"; exit 1; }

# ------------------------------------------------------------------ interactive input

# Questions are read from the terminal even when the script itself arrived on stdin (curl ... | sudo bash).
ZS_TTY="${ZS_TTY:-/dev/tty}"
ZS_TTY_OUT="${ZS_TTY_OUT:-$ZS_TTY}"     # test hook: where the newline after a hidden answer goes
ZS_IN_FD=""
zs_have_tty() { [[ -r "$ZS_TTY" ]] && { true <"$ZS_TTY"; } 2>/dev/null; }

# One descriptor for the whole session, so answers are read in order (also from a scripted input in tests).
_zs_open_input() { [[ -n "$ZS_IN_FD" ]] || exec {ZS_IN_FD}<"$ZS_TTY"; }

# zs_read VAR PROMPT  - one line, visible
zs_read() {
  local __v
  _zs_open_input
  IFS= read -r -u "$ZS_IN_FD" -p "$2" __v || __v=""
  printf -v "$1" '%s' "$__v"
}

# zs_read_secret VAR PROMPT  - one line, not echoed
zs_read_secret() {
  local __v
  _zs_open_input
  IFS= read -r -s -u "$ZS_IN_FD" -p "$2" __v || __v=""
  printf '\n' >>"$ZS_TTY_OUT"
  printf -v "$1" '%s' "$__v"
}

# zs_confirm PROMPT DEFAULT(y|n) -> 0 for yes
zs_confirm() {
  local a def="${2:-n}" shown="[y/N]"
  [[ "$def" == "y" ]] && shown="[Y/n]"
  while true; do
    zs_read a "$1 $shown "
    a="${a,,}"
    [[ -z "$a" ]] && a="$def"
    case "$a" in y|yes) return 0 ;; n|no) return 1 ;; esac
    echo "    Please answer y or n."
  done
}

# ------------------------------------------------------------------ answers (KEY=VALUE, parsed - never sourced)

zs_is_secret_key() { [[ "$ZS_SECRET_KEYS" == *" $1 "* ]]; }
zs_is_answer_key() { local k; for k in "${ZS_ANSWER_KEYS[@]}" ACCEPT_WARNINGS; do [[ "$k" == "$1" ]] && return 0; done; return 1; }

# zs_parse_kv_file FILE - sets ANS_<KEY> for every KEY=VALUE line. Rejects unknown keys and malformed lines
# (prints "line N: reason" for each and returns 1). Values may be wrapped in matching single or double quotes.
zs_parse_kv_file() {
  local file="$1" line key val n=0 bad=0
  while IFS= read -r line || [[ -n "$line" ]]; do
    n=$((n + 1))
    line="${line%$'\r'}"
    [[ -z "${line//[[:space:]]/}" || "$line" =~ ^[[:space:]]*# ]] && continue
    if ! [[ "$line" =~ ^[[:space:]]*([A-Z_][A-Z0-9_]*)[[:space:]]*=(.*)$ ]]; then
      echo "line ${n}: expected KEY=value"; bad=1; continue
    fi
    key="${BASH_REMATCH[1]}"
    val="${BASH_REMATCH[2]}"
    val="${val#"${val%%[![:space:]]*}"}"
    val="${val%"${val##*[![:space:]]}"}"
    if [[ ${#val} -ge 2 && ( ( "$val" == \"*\" ) || ( "$val" == \'*\' ) ) ]]; then val="${val:1:${#val}-2}"; fi
    if ! zs_is_answer_key "$key"; then echo "line ${n}: unknown setting '${key}'"; bad=1; continue; fi
    printf -v "ANS_${key}" '%s' "$val"
  done <"$file"
  return "$bad"
}

# /etc/zoom-stream is shared: the installer keeps its private state in installer/ (root only), and the dashboard keeps
# the vault's key cache there as master.key (root:dashboard, 640). The dashboard service user must therefore be able to
# ENTER the directory - with 700 it cannot read its key, the secret store stays locked and the Remote GUI cannot log in
# to x11vnc. Only the installer/ sub-directory is private.
zs_fix_etc_perms() {
  mkdir -p "$ZS_STATE_DIR"
  chmod 755 "$ZS_ETC_DIR" 2>/dev/null || true
  chmod 700 "$ZS_STATE_DIR" 2>/dev/null || true
}

zs_get() { local n="ANS_$1"; printf '%s' "${!n:-}"; }
zs_set() { printf -v "ANS_$1" '%s' "$2"; }

# Write every answer to the root-only answers file (atomic). Secrets are included so an interrupted install can
# resume; zs_scrub_saved_secrets removes them once the install has finished.
zs_save_answers() {
  local tmp key
  mkdir -p "$ZS_STATE_DIR"
  zs_fix_etc_perms
  tmp="$(mktemp "${ZS_STATE_DIR}/.answers.XXXXXX")"
  chmod 600 "$tmp"
  {
    echo "# Saved installer answers - root only. Secrets are removed when the installation completes."
    for key in "${ZS_ANSWER_KEYS[@]}"; do printf '%s=%s\n' "$key" "$(zs_get "$key")"; done
  } >"$tmp"
  mv -f "$tmp" "$ZS_ANSWERS_FILE"
}

zs_scrub_saved_secrets() {
  [[ -f "$ZS_ANSWERS_FILE" ]] || return 0
  local tmp key
  tmp="$(mktemp "${ZS_STATE_DIR}/.answers.XXXXXX")"
  chmod 600 "$tmp"
  {
    echo "# Saved installer answers (secrets removed after a successful install)."
    for key in "${ZS_ANSWER_KEYS[@]}"; do
      zs_is_secret_key "$key" && continue
      printf '%s=%s\n' "$key" "$(zs_get "$key")"
    done
  } >"$tmp"
  mv -f "$tmp" "$ZS_ANSWERS_FILE"
}

# How a secret is shown on the summary screen.
zs_mask() {
  case "$1" in
    "") printf '%s' "${2:-(not set)}" ;;
    auto) printf 'auto-generate' ;;
    *) printf '******** (%s characters)' "${#1}" ;;
  esac
}

# ------------------------------------------------------------------ step progress

zs_step_done() { [[ -f "$ZS_PROGRESS_FILE" ]] && grep -qxF "$1" "$ZS_PROGRESS_FILE"; }
zs_mark_done() { mkdir -p "$ZS_STATE_DIR"; echo "$1" >>"$ZS_PROGRESS_FILE"; chmod 600 "$ZS_PROGRESS_FILE"; }

# ------------------------------------------------------------------ robustness helpers

# zs_retry ATTEMPTS DELAY_SECONDS COMMAND...
zs_retry() {
  local _zr_max="$1" _zr_delay="$2" _zr_n=1
  shift 2
  until "$@"; do
    if (( _zr_n >= _zr_max )); then echo "[retry] giving up after ${_zr_n} attempts: $*"; return 1; fi
    echo "[retry] attempt ${_zr_n}/${_zr_max} failed: $* - retrying in ${_zr_delay}s"
    sleep "$_zr_delay"
    _zr_n=$((_zr_n + 1))
    _zr_delay=$((_zr_delay * 2))
  done
}

# apt-get that waits for the dpkg lock (unattended-upgrades often holds it on a fresh server) and retries.
zs_apt() {
  DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a NEEDRESTART_SUSPEND=1 \
    zs_retry 3 10 apt-get -o DPkg::Lock::Timeout=900 -o Dpkg::Options::=--force-confdef \
      -o Dpkg::Options::=--force-confold -y "$@"
}

# zs_download URL OUTPUT - official HTTPS sources only, with retries.
zs_download() {
  zs_retry 4 5 curl -fsSL --proto '=https' --tlsv1.2 --connect-timeout 20 --max-time 900 -o "$2" "$1"
}

zs_gen_password() {
  local p=""
  while (( ${#p} < 24 )); do p+="$(head -c 64 /dev/urandom | tr -dc 'A-Za-z0-9')"; done
  printf '%s' "${p:0:24}"
}

zs_public_ip() {
  local ip url
  for url in https://api.ipify.org https://ifconfig.me https://icanhazip.com; do
    ip="$(curl -4 -fsS --max-time 6 "$url" 2>/dev/null | tr -d '[:space:]')" || true
    if [[ "$ip" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then printf '%s' "$ip"; return 0; fi
  done
  return 1
}

# zs_tcp_reachable HOST PORT [TIMEOUT]
zs_tcp_reachable() { timeout "${3:-6}" bash -c "exec 3<>/dev/tcp/$1/$2" 2>/dev/null; }

zs_user_systemctl() {
  local uid
  uid="$(id -u "$ZS_SVC_USER")"
  sudo -u "$ZS_SVC_USER" XDG_RUNTIME_DIR="/run/user/${uid}" systemctl --user "$@"
}

# zs_dashboard_selfcheck [--require-vnc] - run the dashboard's read-only self-test as its service user.
zs_dashboard_selfcheck() {
  (cd "$ZS_DASH_HOME/app" && sudo -u "$ZS_DASH_USER" "$ZS_DASH_HOME/venv/bin/python" -m app.cli selfcheck "$@")
}

# zs_capture_check - one frame through the encoder's capture settings, as the service user (scripts/capture-check.sh).
zs_capture_check() {
  local uid
  uid="$(id -u "$ZS_SVC_USER")"
  sudo -u "$ZS_SVC_USER" XDG_RUNTIME_DIR="/run/user/${uid}" "$ZS_APP_DIR/scripts/capture-check.sh"
}

# True when the broadcast encoder is up. Unknown counts as "up": refusing is always safer than touching a live class.
zs_encoder_running() {
  id "$ZS_SVC_USER" &>/dev/null || return 1
  local state
  state="$(zs_user_systemctl is-active ffmpeg-stream.service 2>/dev/null || true)"
  case "$state" in inactive|failed|"") return 1 ;; *) return 0 ;; esac
}

# zs_git_is_ancestor REPO OLDER NEWER - true when commit OLDER is NEWER itself or part of its history.
zs_git_is_ancestor() { git -C "$1" merge-base --is-ancestor "$2" "$3" 2>/dev/null; }

# ------------------------------------------------------------------ failure explanations

# zs_explain_failure LOGFILE - one plain-language sentence for the most likely cause.
zs_explain_failure() {
  local tail_txt
  tail_txt="$(tail -n 80 "$1" 2>/dev/null || true)"
  case "$tail_txt" in
    *"No space left on device"*)
      echo "The disk is full. Free some space (df -h) or enlarge the disk, then run the installer again." ;;
    *"Could not get lock"*|*"Unable to acquire the dpkg frontend lock"*)
      echo "Another program is still installing packages (often the automatic security updates). Wait a few minutes and run the installer again." ;;
    *"dpkg was interrupted"*)
      echo "A previous package installation was interrupted. Run 'sudo dpkg --configure -a', then run the installer again." ;;
    *"Temporary failure resolving"*|*"Could not resolve host"*|*"Name or service not known"*)
      echo "The server could not look up a download address (DNS). Check its network/DNS settings, then run the installer again." ;;
    *"curl: (7)"*|*"curl: (28)"*|*"curl: (35)"*|*"curl: (56)"*|*"Connection timed out"*|*"Failed to connect"*|*"Network is unreachable"*|*"Connection reset"*)
      echo "A download failed because the network connection dropped or timed out. Run the installer again - it resumes and retries the download." ;;
    *"curl: (22)"*)
      echo "A download server answered with an error (the file may have moved). Run the installer again later; if it keeps failing, report it as an issue." ;;
    *"Unable to locate package"*|*"has no installation candidate"*)
      echo "A required package is not available from this server's package sources. Make sure the standard Ubuntu 24.04 'main' and 'universe' repositories are enabled." ;;
    *"failed validation"*|*"syntax error"*" sudoers"*)
      echo "A generated system configuration file was rejected and NOT installed. Please report this as an issue with the log." ;;
    *"ERROR: Could not find a version"*|*"pip"*"ConnectionError"*|*"ReadTimeoutError"*)
      echo "Python packages could not be downloaded from PyPI. Check the network and run the installer again." ;;
    *)
      echo "The last command of this step failed. The lines below (and the full log) show what happened." ;;
  esac
}
