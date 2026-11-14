#!/usr/bin/env bash
# Guided HTTPS for the dashboard: Caddy terminates TLS on 443 and proxies to the dashboard on 127.0.0.1:8000
# (systemd/dashboard.service only ever listens on localhost). Run as root. Called by install.sh; also runnable on its
# own later, e.g. to add or change a domain.
#
#   setup-https.sh --domain stream.example.com [--email you@example.com]   browser-trusted Let's Encrypt certificate
#   setup-https.sh                                                           no domain: the installer's self-signed
#                                                                            certificate, served for https://<vps-ip>
#   options:  --yes           replace a Caddyfile this script did NOT write (after backing it up) without asking
#             --render-only   print the Caddyfile that would be written and change nothing
#
# Safety:
#   * A Caddyfile that was not written by this script is never overwritten without explicit confirmation (an
#     interactive "yes", or --yes). It is always backed up first.
#   * The new config is validated with `caddy validate` before it is installed; a config Caddy rejects is never put
#     in place, and the running Caddy keeps serving the old one.
#   * Only Caddy is reloaded. The dashboard and every streaming unit are left alone - this cannot start, stop or
#     restart a broadcast.
#   * No secrets are read or printed.
#
# Test hooks (never set in production): ZS_ETC (default /etc), ZS_CERT_SRC (default /home/dashboard/certs).
set -euo pipefail

ETC="${ZS_ETC:-/etc}"
CERT_SRC="${ZS_CERT_SRC:-/home/dashboard/certs}"
CADDY_DIR="${ETC}/caddy"
CADDYFILE="${CADDY_DIR}/Caddyfile"
CERT_DIR="${CADDY_DIR}/zoom-stream"
MARKER="# managed-by: zoom-stream setup-https.sh"
UPSTREAM="127.0.0.1:8000"

DOMAIN="" EMAIL="" YES=0 RENDER_ONLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) DOMAIN="${2:-}"; shift ;;
    --email) EMAIL="${2:-}"; shift ;;
    --yes) YES=1 ;;
    --render-only) RENDER_ONLY=1 ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "setup-https: unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

say() { echo "    [https] $*"; }
die() { echo "    [https] ERROR: $*" >&2; exit 1; }

