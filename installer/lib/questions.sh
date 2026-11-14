#!/usr/bin/env bash
# The installer's questions, the summary screen, and --config validation.
# Each question explains itself, shows its default in [brackets], validates the answer and asks again on bad input.

ZS_CPU_CORES="$(nproc 2>/dev/null || echo 1)"
ZS_PUBLIC_IP="${ZS_PUBLIC_IP:-}"

_q_header() { printf '\n%s%s%s\n' "$C_BOLD" "$1" "$C_RESET"; shift; local l; for l in "$@"; do printf '  %s%s%s\n' "$C_DIM" "$l" "$C_RESET"; done; }

# _q_ask KEY DEFAULT VALIDATOR PROMPT  (visible input)
_q_ask() {
  local key="$1" def="$2" validator="$3" prompt="$4" v msg
  while true; do
    zs_read v "  ${prompt}${def:+ [${def}]}: "
    [[ -z "$v" ]] && v="$def"
    if msg="$("$validator" "$v")"; then zs_set "$key" "$v"; return 0; fi
    printf '    %s%s%s\n' "$C_YELLOW" "$msg" "$C_RESET"
  done
}

_q_public_ip() {
  [[ -n "$ZS_PUBLIC_IP" ]] && return 0
  ZS_PUBLIC_IP="$(zs_public_ip || true)"
  if [[ -z "$ZS_PUBLIC_IP" ]]; then
    zs_warn "This server's public IP address could not be detected automatically."
    zs_hint "Find it in your provider's control panel (often called 'Public IPv4' or 'Elastic IP')."
    local v
    while true; do
      zs_read v "  Public IP address of this server (Enter = skip): "
      [[ -z "$v" ]] && return 0
      if [[ "$v" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then ZS_PUBLIC_IP="$v"; return 0; fi
      printf '    %sThat is not an IPv4 address (example: 203.0.113.7).%s\n' "$C_YELLOW" "$C_RESET"
    done
  fi
}

q_domain() {
  _q_public_ip
  _q_header "Domain name (subdomain)"     "The address you will open the dashboard at - a subdomain you control, e.g. stream.example.com."     "With a domain you get a free, browser-trusted HTTPS certificate (Let's Encrypt), renewed automatically."     "This server's public IP address: ${ZS_PUBLIC_IP:-unknown}"     "Type 'none' to use the IP address only (self-signed certificate, browser warning). You can add a domain later."
  local cur v rc
  cur="$(zs_get DOMAIN)"
  while true; do
    _q_ask DOMAIN "${cur:-none}" zs_valid_domain "Domain name, or 'none'"
    v="$(zs_get DOMAIN)"; v="${v,,}"; zs_set DOMAIN "$v"
    if [[ "$v" == "none" ]]; then
      zs_set SSL_MODE "self-signed"; zs_set LE_EMAIL ""
      zs_ip_only_warning "$ZS_PUBLIC_IP"
      return 0
    fi
    rc=0
    zs_dns_gate "$v" "$ZS_PUBLIC_IP" || rc=$?
    case "$rc" in
      0) return 0 ;;
      3) zs_set DOMAIN "none"; zs_set SSL_MODE "self-signed"; zs_set LE_EMAIL ""
         zs_ip_only_warning "$ZS_PUBLIC_IP"; return 0 ;;
      *) cur="$v" ;;
    esac
  done
}

q_ssl() {
  [[ "$(zs_get DOMAIN)" == "none" ]] && { zs_set SSL_MODE "self-signed"; zs_set LE_EMAIL ""; return 0; }
  _q_header "HTTPS certificate" \
    "letsencrypt  - a free, browser-trusted certificate, renewed automatically (needs ports 80 and 443 open)." \
    "self-signed  - skip Let's Encrypt; the browser shows a one-time warning."
  local cur
  cur="$(zs_get SSL_MODE)"; [[ "$cur" == "letsencrypt" || "$cur" == "self-signed" ]] || cur="letsencrypt"
  _q_ask SSL_MODE "$cur" zs_valid_ssl_mode "letsencrypt or self-signed"
  if [[ "$(zs_get SSL_MODE)" == "letsencrypt" ]]; then
    _q_header "E-mail for Let's Encrypt" "Used only by Let's Encrypt for certificate expiry notices."
    _q_ask LE_EMAIL "$(zs_get LE_EMAIL)" zs_valid_email "E-mail address"
  else
    zs_set LE_EMAIL ""
  fi
}

