#!/usr/bin/env bash
# Domain, DNS, port and HTTPS-certificate helpers, shared by the installer wizard, set-domain.sh and doctor.sh.
#
#   zs_dns_check DOMAIN IP          is DOMAIN's public DNS pointing at IP? (sets ZS_DNS_STATE / ZS_DNS_A / ZS_DNS_AAAA)
#   zs_dns_explain DOMAIN IP        plain-language explanation of ZS_DNS_STATE and how to fix it
#   zs_dns_instructions DOMAIN IP   the exact DNS record to create
#   zs_dns_wait DOMAIN IP SECONDS   re-check until DNS points here (propagation) or the time runs out
#   zs_dns_gate DOMAIN IP           interactive: check / wait / re-check / change / continue / no domain
#   zs_ports_check IP MODE          firewall + reachability of ports 80/443
#   zs_https_verify DOMAIN STAGING  wait for Caddy's certificate and check the site loads over HTTPS
#   zs_explain_cert_error TEXT      turn Caddy / Let's Encrypt errors into one plain sentence
#
# Test hooks (never set in production): ZS_DNS_RESOLVERS (default "1.1.1.1 8.8.8.8"), ZS_SLEEP (default sleep),
# ZS_HTTPS_WAIT (seconds to wait for a certificate, default 180), ZS_DNS_POLL (seconds between DNS checks, default 15),
# ZS_DNS_WAIT_MAX (how long [w] in the DNS menu waits, default 600).

ZS_DNS_STATE="" ZS_DNS_A="" ZS_DNS_AAAA=""

_zs_sleep() { ${ZS_SLEEP:-sleep} "$1"; }

# ------------------------------------------------------------------ DNS

# zs_dns_lookup A|AAAA NAME - unique addresses, space separated. Public resolvers first: that is what Let's Encrypt
# and visitors see, and it avoids this server's own cache. Falls back to the system resolver if dig is missing.
zs_dns_lookup() {
  local type="$1" name="$2" out r rc re
  if [[ "$type" == "A" ]]; then re='^[0-9]{1,3}(\.[0-9]{1,3}){3}$'; else re='^[0-9a-fA-F:]*:[0-9a-fA-F:]*$'; fi
  if command -v dig >/dev/null 2>&1; then
    for r in ${ZS_DNS_RESOLVERS:-1.1.1.1 8.8.8.8}; do
      rc=0
      out="$(dig +short +time=3 +tries=2 "$type" "$name" "@$r" 2>/dev/null)" || rc=$?
      if (( rc == 0 )); then
        # +short also lists CNAME targets ("host.example.net."): keep only addresses.
        printf '%s\n' "$out" | grep -E "$re" | sort -u | tr '\n' ' ' | sed 's/ $//'
        return 0
      fi
    done
  fi
  if [[ "$type" == "A" ]]; then
    getent ahostsv4 "$name" 2>/dev/null | awk '{print $1}' | grep -E "$re" | sort -u | tr '\n' ' ' | sed 's/ $//'
  else
    getent ahostsv6 "$name" 2>/dev/null | awk '{print $1}' | grep -v '^::ffff:' | grep -E "$re" | sort -u \
      | tr '\n' ' ' | sed 's/ $//'
  fi
  return 0
}

_zs_ip2int() { local a b c d; IFS=. read -r a b c d <<<"$1"; echo $(( (a << 24) | (b << 16) | (c << 8) | d )); }

# _zs_in_cidr IP CIDR
_zs_in_cidr() {
  local ip net bits mask
  ip="$(_zs_ip2int "$1")"; net="$(_zs_ip2int "${2%/*}")"; bits="${2#*/}"
  mask=$(( bits == 0 ? 0 : (0xFFFFFFFF << (32 - bits)) & 0xFFFFFFFF ))
  (( (ip & mask) == (net & mask) ))
}

# Cloudflare's published proxy ranges (https://www.cloudflare.com/ips-v4/). An A record that resolves here means the
# record is "Proxied" (orange cloud), so neither visitors nor Let's Encrypt reach this server directly.
ZS_CLOUDFLARE_V4="173.245.48.0/20 103.21.244.0/22 103.22.200.0/22 103.31.4.0/22 141.101.64.0/18 108.162.192.0/18
190.93.240.0/20 188.114.96.0/20 197.234.240.0/22 198.41.128.0/17 162.158.0.0/15 104.16.0.0/13 104.24.0.0/14
172.64.0.0/13 131.0.72.0/22"

