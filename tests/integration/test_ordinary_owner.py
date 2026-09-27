"""Opt-in existing-tmux proof with an inert native CLI stand-in only.

Select BOTAINER_DASHBOARD_TEST_TMUX explicitly. This never launches Botainer,
containers, agents, credentials, network connections, or package installers.
It exercises native PTY lifetime; a browser/full-service restart is separate.
"""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
import uuid

from botainer_dashboard.ordinary_owner import OrdinaryCliOwner
from botainer_dashboard.launch_console import private_lock


TMUX = os.environ.get("BOTAINER_DASHBOARD_TEST_TMUX")
CHILD = r'''
import fcntl, os, signal, struct, sys, termios
def resized(*_args):
    rows, cols = struct.unpack("HHHH", fcntl.ioctl(0, termios.TIOCGWINSZ, b"\0" * 8))[:2]
    print("CLI_FIXTURE_RESIZE:%d,%d" % (cols, rows), flush=True)
signal.signal(signal.SIGWINCH, resized)
print("CLI_FIXTURE_READY:%d:TTY=%s" % (os.getpid(), os.isatty(0)), flush=True)
print("Inert preflight prompt: type PID, SIZE or EXIT", flush=True)
for line in sys.stdin:
    command = line.strip()
    if command == "PID":
        print("CLI_FIXTURE_PID:%d" % os.getpid(), flush=True)
    elif command == "SIZE":
        rows, cols = struct.unpack("HHHH", fcntl.ioctl(0, termios.TIOCGWINSZ, b"\0" * 8))[:2]
        print("CLI_FIXTURE_SIZE:%d,%d" % (cols, rows), flush=True)
    elif command == "EXIT":
        print("CLI_FIXTURE_NATIVE_FAILURE: expected fixture exit 7", flush=True)
        raise SystemExit(7)
'''


@unittest.skipUnless(TMUX, "requires explicit reviewed existing tmux path")
class OrdinaryOwnerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bdo-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.control = self.root / "o"
        self.control.mkdir(mode=0o700)
        self.tmux = Path(TMUX).resolve(strict=True)
        self.digest = hashlib.sha256(self.tmux.read_bytes()).hexdigest()
        self.env = {"HOME": str(self.root), "PATH": "/usr/bin:/bin", "TERM": "xterm-256color", "LANG": "en_US.UTF-8"}
        self.operation_id = uuid.uuid4().hex
        self.owner = self.make_owner()
        self.record = None
        self.attachments = []

    def make_owner(self):
        return OrdinaryCliOwner(self.control, self.tmux, self.digest, self.env)

    def tearDown(self):
        for attached in self.attachments:
            attached.close()
        if self.record and self.record.get("owner"):
            # Cleanup belongs only to this inert acceptance fixture. Production
            # deliberately retains its dead pane until output is archived.
            self.owner._verified(self.record)
            self.owner._run(self.record, "kill-pane", "-t", self.record["owner"]["paneId"])

    def launch(self, code=CHILD):
        self.record = self.owner.launch(self.operation_id, [sys.executable, "-I", "-S", "-c", code],
                                        cwd=self.root, env=self.env)
        self.assertIn("owner", self.record, self.record)
        return self.record

    def attach(self, cols=100, rows=30):
        writer = private_lock(self.root / "writer")
        try:
            attached = self.owner.attach(self.record, writer, cols, rows)
        except BaseException:
            os.close(writer)
            raise
        self.attachments.append(attached)
        return attached

    def read_until(self, attached, marker):
        output = bytearray()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            chunk = attached.read(65536, timeout=.05)
            if chunk == b"":
                break
            if chunk:
                output.extend(chunk)
                self.assertLess(len(output), 2 * 1024 * 1024)
            if marker in output:
                return bytes(output)
        self.fail("inert CLI terminal missing " + repr(marker) + ": " + repr(bytes(output)[-2048:]))

    def write(self, attached, data):
        offset = 0
        deadline = time.monotonic() + 5
        while offset < len(data) and time.monotonic() < deadline:
            offset += attached.write(data[offset:])
        self.assertEqual(offset, len(data))

    def test_native_input_resize_disconnect_recovery_and_failed_cli_log(self):
        record = self.launch()
        evidence = dict(record["owner"])
        attached = self.attach()
        self.read_until(attached, b"CLI_FIXTURE_READY:" + str(evidence["panePid"]).encode() + b":TTY=True")
        self.write(attached, b"PID\r")
        self.read_until(attached, b"CLI_FIXTURE_PID:" + str(evidence["panePid"]).encode())
        attached.resize(119, 43)
        self.read_until(attached, b"CLI_FIXTURE_RESIZE:119,43")
        self.write(attached, b"SIZE\r")
        self.read_until(attached, b"CLI_FIXTURE_SIZE:119,43")
        attached.close()
        self.assertEqual(self.owner.observe(record)["state"], "running")
        self.owner = self.make_owner()
        self.record = self.owner.recover(self.operation_id)
        self.assertEqual(self.record["owner"], evidence)
        self.assertEqual(self.record["state"], "running")
        second = self.attach(119, 43)
        self.write(second, b"PID\r")
        self.read_until(second, b"CLI_FIXTURE_PID:" + str(evidence["panePid"]).encode())
        self.write(second, b"EXIT\r")
        self.read_until(second, b"CLI_FIXTURE_NATIVE_FAILURE:")
        deadline = time.monotonic() + 8
        eof = False
        while time.monotonic() < deadline:
            if second.read(65536, timeout=.05) == b"":
                eof = True
                break
        self.assertTrue(eof, "ended retained pane must finish its browser stream")
        with self.assertRaises(BrokenPipeError):
            second.write(b"must not reach ended CLI")
        second.close()
        self.assertEqual(self.owner.observe(self.record), {"state": "ended", "exitCode": 7, "exitSignal": None})
        self.assertIn(b"CLI_FIXTURE_NATIVE_FAILURE:", self.owner.capture(self.record))
        self.assertEqual(self.owner.recover(self.operation_id)["owner"], evidence)

    def test_fast_native_failure_keeps_exit_and_readonly_output_without_viewer(self):
        record = self.launch('print("CLI_FIXTURE_EARLY_FAILURE", flush=True)\nraise SystemExit(4)\n')
        deadline = time.monotonic() + 5
        status = self.owner.observe(record)
        while status["state"] == "running" and time.monotonic() < deadline:
            time.sleep(.05)
            status = self.owner.observe(record)
        self.assertEqual(status, {"state": "ended", "exitCode": 4, "exitSignal": None})
        self.assertIn(b"CLI_FIXTURE_EARLY_FAILURE", self.owner.capture(record))
        self.assertEqual(self.owner.recover(self.operation_id)["owner"], record["owner"])


if __name__ == "__main__":
    unittest.main()
