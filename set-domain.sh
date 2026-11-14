#!/usr/bin/env bash
# Add, change or remove the dashboard's domain (usually a subdomain) and its HTTPS certificate after installation.
#
#   sudo bash set-domain.sh stream.example.com [--email you@example.com]   use this domain, Let's Encrypt certificate
#   sudo bash set-domain.sh --none                                         back to IP address only (self-signed)
#   sudo bash set-domain.sh --check                                        re-check DNS, ports and HTTPS as configured
#
#   Options:
#     --email ADDR   e-mail for Let's Encrypt expiry notices (default: the one given before, if any)
#     --staging      Let's Encrypt STAGING: untrusted test certificates with generous rate limits (repeated tests)
#     --yes          no questions: continue even if DNS does not point here yet
#     --no-color     plain output
#     -h, --help     this help
#
# Steps: show the DNS record to create -> check that DNS points here (offers to wait while it propagates) -> allow
# ports 80/443 in ufw if ufw is on -> write and validate the Caddy config (scripts/setup-https.sh) -> wait for the
# certificate -> check the site loads over HTTPS. Caddy renews the certificate automatically from then on.
# Only Caddy is reloaded: the dashboard and the streaming services are not touched, so it is safe during a broadcast.
set -Eeuo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=installer/lib/common.sh
source "$SRC/installer/lib/common.sh"
# shellcheck source=installer/lib/validate.sh
source "$SRC/installer/lib/validate.sh"
# shellcheck source=installer/lib/domain.sh
source "$SRC/installer/lib/domain.sh"

DOMAIN="" EMAIL="" MODE="" STAGING=0 YES=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --none) MODE="none" ;;
    --check) MODE="check" ;;
    --email) EMAIL="${2:-}"; shift ;;
    --staging) STAGING=1 ;;
    --yes|-y) YES=1 ;;
    --no-color) export NO_COLOR=1; zs_init_colors ;;
    -h|--help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) zs_die "Unknown option: $1 (see --help)" ;;
    *) [[ -z "$DOMAIN" ]] || zs_die "Only one domain name, please (see --help)."; DOMAIN="$1"; MODE="${MODE:-domain}" ;;
  esac
  shift
