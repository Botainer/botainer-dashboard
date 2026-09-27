"""Offline grant/dispatch checks; no container/source preparation or runtime use."""
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from botainer_dashboard.backend_errors import BackendUnavailable
from botainer_dashboard.operations import OperationStore
from botainer_dashboard.proof_backend import ProofBackend, decode_launch_output, digest, private_json, validate_spec, expected_docker_mounts


def launch_protocol(project, project_uuid, image, record_dir, *, mounts=None):
    summary = {"session_id": "a" * 16, "project_root": str(project), "project_uuid": project_uuid,
               "image": image, "runtime": "docker", "network_mode": "none", "network_endpoints": [],
               "mounts": mounts or [], "auth_profile": "default", "plugins_enabled": ["agent-dashboard-test"],
               "entrypoint_wraps": [["/bin/sh", "/mnt/dashboard-fixture/terminal.sh"]], "nudge_enabled": False,
               "port_forwards": [], "auth_mode": "none", "git_unprotected": False, "env_var_names": [],
               "env_files": [], "host_hooks": [], "capabilities": []}
    launch = {"session_id": "a" * 16, "container_id": "b" * 64, "runtime": "docker", "nudge_supported": False,
              "project_root": str(project), "session_record": str(record_dir)}
    return summary, launch


class LaunchOutputTests(unittest.TestCase):
    def setUp(self):
        self.summary, self.launch = launch_protocol("/tmp/disposable", "uuid", "sha256:" + "c" * 64, "/tmp/state/sessions/" + "a" * 16)
        self.text = json.dumps(self.summary) + "\n" + json.dumps(self.launch) + "\n"

    def test_fixed_two_json_line_protocol_returns_summary_and_unique_launch(self):
        self.assertEqual(decode_launch_output(self.text.encode()), (self.summary, self.launch))

    def test_summary_only_garbage_duplicate_or_reordered_results_are_refused(self):
        for text in (json.dumps(self.summary), json.dumps(self.launch), self.text + json.dumps(self.launch),
                     "banner\n" + self.text, self.text + "garbage", "\n" + self.text,
                     json.dumps(self.launch) + "\n" + json.dumps(self.summary),
                     self.text.replace('"container_id":', '"container_id":"' + "c" * 64 + '","container_id":')):
            with self.subTest(text=text), self.assertRaises(BackendUnavailable): decode_launch_output(text)

    def test_mismatched_identity_or_unknown_fields_are_refused(self):
        for replacement in ({"session_id": "c" * 16}, {"runtime": "apptainer"},
                            {"project_root": "/tmp/other"}, {"unexpected": True}, {"nudge_supported": 0}):
            text = json.dumps(self.summary) + "\n" + json.dumps(dict(self.launch, **replacement))
            with self.subTest(replacement=replacement), self.assertRaises(BackendUnavailable): decode_launch_output(text)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        import threading
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name).resolve()
        self.backend = ProofBackend.__new__(ProofBackend)
        self.backend.root = self.root; self.backend.project = self.root / "project"
        self.backend.project.mkdir()
        self.backend.project_uuid = "uuid"; self.backend.project_id = "proof-project"
        self.backend.namespace = "proof:namespace"; self.backend.receipt = {"synthetic": "immutable"}
        self.backend.database = self.root / "operations.sqlite"; self.backend.registry_path = self.root / "sessions.json"
        self.backend.plan = {"state_root": str(self.root / "state"), "docker": {"image": "sha256:" + "c" * 64}}
        self.backend._thread_lock = threading.RLock()
        self.backend.verify_files = Mock(); self.backend.verify_daemon = Mock()
        self.backend._run = Mock(side_effect=AssertionError("recovery must not dispatch"))
        self.request = "11111111-1111-4111-8111-111111111111"
        self.started = "2026-09-20T12:00:00.123456789Z"
        self.record_dir = self.root / "state/state/uuid/sessions" / ("a" * 16)
        self.record_dir.mkdir(parents=True)
        self.summary, self.launch = launch_protocol(self.backend.project, "uuid", self.backend.plan["docker"]["image"], self.record_dir)
        self.launch_path = self.root / f"launch-{self.request}.json"
        self._write_receipt()
        private_json(self.record_dir / "spec.json", {"session_id": "a" * 16, "project_uuid": "uuid",
                     "project_root": str(self.backend.project), "runtime_handle": {"docker": {"container_id": "b" * 64}},
                     "ended_at": None, "spec": {"mount_plan": {"binds": []}, "env": {"values": {}}}})
        # Grant validation is covered independently above; recovery tests exercise
        # actual journal/receipt/registry ordering with controlled runtime reads.
        self.backend._session_record = lambda entry: json.loads((Path(entry["record_dir"]) / "spec.json").read_text())
        self.backend.inspect_container = Mock(return_value={"State": {"Running": True, "StartedAt": self.started}})
        fingerprint = "sha256:" + hashlib.sha256(json.dumps(self.backend.receipt, sort_keys=True).encode()).hexdigest()
        with OperationStore(self.backend.database) as store:
            store.prepare(operation_id=self.request, scope_key="local-disposable-proof", checkout=str(self.backend.project),
                          context_id=self.backend.namespace, config_revision="v1", intent_fingerprint=fingerprint)
            store.claim(self.request, expected_fingerprint=fingerprint)
            store.record_unknown(self.request, result_code="runtime-outcome-unconfirmed")

    def tearDown(self): self.temp.cleanup()

    def _write_receipt(self, code=0):
        private_json(self.launch_path, {"exit_code": code, "stdout": json.dumps(self.summary) + "\n" + json.dumps(self.launch) + "\n", "stderr": ""})

    def _operation_state(self):
        with OperationStore(self.backend.database) as store: return store.get(self.request).state

    def test_explicit_recovery_adopts_exact_observed_handle_without_redispatch(self):
        result = self.backend.recover_launch(self.request, expected_started_at=self.started)
        self.assertEqual(result["session"]["runtimeId"], "a" * 16)
        self.assertEqual(self._operation_state(), "succeeded")
        entry = json.loads(self.backend.registry_path.read_text())["sessions"][0]
        self.assertEqual((entry["container_id"], entry["started_at"]), ("b" * 64, self.started))
        self.backend._run.assert_not_called()
        with self.assertRaisesRegex(BackendUnavailable, "operation-mismatch"):
            self.backend.recover_launch(self.request, expected_started_at=self.started)

    def test_failed_runtime_recovery_retains_cleanup_handle_and_unknown_operation(self):
        self.backend.inspect_container.side_effect = BackendUnavailable("proof-original-container-changed")
        with self.assertRaises(BackendUnavailable): self.backend.recover_launch(self.request, expected_started_at=self.started)
        entry = json.loads(self.backend.registry_path.read_text())["sessions"][0]
        self.assertEqual((entry["session_id"], entry["container_id"], entry["started_at"]), ("a" * 16, "b" * 64, self.started))
        self.assertEqual(self._operation_state(), "unknown"); self.backend._run.assert_not_called()

    def test_changed_independently_observed_start_time_is_not_adopted(self):
        self.backend.inspect_container.return_value = {"State": {"Running": True, "StartedAt": "2026-09-20T13:00:00Z"}}
        with self.assertRaisesRegex(BackendUnavailable, "runtime-unconfirmed"):
            self.backend.recover_launch(self.request, expected_started_at=self.started)
        self.assertEqual(self._operation_state(), "unknown")
        self.assertTrue(self.backend.registry_path.exists()); self.backend._run.assert_not_called()

    def test_nonzero_exit_or_changed_intent_is_not_adopted(self):
        self._write_receipt(code=1)
        with self.assertRaisesRegex(BackendUnavailable, "receipt-invalid"):
            self.backend.recover_launch(self.request, expected_started_at=self.started)
        self.assertFalse(self.backend.registry_path.exists())
        self._write_receipt(); self.backend.receipt = {"changed": True}
        with self.assertRaisesRegex(BackendUnavailable, "operation-mismatch"):
            self.backend.recover_launch(self.request, expected_started_at=self.started)
        self.assertEqual(self._operation_state(), "unknown"); self.backend._run.assert_not_called()

    def test_duplicate_record_or_changed_summary_is_not_adopted(self):
        duplicate = self.record_dir.parent / ("d" * 16) / "spec.json"
        duplicate.parent.mkdir()
        private_json(duplicate, {"session_id": "d" * 16, "runtime_handle": {"docker": {"container_id": "b" * 64}}})
        with self.assertRaisesRegex(BackendUnavailable, "record-ambiguous"):
            self.backend.recover_launch(self.request, expected_started_at=self.started)
        duplicate.unlink()
        self.summary["image"] = "sha256:" + "d" * 64; self._write_receipt()
        with self.assertRaisesRegex(BackendUnavailable, "summary-or-record-changed"):
            self.backend.recover_launch(self.request, expected_started_at=self.started)
        self.assertEqual(self._operation_state(), "unknown")
        self.assertTrue(self.backend.registry_path.exists()); self.backend._run.assert_not_called()

    def test_recovery_requires_explicit_canonical_request_and_observed_time(self):
        for request, started in (("../launch", self.started), (self.request, ""), (self.request, None)):
            with self.subTest(request=request, started=started), self.assertRaises(BackendUnavailable):
                self.backend.recover_launch(request, expected_started_at=started)
        self.assertFalse(self.backend.registry_path.exists()); self.backend._run.assert_not_called()


class GrantTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.plan = {"project": str(self.root / "project"), "state_root": str(self.root / "state"),
                     "fixture_readonly_source": str(self.root / "fixture"), "docker": {"image": "sha256:" + "a" * 64}}
        self.spec = {"project_root": self.plan["project"], "project_uuid": "uuid", "runtime": "docker",
                     "session_id": "a" * 16,
                     "state_dir": str(self.root / "state/state/uuid"),
                     "image": self.plan["docker"]["image"], "network": {"mode": "none"},
                     "plugins_enabled": ["agent-dashboard-test"], "entrypoint_wraps": [["/bin/sh", "/mnt/dashboard-fixture/terminal.sh"]],
                     "mount_plan": {"binds": [{"source": self.plan["project"], "target": "/workspace", "mode": "rw"},
                         {"source": self.plan["fixture_readonly_source"], "target": "/mnt/dashboard-fixture", "mode": "ro"}]}}
        project_state = self.root / "state/state/uuid"
        for target, relative, mode in [
            ("/packages", "packages", "rw"), ("/scratch", "scratch", "rw"), ("/home/user", "home", "rw"),
            ("/workspace/.botainer", "data/null-bind-anchor", "null-bind"),
            ("/workspace/.botainer/AGENT_ACCESS.txt", "sessions/" + "a" * 16 + "/AGENT_ACCESS.txt", "ro"),
            ("/workspace/.botainer/AGENT_HINTS.md", "sessions/" + "a" * 16 + "/AGENT_HINTS.md", "ro"),
        ]:
            self.spec["mount_plan"]["binds"].append({"source": str(project_state / relative), "target": target, "mode": mode})

    def tearDown(self): self.temp.cleanup()

    def test_exact_fixture_scope_accepts_but_extra_runtime_access_is_refused(self):
        validate_spec(self.spec, self.plan, "uuid")
        changes = [("network", {"mode": "internet"}), ("image", "alpine:latest"),
                   ("plugins_enabled", ["agent-dashboard-test", "browser"]), ("hooks", [{"when": "pre_session"}]),
                   ("sidecars", [{}]), ("port_forwards", [{}]), ("env_files", ["/tmp/creds"]),
                   ("entrypoint_wraps", [["/bin/sh", "-c", "arbitrary"]])]
        for key, value in changes:
            with self.subTest(key=key), self.assertRaises(BackendUnavailable):
                validate_spec(dict(self.spec, **{key: value}), self.plan, "uuid")

    def test_external_or_writable_fixture_mount_and_duplicate_target_refused(self):
        for bind in [{"source": "/home/example", "target": "/home/user", "mode": "rw"},
                     {"source": self.plan["fixture_readonly_source"], "target": "/mnt/dashboard-fixture", "mode": "rw"},
                     {"source": self.plan["project"], "target": "/workspace", "mode": "rw"}]:
            spec = copy.deepcopy(self.spec); spec["mount_plan"]["binds"].append(bind)
            with self.assertRaises(BackendUnavailable): validate_spec(spec, self.plan, "uuid")

    def test_credentials_and_changed_project_identity_refused(self):
        spec = dict(self.spec, env={"values": {"OPENAI_API_KEY": "synthetic"}})
        with self.assertRaises(BackendUnavailable): validate_spec(spec, self.plan, "uuid")
        with self.assertRaises(BackendUnavailable): validate_spec(self.spec, self.plan, "other-uuid")

    def test_generated_null_anchor_maps_to_rw_but_exact_session_hints_stay_ro(self):
        mounts = expected_docker_mounts(self.spec, self.plan, "uuid")
        self.assertIn((str(self.root / "state/state/uuid/data/null-bind-anchor"), "/workspace/.botainer", True), mounts)
        for name in ("AGENT_ACCESS.txt", "AGENT_HINTS.md"):
            self.assertIn((str(self.root / "state/state/uuid/sessions" / ("a" * 16) / name), "/workspace/.botainer/" + name, False), mounts)

    def test_container_inspection_accepts_actual_null_bind_rw_and_refuses_changed_access(self):
        backend = ProofBackend.__new__(ProofBackend); backend.plan = self.plan; backend.project_uuid = "uuid"
        backend._session_record = lambda entry: {"spec": self.spec}
        entry = {"session_id": "a" * 16, "container_id": "b" * 64, "started_at": "original-start"}
        inspected = {"Id": entry["container_id"], "Image": self.plan["docker"]["image"],
                     "HostConfig": {"NetworkMode": "none", "PortBindings": {}},
                     "Config": {"OpenStdin": True, "Tty": True, "Entrypoint": ["/bin/sh"], "Cmd": ["/mnt/dashboard-fixture/terminal.sh"]},
                     "State": {"StartedAt": "original-start"},
                     "Mounts": [{"Type": "bind", "Source": bind["source"], "Destination": bind["target"], "RW": bind["mode"] != "ro"}
                                for bind in self.spec["mount_plan"]["binds"]]}
        backend._docker = lambda *args, **kwargs: (0, json.dumps([inspected]).encode(), b"")
        self.assertEqual(backend.inspect_container(entry), inspected)
        next(mount for mount in inspected["Mounts"] if mount["Destination"] == "/workspace/.botainer/AGENT_ACCESS.txt")["RW"] = True
        with self.assertRaisesRegex(BackendUnavailable, "mounts-changed"): backend.inspect_container(entry)

    def test_null_anchor_and_hint_binds_refuse_other_private_state_paths_and_modes(self):
        for target in ("/workspace/.botainer", "/workspace/.botainer/AGENT_ACCESS.txt", "/workspace/.botainer/AGENT_HINTS.md"):
            for replacement in ({"source": str(self.root / "state/other-private-file")}, {"mode": "rw"}):
                with self.subTest(target=target, replacement=replacement):
                    changed = copy.deepcopy(self.spec)
                    bind = next(bind for bind in changed["mount_plan"]["binds"] if bind["target"] == target)
                    bind.update(replacement)
                    with self.assertRaises(BackendUnavailable): expected_docker_mounts(changed, self.plan, "uuid")
        changed = copy.deepcopy(self.spec)
        bind = next(bind for bind in changed["mount_plan"]["binds"] if bind["target"].endswith("AGENT_ACCESS.txt"))
        bind["source"] = bind["source"].replace("a" * 16, "b" * 16)
        with self.assertRaises(BackendUnavailable): expected_docker_mounts(changed, self.plan, "uuid")

    def test_source_artifact_hash_refuses_symlink_and_atomic_receipt_roundtrip(self):
        path = self.root / "record.json"; private_json(path, {"a": 1})
        self.assertEqual(json.loads(path.read_text()), {"a": 1})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(digest(path), hashlib.sha256(path.read_bytes()).hexdigest())
        link = self.root / "alias"; link.symlink_to(path)
        with self.assertRaises(BackendUnavailable): digest(link)


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name).resolve()
        self.backend = ProofBackend.__new__(ProofBackend)
        self.backend.root = self.root; self.backend.project = self.root
        self.backend.project_id = "proof-project"; self.backend.namespace = "proof:namespace"
        self.backend.receipt = {"synthetic": "immutable"}; self.backend.database = self.root / "operations.sqlite"
        self.backend.registry_path = self.root / "sessions.json"
        self.backend.plan = {"state_root": str(self.root / "state")}; self.backend.project_uuid = "uuid"
        import threading
        self.backend._thread_lock = threading.RLock()
        self.backend.verify_files = lambda: None; self.backend.verify_daemon = lambda: None
        self.backend.preflight = lambda: {"synthetic": "not executed"}
        self.backend.argv = lambda *args: list(args)

    def tearDown(self): self.temp.cleanup()

    def test_ambiguous_launch_is_journaled_and_never_automatically_redispatched(self):
        with patch.object(self.backend, "_run", side_effect=BackendUnavailable("proof-command-timeout")) as run:
            with self.assertRaises(BackendUnavailable): self.backend.start_session("proof-project", "request-1")
            with OperationStore(self.backend.database) as store:
                self.assertEqual(store.get("request-1").state, "unknown")
            with self.assertRaises(BackendUnavailable): self.backend.start_session("proof-project", "request-1")
            with self.assertRaises(BackendUnavailable): self.backend.start_session("proof-project", "request-2")
            self.assertEqual(run.call_count, 1)

    def test_wrong_project_or_context_never_reaches_runtime(self):
        with patch.object(self.backend, "_run") as run:
            with self.assertRaises(BackendUnavailable): self.backend.start_session("some-real-project", "r1")
            with self.assertRaises(BackendUnavailable): self.backend.stop_session("different-machine", "a" * 16, "r2")
            with self.assertRaises(BackendUnavailable): self.backend.attach("different-machine", "a" * 16, 80, 24)
            run.assert_not_called()

    def test_existing_unended_record_blocks_new_start_even_if_runtime_observation_missing(self):
        private_json(self.backend.registry_path, {"sessions": [{"session_id": "a" * 16}]})
        self.backend._session_record = lambda entry: {"ended_at": None}
        with patch.object(self.backend, "_run") as run:
            with self.assertRaises(BackendUnavailable): self.backend.start_session("proof-project", "request-1")
            run.assert_not_called()

    def test_completed_request_returns_original_live_session_without_new_dispatch(self):
        sid = "a" * 16
        private_json(self.backend.registry_path, {"sessions": [{"session_id": sid}]})
        self.backend.inspect_container = lambda entry, **kw: {"State": {"Running": True}}
        fingerprint = "sha256:" + hashlib.sha256(json.dumps(self.backend.receipt, sort_keys=True).encode()).hexdigest()
        with OperationStore(self.backend.database) as store:
            store.prepare(operation_id="request-1", scope_key="local-disposable-proof", checkout=str(self.root),
                          context_id=self.backend.namespace, config_revision="v1", intent_fingerprint=fingerprint)
            store.claim("request-1", expected_fingerprint=fingerprint)
            store.record_success("request-1", session_id=sid)
        with patch.object(self.backend, "_run") as run:
            result = self.backend.start_session("proof-project", "request-1")
            self.assertEqual(result["session"]["runtimeId"], sid)
            self.assertEqual(result["session"]["state"], "running")
            run.assert_not_called()

    def test_unregistered_unended_botainer_record_blocks_launch_after_missing_registry(self):
        record = self.root / "state/state/uuid/sessions" / ("a" * 16) / "spec.json"
        record.parent.mkdir(parents=True)
        private_json(record, {"session_id": "a" * 16, "ended_at": None, "runtime_handle": {"docker": {"container_id": "b" * 64}}})
        with patch.object(self.backend, "_run") as run:
            with self.assertRaisesRegex(BackendUnavailable, "unregistered-runtime-record"):
                self.backend.start_session("proof-project", "request-1")
            run.assert_not_called()

    def _assert_writer_available(self, sid):
        fd = os.open(self.root / f"writer-{sid}.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)

    def test_failed_attach_runtime_checks_never_spawn_pty_and_release_writer(self):
        sid = "a" * 16
        private_json(self.backend.registry_path, {"sessions": [{"session_id": sid, "started_at": "original-start"}]})
        self.backend._session_record = lambda entry: {"ended_at": None}
        self.backend.environment = lambda: {}
        failures = [
            (BackendUnavailable("proof-daemon-changed"), None),
            (None, BackendUnavailable("proof-original-container-changed")),
            (None, {"State": {"Running": False}}),
        ]
        for daemon_error, inspection in failures:
            with self.subTest(daemon=daemon_error, inspection=inspection):
                with patch.object(self.backend, "verify_daemon", side_effect=daemon_error), \
                     patch.object(self.backend, "inspect_container", side_effect=inspection if isinstance(inspection, Exception) else None,
                                  return_value=inspection), \
                     patch("botainer_dashboard.proof_backend.PtyAttachment") as spawn:
                    with self.assertRaises(BackendUnavailable):
                        self.backend.attach(self.backend.namespace, sid, 80, 24)
                    spawn.assert_not_called()
                self._assert_writer_available(sid)

    def test_attach_without_original_start_identity_never_spawns_or_probes(self):
        sid = "a" * 16
        private_json(self.backend.registry_path, {"sessions": [{"session_id": sid}]})
        self.backend._session_record = lambda entry: {"ended_at": None}
        with patch.object(self.backend, "verify_daemon") as daemon, \
             patch("botainer_dashboard.proof_backend.PtyAttachment") as spawn:
            with self.assertRaisesRegex(BackendUnavailable, "original-container-unconfirmed"):
                self.backend.attach(self.backend.namespace, sid, 80, 24)
            daemon.assert_not_called(); spawn.assert_not_called()
        self._assert_writer_available(sid)

    def test_attach_verifies_runtime_before_spawn_and_holds_writer_until_close(self):
        sid = "a" * 16
        private_json(self.backend.registry_path, {"sessions": [{"session_id": sid, "started_at": "original-start"}]})
        self.backend._session_record = lambda entry: {"ended_at": None}
        self.backend.environment = lambda: {}
        events = []
        client = Mock()
        def inspect(entry):
            events.append("runtime"); return {"State": {"Running": True}}
        def spawn(*args, **kwargs):
            events.append("spawn"); return client
        with patch.object(self.backend, "verify_daemon", side_effect=lambda: events.append("daemon")), \
             patch.object(self.backend, "inspect_container", side_effect=inspect), \
             patch("botainer_dashboard.proof_backend.PtyAttachment", side_effect=spawn):
            attachment = self.backend.attach(self.backend.namespace, sid, 80, 24)
            try:
                self.assertEqual(events, ["daemon", "runtime", "spawn"])
                with self.assertRaises(BlockingIOError): self._assert_writer_available(sid)
            finally:
                attachment.close()
        client.close.assert_called_once_with(timeout=1)
        self._assert_writer_available(sid)


if __name__ == "__main__": unittest.main()
