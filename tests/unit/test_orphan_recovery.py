"""Ended-owner recovery uses inert owner, Botainer and Docker observations."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_ordinary_local as f
from botainer_dashboard.ordinary_local import orphan_launch_evidence


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = f.BackendTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.f = fixture = self.fixture
        fixture.durable_owner()
        self.backend = fixture.backend
        self.project = self.backend.snapshot()["projects"][0]
        self.backend.start_session(self.project["id"], f.REQUEST)
        fixture.owner_receipt({"phase": "before-dispatch", "session_id": f.SID})
        self.backend.owner.observe.return_value = {"state": "ended", "exit_code": 2}
        fixture.raw["sessions"] = [fixture.running()]
        self.receipt = self.backend.root / (f.REQUEST + ".result.json")
        self.original = self.receipt.read_bytes()

    def runtime(self):
        self.backend._last = 0
        return next(s for s in self.backend.snapshot()["sessions"] if s.get("kind") != "launch")

    def claim(self):
        self.runtime()
        operations = self.backend._operations()
        entry = operations["operations"][0]
        evidence = orphan_launch_evidence(self.backend.profile, self.backend.root, entry,
            self.project, self.f.binding(), self.backend.owner)
        data = {"request_id": f.REQUEST2, "session_id": f.SID, "binding": self.f.binding(),
            "project_uuid": f.UID, "project_path": str(self.f.project), **evidence}
        entry.update(orphan_recovery_request=f.REQUEST2)
        f.write_private_json(self.backend._operations_path, operations)
        f.write_private_json(self.backend._recoveries_path, {"recoveries": [
            {**data, "state": "dispatching", "namespace": self.backend.namespace}]})
        return data

    def test_ended_owner_enables_only_exact_container_stop_preserving_original(self):
        session = self.runtime()
        self.assertFalse(session["capabilities"]["attachTerminal"])
        self.assertTrue(session["capabilities"]["stopSession"])
        self.assertEqual(session["stopMode"], "orphan-container")
        self.assertEqual(session["stopScope"], "container")
        self.assertEqual(session["containerId"], f.CID)
        self.assertEqual(session["controlRestriction"], "original-owner-ended")
        self.assertEqual(f.read_private_json(self.backend._bindings_path)["sessions"][f.SID], self.f.binding())
        self.assertEqual(self.receipt.read_bytes(), self.original)
        self.backend.owner.attach.assert_not_called()

    def test_running_unknown_missing_and_changed_owner_never_enable_recovery(self):
        for state in ("running", "unknown", "missing"):
            with self.subTest(state=state):
                self.backend.owner.observe.return_value = {"state": state}
                self.assertFalse(self.runtime()["capabilities"]["stopSession"])
        self.backend.owner.observe.return_value = {"state": "ended"}
        self.backend.owner.recover.return_value = {"owner": {"nonce": "different"}}
        self.assertFalse(self.runtime()["capabilities"]["stopSession"])

    def test_changed_receipt_intent_candidate_and_pinned_binding_refused(self):
        receipt = f.read_private_json(self.receipt)
        for changed in (receipt | {"session_id": "c" * 16}, receipt | {"intent_sha256": "f" * 64},
                receipt | {"candidate_binding": {"id": "c" * 64}}, receipt | {"phase": "not-dispatched"}):
            with self.subTest(receipt=changed):
                f.write_private_json(self.receipt, changed)
                self.assertFalse(self.runtime()["capabilities"]["stopSession"])
        f.write_private_json(self.receipt, receipt)
        f.write_private_json(self.backend._bindings_path, {"sessions": {f.SID: self.f.binding() | {"id": "c" * 64}}})
        self.assertFalse(self.runtime()["capabilities"]["stopSession"])

    def test_project_identity_change_never_enables_recovery(self):
        (self.f.project / ".botainer/project-id").write_text(f.REQUEST2)
        self.assertFalse(self.runtime()["capabilities"]["stopSession"])

    def test_missing_original_receipt_never_enables_recovery(self):
        self.receipt.unlink()
        self.assertFalse(self.runtime()["capabilities"]["stopSession"])

    def test_external_terminal_never_attaches_or_runs_unowned_native_cleanup(self):
        f.write_private_json(self.backend._operations_path, {"operations": []})
        session = self.runtime()
        self.assertFalse(session["capabilities"]["attachTerminal"])
        self.assertFalse(session["capabilities"]["stopSession"])
        self.assertEqual(session["controlRestriction"], "external-terminal-owner-unverified")
        with self.assertRaises(f.BackendUnavailable):
            self.backend.attach(self.backend.namespace, f.SID, 80, 24)
        with self.assertRaises(f.BackendUnavailable):
            self.backend.stop_session(self.backend.namespace, f.SID, f.REQUEST2)
        self.assertTrue(all(request["action"] == "inventory" for request in self.f.requests))

    def test_stop_journals_separately_and_replays_without_dispatch(self):
        self.runtime()
        native_runner = self.f.runner.side_effect
        def runner(argv, **kwargs):
            data = f.read_private_json(Path(argv[-1]))
            if data["action"] == "inventory": return native_runner(argv, **kwargs)
            self.assertEqual(data["stop_mode"], "orphan-container")
            self.assertEqual(self.backend._recoveries()["recoveries"][0]["state"], "dispatching")
            self.assertEqual(self.backend._operations()["operations"][0]["state"], "unknown")
            result = {"request_id": f.REQUEST2, "session_id": f.SID, "binding": self.f.binding(), "stopped": True}
            f.write_private_json(self.backend.root / ("orphan-" + f.REQUEST2 + ".result.json"), result)
            return json.dumps(result).encode()
        self.f.runner.side_effect = runner
        result = self.backend.stop_session(self.backend.namespace, f.SID, f.REQUEST2, expected_stop_mode="orphan-container")
        self.assertTrue(result["terminationConfirmed"])
        self.assertFalse(result["helperCleanupConfirmed"])
        self.assertEqual(self.backend._operations()["operations"][0]["state"], "failed")
        self.f.raw["sessions"][0].update(state="stopped")
        project = self.backend.snapshot()["projects"][0]
        self.assertFalse(project["capabilities"]["startSession"])
        self.assertFalse(project["capabilities"]["configWrite"])
        self.assertEqual(project["controlRestriction"], "cleanup-unverified")
        with self.assertRaisesRegex(f.BackendUnavailable, "cleanup-unverified"):
            self.backend._start_console(operation="start", project=self.project,
                request="00000000-0000-4000-8000-000000000004")
        calls = self.f.runner.call_count
        self.assertEqual(self.backend.stop_session(self.backend.namespace, f.SID, f.REQUEST2, expected_stop_mode="orphan-container"), result)
        self.assertEqual(self.f.runner.call_count, calls)
        self.assertEqual(self.receipt.read_bytes(), self.original)
        with self.assertRaisesRegex(f.BackendUnavailable, "conflict"):
            self.backend.stop_session(self.backend.namespace, "c" * 16, f.REQUEST2, expected_stop_mode="orphan-container")

    def test_normal_confirmation_cannot_turn_into_orphan_stop(self):
        self.runtime()
        with self.assertRaisesRegex(f.BackendUnavailable, "stop-mode-changed"):
            self.backend.stop_session(self.backend.namespace, f.SID, f.REQUEST2)
        self.assertEqual(self.backend._recoveries()["recoveries"], [])
        self.assertTrue(all(request["action"] == "inventory" for request in self.f.requests))

    def test_ambiguous_stop_blocks_replay_new_request_and_project_start(self):
        self.runtime()
        native_runner = self.f.runner.side_effect
        def runner(argv, **kwargs):
            if f.read_private_json(Path(argv[-1]))["action"] == "inventory": return native_runner(argv, **kwargs)
            raise f.InventoryError("unit-stop-timeout")
        self.f.runner.side_effect = runner
        with self.assertRaisesRegex(f.BackendUnavailable, "stop-unconfirmed"):
            self.backend.stop_session(self.backend.namespace, f.SID, f.REQUEST2, expected_stop_mode="orphan-container")
        self.assertFalse(self.runtime()["capabilities"]["stopSession"])
        self.assertEqual(self.backend._operations()["operations"][0]["state"], "unknown")
        calls = self.f.runner.call_count
        with self.assertRaisesRegex(f.BackendUnavailable, "stop-unconfirmed"):
            self.backend.stop_session(self.backend.namespace, f.SID, f.REQUEST2, expected_stop_mode="orphan-container")
        self.assertEqual(self.f.runner.call_count, calls)
        self.assertFalse(self.backend.snapshot()["projects"][0]["capabilities"]["startSession"])
        self.assertEqual(self.receipt.read_bytes(), self.original)

    def test_helper_checks_identity_twice_and_stops_full_id_without_hooks(self):
        data = self.claim()
        with patch.object(f.helper, "verify_target") as verify, \
                patch.object(f.helper, "docker") as docker, \
                patch.object(f.helper, "exact_container_stopped", return_value=True):
            result = f.helper.stop_orphan_container(self.backend.profile, data, self.backend.root, owner=self.backend.owner)
        self.assertEqual(verify.call_count, 2)
        docker.assert_called_once_with(self.backend.profile, "container", "stop", "--time", "10", "--", f.CID)
        self.assertTrue(result["stopped"])
        self.assertFalse(result["helper_cleanup_confirmed"])
        self.assertEqual(self.receipt.read_bytes(), self.original)
        with patch.object(f.helper, "docker") as docker:
            with self.assertRaisesRegex(ValueError, "already dispatched"):
                f.helper.stop_orphan_container(self.backend.profile, data, self.backend.root, owner=self.backend.owner)
            docker.assert_not_called()

    def test_changed_container_at_second_verification_never_stops(self):
        data = self.claim()
        with patch.object(f.helper, "verify_target", side_effect=[None, ValueError("changed")]), \
                patch.object(f.helper, "docker") as docker:
            with self.assertRaisesRegex(ValueError, "changed"):
                f.helper.stop_orphan_container(self.backend.profile, data, self.backend.root, owner=self.backend.owner)
            docker.assert_not_called()

    def test_changed_project_identity_between_preview_and_helper_never_stops(self):
        data = self.claim()
        (self.f.project / ".botainer/project-id").write_text(f.REQUEST2)
        with patch.object(f.helper, "docker") as docker:
            with self.assertRaisesRegex(ValueError, "UUID changed"):
                f.helper.stop_orphan_container(self.backend.profile, data, self.backend.root, owner=self.backend.owner)
            docker.assert_not_called()

    def test_stop_exception_retains_uncertain_receipt_and_never_retries(self):
        data = self.claim()
        with patch.object(f.helper, "verify_target"), \
                patch.object(f.helper, "docker", side_effect=f.InventoryError("unit-timeout")) as docker:
            with self.assertRaises(f.InventoryError):
                f.helper.stop_orphan_container(self.backend.profile, data, self.backend.root, owner=self.backend.owner)
        result = f.read_private_json(self.backend.root / ("orphan-" + f.REQUEST2 + ".result.json"))
        self.assertFalse(result["stopped"])
        self.assertEqual(result["phase"], "stop-unconfirmed")
        self.assertEqual(docker.call_count, 1)

    def test_exact_absence_checks_all_names_and_renamed_container_is_not_absent(self):
        binding = self.f.binding()
        with patch.object(f.helper, "verify_daemon"), \
                patch.object(f.helper, "docker", side_effect=[(f.CID + "\n").encode(),
                    json.dumps(binding | {"name": "/renamed", "state": "running"}).encode()]) as docker:
            with self.assertRaisesRegex(ValueError, "identity changed"):
                f.helper.exact_container_stopped(self.backend.profile, binding)
            self.assertEqual(docker.call_args_list[0].args[1:], ("ps", "-a", "--no-trunc", "--format", "{{.ID}}"))

    def test_daemon_or_listing_failure_never_proves_absence(self):
        for failed in ("verify_daemon", "docker"):
            with self.subTest(failed=failed), patch.object(f.helper, "verify_daemon") as daemon, \
                    patch.object(f.helper, "docker") as docker:
                (daemon if failed == "verify_daemon" else docker).side_effect = f.InventoryError("unit-offline")
                with self.assertRaises(f.InventoryError):
                    f.helper.exact_container_stopped(self.backend.profile, self.f.binding())

    def test_private_external_stop_reconciliation_requires_fresh_absence(self):
        self.runtime()
        entry = self.backend._operations()["operations"][0]
        evidence = orphan_launch_evidence(self.backend.profile, self.backend.root, entry,
            self.project, self.f.binding(), self.backend.owner)
        private = self.f.application / "private"; private.mkdir(mode=0o700)
        path = private / "explicit-stop.json"
        f.write_private_json(path, {**evidence, "binding": self.f.binding(), "session_id": f.SID,
            "stopped": True, "daemon_id": self.backend.profile.data["docker"]["daemon_id"]})
        with patch.object(self.backend, "_call", return_value={"stopped": False, "binding": self.f.binding(), "session_id": f.SID}):
            with self.assertRaisesRegex(f.BackendUnavailable, "stop-unconfirmed"):
                self.backend.reconcile_orphan_stop(self.backend.namespace, f.SID, f.REQUEST2, path)
        self.assertEqual(self.backend._operations()["operations"][0]["state"], "unknown")
        with patch.object(self.backend, "_call", return_value={"stopped": True, "binding": self.f.binding(), "session_id": f.SID}) as call:
            result = self.backend.reconcile_orphan_stop(self.backend.namespace, f.SID, f.REQUEST2, path)
        self.assertEqual(call.call_args.args[0], "orphan-observe-stopped")
        self.assertTrue(result["terminationConfirmed"])
        self.assertFalse(result["helperCleanupConfirmed"])
        self.assertEqual(self.backend._operations()["operations"][0]["state"], "failed")
        self.assertEqual(self.receipt.read_bytes(), self.original)
        self.f.raw["sessions"][0]["state"] = "stopped"
        restarted = f.OrdinaryLocalBackend(self.f.application, self.f.profile_path, runner=self.f.runner)
        project = restarted.snapshot()["projects"][0]
        self.assertEqual(project["controlRestriction"], "cleanup-unverified")
        self.assertFalse(project["capabilities"]["startSession"])
        self.assertFalse(project["capabilities"]["configWrite"])

    def ambiguous_recovery_proof(self):
        data = self.claim()
        attempts = self.backend._recoveries()
        attempts["recoveries"][0]["state"] = "unknown"
        f.write_private_json(self.backend._recoveries_path, attempts)
        helper_path = self.backend.root / ("orphan-" + f.REQUEST2 + ".result.json")
        f.write_private_json(helper_path, {**data, "phase": "container-stopped", "stopped": True})
        private = self.f.application / "private"; private.mkdir(mode=0o700)
        proof_path = private / "reconcile-stop.json"
        f.write_private_json(proof_path, {**data, "stopped": True, "recovery_request_id": f.REQUEST2,
            "daemon_id": self.backend.profile.data["docker"]["daemon_id"],
            "helper_receipt_sha256": f.sha(helper_path)})
        return proof_path, helper_path, attempts["recoveries"][0]

    def test_lost_stop_reply_reconciles_same_request_read_only_and_preserves_attempt(self):
        proof_path, helper_path, original_attempt = self.ambiguous_recovery_proof()
        original_helper = helper_path.read_bytes()
        observed = {"stopped": True, "binding": self.f.binding(), "session_id": f.SID}
        with patch.object(self.backend, "_call", return_value=observed) as call:
            result = self.backend.reconcile_orphan_stop(self.backend.namespace, f.SID, f.REQUEST2, proof_path)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args.args[0], "orphan-observe-stopped")
        self.assertTrue(result["terminationConfirmed"])
        self.assertFalse(result["helperCleanupConfirmed"])
        attempts = self.backend._recoveries()["recoveries"]
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["state"], "completed")
        self.assertEqual(attempts[0]["reconciliations"][0]["prior_attempt"], original_attempt)
        self.assertEqual(attempts[0]["reconciliations"][0]["helper_receipt_sha256"], f.sha(helper_path))
        self.assertEqual(helper_path.read_bytes(), original_helper)
        self.assertEqual(self.receipt.read_bytes(), self.original)
        self.assertTrue(self.backend._operations()["operations"][0]["helper_cleanup_unverified"])

    def test_ambiguous_reconciliation_refuses_changed_request_binding_receipt_or_observation(self):
        proof_path, helper_path, original_attempt = self.ambiguous_recovery_proof()
        proof = f.read_private_json(proof_path)
        changed = [proof | {"recovery_request_id": f.REQUEST},
            proof | {"binding": self.f.binding() | {"id": "c" * 64}},
            proof | {"helper_receipt_sha256": "f" * 64}, proof | {"session_id": "a" * 12 + "bbbb"}]
        for candidate in changed:
            with self.subTest(candidate=candidate), patch.object(self.backend, "_call") as call:
                f.write_private_json(proof_path, candidate)
                with self.assertRaises(f.BackendUnavailable):
                    self.backend.reconcile_orphan_stop(self.backend.namespace, f.SID, f.REQUEST2, proof_path)
                call.assert_not_called()
        f.write_private_json(proof_path, proof)
        with patch.object(self.backend, "_call") as call:
            with self.assertRaisesRegex(f.BackendUnavailable, "conflict"):
                self.backend.reconcile_orphan_stop(self.backend.namespace, f.SID, f.REQUEST, proof_path)
            call.assert_not_called()
        with patch.object(self.backend, "_call", side_effect=f.InventoryError("unit-offline")):
            with self.assertRaises(f.InventoryError):
                self.backend.reconcile_orphan_stop(self.backend.namespace, f.SID, f.REQUEST2, proof_path)
        self.assertEqual(self.backend._recoveries()["recoveries"][0], original_attempt)


if __name__ == "__main__": unittest.main()
