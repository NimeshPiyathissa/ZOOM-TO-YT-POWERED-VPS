"""The guided installer (install.sh + installer/lib/*.sh) and its companions (update/backup/restore/uninstall/doctor).

The library functions are executed for real in bash against a sandbox; install.sh as a whole needs root, apt and
systemd, so here it is only checked statically and for its argument/root guards. The full install is verified on a
real Ubuntu 24.04 server (docs/testing.md)."""
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
INSTALLER_FILES = [REPO / "install.sh", REPO / "update.sh", REPO / "backup.sh", REPO / "restore.sh",
                   REPO / "uninstall.sh", REPO / "doctor.sh", *sorted(LIB.glob("*.sh"))]


def _posix(p):
    p = pathlib.Path(p).resolve().as_posix()
    return "/" + p[0].lower() + p[2:] if len(p) > 1 and p[1] == ":" else p


def _code(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] if not line.lstrip().startswith("#!") else "" for line in text.splitlines())


def _bash(tmp_path, body: str, env: dict | None = None, libs=("common", "validate")):
    """Run `body` in bash after sourcing the given installer libraries, with state dirs inside tmp_path."""
    script = tmp_path / "harness.sh"
    head = "set -uo pipefail\n"
    head += f'export ZS_ETC_DIR="{_posix(tmp_path / "etc")}" ZS_LOG_DIR="{_posix(tmp_path / "log")}" NO_COLOR=1\n'
    for lib in libs:
        head += f'source "{_posix(LIB / (lib + ".sh"))}"\n'
    script.write_text(head + body + "\n", encoding="utf-8", newline="\n")
    full_env = dict(os.environ, **(env or {}))
    return subprocess.run([BASH, _posix(script)], capture_output=True, text=True, timeout=60, env=full_env,
                          stdin=subprocess.DEVNULL)


# ------------------------------------------------------------------ validators

VALID = {
    "zs_valid_domain": ["none", "NONE", "stream.example.com", "a.b.example.co.uk", "x1.example.org"],
    "zs_valid_email": ["you@example.com", "first.last+tag@sub.example.org"],
    "zs_valid_username": ["admin", "Ops.Team_1", "a", "me@site"],
    "zs_valid_password": ["Correct-Horse-9", "a long passphrase of words", "Tr0ub4dor&3xyz"],
    "zs_valid_rtmp_url": ["rtmps://a.rtmps.youtube.com:443/live2", "rtmp://a.rtmp.youtube.com/live2",
                          "rtmp://b.rtmp.youtube.com/live2/", "rtmps://ingest.example.com/app/live"],
    "zs_valid_stream_key": ["", "abcd-efgh-ijkl-mnop-qrst", "ABCD_1234_efgh"],
    "zs_valid_bot_name": ["Stream Bot", "Class 12 (Physics)", "Bot-1"],
    "zs_valid_preset": ["720p", "1080p"],
    "zs_valid_yes_no": ["yes", "no", "YES"],
    "zs_valid_ssl_mode": ["letsencrypt", "self-signed"],
    "zs_valid_optional_password": ["", "auto", "Correct-Horse-9"],
}
INVALID = {
    "zs_valid_domain": ["", "http://example.com", "example", "exa mple.com", "example.com/path", "-bad.example.com",
                        "x.com;rm -rf /", "a..example.com"],
    "zs_valid_email": ["", "you", "you@", "@example.com", "you@example", "a b@example.com"],
    "zs_valid_username": ["", "-admin", "has space", "x" * 65, "admin;", "ad'min"],
    "zs_valid_password": ["short1!", "lowercaseonly", "password12345!", "Admin-admin-12", "x\ny" * 4,
                          "qwertyQWERTY12"],
    "zs_valid_rtmp_url": ["", "http://a.rtmp.youtube.com/live2", "rtmp://", "rtmp://host/live2?key=1",
                          "rtmp://host/$(id)", "rtmp://host/a b", "file:///etc/passwd"],
    "zs_valid_stream_key": ["short", "has space here", "key;rm", "a" * 129],
    "zs_valid_bot_name": ["", "x" * 65, 'say "hi"', "it's", "a$b", "back`tick", "a\\b"],
    "zs_valid_preset": ["", "4k", "720", "1080p60"],
    "zs_valid_yes_no": ["", "y", "maybe"],
    "zs_valid_ssl_mode": ["", "lets-encrypt", "none"],
    "zs_valid_optional_password": ["Zq7%k"],
}


