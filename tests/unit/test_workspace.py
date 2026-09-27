"""Filesystem boundaries and save races using disposable standard-library data."""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from botainer_dashboard.workspace import (
    MAX_TEXT_BYTES, Workspace, WorkspaceError, WorkspaceInstallation, WorkspaceRoot,
)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="dashboard-workspace-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.projects_root = self.base / "projects"
        self.projects_root.mkdir()
        self.private = self.base / "private"
        self.allowed = True
        self.validator_action = None
        self.calls = []
        self.installation = WorkspaceInstallation("local", "Reviewed installation", self.validate, "version: config-v1\n# Preserve this comment.\n")
        self.workspace = self.new_workspace()

    def validate(self, text, previous):
        self.calls.append((text, previous))
        if self.validator_action:
            self.validator_action()
        return {"valid": not text.startswith("invalid"), "errors": [] if not text.startswith("invalid") else ["Config is invalid."], "warnings": []}

    def new_workspace(self):
        return Workspace(private_root=self.private,
                         roots=[WorkspaceRoot("projects", "Projects", self.projects_root)],
                         installations=[self.installation], can_write=lambda _id: self.allowed)

    def create(self, **kwargs):
        return self.workspace.create_project(**({"mode": "create", "root_id": "projects", "path": "sample",
                     "name": "Sample", "installation_id": "local", "request_id": str(uuid4())} | kwargs))

    def config_path(self, project):
        return Path(project["path"]) / ".botainer" / "config.yaml"

    def test_create_persists_and_reloads_registered_identity(self):
        project = self.create()
        self.assertEqual(self.config_path(project).read_text(), self.installation.initial_text)
        self.assertEqual(self.new_workspace().project(project["id"]), project)
        self.assertEqual(len(self.calls), 1)
        self.assertIsNone(self.calls[0][1])
        self.assertEqual(self.workspace.projects(), [project])
        self.assertEqual(self.private.joinpath("projects.json").stat().st_mode & 0o777, 0o600)

    def test_internal_exact_project_root_is_hidden_from_setup_and_bounds_file_access(self):
        project = self.create()
        path = Path(project["path"])
        exact = WorkspaceRoot("native-example", "Existing project", path, project_only=True)
        self.workspace.register_project_root(exact)
        registry = self.workspace._registry()
        registry["projects"][0].update(rootId=exact.id, relativePath="")
        self.workspace._persist(registry)
        self.assertEqual(self.workspace.read_config(project["id"])["text"], self.installation.initial_text)
        self.assertEqual([root["id"] for root in self.workspace.metadata()["roots"]], ["projects"])
        for mode in ("create", "register"):
            with self.assertRaisesRegex(WorkspaceError, "project-root-not-for-setup"):
                self.create(mode=mode, root_id=exact.id, path="nested")
        self.assertFalse((path / "nested").exists())
        with self.assertRaises(WorkspaceError): self.workspace.read_file(project["id"], "../sibling")
        registry["projects"][0]["relativePath"] = "nested"
        self.workspace._persist(registry)
        with self.assertRaisesRegex(WorkspaceError, "exact-project-path-required"):
            self.workspace.read_config(project["id"])

    def test_empty_project_path_is_not_allowed_for_an_ordinary_setup_root(self):
        project = self.create()
        registry = self.workspace._registry(); registry["projects"][0]["relativePath"] = ""
        self.workspace._persist(registry)
        with self.assertRaisesRegex(WorkspaceError, "invalid-relative-path"):
            self.workspace.read_config(project["id"])

    def test_internal_exact_root_cannot_be_retargeted_or_replaced(self):
        project = self.create()
        path = Path(project["path"])
        exact = WorkspaceRoot("native-example", "Existing project", path, project_only=True)
        self.workspace.register_project_root(exact)
        self.workspace.register_project_root(exact)
        with self.assertRaisesRegex(WorkspaceError, "project-root-changed"):
            self.workspace.register_project_root(WorkspaceRoot(exact.id, "Elsewhere", self.base, project_only=True))
        path.rename(path.with_name("moved")); path.mkdir()
        with self.assertRaisesRegex(WorkspaceError, "root-identity-changed"):
            self.workspace.register_project_root(exact)

    def test_create_is_idempotent_and_request_collision_refuses(self):
        request = str(uuid4())
        project = self.create(request_id=request)
        self.assertEqual(self.create(request_id=request), project)
        with self.assertRaisesRegex(WorkspaceError, "request-id-conflict"):
            self.create(path="other", request_id=request)
        self.assertFalse(self.projects_root.joinpath("other").exists())

    def test_create_never_overwrites_existing_folder_or_invalid_defaults(self):
        self.projects_root.joinpath("sample").mkdir()
        with self.assertRaisesRegex(WorkspaceError, "folder-already-exists"):
            self.create()
        other = Workspace(private_root=self.base / "other-private",
                          roots=[WorkspaceRoot("projects", "Projects", self.projects_root)],
                          installations=[WorkspaceInstallation("local", "Invalid", self.validate, "invalid")])
        with self.assertRaisesRegex(WorkspaceError, "initial-config-invalid"):
            other.create_project(mode="create", root_id="projects", path="untouched", name="Untouched", installation_id="local")
        self.assertFalse(self.projects_root.joinpath("untouched").exists())

    def test_register_existing_project_without_rewriting_files(self):
        project = self.projects_root / "existing"
        managed = project / ".botainer"
        managed.mkdir(parents=True)
        (managed / "project-id").write_text(str(uuid4()) + "\n")
        (managed / "config.yaml").write_text("# Hand-edited\nversion: config-v1\n")
        previous = (managed / "config.yaml").read_bytes()
        result = self.create(mode="register", path="existing")
        self.assertEqual(result["relativePath"], "existing")
        self.assertEqual((managed / "config.yaml").read_bytes(), previous)
        self.assertEqual(self.calls, [])

    def test_traversal_and_symlink_parents_are_refused(self):
        outside = self.base / "outside"
        outside.mkdir()
        self.projects_root.joinpath("linked").symlink_to(outside, target_is_directory=True)
        for path in ("../escape", "/absolute", "a/../escape", ".hidden", "a//b", "a\\b", "linked/escape"):
            with self.subTest(path=path), self.assertRaises(WorkspaceError):
                self.create(path=path)
        self.assertEqual(list(outside.iterdir()), [])

    def test_malformed_unicode_never_creates_a_project_or_registration(self):
        for options in ({"name": "\ud800"}, {"path": "\ud800"}):
            with self.subTest(options=options), self.assertRaises(WorkspaceError):
                self.create(**options)
        self.assertEqual(self.workspace.projects(), [])
        self.assertEqual(list(self.projects_root.iterdir()), [])

    def test_replacing_registered_directory_or_uuid_is_refused(self):
        project = self.create()
        directory = Path(project["path"])
        directory.rename(directory.with_name("original"))
        directory.mkdir()
        with self.assertRaisesRegex(WorkspaceError, "project-identity-changed"):
            self.workspace.read_config(project["id"])
        directory.rmdir()
        directory.with_name("original").rename(directory)
        directory.joinpath(".botainer", "project-id").write_text(str(uuid4()) + "\n")
        with self.assertRaisesRegex(WorkspaceError, "project-identity-changed"):
            self.workspace.read_file(project["id"], "anything")

    def test_folder_view_and_text_read_exclude_secret_and_linked_files(self):
        project = self.create()
        directory = Path(project["path"])
        directory.joinpath("src").mkdir()
        directory.joinpath("README.md").write_text("<script>data</script>\nλ\n")
        directory.joinpath(".env").write_text("SECRET=private")
        directory.joinpath("credentials.json").write_text("private")
        directory.joinpath("server.pem").write_text("private")
        directory.joinpath("alias").symlink_to(directory / "README.md")
        linked = directory / "linked.txt"
        external = self.base / "external.txt"
        external.write_text("private")
        os.link(external, linked)
        listing = self.workspace.list_files(project["id"])
        self.assertEqual([item["name"] for item in listing["entries"]], ["src", "README.md"])
        self.assertEqual(listing["entries"][0]["kind"], "directory")
        self.assertIn("<script>", self.workspace.read_file(project["id"], "README.md")["text"])
        for name in (".env", "credentials.json", "server.pem", "alias", "linked.txt", ".botainer/config.yaml"):
            with self.subTest(name=name), self.assertRaises(WorkspaceError):
                self.workspace.read_file(project["id"], name)

    def test_binary_and_oversized_file_reads_are_refused(self):
        project = self.create()
        directory = Path(project["path"])
        for name, content in (("binary", b"\xff\xfe"), ("nul", b"\x00"), ("large", b"x" * (MAX_TEXT_BYTES + 1))):
            directory.joinpath(name).write_bytes(content)
            with self.subTest(name=name), self.assertRaises(WorkspaceError):
                self.workspace.read_file(project["id"], name)

    def test_exact_text_save_and_revision_conflict(self):
        project = self.create()
        before = self.workspace.read_config(project["id"])
        text = "# Keep comments and spacing.\nversion: config-v1\n\n"
        result = self.workspace.save_config(project["id"], text, before["revision"])
        self.assertTrue(result["saved"])
        self.assertEqual(self.config_path(project).read_bytes(), text.encode())
        self.assertEqual(self.calls[-1], (text, before["text"]))
        with self.assertRaisesRegex(WorkspaceError, "config-revision-conflict"):
            self.workspace.save_config(project["id"], text + "# old editor", before["revision"])

    def test_validation_failure_or_live_unknown_state_never_writes(self):
        project = self.create()
        before = self.workspace.read_config(project["id"])
        result = self.workspace.save_config(project["id"], "invalid", before["revision"])
        self.assertFalse(result["saved"])
        self.assertEqual(self.config_path(project).read_text(), before["text"])
        self.allowed = False
        self.assertFalse(self.workspace.read_config(project["id"])["writable"])
        with self.assertRaisesRegex(WorkspaceError, "active-or-unknown"):
            self.workspace.save_config(project["id"], "new", before["revision"])
        self.assertEqual(self.config_path(project).read_text(), before["text"])

    def test_external_edit_during_validation_is_not_overwritten(self):
        project = self.create()
        before = self.workspace.read_config(project["id"])
        self.validator_action = lambda: self.config_path(project).write_text("External edit\n")
        with self.assertRaisesRegex(WorkspaceError, "config-revision-conflict"):
            self.workspace.save_config(project["id"], "Dashboard edit\n", before["revision"])
        self.assertEqual(self.config_path(project).read_text(), "External edit\n")

    def test_runtime_becoming_uncertain_during_validation_is_not_written(self):
        project = self.create()
        before = self.workspace.read_config(project["id"])
        self.validator_action = lambda: setattr(self, "allowed", False)
        with self.assertRaisesRegex(WorkspaceError, "active-or-unknown"):
            self.workspace.save_config(project["id"], "new", before["revision"])
        self.assertEqual(self.config_path(project).read_text(), before["text"])

    def test_external_edit_during_final_lifecycle_check_is_preserved(self):
        project = self.create(); before = self.workspace.read_config(project["id"])
        checks = []
        def can_write(_project_id):
            checks.append(True)
            if len(checks) == 2:
                self.config_path(project).write_text("External edit\n")
            return True
        self.workspace.can_write = can_write
        with self.assertRaisesRegex(WorkspaceError, "config-revision-conflict"):
            self.workspace.save_config(project["id"], "Dashboard draft\n", before["revision"])
        self.assertEqual(self.config_path(project).read_text(), "External edit\n")
        recovery = json.loads(next((self.private / "config-recovery").glob("slot-*.json")).read_text())
        self.assertEqual(recovery["base"], before["text"])
        self.assertEqual(recovery["draft"], "Dashboard draft\n")

    def test_recovery_storage_failure_prevents_config_replacement(self):
        from botainer_dashboard.config_recovery import ConfigRecoveryError
        project = self.create(); before = self.workspace.read_config(project["id"])
        with patch("botainer_dashboard.workspace.preserve_config", side_effect=ConfigRecoveryError("config-recovery-unavailable")):
            with self.assertRaisesRegex(WorkspaceError, "config-recovery-unavailable"):
                self.workspace.save_config(project["id"], "Dashboard draft\n", before["revision"])
        self.assertEqual(self.config_path(project).read_text(), before["text"])

    def test_config_symlink_is_not_followed_or_replaced(self):
        project = self.create()
        config = self.config_path(project)
        external = self.base / "outside-config"
        config.rename(external)
        config.symlink_to(external)
        before = external.read_bytes()
        with self.assertRaises(WorkspaceError):
            self.workspace.read_config(project["id"])
        self.assertEqual(external.read_bytes(), before)
        self.assertTrue(config.is_symlink())

    def test_second_dashboard_uses_revision_and_private_registry_lock(self):
        project = self.create()
        other = self.new_workspace()
        before = other.read_config(project["id"])
        self.workspace.save_config(project["id"], "First dashboard edit", before["revision"])
        with self.assertRaisesRegex(WorkspaceError, "config-revision-conflict"):
            other.save_config(project["id"], "Second dashboard edit", before["revision"])
        self.assertEqual(other.projects(), [project])


if __name__ == "__main__":
    unittest.main()
