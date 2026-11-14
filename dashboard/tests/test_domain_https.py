"""Subdomain / DNS / ports / HTTPS-certificate handling: installer/lib/domain.sh, the domain question of the wizard,
set-domain.sh and setup-https.sh --staging.

Network tools (dig, getent, curl, openssl, journalctl, ufw, ss, ip) are replaced by fakes on PATH, so these tests
never touch the network. The real end-to-end path (DNS -> Caddy -> Let's Encrypt -> HTTPS) is verified on a fresh
Ubuntu 24.04 server with a real subdomain (docs/testing.md)."""
from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
LIB = REPO / "installer" / "lib"
BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="bash not available - installer scripts NOT executed")

SERVER_IP = "203.0.113.7"


def _posix(p):
    p = pathlib.Path(p).resolve().as_posix()
    return "/" + p[0].lower() + p[2:] if len(p) > 1 and p[1] == ":" else p


# Fake tools. FAKE_* environment variables drive what they answer.
FAKES = {
    # dig +short TYPE NAME @resolver. FAKE_A_SEQ="ip1|ip2 ip3|..." answers successive A queries (last one repeats).
    "dig": r'''#!/usr/bin/env bash
type="$4"
[[ -n "${FAKE_DIG_FAIL:-}" ]] && exit 9
if [[ "$type" == A ]]; then
  if [[ -n "${FAKE_A_SEQ:-}" ]]; then
    n=$(cat "$FAKE_COUNTER" 2>/dev/null || echo 0); echo $((n + 1)) > "$FAKE_COUNTER"
    IFS='|' read -r -a seq <<<"$FAKE_A_SEQ"
    (( n >= ${#seq[@]} )) && n=$(( ${#seq[@]} - 1 ))
    vals="${seq[n]}"
  else
    vals="${FAKE_A:-}"
  fi
  [[ -n "${FAKE_CNAME:-}" ]] && echo "$FAKE_CNAME"
  for v in $vals; do echo "$v"; done
else
  for v in ${FAKE_AAAA:-}; do echo "$v"; done
fi
exit 0
''',
    "getent": r'''#!/usr/bin/env bash
[[ "$1" == ahostsv4 ]] && for v in ${FAKE_GETENT_A:-}; do echo "$v STREAM $2"; done
exit 0
''',
    "ip": r'''#!/usr/bin/env bash
for v in ${FAKE_LOCAL_V6:-}; do echo "2: eth0    inet6 $v/64 scope global"; done
''',
    "curl": r'''#!/usr/bin/env bash
echo "curl $*" >> "${FAKE_CALLS:-/dev/null}"
printf '%s' "${FAKE_HTTP_CODE:-000}"
''',
    "openssl": r'''#!/usr/bin/env bash
[[ "$1" == x509 ]] && echo "issuer=${FAKE_ISSUER:-}"
exit 0
''',
    "journalctl": r'''#!/usr/bin/env bash
[[ -n "${FAKE_CADDY_LOG:-}" ]] && cat "$FAKE_CADDY_LOG"
exit 0
''',
    "ufw": r'''#!/usr/bin/env bash
if [[ "$1" == status ]]; then
  [[ -n "${FAKE_UFW:-}" ]] || { echo "Status: inactive"; exit 0; }
  echo "Status: active"; echo
  for p in $FAKE_UFW; do printf '%s/tcp                    ALLOW       Anywhere\n' "$p"; done
fi
exit 0
''',
    "ss": r'''#!/usr/bin/env bash
for p in ${FAKE_LISTEN:-80 443}; do [[ "$*" == *":$p"* ]] && echo "LISTEN 0 4096 *:$p *:*"; done
exit 0
''',
}


