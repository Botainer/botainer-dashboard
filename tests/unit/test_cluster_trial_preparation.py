"""Offline preparation boundaries; no Botainer/Slurm/SSH execution."""
import importlib.util
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "cluster_trial_preparation", Path(__file__).resolve().parents[2] / "tools/prepare_cluster_trial.py")
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


class ClusterTrialPreparationTests(unittest.TestCase):
    def test_new_root_must_be_named_beneath_home_and_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            root = home / "botainer-dashboard-test-one"
            self.assertEqual(helper.fresh_root(root, home), root)
            with self.assertRaises(helper.TrialError):
                helper.fresh_root(home / "unmarked", home)
            with self.assertRaises(helper.TrialError):
                helper.fresh_root(home.parent / "botainer-dashboard-test-outside", home)
            root.mkdir()
            with self.assertRaises(helper.TrialError):
                helper.fresh_root(root, home)

    def test_source_copy_refuses_symlink_and_keeps_attribution(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            for name in ("botainer", "plugins", "licenses"):
                (source / name).mkdir()
            for name in ("pyproject.toml", "LICENSE", "THIRD-PARTY-LICENSES.md", "licenses/library.txt"):
                (source / name).write_text("test")
            selected = helper.source_files(source)
            self.assertIn(source / "licenses/library.txt", selected)
            self.assertIn(source / "THIRD-PARTY-LICENSES.md", selected)
            (source / "plugins/escape").symlink_to(source / "LICENSE")
            with self.assertRaises(helper.TrialError):
                helper.source_files(source)

    def test_source_copy_refuses_directory_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "botainer").mkdir()
            (source / "plugins").mkdir()
            (source / "plugins/escape").symlink_to(source / "botainer", target_is_directory=True)
            with self.assertRaises(helper.TrialError):
                helper.source_files(source)

    def test_environment_scrubs_live_auth_and_python_import_channels(self):
        descriptor = {"interpreter": {"invocation": "/existing/venv/bin/python3"},
                      "project_uuid": "test-uuid"}
        with patch.dict(os.environ, {"MY_BOTAINER": "/live/state", "PYTHONPATH": "/live/source",
                                     "SSH_AUTH_SOCK": "/live/socket", "ANTHROPIC_API_KEY": "do-not-copy",
                                     "APPTAINER_BIND": "/live:/leak"}):
            env = helper.safe_environment(Path("/private/trial"), descriptor)
        self.assertEqual(env["MY_BOTAINER"], "/private/trial/state")
        self.assertEqual(env["HOME"], "/private/trial")
        self.assertEqual(env["PATH"], "/existing/venv/bin:/usr/bin:/bin")
        self.assertFalse(set(env) & {"PYTHONPATH", "SSH_AUTH_SOCK", "ANTHROPIC_API_KEY", "APPTAINER_BIND"})

    def fixture(self, root):
        (root / "project").mkdir()
        (root / "fixture").mkdir()
        descriptor = {"project_uuid": "test-uuid", "image": {"path": "/existing/image.sif"}, "site_policies": {}}
        spec = NS(runtime="apptainer", image="/existing/image.sif", project_uuid="test-uuid",
                  project_root=str(root / "project"), plugins_enabled=[helper.PLUGIN, "nudge"],
                  hooks=[], sidecars=[], port_forwards=[], env_files=[], network=NS(mode=NS(value="internet")),
                  mount_plan=NS(binds=[NS(source=str(root / "project"), target="/workspace", mode=NS(value="rw")),
                                      NS(source=str(root / "fixture"), target="/mnt/dashboard-fixture", mode=NS(value="ro"))]),
                  env=NS(values={"HOME": "/home/user", "BOTAINER_AGENT_PERMISSIONS": "prompt"}))
        argv = ["apptainer", "exec", "--containall", "--cleanenv", "--no-privs", "--drop-caps", "all",
                "/existing/image.sif", "/bin/sh", "/mnt/dashboard-fixture/terminal.sh"]
        return descriptor, spec, argv

    def test_spec_refuses_external_bind_and_writable_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            descriptor, spec, argv = self.fixture(root)
            self.assertEqual(len(helper.validate_spec(spec, argv, root, descriptor)), 2)
            spec.mount_plan.binds[1].mode.value = "rw"
            with self.assertRaises(helper.TrialError):
                helper.validate_spec(spec, argv, root, descriptor)
            spec.mount_plan.binds[1].source = str(root.parent)
            with self.assertRaises(helper.TrialError):
                helper.validate_spec(spec, argv, root, descriptor)

    def test_spec_refuses_hook_environment_and_changed_entrypoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            descriptor, spec, argv = self.fixture(root)
            spec.env.values["API_KEY"] = "not-real"
            with self.assertRaises(helper.TrialError):
                helper.validate_spec(spec, argv, root, descriptor)
            spec.env.values.pop("API_KEY")
            spec.hooks = ["unexpected"]
            with self.assertRaises(helper.TrialError):
                helper.validate_spec(spec, argv, root, descriptor)
            spec.hooks = []
            with self.assertRaises(helper.TrialError):
                helper.validate_spec(spec, argv[:-1] + ["-i"], root, descriptor)

    def test_new_file_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.json"
            helper.new_file(path, "first")
            with self.assertRaises(FileExistsError):
                helper.new_file(path, "second")
            self.assertEqual(path.read_text(), "first")

    def test_protected_inputs_exclude_mutable_project_metadata_and_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            required = ["source/botainer/__init__.py", "fixture/terminal.sh",
                        "state/plugins/installed.lock", "project/.botainer/config.yaml",
                        "project/.botainer/project-id", "state/policy.yaml",
                        "descriptor.json", "prepare_cluster_trial.py", "README-TEST.txt"]
            evolving = ["state/state/test-uuid/meta.json", "state/state/test-uuid/sessions/sid/spec.json",
                        "state/state/test-uuid/data/null-bind-anchor/AGENT_ACCESS.txt",
                        "state/hpc-job-outputs/test-uuid/slurm-123.out", "preflights/sid/receipt.json"]
            for name in required + evolving:
                helper.new_file(root / name, "original")
            protected = {str(path.relative_to(root)): helper.sha256(path)
                         for path in helper.immutable_inputs(root)}
            self.assertEqual(set(protected), set(required))
            for name in evolving:
                (root / name).write_text("updated by composition")
            after = {str(path.relative_to(root)): helper.sha256(path)
                     for path in helper.immutable_inputs(root)}
            self.assertEqual(after, protected)
            for name in ("source/botainer/__init__.py", "project/.botainer/config.yaml"):
                (root / name).write_text("changed input")
                self.assertNotEqual(helper.sha256(root / name), protected[name])


if __name__ == "__main__":
    unittest.main()
