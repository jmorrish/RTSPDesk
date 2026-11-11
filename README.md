# RTSP Desk

A small Windows desktop app that publishes local video files through FFmpeg and MediaMTX.

## Run the portable app

Prebuilt Windows downloads, when published, will appear on [GitHub Releases](https://github.com/jmorrish/RTSPDesk/releases). You can also create the portable package by following **Build a Windows package** below. Packaged builds include FFmpeg and MediaMTX.

1. Extract **RTSPDesk-Windows-x64.zip** to a folder you can write to. Keep the whole folder together.
2. Open **RTSPDesk.exe**. No Python installation, administrator account, or Windows service is required.
3. Click **Add videos** and select one or more MP4 files (MOV, MKV, AVI, WebM and other FFmpeg-readable videos also work).
4. Leave **One endpoint per file** selected and click **Start all**, or select rows and click **Start selected**.
5. Wait for **Streaming**, then double-click a row or click **Copy URL**.
6. Open the URL in an RTSP player. In VLC, use **Media → Open Network Stream** and enable RTP over RTSP (TCP) in its input/codec preferences if needed.

Example URL: `rtsp://127.0.0.1:8554/demo-22c5bcf989ac`

The endpoint suffix is derived from the full source path. It stays the same between sessions, and equally named files in different folders receive different URLs. Adding the same path twice does not duplicate it. Streams start from the beginning each time; readers joining an already-running stream see its current position.

**Stop selected** stops individual publishers. **Stop all** also shuts down the app's MediaMTX instance and unlocks settings. Closing the window stops all owned processes. Normal end-of-file shows **Finished** when looping is off. The file list and settings are restored on the next launch; streams do not auto-start.

## Playlist

1. Choose **Playlist · one endpoint**.
2. Use **Move up / Move down** to put the files in playback order.
3. Click **Start playlist**. The endpoint is `rtsp://127.0.0.1:8554/playlist`.

All listed files are played consecutively through one continuous FFmpeg publisher. **Loop** repeats the whole list. Disable it to play the list once. Changing order or adding files requires stopping the active playlist.

Before the first run, each item is converted to **1280×720, 30 fps, H.264, stereo 48 kHz AAC**. Aspect ratio is preserved with letterboxing. Missing audio is replaced with silence; short audio is padded to the video duration. This preparation uses CPU, takes time, and needs free disk space. Prepared copies are reused until the source size, modification time, tool path/version timestamp, or preparation profile changes. Original videos are never modified.

Prepared files live in `%LOCALAPPDATA%\RTSPDesk\cache`. Use **Settings → Clear playlist cache** while stopped to reclaim space. The cache has no automatic size limit. Do not change source files while a stream or preparation is running.

## Codec and timestamp handling

| Mode | Behaviour | When to use it |
|---|---|---|
| Compatible (default) | Video to H.264 / yuv420p at 30 fps; AAC stereo 48 kHz if audio exists | General playback and heterogeneous source videos |
| Direct copy | No re-encoding; accepts H.264 or H.265 with AAC or no audio | Lower CPU use when the receiving player supports the source codecs |
| Playlist | Prepare a common 720p format, concatenate, copy video, resample and encode one continuous AAC audio track | Different codecs, sizes, frame rates, and missing audio within one list |

Compatible single-file mode keeps the source resolution, rounding dimensions down to even numbers. It uses software x264 with the `veryfast` preset, CRF 23, no B-frames, and a keyframe every two seconds. Large or numerous videos can exceed the machine's CPU capacity. Direct copy preserves the original keyframe interval; a player may take longer to show its first frame. H.265 support varies by player.

Files are paced with `-re`, so they publish at playback speed instead of being uploaded as fast as possible. Looping uses `-stream_loop -1` on the input, maintaining one FFmpeg connection. Missing presentation timestamps are generated using `-fflags +genpts`; output is shifted with `-avoid_negative_ts make_zero`. The app deliberately does not use `-copyts`, wall-clock input timestamps, or a fresh publisher for each playlist item.

Raw unrelated files cannot safely be fed directly to FFmpeg's concat demuxer: their stream layout, codecs and time bases must match. Preparation makes them consistent. MP4/AAC encoder priming can still create small audio timestamp overlaps at joins, so playlist publishing resamples and re-encodes audio continuously while copying prepared video. This was checked across repeated playlist boundaries. Tiny codec-frame audio adjustments are possible; this is not a sample-accurate editing application.

The first non-cover-art video track and first audio track are used. Subtitles, data tracks and additional audio tracks are omitted. Separate streams may have no audio. Input files need a detectable finite video duration. DRM-protected files are unsupported. There is no HDR tone mapping or interlace-specific processing; use preprocessed SDR/progressive inputs when those features matter. `+genpts` cannot repair every damaged file: use Compatible mode for troublesome timestamps, and inspect Activity for errors.

## Network settings

The default listener is **127.0.0.1:8554**, accessible only on this computer. RTSP and media both use **TCP**; UDP-only clients are not supported.

For another device on a trusted network:

1. Stop all streams and open **Settings**.
2. Enable **Allow devices on my network to view streams**.
3. Set **Host shown in LAN URLs** to this computer's LAN IPv4 address or hostname, for example `192.168.1.20`.
4. If Windows Firewall blocks the connection, allow the packaged `mediamtx.exe` on the private network for the chosen TCP port. The app does not change firewall settings.
5. Start the streams again and use the displayed LAN URLs on the other device.

LAN mode listens on all IPv4 interfaces. Viewing has no password or encryption; do not expose this port to the public internet. Publishing remains restricted to local addresses. Other MediaMTX protocols and its management API are disabled. The URL-host field affects displayed reader URLs; FFmpeg always publishes to localhost.

If the port is occupied, the app reports it and leaves the existing process alone. Change the port instead of trying to run two instances on the same port. MediaMTX configuration files are generated per run and removed when the server stops. Existing MediaMTX installations/configurations are not edited. Environment variables beginning `MTX_` or legacy `RTSP_` are removed from the child server environment to keep its configuration predictable.

## Run from source

Requires Windows 10/11 x64 and Python 3.11 or later with Tcl/Tk (the standard python.org Windows installation includes it). The app itself uses only the Python standard library. This delivery was tested with Python 3.13.14, FFmpeg 8.1.2 and MediaMTX 1.21.3.

Download:

- [FFmpeg Windows build options](https://ffmpeg.org/download.html#build-windows), including [Gyan's builds](https://www.gyan.dev/ffmpeg/builds/). Choose an x64 build containing `libx264`, the AAC encoder, `ffmpeg.exe` and `ffprobe.exe`.
- [MediaMTX releases](https://github.com/bluenviron/mediamtx/releases). Choose the Windows amd64 ZIP.

Place the three executables here, with the upstream licence/readme files:

```text
RTSPDesk-source/
  run.py
  rtspdesk/
  tools/
    ffmpeg.exe
    ffprobe.exe
    mediamtx.exe
```

The portable package's `tools` folder can also be copied into the source folder. Alternatively, add the tools to PATH or choose their folders in Settings. A supplied FFmpeg folder must contain both `ffmpeg.exe` and `ffprobe.exe`. Explicit folders take precedence, followed by `tools` beside the app, the app folder, and PATH.

Run `Launch.cmd`, or in a terminal opened in the source folder:

```powershell
py -3 run.py
```

An alternate storage folder is supported, including for an entirely self-contained local setup:

```powershell
py -3 run.py --data-dir .\my-data
# The packaged executable also accepts --data-dir.
```

## Build a Windows package

Build on Windows using an x64 Python interpreter. This creates a folder-based package rather than extracting large FFmpeg binaries on every launch.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe build.py --tools .\tools
```

The result is `dist\RTSPDesk\RTSPDesk.exe`. Distribute the **entire `dist\RTSPDesk` folder**, including `_internal`, `tools` and licence notices, as a ZIP. `build.py` checks for the three required executables and copies everything in the specified tools folder, including any DLLs and notices needed by shared FFmpeg builds. Keep upstream version and checksum records with your chosen binaries. The executable is not code-signed.

## Diagnostics and tests

**Activity** shows recent messages. **Open logs** opens `%LOCALAPPDATA%\RTSPDesk`, which contains `rtspdesk.log`, rotated backups and `settings.json`. Logs are limited to three files of about 2 MB each. Logs include local filenames and stream URLs and stay on your computer.

- **Connecting does not complete:** inspect the log, confirm the local port is reachable and that local networking is permitted.
- **Port already in use:** choose another port in Settings.
- **Missing tool / encoder:** select the correct folders and use an FFmpeg build with `libx264` and AAC.
- **Client fails to connect:** wait for Streaming, check the copied URL and use TCP. Browsers do not play RTSP URLs directly.
- **Finished too quickly:** enable Loop before starting.
- **Preparation failed:** inspect the file and free disk space; clear the cache and try again if a cached file was damaged.
- **Stuttering:** reduce concurrent streams or source resolution; try Direct copy for compatible files.

Run core checks and optional real streaming tests from the source folder:

```powershell
py -3 -m unittest discover -s tests -p "test_*.py" -v
py -3 tests\integration.py --mediamtx-dir .\tools --ffmpeg-dir .\tools
```

Integration tests generate their own short videos, open a temporary localhost port, receive/decode RTSP streams, and verify independent endpoints, loop boundaries, mixed playlists, audio handling, timestamps, errors and cleanup. They need localhost networking and take about a minute. Results are written under `test-results`.

## Design and references

Tkinter owns the GUI thread. Background workers probe files, prepare playlists and manage publishers; UI updates travel through queues. A shared MediaMTX child serves one FFmpeg child per running file, or one publisher for a playlist. Stop operations are cancellable and all executables are launched without a command shell or terminal window. Windows job objects terminate owned children if the app exits unexpectedly. The app does not kill unrelated processes.

- [MediaMTX: publishing with FFmpeg](https://mediamtx.org/docs/publish/ffmpeg)
- [MediaMTX configuration reference](https://mediamtx.org/docs/references/configuration-file)
- [FFmpeg options and looping](https://ffmpeg.org/ffmpeg.html)
- [FFmpeg RTSP transport](https://ffmpeg.org/ffmpeg-protocols.html#rtsp)
- [FFmpeg concat demuxer](https://ffmpeg.org/ffmpeg-formats.html#concat)
- [PyInstaller packaging](https://pyinstaller.org/en/stable/usage.html)

See `THIRD-PARTY-NOTICES.md` and the bundled upstream licence files before redistributing dependency binaries.
