"""Regression tests for the v1.0.2 audit fixes (installer, update, uninstall, dependencies, CI).

Shell code is executed for real in bash against a sandbox; system tools the code calls (ufw, sshd, ss, userdel ...)
are replaced by bash functions that record what they were asked to do.
"""
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
GIT = shutil.which("git")
needs_bash = pytest.mark.skipif(BASH is None, reason="bash not available - installer scripts NOT executed")
needs_git = pytest.mark.skipif(BASH is None or GIT is None, reason="bash/git not available")


def _posix(p):
    p = pathlib.Path(p).resolve().as_posix()
    return "/" + p[0].lower() + p[2:] if len(p) > 1 and p[1] == ":" else p


def _bash(tmp_path, body: str, env: dict | None = None, libs=("common", "validate")):
    script = tmp_path / "harness.sh"
    head = "set -uo pipefail\n"
    head += f'export ZS_ETC_DIR="{_posix(tmp_path / "etc")}" ZS_LOG_DIR="{_posix(tmp_path / "log")}" NO_COLOR=1\n'
    for lib in libs:
        head += f'source "{_posix(LIB / (lib + ".sh"))}"\n'
    script.write_text(head + body + "\n", encoding="utf-8", newline="\n")
    full_env = dict(os.environ, **(env or {}))
    return subprocess.run([BASH, _posix(script)], capture_output=True, text=True, timeout=120, env=full_env,
                          stdin=subprocess.DEVNULL)


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


# ------------------------------------------------------------------ firewall: SSH on a custom port must stay reachable

FAKE_NET = r'''
sshd() { printf 'port %s\n' ${FAKE_SSHD_PORTS:-}; [[ -n "${FAKE_SSHD_PORTS:-}" ]]; }
ss() { [[ -n "${FAKE_SS_PORT:-}" ]] && printf 'LISTEN 0 4096 0.0.0.0:%s 0.0.0.0:* users:(("sshd",pid=1,fd=3),("systemd",pid=1,fd=99))\n' "$FAKE_SS_PORT"
       printf 'LISTEN 0 4096 127.0.0.1:8000 0.0.0.0:* users:(("uvicorn",pid=2,fd=3))\n'; }
ufw() { echo "ufw $*" >>"$UFW_LOG"; }
'''


def _firewall(tmp_path, env):
    log = tmp_path / "ufw.log"
    log.write_text("", encoding="utf-8")
    env = {"UFW_LOG": _posix(log), "SSH_CONNECTION": "", **env}
    r = _bash(tmp_path, FAKE_NET + "ANS_FIREWALL=yes; ANS_SSL_MODE=self-signed\nzs_step_firewall",
              env, libs=("common", "validate", "steps"))
    assert r.returncode == 0, r.stderr
    return log.read_text(encoding="utf-8").splitlines()


@needs_bash
def test_firewall_allows_a_custom_ssh_port_before_it_is_enabled(tmp_path):
    calls = _firewall(tmp_path, {"FAKE_SSHD_PORTS": "2222"})
    assert "ufw allow 2222/tcp comment SSH" in calls
    assert calls.index("ufw allow 2222/tcp comment SSH") < calls.index("ufw --force enable")
    assert "ufw allow OpenSSH" not in calls          # 22 is not opened when SSH does not use it


@needs_bash
def test_firewall_default_port_22_keeps_the_openssh_rule(tmp_path):
    calls = _firewall(tmp_path, {"FAKE_SSHD_PORTS": "22"})
    assert "ufw allow OpenSSH" in calls and calls.index("ufw allow OpenSSH") < calls.index("ufw --force enable")


@needs_bash
def test_firewall_collects_ports_from_sshd_the_socket_and_the_current_session(tmp_path):
    calls = _firewall(tmp_path, {"FAKE_SSHD_PORTS": "22", "FAKE_SS_PORT": "2200",
                                 "SSH_CONNECTION": "198.51.100.7 50000 203.0.113.9 2201"})
    for rule in ("ufw allow OpenSSH", "ufw allow 2200/tcp comment SSH", "ufw allow 2201/tcp comment SSH"):
        assert rule in calls
    assert not any("8000" in c for c in calls)       # a non-SSH listener is never opened


