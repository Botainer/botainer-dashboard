"""Ordinary local integration invariants; no Botainer, Docker or agent runs."""
import importlib.util
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import contextlib
import io
import sys

from botainer_dashboard.backend_errors import BackendUnavailable
from botainer_dashboard.inventory import InventoryError
from botainer_dashboard.ordinary_local import LaunchPhaseViewer, OrdinaryLocalBackend, load_local_profile, sha
from botainer_dashboard.pairing import read_private_json, write_private_json

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("ordinary_local_test_helper", ROOT / "tools/ordinary_local_helper.py")
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)
UID = "00000000-0000-4000-8000-000000000001"
REQUEST = "00000000-0000-4000-8000-000000000002"
REQUEST2 = "00000000-0000-4000-8000-000000000003"
SID = "a" * 16
CID = "b" * 64


class OrdinaryFixture(unittest.TestCase):
    def setUp(self):
        # Terminal ownership has a portable Unix-socket path budget. Keep the
        # fixture short and keep application/control trees outside projects.
        self.temp = TemporaryDirectory(prefix="bd-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.application = self.root / "dashboard"
        for name in ("source/botainer", "state", "home", "projects", "bin", "dashboard/tools", "terminal-control"):
            (self.root / name).mkdir(parents=True, mode=0o700)
        for name in ("source/botainer/__init__.py", "dashboard/tools/ordinary_local_helper.py", "dashboard/tools/ordinary_hook_helper.py", "dashboard/tools/workspace_botainer_helper.py"):
            (self.root / name).write_text("# inert unit fixture\n")
        for name in ("python", "docker"):
            path = self.root / "bin" / name
            path.write_text("# inert unit tool, never executed\n"); path.chmod(0o700)
        self.project = self.root / "projects/existing"
        (self.project / ".botainer").mkdir(parents=True)
        (self.project / ".botainer/project-id").write_text(UID)
        (self.project / ".botainer/config.yaml").write_text("agent: claude\n")
        self.profile_path = self.root / "profile.json"
        self.profile_data = {"version": 1, "id": "local", "label": "Local test", "python": str(self.root / "bin/python"),
            "source_root": str(self.root / "source"), "state_root": str(self.root / "state"), "home": str(self.root / "home"),
            "project_roots": [{"id": "work", "label": "Projects", "path": str(self.root / "projects")}],
            "docker": {"executable": str(self.root / "bin/docker"), "host": "unix:///unit.sock", "daemon_id": "daemon-unit"},
            "environment": {"PATH": str(self.root / "bin")},
            "source_hashes": {"botainer/__init__.py": sha(self.root / "source/botainer/__init__.py")},
            "support_hashes": {str(self.root / "bin" / n): sha(self.root / "bin" / n) for n in ("python", "docker")}}
        write_private_json(self.profile_path, self.profile_data)
        self.raw = {"projects": [{"uuid": UID, "display_name": "Existing", "last_path": str(self.project), "last_session_at": ""}], "sessions": []}
        self.requests = []
        def runner(argv, **kwargs):
            request = read_private_json(Path(argv[-1])); self.requests.append(request)
            self.assertEqual(request["action"], "inventory")
            return json.dumps(self.raw).encode()
        self.runner = Mock(side_effect=runner)
        self.consoles = []
        def console(argv, **kwargs):
            value = SimpleNamespace(done=False, cleanup_confirmed=False, viewer=None,
                owner_fd=kwargs["owner_fd"], argv=argv, kwargs=kwargs)
            self.consoles.append(value)
            return value
        self.backend = OrdinaryLocalBackend(self.application, self.profile_path, runner=self.runner, console_factory=console)
        self.addCleanup(self.cleanup_consoles)

    def cleanup_consoles(self):
        for console in self.consoles:
            if console.owner_fd is not None: os.close(console.owner_fd); console.owner_fd = None

    def finish(self, receipt):
        console = self.consoles[-1]
        console.done = True; console.cleanup_confirmed = True
        os.close(console.owner_fd); console.owner_fd = None
        intent = self.backend.root / (REQUEST + ".intent.json")
        write_private_json(self.backend.root / (REQUEST + ".result.json"), {
            "request_id": REQUEST, "intent_sha256": sha(intent), **receipt})
        self.backend._last = 0

    def binding(self):
        return {"id": CID, "name": "/botainer-" + SID[:12], "image": "sha256:image", "created": "created", "started_at": "started"}

    def running(self):
        return {"project_uuid": UID, "session_id": SID, "runtime": "docker", "started_at": "started",
                "ended_at": None, "state": "running", "binding": self.binding(), "interactive": True}


class ProfileTests(OrdinaryFixture):
    def test_only_selected_native_docker_support_pin_can_use_applications_allowance(self):
        from botainer_dashboard.import_policy import trusted_path
        hook = self.root / 'state/hook.py'
        hook.write_text('# reviewed, never executed\n')
        self.save_hook_approval(hook, support_pin=True)
        with patch('botainer_dashboard.ordinary_local.trusted_path', wraps=trusted_path) as check:
            load_local_profile(self.profile_path).verify()
        permitted = [call.args[0] for call in check.call_args_list
                     if call.kwargs.get('allow_macos_docker_app')]
        self.assertEqual(permitted, [self.root / 'bin/docker'])
        self.runner.assert_not_called()

    def test_docker_pin_used_as_hook_does_not_get_applications_allowance(self):
        from botainer_dashboard.import_policy import trusted_path
        self.save_hook_approval(self.root / 'bin/docker', support_pin=True)
        with patch('botainer_dashboard.ordinary_local.trusted_path', wraps=trusted_path) as check:
            load_local_profile(self.profile_path).verify()
        self.assertFalse(any(call.kwargs.get('allow_macos_docker_app')
                             for call in check.call_args_list))

    def test_changed_docker_bytes_still_refuse_before_execution(self):
        (self.root / 'bin/docker').write_text('# replacement native tool\n')
        with self.assertRaisesRegex(BackendUnavailable, 'installation-changed-requalification-required'):
            load_local_profile(self.profile_path).verify()
        self.runner.assert_not_called()

    def save_hook_approval(self, hook, *, source_pin=False, support_pin=False, digest=None):
        digest = digest or sha(hook)
        if source_pin:
            self.profile_data['source_hashes'][str(hook.relative_to(self.root / 'source'))] = digest
        if support_pin:
            self.profile_data['support_hashes'][str(hook)] = digest
        self.profile_data['approved_hooks'] = {str(hook): digest}
        write_private_json(self.profile_path, self.profile_data)

    def test_hook_approval_accepts_exact_source_and_installed_state_pins_without_execution(self):
        source = self.root / 'source/plugins/example/hooks/start.py'
        state = self.root / 'state/plugins/example/hooks/stop.py'
        for hook in (source, state):
            hook.parent.mkdir(parents=True)
            hook.write_text('raise RuntimeError("hook must never execute during review")\n')
        self.profile_data['source_hashes'][str(source.relative_to(self.root / 'source'))] = sha(source)
        self.profile_data['support_hashes'][str(state)] = sha(state)
        self.profile_data['approved_hooks'] = {str(hook): sha(hook) for hook in (source, state)}
        write_private_json(self.profile_path, self.profile_data)
        profile = load_local_profile(self.profile_path); profile.verify()
        for hook in (source, state): profile.verify_hook(hook)
        self.runner.assert_not_called()

    def test_hook_approval_without_source_or_support_pin_is_refused(self):
        hook = self.root / 'unreviewed.py'; hook.write_text('# never executed\n')
        self.save_hook_approval(hook)
        with self.assertRaisesRegex(BackendUnavailable, 'approval-must-match-reviewed-source'):
            load_local_profile(self.profile_path)

    def test_hook_approval_digest_must_match_every_applicable_pin(self):
        hook = self.root / 'source/hook.py'; hook.write_text('# never executed\n')
        self.save_hook_approval(hook, source_pin=True, support_pin=True)
        profile = load_local_profile(self.profile_path); profile.verify_hook(hook)
        # One matching map cannot conceal a conflicting second map.
        self.profile_data['support_hashes'][str(hook)] = 'f' * 64
        write_private_json(self.profile_path, self.profile_data)
        with self.assertRaisesRegex(BackendUnavailable, 'approval-must-match-reviewed-source'):
            load_local_profile(self.profile_path)
        del self.profile_data['support_hashes'][str(hook)]
        self.profile_data['approved_hooks'][str(hook)] = 'e' * 64
        write_private_json(self.profile_path, self.profile_data)
        with self.assertRaisesRegex(BackendUnavailable, 'approval-must-match-reviewed-source'):
            load_local_profile(self.profile_path)

    def test_hook_approval_rejects_symlink_ancestry_even_with_matching_hash_maps(self):
        destination = self.root / 'actual'; destination.mkdir()
        hook = destination / 'hook.py'; hook.write_text('# never executed\n')
        alias = self.root / 'alias'; alias.symlink_to(destination, target_is_directory=True)
        self.save_hook_approval(alias / 'hook.py', support_pin=True)
        with self.assertRaisesRegex(BackendUnavailable, 'not-canonical'):
            load_local_profile(self.profile_path)

    def test_changed_approved_hook_refuses_revalidation_and_launch_hook_check(self):
        hook = self.root / 'state/hooks/start.py'; hook.parent.mkdir()
        hook.write_text('# reviewed bytes\n'); self.save_hook_approval(hook, support_pin=True)
        profile = load_local_profile(self.profile_path); profile.verify()
        hook.write_text('# replacement bytes\n')
        with self.assertRaisesRegex(BackendUnavailable, 'installation-changed'): profile.verify()
        with self.assertRaisesRegex(BackendUnavailable, 'hook-review-required'): profile.verify_hook(hook)

    def test_missing_approved_hook_refuses_load_before_execution(self):
        hook = self.root / 'hook.py'; hook.write_text('# reviewed bytes\n')
        self.save_hook_approval(hook, support_pin=True); hook.unlink()
        with self.assertRaises((BackendUnavailable, FileNotFoundError)):
            load_local_profile(self.profile_path)

    def test_wheel_state_hook_approval_does_not_require_approving_bundled_resource(self):
        self.wheel_profile()
        hook = self.root / 'state/plugins/example/hooks/start.py'; hook.parent.mkdir(parents=True)
        hook.write_text('raise RuntimeError("must not execute")\n')
        self.save_hook_approval(hook, support_pin=True)
        profile = load_local_profile(self.profile_path); profile.verify(); profile.verify_hook(hook)
        self.assertEqual(profile.data['approved_hooks'], {str(hook): sha(hook)})

    def wheel_profile(self):
        metadata = self.root / 'source/botainer-0.1.0a5.dist-info'
        metadata.mkdir()
        (metadata / 'METADATA').write_text('Name: botainer\nVersion: 0.1.0a5\n')
        (metadata / 'entry_points.txt').write_text('[console_scripts]\nbotainer = botainer.cli.main:main\n')
        self.profile_data.update(version=2, installation_layout={
            'kind': 'wheel', 'metadata_path': 'botainer-0.1.0a5.dist-info/METADATA'})
        self.profile_data['source_hashes'].update({str(path.relative_to(self.root / 'source')): sha(path)
            for path in metadata.iterdir()})
        write_private_json(self.profile_path, self.profile_data)
        return metadata

    def test_installed_profile_preserves_namespace_but_requires_metadata_pins(self):
        metadata = self.wheel_profile()
        profile = load_local_profile(self.profile_path); profile.verify()
        self.assertEqual(profile.namespace, self.backend.profile.namespace)
        (metadata / 'METADATA').write_text('Name: botainer\nVersion: 0.1.0a4\n')
        with self.assertRaisesRegex(BackendUnavailable, 'installed-layout'): profile.verify()

    def test_installed_profile_duplicate_distribution_or_wrong_layout_refuses(self):
        self.wheel_profile(); duplicate = self.root / 'source/botainer-0.1.0a4.dist-info'; duplicate.mkdir()
        with self.assertRaisesRegex(BackendUnavailable, 'installed-layout'): load_local_profile(self.profile_path)
        duplicate.rmdir()
        self.profile_data['installation_layout']['kind'] = 'source'
        write_private_json(self.profile_path, self.profile_data)
        with self.assertRaisesRegex(BackendUnavailable, 'installed-layout'): load_local_profile(self.profile_path)

    def test_profile_load_is_pure_and_source_addition_is_refused(self):
        profile = load_local_profile(self.profile_path)
        self.assertEqual(profile.id, "local"); self.assertEqual(profile.fingerprint, sha(self.profile_path))
        self.runner.assert_not_called(); profile.verify()
        (self.root / "source/botainer/unreviewed.py").write_text("# changed source\n")
        with self.assertRaisesRegex(BackendUnavailable, "module-set-changed"): profile.verify()

    def test_changed_source_and_path_docker_selection_refused(self):
        profile = load_local_profile(self.profile_path)
        (self.root / "source/botainer/__init__.py").write_text("# changed\n")
        with self.assertRaisesRegex(BackendUnavailable, "source-changed"): profile.verify()
        (self.root / "bin/docker").unlink()
        with self.assertRaises((BackendUnavailable, OSError)): profile.verify()

    def test_unreviewed_hook_never_runs(self):
        profile = load_local_profile(self.profile_path)
        hook = self.root / "hook.py"; hook.write_text("# no\n")
        with self.assertRaisesRegex(BackendUnavailable, "hook-review-required"): profile.verify_hook(hook)
        # Merely recording the bytes of a dependency is not hook authorization.
        profile.data["support_hashes"][str(hook)] = sha(hook)
        with self.assertRaisesRegex(BackendUnavailable, "hook-review-required"): profile.verify_hook(hook)
        profile.data["approved_hooks"] = {str(hook): sha(hook)}
        profile.verify_hook(hook)


class DiscoveredProjectHelperTests(OrdinaryFixture):
    def setUp(self):
        super().setUp()
        outside = self.root / "elsewhere"; self.project.rename(outside); self.project = outside
        self.data = {"project_path": str(outside), "project_uuid": UID,
            "project_identity": [outside.stat().st_dev, outside.stat().st_ino]}
        self.state = SimpleNamespace(
            ensure_user_state_dir=Mock(return_value=SimpleNamespace(root=self.root / "state")),
            list_projects=Mock(return_value=[SimpleNamespace(uuid=UID, last_path=str(outside))]))

    def check(self):
        with patch.dict(sys.modules, {"botainer.state": SimpleNamespace(dir=self.state)}):
            return helper.project_check(self.data, self.backend.profile)

    def test_native_registry_confirms_exact_external_project_without_running_or_writing(self):
        before = (self.project / ".botainer/project-id").read_bytes()
        self.assertEqual(self.check(), self.project)
        self.state.ensure_user_state_dir.assert_called_once_with(create_if_missing=False)
        self.assertEqual((self.project / ".botainer/project-id").read_bytes(), before)
        self.assertEqual(self.consoles, []); self.runner.assert_not_called()

    def test_copied_uuid_wrong_installation_or_ambiguous_registry_never_authorize_an_external_path(self):
        cases = [[], [SimpleNamespace(uuid=REQUEST, last_path=str(self.project))],
            [SimpleNamespace(uuid=UID, last_path=str(self.root / "another-project"))],
            [SimpleNamespace(uuid=UID, last_path=str(self.project))] * 2,
            [SimpleNamespace(uuid=UID, last_path=str(self.project)),
             SimpleNamespace(uuid=UID.replace("-", ""), last_path=str(self.project))]]
        for entries in cases:
            with self.subTest(entries=entries):
                self.state.list_projects.return_value = entries
                with self.assertRaisesRegex(ValueError, "not registered at this path"): self.check()
        self.state.ensure_user_state_dir.return_value = SimpleNamespace(root=self.root / "other-installation")
        with self.assertRaisesRegex(ValueError, "state root changed"): self.check()

    def test_external_uuid_inode_and_metadata_symlink_changes_are_refused(self):
        self.data["project_identity"] = [0, 0]
        with self.assertRaisesRegex(ValueError, "directory identity changed"): self.check()
        self.data.pop("project_identity")
        (self.project / ".botainer/project-id").write_text(REQUEST)
        with self.assertRaisesRegex(ValueError, "UUID changed"): self.check()
        metadata = self.project / ".botainer"; metadata.rename(self.project / "old-metadata")
        metadata.symlink_to(self.project / "old-metadata", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "metadata must not be a symlink"): self.check()


class BackendTests(OrdinaryFixture):
    def durable_owner(self):
        record = {"id": REQUEST.replace("-", ""), "state": "running", "owner": {"nonce": "test-owner"}}
        self.backend.owner = Mock()
        self.backend.owner.launch.return_value = record
        self.backend.owner.recover.return_value = record
        self.backend.owner.observe.return_value = {"state": "running"}
        self.backend.owner.capture.return_value = b"native preflight\n"
        self.backend._owner_module = self.backend.helper
        self.backend._owner_sha = sha(self.backend.helper)
        return record

    def owner_receipt(self, value):
        write_private_json(self.backend.root / (REQUEST + ".result.json"), {
            "request_id": REQUEST, "intent_sha256": sha(self.backend.root / (REQUEST + ".intent.json")), **value})
        self.backend._last = 0

    def unconfirmed_startup(self):
        self.durable_owner()
        project = self.backend.snapshot()["projects"][0]
        self.backend.start_session(project["id"], REQUEST)
        return {"phase": "dispatch-unconfirmed", "session_id": SID,
            "candidate_binding": {key: value for key, value in self.binding().items() if key != "started_at"},
            "failure_code": "ordinary-startup-timeout"}

    def test_unconfirmed_created_candidate_recovers_only_after_same_runtime_is_ready(self):
        receipt = self.unconfirmed_startup()
        self.owner_receipt(receipt)
        path = self.backend.root / (REQUEST + ".result.json")
        original = path.read_bytes()
        self.raw["sessions"] = [{**self.running(), "state": "unknown",
            "binding": self.binding() | {"started_at": "0001-01-01T00:00:00Z"}}]
        pending = self.backend.snapshot()
        launch = next(s for s in pending["sessions"] if s.get("kind") == "launch")
        self.assertEqual(launch["launchState"], "waiting")
        self.assertIn("not yet verified", launch["launchReason"])
        self.assertNotIn(SID, read_private_json(self.backend._bindings_path)["sessions"])
        self.assertFalse(pending["projects"][0]["capabilities"]["startSession"])
        client = Mock()
        viewer = LaunchPhaseViewer(client, path, self.backend._operations()["operations"][0],
            journal=self.backend._operations_path)
        self.assertFalse(viewer._ready())
        self.raw["sessions"] = [self.running()]; self.backend._last = 0
        value = self.backend.snapshot()
        entry = self.backend._operations()["operations"][0]
        self.assertEqual(entry["state"], "completed")
        self.assertTrue(entry["startup_reconciled"])
        self.assertEqual(entry["qualified_binding"], self.binding())
        self.assertEqual(entry["result_target"], {"contextNamespace": self.backend.namespace, "runtimeId": SID})
        runtime = next(s for s in value["sessions"] if s.get("kind") != "launch")
        self.assertTrue(runtime["capabilities"]["attachTerminal"])
        self.assertEqual(read_private_json(self.backend._bindings_path)["sessions"][SID], self.binding())
        self.assertEqual(path.read_bytes(), original)
        with self.assertRaises(BrokenPipeError): viewer.write(b"must not reach the runtime")
        client.write.assert_not_called(); client.close.assert_called_once()
        self.backend.owner.launch.assert_called_once(); self.backend.owner.attach.assert_not_called()

    def test_created_timestamp_from_older_inventory_can_advance_but_live_binding_cannot(self):
        receipt = self.unconfirmed_startup()
        self.raw["sessions"] = [self.running()]
        old = self.binding() | {"started_at": "previous-running-start"}
        write_private_json(self.backend._bindings_path, {"sessions": {SID: old}})
        self.owner_receipt(receipt)
        self.backend.snapshot()
        self.assertNotIn("result_target", self.backend._operations()["operations"][0])
        self.assertEqual(read_private_json(self.backend._bindings_path)["sessions"][SID], old)
        write_private_json(self.backend._bindings_path, {"sessions": {
            SID: self.binding() | {"started_at": "0001-01-01T00:00:00Z"}}})
        self.backend._last = 0; self.backend.snapshot()
        self.assertEqual(self.backend._operations()["operations"][0]["state"], "completed")
        self.assertEqual(read_private_json(self.backend._bindings_path)["sessions"][SID], self.binding())

    def test_unconfirmed_candidate_requires_full_immutable_identity_and_retryable_reason(self):
        receipt = self.unconfirmed_startup()
        self.raw["sessions"] = [self.running()]
        candidates = [None, receipt["candidate_binding"] | {"id": "c" * 64},
            receipt["candidate_binding"] | {"name": "/botainer-other"},
            receipt["candidate_binding"] | {"image": "sha256:other"},
            receipt["candidate_binding"] | {"created": "different-created"}]
        cases = [receipt | {"candidate_binding": candidate} for candidate in candidates]
        cases += [receipt | {"failure_code": "ordinary-startup-owner-replaced"},
            receipt | {"cli_ended": True, "exit_code": 1}, receipt | {"cli_ended": "false"},
            receipt | {"request_id": REQUEST2}, receipt | {"intent_sha256": "f" * 64}]
        for value in cases:
            with self.subTest(receipt=value):
                self.owner_receipt(value)
                snapshot = self.backend.snapshot()
                entry = self.backend._operations()["operations"][0]
                self.assertNotIn("result_target", entry)
                runtime = next(s for s in snapshot["sessions"] if s.get("kind") != "launch")
                self.assertFalse(runtime["capabilities"]["attachTerminal"])
                self.assertFalse(runtime["capabilities"]["stopSession"])
                self.assertNotIn(SID, read_private_json(self.backend._bindings_path)["sessions"])
        self.backend.owner.launch.assert_called_once(); self.backend.owner.attach.assert_not_called()

    def test_missing_or_invalid_receipt_identity_never_enables_direct_docker_fallback(self):
        receipt = self.unconfirmed_startup()
        self.raw["sessions"] = [self.running()]
        self.owner_receipt(receipt | {"request_id": REQUEST2})
        snapshot = self.backend.snapshot()
        self.assertNotIn("native_session_id", self.backend._operations()["operations"][0])
        runtime = next(s for s in snapshot["sessions"] if s.get("kind") != "launch")
        self.assertFalse(runtime["capabilities"]["attachTerminal"])
        self.assertFalse(runtime["capabilities"]["stopSession"])
        with self.assertRaises(BackendUnavailable): self.backend.attach(self.backend.namespace, SID, 80, 24)
        self.backend.owner.attach.assert_not_called()

    def test_unconfirmed_handoff_rejects_malformed_journal_without_forwarding_input(self):
        self.owner_receipt(self.unconfirmed_startup())
        entry = self.backend._operations()["operations"][0]
        client = Mock()
        viewer = LaunchPhaseViewer(client, self.backend.root / (REQUEST + ".result.json"), entry,
            journal=self.backend._operations_path)
        for journal in ({}, {"operations": {}}, {"operations": [None]},
                        {"operations": [entry | {"result_target": []}]}):
            write_private_json(self.backend._operations_path, journal)
            with self.assertRaisesRegex(BackendUnavailable, "ordinary-launch-journal-invalid"):
                viewer.write(b"must not be forwarded")
        client.write.assert_not_called()

    def test_unconfirmed_startup_requires_live_original_owner_and_qualified_project(self):
        receipt = self.unconfirmed_startup()
        cases = [(self.running(), "ended", {}), (self.running(), "unknown", {}),
            (self.running() | {"interactive": False}, "running", {}),
            (self.running() | {"state": "unknown"}, "running", {}),
            (self.running() | {"binding": self.binding() | {"started_at": "0001-01-01T00:00:00Z"}}, "running", {}),
            (self.running() | {"binding": self.binding() | {"started_at": 1}}, "running", {}),
            (self.running(), "running", {"runtime_unverified": True}),
            (self.running(), "running", {"last_path": str(self.root / "different-project")})]
        for session, owner_state, project_changes in cases:
            with self.subTest(owner=owner_state, session=session, project=project_changes):
                self.raw["projects"] = [{"uuid": UID, "display_name": "Existing", "last_path": str(self.project), **project_changes}]
                self.raw["sessions"] = [session]
                self.backend.owner.observe.return_value = {"state": owner_state}
                self.owner_receipt(receipt)
                self.backend.snapshot()
                self.assertNotIn("result_target", self.backend._operations()["operations"][0])
                if owner_state == "ended":
                    self.assertIn(b"native preflight", (self.backend.root / (REQUEST + ".bin")).read_bytes())
        self.backend.owner.launch.assert_called_once()

    def test_unconfirmed_startup_rejects_another_projects_same_session_id_and_changed_intent(self):
        receipt = self.unconfirmed_startup()
        other_uid = "00000000-0000-4000-8000-000000000099"
        other = self.root / "projects/other"
        (other / ".botainer").mkdir(parents=True)
        (other / ".botainer/project-id").write_text(other_uid)
        self.raw["projects"].append({"uuid": other_uid, "display_name": "Other", "last_path": str(other)})
        self.raw["sessions"] = [self.running() | {"project_uuid": other_uid}]
        self.owner_receipt(receipt); self.backend.snapshot()
        self.assertNotIn("result_target", self.backend._operations()["operations"][0])
        self.raw["sessions"] = [self.running()]
        intent_path = self.backend.root / (REQUEST + ".intent.json")
        intent = read_private_json(intent_path)
        write_private_json(intent_path, intent | {"project_uuid": other_uid})
        self.backend._last = 0; self.backend.snapshot()
        self.assertNotIn("result_target", self.backend._operations()["operations"][0])
        self.backend.owner.launch.assert_called_once()

    def test_durable_pending_cli_survives_without_service_console_and_dispatches_once(self):
        self.durable_owner()
        project = self.backend.snapshot()["projects"][0]
        self.backend.start_session(project["id"], REQUEST)
        self.assertEqual(self.consoles, [])
        self.backend.start_session(project["id"], REQUEST)
        self.backend.owner.launch.assert_called_once()
        self.assertFalse(self.backend.snapshot()["projects"][0]["capabilities"]["startSession"])
        self.assertEqual(self.backend._operations()["operations"][0]["state"], "waiting")

    def test_pending_history_is_plain_text_from_exact_owner_without_terminal_input(self):
        record = self.durable_owner()
        project = self.backend.snapshot()["projects"][0]
        self.backend.start_session(project["id"], REQUEST)
        self.backend.owner.capture.return_value = b'\x1b[31mWarning\x1b[0m\r\n\x1b]52;c;c2VjcmV0\x07<html>\x00'
        before = self.backend._operations()
        result = self.backend.read_terminal_history(self.backend.launch_namespace, REQUEST)
        self.assertEqual(result, {"text": "Warning\n<html>", "kind": "terminal-snapshot", "truncated": False})
        self.backend.owner.capture.assert_called_once_with(record, allow_running=True)
        self.backend.owner.launch.assert_called_once()
        self.backend.owner.attach.assert_not_called()
        self.assertEqual(self.backend._operations(), before)
        self.backend.owner.capture.side_effect = InventoryError("command-timeout")
        with self.assertRaisesRegex(BackendUnavailable, "^ordinary-history-capture-unavailable$"):
            self.backend.read_terminal_history(self.backend.launch_namespace, REQUEST)

    def test_runtime_history_validates_capability_without_recursive_operation_lock(self):
        self.durable_owner()
        project = self.backend.snapshot()["projects"][0]
        self.backend.start_session(project["id"], REQUEST)
        self.owner_receipt({"phase": "runtime-returned", "session_id": SID, "binding": self.binding()})
        self.raw["sessions"] = [self.running()]
        self.assertEqual(self.backend.read_terminal_history(self.backend.namespace, SID)["text"], "native preflight\n")
        self.backend.owner.attach.assert_not_called()
        self.backend.owner.launch.assert_called_once()

    def test_history_scope_and_payload_bounds_fail_closed(self):
        self.durable_owner()
        project = self.backend.snapshot()["projects"][0]
        self.backend.start_session(project["id"], REQUEST)
        for namespace, identity in (("wrong", REQUEST), (self.backend.launch_namespace, REQUEST2),
                                    (self.backend.namespace, SID), (self.backend.namespace, "../other")):
            with self.assertRaises(BackendUnavailable): self.backend.read_terminal_history(namespace, identity)
        self.backend.owner.capture.assert_not_called()
        self.backend.owner.capture.return_value = b"x" * 262144 + b"tail"
        result = self.backend.read_terminal_history(self.backend.launch_namespace, REQUEST)
        self.assertTrue(result["truncated"]); self.assertEqual(len(result["text"]), 262144)
        self.assertTrue(result["text"].endswith("tail"))
        self.backend.owner.capture.return_value = b"[Earlier terminal output omitted.]\r\nretained"
        self.assertTrue(self.backend.read_terminal_history(self.backend.launch_namespace, REQUEST)["truncated"])
        self.backend.owner.capture.return_value = b"\xff" * 262144
        result = self.backend.read_terminal_history(self.backend.launch_namespace, REQUEST)
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["text"].encode("utf-8")), 262144)

    def test_ready_receipt_hands_off_while_native_cli_is_still_alive(self):
        self.durable_owner()
        project = self.backend.snapshot()["projects"][0]
        self.backend.start_session(project["id"], REQUEST)
        self.owner_receipt({"phase": "runtime-returned", "session_id": SID, "binding": self.binding()})
        self.raw["sessions"] = [self.running()]
        value = self.backend.snapshot()
        launch = next(s for s in value["sessions"] if s.get("kind") == "launch")
        self.assertEqual(launch["launchState"], "completed")
        self.assertEqual(launch["resultTarget"]["runtimeId"], SID)
        self.backend.owner.capture.assert_called_once_with(self.backend.owner.recover.return_value, allow_running=True)
        self.assertTrue((self.backend.root / (REQUEST + ".bin")).exists())

    def test_log_capture_failure_cannot_erase_verified_runtime_dispatch(self):
        self.durable_owner()
        self.backend.owner.capture.side_effect = BackendUnavailable("ordinary-owner-unverified")
        project = self.backend.snapshot()["projects"][0]
        self.backend.start_session(project["id"], REQUEST)
        self.owner_receipt({"phase": "runtime-returned", "session_id": SID, "binding": self.binding()})
        value = self.backend.snapshot()
        self.assertEqual(value["sessions"][0]["launchState"], "completed")
        self.assertTrue(self.backend._operations()["operations"][0]["log_capture_failed"])

    def test_live_durable_cli_with_missing_receipt_is_not_declined_or_retried(self):
        self.durable_owner()
        project = self.backend.snapshot()["projects"][0]
        self.backend.start_session(project["id"], REQUEST)
        self.owner_receipt({"phase": "before-dispatch", "session_id": SID})
        launch = self.backend.snapshot()["sessions"][0]
        self.assertEqual(launch["launchState"], "waiting")
        self.backend.owner.observe.return_value = {"state": "ended"}
        self.backend._last = 0
        self.assertEqual(self.backend.snapshot()["sessions"][0]["launchState"], "unknown")
        self.backend.owner.launch.assert_called_once()

    def test_transient_owner_failure_recovers_same_owner_without_direct_container_fallback(self):
        self.durable_owner()
        project = self.backend.snapshot()["projects"][0]
        self.backend.start_session(project["id"], REQUEST)
        self.owner_receipt({"phase": "runtime-returned", "session_id": SID, "binding": self.binding()})
        self.raw["sessions"] = [self.running()]
        self.backend.owner.observe.return_value = {"state": "unknown"}
        value = self.backend.snapshot()
        session = next(s for s in value["sessions"] if s["runtimeId"] == SID)
        self.assertFalse(session["capabilities"]["attachTerminal"])
        self.assertFalse(session["capabilities"]["stopSession"])
        self.backend.owner.observe.return_value = {"state": "running"}; self.backend._last = 0
        value = self.backend.snapshot()
        launch = next(s for s in value["sessions"] if s.get("kind") == "launch")
        self.assertEqual(launch["launchState"], "completed")
        self.assertEqual(launch["resultTarget"]["runtimeId"], SID)
        self.backend.owner.launch.assert_called_once()

    def test_container_absence_does_not_allow_new_start_during_native_cleanup(self):
        self.durable_owner()
        project = self.backend.snapshot()["projects"][0]
        self.backend.start_session(project["id"], REQUEST)
        self.owner_receipt({"phase": "runtime-returned", "session_id": SID, "binding": self.binding()})
        value = self.backend.snapshot()
        self.assertFalse(value["projects"][0]["capabilities"]["startSession"])
        self.assertFalse(value["projects"][0]["capabilities"]["configWrite"])
        self.assertIn("finishing session cleanup", value["projects"][0]["unavailableReason"])
        self.backend.owner.observe.return_value = {"state": "ended"}; self.backend._last = 0
        self.assertTrue(self.backend.snapshot()["projects"][0]["capabilities"]["startSession"])

    def test_idle_inventory_includes_project_and_uses_one_batched_query(self):
        value = self.backend.snapshot()
        self.assertEqual(len(value["projects"]), 1)
        self.assertTrue(value["projects"][0]["capabilities"]["startSession"])
        self.backend.snapshot()
        self.assertEqual(len(self.requests), 1)

    def test_legacy_native_project_ids_are_visible_without_disabling_valid_projects(self):
        legacy = "0123456789ab"
        self.raw["projects"].append({"uuid": legacy, "display_name": "Legacy project", "last_path": "/old/project"})
        self.raw["sessions"] = [{**self.running(), "project_uuid": legacy}]
        value = self.backend.snapshot()
        self.assertEqual(value["connectionStatus"], "available")
        self.assertEqual(len(value["projects"]), 2)
        self.assertTrue(value["projects"][0]["capabilities"]["startSession"])
        self.assertEqual(value["projects"][1]["identityCompatibility"], "legacy-read-only")
        self.assertEqual(value["projects"][1]["controlRestriction"], "legacy-project-identity")
        self.assertFalse(value["projects"][1]["capabilities"]["configWrite"])
        self.assertFalse(value["sessions"][0]["capabilities"]["attachTerminal"])
        self.assertEqual(value["sessions"][0]["state"], "running")

    def test_changed_profile_alias_only_quarantines_prior_project_registration(self):
        self.backend.snapshot()
        self.profile_data["id"] = "renamed"
        write_private_json(self.profile_path, self.profile_data)
        restarted = OrdinaryLocalBackend(self.application, self.profile_path, runner=self.runner)
        value = restarted.snapshot()
        self.assertEqual(value["connectionStatus"], "available")
        self.assertFalse(value["projects"][0]["capabilities"]["startSession"])
        self.assertIn("alias changed", value["projects"][0]["unavailableReason"])
        self.assertEqual(value["projects"][0]["controlRestriction"], "installation-relink-required")

    def test_runtime_uncertainty_overrides_pure_folder_restriction(self):
        self.raw["projects"][0].update(last_path=str(self.root / "outside"), runtime_unverified=True)
        value = self.backend.snapshot()
        self.assertNotIn("controlRestriction", value["projects"][0])
        self.assertIn("ownership need reconciliation", value["projects"][0]["unavailableReason"])
        self.assertFalse(value["projects"][0]["capabilities"]["startSession"])

    def test_unverified_folder_live_runtime_is_visible_but_no_attach_or_stop(self):
        outside = self.root / "outside"; outside.mkdir()
        self.raw["projects"][0]["last_path"] = str(outside)
        self.raw["sessions"] = [self.running()]
        value = self.backend.snapshot()
        self.assertEqual(value["sessions"][0]["state"], "running")
        self.assertEqual(value["projects"][0]["controlRestriction"], "project-registration-unavailable")
        self.assertIn("Botainer identity has changed", value["projects"][0]["unavailableReason"])
        for capability in ("attachTerminal", "stopSession"):
            self.assertFalse(value["sessions"][0]["capabilities"][capability])
            with self.assertRaises(BackendUnavailable):
                self.backend._target(self.backend.namespace, SID, capability)
        with self.assertRaisesRegex(ValueError, "not registered with the selected Botainer"):
            helper.project_check({"project_path": str(outside)}, self.backend.profile)

    def test_running_external_session_explains_launch_block_without_hiding_project_files(self):
        self.raw["sessions"] = [self.running()]
        project = self.backend.snapshot()["projects"][0]
        self.assertFalse(project["capabilities"]["startSession"])
        self.assertTrue(project["capabilities"]["filesRead"])
        self.assertTrue(project["capabilities"]["configRead"])
        self.assertNotIn("unavailableReason", project)
        self.assertIn("outside the dashboard", project["startUnavailableReason"])
        self.assertIn("original terminal", project["startUnavailableReason"])
        self.raw["sessions"][0]["state"] = "stopped"; self.backend._last = 0
        project = self.backend.snapshot()["projects"][0]
        self.assertTrue(project["capabilities"]["startSession"])
        self.assertNotIn("startUnavailableReason", project)

    def test_unknown_session_launch_reason_preserves_more_specific_project_problem(self):
        self.raw["sessions"] = [{**self.running(), "state": "unknown"}]
        project = self.backend.snapshot()["projects"][0]
        self.assertIn("state is unverified", project["startUnavailableReason"])
        self.assertFalse(project["capabilities"]["startSession"])
        self.assertTrue(project["capabilities"]["filesRead"])
        self.raw["projects"][0]["runtime_unverified"] = True; self.backend._last = 0
        project = self.backend.snapshot()["projects"][0]
        self.assertEqual(project["unavailableReason"], "Runtime records or container ownership need reconciliation")
        self.assertIn("state is unverified", project["startUnavailableReason"])

    def test_cleanup_uncertainty_does_not_overwrite_ambiguous_project_registration(self):
        self.owner_receipt(self.unconfirmed_startup())
        self.raw["sessions"] = [self.running()]; self.backend._last = 0
        self.backend.snapshot()
        self.assertEqual(self.backend._operations()["operations"][0]["state"], "completed")
        self.raw["projects"].append(dict(self.raw["projects"][0]))
        self.backend.owner.observe.return_value = {"state": "unknown"}; self.backend._last = 0
        project = self.backend.snapshot()["projects"][0]
        self.assertEqual(project["controlRestriction"], "project-registration-ambiguous")
        self.assertIn("multiple registrations", project["unavailableReason"])
        self.assertIn("state is unverified", project["startUnavailableReason"])

    def test_native_project_outside_setup_roots_is_exactly_bound_and_can_start(self):
        outside = self.root / "elsewhere"; self.project.rename(outside)
        self.project = outside; self.raw["projects"][0]["last_path"] = str(outside)
        (outside / "README.md").write_text("Project text\n")
        (self.root / "sibling.txt").write_text("Not part of the project\n")
        project = self.backend.snapshot()["projects"][0]
        for capability in ("filesRead", "configRead", "configWrite", "startSession"):
            self.assertTrue(project["capabilities"][capability])
        self.assertEqual(self.backend.read_file(project["id"], "README.md")["text"], "Project text\n")
        self.assertEqual(self.backend.read_config(project["id"])["text"], "agent: claude\n")
        self.assertEqual(self.backend.workspace_metadata()["roots"], [{"id": "work", "label": "Projects", "path": str(self.root / "projects")}])
        record = self.backend.workspace._record(project["id"])
        self.assertEqual(record["relativePath"], "")
        self.assertTrue(self.backend.workspace.roots[record["rootId"]].project_only)
        with self.assertRaises(ValueError): self.backend.read_file(project["id"], "../sibling.txt")
        with self.assertRaises(BackendUnavailable):
            self.backend.create_project({"mode": "create", "installationId": "local", "rootId": record["rootId"],
                "path": "nested", "requestId": REQUEST, "name": "Not permitted"})
        self.assertFalse((outside / "nested").exists())
        self.backend.start_session(project["id"], REQUEST, "codex")
        self.assertEqual(len(self.consoles), 1)
        intent = read_private_json(self.backend.root / (REQUEST + ".intent.json"))
        self.assertEqual(intent["project_path"], str(outside)); self.assertEqual(intent["project_uuid"], UID)
        self.assertEqual(intent["profile_sha256"], self.backend.profile.digest)

    def test_discovered_exact_project_recovers_after_restart_without_changing_scope_or_identity(self):
        outside = self.root / "elsewhere"; self.project.rename(outside)
        self.raw["projects"][0]["last_path"] = str(outside)
        original = self.backend.snapshot()["projects"][0]
        before = self.profile_path.read_bytes()
        replacement = OrdinaryLocalBackend(self.application, self.profile_path, runner=self.runner)
        restored = replacement.snapshot()["projects"][0]
        self.assertEqual(restored["id"], original["id"])
        self.assertTrue(restored["capabilities"]["startSession"])
        self.assertEqual(replacement.namespace, self.backend.namespace)
        self.assertEqual(replacement.profile.fingerprint, self.backend.profile.fingerprint)
        self.assertEqual(self.profile_path.read_bytes(), before)

    def test_multiple_unrelated_registered_projects_are_discovered_without_parent_grants(self):
        first = self.root / "first-project"; self.project.rename(first)
        second = self.root / "another-location" / "second-project"
        (second / ".botainer").mkdir(parents=True)
        (second / ".botainer/project-id").write_text(REQUEST)
        (second / ".botainer/config.yaml").write_text("agent: codex\n")
        self.raw["projects"] = [
            {"uuid": UID, "display_name": "First", "last_path": str(first)},
            {"uuid": REQUEST, "display_name": "Second", "last_path": str(second)}]
        projects = self.backend.snapshot()["projects"]
        self.assertEqual(len(projects), 2)
        self.assertTrue(all(project["capabilities"]["startSession"] for project in projects))
        exact_paths = {root.path for root in self.backend.workspace.roots.values() if root.project_only}
        self.assertEqual(exact_paths, {first, second})
        self.assertNotIn(self.root, exact_paths); self.assertNotIn(second.parent, exact_paths)

    def test_duplicate_native_uuid_has_one_unverified_project_and_session_without_an_exact_grant(self):
        outside = self.root / "elsewhere"; self.project.rename(outside)
        self.raw["projects"][0]["last_path"] = str(outside)
        self.raw["projects"].append(dict(self.raw["projects"][0]))
        self.raw["sessions"] = [self.running(), self.running()]
        value = self.backend.snapshot()
        self.assertEqual(value["connectionStatus"], "available")
        self.assertEqual(len(value["projects"]), 1); self.assertEqual(len(value["sessions"]), 1)
        project = value["projects"][0]; session = value["sessions"][0]
        self.assertEqual(project["controlRestriction"], "project-registration-ambiguous")
        self.assertEqual(session["controlRestriction"], "project-registration-ambiguous")
        self.assertEqual(session["state"], "unknown")
        self.assertTrue(all(not enabled for enabled in session["capabilities"].values()))
        for capability in ("filesRead", "configRead", "configWrite", "startSession"):
            self.assertFalse(project["capabilities"][capability])
        self.assertEqual(self.backend.workspace._registry()["projects"], [])
        self.assertFalse(any(root.project_only for root in self.backend.workspace.roots.values()))
        self.assertNotIn(SID, read_private_json(self.backend._bindings_path)["sessions"])

    def test_duplicate_uuid_revokes_existing_read_and_config_save_before_periodic_refresh(self):
        outside = self.root / "elsewhere"; self.project.rename(outside)
        self.raw["projects"][0]["last_path"] = str(outside)
        project = self.backend.snapshot()["projects"][0]
        before = self.backend.read_config(project["id"])
        self.raw["projects"].append({**self.raw["projects"][0], "last_path": str(self.root / "copy")})
        self.assertFalse(self.backend._can_write(project["id"]))
        with self.assertRaisesRegex(BackendUnavailable, "registration-unavailable"):
            self.backend.read_config(project["id"])
        with self.assertRaises(ValueError):
            self.backend.save_config(project["id"], "agent: codex\n", before["revision"])
        self.assertEqual((outside / ".botainer/config.yaml").read_text(), before["text"])
        value = self.backend.snapshot()
        self.assertEqual(len(value["projects"]), 1)
        self.assertFalse(value["projects"][0]["capabilities"]["configWrite"])

    def test_unregistration_revokes_exact_project_reads_without_waiting_for_inventory_cache(self):
        outside = self.root / "elsewhere"; self.project.rename(outside)
        self.raw["projects"][0]["last_path"] = str(outside)
        project = self.backend.snapshot()["projects"][0]
        self.raw["projects"] = []
        with self.assertRaisesRegex(BackendUnavailable, "registration-unavailable"):
            self.backend.read_config(project["id"])
        self.assertEqual(self.backend.snapshot()["projects"], [])

    def test_duplicate_uuid_inside_setup_roots_cannot_receive_control_or_config_write(self):
        project = self.backend.snapshot()["projects"][0]
        before = self.backend.read_config(project["id"])
        self.raw["projects"].append(dict(self.raw["projects"][0]))
        self.assertFalse(self.backend._can_write(project["id"]))
        self.backend._last = 0; value = self.backend.snapshot()
        self.assertEqual(len(value["projects"]), 1)
        self.assertEqual(value["projects"][0]["controlRestriction"], "project-registration-ambiguous")
        self.assertFalse(self.backend.read_config(project["id"])["writable"])
        with self.assertRaises(ValueError):
            self.backend.save_config(project["id"], "agent: codex\n", before["revision"])
        self.assertEqual((self.project / ".botainer/config.yaml").read_text(), before["text"])

    def test_uuid_alias_is_ambiguous_with_its_canonical_registration(self):
        project = self.backend.snapshot()["projects"][0]
        self.raw["projects"].append({**self.raw["projects"][0], "uuid": UID.replace("-", "")})
        self.assertFalse(self.backend._can_write(project["id"]))
        self.backend._last = 0; value = self.backend.snapshot()
        self.assertEqual(len(value["projects"]), 1)
        self.assertEqual(value["projects"][0]["id"], project["id"])
        self.assertEqual(value["projects"][0]["controlRestriction"], "project-registration-ambiguous")

    def test_discovered_project_retains_same_startup_and_orphan_recovery_fences(self):
        outside = self.root / "elsewhere"; self.project.rename(outside); self.project = outside
        self.raw["projects"][0]["last_path"] = str(outside)
        self.owner_receipt(self.unconfirmed_startup())
        self.raw["sessions"] = [self.running()]; self.backend._last = 0
        runtime = next(session for session in self.backend.snapshot()["sessions"] if session.get("kind") != "launch")
        self.assertTrue(runtime["capabilities"]["attachTerminal"])
        self.assertEqual(self.backend._operations()["operations"][0]["state"], "completed")
        self.backend.owner.launch.assert_called_once()
        self.backend.owner.observe.return_value = {"state": "ended"}; self.backend._last = 0
        runtime = next(session for session in self.backend.snapshot()["sessions"] if session.get("kind") != "launch")
        self.assertFalse(runtime["capabilities"]["attachTerminal"])
        self.assertTrue(runtime["capabilities"]["stopSession"])
        self.assertEqual(runtime["stopMode"], "orphan-container")
        self.backend.owner.launch.assert_called_once()
        (outside / ".botainer/project-id").write_text(REQUEST2); self.backend._last = 0
        runtime = next(session for session in self.backend.snapshot()["sessions"] if session.get("kind") != "launch")
        self.assertFalse(runtime["capabilities"]["stopSession"])

    def test_native_project_moved_replaced_copied_or_unregistered_does_not_expand_exact_grant(self):
        outside = self.root / "elsewhere"; self.project.rename(outside)
        self.raw["projects"][0]["last_path"] = str(outside)
        project = self.backend.snapshot()["projects"][0]
        moved = self.root / "moved"; outside.rename(moved)
        self.raw["projects"][0]["last_path"] = str(moved); self.backend._last = 0
        self.assertFalse(self.backend.snapshot()["projects"][0]["capabilities"]["startSession"])
        restarted = OrdinaryLocalBackend(self.application, self.profile_path, runner=self.runner)
        self.assertFalse(restarted.snapshot()["projects"][0]["capabilities"]["startSession"])
        with self.assertRaises((OSError, ValueError, BackendUnavailable)):
            self.backend.read_config(project["id"])
        moved.rename(outside); self.raw["projects"][0]["last_path"] = str(outside)
        (outside / ".botainer/project-id").write_text(REQUEST)
        self.backend._last = 0
        self.assertFalse(self.backend.snapshot()["projects"][0]["capabilities"]["startSession"])
        (outside / ".botainer/project-id").write_text(UID)
        self.raw["projects"] = []; self.backend._last = 0; self.backend.snapshot()
        with self.assertRaises(BackendUnavailable): self.backend.read_config(project["id"])

    def test_native_project_symlink_and_uuid_copy_are_not_registered(self):
        link = self.root / "linked"; link.symlink_to(self.project, target_is_directory=True)
        self.raw["projects"][0]["last_path"] = str(link)
        self.assertFalse(self.backend.snapshot()["projects"][0]["capabilities"]["startSession"])
        outside = self.root / "outside"; (outside / ".botainer").mkdir(parents=True)
        (outside / ".botainer/project-id").write_text(REQUEST)
        self.raw["projects"][0]["last_path"] = str(outside); self.backend._last = 0
        self.assertFalse(self.backend.snapshot()["projects"][0]["capabilities"]["startSession"])

    def test_pending_request_replay_and_agent_conflict_never_spawn_twice(self):
        project = self.backend.snapshot()["projects"][0]
        value = self.backend.start_session(project["id"], REQUEST, "codex")
        self.assertEqual(value["session"]["runtimeId"], REQUEST)
        self.assertEqual(value["session"]["kind"], "launch")
        intent = read_private_json(Path(self.consoles[0].argv[-1]))
        self.assertEqual(intent["agent"], "codex")
        self.assertEqual(intent["config_revision"], sha(self.project / ".botainer/config.yaml"))
        self.backend.start_session(project["id"], REQUEST, "codex")
        with self.assertRaisesRegex(BackendUnavailable, "request-id-conflict"):
            self.backend.start_session(project["id"], REQUEST, "claude")
        self.assertEqual(len(self.consoles), 1)
        value = self.backend.snapshot()
        self.assertFalse(value["projects"][0]["capabilities"]["configWrite"])

    def test_running_container_without_tty_has_explicit_attach_reason(self):
        self.raw["sessions"] = [self.running() | {"interactive": False}]
        session = self.backend.snapshot()["sessions"][0]
        self.assertEqual(session["state"], "running")
        self.assertFalse(session["capabilities"]["attachTerminal"])
        self.assertFalse(session["capabilities"]["stopSession"])
        self.assertIn("without a writable terminal", session["unavailableReason"])

    def test_decline_reopens_project_and_does_not_fabricate_runtime(self):
        project = self.backend.snapshot()["projects"][0]
        self.backend.start_session(project["id"], REQUEST)
        self.finish({"phase": "not-dispatched", "exit_code": 0})
        value = self.backend.snapshot()
        self.assertTrue(value["projects"][0]["capabilities"]["startSession"])
        self.assertEqual(value["sessions"][0]["launchState"], "declined")
        self.assertNotIn("resultTarget", value["sessions"][0])

    def test_failed_receipt_explains_review_limit_without_parsing_transcript(self):
        project = self.backend.snapshot()["projects"][0]
        self.backend.start_session(project["id"], REQUEST)
        self.finish({"phase": "not-dispatched", "exit_code": 1,
            "failure_code": "ordinary-hook-review-required-no-installation-permitted"})
        session = self.backend.snapshot()["sessions"][0]
        self.assertEqual(session["launchState"], "failed")
        self.assertIn("not a request to install", session["launchReason"])
        self.assertNotIn("resultTarget", session)

    def test_receipt_diagnostics_cannot_replace_uncertain_lifecycle_or_inject_text(self):
        project = self.backend.snapshot()["projects"][0]
        self.backend.start_session(project["id"], REQUEST)
        self.finish({"phase": "before-dispatch", "exit_code": 1,
            "failure_code": "ordinary-broker-owner-required"})
        session = self.backend.snapshot()["sessions"][0]
        self.assertEqual(session["launchState"], "unknown")
        self.assertNotIn("No container", session["launchReason"])

    def test_dispatch_ambiguity_stays_fenced_after_restart(self):
        project = self.backend.snapshot()["projects"][0]
        self.backend.start_session(project["id"], REQUEST)
        self.finish({"phase": "before-dispatch", "session_id": SID})
        self.backend.snapshot()
        restarted = OrdinaryLocalBackend(self.application, self.profile_path, runner=self.runner)
        value = restarted.snapshot()
        self.assertEqual(value["sessions"][0]["launchState"], "unknown")
        self.assertFalse(value["projects"][0]["capabilities"]["startSession"])
        with self.assertRaisesRegex(BackendUnavailable, "active-or-unverified"):
            restarted.start_session(project["id"], REQUEST2)

    def test_completed_console_replay_after_restart_has_one_writer_and_no_input(self):
        project = self.backend.snapshot()["projects"][0]
        self.backend.start_session(project["id"], REQUEST)
        self.finish({"phase": "not-dispatched", "exit_code": 0})
        self.backend.snapshot()
        transcript = self.backend.root / (REQUEST + ".bin")
        transcript.write_bytes(b"Native warnings\r\nAborted by user.\r\n"); transcript.chmod(0o600)
        restarted = OrdinaryLocalBackend(self.application, self.profile_path, runner=self.runner)
        other = OrdinaryLocalBackend(self.application, self.profile_path, runner=self.runner)
        viewer = restarted.attach(restarted.launch_namespace, REQUEST, 80, 24)
        try:
            self.assertEqual(viewer.read(65536), transcript.read_bytes())
            self.assertEqual(viewer.read(65536), b"")
            with self.assertRaises(BrokenPipeError): viewer.write(b"y\n")
            with self.assertRaisesRegex(BackendUnavailable, "writer-busy"):
                other.attach(other.launch_namespace, REQUEST, 80, 24)
        finally: viewer.close()
        second = other.attach(other.launch_namespace, REQUEST, 80, 24); second.close()

    def test_verified_receipt_pins_owner_before_auto_switch_then_refuses_replacement(self):
        project = self.backend.snapshot()["projects"][0]
        self.backend.start_session(project["id"], REQUEST, "claude")
        self.finish({"phase": "completed", "exit_code": 0, "session_id": SID, "binding": self.binding()})
        self.raw["sessions"] = [self.running()]
        value = self.backend.snapshot()
        launch = next(s for s in value["sessions"] if s.get("kind") == "launch")
        self.assertEqual(launch["resultTarget"], {"contextNamespace": self.backend.namespace, "runtimeId": SID})
        runtime = next(s for s in value["sessions"] if s.get("kind") != "launch")
        self.assertEqual(runtime["requestedAgent"], "claude")
        self.assertEqual(runtime["agent"], "Agent not recorded")
        self.raw["sessions"][0]["binding"]["id"] = "c" * 64
        self.backend._last = 0
        runtime = self.backend.snapshot()["sessions"][0]
        self.assertEqual(runtime["state"], "unknown")
        self.assertFalse(runtime["capabilities"]["attachTerminal"])

    def test_observation_loss_preserves_unknown_owner(self):
        self.raw["sessions"] = [self.running()]
        self.backend.snapshot(); self.backend._last = 0
        self.runner.side_effect = InventoryError("unit-offline")
        value = self.backend.snapshot()
        self.assertEqual(value["connectionStatus"], "unavailable")
        self.assertEqual(value["unavailableReason"], "unit-offline")
        self.assertEqual(value["sessions"][0]["state"], "unknown")
        self.assertFalse(value["sessions"][0]["capabilities"]["attachTerminal"])

    def test_open_initialized_folder_is_read_only_registration(self):
        before = (self.project / ".botainer/config.yaml").read_bytes()
        value = self.backend.create_project({"mode": "open", "rootId": "work", "installationId": "local",
            "path": "existing", "requestId": REQUEST})
        self.assertNotIn("session", value); self.assertEqual(self.consoles, [])
        self.assertEqual(before, (self.project / ".botainer/config.yaml").read_bytes())

    def test_create_and_native_init_keep_provisional_project_and_return_canonical_identity(self):
        value = self.backend.create_project({"mode": "create", "rootId": "work", "installationId": "local",
            "path": "new", "name": "New project", "requestId": REQUEST})
        self.assertEqual(value["session"]["operation"], "init")
        pending = self.backend.snapshot()
        self.assertIn(value["project"]["id"], {p["id"] for p in pending["projects"]})
        created = self.root / "projects/new"
        (created / ".botainer").mkdir(); (created / ".botainer/project-id").write_text(REQUEST2)
        (created / ".botainer/config.yaml").write_text("agent: codex\n")
        self.finish({"phase": "completed", "exit_code": 0, "project_uuid": REQUEST2})
        self.raw["projects"].append({"uuid": REQUEST2, "display_name": "new", "last_path": str(created)})
        result = self.backend.snapshot()
        launch = result["sessions"][0]
        self.assertEqual(launch["launchState"], "completed")
        self.assertEqual(launch["resultProjectId"], self.backend._project_id(REQUEST2))
        self.assertEqual(launch["projectId"], launch["resultProjectId"])
        self.assertNotIn("resultTarget", launch)
        self.assertIn(launch["projectId"], {p["id"] for p in result["projects"]})
        self.assertNotIn(value["project"]["id"], {p["id"] for p in result["projects"]})
        self.assertEqual(len(result["projects"]), 2)
        # Historical operation intent stays intact; restart must still avoid a
        # duplicate setup row and replay must not return an orphaned session.
        self.assertEqual(self.backend._operations()["operations"][0]["project_id"], value["project"]["id"])
        restarted = OrdinaryLocalBackend(self.application, self.profile_path, runner=self.runner)
        self.assertEqual(len(restarted.snapshot()["projects"]), 2)
        replay = restarted.create_project({"mode": "create", "rootId": "work", "installationId": "local",
            "path": "new", "name": "New project", "requestId": REQUEST})
        self.assertEqual(replay["project"]["id"], launch["resultProjectId"])
        self.assertEqual(replay["session"]["projectId"], replay["project"]["id"])

    def test_config_save_guard_rechecks_runtime_not_stale_idle_snapshot(self):
        project = self.backend.snapshot()["projects"][0]
        self.raw["sessions"] = [self.running()]
        self.assertFalse(self.backend._can_write(project["id"]))
        self.raw["sessions"] = []
        self.raw["projects"][0]["runtime_unverified"] = True
        self.assertFalse(self.backend._can_write(project["id"]))

    def test_completed_init_removes_provisional_even_when_next_inventory_fails(self):
        value = self.backend.create_project({"mode": "create", "rootId": "work", "installationId": "local",
            "path": "new", "requestId": REQUEST})
        self.backend.snapshot()  # cache contains the provisional setup row
        created = self.root / "projects/new"
        (created / ".botainer").mkdir(); (created / ".botainer/project-id").write_text(REQUEST2)
        (created / ".botainer/config.yaml").write_text("agent: claude\n")
        self.finish({"phase": "completed", "exit_code": 0, "project_uuid": REQUEST2})
        self.runner.side_effect = InventoryError("unit-offline")
        result = self.backend.snapshot()
        self.assertEqual(result["connectionStatus"], "unavailable")
        self.assertEqual(len(result["projects"]), 2)
        self.assertNotIn(value["project"]["id"], {p["id"] for p in result["projects"]})
        self.assertEqual(result["sessions"][0]["projectId"], self.backend._project_id(REQUEST2))
