"""Cluster controller invariants with private temporary state and fake transport.

No test invokes SSH, Slurm, or a container. Selected concurrency regressions use
real threads around deterministic transport fakes.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shlex
import tempfile
from threading import Event, Thread as RealThread
import time
import types
import unittest
from unittest.mock import Mock, patch

from botainer_dashboard import cluster_backend as backend_module
from botainer_dashboard.backend_errors import BackendUnavailable
from botainer_dashboard.cluster_backend import ClusterAttachment, ClusterBackend, session_state
from botainer_dashboard.cluster_attach import AttachmentStartError


REQUEST = "11111111-1111-4111-8111-111111111111"
OTHER_REQUEST = "22222222-2222-4222-8222-222222222222"
SESSION = "a" * 16


def profile():
    return {"version": 1, "id": "test-site", "label": "Example cluster",
        "preset": {"version": 1, "id": "slurm-trial", "label": "CPU trial",
            "scheduler": "slurm", "runtime": "apptainer", "attach_route": "slurm-screen",
            "partitions": [{"name": "short", "max_cpus": 2, "max_memory_mib": 2048,
                            "max_time_minutes": 10, "max_gpus": 0}]},
        "connection": {"ssh_alias": "cluster-example", "remote_python": "/opt/botainer/bin/python3",
            "source_root": "/work/example/botainer", "state_root": "/home/example/.botainer",
            "image": "/images/existing.sif", "trial_root": "/home/example/botainer-dashboard-test-workspace",
            "launcher": {"mode": "python_module", "path": "/opt/botainer/bin/python3"},
            "file_roots": {"home": "/home/example"}},
        "defaults": {"partition": "short", "cpus": 1, "memory_mib": 1024, "time_minutes": 5, "gpus": 0}}


def record(state="RUNNING", **extra):
    return {"request_id": REQUEST, "session_id": SESSION, "job_id": "12345", "stage": "submitted",
        "created_at": 1700000000, "observed_at": time.time(),
        "status": {"JobState": state, "Reason": "None", "BatchHost": "node-example", "TimeLimit": "00:05:00"}, **extra}


class ClusterBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        self.repo.chmod(0o700)
        self.profile_path = self.repo / "profile.json"
        self.raw_profile = profile()
        self.write_profile()
        (self.repo / "tools").mkdir()
        for name in ("cluster_trial.py", "cluster_workspace_helper.py", "prepare_cluster_trial.py",
                     "cluster_runtime_probe.py", "cluster_attach_supervisor.py"):
            (self.repo / "tools" / name).write_text("# offline test source\n")
        self.driver = types.SimpleNamespace(BOOTSTRAP="trusted bootstrap", SSH_OPTIONS=("BatchMode=yes",),
            child_environment=Mock(return_value={"PATH": "/usr/bin:/bin"}),
            write_json=Mock(side_effect=backend_module.write_private_json),
            bounded_exchange=Mock(side_effect=AssertionError("no transport is allowed in this test")))
        def write_new(path, data):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as handle: handle.write(data)
        self.driver.write_new = Mock(side_effect=write_new)
        for name, value in (("shell_payload", b"# offline payload\n"), ("source_module", self.driver)):
            guard = patch.object(backend_module, name, return_value=value)
            guard.start(); self.addCleanup(guard.stop)
        self.workers = []
        def thread(*, target, args=(), daemon=False):
            worker = types.SimpleNamespace(target=target, args=args, start=Mock())
            self.workers.append(worker)
            return worker
        guard = patch.object(backend_module.threading, "Thread", side_effect=thread)
        guard.start(); self.addCleanup(guard.stop)
        self.backend = ClusterBackend(self.repo, self.profile_path)
        self.backend._error = None
        self.backend._observed = time.time()
        self.backend._last_attempt = time.monotonic()
        self.backend._call = Mock(side_effect=AssertionError("unexpected remote action"))

    def write_profile(self):
        self.profile_path.write_text(json.dumps(self.raw_profile))
        self.profile_path.chmod(0o600)

    def install_record(self, value=None):
        value = value or record()
        self.backend._records[value["request_id"]] = copy.deepcopy(value)
        return value

    def test_start_journals_before_worker_and_same_request_never_dispatches_twice(self):
        result = self.backend.start_session(self.backend.project_id, REQUEST)
        self.assertEqual(result["session"]["state"], "starting")
        saved = json.loads(self.backend.state_path.read_text())
        self.assertEqual(saved["records"][REQUEST]["local_stage"], "starting")
        self.assertEqual(len(self.workers), 1)
        self.workers[0].start.assert_called_once_with()
        self.assertEqual(self.backend.start_session(self.backend.project_id, REQUEST), result)
        self.assertEqual(len(self.workers), 1)
        self.backend._call.assert_not_called()
        with self.assertRaises(BackendUnavailable):
            self.backend.start_session(self.backend.project_id, OTHER_REQUEST)

    def test_restart_reads_bounded_history_larger_than_authentication_file_limit(self):
        import uuid
        for index in range(60):
            key = str(uuid.uuid4())
            self.backend._records[key] = dict(record('CANCELLED'), request_id=key, job_id=str(12000+index))
        self.backend._save()
        self.assertGreater(self.backend.state_path.stat().st_size, 16384)
        restarted = ClusterBackend(self.repo, self.profile_path)
        self.assertEqual(set(restarted._records), set(self.backend._records))
        self.driver.bounded_exchange.assert_not_called()

    def test_refresh_receipts_reference_one_verified_artifact_without_rewriting_history(self):
        self.driver.bounded_exchange.side_effect = None
        self.driver.bounded_exchange.return_value = {'transport_status':'complete','returncode':0,
            'stdout':b'{"sessions":[]}', 'stderr':b''}
        for _ in range(2):
            ClusterBackend._call(self.backend, 'status')
            # The production transport returns a new mutable result each call.
            self.driver.bounded_exchange.return_value = {'transport_status':'complete','returncode':0,
                'stdout':b'{"sessions":[]}', 'stderr':b''}
        artifacts = list((self.backend.root/'artifacts').glob('*.py'))
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0].read_bytes(), self.backend.payload)
        receipts = list(self.backend.root.glob('request-*'))
        self.assertEqual(len(receipts), 2)
        for receipt in receipts:
            intent = json.loads((receipt/'intent.json').read_text())
            self.assertEqual(self.backend.root/intent['payload_artifact'], artifacts[0])
            self.assertFalse((receipt/'helper.py').exists())
        artifacts[0].write_bytes(b'changed')
        before = self.driver.bounded_exchange.call_count
        with self.assertRaisesRegex(BackendUnavailable, 'artifact-changed'):
            ClusterBackend._call(self.backend, 'status')
        self.assertEqual(self.driver.bounded_exchange.call_count, before)

    def test_shared_payload_rejects_linked_or_public_files_before_transport(self):
        path = self.backend._payload_artifact()
        path.chmod(0o644)
        with self.assertRaisesRegex(BackendUnavailable, 'artifact-invalid'):
            ClusterBackend._call(self.backend, 'status')
        path.chmod(0o600)
        linked = path.with_suffix('.link')
        os.link(path, linked)
        with self.assertRaisesRegex(BackendUnavailable, 'artifact-invalid'):
            ClusterBackend._call(self.backend, 'status')
        linked.unlink()
        path.unlink()
        path.symlink_to(self.profile_path)
        with self.assertRaises(OSError):
            ClusterBackend._call(self.backend, 'status')
        path.unlink()
        os.mkfifo(path,0o600)
        with self.assertRaisesRegex(BackendUnavailable, 'artifact-invalid'):
            ClusterBackend._call(self.backend, 'status')
        self.driver.bounded_exchange.assert_not_called()

    def test_ambiguous_start_remains_unknown_and_blocks_new_or_replayed_launch(self):
        self.backend.start_session(self.backend.project_id, REQUEST)
        self.backend._call.side_effect = BackendUnavailable("transport-lost-after-dispatch")
        self.backend._start_worker(REQUEST)
        self.backend._call.assert_called_once_with("start", request_id=REQUEST)
        replay = self.backend.start_session(self.backend.project_id, REQUEST)
        self.assertEqual(replay["session"]["state"], "unknown")
        self.assertFalse(replay["session"]["capabilities"]["attachTerminal"])
        with self.assertRaises(BackendUnavailable):
            self.backend.start_session(self.backend.project_id, OTHER_REQUEST)
        self.assertEqual(len(self.workers), 1)
        saved = json.loads(self.backend.state_path.read_text())
        self.assertIn("local_error", saved["records"][REQUEST])
        self.assertFalse(self.backend._working)

    def test_restart_preserves_unresolved_intent_without_creating_a_worker(self):
        self.backend.start_session(self.backend.project_id, REQUEST)
        count = len(self.workers)
        restarted = ClusterBackend(self.repo, self.profile_path)
        self.assertEqual(len(self.workers), count)
        self.assertEqual(restarted._view(restarted._records[REQUEST])["state"], "unknown")
        self.assertIn("interrupted", restarted._records[REQUEST]["local_error"])
        self.driver.bounded_exchange.assert_not_called()

    def test_project_namespace_and_submitted_session_identity_are_required(self):
        self.install_record()
        for action, args in (("start_session", ("other-project", REQUEST)),
                             ("list_files", ("other-project", "")),
                             ("read_file", ("other-project", "README.txt")),
                             ("read_config", ("other-project",)),
                             ("stop_session", ("other-namespace", REQUEST, OTHER_REQUEST)),
                             ("attach", ("other-namespace", REQUEST, 80, 24)),
                             ("stop_session", (self.backend.namespace, OTHER_REQUEST, OTHER_REQUEST))):
            with self.subTest(action=action, args=args), self.assertRaises(BackendUnavailable):
                getattr(self.backend, action)(*args)
        self.backend._records[REQUEST]["session_id"] = "--other-session"
        with self.assertRaises(BackendUnavailable):
            self.backend.stop_session(self.backend.namespace, REQUEST, OTHER_REQUEST)
        self.backend._call.assert_not_called()

    def test_profile_or_helper_changes_refuse_execution_on_old_controller(self):
        self.raw_profile["connection"]["ssh_alias"] = "second-cluster"
        self.write_profile()
        with self.assertRaisesRegex(BackendUnavailable, "profile-changed"):
            self.backend.start_session(self.backend.project_id, REQUEST)
        self.raw_profile = profile(); self.write_profile()
        (self.repo / "tools/cluster_runtime_probe.py").write_text("# changed after selection\n")
        with self.assertRaisesRegex(BackendUnavailable, "helper-changed"):
            self.backend.start_session(self.backend.project_id, REQUEST)
        self.backend._call.assert_not_called()
        self.assertFalse(self.workers)

    def test_extracted_transport_changes_refuse_execution_on_old_controller(self):
        transport_path = Path(backend_module.ssh_transport.__file__)
        self.assertIn(str(transport_path), self.backend._source_hashes)
        original_read = Path.read_bytes
        def changed(path):
            data = original_read(path)
            return data + b"# synthetic change\n" if path == transport_path else data
        with patch.object(Path, "read_bytes", changed), self.assertRaisesRegex(BackendUnavailable, "helper-changed"):
            self.backend.start_session(self.backend.project_id, REQUEST)
        self.backend._call.assert_not_called()
        self.assertFalse(self.workers)

    def test_ssh_command_keeps_json_data_literal_and_selected_profile_target(self):
        data = {"path": "folder'; echo untrusted; #"}
        argv = self.backend._argv("files", data)
        self.assertEqual(argv[0:2], ["/usr/bin/ssh", "-T"])
        self.assertEqual(argv[-2], self.raw_profile["connection"]["ssh_alias"])
        command = shlex.split(argv[-1])
        self.assertEqual(command[:5], [self.raw_profile["connection"]["remote_python"], "-I", "-B", "-c", "trusted bootstrap"])
        self.assertEqual(command[5:8], ["files", "--root", self.raw_profile["connection"]["trial_root"]])
        self.assertEqual(json.loads(command[-1]), data)

    def test_snapshot_poll_is_single_flight_and_cached_uncertainty_is_honest(self):
        self.install_record()
        self.backend._last_attempt = 0
        first = self.backend.snapshot()
        second = self.backend.snapshot()
        self.assertEqual(len(self.workers), 1)
        self.assertEqual(first["sessions"][0]["state"], "running")
        self.assertEqual(second["sessions"], first["sessions"])
        self.backend._error = "cluster-status-unavailable"
        stale = self.backend.snapshot()
        session = stale["sessions"][0]
        self.assertEqual(session["state"], "unknown")
        self.assertEqual(session["lastKnownState"], "RUNNING")
        self.assertIsNotNone(session["lastObservedAt"])
        self.assertEqual(session["unavailableReason"], "cluster-status-unavailable")
        self.assertFalse(session["capabilities"]["attachTerminal"])
        self.assertFalse(stale["capabilities"]["startSession"])
        self.assertEqual(stale["projects"][0]["unavailableReason"], "cluster-status-unavailable")
        self.backend._call.assert_not_called()

    def test_successful_refresh_recovers_exact_remote_request_without_launching(self):
        self.backend.start_session(self.backend.project_id, REQUEST)
        self.backend._records[REQUEST]["local_error"] = "outcome-unconfirmed"
        self.backend._call.side_effect = None
        self.backend._call.return_value = {"sessions": [record("PENDING")]}
        self.backend.refresh()
        self.assertEqual(self.backend._view(self.backend._records[REQUEST])["state"], "queued")
        self.backend._call.assert_called_once_with("status")
        self.assertNotIn("local_error", self.backend._records[REQUEST])

    def test_ssh_failure_hint_is_sanitized_and_sessions_stay_unknown_until_observed(self):
        self.install_record()
        original = copy.deepcopy(self.backend._records[REQUEST])
        stderr = b'private-account@private-host: Permission denied (publickey,keyboard-interactive).\n'
        self.driver.bounded_exchange.side_effect = None
        self.driver.bounded_exchange.return_value = {'transport_status': 'complete', 'returncode': 255,
            'remote_outcome': 'unknown', 'stdout': b'', 'stderr': stderr}
        self.backend._call.side_effect = lambda *args, **kwargs: ClusterBackend._call(self.backend, *args, **kwargs)
        self.backend.refresh()
        snapshot = self.backend.snapshot()
        diagnostic = snapshot['clusterSettings']['connectionDiagnostic']
        self.assertEqual(diagnostic['code'], 'ssh-authentication-required')
        self.assertEqual(snapshot['connectionStatus'], 'unavailable')
        self.assertEqual(snapshot['sessions'][0]['state'], 'unknown')
        self.assertEqual(snapshot['sessions'][0]['lastKnownState'], 'RUNNING')
        self.assertFalse(snapshot['capabilities']['startSession'])
        self.assertFalse(snapshot['sessions'][0]['capabilities']['attachTerminal'])
        self.assertEqual(self.backend._records[REQUEST], original)
        self.assertNotIn('private-account', json.dumps(snapshot))
        self.assertNotIn('private-host', json.dumps(snapshot))
        self.driver.bounded_exchange.assert_called_once()
        receipt = next(self.backend.root.glob('request-*'))
        self.assertEqual((receipt / 'stderr.bin').read_bytes(), stderr)
        self.assertEqual((receipt / 'stderr.bin').stat().st_mode & 0o777, 0o600)
        # Recovery is an observation of the same request, never a launch retry.
        self.driver.bounded_exchange.return_value = {'transport_status': 'complete', 'returncode': 0,
            'stdout': json.dumps({'sessions': [original]}).encode(), 'stderr': b''}
        self.backend.refresh()
        restored = self.backend.snapshot()
        self.assertIsNone(restored['clusterSettings']['connectionDiagnostic'])
        self.assertEqual(restored['connectionStatus'], 'available')
        self.assertEqual(restored['sessions'][0]['runtimeId'], REQUEST)
        self.assertEqual(restored['sessions'][0]['state'], 'running')
        self.assertEqual([call.args[0] for call in self.backend._call.call_args_list], ['status', 'status'])

    def test_nontransport_check_failure_replaces_stale_authentication_hint(self):
        from botainer_dashboard.ssh_diagnostics import SshRequestUnavailable
        self.install_record()
        self.backend._call.side_effect = SshRequestUnavailable('cluster-status-unavailable', 'ssh-authentication-required')
        self.backend.refresh()
        self.backend._call.side_effect = BackendUnavailable('cluster-profile-changed-restart-required')
        self.backend.refresh()
        snapshot = self.backend.snapshot()
        self.assertEqual(snapshot['clusterSettings']['connectionDiagnostic']['code'], 'remote-check-failed')
        self.assertEqual(snapshot['sessions'][0]['state'], 'unknown')
        self.assertFalse(snapshot['capabilities']['startSession'])

    def test_missing_remote_record_is_retained_as_unknown_without_enabling_launch(self):
        self.install_record()
        self.backend._call.side_effect = None
        self.backend._call.return_value = {"sessions": []}
        self.backend.refresh()
        snapshot = self.backend.snapshot()
        self.assertEqual(len(snapshot["sessions"]), 1)
        self.assertEqual(snapshot["sessions"][0]["runtimeId"], REQUEST)
        self.assertEqual(snapshot["sessions"][0]["state"], "unknown")
        self.assertFalse(snapshot["capabilities"]["startSession"])

    def test_malformed_or_duplicate_inventory_retains_previous_unknown_state(self):
        for records in ([None], [record(), record()], [dict(record(), status=None)],
                        [dict(record(), session_id=1234567890123456)],
                        [dict(record(), request_id="not-a-uuid")], [record()] * 201):
            with self.subTest(records=records[:2]):
                self.backend._records.clear(); self.install_record()
                self.backend._error = None
                self.backend._call.side_effect = None
                self.backend._call.return_value = {"sessions": records}
                self.backend.refresh()
                self.assertEqual(self.backend._view(self.backend._records[REQUEST])["state"], "unknown")
                self.assertFalse(self.backend._polling)

    def test_malformed_start_reply_preserves_original_intent_and_never_adopts_another_request(self):
        for reply in ({}, dict(record(), request_id=OTHER_REQUEST), dict(record(), status=[])):
            with self.subTest(reply=reply):
                self.backend._records.clear()
                self.backend._error = None
                self.backend._working = False
                self.backend._last_attempt = time.monotonic()
                self.backend.start_session(self.backend.project_id, REQUEST)
                self.backend._call.side_effect = None
                self.backend._call.return_value = reply
                self.backend._start_worker(REQUEST)
                self.assertEqual(set(self.backend._records), {REQUEST})
                view = self.backend._view(self.backend._records[REQUEST])
                self.assertEqual(view["runtimeId"], REQUEST)
                self.assertEqual(view["state"], "unknown")
                self.assertFalse(view["capabilities"]["attachTerminal"])

    def test_stop_targets_original_session_and_does_not_claim_termination_early(self):
        self.install_record()
        self.backend._call.side_effect = None
        self.backend._call.return_value = record("RUNNING")
        result = self.backend.stop_session(self.backend.namespace, REQUEST, OTHER_REQUEST)
        self.backend._call.assert_called_once_with("stop", session_id=SESSION)
        self.assertFalse(result["terminationConfirmed"])
        self.assertEqual(result["session"]["state"], "running")
        self.backend._call.return_value = {"sessions": [record("CANCELLED")]}
        self.backend.refresh()
        self.assertEqual(self.backend._view(self.backend._records[REQUEST])["state"], "stopped")

    def test_stop_scope_publishes_exact_job_without_enabling_missing_or_unavailable_targets(self):
        for state in ('RUNNING', 'PENDING'):
            with self.subTest(state=state):
                self.install_record(record(state))
                row = self.backend.snapshot()['sessions'][0]
                self.assertEqual(row['stopScope'], 'allocation')
                self.assertEqual(row['jobId'], '12345')
                self.assertTrue(row['capabilities']['stopSession'])
        self.backend._error = 'ssh-authentication-required'
        row = self.backend.snapshot()['sessions'][0]
        self.assertEqual(row['stopScope'], 'allocation')
        self.assertEqual(row['jobId'], '12345')
        self.assertFalse(row['capabilities']['stopSession'])
        self.backend._error = None
        self.install_record(record(job_id=None))
        row = self.backend.snapshot()['sessions'][0]
        self.assertEqual(row['stopScope'], 'allocation')
        self.assertIsNone(row['jobId'])
        self.assertFalse(row['capabilities']['stopSession'])

    def test_failed_attachment_constructor_fences_target_before_service_can_retry(self):
        self.install_record()
        with patch("botainer_dashboard.cluster_attach.FramedSshAttachment", side_effect=OSError("cleanup uncertain")) as create:
            with self.assertRaises(BackendUnavailable):
                self.backend.attach(self.backend.namespace, REQUEST, 80, 24)
            with self.assertRaises(BackendUnavailable):
                self.backend.attach(self.backend.namespace, REQUEST, 80, 24)
            self.assertEqual(create.call_count, 1)
        view = self.backend._view(self.backend._records[REQUEST])
        self.assertFalse(view["capabilities"]["attachTerminal"])
        self.assertIn("unconfirmed", view["unavailableReason"])
        receipts = list(self.backend.root.glob("attachment-*"))
        self.assertEqual(len(receipts), 1)
        intent = json.loads((receipts[0] / "intent.json").read_text())
        self.assertEqual((intent["request_id"], intent["cols"], intent["rows"]), (REQUEST, 80, 24))
        result = json.loads((receipts[0] / "result.json").read_text())
        self.assertEqual(result, {"ready": False, "error": "cleanup uncertain", "cleanup": "unconfirmed"})
        self.backend._call.assert_not_called()

    def test_constructor_diagnostics_are_private_bounded_receipts_and_browser_error_stays_fixed(self):
        self.install_record()
        diagnostics = {"stderr": b"private dispatcher path\0lock unavailable\n", "stderr_truncated": False,
            "passenger_returncode": 2, "passenger_finished": True, "remote_cleanup_receipt": False}
        failure = AttachmentStartError("remote attachment ended without confirmed cleanup", diagnostics)
        with patch("botainer_dashboard.cluster_attach.FramedSshAttachment", side_effect=failure) as create:
            with self.assertRaises(BackendUnavailable) as refused:
                self.backend.attach(self.backend.namespace, REQUEST, 80, 24)
            self.assertEqual(refused.exception.code, "cluster-attachment-unconfirmed")
            self.assertNotIn("private dispatcher", str(refused.exception))
            with self.assertRaises(BackendUnavailable):
                self.backend.attach(self.backend.namespace, REQUEST, 80, 24)
            self.assertEqual(create.call_count, 1)
        receipt = next(self.backend.root.glob("attachment-*"))
        self.assertEqual((receipt / "stderr.bin").read_bytes(), diagnostics["stderr"])
        self.assertEqual((receipt / "stderr.bin").stat().st_mode & 0o777, 0o600)
        result = json.loads((receipt / "result.json").read_text())
        self.assertEqual(result["cleanup"], "unconfirmed")
        self.assertNotIn("stderr", result["transport"])
        self.assertEqual(result["transport"]["passenger_returncode"], 2)
        self.assertIn(REQUEST, self.backend._fences)

    def test_status_and_attach_preparation_serialize_but_file_reads_and_live_terminal_do_not(self):
        self.install_record()
        entered, release, attaching = Event(), Event(), Event()
        calls, errors, attachments = [], [], []
        def exchange(argv, *_args, **_kwargs):
            action = shlex.split(argv[-1])[5]
            calls.append(action)
            if action == "status":
                entered.set()
                if not release.wait(2):
                    raise AssertionError("status fixture not released")
            return {"transport_status": "complete", "returncode": 0,
                    "stdout": b'{"sessions":[]}', "stderr": b''}
        self.driver.bounded_exchange.side_effect = exchange
        client = Mock(owner={"pinned": "owner"}, executable_verification="exact")
        def construct(*_args, **_kwargs):
            attaching.set()
            return client
        def status():
            try: ClusterBackend._call(self.backend, "status")
            except Exception as error: errors.append(error)
        def attach():
            try: attachments.append(self.backend.attach(self.backend.namespace, REQUEST, 80, 24))
            except Exception as error: errors.append(error)
        first, second = RealThread(target=status), RealThread(target=attach)
        with patch("botainer_dashboard.cluster_attach.FramedSshAttachment", side_effect=construct):
            first.start()
            try:
                self.assertTrue(entered.wait(1))
                second.start()
                self.assertFalse(attaching.wait(.05))
                ClusterBackend._call(self.backend, "files", path="")
                self.assertEqual(calls, ["status", "files"])
            finally:
                release.set()
                first.join(2)
                if second.ident is not None: second.join(2)
            self.assertFalse(first.is_alive() or second.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(len(attachments), 1)
            # Ready returned, but the viewer is still open. Stop must dispatch
            # now rather than waiting for its eventual detach.
            ClusterBackend._call(self.backend, "status")
            ClusterBackend._call(self.backend, "stop", session_id=SESSION)
            self.assertEqual(calls[-2:], ["status", "stop"])
            client.close.assert_not_called()
            attachments[0].close()

    def test_preparation_lock_busy_refuses_before_dispatch_without_fencing_an_unstarted_attach(self):
        self.install_record()
        with self.backend._control_guard(), patch.object(backend_module, "CONTROL_WAIT_SECONDS", .01), \
                patch("botainer_dashboard.cluster_attach.FramedSshAttachment") as create:
            for action in ("status", "start", "stop"):
                with self.subTest(action=action), self.assertRaisesRegex(BackendUnavailable, "preparation-busy"):
                    ClusterBackend._call(self.backend, action)
            with self.assertRaisesRegex(BackendUnavailable, "preparation-busy"):
                self.backend.attach(self.backend.namespace, REQUEST, 80, 24)
            create.assert_not_called()
            self.driver.bounded_exchange.assert_not_called()
        self.assertEqual(self.backend._fences, {})
        self.assertEqual(list(self.backend.root.glob("attachment-*")), [])

    def test_ready_receipt_failure_cleans_attacher_and_fences_if_cleanup_is_uncertain(self):
        self.install_record()
        client = Mock(owner={"synthetic": "owner"}, executable_verification="exact")
        client.close.side_effect = OSError("cleanup uncertain")
        def receipt(path, value):
            if path.name == "result.json": raise OSError("receipt disk unavailable")
            backend_module.write_private_json(path, value)
        self.driver.write_json.side_effect = receipt
        with patch("botainer_dashboard.cluster_attach.FramedSshAttachment", return_value=client) as create:
            with self.assertRaises((OSError, BackendUnavailable)):
                self.backend.attach(self.backend.namespace, REQUEST, 80, 24)
            client.close.assert_called_once_with()
            self.assertIn(REQUEST, self.backend._fences)
            with self.assertRaises(BackendUnavailable):
                self.backend.attach(self.backend.namespace, REQUEST, 80, 24)
            self.assertEqual(create.call_count, 1)

    def test_failed_close_retains_writer_fence_and_never_stops_runtime(self):
        self.install_record()
        client = Mock(); client.close.side_effect = OSError("no detach receipt")
        attachment = ClusterAttachment(client, self.backend, REQUEST)
        with self.assertRaises(OSError): attachment.close()
        self.assertIn("detach-unconfirmed", self.backend._fences[REQUEST])
        self.backend._call.assert_not_called()
        client.close.assert_called_once_with()

    def test_confirmed_job_end_supersedes_live_detach_warning_without_reopening_attach(self):
        self.install_record()
        self.backend._fences[REQUEST] = 'cluster-remote-detach-unconfirmed'
        self.assertIn('unconfirmed', self.backend._view(self.backend._records[REQUEST])['unavailableReason'])
        self.backend._call.side_effect = None
        self.backend._call.return_value = {'sessions': [record('CANCELLED')]}
        self.backend.refresh()
        view = self.backend._view(self.backend._records[REQUEST])
        self.assertEqual(view['state'], 'stopped')
        self.assertIsNone(view['unavailableReason'])
        self.assertFalse(view['capabilities']['attachTerminal'])
        self.assertIn(REQUEST, self.backend._fences)
        with self.assertRaises(BackendUnavailable):
            self.backend.attach(self.backend.namespace, REQUEST, 80, 24)

    def test_files_and_config_use_frontend_document_contracts(self):
        helper_path = Path(__file__).resolve().parents[2] / "tools/cluster_workspace_helper.py"
        spec = importlib.util.spec_from_file_location("cluster_workspace_contract", helper_path)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        workspace = self.repo / "disposable-workspace"
        (workspace / "project/folder").mkdir(parents=True)
        (workspace / "project/README.txt").write_text("test")
        (workspace / "project/.botainer").mkdir()
        config = workspace / "project/.botainer/config.yaml"
        config.write_text("agent: ''\n")
        self.backend._call.side_effect = None
        self.backend._call.return_value = helper.files(workspace, "")
        result = self.backend.list_files(self.backend.project_id, "")
        self.assertEqual({entry["name"]: entry["kind"] for entry in result["entries"]},
                         {"folder": "directory", "README.txt": "file"})
        self.backend._call.assert_called_once_with("files", path="")
        self.backend._call.return_value = helper.read_config(workspace)
        document = self.backend.read_config(self.backend.project_id)
        self.assertIs(document["writable"], False)
        self.assertEqual(document["text"], config.read_text())
        self.assertRegex(document["revision"], r"^[0-9a-f]{64}$")
        self.assertEqual(document["path"], str(config))
        self.assertFalse(self.backend.snapshot()["capabilities"]["configWrite"])


class SessionStateTests(unittest.TestCase):
    def test_scheduler_states_do_not_infer_readiness_or_end_from_silence(self):
        for state, expected in (("PENDING", "queued"), ("CONFIGURING", "queued"),
                                ("RUNNING", "running"), ("COMPLETING", "unknown"),
                                ("NEW_STATE", "unknown"), ("CANCELLED", "stopped"),
                                ("TIMEOUT", "stopped"), ("FAILED", "failed")):
            with self.subTest(state=state):
                self.assertEqual(session_state(record(state)), expected)
                self.assertEqual(session_state(record(state), stale=True), "unknown")
        self.assertEqual(session_state({"local_stage": "starting"}), "starting")
        self.assertEqual(session_state(record(observation_error="unavailable")), "unknown")
        self.assertEqual(session_state(record(local_error="unconfirmed")), "unknown")


if __name__ == "__main__":
    unittest.main()
