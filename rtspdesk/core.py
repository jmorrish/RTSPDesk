from __future__ import annotations

import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable


class Cancelled(Exception):
    pass


def app_root() -> Path:
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]


def data_root() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "RTSPDesk"


def find_tool(name: str, directory: str = "") -> str:
    suffix = ".exe" if os.name == "nt" else ""
    if directory:
        candidate = Path(directory) / (name + suffix)
        if not candidate.is_file():
            raise ValueError(f"{name + suffix} was not found in {directory}.")
        return str(candidate.resolve())
    for folder in (app_root() / "tools", app_root()):
        candidate = folder / (name + suffix)
        if candidate.is_file():
            return str(candidate)
    result = shutil.which(name)
    if result:
        return result
    raise ValueError(f"{name + suffix} was not found. Put it in the tools folder, or choose its folder in Settings.")


def endpoint(path: Path) -> str:
    canonical = os.path.normcase(str(path.resolve()))
    slug = re.sub(r"[^a-z0-9]+", "-", path.stem.lower()).strip("-")[:40] or "video"
    return slug + "-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class Settings:
    port: int = 8554
    lan: bool = False
    host: str = "127.0.0.1"
    loop: bool = True
    copy: bool = False
    ffmpeg_dir: str = ""
    mediamtx_dir: str = ""

    def validate(self):
        if not 1024 <= self.port <= 65535:
            raise ValueError("Choose a port between 1024 and 65535.")
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.\-]*[A-Za-z0-9])?", self.host):
            raise ValueError("URL host must be an IPv4 address or a hostname, without a scheme or port.")

    def url(self, name: str, publish: bool = False) -> str:
        return f"rtsp://{'127.0.0.1' if publish or not self.lan else self.host}:{self.port}/{name}"


def server_config(settings: Settings) -> str:
    settings.validate()
    address = f"{'0.0.0.0' if settings.lan else '127.0.0.1'}:{settings.port}"
    # Publishing stays local, including when anonymous LAN viewing is enabled.
    return f'''logLevel: info
logDestinations: [stdout]
readTimeout: 10s
writeTimeout: 10s
rtsp: true
rtspAddress: "{address}"
rtspTransports: [tcp]
rtmp: false
hls: false
webrtc: false
srt: false
moq: false
api: false
metrics: false
pprof: false
playback: false
authMethod: internal
authInternalUsers:
  - user: any
    ips: [127.0.0.1, "::1"]
    permissions:
      - action: publish
  - user: any
    ips: []
    permissions:
      - action: read
pathDefaults:
  source: publisher
  overridePublisher: false
paths:
  all_others:
'''


class WindowsJob:
    """Close the OS job handle to terminate only children owned by this app."""

    def __init__(self):
        self.handle = None
        if os.name != "nt":
            return
        from ctypes import wintypes as w

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", w.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", w.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in
                        ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                         "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IO),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = w.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        self.kernel.SetInformationJobObject.restype = w.BOOL
        self.kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.kernel.AssignProcessToJobObject.restype = w.BOOL
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.kernel.CloseHandle.restype = w.BOOL
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def attach(self, process: subprocess.Popen):
        if self.handle and not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            error = ctypes.WinError(ctypes.get_last_error())
            process.kill()
            process.wait()
            raise error

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class Child:
    def __init__(self, args: list[str], owner: WindowsJob, log: Callable[[str], None],
                 on_line: Callable[[str], None] | None = None, env: dict | None = None):
        self.tail = deque(maxlen=100)
        self.process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                                        env=env)
        owner.attach(self.process)

        def drain():
            try:
                for raw in self.process.stdout:
                    line = raw.rstrip()
                    self.tail.append(line)
                    log(line)
                    if on_line:
                        on_line(line)
            finally:
                self.process.stdout.close()

        self.reader = threading.Thread(target=drain, daemon=True)
        self.reader.start()

    def stop(self):
        if self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
            except ProcessLookupError:
                pass
        self.reader.join(timeout=2)

    def wait(self, cancel: threading.Event, timeout: float | None = None,
             health: Callable[[], None] | None = None):
        deadline = time.monotonic() + timeout if timeout else math.inf
        try:
            while self.process.poll() is None:
                if cancel.wait(0.1):
                    raise Cancelled()
                if time.monotonic() > deadline:
                    raise RuntimeError("The operation timed out.")
                if health:
                    health()
            self.reader.join(timeout=2)
            if cancel.is_set():
                raise Cancelled()
            if self.process.returncode:
                detail = "\n".join(list(self.tail)[-12:])
                raise RuntimeError(f"{Path(self.process.args[0]).name} exited ({self.process.returncode}).\n{detail}")
        finally:
            if self.process.poll() is None:
                self.stop()


