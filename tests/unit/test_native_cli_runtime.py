"""Private launch journaling/CLI instrumentation, without any runtime dispatch."""
import importlib.util
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from botainer_dashboard.backend_errors import BackendUnavailable
from botainer_dashboard.native_cli_runtime import NativeShellProjectBackend, NativeSandboxRuntime
from botainer_dashboard.operations import OperationStore
from botainer_dashboard.proof_backend import digest, private_json

HELPER = Path(__file__).resolve().parents[2] / "tools/native_cli_botainer_helper.py"
SPEC = importlib.util.spec_from_file_location("native_cli_test_helper", HELPER)
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)
REQUEST = "00000000-0000-4000-8000-000000000001"
REQUEST2 = "00000000-0000-4000-8000-000000000002"


class NativePreparationTests(unittest.TestCase):
    def test_new_preparation_pins_extracted_console_without_changing_baseline(self):
        with TemporaryDirectory(prefix="dashboard-native-prepare-") as directory:
            root = Path(directory).resolve()
            source_state = root / "baseline-state"
            (source_state / "plugins").mkdir(parents=True)
            (source_state / "policy.yaml").write_text("# inert policy fixture\n")
            baseline_plan = root / "baseline.json"
            baseline_plan.write_text('{"fixture": true}\n')
            original = baseline_plan.read_bytes()
            runtime = object.__new__(NativeSandboxRuntime)
            runtime.repo = HELPER.parents[1]
            runtime.root = root / "workspace"
            (runtime.root / "runtimes").mkdir(parents=True)
            runtime.helper = runtime.repo / "tools/shell_botainer_helper.py"
            runtime.native_helper = HELPER
            runtime.plan = {}
            runtime.baseline = SimpleNamespace(verify_files=Mock(), plan={"state_root": str(source_state)},
                plan_path=baseline_plan, receipt={"daemon_id": "fixture-daemon", "executables": []})
            runtime.backend = Mock(return_value="prepared-fixture")
            project = {"uuid": "fixture-project", "id": "fixture", "path": str(root / "project"),
                       "device": 1, "inode": 2}
            self.assertEqual(runtime.prepare_project(project), "prepared-fixture")
            receipt = json.loads((runtime.root / "runtimes/fixture-project/prepared.json").read_text())
            selected = {item["path"]: item["sha256"] for item in receipt["protected_files"]}
            console = runtime.repo / "src/botainer_dashboard/launch_console.py"
            self.assertEqual(selected[str(console)], digest(console))
            self.assertEqual(baseline_plan.read_bytes(), original)
            runtime.baseline.verify_files.assert_called_once_with()


class NativeReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="dashboard-native-receipts-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.plan = self.root / "plan.json"
        private_json(self.plan, {})
        self.receipt = self.root / "result.json"
        self.backend = SimpleNamespace(project=self.project, project_uuid="project-uuid", plan={},
                                       plan_path=self.plan, _receipt=lambda request: self.receipt,
                                       verify_files=Mock(), verify_daemon=Mock())
        self.load = Mock()
        self.config = SimpleNamespace(load_config=self.load)
        self.launch = Mock(return_value=SimpleNamespace(id="c" * 64))
        self.composition = SimpleNamespace(launch=self.launch)
        self.spec = SimpleNamespace(model_dump=lambda **kwargs: {}, session_id="a" * 16,
                                    runtime="docker", project_root=str(self.project), state_dir=str(self.root / "state"))
        self.cfg = Mock()
        self.adapter = Mock()
        self.adapter.return_value.render_argv.return_value = ["docker", "run", "--rm", "-dit", "--pull", "never"]
        self.validation = patch.object(helper, "validate_spec")
        self.validation.start()
        self.addCleanup(self.validation.stop)

    def run_cli(self, cli):
        return helper.native_start(self.backend, REQUEST, "f" * 64, cli_main=cli,
                                   config_module=self.config, composition=self.composition,
                                   adapter_class=self.adapter, cfg=self.cfg)

    def test_decline_and_prelaunch_failure_preserve_cli_arguments_and_never_dispatch(self):
        for code in (0, 1, 3, 130):
            with self.subTest(code=code):
                def cli(args):
                    self.assertEqual(args, ["start", "--runtime=docker", "--detach", "--no-auto-onboard"])
                    self.assertIs(self.config.load_config(self.project), self.cfg.model_copy.return_value)
                    self.assertFalse(self.receipt.exists())
                    return code
                self.assertEqual(self.run_cli(cli), code)
                result = json.loads(self.receipt.read_text())
                self.assertEqual(result["phase"], "not-dispatched")
                self.assertEqual(result["exit_code"], code)
                self.launch.assert_not_called()
                self.assertIs(self.config.load_config, self.load)
                self.receipt.unlink()

    def test_before_dispatch_is_durable_and_result_is_exact_without_terminal_parsing(self):
        def runtime(spec, *, detach):
            self.assertIs(spec, self.spec)
            self.assertTrue(detach)
            record = json.loads(self.receipt.read_text())
            self.assertEqual(record["phase"], "before-dispatch")
            self.assertEqual(record["request_id"], REQUEST)
            return SimpleNamespace(id="c" * 64)
        self.launch.side_effect = runtime
        def cli(args):
            self.composition.launch(self.spec, detach=True)
            self.assertEqual(json.loads(self.receipt.read_text())["phase"], "runtime-returned")
            return 0
        self.assertEqual(self.run_cli(cli), 0)
        result = json.loads(self.receipt.read_text())
        self.assertEqual(result["phase"], "completed")
        self.assertEqual(result["result"]["session_id"], "a" * 16)
        self.launch.assert_called_once_with(self.spec, detach=True)

    def test_dispatch_exception_never_becomes_safe_decline(self):
        self.launch.side_effect = RuntimeError("dispatch may have succeeded")
        with self.assertRaises(RuntimeError):
            self.run_cli(lambda args: self.composition.launch(self.spec, detach=True))
        self.assertEqual(json.loads(self.receipt.read_text())["phase"], "before-dispatch")
        self.assertIs(self.composition.launch, self.launch)

    def test_interruption_before_dispatch_is_explicit_no_dispatch(self):
        def cli(args):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.run_cli(cli)
        self.assertEqual(json.loads(self.receipt.read_text())["phase"], "not-dispatched")
        self.assertEqual(json.loads(self.receipt.read_text())["exit_code"], 130)

    def test_result_write_failure_after_launch_never_becomes_safe_decline(self):
        original_write = helper.private_json
        def fail_result(path, value):
            if value["phase"] in {"runtime-returned", "completed"}:
                raise OSError("synthetic lost result")
            return original_write(path, value)
        with patch.object(helper, "private_json", side_effect=fail_result):
            with self.assertRaises(OSError):
                self.run_cli(lambda args: self.composition.launch(self.spec, detach=True))
        self.launch.assert_called_once()
        self.assertEqual(json.loads(self.receipt.read_text())["phase"], "before-dispatch")

    def test_invalid_scope_or_second_dispatch_never_reaches_runtime_again(self):
        self.adapter.return_value.render_argv.return_value = ["docker", "run", "--rm", "-d"]
        with self.assertRaises(RuntimeError):
            self.run_cli(lambda args: self.composition.launch(self.spec, detach=True))
        self.launch.assert_not_called()
        self.assertFalse(self.receipt.exists())
        self.adapter.return_value.render_argv.return_value = ["docker", "run", "--rm", "-dit", "--pull", "never"]
        def twice(args):
            self.composition.launch(self.spec, detach=True)
            self.composition.launch(self.spec, detach=True)
        with self.assertRaises(RuntimeError):
            self.run_cli(twice)
        self.assertEqual(self.launch.call_count, 1)
        self.assertNotEqual(json.loads(self.receipt.read_text())["exit_code"], 0)


class NativeJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="dashboard-native-journal-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        backend = self.backend = NativeShellProjectBackend.__new__(NativeShellProjectBackend)
        backend.root = self.root
        backend.project = self.root / "project"
        (backend.project / ".botainer").mkdir(parents=True)
        (backend.project / ".botainer/config.yaml").write_text("fixture")
        backend.project_uuid = "project-fixture"
        backend.project_id = "project-fixture"
        backend.namespace = "shell:fixture"
        backend.launch_namespace = "launch:fixture"
        backend.plan_path = self.root / "plan.json"
        backend.plan = {"interpreter": "/fixture/python", "state_root": str(self.root / "state")}
        private_json(backend.plan_path, backend.plan)
        backend.registry_path = self.root / "sessions.json"
        backend.launches_path = self.root / "native-launches.json"
        backend.database = self.root / "operations.sqlite"
        backend._thread_lock = threading.RLock()
        backend.consoles = {}
        backend.owner = SimpleNamespace(native_helper=Path("/fixture/native-helper.py"))
        backend.verify_files = Mock()
        backend.verify_daemon = Mock()
        backend.environment = lambda: {}
        self.fds = []
        self.addCleanup(self.close_fds)
        def create_console(argv, **kwargs):
            with OperationStore(backend.database) as store:
                self.assertEqual(store.get(REQUEST).state, "dispatching")
            self.assertEqual(backend._launches()["launches"][0]["request_id"], REQUEST)
            self.fds.append(kwargs["owner_fd"])
            return SimpleNamespace(done=False, cleanup_confirmed=True, viewer=None)
        self.console_patch = patch("botainer_dashboard.native_cli_runtime.LaunchConsole", side_effect=create_console)
        self.console_mock = self.console_patch.start()
        self.addCleanup(self.console_patch.stop)

    def close_fds(self):
        for fd in self.fds:
            os.close(fd)
        self.fds.clear()

    def start(self, request=REQUEST):
        return self.backend.start_session(self.backend.project_id, request)

    def result(self, **result):
        private_json(self.backend._receipt(REQUEST), {"request_id": REQUEST,
                     "config_revision": digest(self.backend.project / ".botainer/config.yaml"),
                     "plan_sha256": digest(self.backend.plan_path), **result})
        self.backend.consoles[REQUEST].done = True
        self.close_fds()

    def test_request_is_durable_before_cli_and_replay_does_not_dispatch_again(self):
        first = self.start()
        self.assertEqual(first["session"]["kind"], "launch")
        self.assertTrue(first["session"]["capabilities"]["attachTerminal"])
        self.assertFalse(first["session"]["capabilities"]["stopSession"])
        self.assertEqual(self.start(), first)
        self.assertEqual(self.console_mock.call_count, 1)
        with self.assertRaisesRegex(BackendUnavailable, "launch-unresolved"):
            self.start(REQUEST2)

    def test_decline_releases_new_request_but_original_request_never_retries(self):
        self.start()
        self.result(phase="not-dispatched", exit_code=0)
        replay = self.start()
        self.assertEqual(replay["session"]["launchState"], "declined")
        with OperationStore(self.backend.database) as store:
            self.assertEqual(store.get(REQUEST).state, "failed")
            self.assertFalse(store.unresolved())
        self.assertEqual(self.console_mock.call_count, 1)

    def test_lost_receipt_after_restart_stays_unknown_and_fences_new_request(self):
        self.start()
        self.backend.consoles.clear()
        self.close_fds()
        replay = self.start()
        self.assertEqual(replay["session"]["launchState"], "unknown")
        with self.assertRaisesRegex(BackendUnavailable, "launch-unresolved"):
            self.start(REQUEST2)
        self.assertEqual(self.console_mock.call_count, 1)

    def test_a_live_owner_blocks_even_before_new_intent(self):
        private_json(self.backend.registry_path, {"sessions": [{"session_id": "a" * 16}]})
        self.backend._session_record = lambda entry: {"ended_at": None}
        with self.assertRaisesRegex(BackendUnavailable, "project-already-active"):
            self.start()
        self.console_mock.assert_not_called()
        self.assertFalse(self.backend.launches_path.exists())

    def test_unregistered_launch_target_and_launch_stop_are_refused(self):
        with self.assertRaisesRegex(BackendUnavailable, "launch-unregistered"):
            self.backend.validate_launch(REQUEST)
        with self.assertRaisesRegex(BackendUnavailable, "context-unregistered"):
            self.backend.stop_session(self.backend.launch_namespace, REQUEST, REQUEST2)

    def test_result_bound_to_different_config_is_unknown_and_fences_new_launch(self):
        self.start()
        self.result(phase="not-dispatched", exit_code=0)
        receipt = json.loads(self.backend._receipt(REQUEST).read_text())
        receipt["config_revision"] = "0" * 64
        private_json(self.backend._receipt(REQUEST), receipt)
        self.assertEqual(self.start()["session"]["launchState"], "unknown")
        with self.assertRaisesRegex(BackendUnavailable, "launch-unresolved"):
            self.start(REQUEST2)

    def test_confirmed_cli_result_is_not_complete_until_exact_owner_is_verified(self):
        self.start()
        record = self.root / "state/state/project-fixture/sessions" / ("a" * 16)
        self.result(phase="completed", exit_code=0, result={"session_id": "a" * 16, "container_id": "b" * 64,
                    "runtime": "docker", "project_root": str(self.backend.project), "session_record": str(record)})
        self.backend._session_record = Mock(return_value={})
        self.backend.inspect_container = Mock(return_value={"State": {"Running": True, "StartedAt": "exact-start-identity"}})
        session = self.start()["session"]
        self.assertEqual(session["launchState"], "completed")
        self.assertEqual(session["resultTarget"], {"contextNamespace": "shell:fixture", "runtimeId": "a" * 16})
        self.assertEqual(self.backend._registry()["sessions"][0]["started_at"], "exact-start-identity")
        self.assertEqual(self.console_mock.call_count, 1)

    def test_failed_cli_cleanup_keeps_fence_even_with_no_dispatch_receipt(self):
        self.start()
        self.backend.consoles[REQUEST].done = True
        self.backend.consoles[REQUEST].cleanup_confirmed = False
        self.result(phase="not-dispatched", exit_code=0)
        # The fake closes its test fd above; actual LaunchConsole deliberately
        # retains the lock while its process-group cleanup is unconfirmed.
        self.assertEqual(self.start()["session"]["launchState"], "unknown")
        with self.assertRaisesRegex(BackendUnavailable, "launch-unresolved"):
            self.start(REQUEST2)

    def test_config_read_failure_releases_owner_lock_and_does_not_spawn(self):
        (self.backend.project / ".botainer/config.yaml").unlink()
        with self.assertRaises(BackendUnavailable):
            self.start()
        descriptor = self.backend._owner_lock()
        os.close(descriptor)
        self.console_mock.assert_not_called()




if __name__ == "__main__":
    unittest.main()
