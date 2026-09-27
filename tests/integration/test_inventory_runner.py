"""Opt-in bounded-command tests using only deterministic local Python children."""

import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from botainer_dashboard.inventory import InventoryError, bounded_run


class InventoryRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="dashboard-inventory-runner-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.environment = {"HOME": str(self.root), "PATH": str(self.root), "REVIEWED": "fixture"}
        self.children = []
        self.real_popen = subprocess.Popen

    def capture(self, *args, **kwargs):
        process = self.real_popen(*args, **kwargs)
        self.children.append(process)
        return process

    def execute(self, source, *arguments, timeout=2):
        with patch("botainer_dashboard.inventory.subprocess.Popen", side_effect=self.capture):
            return bounded_run((sys.executable, "-I", "-S", "-c", source, *arguments),
                               cwd=self.root, env=self.environment, timeout=timeout)

    def assert_reaped(self):
        self.assertEqual(len(self.children), 1)
        self.assertIsNotNone(self.children[0].returncode)
        with self.assertRaises(ChildProcessError):
            os.waitpid(self.children[0].pid, os.WNOHANG)

    def test_success_preserves_argv_and_minimal_environment(self):
        result = self.execute("import os,sys;os.write(1,(sys.argv[1]+':'+os.environ['REVIEWED']+':'+str(os.environ.get('PYTHONPATH'))).encode())",
                              "$(not-a-shell); literal")
        self.assertEqual(result, b"$(not-a-shell); literal:fixture:None")
        self.assertEqual(self.children[0].returncode, 0)
        self.assert_reaped()

    def test_stdout_limit_kills_and_reaps_without_returning_partial_output(self):
        with patch("botainer_dashboard.inventory.MAX_STDOUT_BYTES", 2048):
            with self.assertRaises(InventoryError) as error:
                self.execute("import os,time;os.write(1,b'x'*100000);time.sleep(10)")
        self.assertEqual(error.exception.code, "inventory-command-output-limit")
        self.assert_reaped()

    def test_stderr_limit_does_not_expose_untrusted_output(self):
        with patch("botainer_dashboard.inventory.MAX_STDERR_BYTES", 2048):
            with self.assertRaises(InventoryError) as error:
                self.execute("import os,time;os.write(2,b'PRIVATE'*20000);time.sleep(10)")
        self.assertNotIn("PRIVATE", str(error.exception))
        self.assertEqual(error.exception.code, "inventory-command-output-limit")
        self.assert_reaped()

    def test_timeout_is_bounded_and_reaps_child(self):
        started = time.monotonic()
        with self.assertRaises(InventoryError) as error:
            self.execute("import time;time.sleep(10)", timeout=0.15)
        self.assertEqual(error.exception.code, "inventory-command-timeout")
        self.assertLess(time.monotonic() - started, 2)
        self.assert_reaped()


if __name__ == "__main__":
    unittest.main()
