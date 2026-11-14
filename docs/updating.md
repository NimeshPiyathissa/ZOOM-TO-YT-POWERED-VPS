# Updating

```bash
sudo /opt/zoom-stream/update.sh            # newest release
sudo /opt/zoom-stream/update.sh --check    # only show what is available
sudo /opt/zoom-stream/update.sh --ref v1.1.0
```

## What it does

1. Refuses to run while the encoder is running - stop the broadcast first. A live class is never interrupted.
2. Refuses if files in `/opt/zoom-stream` were edited by hand (so your changes are not lost silently).
3. Fetches the releases with `git` and shows the installed and available versions.
4. Asks for confirmation (or `--yes`).
5. **Backs up** the installation (`/var/backups/zoom-stream/zs-pre-update-*.tar.gz`).
6. Checks out the new version and redeploys: system packages, pipeline scripts, services, dashboard and Python
   packages.
7. Updates the database schema and restarts the dashboard.
8. Waits for the dashboard to answer its health check.

If any step fails, it **rolls back automatically**: the previous version is checked out again, the backup is
restored and redeployed, and the reason and log path are shown (`/var/log/zoom-stream/update-*.log`).

Updates never change your stream settings (`.env`), never start a broadcast, and leave running pipeline services
alone - they use the new scripts the next time they start.

## Zoom and Chrome

Google Chrome is updated by Ubuntu's normal package updates (its installer adds Google's package repository).
The Zoom client is not in a repository; update it when Zoom asks you to:

```bash
curl -fsSL -o /tmp/zoom_amd64.deb https://zoom.us/client/latest/zoom_amd64.deb
sudo apt-get install -y /tmp/zoom_amd64.deb
```

Do this when you are not live, then rejoin the meeting.

## Restoring an earlier version by hand

```bash
sudo /opt/zoom-stream/restore.sh        # pick the pre-update backup
sudo git -C /opt/zoom-stream checkout v1.0.0
sudo /opt/zoom-stream/install.sh --upgrade
```
