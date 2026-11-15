# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.0.3] - 2026-11-15

### Fixed
- **Remote GUI on fresh installs** ("VNC authentication failed"): the installer closed `/etc/zoom-stream` to root
  only, so the dashboard could not read its vault key cache there, the secret store stayed locked and no VNC password
  was available. Affects every install made with v1.0.0 - v1.0.2. The directory is enterable again (the installer's
  own `installer/` sub-directory stays private); `update.sh` repairs existing installs, and so does a restore.

- **Go Live failed with "Error opening input files: Invalid argument"** when the output was larger than the virtual
  screen (the default 1280x720 screen with a 1080p output preset): the encoder asked x11grab for 1920x1080 of a
  1280x720 screen. The capture size now always comes from the real screen and the picture is scaled to the output;
  the same applies to test recordings and to every framing mode measured on a screen of another size. With an output
  smaller than the screen, the whole screen is now captured instead of only its top-left corner.

### Added
- `scripts/capture-check.sh`: grabs one frame through the encoder's own capture settings without starting a stream.
  `doctor.sh` and the install/update health check run it, so a capture that cannot work is reported before Go Live.
- `app.cli selfcheck`: as the dashboard user, checks that the secret store unlocks automatically and that the stored
  VNC password really logs in to x11vnc. The installer's and updater's health check and `doctor.sh` now use it, so
  this kind of fault fails the install instead of passing unnoticed.
- The Remote GUI's error says when the secret store is locked instead of "no password configured".

## [1.0.2] - 2026-11-15

Audit release: the public installer was compared with a working production server and tested on a server again.

### Changed
- Caddy is installed from Caddy's official apt repository (current stable, signing-key fingerprint checked) instead of
  Ubuntu 24.04's Caddy 2.6.2. Ubuntu's package remains the automatic fallback. `update.sh` upgrades an existing
  install in place; the Caddyfile and certificates are kept.
- Indirect Python dependencies are pinned (`dashboard/constraints.txt`), so every install resolves to the same
  tested package set.
- CI uses `actions/checkout@v7` and `actions/setup-python@v7` (Node 20 actions are deprecated).

### Fixed
- Firewall step: SSH is allowed on the port(s) the server really uses before ufw is enabled. With SSH on a
  non-default port the installer could previously lock the operator out.
- `update.sh` without `--ref` no longer moves an install made from `main` back to an older release tag.
- The one-command installer run again on an unfinished install now updates the existing clone to the requested
  `--ref`; a `/opt/zoom-stream` that is not a clone of this project is refused with an explanation.
- `rsync` (used by Chrome account backup/import) is installed; more tools are verified after the package step.
- Installer tools run in the C locale, so `ufw`/`apt` output is understood on servers set to another language.
- `uninstall.sh`: an unexpected failure names its line instead of ending silently, the script ends with an explicit
  success status, service accounts are removed reliably (and it says so when one could not be), and the Chrome and
  Caddy apt sources are removed together with the packages.
- Output page: choosing an output larger than the virtual screen now says that the picture is only enlarged, and
  `docs/configuration.md` explains how to get a true 1080p screen.

## [1.0.1] - 2026-11-14

### Changed
- Repository renamed to `NimeshPiyathissa/ZOOM-TO-YT-POWERED-VPS`; installer, README, docs and issue templates use
  the new URL (GitHub redirects the old one).

### Fixed
- CI: five `A && B || true` shell lines are explicit `if` statements (shellcheck SC2015 on shellcheck 0.9.0).
- Tests pass on Linux CI: stand-in `zoombot`/`dashboard` accounts when they do not exist, RGB test pictures so
  ffmpeg 6.1 keeps odd heights, and no real X display query in the capture-framing test.

## [1.0.0] - 2026-11-14

First public release.

### Added
- Guided installer (`install.sh`) for Ubuntu 24.04: server checks with fix hints, validated questions, a summary
  screen with edit/cancel, numbered progress, a full log, plain-language failure explanations, resume after a
  failure or interruption, and unattended mode (`--config`, see `installer/config.example`).
- `update.sh` (backup, git update, redeploy, database migration, health check, automatic rollback), `backup.sh`,
  `restore.sh`, `uninstall.sh` (asks before deleting data) and `doctor.sh` (read-only diagnosis).
- Configurable YouTube ingest URL (`YT_RTMP_URL`), validated by the dashboard and the encoder script.
- Configurable schedule timezone: the Schedule page follows the server timezone chosen during installation
  (override with `SCHEDULE_TIMEZONE`).
- Non-interactive admin password setup: `app.cli set-password --password-stdin` or `ZOOMBOT_ADMIN_PASSWORD`.
- Subdomain and HTTPS wizard: shows the server's public IP and the DNS A record to create, checks public DNS with
  wait-and-retry (explains missing/wrong/proxied/extra/AAAA records), checks ports 80/443, verifies HTTPS after
  installation and explains DNS, port, rate-limit and CAA certificate errors. Caddy issues and renews the
  Let's Encrypt certificate automatically; IP-only mode keeps a clear warning.
- `--staging` / `LE_STAGING=yes`: Let's Encrypt staging certificates for repeated test installs.
- `set-domain.sh`: add, change or remove the domain after installation (`--none`, `--check`), reloading only Caddy.
- `docs/domain-and-https.md`: beginner guide to subdomains, DNS A records, HTTPS and troubleshooting.
- Daily disk clean-up timer (`zoom-vacuum.timer`) installed and enabled by default.
- Documentation set, security policy, contribution guide, issue and pull-request templates.

### Fixed
- A successful sign-in cleared **all** login records for that user and address; it now clears only the failed
  attempts, so successful sign-ins remain in the record.
- `python -m app.cli set-password` created a user literally named `set-password` instead of resetting the admin
  password.
- Installer: Ubuntu's stock `/etc/caddy/Caddyfile` could block HTTPS setup on a fresh server; package-default files
  are now replaced (with a backup), hand-edited ones still need explicit confirmation.
- Installer: apt calls wait for the package lock instead of failing while automatic updates run.
- Tests no longer depend on the machine's DNS resolver for `example.com`.
- Found on the release test server: on dual-stack (IPv4 + IPv6) servers the IP-only self-signed certificate was
  issued for the IPv6 address and the DNS check warned falsely; public-IP detection is now IPv4-only.
- `zoom.service` failed at boot when the active source was a web page; it now exits cleanly.
- all tools accept `--no-color`; the installer's DNS menu no longer repeats its explanation after
  a wait timeout; `set-domain.sh --check` says "Checking", and the provider-firewall hint names Linode.
- `doctor.sh`: a stray line continuation hid the "firewall blocks HTTPS" result.

### Changed
- The application is installed as a git clone (`/opt/zoom-stream`) so it can be updated in place.
- Overview card and Schedule page wording now describe only what the software does: the watermark studio is
  text/logo watermarks, and schedules prepare and end a meeting but never start the broadcast.
- All shell scripts pass shellcheck at every severity; CI runs shellcheck, ruff and the test suite on Ubuntu 24.04
  with Python 3.12.

### Removed
- Unused optional modules and their pages, an inert licensing hook, internal planning documents and an unattributed
  background image.