@dataclass(frozen=True)
class Media:
    video: str
    audio: str | None
    duration: float


def probe(path: Path, executable: str, owner: WindowsJob, cancel: threading.Event) -> Media:
    if not path.is_file():
        raise ValueError(f"File no longer exists: {path}")
    lines = []
    child = Child([executable, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                  owner, lines.append)
    child.wait(cancel, timeout=30)
    data = json.loads("\n".join(lines))
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not video:
        raise ValueError(f"{path.name} does not contain a video track.")
    try:
        duration = float(video.get("duration") or data.get("format", {}).get("duration", 0))
    except (TypeError, ValueError):
        duration = 0
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError(f"Could not determine the video duration of {path.name}.")
    return Media(video.get("codec_name", "unknown"), audio.get("codec_name") if audio else None, duration)


def encode_options(playlist: bool) -> list[str]:
    vf = ("scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2:reset_sar=1,"
          "pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1" if playlist else
          "scale=trunc(iw/2)*2:trunc(ih/2)*2")
    return ["-vf", vf + ",fps=30", "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
            "-crf", "23", "-pix_fmt", "yuv420p", "-g", "60", "-keyint_min", "60", "-sc_threshold", "0",
            "-bf", "0", "-threads", "2", "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"]


def publish_command(ffmpeg: str, source: Path, url: str, loop: bool, copy: bool,
                    playlist: bool = False) -> list[str]:
    args = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "warning", "-nostats",
            "-progress", "pipe:1", "-stats_period", "0.5", "-fflags", "+genpts", "-re"]
    if loop:
        args += ["-stream_loop", "-1"]
    if playlist:
        args += ["-f", "concat", "-safe", "0"]
    args += ["-i", str(source), "-map", "0:V:0", "-map", "0:a:0?", "-sn", "-dn"]
    if playlist:
        # AAC priming at each prepared MP4 boundary can overlap by a few samples.
        # Keep video lossless here, but rebuild the audio clock in one encoder.
        args += ["-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
                 "-af", "aresample=async=1:first_pts=0"]
    elif copy:
        args += ["-c", "copy"]
    else:
        args += encode_options(False) + ["-af", "aresample=async=1:first_pts=0"]
    return args + ["-avoid_negative_ts", "make_zero", "-f", "rtsp", "-rtsp_transport", "tcp", url]


def normalize_command(ffmpeg: str, source: Path, destination: Path, media: Media) -> list[str]:
    args = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "warning", "-nostats", "-y",
            "-fflags", "+genpts", "-i", str(source)]
    if not media.audio:
        args += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    args += ["-map", "0:V:0", "-map", "0:a:0" if media.audio else "1:a:0", "-sn", "-dn"]
    args += encode_options(True)
    # One video-frame-aligned duration keeps concat offsets deterministic; pad short audio.
    duration = math.ceil(media.duration * 30) / 30
    args += ["-af", "aresample=async=1:first_pts=0,apad", "-t", f"{duration:.9f}",
             "-video_track_timescale", "90000", "-map_metadata", "-1", "-map_chapters", "-1",
             "-movflags", "+faststart", "-f", "mp4", str(destination)]
    return args


def concat_text(paths: list[Path]) -> str:
    # ffconcat quoting, not shell quoting. Only local app-generated cache paths enter this file.
    return "ffconcat version 1.0\n" + "".join(
        "file '" + p.resolve().as_posix().replace("'", "'\\''") + "'\n" for p in paths)