done
[[ -n "$MODE" ]] || { sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
if [[ "$MODE" == "domain" ]]; then
  DOMAIN="${DOMAIN,,}"
  if [[ "$DOMAIN" == "none" ]]; then MODE="none"
  elif ! msg="$(zs_valid_domain "$DOMAIN")"; then zs_die "$msg"; fi
fi
if [[ -n "$EMAIL" ]] && ! msg="$(zs_valid_email "$EMAIL")"; then zs_die "$msg"; fi

[[ $EUID -eq 0 ]] || zs_die "Run as root: sudo bash $0 ..."
[[ -f "$ZS_INSTALL_CONF" ]] || zs_die "No completed installation found ($ZS_INSTALL_CONF). Run install.sh first."

conf() { sed -n "s/^$1=//p" "$ZS_INSTALL_CONF" 2>/dev/null | tail -n1; }

# set_setting FILE KEY VALUE - replace or append KEY=VALUE (atomic).
set_setting() {
  local file="$1" key="$2" val="$3" tmp
  [[ -f "$file" ]] || return 0
  tmp="$(mktemp "${file}.XXXXXX")"
  grep -v "^${key}=" "$file" >"$tmp" || true
  printf '%s=%s\n' "$key" "$val" >>"$tmp"
  chmod --reference="$file" "$tmp"
  mv -f "$tmp" "$file"
}

save_choice() {   # save_choice DOMAIN SSL_MODE EMAIL STAGING
  local f
  for f in "$ZS_INSTALL_CONF" "$ZS_ANSWERS_FILE"; do
    set_setting "$f" DOMAIN "$1"
    set_setting "$f" SSL_MODE "$2"
    set_setting "$f" LE_EMAIL "$3"
    set_setting "$f" LE_STAGING "$4"
  done
}

ZS_PUBLIC_IP="$(zs_public_ip || true)"
zs_title "Domain and HTTPS"
zs_info "This server's public IP address: ${ZS_PUBLIC_IP:-unknown (could not be detected)}"

# ------------------------------------------------------------------ --check
if [[ "$MODE" == "check" ]]; then
  cur="$(conf DOMAIN)"; ssl="$(conf SSL_MODE)"; stg="$(conf LE_STAGING)"
  rc=0
  if [[ -z "$cur" || "$cur" == "none" || "$ssl" != "letsencrypt" ]]; then
    zs_info "Configured: no domain (IP address only, self-signed certificate)."
    zs_ports_check "$ZS_PUBLIC_IP" self-signed || rc=1
    zs_https_verify_ip "$ZS_PUBLIC_IP" || rc=1
    zs_ip_only_warning "$ZS_PUBLIC_IP"
  else
    note=""; [[ "$stg" == yes ]] && note=" (Let's Encrypt STAGING - test certificate)"
    zs_info "Configured: ${cur}${note}"
    if ! zs_dns_check "$cur" "$ZS_PUBLIC_IP"; then rc=1; zs_dns_instructions "$cur" "$ZS_PUBLIC_IP"; fi
    zs_dns_explain "$cur" "$ZS_PUBLIC_IP"
    zs_ports_check "$ZS_PUBLIC_IP" letsencrypt || rc=1
    ZS_HTTPS_CHECK_ONLY=1 ZS_HTTPS_WAIT="${ZS_HTTPS_WAIT:-30}" zs_https_verify "$cur" "${stg:-no}" || rc=1
  fi
  exit "$rc"
fi

# ------------------------------------------------------------------ --none
if [[ "$MODE" == "none" ]]; then
  bash "$SRC/scripts/setup-https.sh"
  save_choice none self-signed "" no
  zs_https_verify_ip "$ZS_PUBLIC_IP" || true
  zs_ip_only_warning "$ZS_PUBLIC_IP"
  zs_ok "Dashboard: https://${ZS_PUBLIC_IP:-<server-ip>}"
  exit 0
fi

# ------------------------------------------------------------------ a domain
[[ -n "$EMAIL" ]] || EMAIL="$(conf LE_EMAIL)"
if [[ -z "$EMAIL" ]] && (( ! YES )) && zs_have_tty; then
  zs_read EMAIL "  E-mail for Let's Encrypt expiry notices (Enter = none): "
  while [[ -n "$EMAIL" ]] && ! msg="$(zs_valid_email "$EMAIL")"; do
    printf '    %s\n' "$msg"; zs_read EMAIL "  E-mail (Enter = none): "
  done
fi

if (( YES )) || ! zs_have_tty; then
  if zs_dns_check "$DOMAIN" "$ZS_PUBLIC_IP"; then
    zs_dns_explain "$DOMAIN" "$ZS_PUBLIC_IP"
  else
    zs_dns_instructions "$DOMAIN" "$ZS_PUBLIC_IP"
    zs_dns_explain "$DOMAIN" "$ZS_PUBLIC_IP"
    (( YES )) || zs_die "DNS does not point here yet - nothing was changed. Fix the record and run this again (or add --yes)."
    zs_warn "--yes: continuing anyway; Caddy keeps retrying the certificate until DNS points here."
  fi
else
  gate=0
  zs_dns_gate "$DOMAIN" "$ZS_PUBLIC_IP" || gate=$?
  case "$gate" in
    0) ;;
    2) zs_say "Run it again with the other name:  sudo bash $0 <name>"; exit 1 ;;
    3) zs_say "Nothing was changed. To switch to IP-only:  sudo bash $0 --none"; exit 1 ;;
  esac
fi

if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow 80/tcp >/dev/null && ufw allow 443/tcp >/dev/null && zs_ok "Firewall (ufw): ports 80 and 443 allowed"
fi

args=(--domain "$DOMAIN")
[[ -n "$EMAIL" ]] && args+=(--email "$EMAIL")
(( STAGING )) && args+=(--staging)
bash "$SRC/scripts/setup-https.sh" "${args[@]}"
grep -q "^${DOMAIN//./\\.} {" /etc/caddy/Caddyfile 2>/dev/null || zs_die "Caddy was not configured for ${DOMAIN} (see the message above). Nothing else was changed."
stg=no; (( STAGING )) && stg=yes
save_choice "$DOMAIN" letsencrypt "$EMAIL" "$stg"

zs_title "Checking HTTPS"
rc=0
zs_ports_check "$ZS_PUBLIC_IP" letsencrypt || rc=1
zs_https_verify "$DOMAIN" "$stg" || rc=1
echo
if (( rc == 0 )); then
  zs_ok "Done. Dashboard: https://${DOMAIN}  (Caddy renews the certificate automatically)"
else
  zs_warn "Caddy is configured for ${DOMAIN} but HTTPS is not working yet - see the reason above."
  zs_hint "After fixing it, re-check with: sudo bash $0 --check   (guide: docs/domain-and-https.md)"
fi
exit "$rc"