class HelperTests(unittest.TestCase):
    def container(self):
        return {"id": CID, "name": "/botainer-" + SID[:12], "image": "image", "created": "created",
            "started_at": "started", "state": "running", "tty": True, "stdin": True,
            "mounts": [{"Type": "bind", "Source": "/unit/project"}]}

    def record(self, **changes):
        values = {"session_id": SID, "project_uuid": UID, "project_root": "/unit/project", "runtime": "docker",
            "docker": SimpleNamespace(container_id="botainer-" + SID[:12]), "apptainer": None,
            "started_at": "started", "ended_at": None}
        return SimpleNamespace(**(values | changes))

    def run_inventory(self, records, containers, *, error=None):
        state = SimpleNamespace(list_projects=lambda: [SimpleNamespace(uuid=UID, display_name="Project", last_path="/unit/project", last_session_at="started")])
        sessions = SimpleNamespace(candidate_session_dirs=lambda root: list(range(len(records))),
            read=Mock(side_effect=error or records))
        profile = SimpleNamespace(data={"state_root": "/unit/state"})
        with patch.dict("sys.modules", {"botainer.state": SimpleNamespace(dir=state, session_record=sessions)}), \
                patch.object(helper, "docker_inventory", return_value={c["name"]: c for c in containers}), \
                patch.object(Path, "exists", return_value=True):
            return helper.inventory(profile)

    def test_docker_bridge_only_changes_transport_flags(self):
        original = ["docker", "run", "--rm", "-d", "--name", "botainer-unit", "--cap-drop", "ALL", "image", "agent"]
        for plugins, expected in (([], "-dit"), (["nudge"], "-it")):
            value = helper.corrected_docker_argv(original, SimpleNamespace(plugins_enabled=plugins), True)
            self.assertEqual(value[:6], ["docker", "run", "--rm", expected, "--pull", "never"])
            self.assertEqual(value[6:], original[4:])
        self.assertEqual(original[3], "-d")
        with self.assertRaisesRegex(ValueError, "requalification"):
            helper.corrected_docker_argv(["different"], SimpleNamespace(plugins_enabled=[]), True)

    def test_runtime_requires_project_mount_name_and_full_id(self):
        record, container = self.record(), self.container()
        self.assertEqual(helper.container_binding(record, container)["id"], CID)
        container["mounts"] = [{"Type": "bind", "Source": "/different"}]
        with self.assertRaises(ValueError): helper.container_binding(record, container)

    def test_ended_metadata_cannot_hide_live_owner(self):
        value = self.run_inventory([self.record(ended_at="end")], [self.container()])
        self.assertEqual(value["sessions"][0]["state"], "unknown")

    def test_compose_only_record_is_not_runtime_and_unrecorded_owner_fences(self):
        record = self.record(started_at=None, docker=SimpleNamespace(container_id=None))
        value = self.run_inventory([record], [])
        self.assertEqual(value["sessions"], [])
        value = self.run_inventory([record], [self.container()])
        self.assertTrue(value["projects"][0]["runtime_unverified"])

    def test_malformed_record_fences_project_even_when_docker_is_empty(self):
        value = self.run_inventory([None], [], error=ValueError("bad record"))
        self.assertTrue(value["projects"][0]["runtime_unverified"])


