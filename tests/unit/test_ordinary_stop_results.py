"""Exact Stop results with inert native CLI/Docker fixtures; no runtime executes."""
from contextlib import ExitStack, redirect_stdout
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from botainer_dashboard.backend_errors import BackendUnavailable
from botainer_dashboard.inventory import InventoryError
from botainer_dashboard.pairing import write_private_json
import test_ordinary_local as fixtures


class OrdinaryStopResultTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.NativeCliTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        f.profile.data["terminal_owner"] = {
            "path": str(f.root / "bin/python"), "sha256": fixtures.sha(f.root / "bin/python"),
            "control_root": str(f.root / "terminal-control")}
        self.request = f.backend.root / "stop-query.json"
        write_private_json(self.request, {
            "action": "stop", "profile_sha256": f.profile.digest,
            "project_path": str(f.project), "project_uuid": fixtures.UID,
            "session_id": fixtures.SID, "binding": f.binding(),
            "terminal_owner_id": fixtures.REQUEST.replace("-", ""),
            "launch_request_id": fixtures.REQUEST,
            "launch_intent_sha256": fixtures.sha(f.intent)})
        write_private_json(f.result, {"request_id": fixtures.REQUEST,
            "intent_sha256": fixtures.sha(f.intent), "phase": "runtime-returned",
            "session_id": fixtures.SID, "binding": f.binding()})

    def run_stop(self, containers, *, native_stopped=False, cli_code=0,
                 stdout="", stderr="", daemon_changed=False, observation_error=None):
        f = self.fixture
        native_stop = Mock(return_value=native_stopped)
        stop = SimpleNamespace(_do_runtime_stop=native_stop)
        post = Mock()
        composition = SimpleNamespace(run_post_session_hooks=post)

        def cli(argv):
            self.assertEqual(argv, ["stop", "--", fixtures.SID])
            stopped = stop._do_runtime_stop(f.record, force=False)
            if stopped:
                composition.run_post_session_hooks(f.spec)
            # Native stop can print its failure and return normally. It cannot
            # establish a termination verdict solely from the CLI exit code.
            print(stdout, end="")
            print(stderr, end="", file=sys.stderr)
            return cli_code

        calls = []

        def docker(_profile, *args):
            calls.append(args)
            if args == ("info", "--format", "{{json .ID}}"):
                return json.dumps("different-daemon" if daemon_changed
                    else f.profile.data["docker"]["daemon_id"]).encode()
            if args == ("ps", "-a", "--no-trunc", "--format", "{{.ID}}"):
                if observation_error:
                    raise observation_error
                return "".join(item["id"] + "\n" for item in containers).encode()
            if args[:4] == ("container", "inspect", "--format", fixtures.helper.CONTAINER_FORMAT):
                self.assertEqual(args[4:], (fixtures.CID,))
                return json.dumps(next(item for item in containers if item["id"] == fixtures.CID)).encode()
            self.fail("Unexpected Docker operation: " + repr(args))

        modules = {
            "botainer": SimpleNamespace(__file__=str(f.root / "source/botainer/__init__.py")),
            "botainer.cli": SimpleNamespace(stop=stop),
            "botainer.cli.main": SimpleNamespace(main=cli),
            "botainer.core": SimpleNamespace(composition=composition),
            "botainer.plugins": SimpleNamespace(hooks=f.hooks)}
        owner = Mock()
        owner.observe.return_value = {"state": "running"}
        output = io.StringIO()
        with ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, modules))
            stack.enter_context(patch.object(sys, "path", list(sys.path)))
            stack.enter_context(patch.object(sys, "argv", [str(fixtures.ROOT / "tools/ordinary_local_helper.py"),
                str(f.profile_path), str(self.request)]))
            stack.enter_context(patch.object(fixtures.helper, "OrdinaryLocalProfile", return_value=f.profile))
            stack.enter_context(patch.object(fixtures.helper, "verify_target", return_value=f.record))
            stack.enter_context(patch.object(Path, "cwd", return_value=f.project))
            stack.enter_context(patch.object(fixtures.helper, "docker", side_effect=docker))
            stack.enter_context(patch("botainer_dashboard.ordinary_owner.OrdinaryCliOwner", return_value=owner))
            stack.enter_context(redirect_stdout(output))
            self.assertEqual(fixtures.helper.main(), 0)
        native_stop.assert_called_once_with(f.record, force=False)
        self.assertEqual(f.record.docker.container_id, fixtures.CID)
        post.assert_not_called()  # The original foreground CLI still owns cleanup.
        self.assertIs(stop._do_runtime_stop, native_stop)
        self.assertIs(composition.run_post_session_hooks, post)
        result = json.loads(output.getvalue())
        self.assertIs(result["helper_cleanup_confirmed"], False)
        self.assertIs(result["foreground_cleanup"], True)
        return result, calls

    def test_failed_native_stop_retains_bounded_plain_text_diagnostic(self):
        result, calls = self.run_stop([self.fixture.container],
            stdout="\x1b[31mStop failed\x1b[0m\r\n", stderr="Native warning: still running\n")
        self.assertFalse(result["stopped"])
        self.assertEqual(result["exit_code"], 0)
        self.assertIsNone(result["observation_error"])
        self.assertEqual(result["diagnostic"], "Stop failed\nNative warning: still running")
        self.assertFalse(result["diagnostic_truncated"])
        self.assertIn(("container", "inspect", "--format", fixtures.helper.CONTAINER_FORMAT, fixtures.CID), calls)

    def test_renamed_running_original_is_unknown_not_terminated(self):
        renamed = {**self.fixture.container, "name": "/renamed-fixture"}
        result, _ = self.run_stop([renamed], stderr="Native stop failed")
        self.assertFalse(result["stopped"])
        self.assertEqual(result["observation_error"], "ordinary-stop-observation-unconfirmed")
        self.assertEqual(result["diagnostic"], "Native stop failed")

    def test_reused_old_name_does_not_hide_original_running_id(self):
        renamed = {**self.fixture.container, "name": "/renamed-fixture"}
        replacement = {**self.fixture.container, "id": "c" * 64, "state": "exited"}
        result, calls = self.run_stop([renamed, replacement])
        self.assertFalse(result["stopped"])
        self.assertEqual(result["observation_error"], "ordinary-stop-observation-unconfirmed")
        inspected = [args[-1] for args in calls if args[:2] == ("container", "inspect")]
        self.assertEqual(inspected, [fixtures.CID])

    def test_original_removed_is_confirmed_even_if_another_container_reuses_name(self):
        for containers in ([], [{**self.fixture.container, "id": "c" * 64}]):
            with self.subTest(replacement=bool(containers)):
                result, calls = self.run_stop(containers)
                self.assertTrue(result["stopped"])
                self.assertIsNone(result["observation_error"])
                self.assertFalse(any(args[:2] == ("container", "inspect") for args in calls))

    def test_exact_exited_or_dead_container_is_confirmed(self):
        for state in ("exited", "dead"):
            with self.subTest(state=state):
                result, _ = self.run_stop([{**self.fixture.container, "state": state}], native_stopped=True)
                self.assertTrue(result["stopped"])
                self.assertIsNone(result["observation_error"])

    def test_changed_creation_or_start_identity_is_never_confirmed(self):
        for field in ("image", "created", "started_at"):
            with self.subTest(field=field):
                result, _ = self.run_stop([{**self.fixture.container, "state": "exited", field: "changed"}])
                self.assertFalse(result["stopped"])
                self.assertEqual(result["observation_error"], "ordinary-stop-observation-unconfirmed")

    def test_daemon_change_is_unknown_without_querying_replacement_containers(self):
        result, calls = self.run_stop([], daemon_changed=True)
        self.assertFalse(result["stopped"])
        self.assertEqual(result["observation_error"], "ordinary-stop-observation-unconfirmed")
        self.assertEqual(calls, [("info", "--format", "{{json .ID}}")])

    def test_post_dispatch_observation_failure_preserves_diagnostic_and_never_retries_stop(self):
        result, calls = self.run_stop([], stderr="Native stop was requested",
            observation_error=InventoryError("synthetic-private-error-details"))
        self.assertFalse(result["stopped"])
        self.assertEqual(result["observation_error"], "ordinary-stop-observation-unconfirmed")
        self.assertEqual(result["diagnostic"], "Native stop was requested")
        self.assertNotIn("synthetic-private", json.dumps(result))
        self.assertEqual(len(calls), 2)

    def test_unknown_runtime_state_is_not_termination(self):
        result, _ = self.run_stop([{**self.fixture.container, "state": "unrecognized"}])
        self.assertFalse(result["stopped"])

    def test_native_nonzero_exit_is_separate_from_proven_original_absence(self):
        result, _ = self.run_stop([], cli_code=1, stderr="Native cleanup is not verified")
        self.assertTrue(result["stopped"])
        self.assertEqual(result["exit_code"], 1)
        self.assertEqual(result["diagnostic"], "Native cleanup is not verified")


