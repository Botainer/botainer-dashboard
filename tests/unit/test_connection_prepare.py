from __future__ import annotations

import base64
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import stat
import sys
import sysconfig
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from botainer_dashboard.connection_prepare import PROBE_SOURCE, _run, prepare_connection
from botainer_dashboard.backend_errors import BackendUnavailable
from botainer_dashboard.connections import ConnectionRegistry, ConnectionRegistryError
from botainer_dashboard.ordinary_local import OrdinaryLocalProfile
from botainer_dashboard.ordinary_owner import OrdinaryCliOwner
from botainer_dashboard.pairing import write_private_json


HASH = "a" * 64
REMOTE_REQUIRED = ("pyproject.toml", "botainer/__init__.py", "botainer/plugins/lifecycle.py",
    "botainer/plugins/manifest.py", "botainer/cli/main.py", "botainer/cli/plugin.py",
    "botainer/cli/hpc.py", "plugins/hpc-launcher/host_helper/submit.py",
    "plugins/hpc-launcher/host_helper/_common.py")


def response(value, code=0, error=b""):
    return {"returncode": code, "stdout": json.dumps(value).encode(), "stderr": error}


def remote_request():
    return {"kind": "remote", "id": "cluster", "label": "Research cluster", "ssh_alias": "research",
        "python": "/opt/botainer/bin/python", "source_root": "/opt/source with ' quotes",
        "state_root": "/home/person/.botainer", "launcher": "/opt/bin/botainer",
        "project_roots": ["/work/projects"], "control_root": "/home/person/.dashboard",
        "read_only_acknowledged": True}


def observed_remote():
    return {"ok": True, "source_root": "/opt/source with ' quotes", "state_root": "/home/person/.botainer",
        "python_sha256": HASH, "launcher": {"path": "/opt/bin/botainer", "sha256": HASH},
        "source_hashes": {p: HASH for p in REMOTE_REQUIRED}, "state_hashes": {},
        "control_root": "/home/person/.dashboard", "control_exists": True,
        "project_roots": ["/work/projects"], "hook_candidates": {}}


