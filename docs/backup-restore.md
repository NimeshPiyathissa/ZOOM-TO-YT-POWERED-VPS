# Backup and restore

## Make a backup

```bash
sudo /opt/zoom-stream/backup.sh                       # /var/backups/zoom-stream/zs-manual-<date>.tar.gz
sudo /opt/zoom-stream/backup.sh --label before-term
sudo /opt/zoom-stream/backup.sh list
```

Safe while you are live. Each backup has a `.sha256` checksum next to it. `update.sh` makes one automatically before
every update.

**Copy backups off the server** - a backup on the same disk does not help if the server is lost:

```bash
scp -i your-key.pem 'ubuntu@203.0.113.10:/var/backups/zoom-stream/zs-*' ./
```

(The backup folder is root-only; copy the file somewhere readable first with `sudo cp ... /tmp/ && sudo chown ubuntu /tmp/zs-*`.)

## What is in a backup

| Included | Not included |
|---|---|
| Stream settings (`.env`, active source) and pipeline scripts | Logs |
| Dashboard code, database (users, saved meetings, schedules, audit log), settings | Recordings |
| Encrypted secret store | Chrome profiles (Google sign-ins) - sign in again after a restore |
| HTTPS certificate and Caddy configuration | The cached unlock key, unless you add `--include-master-key` |
| Sudoers rule, systemd units, log rotation | |

Without `--include-master-key` a backup file alone cannot reveal the secrets in the encrypted store; after
restoring on a **new** server you need the master password (see [Move to a new server](#move-to-a-new-server)).
Note that `.env` - which holds the stream key - is included as a normal file, so treat every backup as confidential.

## Restore

```bash
sudo /opt/zoom-stream/restore.sh                  # choose from a list
sudo /opt/zoom-stream/restore.sh /var/backups/zoom-stream/zs-manual-20261114-101500.tar.gz
```

The restore:

- refuses while the encoder is running;
- verifies the checksum and refuses archives that would write outside the installation's folders;
- asks you to confirm (or `--yes`);
- first makes a safety backup of the current state (`zs-pre-restore-*`), so a restore can be undone;
- restarts only the dashboard. Nothing is put on air.

## Move to a new server

1. Install on the new server with `install.sh` (same or newer version).
2. Copy the backup to `/var/backups/zoom-stream/` on the new server (and its `.sha256`).
3. `sudo /opt/zoom-stream/restore.sh <file>`.
4. If the backup did not include the master key, recreate the automatic-unlock key from your master password, then
   restart the dashboard:
   `cd /home/dashboard/app && sudo /home/dashboard/venv/bin/python -m app.cli vault set-unlock-mode cached && sudo systemctl restart dashboard`.
5. Sign in to Google accounts again on the Remote GUI if you use them.
