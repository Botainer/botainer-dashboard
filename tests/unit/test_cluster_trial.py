"""Offline SSH-trial boundaries. No SSH, cluster operation or installation."""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("cluster_trial", Path(__file__).resolve().parents[2] / "tools/cluster_trial.py")
trial = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(trial)


class ClusterTrialGuards(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # macOS's /var and /tmp aliases are resolved only for this test fixture.
        self.root = Path(self.temp.name).resolve()
        self.root.chmod(0o700)
        self.profile = {"ssh_alias": "cluster-trial", "remote_python": "/opt/botainer/bin/python3",
                        "trial_root": "/scratch/example/dashboard-trial", "source_root": "/work/example/botainer",
                        "image": "/images/existing.sif"}

    def local(self, source, payload=b"", **kwargs):
        return trial.bounded_exchange([sys.executable, "-I", "-B", "-c", source], payload, **kwargs)

    def write_profile(self, profile=None):
        path = self.root / "profile.json"
        path.write_text(json.dumps(profile or self.profile))
        path.chmod(0o600)
        return path

    def test_alias_options_commands_and_extra_profile_fields_are_refused(self):
        for alias in ("-F/tmp/config", "user@host", "host;id", "host\nother", "host test", "$(id)"):
            with self.subTest(alias=alias), self.assertRaises(trial.Refused):
                trial.validate_profile({**self.profile, "ssh_alias": alias})
        with self.assertRaises(trial.Refused):
            trial.validate_profile({**self.profile, "command": "anything"})
        with self.assertRaises(trial.Refused):
            trial.ssh_argv(self.profile, "attach")

    def test_runtime_actions_route_only_to_fixed_repository_helper(self):
        prepare = self.root / "prepare.py"
        runtime = self.root / "runtime.py"
        prepare.write_bytes(b"PREPARATION = True\n")
        runtime.write_bytes(b"RUNTIME = True\n")
        with patch.object(trial, "HELPER", prepare), patch.object(trial, "RUNTIME_HELPER", runtime):
            for action in ("prepare", "preflight"):
                self.assertEqual(trial.helper_source(action), prepare.read_bytes())
            for action in ("submit", "status", "owner", "stop"):
                self.assertEqual(trial.helper_source(action), runtime.read_bytes())
                remote = shlex.split(trial.ssh_argv(self.profile, action)[-1])
                self.assertEqual(remote[5:], [action, "--trial-root", self.profile["trial_root"]])
            for action in ("attach", "/tmp/arbitrary-helper.py", "status;id"):
                with self.assertRaises(trial.Refused):
                    trial.helper_source(action)

    def test_paths_refuse_relative_ambiguous_and_overlapping_targets(self):
        for value in ("relative", "/", "//server/path", "/a/../b", "/a/./b", "/a//b", "/a/", "/a\nb", "/a\x00b"):
            with self.subTest(value=value), self.assertRaises(trial.Refused):
                trial.validate_profile({**self.profile, "trial_root": value})
        for value in (self.profile["source_root"], self.profile["source_root"] + "/trial", "/work/example"):
            with self.subTest(value=value), self.assertRaises(trial.Refused):
                trial.validate_profile({**self.profile, "trial_root": value})
        with self.assertRaises(trial.Refused):
            trial.validate_profile({**self.profile, "image": self.profile["trial_root"] + "/image.sif"})

    def test_remote_shell_quoting_roundtrips_metacharacters_as_single_arguments(self):
        profile = {**self.profile, "source_root": "/work/a 'quote';$(echo surprise)`x`/botainer"}
        argv = trial.ssh_argv(profile, "prepare")
        remote = shlex.split(argv[-1])
        self.assertEqual(remote[remote.index("--source-root") + 1], profile["source_root"])
        self.assertEqual(remote[:5], [profile["remote_python"], "-I", "-B", "-c", trial.BOOTSTRAP])
        self.assertEqual(argv[-2], profile["ssh_alias"])
        self.assertNotIn("-O", argv)  # Existing SSH masters cannot be stopped by this API.
        self.assertIn("ControlMaster=no", argv)
        self.assertIn("StrictHostKeyChecking=yes", argv)
        self.assertIn("ForwardAgent=no", argv)
        self.assertIn("ForwardX11=no", argv)
        self.assertIn("ClearAllForwardings=yes", argv)

    def test_private_profile_checks_permissions_symlinks_and_duplicate_keys(self):
        path = self.write_profile()
        self.assertEqual(trial.load_profile(path)[0], self.profile)
        path.chmod(0o644)
        with self.assertRaises(trial.Refused):
            trial.load_profile(path)
        path.chmod(0o600)
        link = self.root / "link.json"
        link.symlink_to(path)
        with self.assertRaises(OSError):
            trial.load_profile(link)
        path.write_text('{"ssh_alias":"one","ssh_alias":"two"}')
        with self.assertRaises(trial.Refused):
            trial.load_profile(path)
        self.root.chmod(0o755)
        with self.assertRaises(trial.Refused):
            trial.load_profile(path)

    def test_bootstrap_executes_only_complete_bounded_input_as_main(self):
        code = b"import json,sys\nprint(json.dumps([__name__,sys.argv[1:]]))\n"
        args = [sys.executable, "-I", "-B", "-c", trial.BOOTSTRAP, "preflight", "--trial-root", "/trial"]
        result = trial.bounded_exchange(args, code)
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(json.loads(result["stdout"]), ["__main__", ["preflight", "--trial-root", "/trial"]])
        # Test the remote bootstrap's own bound separately from the local bound.
        result = subprocess.run(args, input=b" " * (trial.MAX_SOURCE + 1), capture_output=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"invalid helper size", result.stderr)

    def test_invocation_persists_exact_private_receipts_and_never_retries(self):
        path = self.write_profile()
        helper = b"print('trusted repository helper')\n"
        expected = {"transport_status": "timeout", "remote_outcome": "unknown", "returncode": -9,
                    "stdout": b"partial\x00\xff", "stderr": b"diagnostic", "elapsed_seconds": 60}
        with patch.object(trial, "helper_source", return_value=helper), \
                patch.object(trial, "bounded_exchange", side_effect=lambda *a, **k: dict(expected)) as exchange:
            result, first = trial.invoke(path, "prepare")
            self.assertEqual(exchange.call_count, 1)
            self.assertEqual(exchange.call_args.args[1], helper)
            self.assertEqual(result["remote_outcome"], "unknown")
            _, second = trial.invoke(path, "prepare")
        self.assertNotEqual(first, second)
        self.assertEqual((first / "stdout.bin").read_bytes(), expected["stdout"])
        self.assertEqual((first / "stderr.bin").read_bytes(), expected["stderr"])
        intent = json.loads((first / "intent.json").read_text())
        self.assertEqual(intent["helper_sha256"], trial.hashlib.sha256(helper).hexdigest())
        self.assertEqual(stat.S_IMODE(first.stat().st_mode), 0o700)
        for file in first.iterdir():
            self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o600)
        with self.assertRaises(FileExistsError):
            trial.write_new(first / "stdout.bin", b"replacement")
        self.assertEqual((first / "stdout.bin").read_bytes(), expected["stdout"])

    def test_receipt_intent_must_succeed_before_any_dispatch(self):
        path = self.write_profile()
        with patch.object(trial, "helper_source", return_value=b"pass\n"), \
                patch.object(trial, "write_json", side_effect=OSError("unwritable receipt")), \
                patch.object(trial, "bounded_exchange") as exchange:
            with self.assertRaises(OSError):
                trial.invoke(path, "prepare")
            exchange.assert_not_called()


if __name__ == "__main__":
    unittest.main()
