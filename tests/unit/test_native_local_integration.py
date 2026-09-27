"""Native launch routing/capabilities/restart replay without a live runtime."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from botainer_dashboard.backend_errors import BackendUnavailable
from botainer_dashboard.local_backend import LocalBackend
from botainer_dashboard.native_cli_runtime import NativeShellProjectBackend
from botainer_dashboard.proof_backend import private_json


REQUEST = "00000000-0000-4000-8000-000000000001"


class NativeLocalRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="dashboard-native-integration-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.local = LocalBackend.__new__(LocalBackend)
        self.local.root = self.root
        self.local.native_cli = True
        self.local._thread_lock = threading.RLock()
        self.local._refresh_projects = Mock()
        self.local.config = SimpleNamespace(source_path=None, machines={})
        self.project = {"id": "project-one", "uuid": "uuid-one", "capabilities": {"configWrite": True}}
        self.local._projects = {"project-one": self.project}
        prepared = self.root / "runtimes/uuid-one/prepared.json"
        prepared.parent.mkdir(parents=True)
        private_json(prepared, {})
        self.backend = Mock(namespace="shell:one", launch_namespace="launch:one", database=self.root / "operations.sqlite")
        self.local.runtime = SimpleNamespace(backend=lambda project: self.backend)
        self.local._runtime = lambda project_id: self.backend

    def test_pending_and_unknown_native_launch_capabilities_are_authoritative(self):
        # Legacy sessions could look stopped/running while the new launch gate
        # remains fenced. LocalBackend must not recalculate a permissive result.
        for state in ("running", "stopped"):
            self.backend.snapshot.return_value = {"sessions": [{"state": state}],
                                                  "capabilities": {"startSession": False, "configWrite": False}}
            value = self.local.snapshot()["projects"][0]
            self.assertFalse(value["capabilities"]["startSession"])
            self.assertFalse(value["capabilities"]["configWrite"])
            self.assertTrue(value["capabilities"]["nativeCliLaunch"])
            self.assertFalse(self.local._can_write("project-one"))

    def test_finished_launch_logs_do_not_disable_native_backend_write_permission(self):
        self.backend.snapshot.return_value = {"sessions": [{"kind": "launch", "state": "stopped", "launchState": "completed"}],
                                              "capabilities": {"startSession": True, "configWrite": True}}
        value = self.local.snapshot()["projects"][0]
        self.assertTrue(value["capabilities"]["startSession"])
        self.assertTrue(value["capabilities"]["configWrite"])
        self.assertTrue(self.local._can_write("project-one"))

    def test_launch_target_is_verified_and_routed_without_alias_to_owner(self):
        self.local.attach("launch:one", REQUEST, 120, 40)
        self.backend.validate_launch.assert_called_once_with(REQUEST)
        self.backend._record.assert_not_called()
        self.backend.attach.assert_called_once_with("launch:one", REQUEST, 120, 40)
        self.backend.validate_launch.side_effect = BackendUnavailable("native-launch-unregistered")
        with self.assertRaisesRegex(BackendUnavailable, "launch-unregistered"):
            self.local.attach("launch:one", "00000000-0000-4000-8000-000000000099", 120, 40)
        self.assertEqual(self.backend.attach.call_count, 1)

    def test_owner_target_remains_exact_and_legacy_mode_refuses_launch_namespace(self):
        self.local.attach("shell:one", "a" * 16, 100, 30)
        self.backend._record.assert_called_once_with("a" * 16)
        self.backend.validate_launch.assert_not_called()
        self.local.native_cli = False
        with self.assertRaisesRegex(BackendUnavailable, "session-unregistered"):
            self.local.attach("launch:one", REQUEST, 100, 30)
        with self.assertRaisesRegex(BackendUnavailable, "session-unregistered"):
            self.local.attach("shell:another-project", "a" * 16, 100, 30)
        self.assertEqual(self.backend.attach.call_count, 1)


class NativeTranscriptRestartTests(unittest.TestCase):
    def test_finished_transcript_replays_after_reconstruction_without_a_cli_process(self):
        with TemporaryDirectory(prefix="dashboard-native-restart-") as directory:
            root = Path(directory).resolve()
            backend = NativeShellProjectBackend.__new__(NativeShellProjectBackend)
            backend.root = root
            backend.launch_namespace = "launch:one"
            backend.namespace = "shell:one"
            backend.project_id = "project-one"
            backend.launches_path = root / "native-launches.json"
            backend.consoles = {}
            backend._thread_lock = threading.RLock()
            backend.verify_files = Mock()
            entry = {"request_id": REQUEST, "state": "completed", "created_at": "synthetic",
                     "result_target": {"contextNamespace": "shell:one", "runtimeId": "a" * 16}}
            private_json(backend.launches_path, {"launches": [entry]})
            transcript = backend._transcript(REQUEST)
            descriptor = os.open(transcript, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(b"Native warning\r\nContinue? y\r\nStarted detached\r\n")
            self.assertTrue(backend._launch_view(entry)["capabilities"]["attachTerminal"])
            viewer = backend.attach("launch:one", REQUEST, 100, 30)
            try:
                self.assertEqual(viewer.read(65536), transcript.read_bytes())
                self.assertEqual(viewer.read(65536), b"")
                with self.assertRaises(BrokenPipeError):
                    viewer.write(b"y\r")
                self.assertEqual(backend._launch_view(entry)["resultTarget"], entry["result_target"])
            finally:
                viewer.close()
            # Opening it again is read-only transcript replay, never another CLI.
            second = backend.attach("launch:one", REQUEST, 80, 24)
            try:
                self.assertEqual(second.read(65536), transcript.read_bytes())
            finally:
                second.close()


if __name__ == "__main__":
    unittest.main()
