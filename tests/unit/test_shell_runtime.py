"""Shell launch ownership/ambiguity with real private journals and fake runtime.

No Botainer process, Docker call, image or live project is used. These tests
exercise dispatch ordering and durable outcomes, not container confinement.
"""

import copy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import Mock

from botainer_dashboard.backend_errors import BackendUnavailable
from botainer_dashboard.operations import OperationStore
from botainer_dashboard.proof_backend import private_json
from botainer_dashboard.sandbox_runtime import ShellProjectBackend


class ShellLaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="dashboard-shell-journal-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.backend = ShellProjectBackend.__new__(ShellProjectBackend)
        self.backend.root = self.root
        self.backend.project = self.root / "project"
        (self.backend.project / ".botainer").mkdir(parents=True)
        self.config = self.backend.project / ".botainer/config.yaml"
        self.config.write_text("# Synthetic offline dispatch input\n")
        self.backend.project_uuid = "00000000-0000-4000-8000-000000000001"
        self.backend.project_id = "project-fixture"
        self.backend.namespace = "shell:fixture"
        self.backend.plan = {"state_root": str(self.root / "state"), "docker": {"image": "sha256:" + "c" * 64}}
        self.backend.plan_path = self.root / "plan.json"
        private_json(self.backend.plan_path, self.backend.plan)
        self.backend.database = self.root / "operations.sqlite"
        self.backend.registry_path = self.root / "sessions.json"
        self.backend._thread_lock = threading.RLock()
        self.backend.verify_files = Mock()
        self.backend.verify_daemon = Mock()
        self.backend.argv = lambda *args: tuple(args)
        self.states = {}
        self.backend.inspect_container = Mock(side_effect=self.inspect)
        # Actual grant validation is covered separately. Session record ordering
        # and the exact two-record launch protocol remain real in these tests.
        self.backend._session_record = lambda entry: json.loads((Path(entry["record_dir"]) / "spec.json").read_text())
        self.launch_count = 0
        self.backend._run = Mock(side_effect=self.dispatch)
        self.request = "request-one"

    def record_directory(self, sid):
        return Path(self.backend.plan["state_root"]) / "state" / self.backend.project_uuid / "sessions" / sid

    def write_record(self, sid, container_id, *, ended=False):
        folder = self.record_directory(sid)
        folder.mkdir(parents=True, exist_ok=True)
        private_json(folder / "spec.json", {"session_id": sid, "project_uuid": self.backend.project_uuid,
            "project_root": str(self.backend.project), "runtime_handle": {"docker": {"container_id": container_id}},
            "ended_at": "2026-09-20T12:02:00Z" if ended else None,
            "spec": {"mount_plan": {"binds": []}, "env": {"values": {}}}})

    def add_known(self, sid="1" * 16, *, running=True, ended=False):
        container_id = sid * 4
        self.write_record(sid, container_id, ended=ended)
        entry = {"session_id": sid, "container_id": container_id, "record_dir": str(self.record_directory(sid)),
                 "request_id": "prior-" + sid, "started_at": "2026-09-20T12:00:00.000000001Z"}
        registry = self.backend._registry()
        registry["sessions"].append(entry)
        private_json(self.backend.registry_path, registry)
        self.states[sid] = ({"State": {"Running": running, "StartedAt": entry["started_at"]}} if running is not None else None)
        return entry

    def inspect(self, entry, *, missing_ok=False):
        value = self.states.get(entry["session_id"])
        if isinstance(value, BaseException):
            raise value
        if value is None and not missing_ok:
            raise BackendUnavailable("fixture-container-unavailable")
        return copy.deepcopy(value)

    def dispatch(self, argv, *, timeout):
        self.assertEqual(timeout, 45)
        self.assertEqual(argv[0], "start")
        expected_revision = hashlib.sha256(self.config.read_bytes()).hexdigest()
        self.assertEqual(argv[1], expected_revision)
        with OperationStore(self.backend.database) as store:
            pending = store.unresolved()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0].state, "dispatching")
            self.assertEqual(pending[0].config_revision, expected_revision)
            self.assertEqual(pending[0].checkout, str(self.backend.project))
            self.assertEqual(pending[0].context_id, self.backend.namespace)
        self.launch_count += 1
        sid = f"{self.launch_count + 100:016x}"
        container_id = sid * 4
        self.write_record(sid, container_id)
        self.states[sid] = {"State": {"Running": True, "StartedAt": f"2026-09-20T12:01:{self.launch_count:02d}.000000001Z"}}
        summary = {"session_id": sid, "project_root": str(self.backend.project), "project_uuid": self.backend.project_uuid,
            "image": self.backend.plan["docker"]["image"], "runtime": "docker", "network_mode": "none",
            "network_endpoints": [], "mounts": [], "auth_profile": "default", "plugins_enabled": ["agent-dashboard-test"],
            "entrypoint_wraps": [["/bin/sh", "/mnt/dashboard-fixture/terminal.sh"]], "nudge_enabled": False,
            "port_forwards": [], "auth_mode": "none", "git_unprotected": False, "env_var_names": [],
            "env_files": [], "host_hooks": [], "capabilities": []}
        handle = {"session_id": sid, "container_id": container_id, "runtime": "docker", "nudge_supported": False,
                  "project_root": str(self.backend.project), "session_record": str(self.record_directory(sid))}
        return 0, (json.dumps(summary) + "\n" + json.dumps(handle) + "\n").encode(), b""

    def start(self, request=None):
        return self.backend.start_session(self.backend.project_id, request or self.request)

    def operation(self, request=None):
        with OperationStore(self.backend.database) as store:
            return store.get(request or self.request)

    def test_known_live_session_can_coexist_and_intent_precedes_new_dispatch(self):
        old = self.add_known()
        result = self.start()
        sid = result["session"]["runtimeId"]
        self.assertNotEqual(sid, old["session_id"])
        self.assertEqual(result["session"]["state"], "running")
        self.assertEqual(self.launch_count, 1)
        self.assertEqual(self.operation().state, "succeeded")
        self.assertEqual(self.operation().session_id, sid)
        self.assertEqual(len(self.backend._registry()["sessions"]), 2)
        self.assertEqual(self.states[old["session_id"]]["State"]["Running"], True)

    def test_unknown_existing_session_blocks_before_dispatch_or_intent(self):
        self.add_known(running=None)
        with self.assertRaisesRegex(BackendUnavailable, "existing-session-unknown"):
            self.start()
        self.backend._run.assert_not_called()
        with self.assertRaises(KeyError):
            self.operation()

    def test_ended_record_with_present_container_is_unknown_and_blocks_dispatch(self):
        entry = self.add_known(ended=True)
        for running in (True, False):
            with self.subTest(running=running):
                self.states[entry["session_id"]]["State"]["Running"] = running
                snapshot = self.backend.snapshot()
                self.assertEqual(snapshot["sessions"][0]["state"], "unknown")
                self.assertFalse(snapshot["capabilities"]["startSession"])
                with self.assertRaisesRegex(BackendUnavailable, "ended-record-container-present"):
                    self.start()
                with self.assertRaises(KeyError):
                    self.operation()
        self.backend._run.assert_not_called()

    def test_ended_record_with_confirmed_absent_container_is_stopped(self):
        entry = self.add_known(ended=True, running=None)
        snapshot = self.backend.snapshot()
        self.assertEqual(snapshot["sessions"][0]["state"], "stopped")
        self.assertTrue(snapshot["capabilities"]["startSession"])
        self.backend.inspect_container.assert_called_once_with(entry, missing_ok=True)
        self.backend._run.assert_not_called()

    def test_daemon_or_existing_inspection_uncertainty_never_dispatches(self):
        old = self.add_known()
        self.states[old["session_id"]] = BackendUnavailable("fixture-daemon-unconfirmed")
        with self.assertRaisesRegex(BackendUnavailable, "daemon-unconfirmed"):
            self.start()
        self.backend._run.assert_not_called()
        with self.assertRaises(KeyError):
            self.operation()

    def test_successful_request_is_idempotent_after_config_change_and_stop(self):
        result = self.start()
        sid = result["session"]["runtimeId"]
        self.config.write_text("# A later editor revision must not redispatch an old request.\n")
        self.assertEqual(self.start(), result)
        entry = self.backend._record(sid)
        self.write_record(sid, entry["container_id"], ended=True)
        self.states[sid] = None
        replay = self.start()
        self.assertEqual(replay["session"]["runtimeId"], sid)
        self.assertEqual(replay["session"]["state"], "stopped")
        self.assertEqual(self.backend._run.call_count, 1)
        self.assertEqual(self.operation().state, "succeeded")

    def test_proven_prelaunch_refusal_is_failed_and_same_request_never_retries(self):
        self.backend._run.side_effect = None
        self.backend._run.return_value = (3, b"", b"fixture-config-refusal")
        with self.assertRaisesRegex(BackendUnavailable, "config-refused-before-launch"):
            self.start()
        self.assertEqual(self.operation().state, "failed")
        self.assertEqual(self.operation().result_code, "config-refused-before-launch")
        with OperationStore(self.backend.database) as store:
            self.assertEqual(store.unresolved(), [])
        with self.assertRaisesRegex(BackendUnavailable, "launch-unresolved"):
            self.start()
        self.assertEqual(self.backend._run.call_count, 1)
        # A newly initiated action may try again after the user fixes config.
        self.backend._run.side_effect = self.dispatch
        result = self.start("request-after-fix")
        self.assertEqual(result["session"]["state"], "running")
        self.assertEqual(self.operation("request-after-fix").state, "succeeded")

    def test_postdispatch_error_is_unknown_and_blocks_all_retries(self):
        self.backend._run.side_effect = None
        self.backend._run.return_value = (1, b"partial launch output", b"fixture failure")
        with self.assertRaisesRegex(BackendUnavailable, "outcome-unconfirmed"):
            self.start()
        self.assertEqual(self.operation().state, "unknown")
        receipt = json.loads((self.root / f"launch-{self.request}.json").read_text())
        self.assertEqual(receipt["exit_code"], 1)
        for request in (self.request, "new-request"):
            with self.subTest(request=request), self.assertRaisesRegex(BackendUnavailable, "launch-unresolved"):
                self.start(request)
        self.assertEqual(self.backend._run.call_count, 1)

    def test_exit_three_with_output_is_not_mislabeled_safe_prelaunch_failure(self):
        self.backend._run.side_effect = None
        self.backend._run.return_value = (3, b"some launch output", b"fixture failure")
        with self.assertRaisesRegex(BackendUnavailable, "outcome-unconfirmed"):
            self.start()
        self.assertEqual(self.operation().state, "unknown")

    def test_timeout_keeps_unknown_journal_and_never_automatically_redispatches(self):
        self.backend._run.side_effect = BackendUnavailable("fixture-process-timeout")
        with self.assertRaisesRegex(BackendUnavailable, "process-timeout"):
            self.start()
        self.assertEqual(self.operation().state, "unknown")
        for request in (self.request, "later-request"):
            with self.assertRaisesRegex(BackendUnavailable, "launch-unresolved"):
                self.start(request)
        self.assertEqual(self.backend._run.call_count, 1)

    def test_failed_postlaunch_inspection_keeps_cleanup_handle_before_unknown(self):
        def refuse_after_dispatch(entry, *, missing_ok=False):
            raise BackendUnavailable("fixture-launched-scope-unconfirmed")
        self.backend.inspect_container.side_effect = refuse_after_dispatch
        with self.assertRaisesRegex(BackendUnavailable, "scope-unconfirmed"):
            self.start()
        self.assertEqual(self.operation().state, "unknown")
        registry = self.backend._registry()
        self.assertEqual(len(registry["sessions"]), 1)
        entry = registry["sessions"][0]
        self.assertEqual(entry["request_id"], self.request)
        self.assertEqual(len(entry["container_id"]), 64)
        self.assertTrue((Path(entry["record_dir"]) / "spec.json").is_file())
        self.assertNotIn("started_at", entry)
        with self.assertRaisesRegex(BackendUnavailable, "launch-unresolved"):
            self.start("later-request")
        self.assertEqual(self.backend._run.call_count, 1)

    def test_live_session_limit_and_unregistered_owner_block_before_dispatch(self):
        for index in range(8):
            self.add_known(f"{index + 1:016x}")
        with self.assertRaisesRegex(BackendUnavailable, "eight-session-limit"):
            self.start()
        self.backend._run.assert_not_called()
        self.write_record("f" * 16, "e" * 64)
        with self.assertRaisesRegex(BackendUnavailable, "unregistered-runtime-record"):
            self.start()
        self.backend._run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