@pytest.fixture
def sh(tmp_path):
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    for name, body in FAKES.items():
        (fakebin / name).write_text(body, encoding="utf-8", newline="\n")
        os.chmod(fakebin / name, 0o755)

    def run(body: str, libs=("common", "validate", "domain"), tty: list[str] | None = None, **env):
        script = tmp_path / "harness.sh"
        head = "set -uo pipefail\n"
        head += f'export ZS_ETC_DIR="{_posix(tmp_path / "etc")}" ZS_LOG_DIR="{_posix(tmp_path / "log")}" NO_COLOR=1\n'
        for lib in libs:
            head += f'source "{_posix(LIB / (lib + ".sh"))}"\n'
        # Port probes through the public IP: FAKE_REACH lists the ports that "answer".
        head += 'zs_tcp_reachable() { [[ " ${FAKE_REACH:-80 443} " == *" $2 "* ]]; }\n'
        script.write_text(head + body + "\n", encoding="utf-8", newline="\n")
        full = dict(os.environ, PATH=_posix(fakebin) + ":" + os.environ.get("PATH", ""),
                    FAKE_COUNTER=_posix(tmp_path / "counter"), FAKE_CALLS=_posix(tmp_path / "calls"),
                    ZS_SLEEP="true", ZS_DNS_POLL="1", ZS_HTTPS_WAIT="3")
        if tty is not None:
            answers = tmp_path / "tty.txt"
            answers.write_text("\n".join(tty) + "\n", encoding="utf-8", newline="\n")
            full.update(ZS_TTY=_posix(answers), ZS_TTY_OUT="/dev/null")
        full.update({k: str(v) for k, v in env.items()})
        return subprocess.run([BASH, _posix(script)], capture_output=True, text=True, timeout=60, env=full,
                              stdin=subprocess.DEVNULL)
    run.tmp = tmp_path
    return run


# ------------------------------------------------------------------ DNS checks

@needs_bash
@pytest.mark.parametrize("a, aaaa, local6, state", [
    (SERVER_IP, "", "", "ok"),
    ("", "", "", "missing"),
    ("198.51.100.9", "", "", "mismatch"),
    (f"{SERVER_IP} 198.51.100.9", "", "", "extra"),
    (SERVER_IP, "2001:db8::1", "", "aaaa"),                     # IPv6 record pointing somewhere else
    (SERVER_IP, "2001:db8::1", "2001:db8::1", "ok"),            # IPv6 record pointing at this server
])
def test_dns_check_classifies_every_situation(sh, a, aaaa, local6, state):
    r = sh(f'zs_dns_check stream.example.com {SERVER_IP} && echo PASS; echo "STATE=$ZS_DNS_STATE A=$ZS_DNS_A"',
           FAKE_A=a, FAKE_AAAA=aaaa, FAKE_LOCAL_V6=local6)
    assert f"STATE={state} " in r.stdout, r.stdout + r.stderr
    assert ("PASS" in r.stdout) == (state == "ok")


@needs_bash
def test_dns_lookup_ignores_cname_lines_and_unknown_server_ip(sh):
    r = sh('zs_dns_check stream.example.com "" ; echo "STATE=$ZS_DNS_STATE A=[$ZS_DNS_A]"',
           FAKE_A=SERVER_IP, FAKE_CNAME="lb.provider.example.net.")
    assert "STATE=noip A=[203.0.113.7]" in r.stdout


@needs_bash
def test_dns_lookup_falls_back_to_the_system_resolver(sh):
    r = sh(f'zs_dns_check stream.example.com {SERVER_IP} && echo PASS', FAKE_DIG_FAIL="1", FAKE_GETENT_A=SERVER_IP)
    assert "PASS" in r.stdout


@needs_bash
@pytest.mark.parametrize("ip, cf", [("104.21.3.4", True), ("172.67.10.1", True), ("188.114.97.3", True),
                                    ("162.159.1.1", True), (SERVER_IP, False), ("8.8.8.8", False),
                                    ("104.32.0.1", False), ("not-an-ip", False)])
def test_cloudflare_proxy_addresses_are_recognised(sh, ip, cf):
    r = sh(f'zs_is_cloudflare_ip "{ip}" && echo YES || echo NO')
    assert r.stdout.strip() == ("YES" if cf else "NO")


