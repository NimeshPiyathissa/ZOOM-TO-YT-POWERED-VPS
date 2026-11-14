import pathlib
import sys
import types

# Make `app` importable when pytest is run from anywhere (e.g. `pytest
# dashboard/tests` from the repo root, not just from inside `dashboard/`).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# app/control.py does `import pwd` (POSIX-only - it's for reading the
# zoombot UID on the real VPS). The tests here never actually call
# zoombot_uid(), so a minimal stand-in is enough to let the module import
# cleanly on a non-POSIX dev machine (e.g. Windows) too. This never runs
# on the real deployment target, where the real stdlib `pwd` is used.
if "pwd" not in sys.modules:
    try:
        import pwd  # noqa: F401
    except ImportError:
        fake_pwd = types.ModuleType("pwd")

        class _FakePasswdEntry:
            pw_uid = 1000

        fake_pwd.getpwnam = lambda name: _FakePasswdEntry()
        sys.modules["pwd"] = fake_pwd

if "grp" not in sys.modules:
    try:
        import grp  # noqa: F401
    except ImportError:
        fake_grp = types.ModuleType("grp")

        class _FakeGroupEntry:
            gr_gid = 1000

        fake_grp.getgrnam = lambda name: _FakeGroupEntry()
        sys.modules["grp"] = fake_grp

if "pyDes" not in sys.modules:
    try:
        import pyDes  # noqa: F401
    except ImportError:
        fake_pydes = types.ModuleType("pyDes")
        fake_pydes.des = lambda key, *a, **k: types.SimpleNamespace(encrypt=lambda d: d, decrypt=lambda d: d)
        fake_pydes.ECB = 0
        fake_pydes.PAD_NORMAL = 0
        sys.modules["pyDes"] = fake_pydes


import socket as _socket  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture
def public_dns(monkeypatch):
    """Resolve every hostname to a public documentation address, so tests that save web/direct sources do not depend
    on the machine's DNS (a VPN or DNS proxy can map example.com into a private range the SSRF guard rejects)."""
    from app import url_security

    def fake_getaddrinfo(host, *a, **k):
        return [(_socket.AF_INET, _socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
    monkeypatch.setattr(url_security.socket, "getaddrinfo", fake_getaddrinfo)


@pytest.fixture(autouse=True)
def service_accounts(monkeypatch):
    """Stand-ins for the `zoombot` and `dashboard` accounts that the installer creates, for machines where they do not
    exist (CI runners, developer laptops).

    - pwd.getpwnam / grp.getgrnam answer for those names with stable fake ids; every other name, and the real accounts
      on an installed server, still go through the real lookup.
    - When the tests do not run as root, os.chown cannot hand files to root or to those accounts. Such a chown is a
      no-op here instead of a PermissionError; the ownership logic itself is covered by tests that replace os.chown
      with a recorder (test_secret_store.py).
    """
    import os

    from app import config, control

    fake_uids = {config.ZOOMBOT_USER: 1001, config.DASHBOARD_USER: 1002}
    fake_gids = {config.ZOOMBOT_USER: 1001, config.DASHBOARD_GROUP: 1002}

    import grp
    import pwd

    real_getpwnam, real_getgrnam = pwd.getpwnam, grp.getgrnam

    def getpwnam(name):
        try:
            return real_getpwnam(name)
        except KeyError:
            if name not in fake_uids:
                raise
            return types.SimpleNamespace(pw_name=name, pw_uid=fake_uids[name], pw_gid=fake_uids[name],
                                         pw_dir=f"/home/{name}", pw_shell="/bin/bash")

    def getgrnam(name):
        try:
            return real_getgrnam(name)
        except KeyError:
            if name not in fake_gids:
                raise
            return types.SimpleNamespace(gr_name=name, gr_gid=fake_gids[name], gr_mem=[])

    monkeypatch.setattr(pwd, "getpwnam", getpwnam)
    monkeypatch.setattr(grp, "getgrnam", getgrnam)

    if hasattr(os, "chown") and hasattr(os, "geteuid") and os.geteuid() != 0:
        real_chown = os.chown

        def chown(path, uid, gid, *args, **kwargs):
            try:
                real_chown(path, uid, gid, *args, **kwargs)
            except PermissionError:
                pass

        monkeypatch.setattr(os, "chown", chown)

    control.zoombot_uid.cache_clear()
    yield
    control.zoombot_uid.cache_clear()
