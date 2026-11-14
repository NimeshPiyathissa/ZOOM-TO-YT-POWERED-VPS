#!/usr/bin/env bash
# Preflight checks. Each one prints pass / warn / fail with a hint on how to fix it.
# Hard failures stop the installer; warnings are listed and the operator decides (or ACCEPT_WARNINGS=yes in --config).
#
# Test hooks (never set in production): ZS_OS_RELEASE (default /etc/os-release).

PF_HARD=0
PF_SOFT=0
PF_SOFT_LIST=()

_pf_pass() { zs_ok "$1"; }
_pf_warn() { zs_warn "$1"; [[ -n "${2:-}" ]] && zs_hint "$2"; PF_SOFT=$((PF_SOFT + 1)); PF_SOFT_LIST+=("$1"); }
_pf_fail() { zs_fail "$1"; [[ -n "${2:-}" ]] && zs_hint "$2"; PF_HARD=$((PF_HARD + 1)); }

pf_check_os() {
  local file="${ZS_OS_RELEASE:-/etc/os-release}" id="" ver="" pretty=""
  if [[ -r "$file" ]]; then
    id="$(sed -n 's/^ID=//p' "$file" | tr -d '"')"
    ver="$(sed -n 's/^VERSION_ID=//p' "$file" | tr -d '"')"
    pretty="$(sed -n 's/^PRETTY_NAME=//p' "$file" | tr -d '"')"
  fi
  if [[ "$id" == "ubuntu" && "$ver" == "24.04" ]]; then
    _pf_pass "Operating system: ${pretty}"
  else
    _pf_fail "Operating system: ${pretty:-unknown} is not supported" \
      "This installer supports Ubuntu 24.04 LTS only (Zoom, Chrome and the audio stack are tested there). Rebuild the server with Ubuntu 24.04 LTS."
  fi
}

pf_check_arch() {
  local arch
  arch="$(uname -m)"
  if [[ "$arch" == "x86_64" ]]; then _pf_pass "CPU architecture: x86_64"
  else _pf_fail "CPU architecture: ${arch} is not supported" "The Zoom and Chrome Linux packages exist only for x86_64 (amd64). Use an x86_64 server."
  fi
}

pf_check_root() {
  if [[ $EUID -eq 0 ]]; then _pf_pass "Running as root"
  else _pf_fail "Not running as root" "Run it with sudo: sudo bash install.sh"
  fi
}

pf_check_systemd() {
  if [[ -d /run/systemd/system ]] && command -v systemctl >/dev/null; then _pf_pass "systemd is running"
  else _pf_fail "systemd is not running" "The services are managed by systemd. Containers without systemd are not supported - use a VM or VPS."
  fi
}

pf_check_cpu() {
  local n
  n="$(nproc)"
  if (( n >= 4 )); then _pf_pass "CPU cores: ${n}"
  elif (( n >= 2 )); then
    _pf_warn "CPU cores: ${n} (4 or more recommended)" "Zoom, Chrome and the encoder share the CPU. With ${n} cores use 720p and expect high load."
  else _pf_fail "CPU cores: ${n} (at least 2 required, 4 recommended)" "Choose a server size with 4 vCPUs (or at least 2)."
  fi
  if (( n < 8 )); then
    zs_hint "1080p output is only recommended with 8+ cores; with ${n} cores the installer will suggest 720p."
  fi
}

pf_check_ram() {
  local kb gb
  kb="$(awk '/MemTotal/ {print $2}' /proc/meminfo)"
  gb="$(awk -v k="$kb" 'BEGIN {printf "%.1f", k/1024/1024}')"
  if (( kb >= 7 * 1024 * 1024 )); then _pf_pass "Memory: ${gb} GB"
  elif (( kb >= 3500 * 1024 )); then _pf_warn "Memory: ${gb} GB (8 GB recommended)" "It can work, but Chrome and Zoom together use several GB. Add swap or choose a larger server."
  else _pf_fail "Memory: ${gb} GB (at least 4 GB required)" "Choose a server with 8 GB of memory (4 GB minimum)."
  fi
}

