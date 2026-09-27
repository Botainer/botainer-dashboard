"""Protected local paths cannot become container project data; inert fixtures."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from botainer_dashboard.backend_errors import BackendUnavailable
from botainer_dashboard.ordinary_local import OrdinaryLocalBackend
from botainer_dashboard.project_boundaries import protected_local_paths, require_separate_project
from botainer_dashboard.workspace import WorkspaceRoot
from test_ordinary_local import OrdinaryFixture, UID, REQUEST, helper, sha, write_private_json
import test_ordinary_local as fixtures


class ProtectedProjects(OrdinaryFixture):
    def test_changed_control_validation_module_refuses_before_native_observation(self):
        original = sha
        def digest(path):
            if Path(path) == self.backend._control_module:
                return "0" * 64
            return original(path)
        # Simulate a module replacement without editing installed source. A
        # retained service must not spawn a helper using different validation.
        with patch("botainer_dashboard.ordinary_local.sha", side_effect=digest):
            with self.assertRaisesRegex(BackendUnavailable, "changed-restart-required"):
                self.backend._call("inventory")
        self.runner.assert_not_called()
        self.assertFalse(list(self.backend.root.glob("query-*.json")))
        self.backend.verify()

    def test_short_private_control_inside_project_is_never_granted(self):
        control = self.project / ".c"; control.mkdir(mode=0o700)
        self.assertLessEqual(len(bytes(control)), 55)
        self.profile_data["terminal_owner"] = {"path": str(self.root / "bin/python"),
            "sha256": sha(self.root / "bin/python"), "control_root": str(control)}
        write_private_json(self.profile_path, self.profile_data)
        backend = OrdinaryLocalBackend(self.application, self.profile_path, runner=self.runner)
        project = backend.snapshot()["projects"][0]
        self.assertEqual(project["controlRestriction"], "protected-project-path")
        for action in ("filesRead", "configRead", "configWrite", "startSession"):
            self.assertFalse(project["capabilities"][action])
        with self.assertRaisesRegex(BackendUnavailable, "overlaps-protected"):
            helper.project_check({"project_path": str(self.project), "project_uuid": UID}, backend.profile)
        self.assertEqual(backend._operations()["operations"], [])

    def test_broad_setup_parent_still_allows_safe_sibling_project(self):
        self.profile_data["project_roots"][0]["path"] = str(self.root)
        write_private_json(self.profile_path, self.profile_data)
        backend = OrdinaryLocalBackend(self.application, self.profile_path, runner=self.runner)
        self.assertTrue(backend.snapshot()["projects"][0]["capabilities"]["startSession"])

    def test_application_state_native_state_and_other_owner_are_protected(self):
        another_control = self.root / "other-owner"; another_control.mkdir(mode=0o700)
        backend = OrdinaryLocalBackend(self.application, self.profile_path, runner=self.runner,
                                       protected_paths=[another_control])
        for target in (self.application, self.backend.root, self.root / "state",
                       self.root / "source", self.root, another_control):
            with self.subTest(target=target.name), self.assertRaisesRegex(BackendUnavailable, "overlaps-protected"):
                require_separate_project(target, backend.protected_paths)
        self.assertEqual(require_separate_project(self.project, backend.protected_paths), self.project)

    def test_creation_refuses_protected_descendant_before_mkdir_or_dispatch(self):
        self.backend.workspace.roots["broad"] = WorkspaceRoot("broad", "Broad parent", self.root)
        target = self.application / "new"
        with self.assertRaisesRegex(BackendUnavailable, "overlaps-protected"):
            self.backend.create_project({"mode": "create", "installationId": "local", "rootId": "broad",
                "path": "dashboard/new", "name": "New", "requestId": REQUEST})
        self.assertFalse(target.exists())
        self.assertEqual(self.consoles, [])

    def test_cached_registration_cannot_reopen_new_protected_target(self):
        project = self.backend.snapshot()["projects"][0]
        control = self.project / "control"; control.mkdir(mode=0o700)
        self.backend.protected_paths += (control,)
        actions = [lambda: self.backend.read_config(project["id"]),
                   lambda: self.backend.list_files(project["id"], ""),
                   lambda: self.backend.read_file(project["id"], "file.txt"),
                   lambda: self.backend.save_config(project["id"], "agent: codex\n", "old"),
                   lambda: self.backend._start_console(operation="start", project=project, request=REQUEST)]
        for action in actions:
            with self.assertRaisesRegex(BackendUnavailable, "overlaps-protected"):
                action()
        self.assertEqual(self.consoles, [])

    def test_alias_to_protected_target_does_not_evade_comparison(self):
        alias = self.root / "alias"; alias.symlink_to(self.application, target_is_directory=True)
        with self.assertRaisesRegex(BackendUnavailable, "overlaps-protected"):
            require_separate_project(alias / "child", self.backend.protected_paths)

    def test_runtime_uncertainty_does_not_hide_protected_folder_explanation(self):
        self.backend.protected_paths += (self.project / "private-control",)
        self.raw["projects"][0]["runtime_unverified"] = True
        project = self.backend.snapshot()["projects"][0]
        self.assertEqual(project["controlRestriction"], "protected-project-path")

    def test_installed_resources_protect_package_environment_and_separate_private_data(self):
        environment = self.root / "dashboard-env"
        base = self.root / "dashboard-python"
        (base / "bin").mkdir(parents=True)
        python = base / "bin/python"; python.write_text("# inert base interpreter\n")
        (environment / "bin").mkdir(parents=True)
        selected = environment / "bin/python"; selected.symlink_to(python)
        package = environment / "lib/python3.13/site-packages/botainer_dashboard"
        resources = package / "_resources"
        resources.mkdir(parents=True)
        private_data = self.root / "dashboard-data"; private_data.mkdir()
        with patch("botainer_dashboard.project_boundaries.sys.prefix", str(environment)), \
                patch("botainer_dashboard.project_boundaries.sys.base_prefix", str(base)), \
                patch("botainer_dashboard.project_boundaries.sys.executable", str(selected)):
            protected = protected_local_paths(self.backend.profile, resources, data_directory=private_data)
        # A project cannot enclose or live anywhere in the installed dashboard
        # environment, including executable imports outside packaged resources.
        for target in (environment, environment / "bin/project", package / "project",
                       resources / "project", private_data, private_data / "new", self.root,
                       python, base / "bin/project", base / "lib/project", base / "lib64/project"):
            with self.subTest(target=target), self.assertRaisesRegex(BackendUnavailable, "overlaps-protected"):
                require_separate_project(target, protected)
        self.assertEqual(require_separate_project(self.project, protected), self.project)
        safe = base / "projects/work"
        self.assertEqual(require_separate_project(safe, protected), safe)

    def test_ordinary_tmux_executable_outside_support_pins_is_protected(self):
        folder = self.root / "terminal-program"; folder.mkdir()
        tool = folder / "tmux"; tool.write_text("# inert tmux\n"); tool.chmod(0o700)
        self.profile_data["terminal_owner"] = {"path": str(tool), "sha256": sha(tool),
            "control_root": str(self.root / "terminal-control")}
        write_private_json(self.profile_path, self.profile_data)
        backend = OrdinaryLocalBackend(self.application, self.profile_path, runner=self.runner)
        for target in (tool, folder):
            with self.subTest(target=target), self.assertRaisesRegex(BackendUnavailable, "overlaps-protected"):
                require_separate_project(target, backend.protected_paths)
        self.assertEqual(require_separate_project(self.project, backend.protected_paths), self.project)
        self.runner.assert_not_called()

    def test_custom_docker_socket_and_aliased_endpoint_are_protected(self):
        folder = self.root / "engine-control"; folder.mkdir()
        # No socket or daemon is created: the pathname is sufficient to enforce
        # containment before any mount or runtime operation.
        endpoint = folder / "engine.sock"
        alias = self.root / "engine-alias"; alias.symlink_to(folder, target_is_directory=True)
        profile = SimpleNamespace(path=self.profile_path, data={**self.profile_data,
            "docker": {**self.profile_data["docker"], "host": "unix://" + str(alias / "engine.sock")}})
        protected = protected_local_paths(profile, self.application)
        for target in (endpoint, folder, alias, alias / "engine.sock"):
            with self.subTest(target=target), self.assertRaisesRegex(BackendUnavailable, "overlaps-protected"):
                require_separate_project(target, protected)
        self.assertEqual(require_separate_project(self.project, protected), self.project)

    def test_conda_or_base_python_code_is_protected_without_marking_whole_parent_as_code(self):
        prefix = self.root / "native-env"
        (prefix / "bin").mkdir(parents=True)
        python = prefix / "bin/python"; python.write_text("# inert interpreter fixture\n")
        profile = SimpleNamespace(path=self.profile_path, data={**self.profile_data, "python": str(python)})
        self.assertFalse((prefix / "pyvenv.cfg").exists())
        protected = protected_local_paths(profile, self.application)
        for target in (prefix, python, prefix / "bin/project", prefix / "lib/project", prefix / "lib64/project"):
            with self.subTest(target=target), self.assertRaisesRegex(BackendUnavailable, "overlaps-protected"):
                require_separate_project(target, protected)
        # An installation's broad parent is not itself a launch allowlist or
        # blanket exclusion: a disjoint child project remains possible.
        safe = prefix / "projects/work"
        self.assertEqual(require_separate_project(safe, protected), safe)
        self.assertEqual(require_separate_project(self.project, protected), self.project)

    def test_symlinked_venv_interpreter_protects_both_entry_and_resolved_base_code(self):
        environment, base = self.root / "venv", self.root / "base-python"
        for prefix in (environment, base): (prefix / "bin").mkdir(parents=True)
        base_python = base / "bin/python"; base_python.write_text("# inert base interpreter\n")
        selected = environment / "bin/python"; selected.symlink_to(base_python)
        profile = SimpleNamespace(path=self.profile_path, data={**self.profile_data, "python": str(selected)})
        protected = protected_local_paths(profile, self.application)
        for prefix in (environment, base):
            for directory in ("bin", "lib", "lib64"):
                with self.subTest(prefix=prefix, directory=directory), \
                        self.assertRaisesRegex(BackendUnavailable, "overlaps-protected"):
                    require_separate_project(prefix / directory / "new-project", protected)
        self.assertEqual(require_separate_project(self.project, protected), self.project)
        self.assertTrue(selected.is_symlink())

    def test_pinned_helper_outside_source_or_state_cannot_be_enclosed_by_project(self):
        folder = self.root / "external-helper"; folder.mkdir()
        tool = folder / "broker.py"; tool.write_text("# trusted helper fixture\n")
        profile = SimpleNamespace(path=self.profile_path, data={**self.profile_data,
            "support_hashes": {**self.profile_data["support_hashes"], str(tool): sha(tool)}})
        protected = protected_local_paths(profile, self.application)
        with self.assertRaisesRegex(BackendUnavailable, "overlaps-protected"):
            require_separate_project(folder, protected)
        safe = folder / "separate-project"
        self.assertEqual(require_separate_project(safe, protected), safe)

    def test_protected_path_replaced_with_alias_into_project_is_rechecked(self):
        control = self.root / "other-control"; control.mkdir()
        protected = protected_local_paths(self.backend.profile, self.application, extra=(control,))
        self.assertEqual(require_separate_project(self.project, protected), self.project)
        control.rmdir()
        control.symlink_to(self.project / "nested", target_is_directory=True)
        with self.assertRaisesRegex(BackendUnavailable, "overlaps-protected"):
            require_separate_project(self.project, protected)
        self.assertTrue(control.is_symlink())


class NativeMountBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.NativeCliTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)

    def test_user_extra_mount_cannot_expose_controls_even_read_only(self):
        f = self.f
        f.data["protected_paths"] = [str(f.backend.root)]
        f.spec.mount_plan.binds = [SimpleNamespace(source=str(f.backend.root), provenance="user", mode="ro")]
        with self.assertRaisesRegex(BackendUnavailable, "overlaps-protected"):
            f.execute(lambda argv: f.composition.launch(f.spec, detach=True))
        f.original.assert_not_called()

    def test_reviewed_native_core_and_plugin_mounts_preserve_original_policy(self):
        f = self.f
        f.spec.mount_plan.binds = [SimpleNamespace(source=str(f.root / "state"), provenance=kind)
                                  for kind in ("core", "plugin")]
        helper.check_user_mount_boundaries(f.spec, f.data, f.profile)
        f.original.assert_not_called()

    def test_dispatch_rechecks_after_composition(self):
        f = self.f
        def cli(argv):
            f.composition.compose_session()
            f.spec.mount_plan.binds = [SimpleNamespace(source=str(f.root / "state"), provenance="user")]
            f.composition.launch(f.spec, detach=True)
        with self.assertRaisesRegex(BackendUnavailable, "overlaps-protected"):
            f.execute(cli)
        f.original.assert_not_called()


if __name__ == "__main__": unittest.main()
