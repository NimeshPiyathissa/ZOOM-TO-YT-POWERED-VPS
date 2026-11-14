# Installation guide

This guide assumes no prior server experience. It takes about 30-45 minutes, most of it waiting for downloads.

1. [Get a server](#1-get-a-server)
2. [Connect to it](#2-connect-to-it)
3. [Optional: point a domain at it](#3-optional-point-a-domain-at-it)
4. [Get your YouTube stream key](#4-get-your-youtube-stream-key)
5. [Run the installer](#5-run-the-installer)
6. [Sign in and test](#6-sign-in-and-test)
7. [If something goes wrong](#7-if-something-goes-wrong)

## 1. Get a server

Rent a virtual server (VPS) from any provider - for example AWS Lightsail/EC2, DigitalOcean, Hetzner, Linode, Vultr
or OVH. Choose:

| Setting | Choose |
|---|---|
| Operating system | **Ubuntu 24.04 LTS** (x86_64 / amd64). Other systems are refused by the installer. |
| Size | **4 vCPU and 8 GB memory** (dedicated CPU if the provider offers it). 2 vCPU / 4 GB works only for light 720p use. |
| Disk | 20 GB or more |
| Login | An SSH key (the provider shows you how to create or upload one) |

Start from a **fresh** server: the installer is tested on a clean Ubuntu 24.04 image.

**Firewall at the provider.** Many providers (AWS security groups, Lightsail networking, Hetzner/DO cloud firewalls)
filter traffic before it reaches the server. Allow inbound:

| Port | Why |
|---|---|
| TCP 22 | SSH, so you can log in |
| TCP 443 | The dashboard (HTTPS) |
| TCP 80 | Only if you use a domain with Let's Encrypt (certificate checks) |

Outbound traffic must be allowed (it is by default almost everywhere).

## 2. Connect to it

Use the server's public IP address and the user your provider gives you (often `ubuntu` or `root`):

```bash
ssh -i path/to/your-key.pem ubuntu@203.0.113.10
```

On Windows 10/11 this works in PowerShell or Windows Terminal. You are connected when the prompt shows the server's
name.

## 3. Optional: point a domain at it

With a domain (for example `stream.example.com`) the dashboard gets a free, browser-trusted certificate from
Let's Encrypt. Without one you use `https://<server-ip>` and accept a one-time browser warning about a self-signed
certificate - both work the same otherwise.

At your domain registrar or DNS provider, create an **A record**:

| Type | Name | Value |
|---|---|---|
| A | `stream` (or `@` for the bare domain) | your server's public IP |

DNS can take a few minutes to update. The installer checks that the name points at the server and tells you if it
does not yet.

## 4. Get your YouTube stream key

1. Open [YouTube Studio](https://studio.youtube.com) and choose **Create > Go live**. The first time, YouTube may
   take up to 24 hours to enable live streaming on a channel.
2. Choose **Stream** (not Webcam).
3. Copy the **Stream key**. Keep it secret: anyone with it can stream to your channel.
4. The **Stream URL** shown there is normally `rtmp://a.rtmp.youtube.com/live2`; the installer defaults to the
   secure version, `rtmps://a.rtmps.youtube.com:443/live2`, which works the same.

You can also skip the key during installation and paste it into the dashboard later (Live Studio).

**Tip:** for the first tests, set the YouTube broadcast to **Unlisted**.

## 5. Run the installer

On the server:

```bash
curl -fsSL https://raw.githubusercontent.com/OWNER/REPO/main/install.sh -o install.sh
sudo bash install.sh
```

What happens:

1. **Welcome** - what will be installed, the requirements and the expected time. Press Enter to start.
2. **Server checks** - each shows ✓, ! (warning) or ✗ (problem) with a hint. Problems stop the installer before
   anything is changed; warnings let you decide.
3. **Questions** - one at a time, with an explanation and a default in `[brackets]` (press Enter to accept it).
   Passwords are hidden while you type and asked twice. A bad answer is explained and asked again.

   | Question | Notes |
   |---|---|
   | Domain name | Your domain, or `none` to use the IP address |
   | HTTPS certificate | `letsencrypt` (needs the domain and port 80) or `self-signed` |
   | Admin username / password | What you sign in to the dashboard with. 12+ characters, mixed, or a 16+ character passphrase |
   | YouTube stream URL / key | From step 4. Press Enter on the key to set it later |
   | Zoom display name | The name other participants see |
   | Output quality | 720p or 1080p; the recommendation depends on your CPU |
   | Timezone | Used by the Schedule page and the server clock |
   | VNC password | Protects the bot's remote screen. Press Enter to generate one |
   | Master encryption password | Protects the encrypted secret store. Press Enter to generate one |
   | Firewall / security updates | Recommended: yes and yes |

4. **Summary** - every answer, with passwords masked. Press Enter to install, a number to change an answer, or
   `q` to cancel without changing anything.
5. **Installation** - 13 numbered steps with a progress indicator. Full details go to
   `/var/log/zoom-stream/install.log`.
6. **Finish** - the status of every service, the dashboard address and the next steps.

Generated passwords (if you pressed Enter for them) are written to `/root/zoom-stream-credentials.txt`, readable only
by root. View them with `sudo cat /root/zoom-stream-credentials.txt`, store them in a password manager, then delete
the file with `sudo rm /root/zoom-stream-credentials.txt`.

### Unattended installation

Copy [`installer/config.example`](../installer/config.example), fill it in and protect it:

```bash
sudo cp /opt/zoom-stream/installer/config.example /root/zoom-stream.conf
sudo nano /root/zoom-stream.conf
sudo chmod 600 /root/zoom-stream.conf
sudo /opt/zoom-stream/install.sh --config /root/zoom-stream.conf
```

Every value is validated exactly like the interactive questions; if anything is wrong, all problems are listed and
nothing is installed. Delete the file afterwards.

## 6. Sign in and test

1. Open the address from the final screen (`https://stream.example.com` or `https://<server-ip>`). With a
   self-signed certificate the browser warns once - choose *Advanced > Continue*.
2. Sign in with the admin username and password you chose.
3. Follow the [usage guide](usage.md): add a meeting on the **Zoom** page, check it on **Remote GUI**, make a
   **test recording** in Live Studio, then **Go Live**.
4. Before your first real event, go through the [first live test checklist](first-live-test.md).

Run `sudo /opt/zoom-stream/doctor.sh` at any time to check the installation.

## 7. If something goes wrong

- **A step failed.** The installer prints the step, the likely reason in plain words, the last lines of the log
  and the log path. Fix the cause (for example, wait for automatic updates to finish, or free disk space) and run
  `sudo bash /opt/zoom-stream/install.sh` again. It resumes from the failed step with the answers you already gave.
- **Start over with different answers:** `sudo bash /opt/zoom-stream/install.sh --fresh`.
- **The connection dropped during installation.** Reconnect and run the installer again; it resumes.
- **The dashboard does not open.** Check the provider firewall (step 1), then run `sudo /opt/zoom-stream/doctor.sh`.

More in [troubleshooting](troubleshooting.md).
