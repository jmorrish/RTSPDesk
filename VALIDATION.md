# Validation — 11 November 2026

Built on Windows 11 x64 using Python 3.13.14 and PyInstaller 6.22.0.
The exact bundled FFmpeg 8.1.2 essentials and MediaMTX 1.21.3 binaries were used for real localhost RTSP tests.

Six core tests passed: stable unique endpoints; invalid settings; local publishing destination; cancellation;
Windows job cleanup; and refusing to adopt an occupied port.

Real-stream scenarios passed:

- Concurrent unique endpoints; loop beyond EOF; audio/no audio; stop selected leaves other stream running
- Direct-copy H.264/AAC stream decodes across loop
- Mixed playlist: H.264/AAC 720p; silent audio supplied; 4 visible transitions; monotonic timestamps over two loops
- Non-looping file reaches Finished
- Unsupported direct-copy codec reports error
- Corrupt video reports error
- Unexpected server exit stops publisher and reports error
- Cancellation during preparation leaves no active publishers or partial files

The playlist test received and decoded 11 seconds through RTSP, observed four alternating colour transitions,
and verified timestamp order across more than two playlist cycles. Test inputs intentionally mixed resolutions,
frame rates, codecs, audio/no audio, and filenames/cache paths containing spaces, Unicode and apostrophes.

GUI: the earlier packaged app launched successfully and its window was inspected. Desktop interaction was
stopped with Escape before the button checks completed. A display-scaling issue was then corrected. Automated
widget-boundary checks passed at 100%, 150% and 200% scaling. The final layout has not been visually rechecked.

Not exercised: a separate physical LAN client, firewall prompts, VLC-specific preferences, long-duration soak
tests, HDR/interlaced sources, or every possible input codec. The software is unsigned.