@needs_bash
@pytest.mark.parametrize("fn", sorted(VALID))
def test_validators_accept_good_values(tmp_path, fn):
    env = {f"V{i}": v for i, v in enumerate(VALID[fn])}
    body = "".join(f'{fn} "$V{i}" >/dev/null || echo "REJECTED {i}"\n' for i in range(len(VALID[fn])))
    r = _bash(tmp_path, body, env)
    assert r.returncode == 0 and "REJECTED" not in r.stdout, (fn, r.stdout, r.stderr)


@needs_bash
@pytest.mark.parametrize("fn", sorted(INVALID))
def test_validators_reject_bad_values_with_a_reason_and_never_echo_them(tmp_path, fn):
    env = {f"V{i}": v for i, v in enumerate(INVALID[fn])}
    body = "".join(f'if out="$({fn} "$V{i}")"; then echo "ACCEPTED {i}"; else printf "MSG {i} %s\\n" "$out"; fi\n'
                   for i in range(len(INVALID[fn])))
    r = _bash(tmp_path, body, env)
    assert "ACCEPTED" not in r.stdout, (fn, r.stdout)
    msgs = [line for line in r.stdout.splitlines() if line.startswith("MSG")]
    assert len(msgs) == len(INVALID[fn]) and all(len(m.split(" ", 2)[-1]) > 10 for m in msgs), r.stdout
    if "password" in fn:                                    # a rejected password is never repeated back
        for v in INVALID[fn]:
            if len(v) >= 4:
                assert v not in r.stdout


@needs_bash
def test_timezone_validation_uses_the_zoneinfo_database(tmp_path):
    zi = tmp_path / "zoneinfo"
    (zi / "Europe").mkdir(parents=True)
    (zi / "Europe" / "Berlin").write_text("x")
    (zi / "UTC").write_text("x")
    r = _bash(tmp_path, 'for t in UTC Europe/Berlin Mars/Base ../etc/passwd "Europe/Berlin;id" ""; do '
                        'zs_valid_timezone "$t" >/dev/null && echo "OK $t" || echo "NO $t"; done',
              {"ZS_ZONEINFO_DIR": _posix(zi)})
    assert r.stdout.split("\n")[:6] == ["OK UTC", "OK Europe/Berlin", "NO Mars/Base", "NO ../etc/passwd",
                                         "NO Europe/Berlin;id", "NO "]


@needs_bash
@pytest.mark.parametrize("cores, preset", [(1, "720p"), (4, "720p"), (7, "720p"), (8, "1080p"), (16, "1080p")])
def test_recommended_preset_follows_cpu_count(tmp_path, cores, preset):
    assert _bash(tmp_path, f"zs_recommended_preset {cores}").stdout.strip() == preset


# ------------------------------------------------------------------ --config parsing + validation

GOOD_CONFIG = """# comment
DOMAIN=Stream.Example.com
SSL_MODE=letsencrypt
LE_EMAIL=ops@example.com
ADMIN_USERNAME=operator
ADMIN_PASSWORD="Correct-Horse-Battery-9"
YT_STREAM_KEY=abcd-efgh-ijkl-mnop
BOT_NAME='Class Bot'
TIMEZONE=UTC
FIREWALL=YES
"""