@needs_bash
@pytest.mark.parametrize("state_a, phrase", [
    ("", "has no A record yet"),
    ("198.51.100.9", "Edit the A record so its value is 203.0.113.7"),
    ("104.21.3.4", "DNS only"),                                   # Cloudflare orange cloud
    (f"{SERVER_IP} 198.51.100.9", "Delete the other A records"),
])
def test_dns_problems_are_explained_with_the_fix(sh, state_a, phrase):
    r = sh(f'zs_dns_check stream.example.com {SERVER_IP}; zs_dns_explain stream.example.com {SERVER_IP}',
           FAKE_A=state_a)
    assert phrase in r.stdout, r.stdout


@needs_bash
def test_ipv6_record_elsewhere_is_explained(sh):
    r = sh(f'zs_dns_check s.example.com {SERVER_IP}; zs_dns_explain s.example.com {SERVER_IP}',
           FAKE_A=SERVER_IP, FAKE_AAAA="2001:db8::5")
    assert "AAAA" in r.stdout and "Delete the AAAA record" in r.stdout


@needs_bash
@pytest.mark.parametrize("domain, label, zone", [
    ("stream.example.com", "stream", "example.com"),
    ("example.com", "@", "example.com"),
    ("live.class.example.org", "live.class", "example.org"),
    ("stream.example.co.uk", "stream", "example.co.uk"),
    ("live.school.com.lk", "live", "school.com.lk"),
    ("school.com.lk", "@", "school.com.lk"),
])
def test_dns_record_name_for_the_provider_form(sh, domain, label, zone):
    r = sh(f'echo "$(zs_dns_host_label {domain})|$(zs_dns_zone {domain})"')
    assert r.stdout.strip() == f"{label}|{zone}"


@needs_bash
def test_dns_instructions_show_the_exact_a_record(sh):
    r = sh(f'zs_dns_instructions stream.example.com {SERVER_IP}')
    row = [line for line in r.stdout.splitlines() if line.strip().startswith("A ")]
    assert row and row[0].split()[:3] == ["A", "stream", SERVER_IP], r.stdout
    assert "where you manage example.com" in r.stdout and "DNS only" in r.stdout


@needs_bash
def test_dns_wait_retries_until_propagated(sh):
    r = sh(f'zs_dns_wait stream.example.com {SERVER_IP} 60 && echo WAITED-OK',
           FAKE_A_SEQ=f"|198.51.100.9|{SERVER_IP}")
    assert "WAITED-OK" in r.stdout and "now points to this server" in r.stdout
    assert "no A record" in r.stdout and "198.51.100.9" in r.stdout        # progress lines while waiting


@needs_bash
def test_dns_wait_gives_up_with_an_explanation(sh):
    r = sh(f'zs_dns_wait stream.example.com {SERVER_IP} 3 || echo GAVE-UP', FAKE_A="198.51.100.9")
    assert "GAVE-UP" in r.stdout and "Still not pointing here" in r.stdout and "Edit the A record" in r.stdout


@needs_bash
def test_dns_gate_wait_timeout_explains_once(sh):
    # Found on the v1.0.0 test server: after [w] timed out the explanation was printed twice.
    r = sh(f'zs_dns_gate stream.example.com {SERVER_IP}; echo "RC=$?"', libs=("common", "validate", "domain"),
           tty=["w", "n"], FAKE_A="", ZS_DNS_WAIT_MAX="2")
    assert "RC=3" in r.stdout, r.stdout + r.stderr
    assert "Still not pointing here" in r.stdout
    after = r.stdout.split("Still not pointing here", 1)[1]
    assert after.count("has no A record yet") == 1, after


@needs_bash
@pytest.mark.parametrize("answers, seq, rc", [
    (["r"], f"198.51.100.9|{SERVER_IP}", 0),        # re-check after fixing the record
    (["w"], f"|||{SERVER_IP}", 0),                  # wait for propagation
    (["c"], "198.51.100.9", 0),                     # continue anyway
    (["e"], "198.51.100.9", 2),                     # enter another name
    (["n"], "198.51.100.9", 3),                     # no domain
    (["x", "n"], "198.51.100.9", 3),                # bad choice is re-asked
])
def test_dns_gate_menu(sh, answers, seq, rc):
    r = sh(f'zs_dns_gate stream.example.com {SERVER_IP}; echo "RC=$?"', libs=("common", "validate", "domain"),
           tty=answers, FAKE_A_SEQ=seq)
    assert f"RC={rc}" in r.stdout, r.stdout + r.stderr
    assert "Create this DNS record" in r.stdout


