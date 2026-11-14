# FAQ

### Is this the same as Zoom's "Live on Custom Streaming Service"?
No. That feature belongs to the meeting host and needs no server - use it if you can. This project is for when you
are a participant, or need a watermark, framing, scheduling or source switching that Zoom does not offer.

### Do I need permission?
Yes. Only stream meetings you have the right to share, and tell the host and participants first.

### Does it start streaming by itself?
No. Every broadcast needs a confirmed **Go Live** click. Schedules join the meeting and stop at the end, but never
start the broadcast. After a reboot or crash the encoder stays off.

### How much CPU does it need?
There is no GPU, so Zoom or Chrome draws every frame on the CPU - usually more work than encoding. As a guide:

| Output | Suggested server |
|---|---|
| 720p30 (default) | 4 dedicated vCPU, 8 GB |
| 1080p30 | 8+ vCPU; **not verified** on 4 vCPU with a real meeting |

Gallery view with many moving videos costs much more than one speaker or a slide. Measure on your own server with a
test recording and `scripts/measure-cpu.sh`; FFmpeg's speed must stay at 1.0x. Burstable/shared CPU plans may slow
down during long streams.

### Can I use a cheaper 2 vCPU server?
For light 720p use, maybe. The installer warns but lets you continue. Watch for dropped frames.

### Does it work without a domain?
Yes. Use `none`; the dashboard is served at `https://<server-ip>` with a self-signed certificate (one browser
warning).

### Which Zoom links work?
Normal join links (`/j/...`), personal registrant links (`/w/...?tk=...`), meeting ID + passcode, and pasted
invitation text. Personal room vanity links (`/my/name`) must be resolved to a join link first.

### Can it stream something other than Zoom?
Yes: any web page (Google Meet, Teams or Webex web clients, slides, a YouTube video) through the Chrome kiosk, or a
direct media URL (HLS, RTMP/RTMPS, SRT, a file) that FFmpeg reads itself.

### Can it stream to something other than YouTube?
The ingest URL is configurable (`YT_RTMP_URL`, any `rtmp://` or `rtmps://` server), so other RTMP services may
work, but only YouTube is tested.

### Where are my passwords stored?
The dashboard password is stored as an argon2 hash. The VNC password is in the encrypted store. The YouTube stream
key, Zoom passcodes and Telegram token are stored in files only the service accounts can read - see
[security.md](security.md) and [encryption.md](encryption.md).

### How do I update?
`sudo /opt/zoom-stream/update.sh` - it backs up first and rolls back automatically if anything fails.

### How do I remove it?
`sudo /opt/zoom-stream/uninstall.sh` - it asks before deleting any data.

### Can several people use the dashboard?
Yes - create more users with `app.cli set-password <name>`. All users have the same (admin) rights; every action is
in the audit log.

### Docker?
Not supported: the stack needs systemd user services, a virtual X display and PipeWire. Use a VM or VPS.