def _config_run(tmp_path, text, extra=""):
    cfg = tmp_path / "zs.conf"
    cfg.write_text(text, encoding="utf-8", newline="\n")
    zi = tmp_path / "zi"
    zi.mkdir(exist_ok=True)
    (zi / "UTC").write_text("x")
    body = (f'ZS_CPU_CORES=4\nif ! zs_parse_kv_file "{_posix(cfg)}" >"$ZS_ETC_DIR.err"; then echo "PARSE-ERR"; cat "$ZS_ETC_DIR.err"; exit 3; fi\n'
            'if ! out="$(zs_validate_config_answers)"; then echo "INVALID"; echo "$out"; exit 4; fi\n'
            'zs_validate_config_answers >/dev/null\n'
            'for k in "${ZS_ANSWER_KEYS[@]}" ACCEPT_WARNINGS; do zs_is_secret_key "$k" && continue; echo "$k=$(zs_get "$k")"; done\n'
            + extra)
    return _bash(tmp_path, body, {"ZS_ZONEINFO_DIR": _posix(zi)}, libs=("common", "validate", "questions"))


@needs_bash
def test_config_file_is_parsed_validated_and_defaults_filled(tmp_path):
    r = _config_run(tmp_path, GOOD_CONFIG)
    assert r.returncode == 0, r.stdout + r.stderr
    got = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
    assert got["DOMAIN"] == "stream.example.com" and got["SSL_MODE"] == "letsencrypt"
    assert got["BOT_NAME"] == "Class Bot" and got["FIREWALL"] == "yes" and got["AUTO_UPDATES"] == "yes"
    assert got["OUTPUT_PRESET"] == "720p"                                 # 4 cores -> 720p
    assert got["YT_RTMP_URL"] == "rtmps://a.rtmps.youtube.com:443/live2"
    assert got["ACCEPT_WARNINGS"] == "no"
    assert "Correct-Horse" not in r.stdout and "abcd-efgh" not in r.stdout


@needs_bash
def test_config_secrets_default_to_auto_generation(tmp_path):
    r = _config_run(tmp_path, GOOD_CONFIG, extra='echo "VNC=$(zs_get VNC_PASSWORD) MASTER=$(zs_get MASTER_PASSWORD)"\n')
    assert "VNC=auto MASTER=auto" in r.stdout


@needs_bash
def test_config_without_domain_forces_self_signed_and_drops_email(tmp_path):
    r = _config_run(tmp_path, GOOD_CONFIG.replace("DOMAIN=Stream.Example.com", "DOMAIN=none"))
    assert r.returncode == 0 and "SSL_MODE=self-signed" in r.stdout and "LE_EMAIL=\n" in r.stdout + "\n"


@needs_bash
@pytest.mark.parametrize("text, expect", [
    (GOOD_CONFIG + "PASSWORD=x\n", "unknown setting 'PASSWORD'"),
    (GOOD_CONFIG + "this is not a setting\n", "expected KEY=value"),
    (GOOD_CONFIG.replace('ADMIN_PASSWORD="Correct-Horse-Battery-9"', "ADMIN_PASSWORD=short"), "ADMIN_PASSWORD"),
    (GOOD_CONFIG.replace('ADMIN_PASSWORD="Correct-Horse-Battery-9"\n', ""), "ADMIN_PASSWORD: required"),
    (GOOD_CONFIG.replace("LE_EMAIL=ops@example.com", "LE_EMAIL="), "LE_EMAIL"),
    (GOOD_CONFIG.replace("DOMAIN=Stream.Example.com", "DOMAIN=bad_domain"), "DOMAIN"),
    (GOOD_CONFIG + "OUTPUT_PRESET=4k\n", "OUTPUT_PRESET"),
    (GOOD_CONFIG + "YT_RTMP_URL=http://x/live2\n", "YT_RTMP_URL"),
    (GOOD_CONFIG.replace("TIMEZONE=UTC", "TIMEZONE=Mars/Base"), "TIMEZONE"),
    (GOOD_CONFIG + "FIREWALL=maybe\n", "FIREWALL"),
])
def test_bad_config_is_refused_with_every_problem_listed(tmp_path, text, expect):
    r = _config_run(tmp_path, text)
    assert r.returncode in (3, 4), r.stdout
    assert expect in r.stdout
    assert "Correct-Horse" not in r.stdout and "abcd-efgh" not in r.stdout


# ------------------------------------------------------------------ saved answers, progress, credentials