class ConnectionPreparationTests(unittest.TestCase):
    def test_remote_command_is_strict_read_only_probe_and_roundtrips_paths(self):
        calls = []
        def runner(argv, **kwargs):
            calls.append((argv, kwargs)); return response(observed_remote())
        result = prepare_connection(remote_request(), runner=runner)
        self.assertEqual(result["qualification"], "untested")
        self.assertTrue(result["requires_review"])
        self.assertEqual(result["profile"]["ssh_alias"], "research")
        self.assertEqual(len(calls), 1)
        argv, kwargs = calls[0]
        self.assertIn("BatchMode=yes", argv)
        self.assertIn("StrictHostKeyChecking=yes", argv)
        self.assertIn("ForwardAgent=no", argv)
        self.assertIn("PermitLocalCommand=no", argv)
        self.assertIn("UpdateHostKeys=no", argv)
        remote = shlex.split(argv[-1])
        self.assertEqual(remote[:5], ["/opt/botainer/bin/python", "-I", "-S", "-B", "-"])
        self.assertEqual(json.loads(base64.b64decode(remote[5]))["source_root"], remote_request()["source_root"])
        self.assertEqual(kwargs["input"], PROBE_SOURCE.encode())
        self.assertLessEqual(kwargs["timeout"], 30)

    def test_no_process_before_ack_or_for_injection_fields(self):
        samples = [{}, {**remote_request(), "read_only_acknowledged": False},
            {**remote_request(), "ssh_alias": "research; echo bad"},
            {**remote_request(), "ssh_alias": "-ProxyCommand=bad"},
            {**remote_request(), "source_root": "/opt/../etc"},
            {**remote_request(), "password": "secret"},
            {**remote_request(), "project_roots": ["/"]},
            {**remote_request(), "id": "local"},
            {**remote_request(), "python": "~/bin/python"}]
        for request in samples:
            with self.subTest(request=request):
                result = prepare_connection(request, runner=lambda *a, **k: self.fail("process called"))
                self.assertIsNone(result["profile"])
                self.assertEqual(result["qualification"], "failed")

    def test_ssh_failure_returns_auth_guidance_without_raw_banner(self):
        result = prepare_connection(remote_request(), runner=lambda *a, **k:
            response({}, 255, b"private-secret-banner\nperson@server: Permission denied (publickey).\n"))
        self.assertIsNone(result["profile"])
        self.assertIn("authentication", result["checks"][0]["message"])
        self.assertNotIn("private-secret", json.dumps(result))
        self.assertIn("system terminal", result["instructions"][0])

    def test_missing_control_directory_produces_manual_step_not_candidate(self):
        result = prepare_connection(remote_request(), runner=lambda *a, **k:
            response({"ok": False, "code": "control-root-missing"}))
        self.assertIsNone(result["profile"])
        self.assertIn("mkdir -m 700", result["instructions"][0])

    def test_unverified_import_resolution_is_not_a_working_remote_profile(self):
        result = prepare_connection(remote_request(), runner=lambda *a, **k:
            response({"ok": False, "code": "remote-import-selection-unverified"}))
        self.assertIsNone(result["profile"])
        self.assertIn("No import hooks", result["checks"][0]["message"])

    def test_incomplete_source_pins_cannot_be_saved(self):
        observed = observed_remote(); del observed["source_hashes"]["botainer/cli/hpc.py"]
        result = prepare_connection(remote_request(), runner=lambda *a, **k: response(observed))
        self.assertIsNone(result["profile"])

    def test_untrusted_oversized_or_invalid_inspection_is_rejected(self):
        for raw in (b"not json", b"x" * (1024*1024+1)):
            result = prepare_connection(remote_request(), runner=lambda *a, **k:
                {"returncode": 0, "stdout": raw, "stderr": b""})
            self.assertIsNone(result["profile"])

    def test_remote_default_python_is_fixed_no_shell_discovery(self):
        request = remote_request(); del request["python"]
        calls = []
        def runner(argv, **kwargs):
            calls.append(argv); return response({"ok": False, "code": "source-not-discovered"})
        result = prepare_connection(request, runner=runner)
        self.assertEqual(shlex.split(calls[0][-1])[0], "/usr/bin/python3")
        self.assertIsNone(result["profile"])

    def test_directory_diagnostic_identifies_field_and_bounds_remote_hint_text(self):
        result = prepare_connection(remote_request(), runner=lambda *a, **k:
            response({"ok": False, "code": "directory-not-canonical", "field": "project_roots.1",
                "resolved_path": "/resolved/projects"}))
        self.assertIn("Project folder 2", result["checks"][0]["name"])
        self.assertIn("project_roots[1]", result["checks"][0]["name"])
        self.assertIn("/resolved/projects", result["instructions"][0])
        hostile = prepare_connection(remote_request(), runner=lambda *a, **k:
            response({"ok": False, "code": "directory-not-canonical", "field": "private-remote-banner",
                "resolved_path": "/bad\nprivate-remote-banner"}))
        self.assertNotIn("private-remote-banner", json.dumps(hostile))
        self.assertEqual(hostile["instructions"], [])


