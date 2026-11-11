"""Real Windows FFmpeg/MediaMTX tests. Run with --mediamtx-dir PATH."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rtspdesk.core import Service, Settings, endpoint, find_tool

parser = argparse.ArgumentParser()
parser.add_argument("--mediamtx-dir", required=True)
parser.add_argument("--ffmpeg-dir", default="")
parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "test-results" / "integration")
args = parser.parse_args()
root = args.output.resolve()
root.mkdir(parents=True, exist_ok=True)
ffmpeg, ffprobe = find_tool("ffmpeg", args.ffmpeg_dir), find_tool("ffprobe", args.ffmpeg_dir)


def execute(command, timeout=45, binary=False):
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
                            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    if result.returncode:
        raise AssertionError(result.stderr.decode(errors="replace"))
    return result.stdout if binary else result.stdout.decode("utf-8", errors="replace")


red = root / "red caméra 'one'.mp4"
blue = root / "blue silent.avi"
execute([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=320x180:r=25",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", "2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(red)])
execute([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=640x360:r=24",
         "-t", "2.4", "-c:v", "mpeg4", str(blue)])
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
settings = Settings(port=port, ffmpeg_dir=args.ffmpeg_dir, mediamtx_dir=str(Path(args.mediamtx_dir).resolve()))
statuses = {}
events = []
lock = threading.Lock()


def emit(kind, key, value):
    with lock:
        events.append((kind, key, value))
        if kind == "status":
            statuses[key] = value
            print(f"{key}: {value}", flush=True)


service = Service(emit, root / "state avec 'quote'")
checks = []


def wait_for(predicate, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(f"Timeout; statuses: {statuses}; recent events: {events[-20:]}")


def stop_all():
    service.stop_all()
    wait_for(lambda: not service.active())
    assert service.server.child is None


def capture(key, name, seconds=5):
    destination = root / name
    execute([ffmpeg, "-v", "warning", "-y", "-rtsp_transport", "tcp", "-timeout", "7000000",
             "-i", settings.url(key), "-t", str(seconds), "-map", "0", "-c", "copy", str(destination)], timeout=seconds+20)
    # Decode the actual RTSP capture, not just its metadata.
    execute([ffmpeg, "-v", "error", "-i", str(destination), "-f", "null", "-"])
    data = json.loads(execute([ffprobe, "-v", "error", "-show_streams", "-show_packets", "-of", "json", str(destination)]))
    last = {}
    for packet in data["packets"]:
        if "dts_time" in packet:
            stream = packet["stream_index"]
            value = float(packet["dts_time"])
            assert value >= last.get(stream, float("-inf")), (stream, value, last)
            last[stream] = value
    return destination, data


try:
    # Two concurrent endpoints, one with audio, one without, looping beyond EOF.
    service.start([red, blue], settings)
    wait_for(lambda: all(statuses.get(endpoint(p)) == "Streaming" for p in (red, blue)))
    _, red_data = capture(endpoint(red), "individual-red.mkv")
    _, blue_data = capture(endpoint(blue), "individual-blue.mkv")
    assert len(red_data["streams"]) == 2
    assert len(blue_data["streams"]) == 1
    service.stop([endpoint(red)])
    wait_for(lambda: statuses[endpoint(red)] == "Stopped")
    assert statuses[endpoint(blue)] == "Streaming"
    checks.append("Concurrent unique endpoints; loop beyond EOF; audio/no audio; stop selected leaves other stream running")
    stop_all()

    # Real stream-copy, preserving H.264/AAC.
    service.start([red], replace(settings, copy=True))
    wait_for(lambda: statuses.get(endpoint(red)) == "Streaming")
    capture(endpoint(red), "direct-copy.mkv", 4)
    checks.append("Direct-copy H.264/AAC stream decodes across loop")
    stop_all()

    # Mix codecs/resolutions/frame rates/audio layouts and cross the playlist boundary repeatedly.
    service.start([red, blue], settings, playlist=True)
    wait_for(lambda: statuses.get("playlist") == "Streaming", 60)
    capture_path, data = capture("playlist", "playlist.mkv", 11)
    video = next(s for s in data["streams"] if s["codec_type"] == "video")
    audio = next(s for s in data["streams"] if s["codec_type"] == "audio")
    assert (video["width"], video["height"], video["codec_name"]) == (1280, 720, "h264")
    assert (audio["codec_name"], audio["sample_rate"], audio["channels"]) == ("aac", "48000", 2)
    pixels = execute([ffmpeg, "-v", "error", "-i", str(capture_path), "-vf", "fps=2,scale=1:1",
                      "-pix_fmt", "rgb24", "-f", "rawvideo", "-"], binary=True)
    colours = ["red" if pixels[i] > pixels[i+2] else "blue" for i in range(0, len(pixels), 3)]
    transitions = sum(a != b for a, b in zip(colours, colours[1:]))
    assert transitions >= 4, colours
    with lock:
        warnings = [v for kind, key, v in events if kind == "log" and "non-monoton" in v.lower()]
    assert not warnings, warnings
    checks.append(f"Mixed playlist: H.264/AAC 720p; silent audio supplied; {transitions} visible transitions; monotonic timestamps over two loops")
    stop_all()

    service.start([red], replace(settings, loop=False))
    wait_for(lambda: statuses.get(endpoint(red)) == "Finished", 15)
    checks.append("Non-looping file reaches Finished")
    stop_all()

    service.start([blue], replace(settings, copy=True))
    wait_for(lambda: statuses.get(endpoint(blue), "").startswith("Error"))
    checks.append("Unsupported direct-copy codec reports error")
    stop_all()

    corrupt = root / "broken.mp4"
    corrupt.write_bytes(b"not a video")
    service.start([corrupt], settings)
    wait_for(lambda: statuses.get(endpoint(corrupt), "").startswith("Error"))
    checks.append("Corrupt video reports error")
    stop_all()

    service.start([red], settings)
    wait_for(lambda: statuses.get(endpoint(red)) == "Streaming")
    service.server.child.process.kill()
    wait_for(lambda: statuses.get(endpoint(red), "").startswith("Error"))
    checks.append("Unexpected server exit stops publisher and reports error")
    stop_all()

    fresh = root / f"fresh-{time.time_ns()}.mp4"
    shutil.copy2(red, fresh)
    service.start([fresh, blue], settings, playlist=True)
    wait_for(lambda: statuses.get("playlist", "").startswith("Preparing"))
    service.stop_all()
    wait_for(lambda: not service.active())
    assert not list(service.cache.glob("prepare-*.mp4"))
    assert not list(service.root.glob("playlist-*.ffconcat"))
    checks.append("Cancellation during preparation leaves no active publishers or partial files")
finally:
    service.stop_all(close=True)
    wait_for(lambda: not service.active())
    (root / "events.json").write_text(json.dumps(events, indent=2), encoding="utf-8")

(root / "results.json").write_text(json.dumps({"passed": checks}, indent=2), encoding="utf-8")
print(json.dumps({"passed": checks}, indent=2))
