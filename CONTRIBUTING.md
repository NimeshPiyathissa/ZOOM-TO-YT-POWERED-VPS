# Contributing

Thanks for helping! Bug reports, documentation fixes and pull requests are all welcome.

## Before you start

- Search the existing issues first. For a larger change, open an issue to discuss it before writing code.
- By contributing you agree that your work is released under the [MIT licence](LICENSE).
- Follow the [Code of Conduct](CODE_OF_CONDUCT.md).
- Never paste stream keys, passwords, meeting links, real domains or IP addresses into issues, commits or tests.

## Development setup

The dashboard and most tests run on any machine with Python 3.12 and bash (Linux, macOS, or Windows with Git Bash);
the full stack needs Ubuntu 24.04.

```bash
git clone https://github.com/NimeshPiyathissa/ZOOM-TO-YT-POWERD-VPS.git
cd REPO/dashboard
python3 -m venv venv && . venv/bin/activate
pip install -r requirements-dev.txt
pytest -q                      # several minutes: real ffmpeg and bash tests are included
ruff check app/ tests/
cd .. && find . -name '*.sh' -not -path './dashboard/static/vendor/*' -print0 | xargs -0 shellcheck -x
```

Some tests need `ffmpeg` and `bash` on the PATH; they are skipped locally (and CI fails) if those are missing.

## Rules the code must keep

These are enforced by tests - please keep them green:

1. **Nothing starts a broadcast except a confirmed Go Live.** Schedules, watchdogs, installers and source switches
   never start or restart `ffmpeg-stream` (`tests/test_no_autostart_policy.py`, `tests/test_install_wizard.py`).
2. **Secrets never appear in argv, logs or installer output.** Pass them through stdin or environment variables.
3. **Shell scripts pass `shellcheck` at all severities** and use strict mode.
4. **The dashboard talks to the pipeline only through the sudoers allowlist** in `dashboard/install-dashboard.sh`.
   Adding a command there needs a clear reason in the pull request.
5. **User input that reaches Chrome, FFmpeg or a config file is validated** (see `app/url_security.py`,
   `scripts/lib.sh`).

## Pull requests

- One topic per pull request; describe what changed and how you tested it.
- Add or update tests for behaviour changes, and update the docs and `CHANGELOG.md` (Unreleased section).
- If you changed the installer, say whether you ran it on a fresh Ubuntu 24.04 server.