q_admin_username() {
  _q_header "Dashboard admin username" "You will sign in to the dashboard with this name."
  local cur; cur="$(zs_get ADMIN_USERNAME)"
  _q_ask ADMIN_USERNAME "${cur:-admin}" zs_valid_username "Username"
}

q_admin_password() {
  _q_header "Dashboard admin password" \
    "At least ${ZS_MIN_PASSWORD_LEN} characters, mixing letters, digits and symbols (or 16+ characters). It is not shown while you type."
  local p1 p2 msg
  while true; do
    zs_read_secret p1 "  Password: "
    if ! msg="$(zs_valid_password "$p1" "$(zs_get ADMIN_USERNAME)")"; then printf '    %s%s%s\n' "$C_YELLOW" "$msg" "$C_RESET"; continue; fi
    zs_read_secret p2 "  Type it again: "
    if [[ "$p1" != "$p2" ]]; then printf '    %sThe two passwords did not match - try again.%s\n' "$C_YELLOW" "$C_RESET"; continue; fi
    zs_set ADMIN_PASSWORD "$p1"
    return 0
  done
}

q_youtube() {
  _q_header "YouTube ingest server" \
    "From YouTube Studio > Go live > Stream > 'Stream URL'. The default is YouTube's secure (RTMPS) server."
  local cur
  cur="$(zs_get YT_RTMP_URL)"
  _q_ask YT_RTMP_URL "${cur:-$ZS_DEFAULT_RTMP_URL}" zs_valid_rtmp_url "Stream URL"
  _q_header "YouTube stream key" \
    "From YouTube Studio > Go live > Stream > 'Stream key'. It is not shown while you type." \
    "Press Enter to skip and set it later in the dashboard (Live Studio)."
  local k msg
  while true; do
    zs_read_secret k "  Stream key (Enter = set later): "
    if msg="$(zs_valid_stream_key "$k")"; then zs_set YT_STREAM_KEY "$k"; return 0; fi
    printf '    %s%s%s\n' "$C_YELLOW" "$msg" "$C_RESET"
  done
}

q_bot_name() {
  _q_header "Zoom display name" "The name the bot shows to other participants when it joins a meeting."
  local cur; cur="$(zs_get BOT_NAME)"
  _q_ask BOT_NAME "${cur:-Stream Bot}" zs_valid_bot_name "Display name"
}

q_preset() {
  local rec; rec="$(zs_recommended_preset "$ZS_CPU_CORES")"
  _q_header "Output quality" \
    "720p  - 1280x720, 30 fps, 3000 kbps. Recommended for 4-7 CPU cores." \
    "1080p - 1920x1080, 30 fps, 6000 kbps. Needs about 8+ cores; not verified on 4 cores with a live Zoom meeting." \
    "This server has ${ZS_CPU_CORES} cores, so the recommendation is ${rec}. You can change it later on the Output page."
  local cur; cur="$(zs_get OUTPUT_PRESET)"
  _q_ask OUTPUT_PRESET "${cur:-$rec}" zs_valid_preset "720p or 1080p"
  if [[ "$(zs_get OUTPUT_PRESET)" == "1080p" ]] && (( ZS_CPU_CORES < 8 )); then
    zs_warn "1080p on ${ZS_CPU_CORES} cores may drop frames. Measure with a test recording before going live."
  fi
}

_q_detect_tz() {
  local tz
  tz="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
  [[ -z "$tz" ]] && tz="UTC"
  printf '%s' "$tz"
}

