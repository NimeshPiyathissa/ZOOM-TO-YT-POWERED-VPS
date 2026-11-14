# Testing

## Automated tests

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r dashboard/requirements.txt pytest
python -m pytest dashboard/tests -q
shellcheck install.sh doctor.sh set-domain.sh backup.sh restore.sh update.sh uninstall.sh scripts/*.sh installer/lib/*.sh
```

The installer tests (`test_install_wizard.py`, `test_domain_https.py`) run the real bash functions with fake
`dig`, `curl`, `ufw`, `ss`, `openssl` and `journalctl` commands, so DNS, port and certificate situations are covered
without a network or root.

## Release test on a fresh server

Every release is installed on a **fresh Ubuntu 24.04 server** in both modes. Use `--staging` for every run except the
last subdomain run, so repeated installs do not hit Let's Encrypt's rate limits.

### Preparation

- A fresh Ubuntu 24.04 x86_64 VPS (4 vCPU / 8 GB recommended), provider firewall: TCP 22, 80, 443 inbound.
- A test subdomain (e.g. `test-stream.example.com`) with an A record pointing at the server (DNS only, no proxy).
- The repository as a git clone or a git bundle (`git bundle create zs.bundle --all`, then
  `sudo bash install.sh --repo /path/to/zs.bundle --ref main`).

### Run A - subdomain mode

| # | Check | Expected |
|---|---|---|
| A1 | Wizard domain question | Shows the server's public IP and the A record (`Name` = subdomain label, `Value` = IP) |
| A2 | DNS not yet correct (optional: use a name without a record first) | Explains the problem; `w` waits and re-checks; `e` lets you type another name |
| A3 | DNS correct | "points to this server" |
| A4 | Install with `--staging` | "Checking HTTPS": ports 80/443 OK, "HTTPS works with a Let's Encrypt STAGING certificate" |
| A5 | `doctor.sh` | Staging warning, DNS OK, firewall 80/443 OK |
| A6 | `set-domain.sh <subdomain>` (no `--staging`) | Real certificate, "HTTPS works with a trusted certificate", padlock in a browser |
| A7 | `curl -I http://<subdomain>` | Redirect to `https://` |
| A8 | `set-domain.sh --check` | All OK, exit code 0 |
| A9 | Close port 80 at the provider, `set-domain.sh --check` | Port warning naming the provider firewall; reopen afterwards |
| A10 | Reboot, `doctor.sh` | Everything comes back, certificate still valid |

### Run B - IP-only mode

| # | Check | Expected |
|---|---|---|
| B1 | Wizard domain question: `none` | Clear warning: self-signed, not browser-trusted, how to add a domain later |
| B2 | Install finishes | "HTTPS answers with the self-signed certificate (https://IP)" |
| B3 | Browser `https://<ip>` | Certificate warning once, then the sign-in page |
| B4 | `set-domain.sh <subdomain> --staging` | Switches to the domain without reinstalling; dashboard keeps running |
| B5 | `set-domain.sh --none` | Back to IP-only |

### Record the result

Fill in the "Tested environment" section of the release notes: provider, instance size, Ubuntu image, date, which
checks passed and anything that did not.
