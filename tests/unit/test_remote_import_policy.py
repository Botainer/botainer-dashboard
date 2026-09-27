"""Remote helper import lifetime checks with inert scheduler and exec doubles."""
from contextlib import redirect_stdout, redirect_stderr
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from botainer_dashboard.import_policy import source_imports
from botainer_dashboard.ssh_diagnostics import classify_remote_helper_failure

REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("remote_import_lifetime_helper", REPO / "tools/remote_botainer_helper.py")
HELPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER)
HELPER.BUNDLE = {name: (REPO / "src/botainer_dashboard" / name).read_text()
                 for name in ("import_policy.py", "installation_layout.py")}


class RemoteImportLifetimeTests(unittest.TestCase):
    def test_attach_removes_empty_cache_before_exec_and_keeps_failed_exec_isolated(self):
        with TemporaryDirectory() as folder:
            project = Path(folder).resolve()
            profile = {"remote_python": sys.executable}
            data = {"project_uuid": "11111111-1111-4111-8111-111111111111",
                    "session_id": "a" * 16, "job_id": "12345"}
            record = {"project_root": str(project), "screen_session_id": "botainer-12345"}
            observed = {"job_id": "12345", "state": "running", "started_at": "synthetic-start",
                        "node": "compute-fixture"}
            artifact = {"path": str(project / "inert-helper.py"), "sha256": "b" * 64}
            previous = sys.pycache_prefix
            with source_imports():
                prefix = Path(sys.pycache_prefix)
                def inert_exec(binary, argv, environment):
                    self.assertEqual(binary, "/usr/bin/srun")
                    self.assertIn("compute-attach", argv)
                    self.assertFalse(prefix.exists())
                    self.assertEqual(sys.pycache_prefix, str(prefix))
                    self.assertTrue(sys.dont_write_bytecode)
                    raise OSError("synthetic exec failure")
                with patch.object(HELPER, "project_path", return_value=project), \
                        patch.object(HELPER, "session_record", return_value=record), \
                        patch.object(HELPER, "scheduler", return_value=observed), \
                        patch.object(HELPER, "prepare", return_value=artifact), \
                        patch.object(HELPER.shutil, "which", return_value="/usr/bin/srun"), \
                        patch.object(HELPER.os, "execve", side_effect=inert_exec):
                    with self.assertRaisesRegex(OSError, "synthetic exec failure"):
                        HELPER.attach(profile, data)
            self.assertEqual(sys.pycache_prefix, previous)
            self.assertFalse(prefix.exists())


class RemoteVerificationDiagnosticTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / 'source'
        (self.source / 'botainer').mkdir(parents=True)
        self.module = self.source / 'botainer/__init__.py'
        self.module.write_text('raise RuntimeError("must not import this fixture")\n')
        self.launcher = self.root / 'launcher'; self.launcher.write_text('# inert launcher\n')
        self.interpreter = self.root / 'python'; self.interpreter.write_bytes(b'inert interpreter')
        self.state = self.root / 'state'; self.state.mkdir()
        self.plugin = self.state / 'plugin.py'; self.plugin.write_text('# inert plugin\n')
        digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        self.profile = {'version': 1, 'source_root': str(self.source),
            'source_sha256': {'botainer/__init__.py': digest(self.module)},
            'launcher': {'path': str(self.launcher), 'sha256': digest(self.launcher)},
            'remote_python': str(self.interpreter), 'remote_python_sha256': digest(self.interpreter),
            'state_root': str(self.state), 'state_plugin_sha256': {'plugin.py': digest(self.plugin)}}

    def verify(self):
        with patch.object(HELPER.sys, 'executable', str(self.interpreter)), \
                patch.dict(os.environ, {}, clear=True), \
                patch.object(HELPER, 'trusted_environment_cache', side_effect=RuntimeError('passed verification')):
            HELPER.verify(self.profile)

    def test_identity_updates_are_distinct_and_fixture_code_never_imports(self):
        with self.assertRaisesRegex(RuntimeError, 'passed verification'): self.verify()
        for path, code in ((self.module, 'remote-source-changed'), (self.launcher, 'remote-launcher-changed'),
                           (self.interpreter, 'remote-interpreter-changed'), (self.plugin, 'remote-plugins-changed')):
            previous = path.read_bytes()
            try:
                path.write_bytes(previous + b'# changed')
                with self.subTest(code=code), self.assertRaises(HELPER.VerificationRefused) as caught:
                    self.verify()
                self.assertEqual(caught.exception.diagnostic_code, code)
            finally:
                path.write_bytes(previous)

    def test_missing_selected_file_and_added_module_remain_refused(self):
        self.launcher.unlink()
        with self.assertRaises(HELPER.VerificationRefused) as caught: self.verify()
        self.assertEqual(caught.exception.diagnostic_code, 'remote-launcher-changed')
        self.launcher.write_text('# inert launcher\n')
        (self.source / 'botainer/new.py').write_text('# added module\n')
        with self.assertRaises(HELPER.VerificationRefused) as caught: self.verify()
        self.assertEqual(caught.exception.diagnostic_code, 'remote-source-changed')

    def test_wheel_metadata_drift_and_unsupported_layout_are_distinct(self):
        metadata = self.source / 'botainer-0.1.0a5.dist-info'; metadata.mkdir()
        (metadata / 'METADATA').write_text('Name: botainer\nVersion: 0.1.0a5\n')
        (metadata / 'entry_points.txt').write_text('[console_scripts]\nbotainer = botainer.cli.main:main\n')
        self.profile.update(version=2, installation_layout={'kind': 'wheel',
                            'metadata_path': 'botainer-0.1.0a5.dist-info/METADATA'})
        for path in metadata.iterdir():
            self.profile['source_sha256'][str(path.relative_to(self.source))] = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(RuntimeError, 'passed verification'): self.verify()
        changed = metadata / 'entry_points.txt'
        changed.write_text('[console_scripts]\nbotainer = unsupported.module:main\n')
        with self.assertRaises(HELPER.VerificationRefused) as caught: self.verify()
        self.assertEqual(caught.exception.diagnostic_code, 'remote-source-changed')
        self.profile['source_sha256'][str(changed.relative_to(self.source))] = hashlib.sha256(changed.read_bytes()).hexdigest()
        with self.assertRaises(HELPER.VerificationRefused) as caught: self.verify()
        self.assertEqual(caught.exception.diagnostic_code, 'remote-import-layout-unsupported')

    def test_other_account_write_and_unsupported_layout_stay_separate(self):
        self.module.chmod(0o666)
        with self.assertRaises(HELPER.VerificationRefused) as caught: self.verify()
        self.assertEqual(caught.exception.diagnostic_code, 'remote-installation-unsafe')
        self.module.chmod(0o600)
        (self.source / 'botainer/extra.pyc').write_bytes(b'not executable bytecode')
        with self.assertRaises(HELPER.VerificationRefused) as caught: self.verify()
        self.assertEqual(caught.exception.diagnostic_code, 'remote-import-layout-unsupported')

    def test_unexpected_policy_errors_are_generic_and_ownership_is_unsafe(self):
        policy = HELPER.module_from_bundle('import_policy.py')
        for internal, code in (('import-path-owner-untrusted', 'remote-installation-unsafe'),
                               ('future-policy-error: /private/synthetic', 'remote-check-failed')):
            with self.subTest(internal=internal), self.assertRaises(HELPER.VerificationRefused) as caught:
                with HELPER.verification_stage('remote-source-changed', policy):
                    raise policy.ImportPolicyError(internal)
            self.assertEqual(caught.exception.diagnostic_code, code)

    def test_error_envelope_contains_no_exception_detail_and_terminal_protocol_is_unchanged(self):
        detail = '/private/synthetic-secret \x1b]52;c;SYNTHETIC\x07'
        for action in (*HELPER._JSON_ACTIONS, 'launch', 'init', 'attach', 'compute-attach', 'unknown-action'):
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch.object(HELPER.sys, 'argv', ['helper', '{}', action, '{}']), \
                    patch.object(HELPER, 'main', side_effect=HELPER.VerificationRefused('remote-plugins-changed', detail)), \
                    redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(HELPER.run(), 2)
            if action in HELPER._JSON_ACTIONS:
                raw = stdout.getvalue().encode()
                self.assertLessEqual(len(raw), 512)
                self.assertEqual(classify_remote_helper_failure({'transport_status': 'complete', 'returncode': 2}, raw),
                                 'remote-plugins-changed')
                self.assertNotIn(detail, stdout.getvalue())
            else:
                self.assertEqual(stdout.getvalue(), '')
            self.assertIn(detail, stderr.getvalue())  # Existing private evidence is retained.

    def test_arbitrary_exception_or_code_never_becomes_public_text(self):
        for failure in (ValueError('/private/synthetic-secret'),
                        HELPER.VerificationRefused('/private/synthetic-secret', 'private detail')):
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch.object(HELPER.sys, 'argv', ['helper', '{}', 'inventory', '{}']), \
                    patch.object(HELPER, 'main', side_effect=failure), redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(HELPER.run(), 2)
            self.assertEqual(json.loads(stdout.getvalue()), {'protocol': 'botainer-dashboard.remote-error',
                             'version': 1, 'code': 'remote-check-failed'})


if __name__ == "__main__":
    unittest.main()
