# Third-party notices

This project includes or installs the third-party components below. Each keeps its own licence; the project's MIT
licence does not change their terms. Licence names come from each component's own licence file or package metadata.

## Included in this repository

| Component | Where | Licence | Notes |
|---|---|---|---|
| noVNC v1.4.0 (Remote GUI client) | `dashboard/static/vendor/novnc/` | MPL-2.0 (core); `core/des.js` BSD-style (notice in its header); pako MIT (`LICENSE.txt`); licence texts in `docs/` | One file is modified, `core/rfb.js` (20 lines marked `LOCAL PATCH`); the other files are identical to the upstream v1.4.0 tag. Under MPL-2.0 the modified file stays MPL-2.0 and is distributed in source form; see `MODIFICATIONS.md`. |
| Hugeicons "Stroke Rounded" free icon set | `dashboard/templates/_icons.html` | MIT, Copyright (c) 2025 Hugeicons | Notice kept in the file header. Only the free set is used. |
| Lucide icons | `dashboard/templates/_icons.html` (symbols without the `hg-` prefix) | ISC - Copyright (c) Lucide Contributors 2022; portions Copyright (c) Cole Bemis 2013-2022 (Feather, MIT) | Notice kept in the file header. |
| Inter (UI font) | `dashboard/static/fonts/` | SIL OFL 1.1 - `LICENSE-Inter.txt` | |
| Geist / Geist Mono (UI fonts) | `dashboard/static/fonts/` | SIL OFL 1.1 - `LICENSE-Geist.txt` | |
| 66 watermark fonts (Google Fonts) | `scripts/fonts/` | 63 x SIL OFL 1.1, 2 x Apache-2.0 (Permanent Marker, Satisfy), 1 x SIL OFL 1.1 per the upstream repository (Six Caps) | Per-font list in `scripts/fonts/FONT-LICENSES.md`; full texts `OFL-1.1.txt` and `APACHE-2.0.txt`. Files are unmodified. |

**Six Caps:** the font file's metadata has a copyright line but no licence statement. The Google Fonts repository
publishes it under SIL OFL 1.1 (`google/fonts`, `ofl/sixcaps/OFL.txt`, Copyright (c) 2011 Vernon Adams, Reserved
Font Name "Six Caps"). The bundled file is the unmodified build served by the Google Fonts API.

## Downloaded during installation (not included here)

Python packages are installed from PyPI (`dashboard/requirements.txt`, pinned versions):

| Licence | Packages |
|---|---|
| MIT / MIT-0 | annotated-types, anyio, APScheduler, argon2-cffi, argon2-cffi-bindings, cffi, fastapi, h11, httptools, pyaes, pydantic, pydantic-core, pyDes, pytz, PyYAML, six, sniffio (MIT or Apache-2.0), Telethon, typing-inspection, tzlocal, watchfiles, uvloop |
| BSD (2/3-Clause) | click, httpcore, httpx, idna, Jinja2, MarkupSafe, psutil, pyasn1, pycparser, python-dotenv, starlette, uvicorn, websockets |
| Apache-2.0 | cryptography (Apache-2.0 or BSD), python-multipart, rsa |
| MPL-2.0 | certifi |
| PSF-2.0 | typing_extensions |

Downloaded from their publishers or the Ubuntu archive, under their own terms, which you accept by installing them:

* **Zoom Workplace for Linux** - proprietary, from zoom.us. Not redistributed by this project.
* **Google Chrome** - proprietary, from dl.google.com. Not redistributed by this project.
* **FFmpeg** (Ubuntu package, LGPL/GPL depending on build), **Caddy** (Apache-2.0, from Caddy's official apt repository at dl.cloudsmith.io, or Ubuntu's package as fallback), Xvfb, Openbox, x11vnc, PipeWire,
  websockify and other Ubuntu packages - their own licences.
