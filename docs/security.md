# Security

## What is exposed

| Port | Who | Notes |
|---|---|---|
| 443/tcp | Internet | Caddy (HTTPS) → dashboard on 127.0.0.1:8000 |
| 80/tcp | Internet | Only with Let's Encrypt; Caddy redirects to HTTPS and answers certificate checks |
| 22/tcp | Internet | SSH (yours) |
| 8000, 5900, 6080, 9222 | 127.0.0.1 only | Dashboard, x11vnc, websockify, Chrome DevTools - never public |

With the firewall option, `ufw` allows only SSH, 443 and (for Let's Encrypt) 80.

## Dashboard login

- Passwords hashed with **argon2**; minimum 12 characters (installer: mixed characters or 16+).
- **Lockout**: 5 failed sign-ins for the same username from the same address within 15 minutes block further tries
  from there. A successful sign-in clears only the failed attempts - successful sign-ins stay in the record.
- Sessions: `Secure`, `HttpOnly`, `SameSite` cookies; idle and absolute timeouts; a password change revokes all of
  that user's sessions.
- **CSRF** tokens on every state-changing request; destructive actions are POST-only.
- Security headers (HSTS, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`) and a Content Security Policy.
- Every sign-in and change is written to the audit log with the client address.

## Privilege separation

The pipeline runs as `zoombot`, the dashboard as `dashboard`; neither is root. The dashboard reaches the pipeline only
through `/etc/sudoers.d/dashboard`, which lists exact commands: specific `systemctl --user` verbs on specific units,
and specific scripts. Read-only commands carry `NOEXEC`. The file is generated and checked with `visudo -c` before
installation. `dashboard.service` additionally caps its capabilities to what `sudo` needs.

## Secrets at rest

| Secret | Where | Protection |
|---|---|---|
| Dashboard passwords | `dashboard.db` | argon2 hash |
| VNC password | `secrets.enc.json` | AES-256-GCM, key from argon2id ([encryption.md](encryption.md)) |
| YouTube stream key, active Zoom link/passcode | `/home/zoombot/zoom-stream/.env` | file mode 600, owner `zoombot` |
| Saved Zoom links/passcodes | `dashboard.db` in `/home/dashboard/data` | directory mode 700, owner `dashboard`; masked in the UI |
| Telegram token | `/home/dashboard/app/settings.json` | file mode 600, owner `dashboard`; masked in the UI |
| Generated install passwords | `/root/zoom-stream-credentials.txt` | root only - delete it after saving them |

Secrets are never put on command lines (they are passed through environment variables or stdin), are redacted from
the dashboard's log viewer, and are never printed by the installer. `ffmpeg.log` can contain the full ingest URL with
the key, so the logs folder is not world-readable.

## Untrusted input

- Source URLs are checked before they reach Chrome or FFmpeg: allowed schemes only, and hosts that resolve to
  loopback, private, link-local or cloud-metadata addresses are refused (SSRF protection).
- Values written into `.env` / `current-source.env` are validated, and the files are parsed, never `source`d.
- Watermark text, fonts and positions are re-validated by the shell before they reach FFmpeg's filter graph.
- Backup archives are checksum-verified and refused if they contain paths outside the installation.

## Hardening checklist

- Use SSH keys only; disable password SSH logins.
- Keep the firewall and automatic security updates on (installer defaults).
- Use a long, unique dashboard password; consider putting the dashboard behind a VPN (for example Tailscale) if
  only a few people need it.
- Store generated passwords in a password manager and delete `/root/zoom-stream-credentials.txt`.
- On cloud servers, require IMDSv2 (AWS) or the provider's equivalent metadata protection.
- Copy backups off the server and keep them private (they contain the stream key).

## Reporting a vulnerability

See [SECURITY.md](../SECURITY.md).