@needs_bash
def test_answers_are_saved_for_resume_and_secrets_scrubbed_after_success(tmp_path):
    body = ('zs_set DOMAIN none; zs_set ADMIN_USERNAME admin; zs_set ADMIN_PASSWORD "S3cret-Value-xyz"; '
            'zs_set YT_STREAM_KEY "abcd-efgh-ijkl"; zs_set BOT_NAME "Bot = One"\n'
            'zs_save_answers\n'
            'unset ANS_ADMIN_PASSWORD ANS_BOT_NAME\n'
            'zs_parse_kv_file "$ZS_ANSWERS_FILE" && echo "RESTORED $(zs_get ADMIN_PASSWORD) / $(zs_get BOT_NAME)"\n'
            'zs_scrub_saved_secrets\n'
            'echo "---"; cat "$ZS_ANSWERS_FILE"\n')
    r = _bash(tmp_path, body)
    before, after = r.stdout.split("---", 1)
    assert "RESTORED S3cret-Value-xyz / Bot = One" in before
    assert "S3cret-Value-xyz" not in after and "abcd-efgh-ijkl" not in after
    assert "ADMIN_USERNAME=admin" in after and "BOT_NAME=Bot = One" in after


@needs_bash
def test_step_progress_is_recorded(tmp_path):
    r = _bash(tmp_path, 'zs_step_done packages && echo yes1 || echo no1; zs_mark_done packages; '
                        'zs_step_done packages && echo yes2; zs_step_done chrome || echo no3')
    assert r.stdout.split() == ["no1", "yes2", "no3"]


@needs_bash
def test_secret_mask_never_shows_the_value(tmp_path):
    r = _bash(tmp_path, 'zs_mask "Hunter2-Hunter2"; echo; zs_mask auto; echo; zs_mask "" "set later"')
    assert r.stdout.splitlines() == ["******** (15 characters)", "auto-generate", "set later"]


@needs_bash
def test_generated_passwords_are_long_and_random(tmp_path):
    r = _bash(tmp_path, "zs_gen_password; echo; zs_gen_password")
    a, b = r.stdout.splitlines()
    assert len(a) == 24 and re.fullmatch(r"[A-Za-z0-9]{24}", a) and a != b


@needs_bash
@pytest.mark.parametrize("log, phrase", [
    ("E: Could not get lock /var/lib/dpkg/lock-frontend", "Another program is still installing"),
    ("curl: (28) Operation timed out after 20000 milliseconds", "network connection dropped"),
    ("curl: (6) Could not resolve host: dl.google.com", "DNS"),
    ("write error: No space left on device", "disk is full"),
    ("E: Unable to locate package caddy", "package sources"),
    ("dpkg was interrupted, you must manually run", "dpkg --configure -a"),
    ("something odd happened", "last command of this step failed"),
])
def test_failures_are_explained_in_plain_words(tmp_path, log, phrase):
    f = tmp_path / "x.log"
    f.write_text("step output\n" + log + "\n")
    assert phrase in _bash(tmp_path, f'zs_explain_failure "{_posix(f)}"').stdout


@needs_bash
def test_retry_gives_up_after_the_limit_and_succeeds_when_possible(tmp_path):
    r = _bash(tmp_path, 'n=0; flaky() { n=$((n+1)); (( n >= 3 )); }\n'
                        'zs_retry 5 0 flaky && echo "OK after $n"\n'
                        'zs_retry 2 0 false || echo GAVE-UP')
    assert "OK after 3" in r.stdout and "GAVE-UP" in r.stdout


# ------------------------------------------------------------------ preflight

@needs_bash
@pytest.mark.parametrize("osr, ok", [
    ('ID=ubuntu\nVERSION_ID="24.04"\nPRETTY_NAME="Ubuntu 24.04.1 LTS"\n', True),
    ('ID=ubuntu\nVERSION_ID="22.04"\nPRETTY_NAME="Ubuntu 22.04.4 LTS"\n', False),
    ('ID=debian\nVERSION_ID="12"\nPRETTY_NAME="Debian GNU/Linux 12"\n', False),
])
def test_unsupported_os_is_a_hard_failure_with_a_hint(tmp_path, osr, ok):
    f = tmp_path / "os-release"
    f.write_text(osr)
    r = _bash(tmp_path, 'pf_check_os; echo "HARD=$PF_HARD"', {"ZS_OS_RELEASE": _posix(f)},
              libs=("common", "validate", "preflight"))
    assert ("HARD=0" in r.stdout) is ok
    if not ok:
        assert "not supported" in r.stdout and "Ubuntu 24.04 LTS" in r.stdout


