"""Inert subprocess qualification: no real agent, credentials or network."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import py_compile
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from botainer_dashboard.backend_errors import BackendUnavailable
from botainer_dashboard.ordinary_local import OrdinaryLocalProfile
from botainer_dashboard.pairing import write_private_json

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "tools/ordinary_hook_helper.py"
SPEC = importlib.util.spec_from_file_location("ordinary_hook_test_helper", HELPER)
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class HookBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="dashboard-hook-import-unit-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        for name in ("source/botainer/broker", "state", "home", "projects/hostile/botainer/broker", "bin"):
            (self.root / name).mkdir(parents=True, mode=0o700)
        self.project = self.root / "projects/hostile"
        self.marker = self.root / "hostile-executed"
        source = self.root / "source"
        for name in ("botainer/__init__.py", "botainer/broker/__init__.py"):
            (source / name).write_text("")
            (self.project / name).write_text("")
        daemon = source / "botainer/broker/daemon_main.py"
        daemon.write_text("import json, os, sys\nprint(json.dumps({'source': __file__, 'cwd': os.getcwd(), 'argv': sys.argv, 'isolated': sys.flags.isolated}))\n")
        (self.project / "botainer/broker/daemon_main.py").write_text(
            "from pathlib import Path\nPath(" + repr(str(self.marker)) + ").write_text('wrong source')\n")
        docker = self.root / "bin/docker"
        docker.write_text("# inert tool; never executed\n"); docker.chmod(0o700)
        self.hook = source / "plugins/agent-claude-broker/hooks/start_broker.py"
        self.hook.parent.mkdir(parents=True)
        self.hook.write_text("import subprocess, sys\np = subprocess.Popen([sys.executable, '-m', 'botainer.broker.daemon_main'], stdout=subprocess.PIPE)\nout, _ = p.communicate()\nsys.stdout.buffer.write(out)\nraise SystemExit(p.returncode)\n")
        self.profile_path = self.root / "profile.json"
        self.data = {"version": 1, "id": "inert", "label": "Inert hook test", "python": sys.executable,
                     "source_root": str(source), "state_root": str(self.root / "state"), "home": str(self.root / "home"),
                     "project_roots": [{"id": "projects", "label": "Projects", "path": str(self.root / "projects")}],
                     "docker": {"executable": str(docker), "host": "unix:///inert.sock", "daemon_id": "inert"},
                     "environment": {"PATH": str(self.root / "bin")},
                     "source_hashes": {str(p.relative_to(source)): digest(p) for p in (source / "botainer").rglob("*.py")},
                     "support_hashes": {str(Path(sys.executable).resolve()): digest(sys.executable), str(docker): digest(docker)},
                     "approved_hooks": {str(self.hook): digest(self.hook)}}
        self.data["source_hashes"][str(self.hook.relative_to(source))] = digest(self.hook)
        write_private_json(self.profile_path, self.data)

    def run_helper(self, *args):
        return subprocess.run([sys.executable, "-I", "-B", str(HELPER), *(args or (str(self.profile_path), str(self.hook)))],
                              cwd=self.project, env={**os.environ, "PYTHONPATH": str(self.project)},
                              capture_output=True, text=True, timeout=15, check=False)

    def test_real_inert_child_ignores_hostile_cwd_and_pythonpath(self):
        # Prove each vector independently; no actual Botainer daemon executes.
        for vector in ("cwd", "pythonpath"):
            with self.subTest(vector=vector):
                cwd = self.project if vector == "cwd" else self.root / "home"
                environment = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
                if vector == "pythonpath": environment["PYTHONPATH"] = str(self.project)
                baseline = subprocess.run([sys.executable, "-B", "-m", "botainer.broker.daemon_main"], cwd=cwd,
                                          env=environment, capture_output=True, timeout=10)
                self.assertEqual(baseline.returncode, 0)
                self.assertTrue(self.marker.exists()); self.marker.unlink()
                result = subprocess.run([sys.executable, "-I", "-B", str(HELPER), str(self.profile_path), str(self.hook)],
                                        cwd=cwd, env=environment, capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                value = json.loads(result.stdout)
                self.assertEqual(value["source"], str(self.root / "source/botainer/broker/daemon_main.py"))
                self.assertEqual(value["cwd"], str(cwd))
                self.assertEqual(value["isolated"], 1)
                self.assertFalse(self.marker.exists())

    def test_changed_hook_refused_before_execution(self):
        self.hook.write_text("raise RuntimeError('must never execute')\n")
        result = self.run_helper()
        self.assertEqual(result.returncode, 2)
        self.assertIn("requalification-required", result.stderr)
        self.assertNotIn("must never execute", result.stderr)

    def test_alpha5_isolated_broker_child_retains_pinned_source_binding(self):
        self.hook.write_text("import subprocess, sys\np = subprocess.Popen([sys.executable, '-I', '-B', '-m', 'botainer.broker.daemon_main'], stdout=subprocess.PIPE)\nout, _ = p.communicate()\nsys.stdout.buffer.write(out)\nraise SystemExit(p.returncode)\n")
        self.data["approved_hooks"] = {str(self.hook): digest(self.hook)}
        self.data["source_hashes"][str(self.hook.relative_to(self.root / "source"))] = digest(self.hook)
        write_private_json(self.profile_path, self.data)
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["source"], str(self.root / "source/botainer/broker/daemon_main.py"))
        self.assertEqual(value["isolated"], 1)
        self.assertFalse(self.marker.exists())

    def test_installed_unchecked_bytecode_is_ignored_in_hook_and_broker_child(self):
        daemon = self.root / "source/botainer/broker/daemon_main.py"
        reviewed_digest = digest(daemon)
        alternate = self.root / "alternate.py"
        alternate.write_text("print('UNREVIEWED_INERT_CACHE_EXECUTED')\n")
        cache = Path(importlib.util.cache_from_source(str(daemon)))
        py_compile.compile(str(alternate), cfile=str(cache), dfile=str(daemon),
                           doraise=True, invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
        cache_digest = digest(cache)
        # The reviewed source and its profile pins have not changed. Existing
        # caches are allowed to remain installed; they must not be consumed.
        OrdinaryLocalProfile(self.profile_path).verify()
        for arguments in ((), ("--broker-child", str(self.profile_path), str(self.hook))):
            with self.subTest(arguments=arguments):
                result = self.run_helper(*arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["source"], str(daemon))
                self.assertNotIn("UNREVIEWED_INERT_CACHE_EXECUTED", result.stdout)
        self.assertEqual(digest(daemon), reviewed_digest)
        self.assertEqual(digest(cache), cache_digest)

    def test_shadow_executable_artifacts_refused_before_broker_execution(self):
        for suffix in (".so", ".pyd", ".pyc"):
            with self.subTest(suffix=suffix):
                artifact = self.root / ("source/botainer/broker/daemon_main" + suffix)
                artifact.write_bytes(b"inert extra executable artifact")
                try:
                    result = self.run_helper("--broker-child", str(self.profile_path), str(self.hook))
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("unreviewed-executable", result.stderr)
                    self.assertFalse(result.stdout)
                finally:
                    artifact.unlink()

    def test_other_account_writable_source_refused_before_execution(self):
        source = self.root / "source/botainer/broker/daemon_main.py"
        source.chmod(0o666)
        result = self.run_helper("--broker-child", str(self.profile_path), str(self.hook))
        self.assertEqual(result.returncode, 2)
        self.assertIn("other-account-writable", result.stderr)

    def test_unapproved_hook_refused_before_execution(self):
        self.data["approved_hooks"] = {}
        write_private_json(self.profile_path, self.data)
        result = self.run_helper()
        self.assertEqual(result.returncode, 2)
        self.assertIn("hook-review-required", result.stderr)

    def test_child_source_is_rechecked(self):
        (self.root / "source/botainer/broker/daemon_main.py").write_text("raise RuntimeError('not reviewed')\n")
        result = self.run_helper("--broker-child", str(self.profile_path), str(self.hook))
        self.assertEqual(result.returncode, 2)
        self.assertIn("source-changed", result.stderr)

    def test_changed_known_broker_spawn_is_refused(self):
        profile = Mock(data=self.data, path=self.profile_path)
        original = helper.subprocess.Popen
        for command in ([sys.executable, "-m", "different.module"], "sh anything",
                        [sys.executable, "-m", "botainer.broker.daemon_main", "extra"],
                        [sys.executable, "-I", "-m", "botainer.broker.daemon_main"],
                        [sys.executable, "-B", "-I", "-m", "botainer.broker.daemon_main"],
                        [sys.executable, "-I", "-B", "-m", "different.module"],
                        [sys.executable, "-I", "-B", "-m", "botainer.broker.daemon_main", "extra"],
                        ["/other/python", "-I", "-B", "-m", "botainer.broker.daemon_main"]):
            with self.subTest(command=command), helper.isolated_broker_spawn(profile, self.hook):
                with self.assertRaisesRegex(BackendUnavailable, "child-command-changed"):
                    helper.subprocess.Popen(command)
        self.assertIs(helper.subprocess.Popen, original)

    def test_both_exact_broker_paths_are_guarded_without_shell_or_executable_override(self):
        profile = Mock(data=self.data, path=self.profile_path)
        for root in (self.data["source_root"], self.data["state_root"]):
            for plugin in ("agent-claude-broker", "agent-codex-broker"):
                hook = Path(root) / "plugins" / plugin / "hooks/start_broker.py"
                for flags in ([], ["-I", "-B"]):
                    command = [sys.executable, *flags, "-m", "botainer.broker.daemon_main"]
                    with self.subTest(hook=hook, flags=flags), patch.object(helper.subprocess, "Popen") as original:
                        with helper.isolated_broker_spawn(profile, hook):
                            helper.subprocess.Popen(command, stdout=subprocess.PIPE)
                            with self.assertRaises(BackendUnavailable):
                                helper.subprocess.Popen(command, shell=True)
                            with self.assertRaises(BackendUnavailable):
                                helper.subprocess.Popen(command, executable="/other")
                        self.assertEqual(original.call_args.args[0], helper.broker_command(profile, hook))
                        self.assertEqual(original.call_count, 1)

    def test_nonbroker_approved_hook_keeps_native_behavior_and_argv(self):
        self.hook = self.root / "source/plugins/git/hooks/pre_session.py"
        self.hook.parent.mkdir(parents=True)
        self.hook.write_text("import json, sys\nprint(json.dumps({'argv': sys.argv, 'name': __name__}))\n")
        self.data["approved_hooks"] = {str(self.hook): digest(self.hook)}
        self.data["source_hashes"][str(self.hook.relative_to(self.root / "source"))] = digest(self.hook)
        write_private_json(self.profile_path, self.data)
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"argv": [str(self.hook)], "name": "__main__"})
        child = self.run_helper("--broker-child", str(self.profile_path), str(self.hook))
        self.assertEqual(child.returncode, 2)
        self.assertIn("child-hook-invalid", child.stderr)


if __name__ == "__main__":
    unittest.main()
