"""Offline selection persistence/security tests; no runtime or SSH is started."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from botainer_dashboard.connections import ConnectionRegistry, ConnectionRegistryError
from botainer_dashboard.backend_errors import BackendUnavailable
from botainer_dashboard.launch_console import private_lock
from botainer_dashboard.ordinary_local import load_local_profile
from botainer_dashboard.pairing import write_private_json


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def remote(identifier="cluster-main", *, alias="cluster", state="/home/test/.botainer", control="/home/test/.dashboard"):
    source_files = (
        "pyproject.toml", "botainer/plugins/lifecycle.py", "botainer/cli/main.py", "botainer/cli/hpc.py",
        "botainer/cli/plugin.py", "botainer/plugins/manifest.py", "plugins/hpc-launcher/host_helper/submit.py",
        "plugins/hpc-launcher/host_helper/_common.py",
    )
    return {"version": 1, "id": identifier, "label": "Research cluster", "ssh_alias": alias,
            "remote_python": "/opt/botainer/bin/python", "remote_python_sha256": "a" * 64,
            "source_root": "/opt/botainer/source", "state_root": state,
            "launcher": {"path": "/home/test/bin/botainer", "sha256": "b" * 64},
            "source_sha256": {name: "c" * 64 for name in source_files}, "state_plugin_sha256": {},
            "control_root": control, "project_roots": {"work": "/home/test/projects"}}


class ConnectionRegistryTests(unittest.TestCase):
    def setUp(self):
        base = "/private/tmp" if Path("/private/tmp").is_dir() else "/tmp"
        self.temp = tempfile.TemporaryDirectory(prefix="bd-con-", dir=base)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / "connections.json"
        self.registry = ConnectionRegistry(self.path)

    def assertCode(self, code, function, *args, **kwargs):
        with self.assertRaises(ConnectionRegistryError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, "connections-" + code)
        return caught.exception

    def initialize(self):
        return self.registry.initialize()

    def write_profile(self, name, profile, *, pretty=False):
        path = self.root / name
        if pretty:
            path.write_text(json.dumps(profile, indent=3) + "\n\n")
            path.chmod(0o600)
        else:
            write_private_json(path, profile)
        return path

    def local(self):
        for name in ("source/botainer", "state", "home", "projects", "bin"):
            (self.root / name).mkdir(parents=True, mode=0o700, exist_ok=True)
        source = self.root / "source/botainer/__init__.py"
        source.write_text("# inert fixture\n")
        for name in ("python", "docker"):
            path = self.root / "bin" / name
            path.write_text("# inert executable, never run\n")
            path.chmod(0o700)
        return {"version": 1, "id": "local", "label": "This computer",
                "python": str(self.root / "bin/python"), "source_root": str(self.root / "source"),
                "state_root": str(self.root / "state"), "home": str(self.root / "home"),
                "project_roots": [{"id": "work", "label": "Projects", "path": str(self.root / "projects")}],
                "docker": {"executable": str(self.root / "bin/docker"), "host": "unix:///test.sock", "daemon_id": "fixture"},
                "environment": {"PATH": str(self.root / "bin")},
                "source_hashes": {"botainer/__init__.py": digest(source)},
                "support_hashes": {str(self.root / "bin" / name): digest(self.root / "bin" / name)
                                   for name in ("python", "docker")}}

    def host(self, identifier="host-main", *, control="host-control"):
        folder = self.root / control
        folder.mkdir(mode=0o700, exist_ok=True)
        projects = self.root / "host-projects"
        projects.mkdir(mode=0o700, exist_ok=True)
        binary = self.root / "host-agent"
        binary.write_text("# inert executable, never run\n")
        binary.chmod(0o700)
        tool = {"path": str(binary), "sha256": digest(binary)}
        return {"version": 1, "id": identifier, "label": "Native agent", "home": str(self.root),
                "tmux": dict(tool), "project_roots": {"work": str(projects)},
                "control_root": str(folder), "agents": {"codex": dict(tool)},
                "default_agent": "codex", "environment": {"PATH": "/usr/bin:/bin"}}

    def test_constructor_and_missing_reads_do_not_create_anything(self):
        missing = self.root / "not-created" / "connections.json"
        registry = ConnectionRegistry(missing)
        self.assertCode("not-initialized", registry.read)
        self.assertCode("not-initialized", registry.selected_profiles)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_explicit_installed_local_profile_is_saved_and_revalidated(self):
        self.initialize(); profile = self.local()
        metadata = self.root / 'source/botainer-0.1.0a5.dist-info'; metadata.mkdir()
        (metadata / 'METADATA').write_text('Name: botainer\nVersion: 0.1.0a5\n')
        (metadata / 'entry_points.txt').write_text('[console_scripts]\nbotainer = botainer.cli.main:main\n')
        profile.update(version=2, installation_layout={
            'kind': 'wheel', 'metadata_path': 'botainer-0.1.0a5.dist-info/METADATA'})
        profile['source_hashes'].update({p.relative_to(self.root / 'source').as_posix(): digest(p)
                                        for p in metadata.iterdir()})
        result = self.registry.add('local', profile, 0, trusted=True)
        self.assertEqual(result['revision'], 1)
        chosen = self.registry.selected_profiles()['local_profile']
        self.assertEqual(load_local_profile(chosen).data['installation_layout'], profile['installation_layout'])
        (metadata / 'METADATA').write_text('Name: changed\nVersion: 0.1.0a5\n')
        with self.assertRaises(BackendUnavailable): load_local_profile(chosen)

    def test_explicit_installed_remote_profile_does_not_require_source_checkout(self):
        self.initialize(); profile = remote()
        profile.update(version=2, installation_layout={
            'kind': 'wheel', 'metadata_path': 'botainer-0.1.0a5.dist-info/METADATA'})
        profile['source_sha256'] = {name: value for name, value in profile['source_sha256'].items()
                                   if name.startswith('botainer/')}
        profile['source_sha256'].update({name: 'a' * 64 for name in ('botainer/__init__.py',
            'botainer-0.1.0a5.dist-info/METADATA', 'botainer-0.1.0a5.dist-info/entry_points.txt')})
        profile['state_plugin_sha256'] = {'plugins/hpc-launcher/' + name: 'b' * 64 for name in
            ('botainer-plugin.yaml', 'host_helper/submit.py', 'host_helper/_common.py')}
        self.assertEqual(self.registry.add('remote', profile, 0, trusted=True)['revision'], 1)

    def test_empty_initialization_and_read_are_safe_for_first_start(self):
        self.assertEqual(self.initialize(), {"version": 1, "revision": 0, "entries": []})
        self.assertEqual(self.registry.selected_profiles(), {"local_profile": None, "remote_profiles": (), "host_profiles": ()})
        before = {str(p): p.stat().st_mtime_ns for p in self.root.rglob("*")}
        with patch("botainer_dashboard.connections.private_lock", side_effect=AssertionError("read must not lock/write")):
            self.assertEqual(self.registry.read()["revision"], 0)
            self.registry.selected_profiles()
        self.assertEqual(before, {str(p): p.stat().st_mtime_ns for p in self.root.rglob("*")})
        for path in (self.path, self.registry.lock_path):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.registry.profile_directory.stat().st_mode & 0o777, 0o700)

    def test_real_loaders_import_three_kinds_without_executing_anything(self):
        source = [self.write_profile(kind + ".json", profile, pretty=True)
                  for kind, profile in (("local", self.local()), ("remote", remote()), ("host", self.host()))]
        original = [path.read_bytes() for path in source]
        with patch("subprocess.Popen", side_effect=AssertionError("must not execute")), \
                patch("botainer_dashboard.inventory.bounded_run", side_effect=AssertionError("must not execute")):
            value = self.registry.initialize([{"kind": kind, "path": path}
                                             for kind, path in zip(("local", "remote", "host"), source)])
        selected = self.registry.selected_profiles()
        self.assertEqual(len(value["entries"]), 3)
        self.assertEqual([path.read_bytes() for path in source], original)
        for entry, raw in zip(value["entries"], original):
            copied = Path(entry["profilePath"])
            self.assertEqual(copied.read_bytes(), raw)
            self.assertEqual(entry["profileDigest"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(copied.stat().st_mode & 0o777, 0o600)
        # Local and host identity fingerprints depend on exact file bytes.
        self.assertEqual(load_local_profile(selected["local_profile"]).fingerprint, digest(source[0]))

    def test_existing_registry_cannot_be_silently_reseeded(self):
        original = self.initialize()
        profile = self.write_profile("remote.json", remote())
        self.assertCode("already-initialized", self.registry.initialize, [{"kind": "remote", "path": profile}])
        self.assertEqual(self.registry.initialize(), original)

    def test_add_requires_literal_trust_confirmation(self):
        self.initialize()
        for trusted in (False, 1, "true", None):
            with self.subTest(trusted=trusted):
                self.assertCode("trust-required", self.registry.add, "remote", remote(), 0, trusted=trusted)
        self.assertEqual(self.registry.read()["entries"], [])

    def test_add_uses_actual_schema_and_keeps_no_failed_profile(self):
        self.initialize()
        profile = remote()
        profile["ssh_alias"] = "-oProxyCommand=anything"
        self.assertCode("profile-invalid", self.registry.add, "remote", profile, 0, trusted=True)
        self.assertEqual(list(self.registry.profile_directory.iterdir()), [])
        self.assertEqual(self.registry.read()["revision"], 0)

    def test_invalid_local_source_pin_is_rejected_before_registration(self):
        self.initialize()
        profile = self.local()
        profile["source_hashes"]["botainer/__init__.py"] = "0" * 64
        error = self.assertCode("profile-invalid", self.registry.add, "local", profile, 0, trusted=True)
        self.assertIn("ordinary-source-changed", str(error))
        self.assertNotIn(str(self.root), str(error))
        self.assertEqual(self.registry.read()["entries"], [])

    def test_profile_version_is_not_a_boolean_alias_for_one(self):
        self.initialize()
        profile = self.local()
        profile["version"] = True
        self.assertCode("profile-invalid", self.registry.add, "local", profile, 0, trusted=True)

    def test_disable_and_remove_preserve_files_and_never_stop_runtime(self):
        self.initialize()
        added = self.registry.add("remote", remote(), 0, trusted=True)
        stored = Path(added["entries"][0]["profilePath"])
        runtime = self.root / "unrelated-recovery.json"
        runtime.write_text("unchanged")
        disabled = self.registry.enable("cluster-main", False, 1)
        self.assertFalse(disabled["entries"][0]["enabled"])
        self.assertEqual(self.registry.selected_profiles()["remote_profiles"], ())
        self.assertEqual(self.registry.enable("cluster-main", True, 2)["revision"], 3)
        self.assertEqual(self.registry.remove("cluster-main", 3)["entries"], [])
        self.assertTrue(stored.is_file())
        self.assertEqual(runtime.read_text(), "unchanged")

    def test_read_disable_remove_work_after_installed_tool_changes_but_enable_refuses(self):
        self.initialize()
        self.registry.add("local", self.local(), 0, trusted=True)
        (self.root / "bin/python").write_text("changed fixture")
        self.assertEqual(self.registry.read()["entries"][0]["id"], "local")
        self.registry.enable("local", False, 1)
        self.assertCode("profile-invalid", self.registry.enable, "local", True, 2)
        self.assertEqual(self.registry.remove("local", 2)["entries"], [])

    def test_stale_revision_never_overwrites_other_window(self):
        self.initialize()
        second = ConnectionRegistry(self.path)
        changed = self.registry.add("remote", remote(), 0, trusted=True)
        for operation in (
            lambda: second.add("remote", remote("other", alias="other"), 0, trusted=True),
            lambda: second.enable("cluster-main", False, 0),
            lambda: second.remove("cluster-main", 0),
        ):
            self.assertCode("revision-conflict", operation)
        self.assertEqual(second.read(), changed)

    def test_parallel_writers_commit_at_most_one_shared_revision(self):
        self.initialize()
        gate = threading.Barrier(2)
        results = []
        def add(identifier):
            registry = ConnectionRegistry(self.path)
            gate.wait()
            try:
                registry.add("remote", remote(identifier, alias=identifier), 0, trusted=True)
                results.append("added")
            except ConnectionRegistryError as error:
                results.append(error.code)
        threads = [threading.Thread(target=add, args=(identifier,)) for identifier in ("one", "two")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(results.count("added"), 1)
        self.assertEqual(len(self.registry.read()["entries"]), 1)
        self.assertTrue(set(results) <= {"added", "connections-busy", "connections-revision-conflict"})

    def test_held_lock_returns_actionable_busy_error(self):
        self.initialize()
        fd = private_lock(self.registry.lock_path)
        try:
            self.assertCode("busy", self.registry.add, "remote", remote(), 0, trusted=True)
            self.assertEqual(self.registry.read()["revision"], 0)
        finally:
            os.close(fd)

    def test_ids_are_unique_and_local_is_reserved(self):
        self.initialize()
        self.registry.add("remote", remote(), 0, trusted=True)
        self.assertCode("duplicate-id", self.registry.add, "remote", remote(), 1, trusted=True)
        self.assertCode("id-invalid", self.registry.add, "remote", remote("local"), 1, trusted=True)
        profile = self.local()
        profile["id"] = "another-local"
        self.assertCode("id-invalid", self.registry.add, "local", profile, 1, trusted=True)

    def test_same_remote_account_state_conflicts_even_with_another_source(self):
        self.initialize()
        self.registry.add("remote", remote(), 0, trusted=True)
        duplicate = remote("other", control="/home/test/.other-control")
        duplicate["source_root"] = "/opt/other/source"
        self.assertCode("target-conflict", self.registry.add, "remote", duplicate, 1, trusted=True)

    def test_same_remote_control_root_conflicts_with_another_state(self):
        self.initialize()
        self.registry.add("remote", remote(), 0, trusted=True)
        self.assertCode("target-conflict", self.registry.add, "remote", remote("other", state="/home/test/.other-state"), 1, trusted=True)

    def test_separate_state_and_control_on_same_alias_can_select_another_installation(self):
        self.initialize()
        self.registry.add("remote", remote(), 0, trusted=True)
        other = remote("other", state="/home/test/.other-state", control="/home/test/.other-control")
        self.assertEqual(len(self.registry.add("remote", other, 1, trusted=True)["entries"]), 2)

    def test_host_profiles_cannot_share_a_control_root(self):
        self.initialize()
        self.registry.add("host", self.host(), 0, trusted=True)
        self.assertCode("target-conflict", self.registry.add, "host", self.host("host-other"), 1, trusted=True)

    def test_local_and_host_cannot_share_a_terminal_control_root(self):
        self.initialize()
        host = self.host()
        self.registry.add("host", host, 0, trusted=True)
        local = self.local()
        local["terminal_owner"] = {**host["tmux"], "control_root": host["control_root"]}
        self.assertCode("target-conflict", self.registry.add, "local", local, 1, trusted=True)

    def test_remote_count_limit_includes_disabled_entries(self):
        self.initialize()
        for index in range(16):
            self.registry.add("remote", remote("site-" + str(index), alias="site-" + str(index)), index, trusted=True)
        self.registry.enable("site-0", False, 16)
        self.assertCode("capacity", self.registry.add, "remote", remote("extra", alias="extra"), 17, trusted=True)

    def test_host_count_limit(self):
        self.initialize()
        for index in range(8):
            self.registry.add("host", self.host("host-" + str(index), control="c" + str(index)), index, trusted=True)
        self.assertCode("capacity", self.registry.add, "host", self.host("extra", control="cextra"), 8, trusted=True)

    def test_malformed_requests_have_fixed_errors_without_private_contents(self):
        self.initialize()
        for kind, value in (([], {}), ("unknown", {}), ("remote", []), ("remote", {"private": "secret"})):
            with self.subTest(kind=kind):
                with self.assertRaises(ConnectionRegistryError) as caught:
                    self.registry.add(kind, value, 0, trusted=True)
                self.assertNotIn("secret", str(caught.exception))
        self.assertCode("revision-conflict", self.registry.add, "remote", remote(), False, trusted=True)
        self.assertCode("request-invalid", self.registry.enable, "missing", "false", 0)
        self.assertCode("not-found", self.registry.remove, "missing", 0)

    def test_injected_loader_is_called_on_private_staged_profile(self):
        local = self.local()
        validator = Mock(return_value=SimpleNamespace(verify=Mock()))
        loaders = {kind: validator for kind in ("local", "remote", "host")}
        registry = ConnectionRegistry(self.path, loaders=loaders)
        registry.initialize()
        registry.add("local", local, 0, trusted=True)
        path = validator.call_args.args[0]
        self.assertEqual(path.parent, registry.profile_directory)
        self.assertFalse(path.exists())
        validator.return_value.verify.assert_called_once()

    def test_import_rejects_world_readable_hardlinked_and_symlink_files(self):
        original = self.write_profile("original.json", remote())
        original.chmod(0o644)
        self.assertCode("private-storage-required", self.registry.initialize, [{"kind": "remote", "path": original}])
        original.chmod(0o600)
        alias = self.root / "hardlink.json"
        os.link(original, alias)
        self.assertCode("private-storage-required", self.registry.initialize, [{"kind": "remote", "path": original}])
        alias.unlink()
        alias.symlink_to(original)
        self.assertCode("path-invalid", self.registry.initialize, [{"kind": "remote", "path": alias}])
        self.assertFalse(self.path.exists())

    def test_constructor_and_reads_reject_symlink_ancestry(self):
        target = self.root / "target"
        target.mkdir(mode=0o700)
        alias = self.root / "alias"
        alias.symlink_to(target, target_is_directory=True)
        self.assertCode("path-invalid", ConnectionRegistry, alias / "connections.json")
        self.initialize()
        saved = self.registry.profile_directory.with_name("saved")
        self.registry.profile_directory.rename(saved)
        self.registry.profile_directory.symlink_to(saved, target_is_directory=True)
        self.assertCode("path-invalid", self.registry.read)

    def test_existing_public_parent_is_not_silently_chmodded(self):
        parent = self.root / "public"
        parent.mkdir(mode=0o755)
        registry = ConnectionRegistry(parent / "connections.json")
        self.assertCode("private-storage-required", registry.initialize)
        self.assertEqual(parent.stat().st_mode & 0o777, 0o755)
        self.assertFalse(registry.path.exists())

    def test_mutated_stored_profile_refuses_read_and_selection(self):
        self.initialize()
        value = self.registry.add("remote", remote(), 0, trusted=True)
        Path(value["entries"][0]["profilePath"]).write_text("{}")
        self.assertCode("profile-changed", self.registry.read)
        self.assertCode("profile-changed", self.registry.selected_profiles)

    def test_manifest_cannot_select_an_arbitrary_private_path(self):
        self.initialize()
        value = self.registry.add("remote", remote(), 0, trusted=True)
        outside = self.write_profile("outside.json", remote())
        value["entries"][0]["profilePath"] = str(outside)
        write_private_json(self.path, value)
        self.assertCode("document-invalid", self.registry.read)

    def test_malformed_duplicate_and_oversized_documents_fail_closed(self):
        self.initialize()
        for raw in (b'{"version":1,"version":1,"revision":0,"entries":[]}',
                    b'{"version":1,"revision":NaN,"entries":[]}',
                    b'{"version":true,"revision":0,"entries":[]}',
                    b"[]", b"x" * (2 * 1024 * 1024 + 1)):
            with self.subTest(length=len(raw)):
                self.path.write_bytes(raw)
                self.assertCode("document-invalid", self.registry.read)

    def test_invalid_manifest_kind_does_not_raise_unhandled_type_error(self):
        self.initialize()
        value = self.registry.add("remote", remote(), 0, trusted=True)
        value["entries"][0]["kind"] = []
        write_private_json(self.path, value)
        self.assertCode("document-invalid", self.registry.read)

    def test_atomic_manifest_failure_keeps_previous_selection_and_profile_evidence(self):
        original = self.initialize()
        with patch("botainer_dashboard.connections.write_private_json", side_effect=OSError("private failure")):
            self.assertCode("private-storage-required", self.registry.add, "remote", remote(), 0, trusted=True)
        self.assertEqual(self.registry.read(), original)
        # A failed manifest replacement may leave an unused immutable copy;
        # retaining evidence is safer than deleting any previous profile.
        self.assertEqual(len(list(self.registry.profile_directory.glob("remote-*.json"))), 1)

    def agent_update_fixture(self):
        self.initialize()
        profile = self.host()
        binary = self.root / "codex"
        binary.write_text("# new inert executable, never run\n")
        binary.chmod(0o700)
        profile["environment"]["PATH"] = str(self.root)
        saved = self.registry.add("host", profile, 0, trusted=True)
        return profile, saved, binary

    def test_review_discovery_is_read_only_and_save_preserves_disabled_selection(self):
        _profile, saved, binary = self.agent_update_fixture()
        self.registry.enable("host-main", False, 1)
        before = self.path.read_bytes()
        original = Path(saved["entries"][0]["profilePath"])
        original_bytes = original.read_bytes()
        with patch("subprocess.Popen", side_effect=AssertionError("review/save must not execute")):
            review = self.registry.prepare_agent_update("host-main", "codex", "", 2)
            self.assertEqual(self.path.read_bytes(), before)
            self.assertEqual(review["candidate"], {"path": str(binary), "sha256": digest(binary)})
            after = self.registry.update_agent("host-main", "codex", str(binary), digest(binary), 2, confirmed=True)
        self.assertFalse(after["entries"][0]["enabled"])
        self.assertEqual(after["entries"][0]["id"], saved["entries"][0]["id"])
        self.assertEqual(after["entries"][0]["label"], saved["entries"][0]["label"])
        self.assertEqual(original.read_bytes(), original_bytes)
        self.assertNotEqual(after["entries"][0]["profileDigest"], saved["entries"][0]["profileDigest"])
        data = json.loads(Path(after["entries"][0]["profilePath"]).read_bytes())
        self.assertEqual(data["original_profile"], {"path": str(original), "sha256": digest(original)})

    def test_review_resolves_an_installed_symlink_but_save_requires_exact_reviewed_target(self):
        _profile, _saved, binary = self.agent_update_fixture()
        link = self.root / "agent-link"
        link.symlink_to(binary)
        review = self.registry.prepare_agent_update("host-main", "codex", str(link), 1)
        self.assertEqual(review["candidate"]["path"], str(binary))
        self.assertCode("agent-changed", self.registry.update_agent, "host-main", "codex", str(link), digest(binary), 1, confirmed=True)
        self.assertEqual(self.registry.read()["revision"], 1)

    def test_update_requires_confirmation_and_current_revision(self):
        _profile, _saved, binary = self.agent_update_fixture()
        for confirmation in (False, None, 1, "true"):
            self.assertCode("trust-required", self.registry.update_agent, "host-main", "codex", str(binary), digest(binary), 1, confirmed=confirmation)
        self.registry.enable("host-main", False, 1)
        self.assertCode("revision-conflict", self.registry.update_agent, "host-main", "codex", str(binary), digest(binary), 1, confirmed=True)
        self.assertCode("revision-conflict", self.registry.prepare_agent_update, "host-main", "codex", str(binary), 1)
        self.assertFalse(self.registry.read()["entries"][0]["enabled"])

    def test_agent_review_cannot_select_other_kind_new_agent_or_shell_syntax(self):
        _profile, _saved, binary = self.agent_update_fixture()
        self.assertCode("host-agent-required", self.registry.prepare_agent_update, "host-main", "claude", str(binary), 1)
        self.assertCode("host-agent-required", self.registry.prepare_agent_update, "host-main", "codex;anything", str(binary), 1)
        for path in ("codex", "~/codex", str(binary) + "\n", str(self.root / ".." / "codex")):
            self.assertCode("path-invalid", self.registry.prepare_agent_update, "host-main", "codex", path, 1)
        self.assertCode("agent-not-found", self.registry.prepare_agent_update, "host-main", "codex", str(self.root / "missing"), 1)
        self.assertEqual(self.registry.read()["revision"], 1)

    def test_same_executable_review_never_creates_pointless_revision(self):
        profile, _saved, _binary = self.agent_update_fixture()
        tool = profile["agents"]["codex"]
        review = self.registry.prepare_agent_update("host-main", "codex", tool["path"], 1)
        self.assertFalse(review["changed"])
        self.assertCode("agent-unchanged", self.registry.update_agent, "host-main", "codex", tool["path"], tool["sha256"], 1, confirmed=True)
        self.assertEqual(self.registry.read()["revision"], 1)


if __name__ == "__main__":
    unittest.main()
