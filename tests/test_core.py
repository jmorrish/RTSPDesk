import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rtspdesk.core import Cancelled, Child, Server, Settings, WindowsJob, endpoint, server_config


class CoreTests(unittest.TestCase):
    def test_duplicate_filenames_have_unique_stable_safe_endpoints(self):
        a = endpoint(Path("first folder") / "Caméra 'one'.mp4")
        b = endpoint(Path("other folder") / "Caméra 'one'.mp4")
        self.assertNotEqual(a, b)
        self.assertEqual(a, endpoint(Path("first folder") / "Caméra 'one'.mp4"))
        self.assertRegex(a, r"^[a-z0-9-]+$")

    def test_invalid_settings_rejected(self):
        for settings in (Settings(port=80), Settings(port=65536), Settings(host="host/path"),
                         Settings(host="host\nrtmp: true"), Settings(host="rtsp://host")):
            with self.assertRaises(ValueError):
                settings.validate()

    def test_display_host_never_changes_publish_destination(self):
        settings = Settings(lan=True, host="192.168.1.9", port=8555)
        self.assertEqual(settings.url("clip"), "rtsp://192.168.1.9:8555/clip")
        self.assertEqual(settings.url("clip", publish=True), "rtsp://127.0.0.1:8555/clip")
        self.assertIn("ips: [127.0.0.1", server_config(settings))

    def test_cancellation_terminates_owned_process(self):
        owner = WindowsJob()
        cancel = threading.Event()
        try:
            child = Child([sys.executable, "-c", "import time; time.sleep(60)"], owner, lambda _: None)
            cancel.set()
            with self.assertRaises(Cancelled):
                child.wait(cancel)
            self.assertIsNotNone(child.process.poll())
        finally:
            owner.close()

    def test_os_job_close_terminates_children(self):
        if os.name != "nt":
            self.skipTest("Windows job object")
        owner = WindowsJob()
        child = Child([sys.executable, "-c", "import time; time.sleep(60)"], owner, lambda _: None)
        owner.close()
        child.process.wait(timeout=5)
        child.reader.join(timeout=2)
        self.assertIsNotNone(child.process.returncode)

    def test_occupied_port_does_not_adopt_existing_server(self):
        owner = WindowsJob()
        scratch = Path(__file__).resolve().parents[1] / "test-results"
        scratch.mkdir(exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(dir=scratch) as directory, socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                sock.listen()
                server = Server(owner, Path(directory), lambda _: None)
                with self.assertRaisesRegex(ValueError, "already in use"):
                    server.ensure(Settings(port=sock.getsockname()[1]), "does-not-exist.exe", threading.Event())
                self.assertIsNone(server.child)
        finally:
            owner.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