q_timezone() {
  _q_header "Timezone" \
    "Scheduled joins, go-live and stop times are entered and shown in this timezone (and the server clock uses it)."
  local cur; cur="$(zs_get TIMEZONE)"
  _q_ask TIMEZONE "${cur:-$(_q_detect_tz)}" zs_valid_timezone "Timezone"
}

q_vnc_password() {
  _q_header "Remote screen (VNC) password" \
    "Protects the bot's remote desktop (used inside the dashboard). Press Enter to generate a strong one" \
    "(it is saved for you in ${ZS_CREDENTIALS_FILE}, readable by root only)."
  local p1 p2 msg
  while true; do
    zs_read_secret p1 "  VNC password (Enter = auto-generate): "
    if [[ -z "$p1" ]]; then zs_set VNC_PASSWORD auto; return 0; fi
    if ! msg="$(zs_valid_password "$p1")"; then printf '    %s%s%s\n' "$C_YELLOW" "$msg" "$C_RESET"; continue; fi
    zs_read_secret p2 "  Type it again: "
    [[ "$p1" == "$p2" ]] || { printf '    %sThe two passwords did not match - try again.%s\n' "$C_YELLOW" "$C_RESET"; continue; }
    zs_set VNC_PASSWORD "$p1"; return 0
  done
}

q_master_password() {
  _q_header "Master encryption password" \
    "Encrypts the dashboard's secret store. The server unlocks it automatically at boot, so you only need it" \
    "for recovery or rotation. Press Enter to generate one (saved in ${ZS_CREDENTIALS_FILE}, root only)."
  local p1 p2 msg
  while true; do
    zs_read_secret p1 "  Master password (Enter = auto-generate): "
    if [[ -z "$p1" ]]; then zs_set MASTER_PASSWORD auto; return 0; fi
    if ! msg="$(zs_valid_password "$p1")"; then printf '    %s%s%s\n' "$C_YELLOW" "$msg" "$C_RESET"; continue; fi
    zs_read_secret p2 "  Type it again: "
    [[ "$p1" == "$p2" ]] || { printf '    %sThe two passwords did not match - try again.%s\n' "$C_YELLOW" "$C_RESET"; continue; }
    zs_set MASTER_PASSWORD "$p1"; return 0
  done
}

_q_yes_no() {
  local key="$1" def="$2" v
  while true; do
    zs_read v "  yes or no [${def}]: "
    v="${v,,}"; [[ -z "$v" ]] && v="$def"
    case "$v" in y|yes) zs_set "$key" yes; return 0 ;; n|no) zs_set "$key" no; return 0 ;; esac
    printf '    %sAnswer yes or no.%s\n' "$C_YELLOW" "$C_RESET"
  done
}

q_firewall() {
  _q_header "Firewall" \
    "Turn on Ubuntu's firewall (ufw) and allow only SSH, HTTPS (443) and - for Let's Encrypt - HTTP (80)." \
    "If your provider also has a cloud firewall / security group, open the same ports there."
  local cur; cur="$(zs_get FIREWALL)"
  _q_yes_no FIREWALL "${cur:-yes}"
}

q_auto_updates() {
  _q_header "Automatic security updates" "Install Ubuntu security updates automatically (unattended-upgrades). Recommended."
  local cur; cur="$(zs_get AUTO_UPDATES)"
  _q_yes_no AUTO_UPDATES "${cur:-yes}"
}

ZS_QUESTIONS=(q_domain q_ssl q_admin_username q_admin_password q_youtube q_bot_name q_preset q_timezone
              q_vnc_password q_master_password q_firewall q_auto_updates)

zs_ask_all() { local q; for q in "${ZS_QUESTIONS[@]}"; do "$q"; done; }