# Strict validation: these values are written into a config file, so nothing but a plain hostname / address may pass.
DOMAIN="${DOMAIN,,}"
if [[ -n "$DOMAIN" ]] && ! [[ "$DOMAIN" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]] || (( ${#DOMAIN} > 253 )); then
  die "'$DOMAIN' is not a valid domain name (example: stream.example.com)"
fi
if [[ -n "$EMAIL" ]] && ! [[ "$EMAIL" =~ ^[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+\.)+[A-Za-z]{2,63}$ ]]; then
  die "'$EMAIL' is not a valid e-mail address"
fi

render() {
  echo "$MARKER"
  echo "# Generated $(date -u +%Y-%m-%dT%H:%M:%SZ). Re-run setup-https.sh to change it; manual edits are kept only if"
  echo "# you remove the marker line above (the script then refuses to overwrite the file without --yes)."
  if [[ -n "$DOMAIN" ]]; then
    if [[ -n "$EMAIL" ]]; then printf '{\n\temail %s\n}\n\n' "$EMAIL"; fi
    echo "${DOMAIN} {"
  else
    # No domain: serve the installer's self-signed certificate for whatever address the browser used (the VPS IP).
    # default_sni picks that certificate for clients that send no server name (connections to a bare IP).
    printf '{\n\tdefault_sni zoom-stream.local\n}\n\n'
    echo ":443 {"
    echo "	tls ${CERT_DIR}/cert.pem ${CERT_DIR}/key.pem"
  fi
  cat <<EOF
	reverse_proxy ${UPSTREAM}
	header {
		Strict-Transport-Security "max-age=63072000"
		X-Content-Type-Options "nosniff"
		X-Frame-Options "DENY"
		Referrer-Policy "no-referrer"
		-Server
	}
	encode gzip
}
EOF
}

if (( RENDER_ONLY )); then
  render
  exit 0
fi

[[ $EUID -eq 0 || -n "${ZS_ETC:-}" ]] || die "run as root"

# ---- Caddy present?
if ! command -v caddy >/dev/null 2>&1; then
  if apt-cache show caddy >/dev/null 2>&1; then
    say "installing Caddy from the Ubuntu archive..."
    DEBIAN_FRONTEND=noninteractive apt-get install -y caddy >/dev/null
  else
    die "the 'caddy' package is not available from apt on this system. Install Caddy (https://caddyserver.com/docs/install), then re-run: $0 ${DOMAIN:+--domain $DOMAIN}"
  fi
fi

# ---- DNS sanity check (warning only: Let's Encrypt needs the name to point at this server)
if [[ -n "$DOMAIN" ]]; then
  PUBLIC_IP="$(curl -s --max-time 4 https://ifconfig.me || true)"
  RESOLVED="$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk '{print $1}' | sort -u | tr '\n' ' ')"
  if [[ -z "$RESOLVED" ]]; then
    say "WARNING: $DOMAIN does not resolve yet. Point its DNS A record at ${PUBLIC_IP:-this server}; Caddy will"
    say "         keep retrying the certificate until it does."
  elif [[ -n "$PUBLIC_IP" && " $RESOLVED" != *" $PUBLIC_IP "* ]]; then
    say "WARNING: $DOMAIN resolves to ${RESOLVED% } but this server is $PUBLIC_IP - the certificate cannot be issued"
    say "         until DNS points here."
  else
    say "DNS: $DOMAIN -> ${RESOLVED% } (matches this server)"
  fi
else
  # The self-signed certificate made by install-dashboard.sh, copied where the caddy user can read it.
  [[ -s "$CERT_SRC/cert.pem" && -s "$CERT_SRC/key.pem" ]] || die "no certificate in $CERT_SRC (run the dashboard installer first)"
  mkdir -p "$CERT_DIR"
  install -m 644 "$CERT_SRC/cert.pem" "$CERT_DIR/cert.pem"
  install -m 640 "$CERT_SRC/key.pem" "$CERT_DIR/key.pem"
  if getent group caddy >/dev/null 2>&1; then chgrp caddy "$CERT_DIR/key.pem"; fi
fi

# ---- write the Caddyfile (never clobber someone else's without a yes)
mkdir -p "$CADDY_DIR"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
render > "$TMP"

if [[ -f "$CADDYFILE" ]]; then
  if ! grep -qxF "$MARKER" "$CADDYFILE"; then
    if (( ! YES )); then
      if [[ -t 0 ]]; then
        read -r -p "    [https] $CADDYFILE was not written by this installer. Replace it (a backup is kept)? [yes/NO] " answer
        [[ "$answer" == "yes" ]] || { say "left $CADDYFILE unchanged - HTTPS NOT configured by the installer."; exit 0; }
      else
        say "$CADDYFILE exists and was not written by this installer - left unchanged (re-run with --yes to replace it)."
        say "HTTPS NOT configured by the installer. The dashboard listens on $UPSTREAM; point your proxy there."
        exit 0
      fi
    fi
  elif cmp -s <(grep -v '^# Generated ' "$CADDYFILE") <(grep -v '^# Generated ' "$TMP"); then
    say "Caddyfile already up to date."
    exit 0
  fi
fi

if ! caddy validate --config "$TMP" --adapter caddyfile >/dev/null 2>&1; then
  die "Caddy rejected the generated configuration - nothing was changed. Check: caddy validate --config $TMP"
fi

if [[ -f "$CADDYFILE" ]]; then
  BACKUP="$CADDYFILE.bak.$(date -u +%Y%m%d-%H%M%S)"
  cp -p "$CADDYFILE" "$BACKUP"
  say "previous Caddyfile kept as $BACKUP"
fi
install -m 644 "$TMP" "$CADDYFILE"

systemctl enable caddy >/dev/null 2>&1 || true
if systemctl is-active --quiet caddy; then
  systemctl reload caddy || die "Caddy refused to reload; restore with: cp '${BACKUP:-<backup>}' $CADDYFILE && systemctl reload caddy"
else
  systemctl start caddy
fi

if [[ -n "$DOMAIN" ]]; then
  say "HTTPS configured: https://$DOMAIN (Let's Encrypt, renewed automatically by Caddy)."
else
  say "HTTPS configured with the self-signed certificate: https://<vps-ip> (browsers warn once)."
  say "For a browser-trusted certificate later: $0 --domain your.domain.example"
fi