class NativeCliTests(OrdinaryFixture):
    def setUp(self):
        super().setUp()
        self.profile = self.backend.profile
        self.intent = self.backend.root / (REQUEST + ".intent.json")
        self.result = self.backend.root / (REQUEST + ".result.json")
        self.data = {"action": "start", "request_id": REQUEST, "project_uuid": UID,
            "project_path": str(self.project), "result_path": str(self.result), "agent": "codex"}
        write_private_json(self.intent, self.data)
        self.spec = SimpleNamespace(runtime="docker", project_uuid=UID, project_root=str(self.project),
            state_dir=str(self.root / "state" / "state" / UID), session_id=SID, plugins_enabled=[], hooks=[],
            mount_plan=SimpleNamespace(binds=[]))
        self.original = Mock(return_value=SimpleNamespace(id=CID))
        self.composition = SimpleNamespace(launch=self.original, compose_session=Mock(return_value=self.spec))
        self.adapter = type("Adapter", (), {"render_argv": lambda *a, **k: ["docker", "run", "--rm", "-d", "image"]})
        self.hooks = SimpleNamespace(run_hook=Mock())
        self.container = {**self.binding(), "state": "running", "tty": True, "stdin": True,
            "mounts": [{"Type": "bind", "Source": str(self.project)}]}
        self.record = SimpleNamespace(session_id=SID, docker=SimpleNamespace(container_id=CID), project_root=str(self.project))

    def execute(self, cli):
        with patch.dict("sys.modules", {"botainer.core": SimpleNamespace(composition=self.composition),
                "botainer.adapters.docker": SimpleNamespace(DockerAdapter=self.adapter),
                "botainer.plugins": SimpleNamespace(hooks=self.hooks)}), \
                patch.object(Path, "cwd", return_value=self.project), \
                patch.object(helper, "verify_daemon"), patch.object(helper, "inventory", return_value=self.raw), \
                patch.object(helper, "docker_inventory", return_value={self.container["name"]: self.container}), \
                patch.object(helper, "exact_record", return_value=self.record):
            return helper.native_operation(self.profile, self.data, self.intent, cli)

    def test_decline_and_interrupt_preserve_native_args_and_do_not_dispatch(self):
        def decline(argv):
            self.assertEqual(argv, ["start", "--runtime=docker", "--detach", "--no-auto-onboard", "--agent=codex"])
            return 0
        self.assertEqual(self.execute(decline), 0)
        self.original.assert_not_called()
        self.assertEqual(read_private_json(self.result)["phase"], "not-dispatched")
        def interrupt(argv): raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt): self.execute(interrupt)
        self.assertEqual(read_private_json(self.result)["exit_code"], 130)
        self.original.assert_not_called()

    def test_only_native_cli_callback_dispatches_and_receipt_precedes_it(self):
        def dispatch(spec, **kwargs):
            self.assertEqual(read_private_json(self.result)["phase"], "before-dispatch")
            return SimpleNamespace(id=CID)
        self.original.side_effect = dispatch
        def accepted(argv):
            self.composition.launch(self.spec, detach=True)
            return 0
        self.assertEqual(self.execute(accepted), 0)
        self.original.assert_called_once_with(self.spec, detach=True)
        receipt = read_private_json(self.result)
        self.assertEqual(receipt["phase"], "completed")
        self.assertEqual(receipt["binding"], self.binding())
        self.assertIs(self.composition.launch, self.original)

    def test_changed_composed_identity_never_dispatches(self):
        self.spec.project_uuid = REQUEST2
        def accepted(argv): return self.composition.launch(self.spec, detach=True)
        with self.assertRaisesRegex(ValueError, "different target"): self.execute(accepted)
        self.original.assert_not_called()
        self.assertEqual(read_private_json(self.result)["phase"], "not-dispatched")

    def test_missing_cleanup_hook_is_refused_before_any_startup_hook(self):
        hook = self.root / "approved-hook.py"; hook.write_text("# inert\n")
        self.profile.data["approved_hooks"] = {str(hook): sha(hook)}
        self.spec.hooks = [SimpleNamespace(script_path=str(hook), plugin="example", when="pre_session"),
            SimpleNamespace(script_path=str(self.root / "missing-cleanup.py"), plugin="example", when="post_session")]
        original_compose = self.composition.compose_session
        def cli(argv):
            self.composition.compose_session()
            self.hooks.run_hook(script_path=hook)
            self.composition.launch(self.spec, detach=True)
        with self.assertRaisesRegex(BackendUnavailable, "hook-review-required"):
            self.execute(cli)
        self.hooks.run_hook.assert_not_called()
        self.original.assert_not_called()
        self.assertIs(self.composition.compose_session, original_compose)
        receipt = read_private_json(self.result)
        self.assertEqual(receipt["phase"], "not-dispatched")
        self.assertEqual(receipt["failure_code"], "ordinary-hook-review-required-no-installation-permitted")

    def test_broker_lifetime_is_not_fixed_by_approving_hooks(self):
        self.spec.plugins_enabled = ["agent-claude-broker"]
        with self.assertRaisesRegex(BackendUnavailable, "broker-owner-required"):
            self.execute(lambda argv: self.composition.compose_session())
        self.original.assert_not_called()
        self.hooks.run_hook.assert_not_called()
        receipt = read_private_json(self.result)
        self.assertEqual(receipt["phase"], "not-dispatched")
        self.assertEqual(receipt["failure_code"], "ordinary-broker-owner-required")

    def test_foreground_owner_keeps_native_cli_and_publishes_ready_before_cli_exit(self):
        self.data["terminal_owner_id"] = REQUEST.replace("-", "")
        self.profile.data["terminal_owner"] = {"path": str(self.root / "bin/python"),
            "sha256": sha(self.root / "bin/python"), "control_root": str(self.root / "terminal-control")}
        write_private_json(self.intent, self.data)
        def cli(argv):
            self.assertEqual(argv, ["start", "--runtime=docker", "--no-auto-onboard", "--agent=codex"])
            self.composition.compose_session()
            self.composition.launch(self.spec)
            receipt = read_private_json(self.result)
            self.assertEqual(receipt["phase"], "runtime-returned")
            self.assertEqual(receipt["binding"]["id"], CID)
            # Native foreground CLI may run arbitrarily long at this point.
            raise SystemExit(7)
        with patch.dict(os.environ, {"TMUX": str(self.root / "terminal-control" / self.data["terminal_owner_id"] / "s") + ",12,0", "TMUX_PANE": "%0"}):
            with self.assertRaises(SystemExit): self.execute(cli)
        self.original.assert_called_once_with(self.spec, detach=False)
        receipt = read_private_json(self.result)
        self.assertEqual(receipt["phase"], "runtime-ended")
        self.assertEqual(receipt["exit_code"], 7)

    def test_persistent_launch_never_nests_unqualified_screen_owner(self):
        self.spec.plugins_enabled = ["nudge"]
        with self.assertRaisesRegex(BackendUnavailable, "nested-screen-owner-unqualified"):
            helper.verify_start_hooks(self.profile, self.spec, durable=True)
        self.hooks.run_hook.assert_not_called()

    def test_native_stop_leaves_cleanup_to_same_foreground_cli_only(self):
        self.profile.data["terminal_owner"] = {"path": str(self.root / "bin/python"),
            "sha256": sha(self.root / "bin/python"), "control_root": str(self.root / "terminal-control")}
        request = self.backend.root / "stop-query.json"
        data = {"action": "stop", "profile_sha256": self.profile.digest, "project_path": str(self.project),
            "project_uuid": UID, "session_id": SID, "binding": self.binding(),
            "terminal_owner_id": REQUEST.replace("-", ""), "launch_request_id": REQUEST,
            "launch_intent_sha256": sha(self.intent)}
        write_private_json(request, data)
        write_private_json(self.result, {"request_id": REQUEST, "intent_sha256": sha(self.intent),
            "phase": "runtime-returned", "session_id": SID, "binding": self.binding()})
        post = Mock()
        composition = SimpleNamespace(run_post_session_hooks=post)
        original_stop = Mock()
        stop = SimpleNamespace(_do_runtime_stop=original_stop)
        def cli(argv):
            self.assertEqual(argv, ["stop", "--", SID])
            stop._do_runtime_stop(self.record, force=False)
            composition.run_post_session_hooks(self.spec)
            return 0
        modules = {"botainer": SimpleNamespace(__file__=str(self.root / "source/botainer/__init__.py")),
            "botainer.cli": SimpleNamespace(stop=stop), "botainer.cli.main": SimpleNamespace(main=cli),
            "botainer.core": SimpleNamespace(composition=composition), "botainer.plugins": SimpleNamespace(hooks=self.hooks)}
        owner = Mock(); owner.observe.return_value = {"state": "running"}
        with patch.dict(sys.modules, modules), patch.object(sys, "path", list(sys.path)), \
                patch.object(sys, "argv", [str(ROOT / "tools/ordinary_local_helper.py"), str(self.profile_path), str(request)]), \
                patch.object(helper, "OrdinaryLocalProfile", return_value=self.profile), \
                patch.object(helper, "verify_target", return_value=self.record), patch.object(Path, "cwd", return_value=self.project), \
                patch.object(helper, "exact_container_stopped", return_value=True), \
                patch("botainer_dashboard.ordinary_owner.OrdinaryCliOwner", return_value=owner), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(helper.main(), 0)
        self.assertTrue(json.loads(output.getvalue())["stopped"])
        original_stop.assert_called_once()
        post.assert_not_called()
        self.assertIs(composition.run_post_session_hooks, post)

    def test_post_dispatch_result_loss_is_ambiguous_and_never_retried(self):
        self.original.side_effect = OSError("lost acknowledgement")
        def accepted(argv): return self.composition.launch(self.spec, detach=True)
        with self.assertRaises(OSError): self.execute(accepted)
        self.assertEqual(read_private_json(self.result)["phase"], "before-dispatch")
        self.original.assert_called_once()


if __name__ == "__main__": unittest.main()