# ------------------------------------------------------------------ ports

@needs_bash
def test_ports_check_flags_a_ufw_rule_that_blocks_port_80(sh):
    r = sh(f'zs_ports_check {SERVER_IP} letsencrypt; echo "RC=$?"', FAKE_UFW="22 443")
    assert "RC=1" in r.stdout and "blocks port 80" in r.stdout and "sudo ufw allow 80/tcp" in r.stdout


@needs_bash
def test_ports_check_passes_when_open_and_reachable(sh):
    r = sh(f'zs_ports_check {SERVER_IP} letsencrypt; echo "RC=$?"', FAKE_UFW="22 80 443")
    assert "RC=0" in r.stdout and "reachable on 203.0.113.7" in r.stdout


@needs_bash
def test_ports_check_names_the_provider_firewall_when_unreachable(sh):
    r = sh(f'zs_ports_check {SERVER_IP} letsencrypt; echo "RC=$?"', FAKE_REACH="443")
    assert "RC=0" in r.stdout                                    # a warning: the self-test is not proof
    assert "could not be reached" in r.stdout and "Security group" in r.stdout


@needs_bash
def test_ports_check_ip_only_mode_needs_only_443(sh):
    r = sh(f'zs_ports_check {SERVER_IP} self-signed; echo "RC=$?"', FAKE_UFW="22 443", FAKE_LISTEN="443")
    assert "RC=0" in r.stdout and "port 80" not in r.stdout


@needs_bash
def test_ports_check_notices_nothing_listening(sh):
    r = sh(f'zs_ports_check {SERVER_IP} letsencrypt; echo "RC=$?"', FAKE_LISTEN="443")
    assert "RC=1" in r.stdout and "Nothing is listening on port 80" in r.stdout


# ------------------------------------------------------------------ certificate errors

CADDY_LOGS = {
    "rate": '{"level":"error","logger":"tls.obtain","msg":"could not get certificate from issuer","error":"HTTP 429 '
            'urn:ietf:params:acme:error:rateLimited - too many certificates (5) already issued for this exact set of '
            'domains in the last 168h0m0s, retry after 2026-11-15 10:00:00 UTC"}',
    "port": '{"level":"error","msg":"challenge failed","problem":{"type":"urn:ietf:params:acme:error:connection",'
            '"detail":"203.0.113.7: Timeout during connect (likely firewall problem)"}}',
    "dns": '{"level":"error","msg":"challenge failed","problem":{"type":"urn:ietf:params:acme:error:dns",'
           '"detail":"DNS problem: NXDOMAIN looking up A for stream.example.com"}}',
    "wrong": '{"level":"error","msg":"challenge failed","problem":{"type":"urn:ietf:params:acme:error:unauthorized",'
             '"detail":"198.51.100.9: Invalid response from http://stream.example.com/.well-known/acme-challenge/x: 404"}}',
    "caa": '{"level":"error","msg":"could not get certificate from issuer","error":"HTTP 403 '
           'urn:ietf:params:acme:error:caa - CAA record for example.com prevents issuance"}',
    "other": '{"level":"error","msg":"could not get certificate from issuer","error":"something unexpected"}',
}


@needs_bash
@pytest.mark.parametrize("kind, phrase", [("rate", "RATE LIMIT"), ("port", "PORT:"), ("dns", "DNS:"),
                                          ("wrong", "WRONG SERVER"), ("caa", "CAA:"), ("other", "request failed")])
def test_certificate_errors_are_explained(sh, kind, phrase):
    log = sh.tmp / "caddy.log"
    log.write_text(CADDY_LOGS[kind], encoding="utf-8")
    r = sh(f'zs_explain_cert_error "$(cat "{_posix(log)}")"')
    assert r.stdout.startswith(phrase) or phrase in r.stdout, r.stdout
    if kind == "rate":
        assert "retry after 2026-11-15" in r.stdout and "--staging" in r.stdout


