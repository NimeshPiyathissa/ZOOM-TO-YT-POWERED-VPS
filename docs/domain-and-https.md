# Subdomain, DNS and HTTPS - step by step

This guide shows how to open the dashboard at an address like **`https://stream.example.com`** with a free,
browser-trusted certificate. No previous DNS experience is needed. It takes about 10 minutes plus DNS waiting time.

1. [How it works](#1-how-it-works)
2. [Choose a subdomain](#2-choose-a-subdomain)
3. [Find your server's public IP address](#3-find-your-servers-public-ip-address)
4. [Create the DNS A record](#4-create-the-dns-a-record)
5. [Check that DNS works](#5-check-that-dns-works)
6. [Open ports 80 and 443](#6-open-ports-80-and-443)
7. [Run the installer wizard](#7-run-the-installer-wizard)
8. [Open the dashboard over HTTPS](#8-open-the-dashboard-over-https)
9. [No domain? IP-only mode](#9-no-domain-ip-only-mode)
10. [Add, change or remove the domain later](#10-add-change-or-remove-the-domain-later)
11. [Test installs: staging certificates](#11-test-installs-staging-certificates)
12. [Troubleshooting DNS and certificates](#12-troubleshooting-dns-and-certificates)

## 1. How it works

```
 your browser ──https://stream.example.com──▶ DNS says "stream.example.com = 203.0.113.10"
                                                   │
                                                   ▼
                                      your server 203.0.113.10
                                      Caddy (ports 80 + 443)  ── HTTPS certificate from Let's Encrypt,
                                           │                     renewed automatically
                                           ▼
                                      dashboard (127.0.0.1:8000, never exposed directly)
```

- **DNS** turns the name into your server's address. You set this up once, at the company where you manage your
  domain.
- **Caddy** is the web server ("reverse proxy") the installer puts in front of the dashboard. It gets the certificate
  from **Let's Encrypt** (free), renews it automatically about 30 days before it expires, and redirects `http://` to
  `https://`. You never have to renew anything by hand.
- Let's Encrypt only gives a certificate after it has checked that the name really points at your server, through
  port 80 or 443. That is why DNS and both ports must be right **before** the certificate can be issued.

## 2. Choose a subdomain

You need a domain you control, for example `example.com` (bought from a registrar such as Namecheap, GoDaddy,
Cloudflare, Google/Squarespace Domains, Porkbun or your local registrar).

Use a **subdomain** for the dashboard so the rest of your domain (your website, e-mail) is not affected:

| Good choices | Full address |
|---|---|
| `stream` | `stream.example.com` |
| `live` | `live.example.com` |
| `zoom` | `zoom.example.com` |

You do not need to "create" the subdomain anywhere else - the DNS record in step 4 is all it takes.

## 3. Find your server's public IP address

The **public IPv4 address** looks like `203.0.113.10`. You can find it:

- in your provider's control panel (often labelled *Public IPv4*, *IPv4 address* or *Elastic IP*), or
- on the server: `curl -4 https://api.ipify.org`
- The installer also **shows it** when it asks for the domain.

**AWS users:** a normal EC2 public IP changes when you stop and start the instance. Attach an **Elastic IP** first,
otherwise your DNS record will point at the wrong address after a restart. (Lightsail: attach a *static IP*.)

## 4. Create the DNS A record

Log in where you manage your domain's DNS (registrar or DNS provider), open the DNS settings for `example.com`
and **add a record**:

| Field | What to enter | Example |
|---|---|---|
| Type | `A` | A |
| Name / Host | only the subdomain part | `stream` |
| Value / Points to / IPv4 address | your server's public IP | `203.0.113.10` |
| TTL | Auto, or 300 seconds | Auto |

Notes for common providers:

- **Most providers** add `.example.com` to the name for you - type only `stream`. If a provider asks for the full
  name, type `stream.example.com`. If you end up with `stream.example.com.example.com`, you typed too much.
- **Cloudflare:** set **Proxy status** to **DNS only** (grey cloud). With the orange cloud, Cloudflare answers instead
  of your server and the certificate check fails.
- **GoDaddy / Namecheap:** "Host" = `stream`, "Points to"/"Value" = the IP.
- **AWS Route 53:** "Record name" = `stream`, "Record type" = A, "Value" = the IP, routing policy *Simple*.
- Delete any **other A or AAAA (IPv6) record with the same name** - for example one pointing at an old server.

The installer prints this exact record for your name and IP, so you can copy it.

## 5. Check that DNS works

New records usually work within a few minutes; some providers take up to an hour. Check from your computer:

```bash
nslookup stream.example.com 1.1.1.1
```

or on the server:

```bash
dig +short A stream.example.com @1.1.1.1
```

It must answer with your server's IP **and nothing else**. You can also use a web checker such as
<https://dnschecker.org>. You do not have to do this by hand - the installer checks it and can wait for you.

## 6. Open ports 80 and 443

Let's Encrypt and your browser must be able to reach the server on:

| Port | Why |
|---|---|
| TCP 443 | HTTPS - the dashboard |
| TCP 80 | Let's Encrypt's certificate check and the `http://` → `https://` redirect |

- **On the server**, the installer opens both in `ufw` (if you let it enable the firewall).
- **At the provider**, many servers have a second firewall you must change yourself. Allow inbound TCP 80 and 443 from
  anywhere (`0.0.0.0/0`):
  - AWS EC2: *Security group > Inbound rules* · Lightsail: *Networking > IPv4 Firewall*
  - Google Cloud: *VPC network > Firewall* · Azure: *Network security group* · Oracle: *Security list*
  - DigitalOcean / Hetzner / Vultr: *Cloud firewall* (only if you created one)

## 7. Run the installer wizard

Run the installer as described in the [installation guide](installation.md#5-run-the-installer). The first question
is the domain:

```
Domain name (subdomain)
  The address you will open the dashboard at - a subdomain you control, e.g. stream.example.com.
  With a domain you get a free, browser-trusted HTTPS certificate (Let's Encrypt), renewed automatically.
  This server's public IP address: 203.0.113.10
  Type 'none' to use the IP address only (self-signed certificate, browser warning). You can add a domain later.
  Domain name, or 'none' [none]: stream.example.com

  Create this DNS record where you manage example.com (your domain registrar or DNS provider):

      Type   Name / Host            Value / Points to  TTL
      A      stream                 203.0.113.10       Auto (or 300)

  ✓ DNS: stream.example.com points to this server (203.0.113.10).
```

If DNS does not point at the server yet, the wizard explains why (no record yet, wrong IP, Cloudflare proxy, an extra
record, an IPv6 record elsewhere) and offers:

| Key | What it does |
|---|---|
| `w` (or Enter) | **Wait**: re-check every 15 seconds for up to 10 minutes while DNS updates |
| `r` | Re-check once now |
| `e` | Enter a different name (for example after a typo) |
| `c` | Continue anyway - Caddy keeps trying and gets the certificate as soon as DNS is right |
| `n` | No domain - use IP-only mode instead |

Then the wizard asks:

- **HTTPS certificate** - choose `letsencrypt` (recommended). `self-signed` keeps the domain but gives a browser
  warning.
- **E-mail for Let's Encrypt** - used only for expiry warnings, which you should normally never receive because Caddy
  renews automatically.

At the end of the installation the installer **checks HTTPS for real**:

```
Checking HTTPS
  ✓ Firewall (ufw) allows port 80
  ✓ Firewall (ufw) allows port 443
  ✓ Port(s) 80, 443 reachable on 203.0.113.10
  ✓ DNS: stream.example.com points to this server (203.0.113.10).
  • Requesting the HTTPS certificate for stream.example.com from Let's Encrypt - usually under a minute...
  ✓ HTTPS works with a trusted certificate: https://stream.example.com
```

If something is wrong it says what (DNS, a closed port, a rate limit, CAA) and how to fix it. Everything else is
installed anyway; fix the cause and run `sudo bash /opt/zoom-stream/set-domain.sh --check`.

**Unattended install (`--config`):** set `DOMAIN=stream.example.com`, `SSL_MODE=letsencrypt` and `LE_EMAIL=...` in the
config file. A DNS mismatch is a warning; the install stops unless you also set `ACCEPT_WARNINGS=yes`.

## 8. Open the dashboard over HTTPS

Open **`https://stream.example.com`** and sign in. The browser shows a padlock and no warning. `http://` addresses are
redirected to `https://` automatically.

Check the certificate and its renewal at any time:

```bash
sudo /opt/zoom-stream/doctor.sh                  # includes DNS, ports, HTTPS and days until the certificate expires
sudo bash /opt/zoom-stream/set-domain.sh --check  # just the domain/DNS/port/HTTPS checks
```

## 9. No domain? IP-only mode

Type `none` at the domain question. The dashboard is then at **`https://<server-ip>`** with a **self-signed**
certificate:

- The connection is encrypted, but browsers show **"Not secure" / "Your connection is not private"** every time you
  open it on a new device, because a self-signed certificate cannot prove the server's identity. Choose
  *Advanced > Continue* to open it.
- Only port 443 is needed.
- Fine for testing or private use; for regular use or other people, add a domain (next section).

## 10. Add, change or remove the domain later

`set-domain.sh` does steps 4-8 for an existing installation. It only reloads Caddy - the dashboard and any running
broadcast are not interrupted.

```bash
# add a domain, or switch to a different one (shows the record to create, checks DNS, gets the certificate)
sudo bash /opt/zoom-stream/set-domain.sh stream.example.com --email you@example.com

# back to IP-only (self-signed)
sudo bash /opt/zoom-stream/set-domain.sh --none

# check DNS, ports and HTTPS as currently configured
sudo bash /opt/zoom-stream/set-domain.sh --check
```

Changing the name does not affect your settings, meetings or recordings. Browsers remember sign-ins per address, so
sign in again at the new address.

## 11. Test installs: staging certificates

Let's Encrypt limits how many certificates you can get for the same name (5 identical certificates per week, and a
few failed checks per hour). If you install the same subdomain again and again for testing, use **staging**:

```bash
sudo bash install.sh --staging                                        # new install
sudo bash /opt/zoom-stream/set-domain.sh stream.example.com --staging # existing install
```

Staging certificates come from Let's Encrypt's test service with much higher limits, but **browsers do not trust them**
(you see a warning - this is expected). The installer and `doctor.sh` say clearly when staging is in use. When testing
is done, switch to a real certificate:

```bash
sudo bash /opt/zoom-stream/set-domain.sh stream.example.com
```

In unattended mode, set `LE_STAGING=yes` in the config file.

## 12. Troubleshooting DNS and certificates

Run `sudo bash /opt/zoom-stream/set-domain.sh --check` first - it prints the exact problem and fix. Caddy's own log:
`sudo journalctl -u caddy -n 80 --no-pager`.

### DNS

| What you see | Cause | Fix |
|---|---|---|
| "has no A record yet" | Record missing, not yet visible, typo in the name, or added to the wrong domain | Check the record (section 4). Wait 5-15 minutes and re-check. Confirm the domain's nameservers are the provider where you added it. |
| "points to X, but this server is Y" | Record has the wrong IP, or the old value is still cached | Correct the value. If you just changed it, wait up to the record's old TTL. |
| "X is a Cloudflare proxy address" | Cloudflare orange cloud is on | Cloudflare > DNS > edit the record > **DNS only** (grey cloud). |
| "has several A records" | An extra A record (e.g. an old server) | Delete every A record for the name except this server's. |
| "also has an IPv6 (AAAA) record" | An AAAA record points elsewhere | Delete the AAAA record (Let's Encrypt prefers IPv6). |
| `stream.example.com.example.com` | Full name typed where only the host was expected | Rename the record to just `stream`. |
| Server IP changed after a restart (AWS) | No Elastic IP | Attach an Elastic IP, update the A record. |

### Ports

| What you see | Fix |
|---|---|
| "Firewall (ufw) blocks port 80/443" | `sudo ufw allow 80/tcp && sudo ufw allow 443/tcp` |
| "Port(s) 80 could not be reached on ..." | Open TCP 80 and 443 to `0.0.0.0/0` in the provider firewall / security group (section 6). Some providers block this self-test even when the port is open; test from your own computer: `curl -I http://stream.example.com`. |
| "Nothing is listening on port 443" | `sudo systemctl restart caddy`, then `sudo journalctl -u caddy -n 50 --no-pager` |
| "Port 80/443 is already in use by nginx/apache2" (installer) | Another web server is installed: `sudo apt purge nginx apache2`, or use a fresh server. |

### Certificates

| What you see | Cause | Fix |
|---|---|---|
| "RATE LIMIT" | Too many certificates or failed checks for this name recently | Wait until the time shown (often up to a week for 5 duplicate certificates). Use `--staging` for tests. Using a different subdomain also works. |
| "CAA" | A CAA DNS record does not allow Let's Encrypt | Add a CAA record `0 issue "letsencrypt.org"` or delete the CAA records. |
| "PORT: ... could not connect" | Port 80/443 closed at the provider | See *Ports* above. |
| "WRONG SERVER" | The name reaches another server or proxy | Fix DNS so it points only at this server; turn off Cloudflare proxy. |
| Browser warns although a domain is set | Staging certificate, `self-signed` chosen, or the certificate is not issued yet | `sudo bash /opt/zoom-stream/set-domain.sh --check`; for a real certificate: `sudo bash /opt/zoom-stream/set-domain.sh stream.example.com` |
| "Certificate expires in N days" in `doctor.sh` | Renewal is failing (usually DNS or port 80 changed) | Fix DNS/ports, then `sudo systemctl reload caddy`. |

After fixing anything, Caddy retries by itself within a few minutes; `set-domain.sh --check` confirms it.