@needs_bash
def test_firewall_falls_back_to_22_when_nothing_can_be_detected(tmp_path):
    calls = _firewall(tmp_path, {})
    assert "ufw allow OpenSSH" in calls and "ufw --force enable" in calls


# ------------------------------------------------------------------ locale: tool output is matched as English text

@needs_bash
def test_installer_tools_run_in_the_c_locale_whatever_the_server_language(tmp_path):
    body = r'''
ufw() { if [[ "${LC_ALL:-}" == C* ]]; then echo "Status: active"; echo "443/tcp ALLOW Anywhere"; else echo "Status: aktiv"; fi; }
echo "LC_ALL=$LC_ALL LANGUAGE=[$LANGUAGE]"
if ufw status | grep -q "Status: active"; then echo UFW_SEEN_ACTIVE; fi
_zs_ufw_allows 443 && echo PORT_443_SEEN
'''
    r = _bash(tmp_path, body, {"LC_ALL": "de_DE.UTF-8", "LANG": "de_DE.UTF-8", "LANGUAGE": "de"},
              libs=("common", "domain"))
    assert "LC_ALL=C.UTF-8 LANGUAGE=[]" in r.stdout
    assert "UFW_SEEN_ACTIVE" in r.stdout and "PORT_443_SEEN" in r.stdout


# ------------------------------------------------------------------ update.sh never moves backwards on its own

