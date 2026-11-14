#!/usr/bin/env bash
# The installation steps. Each zs_step_* function runs in its own subshell with strict mode, its output going only to
# the install log. Every step checks current state first, so running it again is always safe.
#
# Never here: starting the broadcast encoder (ffmpeg-stream), printing or logging a secret, `set -x`.
#
# Globals provided by install.sh: ZS_SRC (the git checkout), ANS_* (answers), ZS_MODE (install|upgrade).

_record_version() {   # _record_version NAME VALUE
  mkdir -p "$ZS_STATE_DIR"
  touch "$ZS_VERSIONS_FILE"
  chmod 600 "$ZS_VERSIONS_FILE"
  sed -i "/^$1=/d" "$ZS_VERSIONS_FILE"
  printf '%s=%s\n' "$1" "$2" >>"$ZS_VERSIONS_FILE"
}

_pick_pkg() { local p; for p in "$@"; do apt-cache show "$p" &>/dev/null && { echo "$p"; return 0; }; done; echo "$1"; }

# ------------------------------------------------------------------ 1. system packages

zs_step_packages() {
  zs_apt update
  local asound gtk
  asound="$(_pick_pkg libasound2t64 libasound2)"
  gtk="$(_pick_pkg libgtk-3-0t64 libgtk-3-0)"
  zs_apt install --no-install-recommends \
    xvfb openbox x11vnc wmctrl xdotool x11-xserver-utils x11-utils unclutter \
    at-spi2-core python3-pyatspi gir1.2-atspi-2.0 \
    ffmpeg fontconfig \
    pipewire pipewire-pulse wireplumber pipewire-audio-client-libraries pulseaudio-utils \
    dbus-user-session dbus-x11 \
    fonts-liberation "$asound" libnss3 libxss1 "$gtk" \
    python3 python3-venv python3-pip websockify openssl \
    wget curl ca-certificates gnupg ufw jq logrotate git sudo iproute2 psmisc bind9-dnsutils caddy
  local c missing=()
  for c in Xvfb openbox x11vnc xdotool wmctrl ffmpeg ffprobe pactl python3 websockify jq ufw git caddy curl; do
    command -v "$c" >/dev/null || missing+=("$c")
  done
  if (( ${#missing[@]} )); then echo "missing after install: ${missing[*]}"; return 1; fi
  _record_version ffmpeg "$(ffmpeg -version | head -n1 | awk '{print $3}')"
  _record_version python3 "$(python3 -c 'import sys; print(sys.version.split()[0])')"
  _record_version caddy "$(caddy version 2>/dev/null | awk '{print $1}')"
}

# ------------------------------------------------------------------ 2. Google Chrome (official .deb, adds Google's apt repo)

zs_step_chrome() {
  if ! dpkg -s google-chrome-stable &>/dev/null; then
    local deb
    deb="$(mktemp --suffix=.deb)"
    zs_download "https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb" "$deb"
    zs_apt install "$deb"
    rm -f "$deb"
  fi
  google-chrome-stable --version
  _record_version google-chrome "$(google-chrome-stable --version | awk '{print $NF}')"
}

# ------------------------------------------------------------------ 3. Zoom Linux client (official .deb)

zs_step_zoom() {
  if ! dpkg -s zoom &>/dev/null; then
    local deb
    deb="$(mktemp --suffix=.deb)"
    zs_download "https://zoom.us/client/latest/zoom_amd64.deb" "$deb"
    zs_apt install "$deb"
    rm -f "$deb"
  fi
  dpkg-query -W -f='${Version}\n' zoom
  [[ -x /opt/zoom/zoom || -x /usr/bin/zoom ]]
  _record_version zoom "$(dpkg-query -W -f='${Version}' zoom)"
}

# ------------------------------------------------------------------ 4. service user

zs_step_users() {
  if ! id "$ZS_SVC_USER" &>/dev/null; then
    adduser --system --group --home "$ZS_SVC_HOME" --shell /bin/bash "$ZS_SVC_USER"
  fi
  mkdir -p "$ZS_SVC_HOME"
  chown "$ZS_SVC_USER:$ZS_SVC_USER" "$ZS_SVC_HOME"
  chmod 750 "$ZS_SVC_HOME"
  loginctl enable-linger "$ZS_SVC_USER"
  [[ "$(loginctl show-user "$ZS_SVC_USER" --property=Linger --value 2>/dev/null)" == "yes" ]]
}

# ------------------------------------------------------------------ 5. pipeline files + settings

_env_quote() { local v="${1//\\/\\\\}"; v="${v//\"/\\\"}"; printf '"%s"' "$v"; }

# zs_write_stream_env APP_DIR - the ONLY place the pipeline .env is written: a fresh install that has none yet.
# An existing .env (upgrade, re-run, reinstall over kept data) is never touched, so chosen settings are kept.
zs_write_stream_env() {
  local dir="$1" profile=720p30 tmp
  if [[ "$ZS_MODE" != "install" || -f "$dir/.env" ]]; then echo "keeping existing stream settings"; return 0; fi
  # shellcheck source=../../scripts/stream-profiles.sh
  source "$ZS_SRC/scripts/stream-profiles.sh"
  if [[ "$(zs_get OUTPUT_PRESET)" == "1080p" ]]; then profile=1080p30; fi
  tmp="$(mktemp)"
  chmod 600 "$tmp"
  {
    printf 'BOT_NAME=%s\n' "$(_env_quote "$(zs_get BOT_NAME)")"
    printf 'ZOOM_SIGNIN_MODE=guest\n'
    stream_profile_env "$profile" ""
    printf 'DISPLAY_NUM=:99\n'
    if [[ "$(zs_get YT_RTMP_URL)" != "$ZS_DEFAULT_RTMP_URL" ]]; then
      printf 'YT_RTMP_URL=%s\n' "$(_env_quote "$(zs_get YT_RTMP_URL)")"
    fi
    if [[ -n "$(zs_get YT_STREAM_KEY)" ]]; then
      printf 'YT_STREAM_KEY=%s\n' "$(_env_quote "$(zs_get YT_STREAM_KEY)")"
    fi
  } >"$tmp"
  install -o "$ZS_SVC_USER" -g "$ZS_SVC_USER" -m 600 "$tmp" "$dir/.env"
  rm -f "$tmp"
  echo "wrote $dir/.env (profile ${profile})"
}

zs_step_pipeline() {
  mkdir -p "$ZS_APP_DIR/scripts" "$ZS_APP_DIR/logs"
  cp -r "$ZS_SRC/scripts/." "$ZS_APP_DIR/scripts/"
  rm -rf "$ZS_APP_DIR/extensions"
  cp -r "$ZS_SRC/extensions" "$ZS_APP_DIR/"
  chmod +x "$ZS_APP_DIR"/scripts/*.sh
  zs_write_stream_env "$ZS_APP_DIR"
  if [[ ! -f "$ZS_APP_DIR/current-source.env" ]]; then
    # A visible page until a real source is chosen, so the Remote GUI never shows a black screen.
    printf 'SOURCE_TYPE=webpage\nWEBPAGE_URL=https://www.google.com\nWEBPAGE_ZOOM=1.0\nWEBPAGE_RELOAD_SECONDS=0\n' \
      >"$ZS_APP_DIR/current-source.env"
  fi
  chown -R "$ZS_SVC_USER:$ZS_SVC_USER" "$ZS_SVC_HOME"
  chmod 600 "$ZS_APP_DIR/.env" 2>/dev/null || true

  # Log rotation (copytruncate: never restarts anything, safe during a live stream).
  local tmpl="$ZS_SRC/scripts/logrotate-zoom-stream.conf" out
  out="$(mktemp)"
  sed -e "s|@LOG_DIR@|${ZS_APP_DIR}/logs|g" -e "s|@SVC_USER@|${ZS_SVC_USER}|g" "$tmpl" >"$out"
  if grep -q '@[A-Z_]*@' "$out" || ! logrotate -d "$out" >/dev/null 2>&1; then
    echo "logrotate template did not render cleanly"; rm -f "$out"; return 1
  fi
  install -o root -g root -m 644 "$out" /etc/logrotate.d/zoom-stream
  rm -f "$out"
}

# ------------------------------------------------------------------ 6. systemd --user units (enabled, NOT started here)

zs_step_units() {
  local unit_dir="$ZS_SVC_HOME/.config/systemd/user" f name uid
  mkdir -p "$unit_dir"
  for f in "$ZS_SRC"/systemd/*.service "$ZS_SRC"/systemd/*.timer; do
    name="$(basename "$f")"
    [[ "$name" == "dashboard.service" ]] && continue
    install -m 644 "$f" "$unit_dir/$name"
  done
  chown -R "$ZS_SVC_USER:$ZS_SVC_USER" "$ZS_SVC_HOME/.config"
  uid="$(id -u "$ZS_SVC_USER")"
  systemctl start "user@${uid}.service"
  for _ in 1 2 3 4 5 6 7 8 9 10; do [[ -S "/run/user/${uid}/bus" ]] && break; sleep 1; done
  zs_user_systemctl daemon-reload
  for f in "$ZS_SRC"/systemd/*.service; do
    name="$(basename "$f")"
    case "$name" in dashboard.service|zoom-vacuum.service) continue ;; esac
    # ffmpeg-stream.service has no [Install] section: enabling it is a no-op, it can never start at boot.
    zs_user_systemctl enable "$name"
  done
  zs_user_systemctl enable zoom-vacuum.timer
  zs_user_systemctl start zoom-vacuum.timer
}

# ------------------------------------------------------------------ 7. timezone

zs_step_timezone() {
  timedatectl set-timezone "$(zs_get TIMEZONE)"
  [[ "$(timedatectl show -p Timezone --value)" == "$(zs_get TIMEZONE)" ]]
}

# ------------------------------------------------------------------ 8. firewall

zs_step_firewall() {
  if [[ "$(zs_get FIREWALL)" != "yes" ]]; then echo "firewall: skipped by choice"; return 0; fi
  ufw allow OpenSSH
  ufw allow 443/tcp
  if [[ "$(zs_get SSL_MODE)" == "letsencrypt" ]]; then ufw allow 80/tcp; fi
  ufw --force enable
  ufw status verbose
}

# ------------------------------------------------------------------ 9. automatic security updates

zs_step_autoupdates() {
  if [[ "$(zs_get AUTO_UPDATES)" != "yes" ]]; then echo "automatic updates: skipped by choice"; return 0; fi
  zs_apt install unattended-upgrades
  cat >/etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
  systemctl enable --now unattended-upgrades.service
}

# ------------------------------------------------------------------ 10. dashboard, secret vault, admin account

zs_step_dashboard() {
  local mode="install"
  [[ "$ZS_MODE" == "upgrade" ]] && mode="upgrade"
  # install-dashboard.sh runs `app.cli setup` non-interactively from these variables (never argv, never logged).
  ZOOMBOT_MASTER_PASSWORD="$(zs_get MASTER_PASSWORD)" \
  ZOOMBOT_ADMIN_USERNAME="$(zs_get ADMIN_USERNAME)" \
  ZOOMBOT_ADMIN_PASSWORD="$(zs_get ADMIN_PASSWORD)" \
  ZOOMBOT_VNC_PASSWORD="$(zs_get VNC_PASSWORD)" \
  ZOOMBOT_UNLOCK_MODE=cached \
    bash "$ZS_SRC/dashboard/install-dashboard.sh" "$mode"
  [[ "$ZS_MODE" == "upgrade" ]] && return 0
  # Make sure the admin account exists with exactly the chosen password, even if an earlier interrupted run had
  # already initialised the vault (setup then skips itself). Hashing is done by the app itself (app.cli set-password).
  printf '%s\n' "$(zs_get ADMIN_PASSWORD)" | (cd "$ZS_DASH_HOME/app" && \
    sudo -u "$ZS_DASH_USER" "$ZS_DASH_HOME/venv/bin/python" -m app.cli set-password \
      --username "$(zs_get ADMIN_USERNAME)" --password-stdin)
  systemctl is-enabled --quiet dashboard.service
}

# ------------------------------------------------------------------ 11. HTTPS (Caddy)

zs_step_https() {
  local args=()
  if [[ "$ZS_MODE" == "upgrade" ]]; then
    if [[ -f /etc/caddy/Caddyfile ]]; then echo "upgrade: existing Caddyfile kept"; return 0; fi
  fi
  if [[ "$(zs_get SSL_MODE)" == "letsencrypt" ]]; then
    args+=(--domain "$(zs_get DOMAIN)")
    [[ -n "$(zs_get LE_EMAIL)" ]] && args+=(--email "$(zs_get LE_EMAIL)")
    [[ "$(zs_get LE_STAGING)" == "yes" ]] && args+=(--staging)
  fi
  # Ubuntu's caddy package ships a placeholder Caddyfile; replacing it is safe (setup-https.sh keeps a backup).
  # A Caddyfile someone edited by hand is never replaced without the operator's explicit yes.
  if [[ -f /etc/caddy/Caddyfile ]] && ! dpkg -V caddy 2>/dev/null | grep -q '/etc/caddy/Caddyfile'; then
    args+=(--yes)
  fi
  bash "$ZS_SRC/scripts/setup-https.sh" "${args[@]}"
  grep -q '^# managed-by: zoom-stream setup-https.sh' /etc/caddy/Caddyfile
  systemctl is-active --quiet caddy
}

# ------------------------------------------------------------------ 12. start the display / remote-screen services

zs_step_services() {
  local u
  # The encoder (ffmpeg-stream) is deliberately NOT in this list: going live always needs a confirmed Go Live.
  for u in xvfb openbox audio-setup browser-source x11vnc novnc-proxy; do
    zs_user_systemctl start "$u"
    sleep 1
  done
  systemctl restart dashboard.service
}

# ------------------------------------------------------------------ 13. health check

zs_step_verify() {
  local ok=0
  for _ in $(seq 1 30); do
    if curl -fsS --max-time 3 http://127.0.0.1:8000/health >/dev/null 2>&1; then ok=1; break; fi
    sleep 2
  done
  (( ok )) || { echo "dashboard did not answer on 127.0.0.1:8000/health"; return 1; }
  local u
  for u in xvfb openbox audio-setup x11vnc novnc-proxy; do
    zs_user_systemctl is-active --quiet "$u" || { echo "service $u is not running"; zs_user_systemctl status "$u" --no-pager || true; return 1; }
  done
  if zs_user_systemctl is-active --quiet ffmpeg-stream; then echo "encoder is running - it must not be"; return 1; fi
  echo "health check passed"
}

# ------------------------------------------------------------------ database migrations (update.sh)

zs_step_migrate() {
  # db.init_db() creates missing tables/columns idempotently; running it here (not only at dashboard start-up)
  # makes a failing migration fail the update, which then rolls back.
  (cd "$ZS_DASH_HOME/app" && sudo -u "$ZS_DASH_USER" "$ZS_DASH_HOME/venv/bin/python" -c \
    'from app import db; db.init_db(); print("database schema up to date")')
}