@needs_bash
def test_unrelated_log_text_gives_no_explanation(sh):
    r = sh('zs_explain_cert_error "certificate obtained successfully" || echo NONE')
    assert r.stdout.strip() == "NONE"


# ------------------------------------------------------------------ HTTPS verification

@needs_bash
def test_https_verify_succeeds_with_a_trusted_certificate(sh):
    r = sh('zs_https_verify stream.example.com no; echo "RC=$?"', FAKE_HTTP_CODE="302")
    assert "RC=0" in r.stdout and "trusted certificate: https://stream.example.com" in r.stdout
    calls = (sh.tmp / "calls").read_text()
    assert "--resolve stream.example.com:443:127.0.0.1" in calls and " -k" not in calls.split("\n")[0]


@needs_bash
def test_https_verify_stops_early_on_a_rate_limit_and_explains_it(sh):
    log = sh.tmp / "caddy.log"
    log.write_text(CADDY_LOGS["rate"], encoding="utf-8")
    r = sh('zs_https_verify stream.example.com no; echo "RC=$?"', FAKE_HTTP_CODE="000", FAKE_CADDY_LOG=_posix(log),
           ZS_HTTPS_WAIT="600")
    assert "RC=1" in r.stdout and "RATE LIMIT" in r.stdout and "set-domain.sh --check" in r.stdout


@needs_bash
def test_https_verify_times_out_with_the_port_explanation(sh):
    log = sh.tmp / "caddy.log"
    log.write_text(CADDY_LOGS["port"], encoding="utf-8")
    r = sh('zs_https_verify stream.example.com no; echo "RC=$?"', FAKE_HTTP_CODE="000", FAKE_CADDY_LOG=_posix(log),
           ZS_HTTPS_WAIT="0")
    assert "RC=1" in r.stdout and "PORT:" in r.stdout


@needs_bash
def test_https_verify_accepts_a_staging_certificate_only_in_staging_mode(sh):
    r = sh('zs_https_verify stream.example.com yes; echo "RC=$?"', FAKE_HTTP_CODE="200",
           FAKE_ISSUER="C = US, O = (STAGING) Let's Encrypt, CN = (STAGING) Ersatz Edamame E1")
    assert "RC=0" in r.stdout and "STAGING certificate" in r.stdout and "NOT trusted" in r.stdout
    r = sh('zs_https_verify stream.example.com yes; echo "RC=$?"', FAKE_HTTP_CODE="200",
           FAKE_ISSUER="C = US, O = Some Other CA", ZS_HTTPS_WAIT="0")
    assert "RC=1" in r.stdout


@needs_bash
def test_https_verify_ip_only(sh):
    r = sh(f'zs_https_verify_ip {SERVER_IP}; echo "RC=$?"', FAKE_HTTP_CODE="200")
    assert "RC=0" in r.stdout and "self-signed" in r.stdout
    r = sh(f'zs_https_verify_ip {SERVER_IP}; echo "RC=$?"', FAKE_HTTP_CODE="000")
    assert "RC=1" in r.stdout and "not answering" in r.stdout


# ------------------------------------------------------------------ the wizard's domain question

@needs_bash
def test_wizard_shows_the_ip_and_record_then_accepts_a_domain_once_dns_points_here(sh):
    body = ('q_domain; q_ssl\n'
            'echo "DOMAIN=$(zs_get DOMAIN) SSL=$(zs_get SSL_MODE) EMAIL=$(zs_get LE_EMAIL)"\n')
    r = sh(body, libs=("common", "validate", "domain", "questions"),
           tty=["Stream.Example.com", "r", "", "ops@example.com"],
           ZS_PUBLIC_IP=SERVER_IP, FAKE_A_SEQ=f"|{SERVER_IP}")
    out = r.stdout
    assert "This server's public IP address: 203.0.113.7" in out
    assert re.search(r"A\s+stream\s+203\.0\.113\.7", out)
    assert "has no A record yet" in out and "points to this server" in out
    assert "DOMAIN=stream.example.com SSL=letsencrypt EMAIL=ops@example.com" in out


