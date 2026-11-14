# Watermark

The watermark is burned into the video by the encoder (FFmpeg `drawtext` / `overlay`). There is one implementation;
the old browser-DOM overlay is retired (only its clean-up remains).

## Pipeline

```
Watermark Studio (/overlay, static/js/overlay.js)
  -> POST /api/overlay            settings (never ON/OFF, never the image path)
  -> POST /api/overlay/toggle     {"enabled": true|false}   the only ON/OFF switch
  -> app/overlay.py normalize()   validation; refused values are returned in "rejected"
  -> data/overlay.json            the saved truth (atomic write)
  -> app/overlay.py encoder_env() WATERMARK_* keys
  -> current-source.env           via scripts/write-source.sh
  -> scripts/lib.sh build_watermark_filter()   re-validates every value, builds the filter
  -> FFmpeg                       at the next encoder start
  -> logs/watermark.status        what that start really did (on / off / error + reason, font, settings hash, PID)
  -> GET /api/overlay/status      saved vs. live, read back from the status file
```

## Rules

* **Saving never starts, stops or restarts anything.** FFmpeg's filter graph is fixed at start, so a change made while
  a broadcast is running is queued and the page shows *"Changes will apply to the next stream."*
* **Every authorized Go Live re-reads the saved settings** and writes them in the same step as the one-time start
  permission (`control.write_broadcast_auth`). A stale `current-source.env` cannot put a disabled watermark on air.
* **Fonts are real files.** `scripts/fonts/<key>.ttf` + `scripts/fonts/fonts.json` (66 static fonts, licences in
  `FONT-LICENSES.md`). Only installed, checksum-verified fonts are offered; the preview loads the same file
  (`/overlay/fonts/<key>.ttf`). A missing or damaged font is an error that is shown - never a silent default.
* **Sizes are pixels on a 1080-line frame** and scale with the output height, so 720p and 1080p look the same.
* **Multi-line text** is one `drawtext` per line (FFmpeg draws a line break as an empty box with most fonts).
* **The preview is close, not pixel-identical.** It uses the same font file, size, colour and position rules; the
  browser's line box is slightly taller than FFmpeg's glyph-tight text box, so edges can differ by about 1 % of the frame.
* **Image watermarks** use a plain `scale` when the frame size is known (every capture source). `scale2ref`
  (deprecated since FFmpeg 7, present in 6.1) is only used for a direct source whose size is unknown.
* The watermark never blocks a broadcast: if it cannot be drawn the stream starts without it and says why.

## Adding or refreshing fonts (developer machine, not the VPS)

```
pip install fonttools
python tools/fetch_watermark_fonts.py      # rewrites scripts/fonts/*.ttf, fonts.json, FONT-LICENSES.md
```

## Verification status

| What | Status |
|---|---|
| Settings -> env -> shell filter -> real FFmpeg frames (on/off, all 66 fonts, size, position, opacity, colour, outline/box/shadow, image, multi-line, non-Latin text) | Automated - `dashboard/tests/test_watermark_pipeline.py` |
| No stale filter after turning it off; a restart picks up saved settings; watermark actions never start a broadcast | Automated - same file |
| Watermark Studio in a real browser, preview compared with the encoded frame (within a few pixels at 1080p) | Manual test passed during development |
| Ubuntu 24.04's FFmpeg 6.1 drawing the same output | See [testing.md](testing.md) |
| A real Zoom meeting streamed to YouTube with a watermark | Not yet verified in a real live class - see [testing.md](testing.md) |
