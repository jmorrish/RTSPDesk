# Third-party components

The MIT licence at the root applies to RTSP Desk's original Python source, not to the separately supplied tools or runtime.

- **FFmpeg** and **ffprobe** are separate executables. The supplied Gyan FFmpeg 8.1.2 essentials build uses GPL components, including libx264. Its upstream licence is `tools/FFmpeg-LICENSE`; build information is in `tools/FFmpeg-README.txt` and `tools/VERSIONS.json`. [Upstream FFmpeg source](https://github.com/FFmpeg/FFmpeg/tree/n8.1.2), [build publisher and dependency information](https://www.gyan.dev/ffmpeg/builds/), [FFmpeg licensing](https://ffmpeg.org/legal.html). Retain the applicable notices and meet the licences' corresponding-source obligations when redistributing those binaries; the app's MIT licence does not replace them.
- **MediaMTX 1.21.3** is distributed under the MIT licence. See `tools/MediaMTX-LICENSE` and [its source and notices](https://github.com/bluenviron/mediamtx/tree/v1.21.3).
- The packaged app includes **CPython**, **Tcl/Tk** and a **PyInstaller bootloader**. Their licences/notices are supplied under `licenses` in the portable distribution. [Python licence](https://docs.python.org/3/license.html), [Tcl/Tk terms](https://www.tcl.tk/software/tcltk/license.html), [PyInstaller licence and bootloader exception](https://pyinstaller.org/en/stable/license.html).

Dependency versions, download URLs and checksums are recorded in `tools/VERSIONS.json` in the supplied portable package. If you replace any dependency, preserve the matching licence and build information for the replacement.
