"""Opt-in existing-tmux proof using only a deterministic Python child.

Set BOTAINER_DASHBOARD_TEST_TMUX to a reviewed installed binary. No real agent,
agent configuration, credentials, network, package or container is accessed.
This proves local owner/terminal mechanics, not real-agent qualification or an
actual HTTP-service restart. The ordinary integration gate skips this test.
"""
import hashlib
import os
from pathlib import Path
import select
import sys
import tempfile
import time
import unittest
import uuid

from botainer_dashboard.host_backend import HostAgentBackend
from botainer_dashboard.pairing import write_private_json


TMUX = os.environ.get("BOTAINER_DASHBOARD_TEST_TMUX")
CHILD = r'''
import fcntl, os, signal, struct, termios, tty
tty.setraw(0)
def resized(*_args):
    rows, cols = struct.unpack("HHHH", fcntl.ioctl(0, termios.TIOCGWINSZ, b"\0" * 8))[:2]
    os.write(1, ("HOST_FIXTURE_RESIZE:%d,%d\r\n" % (cols, rows)).encode())
signal.signal(signal.SIGWINCH, resized)
os.write(1, ("HOST_FIXTURE_READY:%d\r\n" % os.getpid()).encode())
line = bytearray()
while True:
    value = os.read(0, 1)
    if not value:
        break
    if value in (b"\r", b"\n"):
        if line == b"PID":
            os.write(1, ("HOST_FIXTURE_PID:%d\r\n" % os.getpid()).encode())
        elif line == b"SIZE":
            rows, cols = struct.unpack("HHHH", fcntl.ioctl(0, termios.TIOCGWINSZ, b"\0" * 8))[:2]
            os.write(1, ("HOST_FIXTURE_SIZE:%d,%d\r\n" % (cols, rows)).encode())
        elif line == b"EXIT":
            raise SystemExit(7)
        line.clear()
    else:
        line.extend(value)
'''


@unittest.skipUnless(TMUX, "requires explicit reviewed existing tmux path")
class HostTmuxIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bdh-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.control = self.root / "c"
        self.control.mkdir(mode=0o700)
        projects = self.root / "projects"
        projects.mkdir()
        self.child = self.root / "deterministic-child"
        self.child.write_text("#!" + sys.executable + "\n" + CHILD)
        self.child.chmod(0o700)
        tmux = Path(TMUX).resolve(strict=True)
        self.profile = self.root / "profile.json"
        write_private_json(self.profile, {"version": 1, "id": "inert-test", "label": "Inert fixture",
            "home": str(self.root), "tmux": {"path": str(tmux), "sha256": hashlib.sha256(tmux.read_bytes()).hexdigest()},
            "project_roots": {"fixtures": str(projects)}, "control_root": str(self.control),
            "agents": {"codex": {"path": str(self.child), "sha256": hashlib.sha256(self.child.read_bytes()).hexdigest()}},
            "default_agent": "codex", "environment": {"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"}})
        self.backend = HostAgentBackend(self.root, self.profile)
        self.project = self.backend.create_project({"rootId": "fixtures", "installationId": "inert-test", "path": "project",
            "name": "Inert host fixture", "mode": "create", "requestId": str(uuid.uuid4())})["project"]
        self.session = None
        self.attachments = []

    def tearDown(self):
        for attachment in self.attachments:
            attachment.close()
        if self.session:
            rows = self.backend.snapshot()["sessions"]
            current = next(s for s in rows if s["runtimeId"] == self.session["runtimeId"])
            if current["state"] == "running":
                result = self.backend.stop_session(current["contextNamespace"], current["runtimeId"], str(uuid.uuid4()))
                self.assertEqual(result["session"]["state"], "stopped", "fixture cleanup must be confirmed")
            self.assertNotEqual(current["state"], "unknown", "fixture ownership needs explicit reconciliation")

    def launch(self):
        self.session = self.backend.start_session(self.project["id"], str(uuid.uuid4()))["session"]
        self.assertEqual(self.session["state"], "running")
        return self.backend._load()["sessions"][0]["owner"]

    def attach(self, cols=100, rows=30):
        result = self.backend.attach(self.session["contextNamespace"], self.session["runtimeId"], cols, rows)
        self.attachments.append(result)
        return result

    def read_until(self, attachment, marker):
        output = bytearray()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            chunk = attachment.read(65536, timeout=.05)
            if chunk == b"":
                break
            if chunk:
                output.extend(chunk)
                self.assertLess(len(output), 2 * 1024 * 1024)
            if marker in output:
                return bytes(output)
        self.fail("fixture terminal did not produce " + repr(marker) + ": " + repr(bytes(output)[-2048:]))

    def write(self, attachment, value):
        offset = 0
        deadline = time.monotonic() + 5
        while offset < len(value) and time.monotonic() < deadline:
            offset += attachment.write(value[offset:])
        self.assertEqual(offset, len(value))

    def test_input_resize_detach_and_backend_restart_preserve_original_owner(self):
        owner = self.launch()
        first = self.attach()
        self.read_until(first, b"HOST_FIXTURE_READY:" + str(owner["panePid"]).encode())
        self.write(first, b"PID\r")
        self.read_until(first, b"HOST_FIXTURE_PID:" + str(owner["panePid"]).encode())
        first.resize(119, 43)
        # Client resize and tmux's pane SIGWINCH are asynchronous. Await the
        # original child's actual observation, not a fixed sleep or immediate
        # query that can race the server's next event-loop iteration.
        self.read_until(first, b"HOST_FIXTURE_RESIZE:119,43")
        self.write(first, b"SIZE\r")
        self.read_until(first, b"HOST_FIXTURE_SIZE:119,43")
        first.close()
        self.assertEqual(self.backend.snapshot()["sessions"][0]["state"], "running")
        self.backend.close()
        self.backend = HostAgentBackend(self.root, self.profile)
        self.assertEqual(self.backend._load()["sessions"][0]["owner"], owner)
        second = self.attach(119, 43)
        self.write(second, b"PID\r")
        self.read_until(second, b"HOST_FIXTURE_PID:" + str(owner["panePid"]).encode())
        second.close()
        result = self.backend.stop_session(self.session["contextNamespace"], self.session["runtimeId"], str(uuid.uuid4()))
        self.assertEqual(result["session"]["state"], "stopped")

    def test_natural_exit_status_recorded_and_private_server_retires(self):
        self.launch()
        attachment = self.attach()
        self.read_until(attachment, b"HOST_FIXTURE_READY:")
        self.write(attachment, b"EXIT\r")
        deadline = time.monotonic() + 5
        state = None
        while time.monotonic() < deadline:
            state = self.backend.snapshot()["sessions"][0]
            if state["state"] == "failed":
                break
            time.sleep(.05)
        self.assertEqual(state["state"], "failed")
        self.assertEqual(state["exitCode"], 7)
        self.assertEqual(state["ownerCleanup"], "completed")
        attachment.close()


if __name__ == "__main__":
    unittest.main()