class Server:
    def __init__(self, owner: WindowsJob, root: Path, log: Callable[[str], None]):
        self.owner, self.root, self.log = owner, root, log
        self.lock = threading.Lock()
        self.child: Child | None = None
        self.signature = None
        self.config_path: Path | None = None

    def ensure(self, settings: Settings, executable: str, cancel: threading.Event):
        signature = (settings.port, settings.lan, executable)
        with self.lock:
            if cancel.is_set():
                raise Cancelled()
            if self.child and self.child.process.poll() is None:
                if signature != self.signature:
                    raise ValueError("Stop all streams before changing server settings.")
                return
            self._stop()
            # Fail explicitly on a port conflict; never adopt/kill an existing server.
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                if os.name == "nt":
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                try:
                    sock.bind(("0.0.0.0" if settings.lan else "127.0.0.1", settings.port))
                except OSError as exc:
                    raise ValueError(f"Port {settings.port} is already in use. Choose another port in Settings.") from exc
            fd, name = tempfile.mkstemp(prefix="mediamtx-", suffix=".yml", dir=self.root)
            self.config_path = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(server_config(settings))
            ready = threading.Event()
            env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("MTX_", "RTSP_"))}
            self.child = Child([executable, str(self.config_path)], self.owner, self.log,
                               lambda line: ready.set() if "[RTSP]" in line and
                               ("listener opened" in line or "started with listeners" in line) else None, env=env)
            self.signature = signature
            deadline = time.monotonic() + 10
            try:
                while not ready.wait(0.1):
                    if cancel.is_set():
                        raise Cancelled()
                    self.check()
                    if time.monotonic() > deadline:
                        raise RuntimeError("MediaMTX did not open its RTSP listener within 10 seconds.")
                self.check()
            except Exception:
                self._stop()
                raise

    def check(self):
        if not self.child or self.child.process.poll() is not None:
            detail = "\n".join(self.child.tail) if self.child else ""
            raise RuntimeError("MediaMTX stopped unexpectedly. Stop all and restart.\n" + detail)

    def _stop(self):
        if self.child:
            self.child.stop()
            self.child = None
        if self.config_path:
            self.config_path.unlink(missing_ok=True)
            self.config_path = None
        self.signature = None

    def stop(self):
        with self.lock:
            self._stop()


@dataclass
class StreamJob:
    key: str
    paths: list[Path]
    settings: Settings
    cancel: threading.Event
    thread: threading.Thread | None = None


