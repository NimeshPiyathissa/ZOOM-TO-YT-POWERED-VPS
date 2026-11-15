#!/usr/bin/env bash
# Diagnose an installation and explain common problems in plain words. Read-only: it never changes anything.
#
#   sudo ./doctor.sh            run every check
#   sudo ./doctor.sh --updates  also check GitHub for a newer release
#   --no-color                  plain output
#
# Exit code: 0 = no problems, 1 = at least one problem found.
set -uo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=installer/lib/common.sh
source "$SRC/installer/lib/common.sh"
# shellcheck source=installer/lib/domain.sh
source "$SRC/installer/lib/domain.sh"

CHECK_UPDATES=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --updates) CHECK_UPDATES=1 ;;
    --no-color) export NO_COLOR=1; zs_init_colors ;;
    -h|--help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) zs_die "Unknown option: $1 (see --help)" ;;
  esac
  shift
done
[[ $EUID -eq 0 ]] || zs_die "Run as root: sudo $0"

PROBLEMS=0 WARNINGS=0
pass() { zs_ok "$1"; }
warn() { zs_warn "$1"; shift; local h; for h in "$@"; do zs_hint "$h"; done; WARNINGS=$((WARNINGS + 1)); }
prob() { zs_fail "$1"; shift; local h; for h in "$@"; do zs_hint "$h"; done; PROBLEMS=$((PROBLEMS + 1)); }
conf() { [[ -f "$ZS_INSTALL_CONF" ]] && sed -n "s/^$1=//p" "$ZS_INSTALL_CONF" | head -n1; }

# ------------------------------------------------------------------ installation
zs_title "Installation"
if [[ -f "$ZS_INSTALLED_FILE" ]]; then
  pass "Installed $(cat "$ZS_INSTALLED_FILE"), version $(git -C "$SRC" describe --tags --always 2>/dev/null || conf VERSION)"
elif [[ -f "$ZS_PROGRESS_FILE" ]]; then
  prob "The installation did not finish (last completed step: $(tail -n1 "$ZS_PROGRESS_FILE"))" \
       "Run the installer again - it resumes: sudo bash $SRC/install.sh" "Install log: $ZS_LOG_DIR/install.log"
else
  prob "Not installed on this server" "Install with: sudo bash $SRC/install.sh"
fi

# ------------------------------------------------------------------ server
zs_title "Server"
free_gb=$(( $(df -Pk / | awk 'NR==2 {print $4}') / 1024 / 1024 ))
if (( free_gb < 2 )); then prob "Disk almost full: ${free_gb} GB free" "Delete old recordings/backups (/var/backups/zoom-stream) or enlarge the disk. A full disk stops the stream and the dashboard."
elif (( free_gb < 5 )); then warn "Low disk space: ${free_gb} GB free" "Recordings and logs grow over time; clean up soon."
else pass "Disk: ${free_gb} GB free"; fi
avail_mb=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
if (( avail_mb < 500 )); then warn "Memory is tight: ${avail_mb} MB available" "Zoom and Chrome may be killed. Close extra sources or use a server with more memory."
else pass "Memory: ${avail_mb} MB available"; fi
load=$(cut -d' ' -f1 /proc/loadavg); cores=$(nproc)
if awk -v l="$load" -v c="$cores" 'BEGIN {exit !(l > c * 1.5)}'; then
  warn "High CPU load: ${load} on ${cores} cores" "The stream may stutter. Lower the output quality on the Output page."
