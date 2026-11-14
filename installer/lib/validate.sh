#!/usr/bin/env bash
# Input validators shared by the interactive questions and --config mode.
#
# Every validator takes the value as $1, returns 0 when it is acceptable, and otherwise prints ONE plain-language
# reason on stdout and returns 1. They never print the value itself, so they are safe to use on passwords and keys.
# Pure functions: no files, no network, no global state (except the read-only constants below).

ZS_DEFAULT_RTMP_URL="rtmps://a.rtmps.youtube.com:443/live2"
ZS_MIN_PASSWORD_LEN=12
# Must match dashboard/app/cli.py USERNAME_RE.
ZS_USERNAME_RE='^[A-Za-z0-9][A-Za-z0-9._@-]{0,63}$'
# Must match scripts/stream.sh and dashboard/app/config.py RTMP_URL_RE.
ZS_RTMP_RE='^rtmps?://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~-]+)*/?$'
ZS_DOMAIN_RE='^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$'
ZS_EMAIL_RE='^[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+\.)+[A-Za-z]{2,63}$'

zs_valid_domain() {
  local v="${1,,}"
  if [[ "$v" == "none" ]]; then return 0; fi
  if [[ -z "$v" ]]; then echo "Enter a domain name such as stream.example.com, or 'none'."; return 1; fi
  if (( ${#v} > 253 )) || ! [[ "$v" =~ $ZS_DOMAIN_RE ]]; then
    echo "That is not a valid domain name. Use letters, digits, dots and hyphens, e.g. stream.example.com (no http://, no path)."
    return 1
  fi
}

zs_valid_email() {
  if [[ "$1" =~ $ZS_EMAIL_RE ]] && (( ${#1} <= 254 )); then return 0; fi
  echo "That is not a valid e-mail address (example: you@example.com)."
  return 1
}

zs_valid_username() {
  if [[ "$1" =~ $ZS_USERNAME_RE ]]; then return 0; fi
  echo "Use 1-64 characters: letters, digits, '.', '_', '@' or '-', starting with a letter or digit."
  return 1
}

# zs_valid_password PASSWORD [USERNAME]
zs_valid_password() {
  local pw="$1" user="${2:-}" classes=0
  if [[ "$pw" == *$'\n'* || "$pw" == *$'\r'* ]]; then echo "The password cannot contain line breaks."; return 1; fi
  if (( ${#pw} < ZS_MIN_PASSWORD_LEN )); then
    echo "Too short: use at least ${ZS_MIN_PASSWORD_LEN} characters (a few random words work well)."
    return 1
  fi
  if (( ${#pw} > 128 )); then echo "Too long: use at most 128 characters."; return 1; fi
  if [[ -n "$user" && "${pw,,}" == *"${user,,}"* ]]; then echo "The password must not contain the username."; return 1; fi
  [[ "$pw" =~ [a-z] ]] && classes=$((classes + 1))
  [[ "$pw" =~ [A-Z] ]] && classes=$((classes + 1))
  [[ "$pw" =~ [0-9] ]] && classes=$((classes + 1))
  [[ "$pw" =~ [^A-Za-z0-9] ]] && classes=$((classes + 1))
  if (( ${#pw} < 16 && classes < 3 )); then
    echo "Too weak: mix at least three of lowercase, uppercase, digits and symbols - or use 16+ characters."
    return 1
  fi
  local lower="${pw,,}" weak
  for weak in password passw0rd qwerty 123456 letmein welcome admin zoom youtube stream; do
    if [[ "$lower" == *"$weak"* ]] && (( ${#pw} < 20 )); then
      echo "Too guessable: it contains a common word ('${weak}'). Pick something less predictable."
      return 1
    fi
  done
}

zs_valid_rtmp_url() {
  if [[ "$1" =~ $ZS_RTMP_RE ]] && [[ "$1" != *"?"* ]]; then return 0; fi
  echo "Enter the server URL only, starting with rtmp:// or rtmps:// (for YouTube: ${ZS_DEFAULT_RTMP_URL}). Do not include the stream key."
  return 1
}

# Empty means "set it later in the dashboard".
zs_valid_stream_key() {
  if [[ -z "$1" ]]; then return 0; fi
  if [[ "$1" =~ ^[A-Za-z0-9_-]{8,128}$ ]]; then return 0; fi
  echo "A YouTube stream key looks like abcd-efgh-ijkl-mnop-qrst (letters, digits, '-' and '_'). Leave it empty to set it later."
  return 1
}

zs_valid_bot_name() {
  local v="$1"
  if [[ -z "$v" || ${#v} -gt 64 ]]; then echo "Use 1-64 characters."; return 1; fi
  if [[ "$v" =~ [\"\'\\\$\`] || "$v" == *$'\n'* || "$v" == *$'\r'* || "$v" =~ [[:cntrl:]] ]]; then
    echo "Do not use quotes, backslashes, \$, backticks or control characters."
    return 1
  fi
}

zs_valid_preset() {
  case "$1" in 720p|1080p) return 0 ;; esac
  echo "Choose 720p or 1080p."
  return 1
}

# zs_valid_timezone NAME [ZONEINFO_DIR]
zs_valid_timezone() {
  local tz="$1" dir="${2:-${ZS_ZONEINFO_DIR:-/usr/share/zoneinfo}}"
  if [[ "$tz" =~ ^[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+){0,2}$ && -f "$dir/$tz" ]]; then return 0; fi
  echo "Unknown timezone. Use a name like UTC, Europe/London or Asia/Colombo (list them with: timedatectl list-timezones)."
  return 1
}

zs_valid_yes_no() {
  case "${1,,}" in yes|no) return 0 ;; esac
  echo "Answer yes or no."
  return 1
}

zs_valid_ssl_mode() {
  case "$1" in letsencrypt|self-signed) return 0 ;; esac
  echo "Choose letsencrypt or self-signed."
  return 1
}

# "auto" (or empty) asks the installer to generate the password.
zs_valid_optional_password() {
  if [[ -z "$1" || "$1" == "auto" ]]; then return 0; fi
  zs_valid_password "$1"
}

# Recommended output preset for a CPU count: 1080p is only offered as the default on 8+ cores.
zs_recommended_preset() {
  if (( ${1:-0} >= 8 )); then echo 1080p; else echo 720p; fi
}