zs_print_summary() {
  local dom ssl
  dom="$(zs_get DOMAIN)"; ssl="$(zs_get SSL_MODE)"
  zs_title "Summary - please check before installing"
  printf '   1) Domain              %s\n' "$([[ "$dom" == none ]] && echo "none (use the server IP)" || echo "$dom")"
  if [[ "$ssl" == letsencrypt && "$(zs_get LE_STAGING)" == yes ]]; then
    printf '   2) HTTPS certificate   %s\n' "Let's Encrypt STAGING - test certificate, NOT browser-trusted ($(zs_get LE_EMAIL))"
  elif [[ "$ssl" == letsencrypt ]]; then
    printf '   2) HTTPS certificate   %s\n' "Let's Encrypt ($(zs_get LE_EMAIL)), renewed automatically"
  elif [[ "$dom" == none ]]; then
    printf '   2) HTTPS certificate   %s\n' "self-signed - no domain, browsers show a warning"
  else
    printf '   2) HTTPS certificate   %s\n' "self-signed - browsers show a warning"
  fi
  printf '   3) Admin username      %s\n' "$(zs_get ADMIN_USERNAME)"
  printf '   4) Admin password      %s\n' "$(zs_mask "$(zs_get ADMIN_PASSWORD)")"
  printf '   5) YouTube             %s, key: %s\n' "$(zs_get YT_RTMP_URL)" "$(zs_mask "$(zs_get YT_STREAM_KEY)" "set later in the dashboard")"
  printf '   6) Zoom display name   %s\n' "$(zs_get BOT_NAME)"
  printf '   7) Output quality      %s\n' "$(zs_get OUTPUT_PRESET)"
  printf '   8) Timezone            %s\n' "$(zs_get TIMEZONE)"
  printf '   9) VNC password        %s\n' "$(zs_mask "$(zs_get VNC_PASSWORD)")"
  printf '  10) Master password     %s\n' "$(zs_mask "$(zs_get MASTER_PASSWORD)")"
  printf '  11) Firewall (ufw)      %s\n' "$(zs_get FIREWALL)"
  printf '  12) Security updates    %s\n' "$(zs_get AUTO_UPDATES)"
}

# Returns 0 = install, 1 = cancelled.
zs_summary_loop() {
  local choice n
  while true; do
    zs_print_summary
    echo
    zs_read choice "  [Enter] install   [1-12] change an answer   [q] cancel: "
    case "${choice,,}" in
      ""|y|yes|i|install) return 0 ;;
      q|quit|c|cancel) return 1 ;;
    esac
    if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= 12 )); then
      n="$choice"
      case "$n" in
        1) q_domain; q_ssl ;;
        2) q_ssl ;;
        3) q_admin_username ;;
        4) q_admin_password ;;
        5) q_youtube ;;
        6) q_bot_name ;;
        7) q_preset ;;
        8) q_timezone ;;
        9) q_vnc_password ;;
        10) q_master_password ;;
        11) q_firewall ;;
        12) q_auto_updates ;;
      esac
    else
      echo "    Press Enter, a number from 1 to 12, or q."
    fi
  done
}

