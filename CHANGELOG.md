# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.0.0] - 2026-11-XX

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

### Changed
- The application is installed as a git clone (`/opt/zoom-stream`) so it can be updated in place.
- Overview card and Schedule page wording now describe only what the software does: the watermark studio is
  text/logo watermarks, and schedules prepare and end a meeting but never start the broadcast.
- All shell scripts pass shellcheck at every severity; CI runs shellcheck, ruff and the test suite on Ubuntu 24.04
  with Python 3.12.

### Removed
- Unused optional modules and their pages, an inert licensing hook, internal planning documents and an unattributed
  background image.
