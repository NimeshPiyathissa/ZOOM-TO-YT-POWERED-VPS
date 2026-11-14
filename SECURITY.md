# Security policy

## Supported versions

| Version | Supported |
|---|---|
| 1.0.x | Yes |

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Report privately through GitHub: open the repository's **Security** tab and choose **Report a vulnerability**
(GitHub private vulnerability reporting). Include:

- what is affected (file, endpoint or script) and the version (`git describe --tags` in `/opt/zoom-stream`);
- steps to reproduce, and the impact you expect;
- any logs - with stream keys, passwords, meeting links, domains and IP addresses removed.

You should get a first answer within 7 days. Fixes are released as a new version with a note in the changelog;
reporters are credited unless they prefer not to be.

## Scope

In scope: the dashboard, the installer and the scripts in this repository. Out of scope: vulnerabilities in Zoom,
Google Chrome, FFmpeg, Caddy, Ubuntu or other third-party software (report those to their vendors), and attacks that
require root on the server.

See [docs/security.md](docs/security.md) for the security design.
