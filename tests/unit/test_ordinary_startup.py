"""Foreground startup transitions use inert native CLI/Docker fixtures only."""
from contextlib import ExitStack, redirect_stderr
import copy
import io
import os
import unittest
from unittest.mock import patch

import test_ordinary_local as fixtures

helper = fixtures.helper


class StartupTests(unittest.TestCase):
    def setUp(self):
        # Compose the existing fixture rather than inheriting its test methods.
        self.fixture = fixtures.NativeCliTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        f.data["terminal_owner_id"] = fixtures.REQUEST.replace("-", "")
        f.profile.data["terminal_owner"] = {"path": str(f.root / "bin/python"),
            "sha256": fixtures.sha(f.root / "bin/python"), "control_root": str(f.root / "terminal-control")}
        fixtures.write_private_json(f.intent, f.data)
        self.clock = 0.
        self.output = io.StringIO()

    def tick(self, delay):
        self.clock += delay

    def run_case(self, observations, *, exit_code=0):
        f = self.fixture
        states = iter(observations)
        self.calls = 0
        last = {}
        def inventory(_profile):
            nonlocal last
            self.calls += 1
            item = next(states, last)
            if isinstance(item, Exception): raise item
            last = item
            return {} if item is None else {item["name"]: item} if item else {}
        def cli(argv):
            self.assertNotIn("--detach", argv)
            handle = f.composition.launch(f.spec)
            self.assertIs(handle, f.original.return_value)
            # Native attach/wait is reached even if readiness is unconfirmed.
            self.before_wait = fixtures.read_private_json(f.result)
            self.native_wait_called = True
            return exit_code
        modules = {"botainer.core": fixtures.SimpleNamespace(composition=f.composition),
                   "botainer.adapters.docker": fixtures.SimpleNamespace(DockerAdapter=f.adapter),
                   "botainer.plugins": fixtures.SimpleNamespace(hooks=f.hooks)}
        self.native_wait_called = False
        with ExitStack() as stack:
            stack.enter_context(patch.dict("sys.modules", modules))
            stack.enter_context(patch.object(fixtures.Path, "cwd", return_value=f.project))
            stack.enter_context(patch.object(helper, "verify_daemon"))
            stack.enter_context(patch.object(helper, "inventory", return_value=f.raw))
            stack.enter_context(patch.object(helper, "exact_record", return_value=f.record))
            stack.enter_context(patch.object(helper, "docker_inventory", side_effect=inventory))
            stack.enter_context(patch.object(helper.time, "monotonic", side_effect=lambda: self.clock))
            stack.enter_context(patch.object(helper.time, "sleep", side_effect=self.tick))
            stack.enter_context(patch.dict(os.environ, {"TMUX": str(f.root / "terminal-control" / f.data["terminal_owner_id"] / "s") + ",12,0", "TMUX_PANE": "%0"}))
            stack.enter_context(redirect_stderr(self.output))
            self.assertEqual(helper.native_operation(f.profile, f.data, f.intent, cli), exit_code)
        self.assertTrue(self.native_wait_called)
        f.original.assert_called_once_with(f.spec, detach=False)
        return fixtures.read_private_json(f.result)

    def container(self, **changes):
        return copy.deepcopy(self.fixture.container) | changes

    def test_missing_then_created_then_running_uses_final_started_at(self):
        result = self.run_case([None, self.container(state="created", started_at="0001-01-01T00:00:00Z"),
                                self.container(state="created", started_at="0001-01-01T00:00:00Z"), self.container()])
        self.assertEqual(self.calls, 4)
        self.assertGreater(self.clock, 0)
        self.assertEqual(self.before_wait["phase"], "runtime-returned")
        self.assertEqual(self.before_wait["binding"]["started_at"], "started")
        self.assertEqual(result["phase"], "runtime-ended")

    def assert_unconfirmed(self, result, code, *, candidate=True):
        self.assertEqual(self.before_wait["phase"], "dispatch-unconfirmed")
        self.assertEqual(result["phase"], "dispatch-unconfirmed")
        self.assertEqual(result["failure_code"], code)
        self.assertEqual(result["session_id"], fixtures.SID)
        self.assertTrue(result["cli_ended"])
        self.assertEqual(result["intent_sha256"], fixtures.sha(self.fixture.intent))
        self.assertNotIn("binding", result)
        if candidate:
            self.assertEqual(set(result["candidate_binding"]), {"id", "name", "image", "created"})
            self.assertEqual(result["candidate_binding"]["id"], fixtures.CID)
        else:
            self.assertNotIn("candidate_binding", result)

    def test_created_timeout_preserves_candidate_and_native_wait(self):
        result = self.run_case([self.container(state="created")], exit_code=8)
        self.assert_unconfirmed(result, "ordinary-startup-timeout")
        self.assertEqual(result["exit_code"], 8)
        self.assertAlmostEqual(self.clock, 15.)
        self.assertIn("keeps ownership", self.output.getvalue())

    def test_missing_timeout_preserves_native_wait_without_inventing_candidate(self):
        result = self.run_case([None])
        self.assert_unconfirmed(result, "ordinary-startup-timeout", candidate=False)
        self.assertAlmostEqual(self.clock, 15.)

    def test_replacement_is_never_adopted(self):
        # Native record uses a name, as real foreground Botainer does.
        self.fixture.record.docker.container_id = "botainer-" + fixtures.SID[:12]
        for change in ({"id": "c" * 64}, {"image": "different-image"}, {"created": "different-created"}):
            with self.subTest(change=change):
                self.fixture.original.reset_mock()
                result = self.run_case([self.container(state="created"), None, self.container(**change)])
                self.assert_unconfirmed(result, "ordinary-startup-owner-replaced")

    def test_inventory_failure_preserves_first_candidate_and_native_wait(self):
        result = self.run_case([self.container(state="created"), fixtures.InventoryError("unit-offline")])
        self.assert_unconfirmed(result, "ordinary-startup-observation-failed")

    def test_failure_before_any_observation_does_not_invent_candidate(self):
        result = self.run_case([OSError("temporarily unavailable")])
        self.assert_unconfirmed(result, "ordinary-startup-observation-failed", candidate=False)

    def test_no_tty_is_distinct_from_no_stdin(self):
        for field, code in (("tty", "ordinary-startup-tty-unavailable"), ("stdin", "ordinary-startup-stdin-unavailable")):
            with self.subTest(field=field):
                self.fixture.original.reset_mock()
                result = self.run_case([self.container(**{field: False})])
                self.assert_unconfirmed(result, code)

    def test_ended_before_ready_is_not_success_or_not_dispatched(self):
        for state in ("exited", "dead"):
            with self.subTest(state=state):
                self.fixture.original.reset_mock()
                result = self.run_case([self.container(state=state)])
                self.assert_unconfirmed(result, "ordinary-startup-ended-before-ready")

    def test_unexpected_state_retains_owner_without_claiming_readiness(self):
        for state in ("paused", None, [], {"invalid": True}):
            with self.subTest(state=state):
                self.fixture.original.reset_mock()
                result = self.run_case([self.container(state=state)])
                self.assert_unconfirmed(result, "ordinary-startup-state-unconfirmed")

    def test_running_without_start_metadata_stays_unconfirmed(self):
        for started in (None, "", 123, "0001-01-01T00:00:00Z", "0001-01-01T00:00:00.000000000Z"):
            with self.subTest(started=started):
                self.fixture.original.reset_mock()
                result = self.run_case([self.container(started_at=started)])
                self.assert_unconfirmed(result, "ordinary-startup-observation-failed")

    def test_invalid_native_binding_does_not_persist_candidate(self):
        result = self.run_case([self.container(mounts=[{"Type": "bind", "Source": "/wrong-project"}])])
        self.assert_unconfirmed(result, "ordinary-startup-binding-invalid", candidate=False)

    def recovered_stop(self, changes=None):
        f = self.fixture
        write = fixtures.write_private_json
        def historical_receipt(path, value):
            if path == f.result:
                value = dict(value)
                value.update(phase="dispatch-unconfirmed", failure_code="ordinary-startup-timeout",
                    candidate_binding={key: value["binding"][key] for key in ("id", "name", "image", "created")})
                del value["binding"]
                value.update(changes or {})
            return write(path, value)
        # Run the existing native Stop fixture with only its original receipt
        # changed to the preserved unconfirmed phase. It verifies the exact
        # current target and owner and asserts no duplicate post-hook cleanup.
        with patch.object(fixtures, "write_private_json", side_effect=historical_receipt):
            f.test_native_stop_leaves_cleanup_to_same_foreground_cli_only()

    def test_recovered_original_candidate_can_stop_without_rewriting_receipt(self):
        self.recovered_stop()
        result = fixtures.read_private_json(self.fixture.result)
        self.assertEqual(result["phase"], "dispatch-unconfirmed")
        self.assertNotIn("binding", result)

    def test_unconfirmed_stop_rejects_missing_changed_ended_or_wrong_request_receipt(self):
        for changes in ({"candidate_binding": None}, {"candidate_binding": {"id": "c" * 64}},
                        {"cli_ended": True}, {"cli_ended": "false"}, {"cli_ended": 0}, {"request_id": fixtures.REQUEST2},
                        {"failure_code": "ordinary-startup-owner-replaced"}):
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(ValueError, "cleanup owner is unverified"):
                    self.recovered_stop(changes)


if __name__ == "__main__":
    unittest.main()