class StopDiagnosticTests(unittest.TestCase):
    def test_capture_is_bounded_during_writes_including_unicode(self):
        capture = fixtures.helper.StopOutput()
        for _ in range(100):
            text = "\U0001f642" * 1000
            self.assertEqual(capture.write(text), len(text))
        self.assertLessEqual(len(capture.getvalue()), capture.LIMIT)
        self.assertLessEqual(len(capture.diagnostic()), capture.LIMIT)
        self.assertLessEqual(len(capture.diagnostic().encode()), capture.LIMIT * 4)
        self.assertTrue(capture.truncated)
        self.assertIn("[Further native stop output omitted.]", capture.diagnostic())

    def test_terminal_strings_bidi_and_controls_are_removed_without_parsing_html(self):
        capture = fixtures.helper.StopOutput()
        for part in ("\x1b]52;c;clipboard", "\x07Text\x1bPprivate-control\x1b\\", "\u202eevil\x00\x07\x7f\x85\n<img>\t"):
            capture.write(part)
        self.assertEqual(capture.diagnostic(), "Textevil\n<img>")
        self.assertFalse(capture.truncated)

    def test_escape_sequence_cut_at_capture_limit_is_omitted(self):
        capture = fixtures.helper.StopOutput()
        capture.write("Visible\n\x1b]52;c;" + "x" * 10000)
        diagnostic = capture.diagnostic()
        self.assertIn("Visible", diagnostic)
        self.assertNotIn("52;c;", diagnostic)
        self.assertNotIn("x", diagnostic)
        self.assertTrue(capture.truncated)


class StopBackendResultTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.BackendTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        f.durable_owner()
        self.project = f.backend.snapshot()["projects"][0]
        f.backend.start_session(self.project["id"], fixtures.REQUEST)
        f.owner_receipt({"phase": "runtime-returned", "session_id": fixtures.SID, "binding": f.binding()})
        f.raw["sessions"] = [f.running()]
        f.backend._last = 0
        f.backend.snapshot()

    def stop(self, reply):
        f = self.fixture
        original = f.backend._call
        calls = []
        def call(action, data=None):
            if action == "stop":
                calls.append(data)
                return reply
            return original(action, data)
        with patch.object(f.backend, "_call", side_effect=call):
            result = f.backend.stop_session(f.backend.namespace, fixtures.SID, fixtures.REQUEST2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["binding"], f.binding())
        return result

    def test_unconfirmed_stop_preserves_native_diagnostic_without_claiming_cleanup(self):
        result = self.stop({"stopped": False, "diagnostic": "Native stop failed <untrusted>\n",
            "diagnostic_truncated": False, "observation_error": "ordinary-stop-observation-unconfirmed",
            "exit_code": 0, "foreground_cleanup": True})
        self.assertFalse(result["terminationConfirmed"])
        self.assertFalse(result["helperCleanupConfirmed"])
        self.assertEqual(result["observationError"], "ordinary-stop-observation-unconfirmed")
        self.assertEqual(result["diagnostic"], "Native stop failed <untrusted>\n")
        self.assertEqual(result["nativeExitCode"], 0)

    def test_proven_runtime_termination_does_not_promote_foreground_cleanup_to_confirmed(self):
        result = self.stop({"stopped": True, "foreground_cleanup": True, "exit_code": 1})
        self.assertTrue(result["terminationConfirmed"])
        self.assertFalse(result["helperCleanupConfirmed"])
        self.assertEqual(result["nativeExitCode"], 1)

    def test_diagnostics_are_bounded_and_unexpected_status_is_not_success(self):
        result = self.stop({"stopped": False, "diagnostic": "\x00\u202e" + "x" * 10000})
        self.assertLessEqual(len(result["diagnostic"]), 4096)
        self.assertNotIn("\x00", result["diagnostic"])
        self.assertNotIn("\u202e", result["diagnostic"])
        self.assertTrue(result["diagnosticTruncated"])
        with self.assertRaisesRegex(BackendUnavailable, "stop-result-unconfirmed"):
            self.stop({"stopped": "true"})


