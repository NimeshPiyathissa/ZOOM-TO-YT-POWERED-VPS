# noVNC in this product - provenance and modifications

* **Upstream:** noVNC **v1.4.0**, https://github.com/novnc/noVNC/tree/v1.4.0 (the `core/`, `vendor/pako/` and
  `LICENSE.txt` files of that release; nothing else is included).
* **Verified 2026-11-11:** every one of the 50 vendored files was compared (git blob hash) with the v1.4.0 tag.
  49 are byte-identical to upstream. **One file is modified: `core/rfb.js`.**
* **Licence:** noVNC core files are MPL-2.0; `core/des.js` carries its own BSD-style notice in its header;
  `vendor/pako/` is MIT (`vendor/pako/LICENSE`). See `LICENSE.txt`. The licence texts it refers to ship in `docs/`
  (`LICENSE.MPL-2.0`, `LICENSE.BSD-3-Clause`, `LICENSE.BSD-2-Clause`, copied unmodified from the v1.4.0 tag).
  The OFL font and CC-BY-SA images that `LICENSE.txt` also mentions are not part of this copy.

## The modification (MPL-2.0 Covered Software, remains under MPL-2.0)

`core/rfb.js` - added 2026-10-03 (commit 44026de in the product history), 20 lines, every change marked
`LOCAL PATCH`:

* `_socketClose()` stores the WebSocket close `code` and `reason`;
* the `disconnect` event's `detail` now also carries that `code` and `reason` (upstream exposes only `clean`).

Why: the dashboard's VNC proxy uses distinct close codes so the client can tell "connection lost, retry" from
"authentication failed, stop retrying"; noVNC v1.4.0 has no public API exposing the close code.

MPL-2.0 obligations this satisfies: the modified file stays under MPL-2.0 with its original header; it ships in
**source form** (unminified) inside every package, so its Source Code Form is available to every recipient; this
file documents the change. The rest of the product is a "Larger Work" and is not affected by MPL-2.0 (section 3.3).
If `core/rfb.js` is ever changed again, update this file; if noVNC is upgraded, re-apply or drop the patch and
re-run the comparison.