@needs_bash
def test_wizard_ip_only_mode_warns_about_no_trusted_https(sh):
    r = sh('q_domain; q_ssl; echo "DOMAIN=$(zs_get DOMAIN) SSL=$(zs_get SSL_MODE)"',
           libs=("common", "validate", "domain", "questions"), tty=["none"], ZS_PUBLIC_IP=SERVER_IP)
    assert "DOMAIN=none SSL=self-signed" in r.stdout
    assert "NOT browser-trusted" in r.stdout and "set-domain.sh stream.example.com" in r.stdout
    assert "https://203.0.113.7" in r.stdout


@needs_bash
def test_wizard_can_fall_back_to_ip_only_from_the_dns_menu(sh):
    r = sh('q_domain; echo "DOMAIN=$(zs_get DOMAIN) SSL=$(zs_get SSL_MODE)"',
           libs=("common", "validate", "domain", "questions"), tty=["stream.example.com", "n"],
           ZS_PUBLIC_IP=SERVER_IP, FAKE_A="198.51.100.9")
    assert "DOMAIN=none SSL=self-signed" in r.stdout and "points to 198.51.100.9" in r.stdout


@needs_bash
def test_wizard_asks_for_the_ip_when_it_cannot_be_detected(sh):
    body = 'zs_public_ip() { return 1; }\nq_domain; echo "IP=$ZS_PUBLIC_IP DOMAIN=$(zs_get DOMAIN)"\n'
    r = sh(body, libs=("common", "validate", "domain", "questions"), tty=["nope", SERVER_IP, "none"])
    assert "not an IPv4 address" in r.stdout and f"IP={SERVER_IP} DOMAIN=none" in r.stdout


@needs_bash
def test_summary_marks_staging_certificates(sh):
    body = ('zs_set DOMAIN stream.example.com; zs_set SSL_MODE letsencrypt; zs_set LE_EMAIL a@example.com\n'
            'zs_set LE_STAGING yes; zs_print_summary\n')
    r = sh(body, libs=("common", "validate", "domain", "questions"))
    assert "STAGING" in r.stdout and "NOT browser-trusted" in r.stdout


@needs_bash
@pytest.mark.parametrize("value, ok", [("", True), ("yes", True), ("NO", True), ("maybe", False)])
def test_config_le_staging_is_validated(sh, value, ok):
    body = (f'zs_set ADMIN_PASSWORD Correct-Horse-9x; zs_set LE_STAGING "{value}"; zs_set TIMEZONE UTC\n'
            'ZS_ZONEINFO_DIR=/nonexistent; zs_valid_timezone() { return 0; }\n'
            'if zs_validate_config_answers; then echo "VALID $(zs_get LE_STAGING)"; else echo INVALID; fi\n')
    r = sh(body, libs=("common", "validate", "domain", "questions"))
    if ok:
        assert "VALID " in r.stdout and ("VALID no" in r.stdout or "VALID yes" in r.stdout), r.stdout
    else:
        assert "LE_STAGING" in r.stdout and "INVALID" in r.stdout


# ------------------------------------------------------------------ setup-https.sh --staging, set-domain.sh, wiring

@needs_bash
def test_setup_https_staging_uses_the_lets_encrypt_staging_ca():
    script = _posix(REPO / "scripts" / "setup-https.sh")
    r = subprocess.run([BASH, script, "--render-only", "--domain", "s.example.com", "--staging"],
                       capture_output=True, text=True, timeout=30)
    assert "acme_ca https://acme-staging-v02.api.letsencrypt.org/directory" in r.stdout
    r = subprocess.run([BASH, script, "--render-only", "--domain", "s.example.com"], capture_output=True, text=True,
                       timeout=30)
    assert "acme_ca" not in r.stdout and "staging" not in r.stdout