# ------------------------------------------------------------------ the stream settings file (.env)

@needs_bash
@pytest.mark.parametrize("mode, existing", [("install", False), ("install", True), ("upgrade", True), ("upgrade", False)])
def test_env_is_only_written_on_a_fresh_install_and_never_overwritten(tmp_path, mode, existing):
    app = tmp_path / "zoom-stream"
    app.mkdir()
    custom = "BOT_NAME=Mine\nRESOLUTION=1920x1080\nVIDEO_BITRATE=7000\n"
    if existing:
        (app / ".env").write_text(custom, newline="\n")
    body = (f'ZS_SRC="{_posix(REPO)}"; ZS_MODE={mode}\n'
            'install() { local src="${@: -2:1}" dst="${@: -1}"; cp "$src" "$dst"; }\n'
            'zs_set BOT_NAME "Class \\"A\\" Bot"; zs_set OUTPUT_PRESET 1080p\n'
            'zs_set YT_RTMP_URL "rtmp://b.rtmp.youtube.com/live2"; zs_set YT_STREAM_KEY "abcd-efgh-ijkl"\n'
            f'zs_write_stream_env "{_posix(app)}"\n')
    r = _bash(tmp_path, body, libs=("common", "validate", "steps"))
    assert r.returncode == 0, r.stderr
    env = app / ".env"
    if existing:
        assert env.read_text() == custom
    elif mode == "upgrade":
        assert not env.exists()
    else:
        text = env.read_text()
        assert 'BOT_NAME="Class \\"A\\" Bot"' in text and "RESOLUTION=1920x1080" in text and "FPS=30" in text
        assert 'YT_RTMP_URL="rtmp://b.rtmp.youtube.com/live2"' in text and 'YT_STREAM_KEY="abcd-efgh-ijkl"' in text
        assert "abcd-efgh-ijkl" not in r.stdout + r.stderr


def test_env_is_written_in_exactly_one_place():
    code = "\n".join(_code(f.read_text(encoding="utf-8")) for f in INSTALLER_FILES)
    assert len(re.findall(r'install -o "\$ZS_SVC_USER" -g "\$ZS_SVC_USER" -m 600 "\$tmp" "\$dir/\.env"', code)) == 1
    assert not re.search(r'(sed\s+-i|\btee\b|>>?)\s*"?[^\n"]*/\.env"', code)


# ------------------------------------------------------------------ static safety rules

def test_installer_never_starts_the_encoder():
    for f in INSTALLER_FILES:
        code = _code(f.read_text(encoding="utf-8"))
        assert not re.search(r"(start|restart|enable --now)\s+ffmpeg-stream", code), f.name
    steps = (LIB / "steps.sh").read_text(encoding="utf-8")
    services = steps[steps.index("zs_step_services() {"):steps.index("zs_step_verify() {")]
    assert "for u in xvfb openbox audio-setup browser-source x11vnc novnc-proxy; do" in services
    assert "ffmpeg-stream" not in _code(services)
    unit = (REPO / "systemd" / "ffmpeg-stream.service").read_text(encoding="utf-8")
    assert "[Install]" not in _code(unit)