def _git(cwd, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.com", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    r = subprocess.run([GIT, "-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false",
                        *args], cwd=cwd, capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _origin(tmp_path):
    """A small upstream repo: commit 1 = tag v1.0.0, commit 2 = tag v1.0.1, commit 3 = main only (post-release fix)."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q")
    for n, tag in ((1, "v1.0.0"), (2, "v1.0.1"), (3, None)):
        (origin / "file.txt").write_text(f"{n}\n", encoding="utf-8")
        _git(origin, "add", "-A")
        _git(origin, "commit", "-qm", f"c{n}")
        if tag:
            _git(origin, "tag", tag)
    return origin


@needs_git
def test_ancestor_helper_tells_older_from_newer(tmp_path):
    origin = _origin(tmp_path)
    body = (f'R="{_posix(origin)}"\n'
            'zs_git_is_ancestor "$R" v1.0.1 main && echo TAG_IS_IN_MAIN\n'
            'zs_git_is_ancestor "$R" main v1.0.1 || echo MAIN_NOT_IN_TAG\n'
            'zs_git_is_ancestor "$R" v1.0.1 v1.0.1 && echo SAME_IS_ANCESTOR\n')
    out = _bash(tmp_path, body).stdout
    assert "TAG_IS_IN_MAIN" in out and "MAIN_NOT_IN_TAG" in out and "SAME_IS_ANCESTOR" in out


def test_update_refuses_an_automatic_downgrade_but_allows_an_explicit_ref():
    src = _read("update.sh")
    guard = src.index('if (( REF_CHOSEN )) && zs_git_is_ancestor "$SRC" "$TARGET" "$CURRENT"; then')
    assert src.index('if [[ "$CURRENT" == "$TARGET" ]]') < guard < src.index('zs_title "1/4 Backup"')
    assert guard < src.index("if (( CHECK )); then")              # --check reports the same answer
    block = src[guard:guard + 400]
    assert "exit 0" in block and "--ref" in block
    assert re.search(r'REF_CHOSEN=0\nif \[\[ -z "\$REF" \]\]; then\n  REF_CHOSEN=1', src)


# ------------------------------------------------------------------ install.sh bootstrap (the one-command install)

def _bootstrap_fn() -> str:
    src = _read("install.sh")
    return src[src.index("# >>> bootstrap-fn"):src.index("# <<< bootstrap-fn")]


def _bootstrap(tmp_path, dest, ref, marker="no-marker", origin=None):
    origin = origin or tmp_path / "origin"
    body = (_bootstrap_fn() + "\nexport GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null\n"
            f'zs_bootstrap_clone "{_posix(dest)}" "{_posix(origin)}" "{ref}" "{_posix(tmp_path / marker)}"\n'
            'echo "RC=$?"\n')
    return _bash(tmp_path, body, libs=())


@needs_git
def test_bootstrap_clones_the_requested_ref(tmp_path):
    _origin(tmp_path)
    dest = tmp_path / "clone"
    r = _bootstrap(tmp_path, dest, "v1.0.0")
    assert "RC=0" in r.stdout, r.stderr
    assert (dest / "file.txt").read_text().strip() == "1"


@needs_git
def test_bootstrap_rerun_moves_an_unfinished_install_to_the_requested_ref(tmp_path):
    origin = _origin(tmp_path)
    dest = tmp_path / "clone"
    assert "RC=0" in _bootstrap(tmp_path, dest, "v1.0.0").stdout
    assert "RC=0" in _bootstrap(tmp_path, dest, "main").stdout               # tag -> branch
    assert (dest / "file.txt").read_text().strip() == "3"
    (origin / "file.txt").write_text("4\n", encoding="utf-8")                # upstream moves on
    _git(origin, "commit", "-qam", "c4")
    assert "RC=0" in _bootstrap(tmp_path, dest, "main").stdout               # the branch follows it
    assert (dest / "file.txt").read_text().strip() == "4"
    assert "RC=0" in _bootstrap(tmp_path, dest, "v1.0.1").stdout             # branch -> tag
    assert (dest / "file.txt").read_text().strip() == "2"


@needs_git
def test_bootstrap_leaves_a_finished_install_alone(tmp_path):
    _origin(tmp_path)
    dest = tmp_path / "clone"
    _bootstrap(tmp_path, dest, "v1.0.0")
    (tmp_path / "installed").write_text("done", encoding="utf-8")
    assert "RC=0" in _bootstrap(tmp_path, dest, "main", marker="installed").stdout
    assert (dest / "file.txt").read_text().strip() == "1"                    # only update.sh changes versions


@needs_git
def test_bootstrap_never_overwrites_local_edits_or_foreign_directories(tmp_path):
    _origin(tmp_path)
    dest = tmp_path / "clone"
    _bootstrap(tmp_path, dest, "v1.0.0")
    (dest / "file.txt").write_text("my edit\n", encoding="utf-8")
    r = _bootstrap(tmp_path, dest, "main")
    assert "RC=0" in r.stdout and "edited locally" in r.stderr
    assert (dest / "file.txt").read_text().strip() == "my edit"

    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "keep.txt").write_text("x", encoding="utf-8")
    r = _bootstrap(tmp_path, foreign, "main")
    assert "RC=1" in r.stdout and "not a git clone" in r.stderr
    assert (foreign / "keep.txt").exists()

    r = _bootstrap(tmp_path, tmp_path / "clone2", "v9.9.9")
    assert "RC=0" not in r.stdout                                            # unknown version: a clear failure


# ------------------------------------------------------------------ packages: every tool a script calls is installed

# External programs the pipeline scripts call -> the Ubuntu 24.04 package that provides each one.
TOOL_PACKAGES = {
    "Xvfb": "xvfb", "openbox": "openbox", "x11vnc": "x11vnc", "wmctrl": "wmctrl", "xdotool": "xdotool",
    "xprop": "x11-utils", "xwininfo": "x11-utils", "xdpyinfo": "x11-utils", "xset": "x11-xserver-utils",
    "xsetroot": "x11-xserver-utils", "xrandr": "x11-xserver-utils", "unclutter": "unclutter",
    "ffmpeg": "ffmpeg", "ffprobe": "ffmpeg", "pactl": "pulseaudio-utils", "parec": "pulseaudio-utils",
    "paplay": "pulseaudio-utils", "websockify": "websockify", "rsync": "rsync", "jq": "jq", "curl": "curl",
    "fc-cache": "fontconfig", "fc-list": "fontconfig", "pgrep": "procps", "pkill": "procps", "flock": "util-linux",
    "ss": "iproute2", "dig": "bind9-dnsutils", "openssl": "openssl", "python3": "python3", "git": "git",
    "fuser": "psmisc", "killall": "psmisc", "logrotate": "logrotate", "ufw": "ufw", "caddy": "caddy",
    "sudo": "sudo", "wget": "wget",
}
# Not from apt's package list: installed from their vendors' own .deb in separate steps.
VENDOR_TOOLS = {"zoom", "google-chrome-stable", "google-chrome"}
# Other well-known programs: if a script starts calling one of these, a package decision is needed here.
KNOWN_UNPACKAGED = {"scrot", "xwd", "convert", "identify", "nc", "ncat", "socat", "lsof", "netstat",
                    "sqlite3", "xclip", "xsel", "bc", "zip", "unzip", "xauth", "vncpasswd", "pacmd", "pw-cli",
                    "wpctl", "xdg-open", "gsettings", "dbus-launch", "tmux", "crontab", "ifconfig"}


def _installed_packages() -> set[str]:
    src = _read("installer/lib/steps.sh")
    block = src[src.index("zs_apt install --no-install-recommends"):src.index("local c missing=()")]
    words = set(re.findall(r"[a-z0-9][a-z0-9.+-]+", block.replace("\\\n", " ")))
    return words | {"util-linux", "sudo"} if "util-linux" in words else words


def _commands_used(text: str) -> set[str]:
    """Program names in command position: at the start of a line or after ; | & ( $( ` then/do/else/exec/timeout ..."""
    used = set()
    for line in text.splitlines():
        code = line.split(" #", 1)[0]
        if code.lstrip().startswith("#"):
            continue
        for m in re.finditer(r"(?:^|[;|&(`]|\$\(|\b(?:then|do|else|exec|timeout(?: -\S+)*(?: [0-9.]+[smh]?)?|nohup|setsid|"
                             r"stdbuf(?: -\S+)*|sudo(?: -\S+ \S+)*|command -v|if|while|until|!)\s)\s*"
                             r"(?:/usr/s?bin/|/bin/)?([A-Za-z][A-Za-z0-9_.-]*)", code):
            used.add(m.group(1))
    return used


def test_every_external_tool_the_scripts_call_is_installed_by_the_installer():
    packages = _installed_packages()
    for tool, pkg in TOOL_PACKAGES.items():
        assert pkg in packages, f"'{tool}' needs package '{pkg}', which install.sh does not install"
    used = set()
    for f in sorted((REPO / "scripts").glob("*.sh")):
        used |= _commands_used(f.read_text(encoding="utf-8"))
    assert "rsync" in used and "wmctrl" in used                      # the scanner really sees commands
    unknown = used & KNOWN_UNPACKAGED
    assert not unknown, f"scripts call {sorted(unknown)}, which the installer does not install"


def test_installer_verifies_the_tools_after_installing():
    src = _read("installer/lib/steps.sh")
    check = src[src.index("local c missing=()"):src.index('if (( ${#missing[@]} ))')]
    for tool in ("rsync", "xprop", "parec", "flock", "ffprobe", "websockify"):
        assert re.search(rf"\b{re.escape(tool)}\b", check), f"{tool} is not verified after the package step"


# ------------------------------------------------------------------ Python dependencies are fully pinned

def _pins(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?==([^\s#]+)", line.strip())
        if m:
            out[re.sub(r"[-_.]+", "-", m.group(1)).lower()] = m.group(2)
    return out


def test_constraints_pin_every_requirement_at_the_same_version():
    req = _read("dashboard/requirements.txt")
    cons = _pins(_read("dashboard/constraints.txt"))
    assert "-c constraints.txt" in req.splitlines()
    for name, version in _pins(req).items():
        assert cons.get(name) == version, f"{name}=={version} is not pinned identically in constraints.txt"
    for indirect in ("starlette", "pydantic", "pydantic-core", "anyio", "h11", "uvloop", "httptools", "cffi"):
        assert indirect in cons, f"indirect dependency {indirect} is not pinned"
    assert all(re.fullmatch(r"[0-9][0-9A-Za-z.+!-]*", v) for v in cons.values())


def test_constraints_file_is_deployed_next_to_requirements():
    src = _read("dashboard/install-dashboard.sh")
    assert '"${SRC_DIR}/constraints.txt"' in src
    assert src.index("constraints.txt") < src.index('pip" install -q -r "${APP_DIR}/requirements.txt"')


# ------------------------------------------------------------------ uninstall.sh

def test_uninstall_reports_unexpected_failures_and_ends_with_exit_0():
    src = _read("uninstall.sh")
    assert "set -Eeuo pipefail" in src
    assert "trap 'on_error $? $LINENO' ERR" in src and "unexpected error at line" in src
    tail = [line for line in src.strip().splitlines() if line.strip()][-2:]
    assert tail == ["trap - ERR", "exit 0"]


@needs_bash
def test_uninstall_only_claims_a_user_is_deleted_when_it_is(tmp_path):
    src = _read("uninstall.sh")
    fn = src[src.index("remove_user() {"):src.index("if (( PURGE )); then")]
    body = r'''
sleep() { :; }
loginctl() { echo "loginctl $*" >>"$CALLS"; }
pkill() { echo "pkill $*" >>"$CALLS"; : >"$STATE/quiet"; }
pgrep() { [[ ! -e "$STATE/quiet" && "$2" == busy* ]]; }
userdel() { echo "userdel $1" >>"$CALLS"; [[ "$1" == busy-forever ]] && return 8; : >"$STATE/gone-$1"; }
id() { [[ "$1" == ghost ]] && return 1; [[ ! -e "$STATE/gone-$1" ]]; }
''' + fn + r'''
remove_user ghost; echo "ghost=$?"
remove_user idle; echo "idle=$?"
remove_user busy; echo "busy=$?"
rm -f "$STATE/quiet"
remove_user busy-forever; echo "forever=$?"
'''
    state = tmp_path / "state"
    state.mkdir()
    calls = tmp_path / "calls.log"
    r = _bash(tmp_path, body, {"STATE": _posix(state), "CALLS": _posix(calls)}, libs=())
    assert "ghost=0" in r.stdout and "idle=0" in r.stdout and "busy=0" in r.stdout, r.stdout + r.stderr
    assert "forever=1" in r.stdout                                   # still there -> reported, not claimed deleted
    log = calls.read_text(encoding="utf-8")
    assert "userdel ghost" not in log                                # a missing account is not touched
    assert log.index("pkill -KILL -u busy") < log.index("userdel busy")
    purge = src[src.index("if (( PURGE )); then"):src.index("if (( REMOVE_PKGS )); then")]
    assert "Could not delete the user account(s)" in purge
    assert purge.index("remove_user") < purge.index('rm -rf "$ZS_DASH_HOME"')


def test_uninstall_removes_chromes_apt_source_in_both_formats():
    src = _read("uninstall.sh")
    assert "google-chrome.list" in src and "google-chrome.sources" in src


# ------------------------------------------------------------------ CI workflow

def test_ci_actions_run_on_a_supported_node_runtime():
    wf = _read(".github/workflows/ci.yml")
    checkout = [int(v) for v in re.findall(r"actions/checkout@v(\d+)", wf)]
    setup_py = [int(v) for v in re.findall(r"actions/setup-python@v(\d+)", wf)]
    assert checkout and min(checkout) >= 5, "actions/checkout below v5 runs on the deprecated Node 20"
    assert setup_py and min(setup_py) >= 6, "actions/setup-python below v6 runs on the deprecated Node 20"
