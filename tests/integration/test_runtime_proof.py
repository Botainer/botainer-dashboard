"""Bounded CLI runner exercised with deterministic local Python children only.

This file does not start Botainer, Docker, an agent, or a network connection.
These synthetic checks do not establish live container acceptance.
"""
import os
from pathlib import Path
import sys
import tempfile
import unittest

from botainer_dashboard.backend_errors import BackendUnavailable
from botainer_dashboard.proof_backend import bounded_run


class BoundedCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cwd = Path(self.temp.name)
        self.env = {"PATH": "/usr/bin:/bin", "HOME": str(self.cwd)}

    def tearDown(self): self.temp.cleanup()

    def run_child(self, code, **options):
        return bounded_run([sys.executable, "-I", "-S", "-c", code], cwd=self.cwd, env=self.env, **options)

    def test_stdout_stderr_and_exit_status_are_separate(self):
        status, stdout, stderr = self.run_child("import os,sys;os.write(1,b'out\\x00bytes');os.write(2,b'err');sys.exit(7)")
        self.assertEqual((status, stdout, stderr), (7, b"out\x00bytes", b"err"))

    def test_unbounded_output_is_terminated_at_output_cap(self):
        with self.assertRaisesRegex(BackendUnavailable, "output-limit"):
            self.run_child("import os;os.write(1,b'x'*262144)", max_output=1024)

    def test_hung_command_is_terminated_at_deadline(self):
        with self.assertRaisesRegex(BackendUnavailable, "timeout"):
            self.run_child("import time;time.sleep(20)", timeout=.1)


if __name__ == "__main__": unittest.main()