zs_is_cloudflare_ip() {
  local c
  [[ "$1" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || return 1
  for c in $ZS_CLOUDFLARE_V4; do _zs_in_cidr "$1" "$c" && return 0; done
  return 1
}

_zs_local_ipv6() { ip -o -6 addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1; }

# zs_dns_check DOMAIN IP - returns 0 only when DOMAIN points at IP and nowhere else.
# ZS_DNS_STATE: ok | missing (no A record) | mismatch (A record, not this IP) | extra (this IP plus others)
#               | aaaa (A is right, but an IPv6 record points elsewhere) | noip (this server's IP is unknown)
zs_dns_check() {
  local d="$1" ip="$2" v6 mine
  ZS_DNS_A="$(zs_dns_lookup A "$d")"
  ZS_DNS_AAAA="$(zs_dns_lookup AAAA "$d")"
  if [[ -z "$ZS_DNS_A" ]]; then ZS_DNS_STATE=missing
  elif [[ -z "$ip" ]]; then ZS_DNS_STATE=noip
  elif [[ " $ZS_DNS_A " != *" $ip "* ]]; then ZS_DNS_STATE=mismatch
  elif [[ "$ZS_DNS_A" != "$ip" ]]; then ZS_DNS_STATE=extra
  else
    ZS_DNS_STATE=ok
    if [[ -n "$ZS_DNS_AAAA" ]]; then
      mine=" $(_zs_local_ipv6 | tr '\n' ' ') "
      for v6 in $ZS_DNS_AAAA; do
        [[ "$mine" == *" $v6 "* ]] || { ZS_DNS_STATE=aaaa; break; }
      done
    fi
  fi
  [[ "$ZS_DNS_STATE" == ok ]]
}

# zs_dns_host_label DOMAIN - what goes in the "Name/Host" field at most DNS providers ("@" for the bare domain).
zs_dns_host_label() {
  local d="${1,,}" n suffix2
  local -a parts
  IFS=. read -r -a parts <<<"$d"
  n=${#parts[@]}
  (( n >= 2 )) || { echo "@"; return; }
  suffix2="${parts[n-2]}.${parts[n-1]}"
  local keep=2
  case "$suffix2" in
    co.uk|org.uk|ac.uk|me.uk|com.au|net.au|org.au|edu.au|co.nz|co.in|net.in|org.in|co.za|com.br|com.lk|org.lk|\
    edu.lk|ac.lk|net.lk|gov.lk|com.sg|com.my|co.jp|co.id|com.ph|com.pk|com.ng|co.ke|com.mx|com.ar|com.tr|com.cn)
      keep=3 ;;
  esac
  if (( n <= keep )); then echo "@"; return; fi
  local IFS=.
  echo "${parts[*]:0:n-keep}"
}

# zs_dns_zone DOMAIN - the part after the host label (the domain whose DNS settings you edit).
zs_dns_zone() {
  local d="${1,,}" label
  label="$(zs_dns_host_label "$d")"
  if [[ "$label" == "@" ]]; then echo "$d"; else echo "${d#"$label".}"; fi
}

zs_dns_instructions() {
  local d="$1" ip="$2" label zone
  [[ -n "$ip" ]] || ip="<this server's IP>"
  label="$(zs_dns_host_label "$d")"; zone="$(zs_dns_zone "$d")"
  zs_say ""
  zs_say "  ${C_BOLD}Create this DNS record${C_RESET} where you manage ${zone} (your domain registrar or DNS provider):"
  zs_say ""
  printf '      %-6s %-22s %-18s %s\n' "Type" "Name / Host" "Value / Points to" "TTL"
  printf '      %-6s %-22s %-18s %s\n' "A" "$label" "$ip" "Auto (or 300)"
  zs_say ""
  zs_hint "Most providers add '${zone}' to the name for you - type only '${label}'. If yours asks for the full name,"
  zs_hint "type '${d}'. Cloudflare: set 'Proxy status' to 'DNS only' (grey cloud)."
  zs_hint "Delete any other A or AAAA record for ${d}. Guide: docs/domain-and-https.md"
}

zs_dns_explain() {
  local d="$1" ip="$2" a
  case "$ZS_DNS_STATE" in
    ok) zs_ok "DNS: ${d} points to this server (${ip})." ;;
    missing)
      zs_warn "DNS: ${d} has no A record yet (or it has not reached the public DNS servers)."
      zs_hint "Create the A record shown above. New records usually work within a few minutes; some providers take"
      zs_hint "up to an hour. Check the spelling of the name, and that you edited the DNS of the right domain." ;;
    noip)
      zs_warn "DNS: ${d} points to ${ZS_DNS_A}, but this server's public IP address could not be detected."
      zs_hint "Compare it with the IP address in your provider's control panel." ;;
    mismatch)
      zs_warn "DNS: ${d} points to ${ZS_DNS_A}, but this server is ${ip}."
      for a in $ZS_DNS_A; do
        if zs_is_cloudflare_ip "$a"; then
          zs_hint "${a} is a Cloudflare proxy address: in Cloudflare > DNS, click the record and set 'Proxy status'"
          zs_hint "to 'DNS only' (grey cloud). Let's Encrypt has to reach this server directly."
          return 0
        fi
      done
      zs_hint "Edit the A record so its value is ${ip}. If you changed it just now, the old answer may still be"
      zs_hint "cached for up to the record's TTL - wait and re-check." ;;
    extra)
      zs_warn "DNS: ${d} has several A records (${ZS_DNS_A}); only ${ip} is this server."
      zs_hint "Delete the other A records, otherwise some visitors and Let's Encrypt reach the wrong server." ;;
    aaaa)
      zs_warn "DNS: the A record is right, but ${d} also has an IPv6 (AAAA) record (${ZS_DNS_AAAA}) that is not this server."
      zs_hint "Let's Encrypt tries IPv6 first, so the certificate request can fail. Delete the AAAA record." ;;
  esac
}

