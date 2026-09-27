"""Identity boundaries, tested with synthetic paths and no Botainer execution."""

from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from botainer_dashboard.context import (
    ContextError, ExecutionContext, Launcher, RuntimeTarget,
)


PROJECT_ID = "11111111-2222-4333-8444-555555555555"
SESSION_ID = "0123456789abcdef"


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="dashboard-context-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.executable = self.root / "botainer"
        self.executable.write_text("#!/bin/sh\nexit 97\n", encoding="utf-8")
        self.executable.chmod(0o700)
        self.project = self.root / "project"
        self.project.mkdir()
        self.context = ExecutionContext(
            "stable", "local", "demo-user", Launcher(self.executable), self.root / "state")

    def test_venv_interpreter_symlink_is_not_replaced_by_base_python(self):
        venv_bin = self.root / "venv" / "bin"
        venv_bin.mkdir(parents=True)
        selected = venv_bin / "python"
        selected.symlink_to(self.executable)
        launcher = Launcher(selected, mode="python_module")
        self.assertEqual(launcher.executable, selected)
        self.assertNotEqual(launcher.executable, selected.resolve())
        self.assertEqual(launcher.argv_prefix,
                         (str(selected), "-I", "-m", "botainer.cli.main"))

    def test_inventory_namespace_is_independent_of_binary_or_registration(self):
        other = self.root / "botainer-development"
        other.write_text("#!/bin/sh\nexit 98\n", encoding="utf-8")
        other.chmod(0o700)
        development = replace(self.context, context_id="development", launcher=Launcher(other))
        self.assertEqual(self.context.namespace_key, development.namespace_key)
        for changed in (replace(self.context, state_root=self.root / "other-state"),
                        replace(self.context, site_id="other-site"),
                        replace(self.context, account="another-user")):
            self.assertNotEqual(self.context.namespace_key, changed.namespace_key)

    def test_checkout_identity_spans_state_roots_and_resolves_directory_aliases(self):
        alias = self.root / "project-alias"
        alias.symlink_to(self.project, target_is_directory=True)
        left = RuntimeTarget(self.context, self.project, project_id=PROJECT_ID)
        other_context = replace(self.context, state_root=self.root / "other-state")
        right = RuntimeTarget(other_context, alias, project_id=PROJECT_ID)
        self.assertEqual(left.checkout_key, right.checkout_key)
        self.assertEqual(right.project_root, self.project)

    def test_context_and_nested_launcher_are_immutable_snapshots(self):
        target = RuntimeTarget(self.context, self.project, project_id=PROJECT_ID,
                               session_id=SESSION_ID)
        changed = replace(self.context, revision=2, state_root=self.root / "new-state")
        self.assertEqual(target.context.revision, 1)
        self.assertNotEqual(target.context, changed)
        with self.assertRaises(FrozenInstanceError):
            self.context.state_root = self.root / "changed"
        with self.assertRaises(FrozenInstanceError):
            self.context.launcher.mode = "python_module"
        with self.assertRaises(FrozenInstanceError):
            target.session_id = "aaaaaaaaaaaaaaaa"

    def test_constructing_context_does_not_create_or_initialize_state(self):
        before = set(self.root.rglob("*"))
        absent = self.root / "not-created" / "nested"
        context = replace(self.context, state_root=absent)
        RuntimeTarget(context, self.project, project_id=PROJECT_ID)
        self.assertEqual(before, set(self.root.rglob("*")))
        self.assertFalse(absent.exists())

    def test_launcher_rejects_missing_relative_and_nonexecutable_selection(self):
        plain = self.root / "not-executable"
        plain.write_text("not a program", encoding="utf-8")
        for selected in (Path("botainer"), self.root / "missing", plain, self.root):
            with self.subTest(selected=selected), self.assertRaises(ContextError):
                Launcher(selected)
        with self.assertRaises(ContextError):
            Launcher(self.executable, mode="shell")

    def test_project_paths_with_spaces_and_shell_punctuation_are_data(self):
        unusual = self.root / "my project;$(touch SHOULD_NOT_EXIST)"
        unusual.mkdir()
        target = RuntimeTarget(self.context, unusual, project_id=PROJECT_ID)
        self.assertEqual(target.project_root, unusual)
        self.assertFalse((self.root / "SHOULD_NOT_EXIST").exists())

    def test_state_root_is_canonical_but_not_claimed_compatible(self):
        actual = self.root / "state-actual"
        actual.mkdir()
        alias = self.root / "state-alias"
        alias.symlink_to(actual, target_is_directory=True)
        self.assertEqual(replace(self.context, state_root=alias).state_root, actual)
        for root in (Path("relative"), self.root / ".." / "other", self.root / "with space",
                     self.executable):
            with self.subTest(root=root), self.assertRaises(ContextError):
                replace(self.context, state_root=root)

    def test_full_session_id_is_required_and_injection_is_rejected(self):
        for session_id in ("0123", "--all", "../0123456789abcdef", "a" * 16 + "\n",
                           "A" * 16, "a" * 16 + ";id", 123):
            with self.subTest(session_id=session_id), self.assertRaises(ContextError):
                RuntimeTarget(self.context, self.project, project_id=PROJECT_ID,
                              session_id=session_id)
        with self.assertRaises(ContextError):
            RuntimeTarget(self.context, self.project, session_id=SESSION_ID)

    def test_project_identity_and_runtime_are_explicit(self):
        for project_id in ("project", "../state", PROJECT_ID + "\n", "--force"):
            with self.subTest(project_id=project_id), self.assertRaises(ContextError):
                RuntimeTarget(self.context, self.project, project_id=project_id)
        with self.assertRaises(ContextError):
            RuntimeTarget(self.context, self.project, runtime="auto")
        with self.assertRaises(ContextError):
            RuntimeTarget(self.context, self.root / "missing")
        with self.assertRaises(ContextError):
            replace(self.context, revision=True)
        with self.assertRaises(ContextError):
            replace(self.context, transport="ssh")

    def test_session_key_includes_namespace_project_and_full_id(self):
        target = RuntimeTarget(self.context, self.project, project_id=PROJECT_ID,
                               session_id=SESSION_ID)
        self.assertEqual(target.session_key,
                         (*self.context.namespace_key, PROJECT_ID, SESSION_ID))
        self.assertIsNone(RuntimeTarget(self.context, self.project).session_key)


if __name__ == "__main__":
    unittest.main()
