"""Audited argv contracts and refusal boundaries; no subprocesses are run."""

from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from botainer_dashboard.commands import CommandError, LocalCommandBuilder
from botainer_dashboard.context import ContextError, ExecutionContext, Launcher, RuntimeTarget


PROJECT_ID = "11111111-2222-4333-8444-555555555555"
SESSION_ID = "0123456789abcdef"


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="dashboard-commands-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.executable = self.root / "chosen-botainer"
        self.executable.write_text("#!/bin/sh\nexit 97\n", encoding="utf-8")
        self.executable.chmod(0o700)
        self.project = self.root / "project; with spaces"
        self.project.mkdir()
        self.context = ExecutionContext(
            "stable", "local", "demo-user", Launcher(self.executable), self.root / "state")
        self.builder = LocalCommandBuilder(self.context, self.root)
        self.target = RuntimeTarget(self.context, self.project, project_id=PROJECT_ID)
        self.session = replace(self.target, session_id=SESSION_ID)

    def test_inventory_uses_neutral_cwd_and_existing_global_json_commands(self):
        projects = self.builder.project_inventory()
        sessions = self.builder.session_inventory()
        self.assertEqual(projects.argv, (str(self.executable), "list", "--json"))
        self.assertEqual(sessions.argv,
                         (str(self.executable), "status", "--global", "--all", "--json"))
        self.assertEqual(projects.cwd, self.root)
        self.assertEqual(sessions.cwd, self.root)

    def test_attach_and_stop_pin_full_session_and_project_scope(self):
        attach = self.builder.attach_session(self.session)
        stop = self.builder.stop_session(self.session)
        self.assertEqual(attach.argv, (str(self.executable), "attach", "--", SESSION_ID))
        self.assertEqual(stop.argv, (str(self.executable), "stop", "--", SESSION_ID))
        self.assertEqual(attach.cwd, self.project)
        self.assertEqual(stop.cwd, self.project)
        self.assertTrue(attach.terminal)
        self.assertFalse(stop.terminal)
        self.assertNotIn("--all", stop.argv)
        self.assertNotIn("--force", stop.argv)

    def test_start_is_explicit_detached_json_without_setup_or_implicit_consent(self):
        plan = self.builder.start_session(self.target)
        self.assertEqual(plan.argv, (str(self.executable), "start", "--runtime=docker",
                                     "--detach", "--json", "--no-auto-onboard"))
        self.assertNotIn("--yes", plan.argv)
        self.assertNotIn("--fork", plan.argv)
        self.assertNotIn("--accept-identity-change", plan.argv)
        self.assertFalse(plan.terminal)
        self.assertEqual(plan.cwd, self.project)

    def test_capability_confirmation_is_a_separate_explicit_choice(self):
        self.assertIn("--yes", self.builder.start_session(
            self.target, confirm_capabilities=True).argv)
        for value in (1, "yes", None):
            with self.subTest(value=value), self.assertRaises(CommandError):
                self.builder.start_session(self.target, confirm_capabilities=value)

    def test_init_does_not_force_rewrite_or_turn_name_into_options(self):
        new_project = replace(self.target, project_id=None)
        name = "--force; $(touch SHOULD_NOT_EXIST)"
        plan = self.builder.init_project(new_project, agent="claude", name=name)
        self.assertEqual(plan.argv, (str(self.executable), "init", "--non-interactive",
                                     "--agent=claude", "--runtime=docker", f"--name={name}"))
        self.assertNotIn("--force", plan.argv)
        self.assertEqual(plan.cwd, self.project)
        self.assertFalse((self.root / "SHOULD_NOT_EXIST").exists())

    def test_init_does_not_accept_arbitrary_agent_option_or_control_characters(self):
        for agent in ("--force", "claude;id", "claude\n", "$(id)"):
            with self.subTest(agent=agent), self.assertRaises(CommandError):
                self.builder.init_project(self.target, agent=agent)
        with self.assertRaises(ContextError):
            self.builder.init_project(self.target, agent="claude", name="bad\x00name")

    def test_wrong_context_revision_root_or_account_cannot_retarget_session(self):
        for context in (replace(self.context, revision=2),
                        replace(self.context, state_root=self.root / "different-root"),
                        replace(self.context, account="different-user"),
                        replace(self.context, context_id="development")):
            target = replace(self.session, context=context)
            with self.subTest(context=context), self.assertRaises(CommandError):
                self.builder.attach_session(target)
            with self.subTest(context=context), self.assertRaises(CommandError):
                self.builder.stop_session(target)

    def test_missing_identity_and_session_never_fall_back_to_implicit_target(self):
        unknown = replace(self.target, project_id=None)
        with self.assertRaises(CommandError):
            self.builder.start_session(unknown)
        for operation in (self.builder.attach_session, self.builder.stop_session):
            with self.assertRaises(CommandError):
                operation(self.target)
        with self.assertRaises(CommandError):
            self.builder.start_session(self.session)
        with self.assertRaises(CommandError):
            self.builder.init_project(self.session, agent="claude")

    def test_hpc_lifecycle_is_refused_instead_of_starting_a_fresh_agent(self):
        target = replace(self.target, runtime="apptainer")
        with self.assertRaises(CommandError):
            self.builder.start_session(target)
        for operation in (self.builder.attach_session, self.builder.stop_session):
            with self.assertRaises(CommandError):
                operation(replace(target, session_id=SESSION_ID))
        # Preparing project configuration does not submit or attach a job.
        self.assertIn("--runtime=apptainer", self.builder.init_project(target, agent="claude").argv)

    def test_selected_python_entrypoint_is_isolated_and_does_not_search_path(self):
        context = replace(self.context, launcher=Launcher(self.executable, "python_module"))
        builder = LocalCommandBuilder(context, self.root)
        plan = builder.attach_session(replace(self.session, context=context))
        self.assertEqual(plan.argv, (str(self.executable), "-I", "-m", "botainer.cli.main",
                                     "attach", "--", SESSION_ID))

    def test_environment_is_explicit_fresh_and_removes_import_and_state_redirects(self):
        plan = self.builder.start_session(self.target)
        base = {"PATH": "/reviewed/bin", "HOME": str(self.root),
                "PYTHONPATH": "/project/evil", "PYTHONHOME": "/other-python",
                "PYTHONINSPECT": "1", "BASH_ENV": "/project/startup",
                "ENV": "/project/startup", "BOTAINER_STATE_ROOT": "/other-root",
                "BOTAINER_STATE_DIR": "/other-project", "MY_BOTAINER": "/wrong",
                "BOTAINER_NO_TIPS": "0"}
        original = dict(base)
        with patch.dict("os.environ", {"UNREVIEWED_SECRET": "do-not-copy"}):
            env = plan.environment_from(base)
        self.assertEqual(base, original)
        self.assertEqual(env, {"PATH": "/reviewed/bin", "HOME": str(self.root),
                               "MY_BOTAINER": str(self.context.state_root),
                               "BOTAINER_NO_TIPS": "1"})
        env["MY_BOTAINER"] = "/mutated-result"
        self.assertEqual(dict(plan.env_overrides)["MY_BOTAINER"], str(self.context.state_root))

    def test_environment_rejects_invalid_process_entries(self):
        for base in ({"BAD=NAME": "x"}, {"GOOD": "bad\x00value"}, {"GOOD": 12}):
            with self.subTest(base=base), self.assertRaises(CommandError):
                self.builder.project_inventory().environment_from(base)

    def test_plans_are_immutable_and_building_them_never_starts_processes_or_writes_state(self):
        before = set(self.root.rglob("*"))
        with patch("subprocess.Popen", side_effect=AssertionError("must not execute")):
            plans = (self.builder.project_inventory(), self.builder.session_inventory(),
                     self.builder.start_session(self.target), self.builder.attach_session(self.session),
                     self.builder.stop_session(self.session),
                     self.builder.init_project(self.target, agent="claude"))
        self.assertEqual(before, set(self.root.rglob("*")))
        for plan in plans:
            self.assertIsInstance(plan.argv, tuple)
            self.assertEqual(plan.argv[0], str(self.executable))
            with self.assertRaises(FrozenInstanceError):
                plan.cwd = self.root / "different"


if __name__ == "__main__":
    unittest.main()