# zs_dns_wait DOMAIN IP MAX_SECONDS - 0 as soon as DNS points here, 1 if it still does not after MAX_SECONDS.
zs_dns_wait() {
  local d="$1" ip="$2" max="${3:-600}" poll="${ZS_DNS_POLL:-15}" waited=0
  zs_info "Waiting for ${d} to point to ${ip} (checking every ${poll}s, up to $((max / 60)) minutes)..."
  while true; do
    if zs_dns_check "$d" "$ip"; then zs_ok "DNS: ${d} now points to this server (${ip})."; return 0; fi
    if (( waited >= max )); then
      zs_warn "Still not pointing here after $((waited / 60)) min $((waited % 60)) s."
      zs_dns_explain "$d" "$ip"
      return 1
    fi
    printf '    %s... %dm%02ds - currently: %s%s\n' "$C_DIM" $((waited / 60)) $((waited % 60)) \
      "${ZS_DNS_A:-no A record}" "$C_RESET"
    _zs_sleep "$poll"
    waited=$((waited + poll))
  done
}

# zs_dns_gate DOMAIN IP - interactive DNS step. Returns 0 = DNS OK (or user chose to continue anyway),
# 2 = user wants to type another name, 3 = user chose no domain (IP-only).
zs_dns_gate() {
  local d="$1" ip="$2" choice explained=0 max="${ZS_DNS_WAIT_MAX:-600}"
  zs_dns_instructions "$d" "$ip"
  zs_info "Checking DNS for ${d}..."
  while true; do
    if zs_dns_check "$d" "$ip"; then zs_dns_explain "$d" "$ip"; return 0; fi
    # After a timed-out [w] the explanation was just printed by zs_dns_wait: do not repeat it.
    (( explained )) || zs_dns_explain "$d" "$ip"
    explained=0
    zs_say ""
    zs_say "    [w] wait - re-check automatically every ${ZS_DNS_POLL:-15}s for up to $((max / 60)) minutes (DNS can take a while)"
    zs_say "    [r] re-check now          [e] enter a different name"
    zs_say "    [c] continue anyway       (the certificate is requested once DNS points here)"
    zs_say "    [n] no domain             (IP address only, self-signed certificate)"
    zs_read choice "  Choose [w/r/e/c/n]: "
    case "${choice,,}" in
      w|"") zs_dns_wait "$d" "$ip" "$max" && return 0; explained=1 ;;
      r) zs_info "Checking DNS for ${d}..." ;;
      e) return 2 ;;
      c) zs_warn "Continuing although ${d} does not point here yet."; return 0 ;;
      n) return 3 ;;
      *) zs_say "    Type w, r, e, c or n." ;;
    esac
  done
}