class RenamedInventoryTests(unittest.TestCase):
    def test_recognized_current_session_does_not_make_previous_sessions_unknown(self):
        h = fixtures.HelperTests()
        historical_id = "d" * 16
        historical = h.record(session_id=historical_id, ended_at="previous-end",
            docker=SimpleNamespace(container_id="botainer-" + historical_id[:12]))
        raw = h.run_inventory([historical, h.record()], [h.container()])
        self.assertEqual([s["state"] for s in raw["sessions"]], ["stopped", "running"])
        self.assertFalse(raw["projects"][0]["runtime_unverified"])

    def test_inventory_does_not_filter_away_renamed_container_ids(self):
        h = fixtures.HelperTests()
        renamed = {**h.container(), "name": "/renamed-fixture"}
        with patch.object(fixtures.helper, "verify_daemon"), patch.object(fixtures.helper, "docker",
                side_effect=[(fixtures.CID + "\n").encode(), json.dumps(renamed).encode()]) as docker:
            result = fixtures.helper.docker_inventory(None)
        self.assertEqual(docker.call_args_list[0].args[1:], ("ps", "-a", "--no-trunc", "--format", "{{.ID}}"))
        self.assertEqual(result, {renamed["name"]: renamed})

    def test_renamed_live_workspace_owner_never_looks_stopped_or_allows_relaunch(self):
        f = fixtures.BackendTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        project = f.backend.snapshot()["projects"][0]
        self.assertTrue(project["capabilities"]["startSession"])
        h = fixtures.HelperTests()
        record = h.record(project_root=str(f.project))
        renamed = {**h.container(), "name": "/renamed-fixture",
            "mounts": [{"Type": "bind", "Source": str(f.project)}]}
        state = SimpleNamespace(list_projects=lambda: [SimpleNamespace(uuid=fixtures.UID,
            display_name="Existing", last_path=str(f.project), last_session_at="started")])
        records = SimpleNamespace(candidate_session_dirs=lambda _root: [0], read=lambda _candidate: record)
        with patch.dict(sys.modules, {"botainer.state": SimpleNamespace(dir=state, session_record=records)}), \
                patch.object(fixtures.helper, "docker_inventory", return_value={renamed["name"]: renamed}), \
                patch.object(Path, "exists", return_value=True):
            raw = fixtures.helper.inventory(f.backend.profile)
        self.assertEqual(raw["sessions"][0]["state"], "unknown")
        self.assertTrue(raw["projects"][0]["runtime_unverified"])
        f.raw.clear(); f.raw.update(raw)
        f.backend._last = 0
        snapshot = f.backend.snapshot()
        runtime = next(s for s in snapshot["sessions"] if s["runtimeId"] == fixtures.SID)
        self.assertEqual(runtime["state"], "unknown")
        self.assertFalse(runtime["capabilities"]["attachTerminal"])
        self.assertFalse(runtime["capabilities"]["stopSession"])
        self.assertFalse(snapshot["projects"][0]["capabilities"]["startSession"])
        self.assertFalse(snapshot["projects"][0]["capabilities"]["configWrite"])
        with self.assertRaisesRegex(BackendUnavailable, "active-or-unverified"):
            f.backend.start_session(project["id"], fixtures.REQUEST)
        self.assertEqual(f.consoles, [])


if __name__ == "__main__":
    unittest.main()
