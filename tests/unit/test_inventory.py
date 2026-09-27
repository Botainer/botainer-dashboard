"""Offline inventory identity, schema and failure tests; no Botainer execution."""

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from botainer_dashboard.backend_errors import BackendUnavailable
from botainer_dashboard.context import ExecutionContext, Launcher
from botainer_dashboard.inventory import InventoryBackend, InventoryError, ReviewedInventoryProfile

_UUID = "11111111-1111-4111-8111-111111111111"
_SID = "0123456789abcdef"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="dashboard-inventory-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.exe = self.root / "python"
        self.exe.write_text("reviewed fixture executable bytes; never executed")
        self.exe.chmod(0o700)
        self.source = self.root / "botainer"
        self.source.mkdir()
        (self.source / "__init__.py").write_text("# reviewed fixture source")
        self.support = self.root / "pyvenv.cfg"
        self.support.write_text("# reviewed fixture registration")
        self.state = self.root / "state"
        self.state.mkdir(mode=0o700)
        self.tools = self.root / "empty-tools"
        self.tools.mkdir(mode=0o700)
        self.project = self.root / "project with spaces"
        (self.project / ".botainer").mkdir(parents=True)
        (self.project / ".botainer" / "project-id").write_text(_UUID + "\n")
        context = ExecutionContext("local.existing", "machine1", "account1", Launcher(self.exe, "python_module"), self.state)
        info = self.state.stat()
        self.profile = ReviewedInventoryProfile(
            context, self.source, {"__init__.py": digest(self.source / "__init__.py")}, digest(self.exe), self.exe,
            {str(self.support): digest(self.support)}, (info.st_dev, info.st_ino), self.root,
            self.tools, self.root, "Reviewed existing installation", "fixture-version",
        )
        self.projects = [{"uuid": _UUID, "display_name": "Registered project", "last_path": str(self.project),
                          "path_exists": True, "paths": [str(self.project)], "sessions_dir_count": 1,
                          "last_session_at": "2026-09-20T10:00:00Z", "last_session_runtime": "docker"}]
        self.status = {"sessions": [{"project_uuid": _UUID, "session_id": _SID, "alive": True,
                                     "runtime": "docker", "started_at": "2026-09-20T10:00:00Z", "ended_at": None,
                                     "image": "ignored-private-image", "container_id": "ignored-handle"}]}
        self.calls = []

    def runner(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        return json.dumps(self.projects if argv[-2:] == ("list", "--json") else self.status).encode()

    def backend(self, **kwargs):
        return InventoryBackend(self.profile, runner=kwargs.get("runner", self.runner))

    def test_constructor_and_module_do_not_execute(self):
        with patch("botainer_dashboard.inventory.subprocess.Popen") as popen:
            self.backend()
            popen.assert_not_called()
        self.assertEqual(self.calls, [])

    def test_only_fixed_commands_explicit_environment_and_matching_identity(self):
        with patch.dict(os.environ, {"PYTHONPATH": "/unreviewed", "DOCKER_HOST": "ssh://unexpected", "TOKEN": "private"}):
            value = self.backend().snapshot()
        self.assertFalse(value["stale"])
        self.assertEqual([call[0] for call in self.calls], [
            (str(self.exe), "-I", "-B", "-m", "botainer.cli.main", "list", "--json"),
            (str(self.exe), "-I", "-B", "-m", "botainer.cli.main", "status", "--global", "--all", "--json"),
        ])
        for _argv, arguments in self.calls:
            self.assertEqual(arguments["env"]["PATH"], str(self.tools))
            self.assertEqual(arguments["env"]["MY_BOTAINER"], str(self.state))
            self.assertFalse(set(arguments["env"]) & {"PYTHONPATH", "DOCKER_HOST", "TOKEN"})
            self.assertEqual(arguments["cwd"], self.root)
        self.assertTrue(value["projects"][0]["pathIdentityVerified"])
        self.assertEqual(value["sessions"][0]["projectId"], value["projects"][0]["id"])
        self.assertEqual(value["sessions"][0]["contextNamespace"], self.profile.namespace)
        self.assertNotIn("image", value["sessions"][0])

    def test_true_false_and_ended_records_all_keep_unverified_state(self):
        for alive, ended in ((True, None), (False, None), (False, "2026-09-20T11:00:00Z")):
            with self.subTest(alive=alive, ended=ended):
                self.status["sessions"][0].update(alive=alive, ended_at=ended)
                value = self.backend().snapshot()
                self.assertEqual(value["sessions"][0]["state"], "unknown")
                self.assertFalse(value["sessions"][0]["livenessVerified"])
                self.assertEqual(value["sessions"][0]["reportedAlive"], alive)
                self.assertFalse(any(value["capabilities"].values()))

    def test_project_launch_time_survives_missing_session_rows_without_becoming_activity(self):
        self.status["sessions"] = []
        self.projects[0]["last_session_ended_at"] = "2026-09-21T16:00:00Z"
        value = self.backend().snapshot()
        project = value["projects"][0]
        self.assertEqual(project["lastLaunchAt"], "2026-09-20T10:00:00Z")
        self.assertEqual(project["lastSessionRecordedEndedAt"], "2026-09-21T16:00:00Z")
        self.assertEqual(project["lastActiveAt"], project["lastLaunchAt"])
        self.assertNotEqual(project["lastLaunchAt"], value["observedAt"])
        self.assertEqual(value["sessions"], [])
        self.assertFalse(any(project["capabilities"].values()))

    def test_absent_or_recorded_end_never_invents_runtime_state_or_start(self):
        for ended in (None, "", "2026-09-21T16:00:00Z"):
            with self.subTest(ended=ended):
                self.projects[0].update(last_session_at="", last_session_ended_at=ended)
                self.status["sessions"][0].update(started_at=None, ended_at=ended, alive=False)
                value = self.backend().snapshot()
                project, session = value["projects"][0], value["sessions"][0]
                self.assertEqual(project["lastLaunchAt"], "")
                self.assertEqual(project["lastSessionRecordedEndedAt"], ended or "")
                self.assertEqual(session["startedAt"], "")
                self.assertEqual(session["createdAt"], "")
                self.assertEqual(session["recordedEndedAt"], ended or "")
                self.assertEqual(session["state"], "unknown")
                self.assertFalse(session["livenessVerified"])

    def test_session_start_retains_exact_record_and_timestamp_metadata_is_bounded(self):
        value = self.backend().snapshot()
        self.assertEqual(value["sessions"][0]["startedAt"], "2026-09-20T10:00:00Z")
        for value in ("x" * 81, "2026-09-20T10:00:00Z\n"):
            with self.subTest(value=value):
                self.projects[0]["last_session_ended_at"] = value
                self.assertEqual(self.backend().snapshot()["error"], "inventory-schema-invalid")

    def test_changed_source_added_file_and_registration_refuse_before_execution(self):
        changes = [(self.source / "__init__.py", "changed"), (self.source / "new_import.py", "new"), (self.support, "changed")]
        for path, text in changes:
            with self.subTest(path=path.name):
                previous = path.read_bytes() if path.exists() else None
                path.write_text(text)
                self.calls.clear()
                value = self.backend().snapshot()
                self.assertTrue(value["stale"])
                self.assertEqual(self.calls, [])
                if previous is None:
                    path.unlink()
                else:
                    path.write_bytes(previous)

    def test_changed_interpreter_and_replaced_state_are_refused(self):
        self.exe.write_text("changed executable")
        value = self.backend().snapshot()
        self.assertEqual(value["error"], "interpreter-bytes-changed")
        self.assertEqual(self.calls, [])
        self.exe.write_text("reviewed fixture executable bytes; never executed")
        self.state.rename(self.root / "former-state")
        self.state.mkdir()
        value = self.backend().snapshot()
        self.assertEqual(value["error"], "state-root-changed")
        self.assertEqual(self.calls, [])

    def test_runtime_tools_or_nonprivate_path_refuse_before_execution(self):
        (self.tools / "docker").write_text("unexpected")
        self.assertEqual(self.backend().snapshot()["error"], "tool-path-not-empty-private")
        (self.tools / "docker").unlink()
        self.tools.chmod(0o755)
        self.assertEqual(self.backend().snapshot()["error"], "tool-path-not-empty-private")
        self.assertEqual(self.calls, [])

    def test_identity_rechecked_after_command_before_accepting_output(self):
        def changed(argv, **kwargs):
            result = self.runner(argv, **kwargs)
            self.support.write_text("registration changed during call")
            return result
        value = self.backend(runner=changed).snapshot()
        self.assertEqual(value["error"], "installation-registration-changed")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(value["projects"], [])

    def test_duplicate_or_orphan_session_and_ambiguous_json_refused(self):
        for change in ("duplicate", "orphan", "json"):
            with self.subTest(change=change):
                original = json.loads(json.dumps(self.status))
                if change == "duplicate":
                    self.status["sessions"] *= 2
                elif change == "orphan":
                    self.status["sessions"][0]["project_uuid"] = "22222222-2222-4222-8222-222222222222"
                def runner(argv, **kwargs):
                    if change == "json":
                        return b'{"sessions":[],"sessions":[]}'
                    return self.runner(argv, **kwargs)
                self.assertTrue(self.backend(runner=runner).snapshot()["stale"])
                self.status = original

    def test_project_path_identity_is_not_assumed_from_registration(self):
        identity = self.project / ".botainer" / "project-id"
        identity.write_text("22222222-2222-4222-8222-222222222222")
        value = self.backend().snapshot()
        self.assertFalse(value["projects"][0]["pathIdentityVerified"])
        identity.unlink()
        identity.symlink_to(self.support)
        self.assertFalse(self.backend().snapshot()["projects"][0]["pathIdentityVerified"])
        self.projects[0]["last_path"] = "../../unexpected"
        self.assertEqual(self.backend().snapshot()["error"], "inventory-project-path-invalid")

    def test_registered_project_without_path_history_is_preserved_without_target(self):
        self.projects[0].update(last_path="(no path recorded)", display_name="", paths=[])
        value = self.backend().snapshot()
        self.assertFalse(value["stale"])
        self.assertEqual(value["projects"][0]["path"], "")
        self.assertFalse(value["projects"][0]["pathIdentityVerified"])
        self.assertFalse(any(value["projects"][0]["capabilities"].values()))

    def test_failure_preserves_previous_inventory_marks_stale_and_never_enables_actions(self):
        backend = self.backend()
        first = backend.snapshot()
        def failed(*args, **kwargs):
            raise InventoryError("inventory-command-timeout")
        backend._runner = failed
        second = backend.snapshot()
        self.assertTrue(second["stale"])
        self.assertEqual(second["projects"], first["projects"])
        self.assertEqual(second["sessions"][0]["state"], "unknown")
        self.assertTrue(second["sessions"][0]["stale"])
        self.assertFalse(first["sessions"][0].get("stale", False))
        for action in (lambda: backend.start_session("any", "r1"),
                       lambda: backend.stop_session("any", "any", "r2"),
                       lambda: backend.attach("any", "any", 80, 24)):
            with self.assertRaises(BackendUnavailable):
                action()

    def test_source_symlinks_refused_and_namespace_distinguishes_state_roots(self):
        (self.source / "imported.py").symlink_to(self.support)
        self.assertTrue(self.backend().snapshot()["stale"])
        self.assertEqual(self.calls, [])
        context = replace(self.profile.context, state_root=self.root / "different-state")
        other = replace(self.profile, context=context)
        self.assertNotEqual(self.profile.namespace, other.namespace)
        self.assertNotIn(str(self.state), self.profile.namespace)


if __name__ == "__main__":
    unittest.main()