zs_ip_only_warning() {
  local ip="${1:-<server-ip>}"
  zs_warn "No domain: the dashboard will be at https://${ip} with a self-signed certificate."
  zs_hint "The connection is encrypted, but it is NOT browser-trusted HTTPS: every browser shows a 'Not secure'"
  zs_hint "warning and cannot prove it is really your server. Fine for testing; for real use add a domain later with:"
  zs_hint "  sudo bash /opt/zoom-stream/set-domain.sh stream.example.com"
}

# ------------------------------------------------------------------ ports

_zs_ufw_allows() { ufw status 2>/dev/null | grep -qE "^$1(/tcp)?[[:space:]]+(ALLOW|LIMIT)"; }

# zs_ports_check IP letsencrypt|self-signed - prints pass/warn lines. Returns 1 if this server itself blocks a port.
zs_ports_check() {
  local ip="$1" mode="$2" port ports="443" bad=0
  [[ "$mode" == "letsencrypt" ]] && ports="80 443"
  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    for port in $ports; do
      if _zs_ufw_allows "$port"; then zs_ok "Firewall (ufw) allows port ${port}"
      else zs_fail "Firewall (ufw) blocks port ${port}"; zs_hint "Fix: sudo ufw allow ${port}/tcp"; bad=1; fi
    done
  fi
  for port in $ports; do
    if ss -Hltn "sport = :${port}" 2>/dev/null | grep -q .; then :
    else zs_fail "Nothing is listening on port ${port}"; zs_hint "Start Caddy: sudo systemctl restart caddy"; bad=1; fi
  done
  (( bad )) && return 1
  [[ -n "$ip" ]] || return 0
  # Through the public address: catches most provider firewalls / security groups. Some providers do not let a
  # server reach its own public IP, so a failure here is a warning, not proof.
  local blocked=()
  for port in $ports; do zs_tcp_reachable "$ip" "$port" 6 || blocked+=("$port"); done
  if (( ${#blocked[@]} == 0 )); then
    zs_ok "Port(s) ${ports// /, } reachable on ${ip}"
  else
    zs_warn "Port(s) ${blocked[*]} could not be reached on ${ip} from this server."
    zs_hint "Open TCP ${blocked[*]} for everyone (0.0.0.0/0) in your provider's firewall: AWS 'Security group inbound"
    zs_hint "rules', Google Cloud 'VPC firewall', Azure 'Network security group', Oracle 'Security list',"
    zs_hint "DigitalOcean/Hetzner/Vultr/Linode 'Cloud firewall'. (Some providers block this self-test even when the port is open.)"
  fi
  return 0
}

# ------------------------------------------------------------------ certificates / HTTPS

# zs_explain_cert_error TEXT - one sentence for the most likely certificate problem in Caddy's log, or nothing.
zs_explain_cert_error() {
  local t="$1"
  case "$t" in
    *rateLimited*|*"too many certificates"*|*"too many failed authorizations"*|*"too many new orders"*)
      local after
      after="$(grep -oE 'retry after [0-9TZ:. -]+(UTC)?' <<<"$t" | head -n1)"
      echo "RATE LIMIT: Let's Encrypt refused because too many certificates or failed attempts were requested for this name recently${after:+ (${after})}. Caddy retries by itself later; for repeated tests use --staging." ;;
    *caa*|*CAA*)
      echo "CAA: the domain's CAA DNS record does not allow Let's Encrypt. Add a CAA record for letsencrypt.org or delete the CAA records." ;;
    *NXDOMAIN*|*"no valid A records"*|*"no valid AAAA records"*|*"DNS problem"*)
      echo "DNS: Let's Encrypt could not find this name in public DNS. Check the A record (sudo bash /opt/zoom-stream/set-domain.sh --check)." ;;
    *"Timeout during connect"*|*"likely firewall problem"*|*"Connection refused"*|*"connection refused"*|*"Fetching http"*"timeout"*)
      echo "PORT: Let's Encrypt could not connect to this server on port 80/443. Open TCP 80 and 443 in the provider firewall / security group (and ufw)." ;;
    *unauthorized*|*"Invalid response"*|*"403"*"acme-challenge"*|*"404"*"acme-challenge"*)
      echo "WRONG SERVER: Let's Encrypt reached a different server or proxy for this name (e.g. a Cloudflare proxy, an old server, or another A/AAAA record). Point the name only at this server." ;;
    *"could not get certificate"*|*"obtaining certificate"*"error"*|*"challenge failed"*)
      echo "The certificate request failed. Details: sudo journalctl -u caddy -n 80 --no-pager" ;;
    *) return 1 ;;
  esac
}

