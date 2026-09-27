"""Native CLI console replay and lock fences, using disposable synthetic state."""
import os
from pathlib import Path
import queue
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from botainer_dashboard.backend_errors import BackendUnavailable
from botainer_dashboard.launch_console import LaunchConsole, private_lock, read_transcript


class LaunchConsoleTests(unittest.TestCase):
    def test_console_timeout_is_distinct_from_read_failure_and_normal_eof(self):
        for mode in ('timeout', 'read-error', 'eof'):
            with self.subTest(mode=mode), TemporaryDirectory(prefix="dashboard-console-outcome-") as directory:
                root = Path(directory).resolve()
                client = Mock()
                client.close.return_value = 0
                client.read.side_effect = OSError('fixture read failure') if mode == 'read-error' else None
                client.read.return_value = b''
                owner_fd = os.open(root / 'owner', os.O_CREAT | os.O_WRONLY, 0o600)
                # Exercise the existing deadline branch without sleeping or
                # starting any real PTY/process. Lifetime policy stays unchanged.
                with patch('botainer_dashboard.launch_console.LAUNCH_TIMEOUT', 0 if mode == 'timeout' else 300):
                    console = LaunchConsole(['/fixture/cli'], cwd=root, env={}, transcript=root / 'transcript',
                                            owner_fd=owner_fd, attachment_factory=lambda *args, **kwargs: client)
                    console.thread.join(2)
                self.assertFalse(console.thread.is_alive())
                self.assertTrue(console.done)
                self.assertTrue(console.cleanup_confirmed)
                self.assertEqual(console.timed_out, mode == 'timeout')
                self.assertEqual(console.failed, mode != 'eof')
                client.close.assert_called_once_with(timeout=1)
                viewer = console.attach(None)
                with self.assertRaises(BrokenPipeError): viewer.write(b'y\n')
                client.write.assert_not_called()
                viewer.close()

    def test_private_lock_and_transcript_reject_linked_or_public_files(self):
        with TemporaryDirectory(prefix="dashboard-private-files-") as directory:
            root = Path(directory).resolve()
            path = root / "lock"
            path.write_bytes(b"test")
            path.chmod(0o644)
            with self.assertRaisesRegex(BackendUnavailable, "lock-file-invalid"):
                private_lock(path)
            path.chmod(0o600)
            alias = root / "alias"
            os.link(path, alias)
            with self.assertRaisesRegex(BackendUnavailable, "lock-file-invalid"):
                private_lock(path)
            with self.assertRaisesRegex(BackendUnavailable, "transcript-invalid"):
                read_transcript(path)
            alias.unlink()
            alias.symlink_to(path)
            with self.assertRaises(OSError):
                read_transcript(alias)
            self.assertEqual(read_transcript(path), b"test")

    def test_output_replays_and_closing_view_preserves_cli_and_input(self):
        with TemporaryDirectory(prefix="dashboard-launch-view-") as directory:
            root = Path(directory).resolve()
            pending = queue.Queue()
            client = Mock()
            def read(size, *, timeout):
                try:
                    return pending.get(timeout=timeout)
                except queue.Empty:
                    return None
            client.read.side_effect = read
            client.close.return_value = 0
            client.write.side_effect = len
            owner_fd = os.open(root / "owner", os.O_CREAT | os.O_WRONLY, 0o600)
            console = LaunchConsole(["/fixture/cli"], cwd=root, env={}, transcript=root / "transcript",
                                    owner_fd=owner_fd, attachment_factory=lambda *a, **kw: client)
            pending.put(b"Native warning: Continue? ")
            viewer = console.attach(None)
            self.assertEqual(viewer.read(65536, timeout=1), b"Native warning: Continue? ")
            viewer.close()
            client.close.assert_not_called()
            second = console.attach(None)
            self.assertEqual(second.read(65536), b"Native warning: Continue? ")
            self.assertEqual(second.write(b"y\r"), 2)
            client.write.assert_called_once_with(b"y\r")
            with self.assertRaisesRegex(BackendUnavailable, "writer-busy"):
                console.attach(None)
            pending.put(b"Started\r\n")
            pending.put(b"")
            console.thread.join(2)
            self.assertFalse(console.thread.is_alive())
            self.assertEqual(second.read(65536), b"Started\r\n")
            self.assertEqual(second.read(65536), b"")
            second.close()
            self.assertEqual((root / "transcript").read_bytes(), b"Native warning: Continue? Started\r\n")


if __name__ == "__main__":
    unittest.main()