# --config: fill defaults and validate every answer. Prints every problem; returns 1 if any.
zs_validate_config_answers() {
  local bad=0 msg v
  _cv() { local key="$1" val="$2"; shift 2; if ! msg="$("$@" "$val")"; then echo "  ${key}: ${msg}"; bad=1; fi; }
  [[ -z "$(zs_get DOMAIN)" ]] && zs_set DOMAIN none
  zs_set DOMAIN "$(zs_get DOMAIN | tr '[:upper:]' '[:lower:]')"
  _cv DOMAIN "$(zs_get DOMAIN)" zs_valid_domain
  if [[ "$(zs_get DOMAIN)" == "none" ]]; then
    zs_set SSL_MODE self-signed
  else
    [[ -z "$(zs_get SSL_MODE)" ]] && zs_set SSL_MODE letsencrypt
    _cv SSL_MODE "$(zs_get SSL_MODE)" zs_valid_ssl_mode
    [[ "$(zs_get SSL_MODE)" == "letsencrypt" ]] && _cv LE_EMAIL "$(zs_get LE_EMAIL)" zs_valid_email
  fi
  [[ "$(zs_get SSL_MODE)" == "letsencrypt" ]] || zs_set LE_EMAIL ""
  [[ -z "$(zs_get LE_STAGING)" ]] && zs_set LE_STAGING no
  zs_set LE_STAGING "$(zs_get LE_STAGING | tr '[:upper:]' '[:lower:]')"
  _cv LE_STAGING "$(zs_get LE_STAGING)" zs_valid_yes_no
  [[ -z "$(zs_get ADMIN_USERNAME)" ]] && zs_set ADMIN_USERNAME admin
  _cv ADMIN_USERNAME "$(zs_get ADMIN_USERNAME)" zs_valid_username
  v="$(zs_get ADMIN_PASSWORD)"
  if [[ -z "$v" ]]; then echo "  ADMIN_PASSWORD: required"; bad=1
  elif ! msg="$(zs_valid_password "$v" "$(zs_get ADMIN_USERNAME)")"; then echo "  ADMIN_PASSWORD: ${msg}"; bad=1
  fi
  [[ -z "$(zs_get YT_RTMP_URL)" ]] && zs_set YT_RTMP_URL "$ZS_DEFAULT_RTMP_URL"
  _cv YT_RTMP_URL "$(zs_get YT_RTMP_URL)" zs_valid_rtmp_url
  _cv YT_STREAM_KEY "$(zs_get YT_STREAM_KEY)" zs_valid_stream_key
  [[ -z "$(zs_get BOT_NAME)" ]] && zs_set BOT_NAME "Stream Bot"
  _cv BOT_NAME "$(zs_get BOT_NAME)" zs_valid_bot_name
  [[ -z "$(zs_get OUTPUT_PRESET)" ]] && zs_set OUTPUT_PRESET "$(zs_recommended_preset "$ZS_CPU_CORES")"
  _cv OUTPUT_PRESET "$(zs_get OUTPUT_PRESET)" zs_valid_preset
  [[ -z "$(zs_get TIMEZONE)" ]] && zs_set TIMEZONE "$(_q_detect_tz)"
  _cv TIMEZONE "$(zs_get TIMEZONE)" zs_valid_timezone
  [[ -z "$(zs_get VNC_PASSWORD)" ]] && zs_set VNC_PASSWORD auto
  _cv VNC_PASSWORD "$(zs_get VNC_PASSWORD)" zs_valid_optional_password
  [[ -z "$(zs_get MASTER_PASSWORD)" ]] && zs_set MASTER_PASSWORD auto
  _cv MASTER_PASSWORD "$(zs_get MASTER_PASSWORD)" zs_valid_optional_password
  local k
  for k in FIREWALL AUTO_UPDATES; do
    [[ -z "$(zs_get "$k")" ]] && zs_set "$k" yes
    zs_set "$k" "$(zs_get "$k" | tr '[:upper:]' '[:lower:]')"
    _cv "$k" "$(zs_get "$k")" zs_valid_yes_no
  done
  [[ -z "$(zs_get ACCEPT_WARNINGS)" ]] && zs_set ACCEPT_WARNINGS no
  _cv ACCEPT_WARNINGS "$(zs_get ACCEPT_WARNINGS | tr '[:upper:]' '[:lower:]')" zs_valid_yes_no
  unset -f _cv
  return "$bad"
}

# --config DNS check: a mismatch is a warning that needs ACCEPT_WARNINGS=yes.
zs_config_dns_check() {
  local d
  d="$(zs_get DOMAIN)"
  [[ "$d" == "none" ]] && { zs_ip_only_warning "${ZS_PUBLIC_IP:-$(zs_public_ip || true)}"; return 0; }
  [[ -z "$ZS_PUBLIC_IP" ]] && ZS_PUBLIC_IP="$(zs_public_ip || true)"
  if zs_dns_check "$d" "$ZS_PUBLIC_IP"; then zs_dns_explain "$d" "$ZS_PUBLIC_IP"; return 0; fi
  zs_dns_instructions "$d" "$ZS_PUBLIC_IP"
  zs_dns_explain "$d" "$ZS_PUBLIC_IP"
  zs_hint "Let's Encrypt cannot issue a certificate until ${d} points here."
  PF_SOFT=$((PF_SOFT + 1))
  return 1
}
