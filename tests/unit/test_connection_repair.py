"""Offline recovery-mode checks; no service, agent, Docker or SSH starts."""
import contextlib
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from botainer_dashboard.connections import ConnectionRegistry, ConnectionRegistryError
from botainer_dashboard import launcher
from botainer_dashboard.pairing import write_private_json


SOURCE = Path(__file__).resolve().parents[2] / "src/botainer_dashboard/cli.py"
SPEC = importlib.util.spec_from_file_location("dashboard_connection_repair_command", SOURCE)
command = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = command
SPEC.loader.exec_module(command)


class ConnectionRepairTests(unittest.TestCase):
    def setUp(self):
        base = "/private/tmp" if Path("/private/tmp").is_dir() else "/tmp"
        self.temp = tempfile.TemporaryDirectory(prefix="bd-rep-", dir=base)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.write(".local/envs/botainer_dashboard/bin/python", "inert interpreter fixture", 0o700)
        self.write("tools/run_dashboard.py", "# inert launcher fixture\n")
        for asset in command.VENDOR_ASSETS:
            self.write(".local/frontend/vendor/" + asset, "inert asset")
        self.write(".local/config.json", '{"version":1}')
        self.path = self.root / "connections.json"
        self.registry = ConnectionRegistry(self.path)
        self.config = SimpleNamespace(machines={})

    def write(self, name, text, mode=0o600):
        path = self.root / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(mode)
        return path

    def host(self):
        control = self.root / "c"
        control.mkdir(mode=0o700)
        projects = self.root / "projects"
        projects.mkdir(mode=0o700)
        self.binary = self.write("agent", "inert executable; never run\n", 0o700)
        tool = {"path": str(self.binary), "sha256": hashlib.sha256(self.binary.read_bytes()).hexdigest()}
        return {"version": 1, "id": "host", "label": "Host agent", "home": str(self.root),
                "tmux": tool, "project_roots": {"work": str(projects)}, "control_root": str(control),
                "agents": {"codex": dict(tool)}, "default_agent": "codex", "environment": {"PATH": "/usr/bin:/bin"}}

    def broken_registry(self):
        self.registry.initialize()
        saved = self.registry.add("host", self.host(), 0, trusted=True)
        self.binary.write_text("changed executable; never run\n")
        return saved

    def plan(self, *args):
        return command.plan_for(command.parser().parse_args(args), root=self.root)

    def tree(self):
        return {str(path.relative_to(self.root)): (path.stat().st_mtime_ns, path.read_bytes())
                for path in self.root.rglob("*") if path.is_file()}

    def test_setup_uses_default_selection_and_forwards_explicit_path_to_launcher(self):
        plan = self.plan("start", "--setup-only", "--open")
        self.assertEqual(plan.connections, self.root / command.DEFAULT_CONNECTIONS)
        self.assertFalse(plan.local)
        self.assertTrue(plan.setup_only)
        self.assertTrue(plan.open_browser)
        self.assertIn("--setup-only", plan.argv())
        index = plan.argv().index("--connections")
        self.assertEqual(plan.argv()[index + 1], str(self.root / command.DEFAULT_CONNECTIONS))

    def test_low_level_launcher_requires_explicit_connections(self):
        error = io.StringIO()
        with contextlib.redirect_stderr(error), self.assertRaises(SystemExit) as caught:
            launcher.main(["--setup-only"])
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("requires --connections", error.getvalue())

    def test_setup_with_explicit_runtime_route_requires_selection(self):
        for args in [("--local-only",), ("--cluster-only",), ("--native-cli",),
                     ("--host-profile", "host.json"), ("--remote-profile", "remote.json"),
                     ("--local-profile", "local.json")]:
            with self.subTest(args=args), self.assertRaisesRegex(ValueError, "requires --connections"):
                self.plan("--setup-only", *args)

    def test_repair_mode_never_selects_an_implicit_trial(self):
        self.write(".local/cluster-workspace/research.json", "{}")
        plan = self.plan("--setup-only", "--connections", str(self.path))
        self.assertFalse(plan.local)
        self.assertEqual(plan.profiles, ())
        self.assertTrue(plan.setup_only)
        self.assertIn("--setup-only", plan.argv())
        self.assertNotIn("--workspace", plan.argv())
        self.assertNotIn("--cluster-profile", plan.argv())
        for flag in ("--local-only", "--cluster-only", "--native-cli"):
            with self.subTest(flag=flag), self.assertRaisesRegex(ValueError, "cannot be mixed"):
                self.plan("--setup-only", "--connections", str(self.path), flag)

    def test_repair_selection_never_loads_changed_installation_or_runtime(self):
        saved = self.broken_registry()
        with patch("botainer_dashboard.launcher.select_installed_backend", side_effect=AssertionError("no controllers")), \
                patch("botainer_dashboard.host_backend.load_host_profile", side_effect=AssertionError("no installed-file validation")), \
                patch("subprocess.Popen", side_effect=AssertionError("no execution")), \
                patch("socket.socket", side_effect=AssertionError("no network")):
            backend, mode, manager = launcher.select_connections_backend(
                self.root, self.config, self.path, setup_only=True)
            snapshot = backend.snapshot()
            settings = manager.snapshot()
        self.assertEqual(snapshot["mode"], "connections-setup")
        self.assertEqual(snapshot["capabilities"], {})
        self.assertEqual(snapshot["sessions"], [])
        self.assertIn("restart without --setup-only", snapshot["notice"])
        self.assertTrue(mode.startswith("connections-setup:"))
        self.assertEqual([{key: value for key, value in entry.items() if key != "agents"}
                          for entry in settings["entries"]], saved["entries"])
        self.assertEqual(settings["entries"][0]["agents"][0]["id"], "codex")
        self.assertEqual(settings["active"], [])
        self.assertTrue(settings["pendingRestart"])

    def test_repair_can_disable_or_remove_broken_profile_without_touching_it(self):
        saved = self.broken_registry()
        profile_path = Path(saved["entries"][0]["profilePath"])
        profile_bytes = profile_path.read_bytes()
        backend, _, manager = launcher.select_connections_backend(self.root, self.config, self.path, setup_only=True)
        manager.registry.enable("host", False, saved["revision"])
        self.assertFalse(manager.snapshot()["pendingRestart"])
        manager.registry.remove("host", saved["revision"] + 1)
        self.assertEqual(manager.snapshot()["entries"], [])
        self.assertEqual(profile_path.read_bytes(), profile_bytes)
        self.assertEqual(self.binary.read_text(), "changed executable; never run\n")
        with self.assertRaisesRegex(RuntimeError, "runtime-not-enabled"):
            backend.start_session("anything")

    def test_normal_selection_still_refuses_changed_host_executable(self):
        self.broken_registry()
        with self.assertRaisesRegex(RuntimeError, "executable-changed"):
            launcher.select_connections_backend(self.root, self.config, self.path)

    def test_failed_normal_prerequisites_explain_the_gui_repair_route(self):
        self.broken_registry()
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(command.os, "execv") as execute:
            result = command.main(["--connections", str(self.path)], root=self.root)
        self.assertEqual(result, 2)
        execute.assert_not_called()
        self.assertIn("rerun with --setup-only", output.getvalue())

    def test_repair_pairing_mode_is_stable_and_distinct_from_normal_empty(self):
        self.registry.initialize()
        _, repair, _ = launcher.select_connections_backend(self.root, self.config, self.path, setup_only=True)
        _, again, _ = launcher.select_connections_backend(self.root, self.config, self.path, setup_only=True)
        _, normal, _ = launcher.select_connections_backend(self.root, self.config, self.path)
        self.assertEqual(repair, again)
        self.assertNotEqual(repair, normal)
        self.assertNotEqual(launcher.authentication_profile(self.root, None, repair),
                            launcher.authentication_profile(self.root, None, normal))

    def test_corrupt_profile_is_not_bypassed_by_repair_mode(self):
        saved = self.broken_registry()
        Path(saved["entries"][0]["profilePath"]).write_text("{}")
        with self.assertRaises(ConnectionRegistryError) as caught:
            launcher.select_connections_backend(self.root, self.config, self.path, setup_only=True)
        self.assertEqual(caught.exception.code, "connections-profile-changed")

    def test_seed_is_allowed_only_at_first_creation_even_in_repair_mode(self):
        profile = self.host()
        source = self.root / "host.json"
        write_private_json(source, profile)
        backend, _, manager = launcher.select_connections_backend(
            self.root, self.config, self.path, host_paths=[source], setup_only=True)
        self.assertEqual(manager.snapshot()["active"], [])
        self.assertEqual(len(manager.snapshot()["entries"]), 1)
        self.assertEqual(backend.snapshot()["sessions"], [])
        with self.assertRaises(ConnectionRegistryError) as caught:
            launcher.select_connections_backend(self.root, self.config, self.path, host_paths=[source], setup_only=True)
        self.assertEqual(caught.exception.code, "connections-already-initialized")

    def test_check_repair_ignores_changed_tool_and_writes_nothing(self):
        self.broken_registry()
        before = self.tree()
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(command.os, "execv") as execute, \
                patch("botainer_dashboard.host_backend.load_host_profile", side_effect=AssertionError("no installed-file validation")), \
                patch("subprocess.Popen", side_effect=AssertionError("no subprocess")), \
                patch("socket.socket", side_effect=AssertionError("no socket")):
            result = command.main(["--check", "--setup-only", "--connections", str(self.path)], root=self.root)
        self.assertEqual(result, 0)
        execute.assert_not_called()
        self.assertEqual(self.tree(), before)
        self.assertIn("intentionally unchecked", output.getvalue())
        self.assertIn("machine controls are inactive", output.getvalue())

    def test_check_missing_selection_does_not_create_directory_or_manifest(self):
        path = self.root / "new-private" / "connections.json"
        before = self.tree()
        with contextlib.redirect_stdout(io.StringIO()), patch.object(command.os, "execv") as execute:
            result = command.main(["--check", "--setup-only", "--connections", str(path)], root=self.root)
        self.assertEqual(result, 0)
        execute.assert_not_called()
        self.assertFalse(path.parent.exists())
        self.assertEqual(self.tree(), before)

    def test_check_corrupt_saved_selection_still_refuses(self):
        saved = self.broken_registry()
        Path(saved["entries"][0]["profilePath"]).write_text("{}")
        with contextlib.redirect_stdout(io.StringIO()), patch.object(command.os, "execv") as execute:
            result = command.main(["--check", "--setup-only", "--connections", str(self.path)], root=self.root)
        self.assertEqual(result, 2)
        execute.assert_not_called()

    def test_check_new_seed_reads_private_document_but_not_changed_installation(self):
        source = self.root / "host.json"
        write_private_json(source, self.host())
        self.binary.unlink()
        before = self.tree()
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(command.os, "execv") as execute:
            result = command.main(["--check", "--setup-only", "--connections", str(self.path),
                                   "--host-profile", str(source)], root=self.root)
        self.assertEqual(result, 0)
        execute.assert_not_called()
        self.assertFalse(self.path.exists())
        self.assertEqual(self.tree(), before)
        self.assertIn("validation occurs during first import", output.getvalue())
        source.chmod(0o644)
        with contextlib.redirect_stdout(io.StringIO()):
            result = command.main(["--check", "--setup-only", "--connections", str(self.path),
                                   "--host-profile", str(source)], root=self.root)
        self.assertEqual(result, 2)

    def test_command_forwards_repair_flag_without_starting_runtime_in_prerequisites(self):
        self.broken_registry()
        args = ["--setup-only", "--connections", str(self.path), "--open"]
        plan = self.plan(*args)
        with contextlib.redirect_stdout(io.StringIO()), patch.object(command.os, "execv") as execute:
            self.assertIsNone(command.main(args, root=self.root))
        execute.assert_called_once_with(str(plan.interpreter), plan.argv())
        self.assertIn("--setup-only", execute.call_args.args[1])

    def test_default_setup_command_uses_saved_broken_selection_without_running_it(self):
        self.path = self.root / command.DEFAULT_CONNECTIONS
        self.registry = ConnectionRegistry(self.path)
        self.broken_registry()
        before = self.tree()
        plan = self.plan("start", "--setup-only", "--open")
        with contextlib.redirect_stdout(io.StringIO()), patch.object(command.os, "execv") as execute, \
                patch("botainer_dashboard.host_backend.load_host_profile", side_effect=AssertionError("no installed-file validation")), \
                patch("subprocess.Popen", side_effect=AssertionError("no subprocess")), \
                patch("socket.socket", side_effect=AssertionError("no socket")):
            result = command.main(["start", "--setup-only", "--open"], root=self.root)
        self.assertIsNone(result)
        execute.assert_called_once_with(str(plan.interpreter), plan.argv())
        self.assertEqual(self.tree(), before)


if __name__ == "__main__":
    unittest.main()
