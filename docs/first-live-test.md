# First live test checklist

Do this once with a real meeting you are allowed to stream, before your first real event. Use an **Unlisted**
YouTube broadcast or a test channel.

## 0. Before the meeting
- [ ] `sudo /opt/zoom-stream/doctor.sh` shows no problems.
- [ ] A stream key is set (Live Studio) and YouTube Studio shows the stream as waiting for data.
- [ ] You have permission to broadcast the meeting, and the host knows.

## 1. Join
- [ ] Zoom page: paste the link, choose the join method, press **Join**. Status reaches *In the meeting*.
- [ ] Remote GUI: the bot is in the meeting, its microphone and camera are off, meeting audio is joined.
- [ ] Overview: `zoom` active, `ffmpeg-stream` **Off air** (not an error).

## 2. Picture
- [ ] Turn on **Meeting only**; the preview no longer shows Zoom's title bar and toolbar.
- [ ] Output page: **Measure now**; try Fit and Fill in speaker view, gallery view and with a shared screen.
      Faces and slides must never be cut off.

## 3. Test recording
- [ ] Live Studio: **Test recording** (60 s). Download it and check picture, sound and lip sync.

## 4. Go Live
- [ ] Press Go Live: the confirmation lists any warnings. Confirm.
- [ ] YouTube Studio receives the stream within about 30 seconds; the picture matches the dashboard preview.
- [ ] Clap or play a countdown with a beep: sound and picture stay in sync (YouTube's delay of 10-30 s is normal).
- [ ] Close the dashboard tab for 2 minutes and reopen: still LIVE, uptime kept counting.

## 5. Quality (10 minutes)
- [ ] Overview/Live Studio: FPS stays at the configured value, dropped frames do not keep growing.
- [ ] YouTube Studio's stream health shows no persistent warnings.
- [ ] A high-motion moment (shared video): FFmpeg speed stays at about 1.0x. If not, lower the preset.
- [ ] CPU load stays below the number of cores; at least 1-2 GB memory remains available.

## 6. Stop
- [ ] **Stop**: `ffmpeg-stream` returns to Off air and stays off.
- [ ] **Leave** (Controls or Zoom page): the bot leaves the meeting.

## Stop the test if
FFmpeg speed stays below 0.9x for more than a minute, dropped frames climb steadily, memory available falls below
1 GB, or faces/slides are clipped. Lower the output quality and try again.