pf_check_disk() {
  local kb gb
  kb="$(df -Pk / | awk 'NR==2 {print $4}')"
  gb=$((kb / 1024 / 1024))
  if (( gb >= 20 )); then _pf_pass "Free disk space: ${gb} GB"
  elif (( gb >= 10 )); then _pf_warn "Free disk space: ${gb} GB (20 GB recommended)" "Enough to install; recordings and logs need more over time."
  else _pf_fail "Free disk space: ${gb} GB (at least 10 GB required)" "Free some space or enlarge the disk, then run the installer again."
  fi
}

# Processes this project runs itself: when re-running or resuming, their ports are "ours", not a conflict.
_pf_port_owner() { ss -Hltnp "sport = :$1" 2>/dev/null | grep -o 'users:(("[^"]*"' | head -n1 | sed 's/users:(("//'; }

pf_check_ports() {
  local port owner busy=0
  for port in 80 443 8000 5900 6080 9222; do
    owner="$(_pf_port_owner "$port")"
    if [[ -z "$owner" ]]; then continue; fi
    case "$owner" in
      caddy|uvicorn|python3|x11vnc|websockify|chrome|google-chrome) continue ;;    # this project's own services
    esac
    busy=1
    _pf_fail "Port ${port} is already in use by '${owner}'" \
      "Stop or remove that program (see: sudo ss -ltnp 'sport = :${port}'). This project needs ports 80/443 public and 8000, 5900, 6080, 9222 locally."
  done
  if (( busy == 0 )); then _pf_pass "Required ports are free (80, 443, 8000, 5900, 6080, 9222)"; fi
}

pf_check_network() {
  if getent ahosts archive.ubuntu.com >/dev/null 2>&1; then _pf_pass "DNS lookups work"
  else
    _pf_fail "DNS lookups fail" "The server cannot resolve host names. Check /etc/resolv.conf or the provider's network settings."
    return
  fi
  if curl -fsS --max-time 10 -o /dev/null https://api.ipify.org 2>/dev/null || zs_tcp_reachable 1.1.1.1 443; then
    _pf_pass "Internet connection works"
  else
    _pf_fail "No internet connection" "The installer downloads packages. Check the server's outbound network/firewall rules."
  fi
}

pf_check_sources() {
  local host failed=()
  for host in archive.ubuntu.com security.ubuntu.com dl.google.com zoom.us pypi.org files.pythonhosted.org github.com; do
    zs_tcp_reachable "$host" 443 8 || failed+=("$host")
  done
  if (( ${#failed[@]} == 0 )); then _pf_pass "Download sources reachable (Ubuntu, Google, Zoom, PyPI, GitHub)"
  else _pf_fail "Cannot reach: ${failed[*]}" "Allow outbound HTTPS (port 443) to these hosts in the server's firewall / security group."
  fi
}

pf_check_youtube() {
  if zs_tcp_reachable a.rtmps.youtube.com 443 8; then _pf_pass "YouTube live ingest reachable (a.rtmps.youtube.com:443)"
  elif zs_tcp_reachable a.rtmp.youtube.com 1935 8; then _pf_pass "YouTube live ingest reachable (a.rtmp.youtube.com:1935)"
  else
    _pf_warn "YouTube live ingest is not reachable right now" "Streaming needs outbound access to a.rtmps.youtube.com:443 (or a.rtmp.youtube.com:1935). Check the provider firewall."
  fi
}

# zs_run_preflight - runs every check; returns 0 if the installation may continue.
zs_run_preflight() {
  PF_HARD=0 PF_SOFT=0 PF_SOFT_LIST=()
  zs_title "Checking this server"
  pf_check_root
  pf_check_os
  pf_check_arch
  pf_check_systemd
  pf_check_cpu
  pf_check_ram
  pf_check_disk
  pf_check_ports
  pf_check_network
  if (( PF_HARD == 0 )); then
    pf_check_sources
    pf_check_youtube
  fi
  echo
  if (( PF_HARD > 0 )); then
    zs_fail "${PF_HARD} check(s) failed. Nothing was installed or changed."
    return 1
  fi
  if (( PF_SOFT > 0 )); then
    zs_warn "${PF_SOFT} warning(s). The installation can continue, but read them first."
  else
    zs_ok "All checks passed."
  fi
  return 0
}
