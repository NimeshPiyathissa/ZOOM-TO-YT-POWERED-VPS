# Troubleshooting

Start with the doctor - it checks every service, HTTPS, DNS, the firewall, permissions, disk and network, and
explains each problem with a fix:

```bash
sudo /opt/zoom-stream/doctor.sh
```

Useful logs:

```bash
sudo journalctl -u dashboard -n 100 --no-pager                  # dashboard
sudo journalctl -u caddy -n 100 --no-pager                      # HTTPS
Z="sudo -u zoombot XDG_RUNTIME_DIR=/run/user/$(id -u zoombot)"
$Z journalctl --user -u ffmpeg-stream -n 100 --no-pager          # encoder (also zoom, browser-source, xvfb, ...)
sudo tail -n 100 /home/zoombot/zoom-stream/logs/ffmpeg.log
sudo less /var/log/zoom-stream/install.log                      # installer
```

The **Logs** page in the dashboard shows the same, with secrets redacted.

## Installation

| Symptom | Cause and fix |
|---|---|
| "Operating system ... is not supported" | Only Ubuntu 24.04 LTS is supported. Rebuild the server with it. |
| "Port 80/443 is already in use by 'nginx'/'apache2'" | Another web server is installed. Remove it (`sudo apt purge nginx apache2`) or use a fresh server. |
| "Another program is still installing packages" | Ubuntu's automatic updates run right after first boot. Wait 5-10 minutes and run the installer again. |
| "A download failed because the network connection dropped" | Run the installer again; it resumes and retries. |
| "Cannot reach: dl.google.com zoom.us ..." | Outbound HTTPS is blocked. Allow outbound TCP 443 at the provider. |
| Domain "points to ... but this server is ..." / "has no A record yet" | Fix the DNS A record, choose `w` to wait while it updates. Or continue with `none`. Details: [DNS troubleshooting](domain-and-https.md#dns). |
| "HTTPS for ... is not working yet" at the end | The line below it names the cause (DNS, PORT, RATE LIMIT, CAA). Fix it, then `sudo bash /opt/zoom-stream/set-domain.sh --check`. See [certificate troubleshooting](domain-and-https.md#certificates). |
| A step failed | Read the "Why:" line and the log lines shown, fix the cause, run `sudo bash /opt/zoom-stream/install.sh`. |

## Dashboard

| Symptom | Cause and fix |
|---|---|
| Browser cannot connect at all | Provider firewall/security group does not allow TCP 443 (and 80 for Let's Encrypt). `doctor.sh` checks the server side. |
| Certificate warning | Expected with `self-signed` / IP-only and with `--staging` test certificates. With a domain: `sudo bash /opt/zoom-stream/set-domain.sh --check` names the problem; see [Subdomain, DNS and HTTPS](domain-and-https.md#12-troubleshooting-dns-and-certificates). |
| "Too many failed attempts" | 5 failed sign-ins lock that username from that address for 15 minutes. Wait, or reset the password ([configuration.md](configuration.md#admin-password)). |
| Forgot the admin password | `cd /home/dashboard/app && sudo -u dashboard /home/dashboard/venv/bin/python -m app.cli set-password admin` |
| Remote GUI shows "VNC authentication failed" / will not connect | The encrypted store (which holds the VNC password) could not unlock automatically. `sudo bash /opt/zoom-stream/doctor.sh` names the cause. On installs made before v1.0.3: `sudo chmod 755 /etc/zoom-stream && sudo systemctl restart dashboard` (or update). See [encryption.md](encryption.md#unlock-modes). |

## Zoom

| Symptom | Cause and fix |
|---|---|
| Stuck on "Connecting" or the Zoom home screen | Open Remote GUI and look: a dialog may need a click (audio, terms, "allow"). Try **Rejoin**. |
| "This meeting requires sign-in" | Sign in a Google account on the Accounts page and choose it for this meeting. |
| Registration link (`tk=`) rejected | Personal links are tied to the registrant's e-mail: sign in with that account. |
| Waiting room | The host must admit the bot. Nothing here bypasses it. |
| The desktop client keeps failing | Set the join method to *Web* (Zoom's web client) or *Auto*. |

## Picture and sound

| Symptom | Cause and fix |
|---|---|
| Black picture on YouTube | Check Remote GUI. If Zoom is fine, check Output framing (Meeting only + measured region). |
| Zoom toolbars visible | Turn on **Meeting only** on the Zoom page, then **Measure now** on the Output page. |
| No sound | Live Studio's audio meter must move. In Zoom, the meeting audio must be joined ("Join audio by computer"); use Remote GUI to accept the audio dialog. |
| Stuttering / low FPS / "speed" below 1.0x | The CPU is overloaded. Use 720p, a lower bitrate or `ultrafast`; avoid gallery view with many videos. Check with `sudo /home/zoombot/zoom-stream/scripts/measure-cpu.sh 60`. |
| YouTube says "no data" | Wrong stream key, or the broadcast was not started. Check `ffmpeg.log` (the key is redacted on the Logs page). |
| YouTube video: "Sign in to confirm you're not a bot" | Sign in a Google account in the kiosk's profile on Remote GUI once; it is remembered. |

## Services after a reboot

All services start automatically. If they do not, `doctor.sh` reports which one. Common causes: the dashboard
cannot unlock its encrypted store (master key missing), or linger was turned off for `zoombot`
(`sudo loginctl enable-linger zoombot`).

## Still stuck?

Open an issue with the output of `doctor.sh` and the relevant log lines. **Remove stream keys, passwords, meeting
links and your domain/IP** before posting.