class Service:
    def __init__(self, emit: Callable[[str, str, str], None], root: Path | None = None):
        self.emit = emit
        self.root = root or data_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self.cache = self.root / "cache"
        self.cache.mkdir(exist_ok=True)
        self.owner = WindowsJob()
        self.server = Server(self.owner, self.root, lambda line: self.emit("log", "server", line))
        self.jobs: dict[str, StreamJob] = {}
        self.lock = threading.RLock()
        self.cache_lock = threading.Lock()
        self.stopping = False
        self.closed = False

    def active(self) -> bool:
        with self.lock:
            return self.stopping or any(j.thread and j.thread.is_alive() for j in self.jobs.values())

    def start(self, paths: list[Path], settings: Settings, playlist: bool = False):
        settings.validate()
        if not paths:
            raise ValueError("Add at least one video first.")
        ffmpeg = find_tool("ffmpeg", settings.ffmpeg_dir)
        ffprobe = find_tool("ffprobe", settings.ffmpeg_dir)
        mediamtx = find_tool("mediamtx", settings.mediamtx_dir)
        groups = [("playlist", list(paths))] if playlist else [(endpoint(p), [p]) for p in paths]
        with self.lock:
            if self.stopping or self.closed:
                raise ValueError("Please wait for the streams to stop.")
            alive = [j for j in self.jobs.values() if j.thread and j.thread.is_alive()]
            if alive and any((j.key == "playlist") != playlist for j in alive):
                raise ValueError("Stop all streams before changing modes.")
            for key, members in groups:
                previous = self.jobs.get(key)
                if previous and previous.thread and previous.thread.is_alive():
                    continue
                job = StreamJob(key, members, settings, threading.Event())
                self.jobs[key] = job
                job.thread = threading.Thread(target=self._run, args=(job, ffmpeg, ffprobe, mediamtx), daemon=True)
                job.thread.start()

    def _run(self, job: StreamJob, ffmpeg: str, ffprobe: str, mediamtx: str):
        key, settings = job.key, job.settings
        playlist = key == "playlist"
        listing = None
        try:
            self.emit("status", key, "Checking video…")
            media = [probe(p, ffprobe, self.owner, job.cancel) for p in job.paths]
            if playlist:
                prepared = []
                for index, (path, info) in enumerate(zip(job.paths, media), 1):
                    self.emit("status", key, f"Preparing {index}/{len(job.paths)}: {path.name}")
                    prepared.append(self._prepare(path, info, ffmpeg, ffprobe, job))
                fd, name = tempfile.mkstemp(prefix="playlist-", suffix=".ffconcat", dir=self.root)
                listing = Path(name)
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(concat_text(prepared))
                source = listing
            else:
                source = job.paths[0]
                info = media[0]
                if settings.copy and (info.video not in ("h264", "hevc") or info.audio not in (None, "aac")):
                    raise ValueError("Direct copy needs H.264/H.265 video and AAC audio (or no audio). Choose Compatible mode.")
            if job.cancel.is_set():
                raise Cancelled()
            self.emit("status", key, "Starting server…")
            self.server.ensure(settings, mediamtx, job.cancel)
            if job.cancel.is_set():
                raise Cancelled()
            self.emit("status", key, "Connecting…")
            announced = False

            def line_received(line: str):
                nonlocal announced
                if not announced and line.startswith("frame="):
                    try:
                        if int(line.split("=", 1)[1]) > 0:
                            announced = True
                            self.emit("status", key, "Streaming")
                    except ValueError:
                        pass

            def log(line: str):
                if not re.match(r"^[a-z0-9_]+=", line):
                    self.emit("log", key, line)

            child = Child(publish_command(ffmpeg, source, settings.url(key, publish=True), settings.loop,
                                          settings.copy, playlist), self.owner, log, line_received)
            started = time.monotonic()

            def health():
                self.server.check()
                if not announced and time.monotonic() - started > 20:
                    raise RuntimeError("FFmpeg did not start sending video within 20 seconds. Check the log and local networking.")

            child.wait(job.cancel, health=health)
            self.emit("status", key, "Finished")
        except Cancelled:
            self.emit("status", key, "Stopped")
        except Exception as exc:
            self.emit("log", key, str(exc))
            self.emit("status", key, "Error — see log")
        finally:
            if listing:
                listing.unlink(missing_ok=True)

    def _prepare(self, path: Path, media: Media, ffmpeg: str, ffprobe: str, job: StreamJob) -> Path:
        stat, binary_stat = path.stat(), Path(ffmpeg).stat()
        identity = f"v4|{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{ffmpeg}|{binary_stat.st_mtime_ns}"
        cached = self.cache / (hashlib.sha256(identity.encode("utf-8")).hexdigest() + ".mp4")
        # Serialize preparation to bound CPU and prevent two writers for the same cached file.
        while not self.cache_lock.acquire(timeout=0.1):
            if job.cancel.is_set():
                raise Cancelled()
        try:
            if job.cancel.is_set():
                raise Cancelled()
            if cached.is_file():
                try:
                    await_info = probe(cached, ffprobe, self.owner, job.cancel)
                    if await_info.video == "h264" and await_info.audio == "aac":
                        return cached
                except (RuntimeError, ValueError, json.JSONDecodeError):
                    pass
            fd, temp_name = tempfile.mkstemp(prefix="prepare-", suffix=".mp4", dir=self.cache)
            os.close(fd)
            temporary = Path(temp_name)
            try:
                child = Child(normalize_command(ffmpeg, path, temporary, media), self.owner,
                              lambda line: self.emit("log", job.key, line))
                child.wait(job.cancel)
                temporary.replace(cached)
            finally:
                temporary.unlink(missing_ok=True)
            return cached
        finally:
            self.cache_lock.release()

    def stop(self, keys: list[str]):
        with self.lock:
            for key in keys:
                job = self.jobs.get(key)
                if job and job.thread and job.thread.is_alive():
                    self.emit("status", key, "Stopping…")
                    job.cancel.set()

    def stop_all(self, close: bool = False):
        with self.lock:
            if self.stopping:
                if close:
                    self.closed = True
                return
            self.stopping = True
            self.closed = self.closed or close
            jobs = list(self.jobs.values())
            for job in jobs:
                job.cancel.set()

        def finish():
            try:
                for job in jobs:
                    if job.thread:
                        job.thread.join()
                self.server.stop()
            finally:
                with self.lock:
                    if self.closed:
                        self.owner.close()
                    self.stopping = False
                self.emit("idle", "", "")

        threading.Thread(target=finish, daemon=True).start()