@unittest.skipIf(sys.version_info < (3, 11), "Read-only source probe requires an existing Python 3.11+")
class ActualReadOnlyProbeTests(unittest.TestCase):
    def setUp(self):
        # A real local owner has a 55-byte socket-root budget. Keep the normal
        # fixture valid independently of macOS's long default temp directory.
        base = "/private/tmp" if Path("/private/tmp").is_dir() else "/tmp"
        self.temp = tempfile.TemporaryDirectory(prefix="bdp-", dir=base)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source, self.state, self.projects, self.control, self.home = [self.root / name for name in ("source", "state", "projects", "control", "home")]
        for path in (self.source, self.state, self.projects, self.control, self.home): path.mkdir(mode=0o700)
        for relative in REMOTE_REQUIRED:
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("raise RuntimeError('must never execute Botainer')\n")
        hook = self.source / "plugins/test/hooks/start.py"
        hook.parent.mkdir(parents=True)
        hook.write_text("raise RuntimeError('must never execute hook')\n")
        self.hook = hook
        installed = self.state / "plugins/test/commands/extensionless"
        installed.parent.mkdir(parents=True)
        installed.write_text("must never run\n")
        self.installed = installed
        self.bin = self.root / "bin"; self.bin.mkdir()
        for name in ("docker", "tmux"):
            path = self.bin / name; path.write_text("#!/bin/sh\nexit 75\n"); path.chmod(0o700)
        self.request = {"kind": "local", "id": "local", "label": "Local test",
            "python": sys.executable, "source_root": str(self.source), "state_root": str(self.state),
            "home": str(self.home), "project_roots": [str(self.projects)], "control_root": str(self.control),
            "docker_path": str(self.bin / "docker"), "tmux_path": str(self.bin / "tmux"),
            "docker_host": "unix:///test/docker.sock", "read_only_acknowledged": True}
        self.calls = []

    def runner(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[0] == sys.executable: return _run(argv, **kwargs)
        self.assertEqual(argv, (str(self.bin / "docker"), "--host", "unix:///test/docker.sock", "info", "--format", "{{json .ID}}"))
        return response("test-daemon-id")

    def test_real_probe_pins_source_and_plugins_without_import_and_profile_loads(self):
        before = set(self.root.rglob("*"))
        result = prepare_connection(self.request, runner=self.runner)
        self.assertEqual(result["qualification"], "untested", result)
        self.assertEqual(set(self.root.rglob("*")), before)
        profile = result["profile"]
        self.assertEqual(profile["approved_hooks"], {})
        self.assertIn(str(self.hook), result["hook_candidates"])
        self.assertIn("plugins/test/hooks/start.py", profile["source_hashes"])
        self.assertIn(str(self.installed), profile["support_hashes"])
        self.assertEqual(len(self.calls), 2)
        path = self.root / "candidate.json"
        path.write_text(json.dumps(profile)); path.chmod(0o600)
        loaded = OrdinaryLocalProfile(path)
        loaded.verify()

    def test_unsafe_source_permissions_fail_setup_before_docker_without_chmod(self):
        target = self.source / 'botainer/cli/main.py'
        target.chmod(0o664)
        before = {str(p): p.lstat().st_mode for p in self.root.rglob('*')}
        result = prepare_connection(self.request, runner=self.runner)
        self.assertIsNone(result['profile'])
        self.assertIn('another account can change', result['checks'][0]['message'])
        self.assertNotIn(str(target), json.dumps(result))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(before, {str(p): p.lstat().st_mode for p in self.root.rglob('*')})

    def test_remote_probe_uses_same_source_permission_policy(self):
        request = {**self.request, 'kind': 'remote', 'id': 'server',
                   'ssh_alias': 'test-server', 'launcher': str(self.bin / 'docker')}
        target = self.source / 'botainer/cli/main.py'
        target.chmod(0o664)
        result = self.wheel_probe(request)
        self.assertFalse(result['ok'])
        self.assertEqual(result['code'], 'import-path-other-account-writable')
        self.assertEqual(result['field'], 'source_files')
        self.assertNotIn(str(target), json.dumps(result))
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o664)

    def test_remote_probe_refuses_runtime_unsupported_hardlinks_and_file_sizes(self):
        request = {**self.request, 'kind': 'remote', 'id': 'server',
                   'ssh_alias': 'test-server', 'launcher': str(self.bin / 'docker')}
        target = self.source / 'botainer/cli/main.py'
        link = self.root / 'another-link'; os.link(target, link)
        self.assertEqual(self.wheel_probe(request)['code'], 'source-hardlink')
        self.assertEqual(target.stat().st_nlink, 2)
        link.unlink()
        target.write_bytes(b'#' * (2 * 1024 * 1024 + 1))
        result = self.wheel_probe(request)
        self.assertEqual(result['code'], 'source-file-too-large')
        self.assertEqual(target.stat().st_size, 2 * 1024 * 1024 + 1)

    def test_real_installation_update_recovers_deleted_source_selection_without_runtime(self):
        from botainer_dashboard.installation_update import prepare_installation_update
        initial = prepare_connection(self.request, runner=self.runner)['profile']
        registry = ConnectionRegistry(self.root / 'connections.json')
        registry.initialize()
        saved = registry.add('local', initial, 0, trusted=True)
        old_entry = saved['entries'][0]
        old_profile_path = Path(old_entry['profilePath'])
        old_bytes = old_profile_path.read_bytes()
        replacement = self.root / 'updated-source'; self.source.rename(replacement)
        (replacement / 'botainer/cli/main.py').write_text('raise RuntimeError("updated source must not execute")\n')
        changes = {'source_root': str(replacement)}
        def inspect(kind, profile, updates):
            return prepare_installation_update(kind, profile, updates, runner=self.runner)
        with patch('botainer_dashboard.installation_update.prepare_installation_update', side_effect=inspect):
            settings = registry.installation_update_settings('local')
            self.assertEqual(settings['settings']['source_root'], str(self.source))
            review = registry.prepare_installation_update('local', changes, 1, old_entry['profileDigest'], acknowledged=True)
            self.assertTrue(review['changed'])
            self.assertEqual(registry.read(), saved)
            updated = registry.update_installation('local', changes, review['candidateDigest'], 1,
                old_entry['profileDigest'], acknowledged=True, confirmed=True)
        current = OrdinaryLocalProfile(Path(updated['entries'][0]['profilePath']))
        current.verify()
        self.assertEqual(current.data['source_root'], str(replacement))
        self.assertEqual(current.data['project_roots'], initial['project_roots'])
        self.assertEqual(old_profile_path.read_bytes(), old_bytes)
        self.assertFalse(self.source.exists())

    def test_installed_hook_and_tool_permissions_are_checked_before_runtime(self):
        for target, field in [(self.installed, 'state_plugins'),
                              (self.bin / 'docker', 'docker_path')]:
            with self.subTest(field=field):
                original = target.stat().st_mode
                try:
                    target.chmod(0o775)
                    result = prepare_connection(self.request, runner=self.runner)
                    self.assertIsNone(result['profile'])
                    self.assertIn('another account can change', result['checks'][0]['message'])
                    self.assertIn(field, result['checks'][0]['name'])
                    self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o775)
                finally:
                    target.chmod(stat.S_IMODE(original))

    def test_native_terminal_tool_matches_owner_policy_and_retains_hash_pin(self):
        shared = self.root / 'native-tools'; shared.mkdir(); shared.chmod(0o775)
        target = shared / 'tmux'
        target.write_text('#!/bin/sh\nexit 75\n'); target.chmod(0o775)
        request = {**self.request, 'tmux_path': str(target)}
        result = prepare_connection(request, runner=self.runner)
        self.assertIsNotNone(result['profile'], result)
        profile = OrdinaryLocalProfile(self.save_candidate(result['profile']))
        profile.verify()
        owner = self.make_inert_owner(profile)
        self.assertEqual(owner.tmux_path, target)
        self.assertEqual(stat.S_IMODE(shared.stat().st_mode), 0o775)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o775)
        target.write_text('#!/bin/sh\nexit 76\n')
        with self.assertRaises(BackendUnavailable):
            profile.verify()
        with self.assertRaises(BackendUnavailable):
            self.make_inert_owner(profile)
        target.chmod(0o600)
        self.assertIsNone(prepare_connection(request, runner=self.runner)['profile'])

    def test_standalone_bytecode_refuses_setup_but_existing_cache_is_untouched(self):
        cache = self.source / 'botainer/__pycache__/old.cpython-313.pyc'
        cache.parent.mkdir(); cache.write_bytes(b'inert cache')
        before = cache.read_bytes()
        result = prepare_connection(self.request, runner=self.runner)
        self.assertIsNotNone(result['profile'], result)
        rogue = self.source / 'botainer/shadow.pyc'; rogue.write_bytes(b'inert standalone bytecode')
        result = prepare_connection(self.request, runner=self.runner)
        self.assertIsNone(result['profile'])
        self.assertIn('standalone bytecode', result['checks'][0]['message'])
        self.assertEqual(cache.read_bytes(), before)
        self.assertTrue(rogue.exists())

    def wheel_fixture(self):
        (self.source / 'pyproject.toml').unlink()
        info = self.source / 'botainer-0.1.0a5.dist-info'; info.mkdir()
        (info / 'METADATA').write_text('Metadata-Version: 2.4\nName: botainer\nVersion: 0.1.0a5\n\n')
        (info / 'entry_points.txt').write_text('[console_scripts]\nbotainer = botainer.cli.main:main\n')
        # Bundled resources are fingerprinted, but must not masquerade as an
        # installed state plugin or be substituted into native dispatch.
        bundled = self.source / 'botainer/_builtin_plugins/hpc-launcher/host_helper/submit.py'
        bundled.parent.mkdir(parents=True)
        bundled.write_text('raise RuntimeError("do not execute bundled helper")\n')
        return info

    def wheel_probe(self, request=None, *, selected=True):
        payload = base64.b64encode(json.dumps(request or self.request).encode()).decode()
        output = io.StringIO()
        # Model the whole interpreter selection, including its prefix. Otherwise
        # a test runner inside a real venv bypasses the mocked sysconfig paths
        # and silently inspects that venv's sites instead of this fixture.
        binary = self.bin / 'probe-python'
        if not binary.exists(): binary.symlink_to(Path(sys.executable).resolve())
        # Execute the exact transported probe with synthetic discovery inputs.
        # Neither this package nor any .pth code is imported or installed.
        paths = [str(self.source)] + sys.path if selected else sys.path
        with patch.object(sys, 'argv', ['-', payload]), patch.object(sys, 'executable', str(binary)), \
                patch.object(sys, 'path', paths), \
                patch.object(sysconfig, 'get_path', return_value=str(self.source if selected else self.home)), \
                redirect_stdout(output):
            exec(compile(PROBE_SOURCE, '<fixed-wheel-probe>', 'exec'), {})
        return json.loads(output.getvalue())

    def test_wheel_probe_creates_explicit_v2_profile_without_botainer_import(self):
        self.wheel_fixture()
        observed = self.wheel_probe()
        self.assertTrue(observed['ok'], observed)
        self.assertIn('botainer/_builtin_plugins/hpc-launcher/host_helper/submit.py', observed['source_hashes'])
        self.assertNotIn('plugins/hpc-launcher/host_helper/submit.py', observed['source_hashes'])
        result = prepare_connection(self.request, runner=lambda argv, **kwargs:
            response(observed if argv[0] == sys.executable else 'test-daemon-id'))
        self.assertEqual(result['profile']['version'], 2, result)
        self.assertEqual(result['profile']['approved_hooks'], {})
        path = self.root / 'wheel-profile.json'
        path.write_text(json.dumps(result['profile'])); path.chmod(0o600)
        OrdinaryLocalProfile(path).verify()

    def test_real_isolated_wheel_probe_discovers_selected_interpreter_package(self):
        self.wheel_fixture()
        # A synthetic prefix only: reuse the already installed interpreter and
        # inert metadata/package files. No venv/pip command or install is run.
        prefix = self.root / 'selected-python'
        binary = prefix / 'bin/python'; binary.parent.mkdir(parents=True)
        binary.symlink_to(Path(sys.executable).resolve())
        (prefix / 'pyvenv.cfg').write_text('home = ' + str(Path(sys.executable).resolve().parent)
            + '\ninclude-system-site-packages = false\n')
        site = prefix / ('lib/python%d.%d/site-packages' % sys.version_info[:2])
        site.parent.mkdir(parents=True); self.source.rename(site)
        request = {**self.request, 'python': str(binary)}; request.pop('source_root')
        before = set(self.root.rglob('*'))
        def runner(argv, **kwargs):
            return _run(argv, **kwargs) if argv[0] == str(binary) else response('test-daemon-id')
        result = prepare_connection(request, runner=runner)
        self.assertEqual(result['qualification'], 'untested', result)
        self.assertEqual(result['profile']['source_root'], str(site))
        self.assertEqual(result['profile']['version'], 2)
        self.assertEqual(set(self.root.rglob('*')), before)
        path = self.root / 'isolated-wheel-profile.json'
        path.write_text(json.dumps(result['profile'])); path.chmod(0o600)
        OrdinaryLocalProfile(path).verify()

        # Also qualify dynamic-.pth refusal through the real isolated child,
        # independent of the in-process discovery seam above.
        marker = self.root / 'import-hook-must-not-run'
        (site / 'dynamic.pth').write_text('import pathlib; pathlib.Path(' + repr(str(marker)) + ').touch()\n')
        refused = prepare_connection(request, runner=runner)
        self.assertIsNone(refused['profile'], refused)
        self.assertIn('No import hooks were executed', refused['checks'][0]['message'])
        self.assertFalse(marker.exists())

    def test_wheel_requires_exact_selected_python_import_and_no_dynamic_pth(self):
        self.wheel_fixture()
        self.assertEqual(self.wheel_probe(selected=False)['code'], 'remote-import-selection-unverified')
        marker = self.root / 'must-not-exist'
        (self.source / 'custom.pth').write_text('import pathlib; pathlib.Path(' + repr(str(marker)) + ').touch()\n')
        self.assertEqual(self.wheel_probe()['code'], 'remote-import-selection-unverified')
        self.assertFalse(marker.exists())

    def test_remote_wheel_requires_installed_hpc_plugin_not_just_bundled_copy(self):
        self.wheel_fixture()
        request = {**self.request, 'kind': 'remote', 'launcher': str(self.bin / 'docker')}
        self.assertEqual(self.wheel_probe(request)['code'], 'installed-hpc-plugin-unavailable')
        for name in ('botainer-plugin.yaml', 'host_helper/submit.py', 'host_helper/_common.py'):
            path = self.state / 'plugins/hpc-launcher' / name
            path.parent.mkdir(parents=True, exist_ok=True); path.write_text('# inert installed plugin\n')
        observed = self.wheel_probe(request)
        self.assertTrue(observed['ok'], observed)
        prepared = prepare_connection(remote_request(), runner=lambda *args, **kwargs: response(observed))
        self.assertEqual(prepared['profile']['version'], 2, prepared)
        self.assertIn('plugins/hpc-launcher/host_helper/submit.py', prepared['profile']['state_plugin_sha256'])

    def test_wheel_metadata_collision_is_not_a_candidate(self):
        self.wheel_fixture()
        (self.source / 'botainer-0.1.0a4.dist-info').mkdir()
        self.assertEqual(self.wheel_probe()['code'], 'installation-layout-unverified')

    def test_source_directory_symlink_is_rejected_before_docker(self):
        (self.source / "botainer/escape").symlink_to(self.state, target_is_directory=True)
        result = prepare_connection(self.request, runner=self.runner)
        self.assertIsNone(result["profile"])
        self.assertEqual(len(self.calls), 1)
        self.assertIn("symlink", result["checks"][0]["message"])

    def test_missing_or_shared_control_root_does_not_create_or_chmod(self):
        self.control.rmdir()
        result = prepare_connection(self.request, runner=self.runner)
        self.assertIsNone(result["profile"])
        self.assertFalse(self.control.exists())
        self.control.mkdir(mode=0o755)
        result = prepare_connection(self.request, runner=self.runner)
        self.assertIsNone(result["profile"])
        self.assertEqual(stat.S_IMODE(self.control.stat().st_mode), 0o755)

    def control_with_bytes(self, length, *, multibyte=False):
        remaining = length - len(os.fsencode(self.root)) - 1
        name = ("é" + "a" * (remaining - len(os.fsencode("é")))) if multibyte else "a" * remaining
        path = self.root / name
        path.mkdir(mode=0o700)
        self.assertEqual(len(os.fsencode(path)), length)
        return path

    def save_candidate(self, profile):
        path = self.root / "candidate.json"
        write_private_json(path, profile)
        return path

    def make_inert_owner(self, profile):
        selected = profile.data["terminal_owner"]
        return OrdinaryCliOwner(selected["control_root"], selected["path"], selected["sha256"],
            profile.environment, runner=lambda *_a, **_kw: self.fail("Owner construction ran tmux"))

    def test_55_byte_root_passes_probe_registry_and_runtime_without_execution(self):
        control = self.control_with_bytes(55)
        result = prepare_connection({**self.request, "control_root": str(control)}, runner=self.runner)
        self.assertEqual(result["qualification"], "untested", result)
        profile = OrdinaryLocalProfile(self.save_candidate(result["profile"]))
        profile.verify()
        owner = self.make_inert_owner(profile)
        self.assertEqual(owner.root, control)
        registry = ConnectionRegistry(self.root / "connections.json")
        registry.initialize()
        saved = registry.add("local", result["profile"], 0, trusted=True)
        self.assertEqual(saved["revision"], 1)
        selected = OrdinaryLocalProfile(registry.selected_profiles()["local_profile"])
        self.assertEqual(selected.namespace, profile.namespace)
        self.assertEqual(self.make_inert_owner(selected).root_identity, owner.root_identity)
        self.assertEqual(list(control.iterdir()), [])

    def test_56_byte_roots_including_multibyte_fail_probe_import_and_runtime(self):
        good = prepare_connection(self.request, runner=self.runner)["profile"]
        registry = ConnectionRegistry(self.root / "connections.json")
        registry.initialize()
        for multibyte in (False, True):
            with self.subTest(multibyte=multibyte):
                control = self.control_with_bytes(56, multibyte=multibyte)
                if multibyte:
                    self.assertLessEqual(len(str(control)), 55)
                before = control.stat()
                result = prepare_connection({**self.request, "control_root": str(control)}, runner=self.runner)
                self.assertIsNone(result["profile"], result)
                self.assertIn("control_root", result["checks"][0]["name"])
                self.assertIn("55 filesystem-encoded bytes", result["checks"][0]["message"])
                manual = {**good, "terminal_owner": {**good["terminal_owner"], "control_root": str(control)}}
                with self.assertRaisesRegex(BackendUnavailable, "ordinary-control-root-too-long"):
                    OrdinaryLocalProfile(self.save_candidate(manual))
                with self.assertRaisesRegex(ConnectionRegistryError, "control_root.*55 filesystem-encoded bytes"):
                    registry.add("local", manual, 0, trusted=True)
                imported = ConnectionRegistry(self.root / ("import-" + str(multibyte) + ".json"))
                with self.assertRaisesRegex(ConnectionRegistryError, "control_root.*55 filesystem-encoded bytes"):
                    imported.initialize([{"kind": "local", "path": self.root / "candidate.json"}])
                self.assertFalse(imported.path.exists())
                with self.assertRaisesRegex(BackendUnavailable, "ordinary-owner-unverified"):
                    OrdinaryCliOwner(control, manual["terminal_owner"]["path"], manual["terminal_owner"]["sha256"], {})
                self.assertEqual(registry.read()["revision"], 0)
                self.assertEqual(list(control.iterdir()), [])
                after = control.stat()
                self.assertEqual((before.st_mode, before.st_mtime_ns), (after.st_mode, after.st_mtime_ns))

    def test_55_encoded_bytes_with_multibyte_name_is_valid(self):
        control = self.control_with_bytes(55, multibyte=True)
        result = prepare_connection({**self.request, "control_root": str(control)}, runner=self.runner)
        self.assertEqual(result["qualification"], "untested", result)
        profile = OrdinaryLocalProfile(self.save_candidate(result["profile"]))
        profile.verify()
        self.assertEqual(self.make_inert_owner(profile).root, control)
        self.assertEqual(list(control.iterdir()), [])

    def test_mode_changes_fail_probe_profile_save_revalidation_and_owner_without_chmod(self):
        candidate = prepare_connection(self.request, runner=self.runner)["profile"]
        profile = OrdinaryLocalProfile(self.save_candidate(candidate))
        owner = self.make_inert_owner(profile)
        registry = ConnectionRegistry(self.root / "connections.json")
        registry.initialize()
        for mode in (0o500, 0o600, 0o755, 0o1700):
            with self.subTest(mode=oct(mode)):
                self.control.chmod(mode)
                try:
                    result = prepare_connection(self.request, runner=self.runner)
                    self.assertIsNone(result["profile"], result)
                    self.assertIn("exactly mode 700", result["checks"][0]["message"])
                    with self.assertRaisesRegex(BackendUnavailable, "control-root-not-private"):
                        profile.verify()
                    with self.assertRaisesRegex(BackendUnavailable, "control-root-not-private"):
                        OrdinaryLocalProfile(profile.path)
                    with self.assertRaisesRegex(ConnectionRegistryError, "control_root.*exactly mode 700"):
                        registry.add("local", candidate, 0, trusted=True)
                    with self.assertRaisesRegex(BackendUnavailable, "ordinary-owner-unverified"):
                        owner._root()
                    self.assertEqual(stat.S_IMODE(self.control.stat().st_mode), mode)
                finally:
                    self.control.chmod(0o700)
        self.assertEqual(registry.read()["revision"], 0)
        profile.verify()
        self.assertEqual(owner._root(), self.control)

    def test_symlink_root_fails_manual_import_as_well_as_inspection(self):
        candidate = prepare_connection(self.request, runner=self.runner)["profile"]
        alias = self.root / "alias"
        alias.symlink_to(self.control, target_is_directory=True)
        candidate["terminal_owner"]["control_root"] = str(alias)
        with self.assertRaisesRegex(BackendUnavailable, "control-root-not-canonical"):
            OrdinaryLocalProfile(self.save_candidate(candidate))
        result = prepare_connection({**self.request, "control_root": str(alias)}, runner=self.runner)
        self.assertIsNone(result["profile"])
        self.assertIn(str(self.control), result["instructions"][0])
        self.assertTrue(alias.is_symlink())

    def test_fifo_source_file_does_not_block(self):
        fifo = self.source / "botainer/no-import.py"
        os.mkfifo(fifo)
        result = prepare_connection(self.request, runner=self.runner)
        self.assertIsNone(result["profile"])

    def test_docker_unavailable_is_a_failure_without_activating_candidate(self):
        def runner(argv, **kwargs):
            if argv[0] == sys.executable: return _run(argv, **kwargs)
            return response({}, 1)
        result = prepare_connection(self.request, runner=runner)
        self.assertIsNone(result["profile"])
        self.assertEqual(result["checks"][0]["name"], "Docker connection")

    def test_discovered_account_home_symlink_is_resolved_without_relaxing_selected_paths(self):
        alias = self.root / "login-home"
        alias.symlink_to(self.home, target_is_directory=True)
        request = dict(self.request); del request["home"]
        payload = base64.b64encode(json.dumps(request).encode()).decode()
        output = io.StringIO()
        # Exercise the exact fixed probe with synthetic OS account metadata;
        # no SSH, Botainer import, hook, Docker command or site setting change.
        with patch("pwd.getpwuid", return_value=SimpleNamespace(pw_dir=str(alias))), \
                patch.object(sys, "argv", ["-", payload]), redirect_stdout(output):
            exec(compile(PROBE_SOURCE, "<fixed-connection-probe>", "exec"), {})
        result = json.loads(output.getvalue())
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["home"], str(self.home))
        self.assertIn(str(self.home), result["directory_identities"])
        self.assertTrue(alias.is_symlink())
        self.assertFalse(any(p.name == "__pycache__" for p in self.source.rglob("*")))
        explicit = prepare_connection({**self.request, "home": str(alias)}, runner=self.runner)
        self.assertIsNone(explicit["profile"])
        self.assertIn("Local home folder (home)", explicit["checks"][0]["name"])
        self.assertIn(str(self.home), explicit["instructions"][0])

    def test_each_missing_selected_directory_names_its_field(self):
        for field in ("source_root", "state_root", "home", "control_root", "project_roots"):
            with self.subTest(field=field):
                missing = str(self.root / ("absent-" + field))
                request = {**self.request, field: [missing] if field == "project_roots" else missing}
                result = prepare_connection(request, runner=self.runner)
                self.assertIsNone(result["profile"])
                self.assertIn(field, result["checks"][0]["name"])
                self.assertFalse(Path(missing).exists())

    def test_selected_symlinked_runtime_roots_still_require_explicit_real_paths(self):
        for field, target in (("source_root", self.source), ("state_root", self.state),
                              ("project_roots", self.projects), ("control_root", self.control)):
            with self.subTest(field=field):
                alias = self.root / ("alias-" + field)
                alias.symlink_to(target, target_is_directory=True)
                request = {**self.request, field: [str(alias)] if field == "project_roots" else str(alias)}
                result = prepare_connection(request, runner=self.runner)
                self.assertIsNone(result["profile"])
                self.assertIn(field, result["checks"][0]["name"])
                self.assertIn(str(target), result["instructions"][0])
                self.assertTrue(alias.is_symlink())


class CandidateFileTests(unittest.TestCase):
    def test_candidate_is_private_exclusive_and_never_overwrites(self):
        tool = Path(__file__).resolve().parents[2] / "tools/prepare_connection.py"
        spec = importlib.util.spec_from_file_location("prepare_connection_tool", tool)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); root.chmod(0o700)
            destination = root / "candidate.json"
            module._write_candidate(destination, {"version": 1})
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError): module._write_candidate(destination, {"version": 2})
            self.assertEqual(json.loads(destination.read_text()), {"version": 1})
            root.chmod(0o755)
            with self.assertRaises(ValueError): module._write_candidate(root / "public.json", {})
            self.assertFalse((root / "public.json").exists())


if __name__ == "__main__":
    unittest.main()