@needs_bash
def test_set_domain_help_and_argument_checks_work_without_root():
    script = _posix(REPO / "set-domain.sh")
    r = subprocess.run([BASH, script, "--help"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and "set-domain.sh stream.example.com" in r.stdout and "--staging" in r.stdout
    r = subprocess.run([BASH, script, "--bogus"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 1 and "Unknown option" in r.stderr
    r = subprocess.run([BASH, script, "http://bad/"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 1 and "not a valid domain" in r.stderr
    r = subprocess.run([BASH, script, "a.example.com", "--email", "nope"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 1 and "e-mail" in r.stderr


def test_installer_wiring():
    install = (REPO / "install.sh").read_text(encoding="utf-8")
    steps = (LIB / "steps.sh").read_text(encoding="utf-8")
    assert 'source "$ZS_SRC/installer/lib/domain.sh"' in install
    assert "--staging) STAGING=1" in install and "zs_https_verify " in install and "zs_ports_check " in install
    assert "LE_STAGING=$(zs_get LE_STAGING)" in install                 # install.conf for set-domain/doctor
    assert "args+=(--staging)" in steps
    doctor = (REPO / "doctor.sh").read_text(encoding="utf-8")
    assert "zs_dns_check" in doctor and "zs_explain_cert_error" in doctor
    # the old line-continuation bug swallowed the 'blocks HTTPS' branch
    assert '"Firewall allows HTTPS (443)" \\' not in doctor


def test_set_domain_only_reloads_caddy_and_never_touches_streaming_units():
    text = (REPO / "set-domain.sh").read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    for unit in ("ffmpeg-stream", "dashboard.service", "zs_user_systemctl", "systemctl restart"):
        assert unit not in code


def test_domain_guide_exists_and_is_linked_from_the_entry_points():
    guide = REPO / "docs" / "domain-and-https.md"
    text = guide.read_text(encoding="utf-8")
    for must in ("A record", "DNS only", "--staging", "set-domain.sh --check", "--none", "RATE LIMIT", "CAA",
                 "Elastic IP", "80", "443"):
        assert must in text, must
    for doc in ("README.md", "docs/installation.md", "docs/troubleshooting.md", "docs/release-notes/v1.0.0.md"):
        assert "domain-and-https.md" in (REPO / doc).read_text(encoding="utf-8"), doc
    # Every command the guide tells users to run exists with that option.
    set_domain = (REPO / "set-domain.sh").read_text(encoding="utf-8")
    for opt in ("--none)", "--check)", "--staging)", "--email)"):
        assert opt in set_domain, opt
    assert "--staging) STAGING=1" in (REPO / "install.sh").read_text(encoding="utf-8")


def test_domain_guide_internal_anchors_resolve():
    def anchors(path):
        out = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^#+\s+(.*)", line)
            if m:
                out.add(re.sub(r"[^\w\- ]", "", m.group(1).strip().lower()).replace(" ", "-"))
        return out
    docs = REPO / "docs"
    guide_anchors = anchors(docs / "domain-and-https.md")
    for md in [REPO / "README.md", *docs.glob("*.md"), *docs.glob("release-notes/*.md")]:
        for frag in re.findall(r"domain-and-https\.md#([\w\-]+)", md.read_text(encoding="utf-8")):
            assert frag in guide_anchors, f"{md.name}: #{frag}"
        if md.name == "domain-and-https.md":
            for frag in re.findall(r"\]\(#([\w\-]+)\)", md.read_text(encoding="utf-8")):
                assert frag in guide_anchors, frag


@needs_bash
def test_doctor_accepts_no_color_like_the_other_tools():
    # Found on the v1.0.0 test server: `doctor.sh --no-color` was rejected as an unknown option.
    r = subprocess.run([BASH, _posix(REPO / "doctor.sh"), "--no-color", "--help"], capture_output=True, text=True,
                       timeout=30, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "--no-color" in r.stdout and "Unknown option" not in r.stdout + r.stderr
    r = subprocess.run([BASH, _posix(REPO / "doctor.sh"), "--bogus"], capture_output=True, text=True,
                       timeout=30, stdin=subprocess.DEVNULL)
    assert r.returncode != 0 and "Unknown option: --bogus" in r.stdout + r.stderr