else pass "CPU load: ${load} on ${cores} cores"; fi
if [[ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" == "yes" ]]; then pass "Clock is synchronised (timezone $(timedatectl show -p Timezone --value))"
else warn "Clock is not synchronised" "Scheduled starts may be off. Check: timedatectl status"; fi

# ------------------------------------------------------------------ services
zs_title "Services"
for s in dashboard caddy; do
  st="$(systemctl is-active "$s" 2>/dev/null)"
  if [[ "$st" == "active" ]]; then pass "$s is running"
  else prob "$s is ${st:-not installed}" "Recent log: sudo journalctl -u $s -n 30 --no-pager" "Restart: sudo systemctl restart $s"; fi
  [[ "$(systemctl is-enabled "$s" 2>/dev/null)" == "enabled" ]] || warn "$s does not start at boot" "Enable it: sudo systemctl enable $s"
done
if id "$ZS_SVC_USER" &>/dev/null; then
  if [[ "$(loginctl show-user "$ZS_SVC_USER" --property=Linger --value 2>/dev/null)" == "yes" ]]; then
    pass "Background services start at boot (linger enabled for $ZS_SVC_USER)"
  else prob "Background services will not start at boot (linger is off)" "Fix: sudo loginctl enable-linger $ZS_SVC_USER"; fi
  for u in xvfb openbox audio-setup x11vnc novnc-proxy; do
    st="$(zs_user_systemctl is-active "$u" 2>/dev/null)"
    if [[ "$st" == "active" ]]; then pass "$u is running"
    else prob "$u is ${st:-unknown}" "Recent log: sudo -u $ZS_SVC_USER XDG_RUNTIME_DIR=/run/user/$(id -u "$ZS_SVC_USER") journalctl --user -u $u -n 30 --no-pager" \
              "Use 'Restart all services' on the dashboard, or reboot the server."; fi
  done
  for u in zoom browser-source ffmpeg-stream; do
    st="$(zs_user_systemctl is-active "$u" 2>/dev/null)"
    if [[ "$st" == "failed" ]]; then
      prob "$u failed" "Last lines: $(sudo -u "$ZS_SVC_USER" XDG_RUNTIME_DIR="/run/user/$(id -u "$ZS_SVC_USER")" journalctl --user -u "$u" -n 3 --no-pager -o cat 2>/dev/null | tr '\n' ' ' | cut -c1-200)" \
           "Check the Logs page. ffmpeg-stream failing usually means a wrong stream key or YouTube not reachable."
    else
      zs_info "$u: ${st:-inactive}$([[ "$u" == ffmpeg-stream ]] && echo ' (runs only while live)')"
    fi
  done
  if [[ "$(zs_user_systemctl is-active zoom-vacuum.timer 2>/dev/null)" == "active" ]]; then pass "Daily disk cleanup timer is active" \
  else warn "Daily disk cleanup timer is not active" "Enable it: sudo -u $ZS_SVC_USER XDG_RUNTIME_DIR=/run/user/$(id -u "$ZS_SVC_USER") systemctl --user enable --now zoom-vacuum.timer"; fi
else
  prob "Service user '$ZS_SVC_USER' does not exist" "Re-run the installer."
fi

# ------------------------------------------------------------------ dashboard + HTTPS
zs_title "Dashboard and HTTPS"
if curl -fsS --max-time 4 http://127.0.0.1:8000/health >/dev/null 2>&1; then pass "Dashboard answers locally (127.0.0.1:8000)"
else prob "Dashboard does not answer on 127.0.0.1:8000" "sudo systemctl restart dashboard; then: sudo journalctl -u dashboard -n 50 --no-pager"; fi
domain="$(conf DOMAIN)"
if [[ -n "$domain" && "$domain" != "none" ]]; then
  ip="$(zs_public_ip || true)"
  if zs_dns_check "$domain" "$ip"; then pass "DNS: $domain -> $ip"
  else
    zs_dns_explain "$domain" "$ip"; PROBLEMS=$((PROBLEMS + 1))
    zs_hint "HTTPS certificates cannot be issued or renewed until it points here. Guide: docs/domain-and-https.md"
  fi
  if [[ "$(conf LE_STAGING)" == "yes" ]]; then
    warn "Let's Encrypt STAGING is configured: a test certificate that browsers do not trust" \
         "For a real certificate: sudo bash $SRC/set-domain.sh $domain"
    code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 8 --resolve "$domain:443:127.0.0.1" "https://$domain/login" 2>/dev/null || true)"
  else
    code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 8 --resolve "$domain:443:127.0.0.1" "https://$domain/login" 2>/dev/null || true)"
  fi
  if [[ "$code" =~ ^(200|302|303|307)$ ]]; then pass "HTTPS works (https://$domain)"
  else
    why="$(zs_explain_cert_error "$(journalctl -u caddy --since '-2 days' --no-pager -o cat 2>/dev/null | tail -n 300)" || true)"
    prob "HTTPS for $domain is not working (result: ${code:-no answer})" \
         "${why:-Caddy log: sudo journalctl -u caddy -n 50 --no-pager}" \
         "Ports 80 and 443 must be open for Let's Encrypt. Re-check: sudo bash $SRC/set-domain.sh --check"
  fi
  end="$(echo | openssl s_client -connect 127.0.0.1:443 -servername "$domain" 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)"
  if [[ -n "$end" ]]; then
    days=$(( ($(date -d "$end" +%s) - $(date +%s)) / 86400 ))
    if (( days < 7 )); then prob "Certificate expires in ${days} days" "Caddy renews automatically when ports 80/443 are reachable - check the Caddy log."
    else pass "Certificate valid for ${days} more days"; fi
  fi
else
  code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 8 https://127.0.0.1/login 2>/dev/null || true)"
  if [[ "$code" =~ ^(200|302|303|307)$ ]]; then pass "HTTPS works (self-signed certificate, https://<server-ip>)"
  else prob "HTTPS on port 443 is not answering (result: ${code:-no answer})" "sudo systemctl restart caddy; log: sudo journalctl -u caddy -n 50 --no-pager"; fi