OUTPUT_WORDS = r"\b(echo|printf|zs_say|zs_ok|zs_warn|zs_fail|zs_info|zs_hint|zs_title|zs_die|_zs_log_plain|tee|logger|cat)\b"
SECRET_GETTERS = ("zs_get ADMIN_PASSWORD", "zs_get MASTER_PASSWORD", "zs_get VNC_PASSWORD", "zs_get YT_STREAM_KEY")
ALLOWED_SECRET_LINES = (
    "printf 'YT_STREAM_KEY=%s\\n'",                       # into the 600 .env (temp file, then install -m 600)
    "VNC_PASSWORD) printf 'VNC (remote screen) password: %s\\n'",          # into the root-only credentials file
    "MASTER_PASSWORD) printf 'Master encryption password:   %s\\n'",
    "printf '%s\\n' \"$(zs_get ADMIN_PASSWORD)\" | (cd",  # piped to app.cli set-password --password-stdin
    "ZOOMBOT_",                                             # environment of install-dashboard.sh, never argv
    "zs_mask",                                              # summary shows only the length
    "if [[ -n \"$(zs_get YT_STREAM_KEY)\" ]]",
    "[[ -z \"$(zs_get YT_STREAM_KEY)\" ]]",
    "if [[ \"$(zs_get \"$key\")\" == \"auto\" ]]",
    "zs_valid_password \"$v\"",
)


def test_secrets_are_never_printed_or_logged():
    for f in INSTALLER_FILES:
        for n, line in enumerate(_code(f.read_text(encoding="utf-8")).splitlines(), 1):
            secret = any(g in line for g in SECRET_GETTERS) or re.search(
                r"\$\{?ANS_(ADMIN_PASSWORD|MASTER_PASSWORD|VNC_PASSWORD|YT_STREAM_KEY)", line)
            if secret and re.search(OUTPUT_WORDS, line):
                assert any(a in line for a in ALLOWED_SECRET_LINES), f"{f.name}:{n}: {line.strip()}"
        assert not re.search(r"^\s*set\s+-[a-z]*x", _code(f.read_text(encoding="utf-8")), re.M), f.name


def test_installer_scripts_use_strict_mode_and_lf_line_endings():
    for f in INSTALLER_FILES:
        raw = f.read_bytes()
        assert b"\r\n" not in raw, f.name
        if f.parent == REPO:
            assert re.search(r"^set -[Eeuo]*u[Eeuo]*o pipefail|^set -[Euo]+ pipefail", raw.decode(), re.M), f.name


def test_every_answer_key_is_documented_in_the_config_example():
    example = (REPO / "installer" / "config.example").read_text(encoding="utf-8")
    common = (LIB / "common.sh").read_text(encoding="utf-8")
    keys = re.search(r"ZS_ANSWER_KEYS=\(([^)]*)\)", common).group(1).split()
    for key in [*keys, "ACCEPT_WARNINGS"]:
        assert re.search(rf"^{key}=", example, re.M), key


def test_installer_validates_sudoers_and_installs_from_git():
    dash = (REPO / "dashboard" / "install-dashboard.sh").read_text(encoding="utf-8")
    assert 'visudo -cf "${SUDOERS_TMP}"' in dash and "install -o root -g root -m 440" in dash
    install = (REPO / "install.sh").read_text(encoding="utf-8")
    assert "rev-parse --is-inside-work-tree" in install and "git clone --branch" in install


# ------------------------------------------------------------------ command-line guards

@needs_bash
@pytest.mark.parametrize("script", ["install.sh", "update.sh", "doctor.sh", "uninstall.sh", "backup.sh", "restore.sh"])
def test_help_works_without_root(script):
    r = subprocess.run([BASH, _posix(REPO / script), "--help"], capture_output=True, text=True, timeout=30,
                       stdin=subprocess.DEVNULL)
    if script == "install.sh":
        assert r.returncode == 0 and "--config FILE" in r.stdout
    else:
        assert r.returncode in (0, 1) and r.stdout.strip()