_zs_caddy_log_since() { journalctl -u caddy --since "@$1" --no-pager -o cat 2>/dev/null | tail -n 300; }

# _zs_cert_issuer DOMAIN - issuer of the certificate Caddy serves locally for DOMAIN.
_zs_cert_issuer() {
  echo | openssl s_client -connect 127.0.0.1:443 -servername "$1" 2>/dev/null | openssl x509 -noout -issuer 2>/dev/null
}

# zs_https_verify DOMAIN STAGING(yes|no) - waits up to ZS_HTTPS_WAIT seconds for Caddy to obtain the certificate,
# then checks the login page loads over HTTPS. Returns 0 when it works; otherwise explains why and returns 1.
zs_https_verify() {
  local d="$1" staging="${2:-no}" max="${ZS_HTTPS_WAIT:-180}" start waited=0 code issuer why=""
  start="$(date +%s)"
  local label="Let's Encrypt"
  [[ "$staging" == "yes" ]] && label="Let's Encrypt STAGING (test certificates)"
  if [[ -n "${ZS_HTTPS_CHECK_ONLY:-}" ]]; then zs_info "Checking the HTTPS certificate for ${d} (${label})..."
  else zs_info "Requesting the HTTPS certificate for ${d} from ${label} - usually under a minute..."; fi
  while true; do
    if [[ "$staging" == "yes" ]]; then
      issuer="$(_zs_cert_issuer "$d")"
      if [[ "$issuer" == *STAGING* || "$issuer" == *"(STAGING)"* || "$issuer" == *"Fake LE"* ]]; then
        code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 8 --resolve "$d:443:127.0.0.1" "https://$d/login" 2>/dev/null || true)"
        if [[ "$code" =~ ^(200|302|303|307)$ ]]; then
          zs_ok "HTTPS works with a Let's Encrypt STAGING certificate (https://${d})."
          zs_hint "Staging certificates are NOT trusted by browsers. For a real one: sudo bash /opt/zoom-stream/set-domain.sh ${d}"
          return 0
        fi
      fi
    else
      code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 8 --resolve "$d:443:127.0.0.1" "https://$d/login" 2>/dev/null || true)"
      if [[ "$code" =~ ^(200|302|303|307)$ ]]; then
        zs_ok "HTTPS works with a trusted certificate: https://${d}"
        # The same request through public DNS (the path a visitor takes).
        code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "https://$d/login" 2>/dev/null || true)"
        if [[ "$code" =~ ^(200|302|303|307)$ ]]; then zs_ok "https://${d} loads through public DNS"
        else zs_hint "(Could not load https://${d} through its public address from the server itself - some providers block this; open it in your browser.)"; fi
        return 0
      fi
    fi
    why="$(zs_explain_cert_error "$(_zs_caddy_log_since "$start")" || true)"
    # Rate limits and CAA do not fix themselves within minutes: stop waiting.
    [[ "$why" == "RATE LIMIT"* || "$why" == "CAA"* ]] && break
    (( waited >= max )) && break
    _zs_sleep 5
    waited=$(( $(date +%s) - start ))
    if (( waited % 30 < 5 )); then printf '    %s... still waiting for the certificate (%ss)%s\n' "$C_DIM" "$waited" "$C_RESET"; fi
  done
  zs_fail "HTTPS for ${d} is not working yet."
  if [[ -n "$why" ]]; then zs_hint "$why"
  else zs_hint "No certificate after ${max}s and no clear error in Caddy's log. Check: sudo journalctl -u caddy -n 80 --no-pager"; fi
  zs_hint "Caddy keeps retrying in the background. After fixing the cause, check again: sudo bash /opt/zoom-stream/set-domain.sh --check"
  return 1
}

# zs_https_verify_ip IP - IP-only mode: the self-signed site answers.
zs_https_verify_ip() {
  local ip="$1" code
  code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 8 https://127.0.0.1/login 2>/dev/null || true)"
  if [[ "$code" =~ ^(200|302|303|307)$ ]]; then
    zs_ok "HTTPS answers with the self-signed certificate (https://${ip:-<server-ip>})"
    return 0
  fi
  zs_fail "HTTPS on port 443 is not answering (result: ${code:-no answer})"
  zs_hint "sudo systemctl restart caddy; log: sudo journalctl -u caddy -n 50 --no-pager"
  return 1
}