fi
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
  if ufw status 2>/dev/null | grep -qE '^443(/tcp)?\s+ALLOW'; then pass "Firewall allows HTTPS (443)"
  else prob "Firewall is on but blocks HTTPS" "Fix: sudo ufw allow 443/tcp"; fi
  if [[ -n "$domain" && "$domain" != "none" ]]; then
    if ufw status 2>/dev/null | grep -qE '^80(/tcp)?\s+ALLOW'; then pass "Firewall allows HTTP (80) for certificate renewal"
    else prob "Firewall is on but blocks port 80 (Let's Encrypt needs it)" "Fix: sudo ufw allow 80/tcp"; fi
  fi
else
  zs_info "Firewall (ufw) is off"
fi

# ------------------------------------------------------------------ settings + secrets
zs_title "Settings"
if [[ -f "$ZS_APP_DIR/.env" ]]; then
  perm="$(stat -c '%a %U' "$ZS_APP_DIR/.env")"
  if [[ "$perm" == "600 $ZS_SVC_USER" ]]; then pass "Stream settings file is private (.env: $perm)" \
  else prob "Stream settings file has unsafe permissions ($perm)" "Fix: sudo chown $ZS_SVC_USER: $ZS_APP_DIR/.env && sudo chmod 600 $ZS_APP_DIR/.env"; fi
  if grep -q '^YT_STREAM_KEY=..' "$ZS_APP_DIR/.env"; then pass "A YouTube stream key is set" \
  else warn "No YouTube stream key yet" "Add it in the dashboard: Live Studio > Stream key."; fi
else
  prob "Stream settings file is missing ($ZS_APP_DIR/.env)" "Restore a backup (sudo bash $SRC/restore.sh) or re-run the installer."
fi
if [[ -f /etc/sudoers.d/dashboard ]]; then
  if visudo -cf /etc/sudoers.d/dashboard >/dev/null 2>&1; then pass "Dashboard permissions (sudoers rule) are valid" \
  else prob "The dashboard sudoers rule is invalid" "Re-deploy it: sudo bash $SRC/install.sh --upgrade"; fi
else prob "Dashboard sudoers rule is missing" "Re-deploy it: sudo bash $SRC/install.sh --upgrade"; fi
if [[ -f "$ZS_DASH_HOME/data/secrets.enc.json" ]]; then
  if [[ -f /etc/zoom-stream/master.key ]]; then
    # Asked as the dashboard's own user: a key file that exists but that the service cannot reach is no use.
    # shellcheck disable=SC2119  # no options: a stopped x11vnc is reported by the Services section above
    if selfcheck_out="$(zs_dashboard_selfcheck 2>&1)"; then pass "Encrypted secret store unlocks automatically; Remote GUI password works"
    else
      prob "The dashboard cannot unlock its secret store or log in to the remote screen (the Remote GUI will not connect)" \
           "$(grep -E '^\[FAIL\]' <<<"$selfcheck_out" | head -n 2 | tr '\n' ' ')" \
           "Usual fix: sudo chmod 755 /etc/zoom-stream && sudo systemctl restart dashboard   (or: sudo bash $SRC/update.sh)"
    fi
  else warn "Encrypted secret store is present but has no automatic unlock key" "Unlock it after each reboot: sudo $ZS_DASH_HOME/venv/bin/python -m app.cli vault unlock (run in $ZS_DASH_HOME/app)"; fi
else warn "No encrypted secret store yet" "It is created during installation (app.cli setup)."; fi
if [[ -f "$ZS_CREDENTIALS_FILE" ]]; then
  warn "Generated passwords are still in $ZS_CREDENTIALS_FILE" "Store them in a password manager, then: sudo rm $ZS_CREDENTIALS_FILE"
fi

# ------------------------------------------------------------------ network
zs_title "Network"
if zs_tcp_reachable a.rtmps.youtube.com 443 8; then pass "YouTube live ingest reachable (a.rtmps.youtube.com:443)"
else prob "YouTube live ingest is not reachable" "Allow outbound TCP 443 (and 1935 for rtmp://) in the provider firewall."; fi
if zs_tcp_reachable zoom.us 443 8; then pass "Zoom reachable"; else prob "zoom.us is not reachable" "Check outbound HTTPS and DNS."; fi

if (( CHECK_UPDATES )); then
  zs_title "Updates"
  bash "$SRC/update.sh" --check || true
fi

echo
if (( PROBLEMS )); then zs_fail "${PROBLEMS} problem(s), ${WARNINGS} warning(s). See the hints above; docs/troubleshooting.md has more."; exit 1; fi
if (( WARNINGS )); then zs_warn "No problems, ${WARNINGS} warning(s)."; else zs_ok "Everything looks healthy."; fi
exit 0
