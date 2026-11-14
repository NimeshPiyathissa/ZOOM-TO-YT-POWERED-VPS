# Installation guide

This guide assumes no prior server experience. It takes about 30-45 minutes, most of it waiting for downloads.

1. [Get a server](#1-get-a-server)
2. [Connect to it](#2-connect-to-it)
3. [Optional: point a (sub)domain at it](#3-optional-point-a-domain-at-it)
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

With a domain - usually a **subdomain** such as `stream.example.com` - the dashboard gets a free, browser-trusted
certificate from Let's Encrypt, renewed automatically. Without one you use `https://<server-ip>` and the browser warns
about a self-signed certificate. Everything else works the same, and you can add a domain later.

Do this **before** running the installer so DNS has time to update:

1. Note the server's **public IPv4 address** (provider control panel; on AWS attach an Elastic IP first).
2. Where you manage your domain's DNS, create an **A record**:

   | Type | Name / Host | Value | TTL |
   |---|---|---|---|
   | A | `stream` | your server's public IP | Auto / 300 |

   Cloudflare: set *Proxy status* to **DNS only** (grey cloud). Remove any other A/AAAA record with the same name.
3. Make sure TCP **80 and 443** are open at the provider (step 1).

The installer shows the IP and this exact record again, checks DNS, and can wait while it updates.
Step-by-step with provider notes and troubleshooting: **[Subdomain, DNS and HTTPS](domain-and-https.md)**.

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
   | Domain name (subdomain) | e.g. `stream.example.com`, or `none` for IP-only. Shows the server IP and the A record to create, then checks DNS - see below |
   | HTTPS certificate | `letsencrypt` (trusted, automatic renewal; needs ports 80 + 443) or `self-signed` |
   | Let's Encrypt e-mail | Only for expiry notices |
   | Admin username / password | What you sign in to the dashboard with. 12+ characters, mixed, or a 16+ character passphrase |
   | YouTube stream URL / key | From step 4. Press Enter on the key to set it later |
   | Zoom display name | The name other participants see |
   | Output quality | 720p or 1080p; the recommendation depends on your CPU |
   | Timezone | Used by the Schedule page and the server clock |
   | VNC password | Protects the bot's remote screen. Press Enter to generate one |
   | Master encryption password | Protects the encrypted secret store. Press Enter to generate one |
   | Firewall / security updates | Recommended: yes and yes |

   **DNS check.** If the name does not point at this server yet, the wizard says why (no record, wrong IP,
   Cloudflare proxy, extra record) and offers: `w` wait and re-check automatically for up to 10 minutes, `r` re-check
   now, `e` enter another name, `c` continue anyway, `n` no domain (IP-only).

4. **Summary** - every answer, with passwords masked. Press Enter to install, a number to change an answer, or
   `q` to cancel without changing anything.
5. **Installation** - 13 numbered steps with a progress indicator. Full details go to
   `/var/log/zoom-stream/install.log`.
6. **Checking HTTPS** - firewall and reachability of ports 80/443, DNS, then it waits for the certificate and loads
   the dashboard over HTTPS. Problems are explained with the fix (see
   [troubleshooting](domain-and-https.md#12-troubleshooting-dns-and-certificates)).
7. **Finish** - the status of every service, the dashboard address and the next steps.

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

**Testing the installer repeatedly with the same subdomain?** Add `--staging` (or `LE_STAGING=yes` in the config
file) to use Let's Encrypt's test service and avoid its rate limits. Staging certificates are not browser-trusted;
switch to a real one later with `sudo bash /opt/zoom-stream/set-domain.sh stream.example.com`.

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
- **DNS or certificate problems.** Run `sudo bash /opt/zoom-stream/set-domain.sh --check`, then see
  [DNS and certificate troubleshooting](domain-and-https.md#12-troubleshooting-dns-and-certificates).
- **Add, change or remove the domain later:** `sudo bash /opt/zoom-stream/set-domain.sh stream.example.com`
  (or `--none`). It does not interrupt a running broadcast.

More in [troubleshooting](troubleshooting.md).
