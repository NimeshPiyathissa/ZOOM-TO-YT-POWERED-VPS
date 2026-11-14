# Using the dashboard

Open the dashboard address and sign in. The sidebar has four groups: **Monitor**, **Broadcast**, **Sources** and
**System**. Nothing on any page puts you on air except **Go Live** followed by its confirmation.

## The basic flow

1. **Zoom** - add or pick a meeting and press **Join**.
2. **Remote GUI** - check that the bot is in the meeting, muted, with the right view.
3. **Output** - choose the framing; the before/after preview shows what YouTube will get.
4. **Live Studio** - make a **test recording** (60 seconds, saved on the server, never sent to YouTube).
5. **Go Live** - read the warnings in the confirmation dialog and confirm. **Stop** ends the broadcast.

## Monitor

### Overview (`/`)
The program preview, the live state (OFF AIR / READY / LIVE / ERROR), uptime, bitrate, FPS and dropped frames, the
Go Live / Stop / Restart buttons, every background service with a plain-language status, and CPU, memory and disk.
Service cards say *why* a service is stopped (for example "not needed for a web page source").

### Logs (`/logs`)
Live logs of each service. Stream keys, passcodes and meeting tokens are redacted before they reach the page.
**Recent errors** collects the last failures in one place.

### Audit (`/audit`)
Who signed in, changed a setting, joined a meeting or went live - with time and IP address.

## Broadcast

### Live Studio (`/studio`)
- Broadcast state and telemetry (ingest bitrate, FPS, dropped frames, CPU).
- Audio peak meter (left/right) - check it moves before going live.
- **Stream key**: paste or replace the YouTube key (only the last four characters are ever shown).
- **Test recording** and program recording; recordings are listed with their size.
- **YouTube Live Chat**: enter the video ID / watch URL of your broadcast to show its chat.
- **Scheduled start/stop** card (same data as the Schedule page).

### Output (`/output`)
How the meeting fills the 16:9 video:
- **Auto** - measures the picture and chooses.
- **Fit** - the whole meeting is visible; thin bars where its shape is not 16:9.
- **Fill** - no bars; a little of the edge is cropped.

Also the **quality preset** (resolution, frame rate, bitrate, encoder speed) and an encoder health panel. Changes
made while live apply at the next start; the page tells you when a restart is needed and never restarts by itself.

### Controls (`/controls`)
The active source, the Zoom producer (rejoin, leave, mic/camera state), test recording, the pipeline services
(start/stop each one) and a **Danger zone** (stop everything).

### Text Overlays - Watermark Studio (`/overlay`)
Text or a logo burned into the video: font (66 bundled), size, colour, opacity, outline, box, shadow, a 9-point
grid or drag to place. Turn it on or off with the switch. Saving while live applies to the next stream; the page
shows what the running encoder is actually drawing. See [watermark.md](watermark.md).

### Schedule (`/schedule`)
Create one-off or repeating schedules: the bot **joins early** and, at the end time, **stops the stream and leaves**.
At the start time the page tells you the meeting is ready - **going live is always your own Go Live click**. Times
are in the schedule timezone shown on the page (the one chosen during installation).

## Sources

### Zoom (`/zoom`)
- **Paste anything**: a join link, a personal link (`/w/...?tk=`), a meeting ID, or a whole invitation text; the
  page extracts the meeting ID and passcode.
- **Join method**: *Desktop* (Zoom Linux client), *Web* (Zoom's web client in Chrome), or *Auto* (desktop, falling
  back to web).
- **Sign-in**: as a guest, or with a Google account from the Accounts page (needed for meetings that require
  sign-in, and for registration links bound to an e-mail).
- **View** (speaker/gallery), **Meeting only** (hide Zoom's toolbars), rejoin policy, and a live meeting status.
- **Saved meetings**: your library; join one with a click. Passcodes are masked.

### Remote (`/remote`)
A control surface for switching sources while you watch the program: YouTube or any web page (played in the
kiosk browser), Zoom, direct media URLs, and an audio mixer.

### Accounts (`/accounts`)
Separate Chrome profiles for Google accounts. Sign in **by hand** on the Remote GUI - the dashboard never sees your
password or 2-step codes. **Verify now** checks the profile is still signed in, and as whom; a session that has not
been confirmed for 12 hours turns amber so you notice before a stream depends on it.

## System

### Remote GUI (`/vnc`)
The bot's screen in your browser. Use it to watch, to click through a Zoom dialog, or to sign in to Google. It goes
through the dashboard's own login; the VNC server itself is never exposed to the internet.

### Settings (`/settings`)
Appearance (themes), Zoom identity, sources, YouTube ingest and stream key, Telegram notifications and recording
upload, **Restart all services** (asks for an extra confirmation while you are live) and **Reboot server** (first
checks that everything will come back up on its own after the reboot).

## Telegram (optional)
In Settings, add a bot token from [@BotFather](https://t.me/BotFather) and your chat ID to receive stream start/stop
and error notifications, and optionally the recordings.
