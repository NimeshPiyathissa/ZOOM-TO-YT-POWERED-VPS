"""Is this value a stream key at all? Shared by every path that saves YT_STREAM_KEY and by the Go Live preflight.

Why (v1.0.4 incident): a Zoom meeting link copied from the browser's address bar ("https://...zoom.us/j/<id>#success")
was saved as the stream key. stream.sh appended it to the ingest URL, YouTube closed the connection, and the operator
only saw systemd's "control process exited with error code". A key is one token that is appended to the ingest URL, so
anything that is itself a link, or that would change the URL's path/fragment, cannot be one.

Deliberately NOT a YouTube-format check (xxxx-xxxx-xxxx-xxxx-xxxx): a custom YT_RTMP_URL can point at another service
whose keys look different (Twitch "live_...", Facebook "FB-...?s_bl=1&..."). The messages never echo the value."""
from __future__ import annotations

import re

_ALLOWED = re.compile(r"^[A-Za-z0-9._~?&=%+-]+$")
_LINKISH = re.compile(r"://|^(?:www\.|https?:|rtmps?:)|\b(?:zoom\.us|zoom\.com|youtube\.com|youtu\.be|studio\.youtube)\b", re.I)
_ZOOM = re.compile(r"zoom\.(?:us|com)\b", re.I)
_WHERE = "Copy the stream key from YouTube Studio (Go live > Stream > Stream key) and paste only that."
MIN_LEN, MAX_LEN = 4, 256


def problem(key: str) -> str | None:
    """None when `key` can be a stream key, otherwise a short, value-free explanation for the operator."""
    if not key:
        return None                                                    # "missing" is reported separately
    if _ZOOM.search(key):
        return "That is a Zoom meeting link, not a YouTube stream key. " + _WHERE
    if _LINKISH.search(key):
        return "That is a link, not a stream key. " + _WHERE
    if any(c.isspace() for c in key):
        return "A stream key cannot contain spaces. " + _WHERE
    if not _ALLOWED.match(key):
        return "That does not look like a stream key (it contains characters such as / # or quotes). " + _WHERE
    if not (MIN_LEN <= len(key) <= MAX_LEN):
        return f"A stream key is {MIN_LEN} to {MAX_LEN} characters long. " + _WHERE
    return None
