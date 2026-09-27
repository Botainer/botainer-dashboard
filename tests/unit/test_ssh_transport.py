"""Bounded SSH transport uses only local synthetic child processes in tests."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from botainer_dashboard import ssh_transport as transport


class SshTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.root.chmod(0o700)

    def local(self, source, payload=b"", **kwargs):
        return transport.bounded_exchange([sys.executable, "-I", "-B", "-c", source], payload, **kwargs)

    def test_pipe_exchange_drains_output_while_sending_large_helper(self):
        payload = b"x" * transport.MAX_SOURCE
        code = "import os,sys\nos.write(1,b'o'*131072)\ndata=sys.stdin.buffer.read()\nos.write(2,str(len(data)).encode())\n"
        result = self.local(code, payload, timeout=3)
        self.assertEqual(result["transport_status"], "complete")
        self.assertEqual(result["stdout"], b"o" * 131072)
        self.assertEqual(result["stderr"], str(len(payload)).encode())

    def test_separate_output_caps_are_enforced_and_outcome_is_unknown(self):
        for stream in ("stdout", "stderr"):
            fd = 1 if stream == "stdout" else 2
            with self.subTest(stream=stream):
                result = self.local(f"import os\nwhile True: os.write({fd}, b'x'*8192)",
                                    stdout_limit=1024, stderr_limit=511, timeout=3)
                self.assertEqual(result["transport_status"], stream + "-limit")
                self.assertEqual(len(result[stream]), 1024 if stream == "stdout" else 511)
                self.assertEqual(result["remote_outcome"], "unknown")
                self.assertLess(result["elapsed_seconds"], 3)

    def test_timeout_when_stdin_is_not_read_does_not_deadlock(self):
        started = time.monotonic()
        result = self.local("import time\ntime.sleep(10)", b"x" * transport.MAX_SOURCE, timeout=0.2)
        self.assertEqual(result["transport_status"], "timeout")
        self.assertEqual(result["remote_outcome"], "unknown")
        self.assertLess(time.monotonic() - started, 2)

    def test_timeout_cleans_only_spawned_process_group(self):
        real_killpg = os.killpg
        with patch.object(transport.os, "killpg", wraps=real_killpg) as kill:
            result = self.local("import time\ntime.sleep(10)", timeout=0.15)
        self.assertEqual(result["transport_status"], "timeout")
        self.assertEqual(kill.call_count, 1)
        target, signal_number = kill.call_args.args
        self.assertGreater(target, 1)
        self.assertNotEqual(target, os.getpgrp())
        self.assertEqual(signal_number, transport.signal.SIGKILL)

    def test_successful_reap_never_signals_group_and_cleanup_never_polls(self):
        real_popen = subprocess.Popen
        children = []

        def spawn(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            # poll() can reap a child; no caller may poll before signalling.
            child.poll = lambda: self.fail("cleanup must not poll/reap before group cleanup")
            children.append(child)
            return child

        with patch.object(transport.subprocess, "Popen", side_effect=spawn), \
                patch.object(transport.os, "killpg") as kill:
            result = self.local("print('complete')")
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(result["stdout"], b"complete\n")
        self.assertEqual(children[0].returncode, 0)
        kill.assert_not_called()

    def test_ssh_disconnect_and_local_exec_failure_have_distinct_outcomes(self):
        result = self.local("raise SystemExit(255)")
        self.assertEqual(result["remote_outcome"], "unknown")
        result = transport.bounded_exchange([str(self.root / "absent-program")], b"")
        self.assertEqual(result["transport_status"], "start-error")
        self.assertEqual(result["remote_outcome"], "not-dispatched")


if __name__ == "__main__":
    unittest.main()