@needs_bash
def test_install_rejects_unknown_options_before_doing_anything():
    r = subprocess.run([BASH, _posix(REPO / "install.sh"), "--bogus"], capture_output=True, text=True, timeout=30,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 2 and "Unknown option" in r.stderr


@needs_bash
@pytest.mark.skipif(os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() == 0, reason="running as root")
def test_install_refuses_to_run_without_root():
    r = subprocess.run([BASH, _posix(REPO / "install.sh")], capture_output=True, text=True, timeout=30,
                       stdin=subprocess.DEVNULL)
    if "Please run the installer as root" not in r.stderr:
        pytest.skip("this bash reports EUID 0 (e.g. Git Bash as administrator)")
    assert r.returncode == 1


# ------------------------------------------------------------------ interactive questions (scripted terminal)

ANSWERS = [
    "bad domain", "none",                                   # domain: invalid, then none (-> self-signed, no SSL question)
    "-bad", "",                                             # username: invalid, then default "admin"
    "short", "Correct-Horse-9x", "Mismatch-Horse-9x",       # password: too short; then a mismatch...
    "Correct-Horse-9x", "Correct-Horse-9x",                 # ...then typed twice correctly
    "http://bad", "",                                       # RTMP URL: invalid, then default
    "bad key!", "",                                         # stream key: invalid, then "set later"
    'bad"name', "",                                         # bot name: invalid, then default
    "4k", "",                                               # preset: invalid, then recommended (720p on 4 cores)
    "Mars/Base", "",                                        # timezone: invalid, then detected default
    "",                                                     # VNC: auto-generate
    "lowercaseonly", "Another-Strong-77", "Another-Strong-77",  # master: too weak, then twice
    "maybe", "",                                            # firewall: invalid, then default yes
    "no",                                                   # automatic updates
    "13", "8", "Europe/Berlin",                             # summary: invalid choice, then edit timezone
    "",                                                     # summary: install
]


@needs_bash
def test_interactive_questions_reask_on_bad_input_and_keep_good_answers(tmp_path):
    answers = tmp_path / "tty.txt"
    answers.write_text("\n".join(ANSWERS) + "\n", encoding="utf-8", newline="\n")
    zi = tmp_path / "zi"
    (zi / "Europe").mkdir(parents=True)
    (zi / "UTC").write_text("x")
    (zi / "Europe" / "Berlin").write_text("x")
    body = ('ZS_CPU_CORES=4\n'
            'timedatectl() { echo UTC; }\n'
            'zs_ask_all\n'
            'zs_summary_loop && echo "CONFIRMED"\n'
            'for k in "${ZS_ANSWER_KEYS[@]}"; do v="$(zs_get "$k")"; '
            'if zs_is_secret_key "$k"; then echo "$k=<${#v}>"; else echo "$k=$v"; fi; done\n')
    r = _bash(tmp_path, body, {"ZS_TTY": _posix(answers), "ZS_TTY_OUT": "/dev/null", "ZS_ZONEINFO_DIR": _posix(zi),
                               "ZS_PUBLIC_IP": "203.0.113.7"},
              libs=("common", "validate", "domain", "questions"))
    out = r.stdout
    assert "CONFIRMED" in out, out + r.stderr
    got = dict(line.split("=", 1) for line in out.splitlines() if re.match(r"^[A-Z_]+=", line))
    assert got["DOMAIN"] == "none" and got["SSL_MODE"] == "self-signed" and got["LE_EMAIL"] == ""
    assert got["ADMIN_USERNAME"] == "admin" and got["ADMIN_PASSWORD"] == "<16>"
    assert got["YT_RTMP_URL"] == "rtmps://a.rtmps.youtube.com:443/live2" and got["YT_STREAM_KEY"] == "<0>"
    assert got["BOT_NAME"] == "Stream Bot" and got["OUTPUT_PRESET"] == "720p"
    assert got["TIMEZONE"] == "Europe/Berlin"                       # edited on the summary screen
    assert got["VNC_PASSWORD"] == "<4>"                             # "auto"
    assert got["MASTER_PASSWORD"] == "<17>"
    assert got["FIREWALL"] == "yes" and got["AUTO_UPDATES"] == "no"
    # every bad answer produced a reason and a re-ask
    for reason in ("not a valid domain", "letters, digits", "Too short", "did not match", "rtmp:// or rtmps://",
                   "stream key looks like", "Do not use quotes", "Choose 720p or 1080p", "Unknown timezone",
                   "Too weak", "Answer yes or no", "number from 1 to 12"):
        assert reason in out, reason
    # passwords never appear in the output
    for secret in ("Correct-Horse-9x", "Another-Strong-77", "Mismatch-Horse-9x"):
        assert secret not in out + r.stderr
