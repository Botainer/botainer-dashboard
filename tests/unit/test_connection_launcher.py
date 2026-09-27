"""Saved selection controls restart only; prerequisite checks are read-only."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from botainer_dashboard.connections import ConnectionRegistry, ConnectionRegistryError
from botainer_dashboard.launcher import select_connections_backend, EmptyConnectionsBackend
from botainer_dashboard.pairing import write_private_json

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("dashboard_connection_launcher_test", ROOT / "src/botainer_dashboard/cli.py")
dashboard = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = dashboard
SPEC.loader.exec_module(dashboard)
try:
    import fastapi
except ModuleNotFoundError:
    fastapi = None


def remote():
    required = ("pyproject.toml", "botainer/plugins/lifecycle.py", "botainer/cli/main.py", "botainer/cli/hpc.py",
                "botainer/cli/plugin.py", "botainer/plugins/manifest.py", "plugins/hpc-launcher/host_helper/submit.py",
                "plugins/hpc-launcher/host_helper/_common.py")
    return {"version": 1, "id": "example", "label": "Example", "ssh_alias": "example",
            "remote_python": "/opt/example/bin/python", "remote_python_sha256": "a" * 64,
            "source_root": "/opt/example/source", "state_root": "/home/example/.botainer",
            "launcher": {"path": "/home/example/bin/botainer", "sha256": "b" * 64},
            "source_sha256": {name: "c" * 64 for name in required}, "state_plugin_sha256": {},
            "control_root": "/home/example/.dashboard", "project_roots": {"work": "/home/example/projects"}}


class ConnectionLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / "connections.json"
        self.config = SimpleNamespace(machines={})

    def plan(self, *arguments):
        return dashboard.plan_for(dashboard.parser().parse_args(list(arguments)), root=self.root)

    def prepared_files(self):
        for filename in (".local/envs/botainer_dashboard/bin/python", "tools/run_dashboard.py",
                         *(".local/frontend/vendor/" + path for path in dashboard.VENDOR_ASSETS)):
            path = self.root / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("inert test file, never executed\n")
            path.chmod(0o700)

    def tree(self):
        return {str(path.relative_to(self.root)): (path.stat().st_mtime_ns,
                    path.read_bytes() if path.is_file() else None) for path in self.root.rglob("*")}

    def test_missing_registry_check_creates_no_files_or_locks_and_selects_no_trial(self):
        self.prepared_files()
        plan = self.plan("--connections", str(self.path), "--check")
        before = self.tree()
        with patch("subprocess.Popen", side_effect=AssertionError("check must not execute")), \
             patch("botainer_dashboard.connections.private_lock", side_effect=AssertionError("check must not write")):
            checks = dashboard.inspect_prerequisites(plan)
        self.assertTrue(all(ok for ok, _ in checks), checks)
        self.assertEqual(before, self.tree())
        self.assertFalse(self.path.exists())
        self.assertFalse(plan.local)
        self.assertEqual(plan.profiles, ())
        self.assertNotIn("--workspace", plan.argv())

    def test_existing_registry_check_reads_immutable_selection_without_lock_or_dispatch(self):
        self.prepared_files()
        registry = ConnectionRegistry(self.path)
        registry.initialize()
        value = registry.add("remote", remote(), 0, trusted=True)
        before = self.tree()
        with patch("subprocess.Popen", side_effect=AssertionError("check must not execute")), \
             patch("botainer_dashboard.connections.private_lock", side_effect=AssertionError("check must not lock")):
            plan = self.plan("--connections", str(self.path), "--check")
            selected = dashboard.selected_installations(plan)
            checks = dashboard.inspect_prerequisites(plan)
        self.assertEqual(selected["remote_profiles"], (Path(value["entries"][0]["profilePath"]),))
        self.assertTrue(all(ok for ok, _ in checks), checks)
        self.assertEqual(before, self.tree())

    def test_connections_cannot_accidentally_activate_legacy_trial_modes(self):
        for flags in (("--local-only",), ("--cluster-only",), ("--native-cli",), ("--cluster-profile", "old.json")):
            with self.subTest(flags=flags), self.assertRaisesRegex(ValueError, "cannot be mixed"):
                self.plan("--connections", str(self.path), *flags)

    def test_existing_selection_rejects_repeated_seed_instead_of_restoring_removed_machine(self):
        registry = ConnectionRegistry(self.path)
        registry.initialize()
        plan = self.plan("--connections", str(self.path), "--remote-profile", "old.json")
        with self.assertRaisesRegex(ValueError, "seed only a new selection"):
            dashboard.selected_installations(plan)
        self.assertEqual(registry.read()["entries"], [])

    @unittest.skipIf(fastapi is None, "requires the existing approved dashboard runtime")
    def test_empty_onboarding_never_constructs_an_installed_or_trial_backend(self):
        with patch("botainer_dashboard.launcher.select_installed_backend") as select, \
             patch("botainer_dashboard.launcher.select_backend") as trial:
            backend, mode, manager = select_connections_backend(self.root, self.config, self.path)
        self.assertIsInstance(backend, EmptyConnectionsBackend)
        self.assertEqual(backend.snapshot()["installations"], [])
        self.assertEqual(backend.snapshot()["projects"], [])
        self.assertFalse(manager.snapshot()["pendingRestart"])
        self.assertTrue(mode.startswith("connections-empty:"))
        select.assert_not_called()
        trial.assert_not_called()

    @unittest.skipIf(fastapi is None, "requires the existing approved dashboard runtime")
    def test_saved_add_and_remove_change_only_a_subsequent_launcher_selection(self):
        _, _, first = select_connections_backend(self.root, self.config, self.path)
        saved = first.registry.add("remote", remote(), 0, trusted=True)
        selected_profile = Path(saved["entries"][0]["profilePath"])
        self.assertTrue(first.snapshot()["pendingRestart"])
        controller = Mock()
        with patch("botainer_dashboard.launcher.select_installed_backend", return_value=(controller, "selected")) as select:
            backend, _, second = select_connections_backend(self.root, self.config, self.path)
        self.assertIs(backend, controller)
        select.assert_called_once_with(self.root, None, (selected_profile,), host_paths=(),
                                      protected_paths=(self.path,))
        self.assertFalse(second.snapshot()["pendingRestart"])
        second.registry.remove("example", 1)
        self.assertTrue(second.snapshot()["pendingRestart"])
        self.assertEqual(second.snapshot()["active"][0]["id"], "example")
        with patch("botainer_dashboard.launcher.select_installed_backend") as select:
            backend, _, third = select_connections_backend(self.root, self.config, self.path)
        self.assertIsInstance(backend, EmptyConnectionsBackend)
        self.assertFalse(third.snapshot()["pendingRestart"])
        self.assertTrue(selected_profile.is_file())
        select.assert_not_called()
        self.assertEqual(controller.mock_calls, [])

    @unittest.skipIf(fastapi is None, "requires the existing approved dashboard runtime")
    def test_initial_profile_import_preserves_exact_bytes_and_is_never_reapplied(self):
        original = self.root / "source-profile.json"
        write_private_json(original, remote())
        with patch("botainer_dashboard.launcher.select_installed_backend", return_value=(Mock(), "selected")):
            _, _, manager = select_connections_backend(self.root, self.config, self.path, remote_paths=[original])
        saved = manager.registry.read()
        self.assertEqual(Path(saved["entries"][0]["profilePath"]).read_bytes(), original.read_bytes())
        manager.registry.remove("example", 0)
        with patch("botainer_dashboard.launcher.select_installed_backend") as select:
            with self.assertRaises(ConnectionRegistryError):
                select_connections_backend(self.root, self.config, self.path, remote_paths=[original])
        self.assertEqual(manager.registry.read()["entries"], [])
        select.assert_not_called()

    @unittest.skipIf(fastapi is None, "requires the existing approved dashboard runtime")
    def test_modified_profile_refuses_before_any_runtime_controller(self):
        registry = ConnectionRegistry(self.path)
        registry.initialize()
        saved = registry.add("remote", remote(), 0, trusted=True)
        Path(saved["entries"][0]["profilePath"]).write_text(json.dumps({**remote(), "ssh_alias": "different"}))
        with patch("botainer_dashboard.launcher.select_installed_backend") as select:
            with self.assertRaises(ConnectionRegistryError) as caught:
                select_connections_backend(self.root, self.config, self.path)
        self.assertEqual(caught.exception.code, "connections-profile-changed")
        select.assert_not_called()


if __name__ == "__main__":
    unittest.main()
