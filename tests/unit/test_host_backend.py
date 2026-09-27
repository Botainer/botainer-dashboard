"""Host-agent boundary tests; no tmux server, agent, shell, or network starts."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from botainer_dashboard.backend_errors import BackendUnavailable
from botainer_dashboard.host_backend import HostAgentBackend, HostProfile, _CONFIG
from botainer_dashboard.inventory import InventoryError
from botainer_dashboard.pairing import write_private_json


REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("host_helper_test", REPO / "tools/host_agent_helper.py")
HELPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER)


def request():
    return str(uuid.uuid4())


class FakeTmux(HostAgentBackend):
    """Answers only the actual backend's narrow tmux command protocol."""
    def __init__(self, *args):
        super().__init__(*args)
        self.calls = []
        self.dead = False
        self.exit_code = ""
        self.clients = b""
        self.extra_pane = False
        self.new_failure = False
        self.owner_pid = "21001"
        self.socket_identity = [31, 41]
        self.probe_failure = False
        self.kill_failure = False

    def _socket(self, item):
        return self.socket_identity

    def _tmux(self, item, *args, create=False):
        self.calls.append((item["id"], args, create))
        command = args[0]
        if command == "new-session":
            if self.new_failure:
                raise InventoryError("fixture-reply-lost")
            return b""
        if command == "display-message":
            values = [self.owner_pid, "$0", "@0", "%0", "22001", "1700000000",
                      str(self._folder(item) / "s"), item["nonce"], "dashboard-" + item["id"],
                      "1" if self.dead else "0", self.exit_code, "", "1"]
            return ("\t".join(values) + "\n").encode()
        if command == "list-panes":
            return b"%0\n%1\n" if self.extra_pane else b"%0\n"
        if command == "list-clients":
            return self.clients
        if command == "kill-pane":
            if self.kill_failure:
                raise InventoryError("fixture-cleanup-reply-lost")
            self.dead = True
            return b""
        raise AssertionError(args)

    def _wait_pane_absent(self, pid):
        if self.probe_failure:
            raise BackendUnavailable("host-stop-process-still-observed")


class FakeAttachment:
    def __init__(self, *args, **kwargs):
        self.argv, self.kwargs = args[0], kwargs
        self.failed_close = False

    def close(self, **_kwargs):
        if self.failed_close:
            raise PermissionError("fixture retained attachment")
        return 0

    def read(self, *_args, **_kwargs):
        return None

    def write(self, data):
        return len(data)

    def resize(self, *_args):
        return None


class HostBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="host-test-", dir="/private/tmp" if Path("/private/tmp").is_dir() else "/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.control = self.root / "c"
        self.control.mkdir(mode=0o700)
        self.projects = self.root / "projects"
        self.projects.mkdir()
        self.binary = self.root / "installed-agent"
        self.binary.write_text("inert fixture; never executed\n")
        self.binary.chmod(0o700)
        tool = {"path": str(self.binary), "sha256": hashlib.sha256(self.binary.read_bytes()).hexdigest()}
        self.tmux = self.root / "installed-tmux"
        self.tmux.write_text("inert tmux fixture; never executed\n")
        self.tmux.chmod(0o700)
        tmux = {"path": str(self.tmux), "sha256": hashlib.sha256(self.tmux.read_bytes()).hexdigest()}
        self.profile = {"version": 1, "id": "host-fixture", "label": "This computer — host",
                        "home": str(self.root), "tmux": tmux, "project_roots": {"projects": str(self.projects)},
                        "control_root": str(self.control), "agents": {"codex": dict(tool),
                        "claude": dict(tool, external_tools="disabled")}, "default_agent": "codex", "environment": {"PATH": "/usr/bin:/bin"}}
        self.profile_path = self.root / "profile.json"
        write_private_json(self.profile_path, self.profile)
        # Backend deliberately locates its fixed trusted helper in the source
        # repository while all profile/state data remains disposable.
        self.backend = FakeTmux(self.root, self.profile_path)

    def create(self, **changes):
        data = {"rootId": "projects", "installationId": "host-fixture", "path": "test-project",
                "name": "Test project", "mode": "create", "requestId": request()}
        data.update(changes)
        return self.backend.create_project(data)["project"], data

    def start(self):
        project, _ = self.create()
        result = self.backend.start_session(project["id"], request())
        return project, result["session"]

    def revision(self, *, name="revision.json", value=None):
        if value is None:
            value = json.loads(json.dumps(self.profile))
            replacement = self.root / "updated-agent"
            replacement.write_text("inert updated agent; never executed\n")
            replacement.chmod(0o700)
            value["agents"]["codex"].update(path=str(replacement), sha256=hashlib.sha256(replacement.read_bytes()).hexdigest())
        value.update(version=2, original_profile={"path": str(self.profile_path),
                                                  "sha256": hashlib.sha256(self.profile_path.read_bytes()).hexdigest()})
        path = self.root / name
        write_private_json(path, value)
        return path

    def test_load_and_construction_never_execute_or_start_servers(self):
        with patch("botainer_dashboard.host_backend.bounded_run", side_effect=AssertionError("no execution")):
            loaded = HostProfile(self.profile_path)
            backend = HostAgentBackend(self.root, self.profile_path)
            self.assertEqual(loaded.fingerprint, backend.profile.fingerprint)
            self.assertEqual(backend.execution_kind, "host")
        self.assertFalse((self.root / "host-state.json").exists())
        self.assertEqual(self.backend.root, self.root / ".local" / "host-agents" / loaded.fingerprint)

    def test_profile_rejects_unsafe_environment_tool_hash_and_claude_policy(self):
        for mutate in [lambda p: p["environment"].update(NODE_OPTIONS="--import=project.js"),
                       lambda p: p["environment"].update(PATH=".:/usr/bin"),
                       lambda p: p["agents"]["claude"].pop("external_tools"),
                       lambda p: p["agents"]["codex"].update(sha256="invalid"),
                       lambda p: p.update(extra="no")]:
            value = json.loads(json.dumps(self.profile))
            mutate(value)
            write_private_json(self.profile_path, value)
            with self.assertRaises(BackendUnavailable):
                HostProfile(self.profile_path)

    def test_missing_or_changed_agent_does_not_hide_sessions_or_allow_new_launch(self):
        project, session = self.start()
        before = self.backend._load()["sessions"][0]
        for remove in (False, True):
            with self.subTest(remove=remove):
                if remove:
                    self.binary.unlink()
                else:
                    self.binary.write_text("changed installed agent\n")
                restarted = FakeTmux(self.root, self.profile_path)
                view = restarted.snapshot()
                self.assertTrue(all(not agent["available"] for agent in view["availableAgents"]))
                self.assertFalse(view["projects"][0]["capabilities"]["startSession"])
                self.assertIn("Settings", view["projects"][0]["startUnavailableReason"])
                self.assertTrue(view["sessions"][0]["capabilities"]["attachTerminal"])
                existing = restarted.start_session(project["id"], before["requestId"])["session"]
                self.assertEqual(existing["runtimeId"], session["runtimeId"])
                with self.assertRaisesRegex(BackendUnavailable, "agent-executable-unavailable"):
                    restarted.start_session(project["id"], request())
                with patch("botainer_dashboard.host_backend.PtyAttachment", FakeAttachment):
                    attachment = restarted.attach(session["contextNamespace"], session["runtimeId"], 80, 24)
                    attachment.close()
                self.assertFalse(any(call[1][0] == "new-session" for call in restarted.calls))
        self.assertEqual(restarted._load()["sessions"][0]["owner"], before["owner"])
        self.assertEqual(restarted.stop_session(session["contextNamespace"], session["runtimeId"], request())["session"]["state"], "stopped")

    def test_agent_hash_mismatch_is_unavailable_until_exact_reviewed_hash(self):
        value = json.loads(json.dumps(self.profile))
        value["agents"]["codex"]["sha256"] = "0" * 64
        write_private_json(self.profile_path, value)
        profile = HostProfile(self.profile_path)
        with self.assertRaisesRegex(BackendUnavailable, "agent-executable-unavailable"):
            profile.verify_agent("codex")
        profile.verify_agent("claude")

    def test_unchosen_stale_agent_does_not_block_available_agent(self):
        value = json.loads(json.dumps(self.profile))
        value["agents"]["codex"]["path"] = str(self.root / "removed-agent")
        write_private_json(self.profile_path, value)
        self.backend = FakeTmux(self.root, self.profile_path)
        project, _ = self.create()
        view = self.backend.snapshot()
        self.assertTrue(view["projects"][0]["capabilities"]["startSession"])
        self.assertFalse(next(agent for agent in view["availableAgents"] if agent["id"] == "codex")["available"])
        self.assertEqual(self.backend.start_session(project["id"], request(), agent="claude")["session"]["agent"], "claude")

    def test_agent_path_replaced_by_symlink_keeps_other_agent_and_owner_usable(self):
        separate = self.root / "installed-claude"
        separate.write_bytes(self.binary.read_bytes())
        separate.chmod(0o700)
        self.profile["agents"]["claude"]["path"] = str(separate)
        write_private_json(self.profile_path, self.profile)
        self.backend = FakeTmux(self.root, self.profile_path)
        project, session = self.start()
        original_owner = self.backend._load()["sessions"][0]["owner"]
        # Even identical bytes at the symlink target do not authorize following
        # the replacement path for a new launch.
        self.binary.unlink()
        self.binary.symlink_to(separate)
        restarted = FakeTmux(self.root, self.profile_path)
        view = restarted.snapshot()
        agents = {agent["id"]: agent for agent in view["availableAgents"]}
        self.assertFalse(agents["codex"]["available"])
        self.assertTrue(agents["claude"]["available"])
        self.assertEqual(view["sessions"][0]["state"], "running")
        self.assertEqual(restarted._load()["sessions"][0]["owner"], original_owner)
        with self.assertRaisesRegex(BackendUnavailable, "agent-executable-unavailable"):
            restarted.start_session(project["id"], request(), agent="codex")
        with patch("botainer_dashboard.host_backend.PtyAttachment", FakeAttachment):
            attachment = restarted.attach(session["contextNamespace"], session["runtimeId"], 80, 24)
            attachment.close()
        self.assertEqual(restarted.start_session(project["id"], request(), agent="claude")["session"]["agent"], "claude")
        self.assertEqual(restarted.stop_session(session["contextNamespace"], session["runtimeId"], request())["session"]["state"], "stopped")

    def test_tmux_change_still_fences_all_control(self):
        _project, session = self.start()
        self.tmux.write_text("changed tmux\n")
        with self.assertRaises(BackendUnavailable):
            self.backend.attach(session["contextNamespace"], session["runtimeId"], 80, 24)
        with self.assertRaises(BackendUnavailable):
            HostProfile(self.profile_path)

    def test_revision_preserves_registry_owners_and_exact_launch_provenance(self):
        project, session = self.start()
        original = self.backend._load()["sessions"][0]
        revision_path = self.revision()
        self.binary.unlink()
        revised = FakeTmux(self.root, revision_path)
        self.assertNotEqual(revised.profile.fingerprint, self.backend.profile.fingerprint)
        self.assertEqual(revised.profile.identity_fingerprint, self.backend.profile.fingerprint)
        self.assertEqual(revised.root, self.backend.root)
        self.assertEqual(revised.namespace, self.backend.namespace)
        view = revised.snapshot()
        self.assertEqual(view["projects"][0]["id"], project["id"])
        self.assertEqual(view["sessions"][0]["runtimeId"], session["runtimeId"])
        self.assertEqual(view["sessions"][0]["executable"], str(self.binary))
        self.assertEqual(revised._load()["sessions"][0]["owner"], original["owner"])
        with patch("botainer_dashboard.host_backend.PtyAttachment", FakeAttachment):
            attachment = revised.attach(session["contextNamespace"], session["runtimeId"], 80, 24)
            attachment.close()
        new_session = revised.start_session(project["id"], request())["session"]
        self.assertEqual(new_session["launchProfile"], revised.profile.fingerprint)
        self.assertEqual(new_session["executable"], str(self.root / "updated-agent"))
        self.assertEqual(new_session["sha256"], revised.profile.data["agents"]["codex"]["sha256"])

    def test_legacy_session_display_uses_original_agent_not_updated_selection(self):
        _project, session = self.start()
        state = self.backend._load()
        for name in ("launchProfile", "executable", "sha256"):
            state["sessions"][0].pop(name)
        self.backend._save(state)
        revised = FakeTmux(self.root, self.revision())
        view = revised.snapshot()["sessions"][0]
        self.assertEqual(view["runtimeId"], session["runtimeId"])
        self.assertEqual(view["executable"], str(self.binary))
        self.assertEqual(view["sha256"], self.profile["agents"]["codex"]["sha256"])
        self.assertEqual(view["launchProfile"], self.backend.profile.fingerprint)

    def test_repeated_revisions_refer_to_original_without_identity_drift(self):
        first_path = self.revision()
        first = HostProfile(first_path)
        value = json.loads(first_path.read_text())
        value["agents"]["codex"]["path"] = str(self.binary)
        value["agents"]["codex"]["sha256"] = self.profile["agents"]["codex"]["sha256"]
        second_path = self.root / "revision-2.json"
        write_private_json(second_path, value)
        second = HostProfile(second_path)
        self.assertEqual(first.identity_fingerprint, second.identity_fingerprint)
        self.assertNotEqual(first.fingerprint, second.fingerprint)

    def test_revision_rejects_all_scope_changes_and_agent_membership_changes(self):
        mutations = [lambda p: p.update(label="Renamed"), lambda p: p.update(id="different"),
                     lambda p: p.update(home=str(self.projects)), lambda p: p.update(control_root=str(self.projects)),
                     lambda p: p.update(default_agent="claude"), lambda p: p["environment"].update(LANG="en_US.UTF-8"),
                     lambda p: p["project_roots"].update(other=str(self.root)),
                     lambda p: p["tmux"].update(sha256="0" * 64),
                     lambda p: p["agents"].pop("claude"), lambda p: p["agents"].update(other=dict(p["agents"]["codex"])),
                     lambda p: p["agents"]["codex"].update(label="Changed"),
                     lambda p: p["agents"]["claude"].update(external_tools="default")]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                value = json.loads(json.dumps(self.profile))
                mutate(value)
                with self.assertRaisesRegex(BackendUnavailable, "profile-scope-changed"):
                    HostProfile(self.revision(name=f"revision-{index}.json", value=value))

    def test_revision_rejects_wrong_baseline_hash_chain_missing_private_and_symlink(self):
        path = self.revision()
        good = json.loads(path.read_text())
        first = HostProfile(path)
        cases = []
        value = json.loads(json.dumps(good)); value["original_profile"]["sha256"] = "0" * 64; cases.append(value)
        value = json.loads(json.dumps(good)); value["original_profile"] = {"path": str(path), "sha256": first.fingerprint}; cases.append(value)
        value = json.loads(json.dumps(good)); value["original_profile"]["path"] = str(self.root / "missing"); cases.append(value)
        link = self.root / "baseline-link.json"; link.symlink_to(self.profile_path)
        value = json.loads(json.dumps(good)); value["original_profile"]["path"] = str(link); cases.append(value)
        for index, value in enumerate(cases):
            write_private_json(self.root / f"bad-{index}.json", value)
            with self.subTest(index=index), self.assertRaises((BackendUnavailable, OSError, ValueError)):
                HostProfile(self.root / f"bad-{index}.json")
        self.profile_path.chmod(0o644)
        with self.assertRaises(ValueError):
            HostProfile(path)

    def test_baseline_change_after_loading_fences_existing_control(self):
        _project, session = self.start()
        revised = FakeTmux(self.root, self.revision())
        self.profile_path.write_text(self.profile_path.read_text() + "\n")
        with self.assertRaisesRegex(BackendUnavailable, "original-profile-changed"):
            revised.attach(session["contextNamespace"], session["runtimeId"], 80, 24)
        self.assertFalse(any(call[1][0] == "kill-pane" for call in revised.calls))

    def test_baseline_must_be_private_single_link_in_same_directory(self):
        path = self.revision()
        original = json.loads(path.read_text())
        other = self.root / "other"
        other.mkdir(mode=0o700)
        copied = other / "baseline.json"
        write_private_json(copied, self.profile)
        value = json.loads(json.dumps(original))
        value["original_profile"]["path"] = str(copied)
        write_private_json(path, value)
        with self.assertRaisesRegex(BackendUnavailable, "original-profile-invalid"):
            HostProfile(path)
        write_private_json(path, original)
        os.link(self.profile_path, self.root / "baseline-hardlink")
        with self.assertRaises(ValueError):
            HostProfile(path)

    def test_baseline_swap_to_revision_during_validation_cannot_create_chain(self):
        from botainer_dashboard import host_backend
        path = self.revision()
        real_read = host_backend._profile_document
        reads = 0
        def swap(candidate):
            nonlocal reads
            value, digest = real_read(candidate)
            if candidate == self.profile_path:
                reads += 1
                if reads == 1:
                    changed = json.loads(json.dumps(value))
                    changed.update(version=2, original_profile={"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
                    write_private_json(candidate, changed)
            return value, digest
        with patch.object(host_backend, "_profile_document", side_effect=swap):
            with self.assertRaisesRegex(BackendUnavailable, "original-profile-invalid"):
                HostProfile(path)
        self.assertEqual(reads, 2)

    def test_revision_rejects_malformed_original_tool_even_with_matching_digest(self):
        malformed = json.loads(json.dumps(self.profile))
        malformed["agents"]["codex"].pop("path")
        write_private_json(self.profile_path, malformed)
        with self.assertRaisesRegex(BackendUnavailable, "executable-selection-invalid"):
            HostProfile(self.revision())

    def test_changed_agent_after_status_is_rechecked_before_new_launch(self):
        project, _ = self.create()
        self.assertTrue(all(agent["available"] for agent in self.backend.snapshot()["availableAgents"]))
        self.binary.write_text("changed after review\n")
        with self.assertRaisesRegex(BackendUnavailable, "agent-executable-unavailable"):
            self.backend.start_session(project["id"], request())
        self.assertFalse(any(call[1][0] == "new-session" for call in self.backend.calls))

    def test_revision_reattach_keeps_writer_exclusion(self):
        _project, session = self.start()
        revised = FakeTmux(self.root, self.revision())
        with patch("botainer_dashboard.host_backend.PtyAttachment", FakeAttachment):
            attachment = revised.attach(session["contextNamespace"], session["runtimeId"], 80, 24)
            try:
                with self.assertRaisesRegex(BackendUnavailable, "terminal-writer-busy"):
                    self.backend.attach(session["contextNamespace"], session["runtimeId"], 80, 24)
            finally:
                attachment.close()

    def test_revision_owner_substitution_does_not_adopt_or_stop(self):
        _project, session = self.start()
        revised = FakeTmux(self.root, self.revision())
        revised.owner_pid = "9999"
        self.assertEqual(revised.snapshot()["sessions"][0]["state"], "unknown")
        with self.assertRaises(BackendUnavailable):
            revised.stop_session(session["contextNamespace"], session["runtimeId"], request())
        self.assertFalse(any(call[1][0] == "kill-pane" for call in revised.calls))

    def test_profile_swap_between_parse_and_digest_is_refused(self):
        original = self.profile_path.read_bytes()
        original_read = __import__("botainer_dashboard.host_backend", fromlist=["read_private_json"]).read_private_json
        def swapping(path, **kwargs):
            value = original_read(path, **kwargs)
            self.profile_path.write_bytes(original + b"\n")
            return value
        with patch("botainer_dashboard.host_backend.read_private_json", side_effect=swapping), self.assertRaises(BackendUnavailable):
            HostProfile(self.profile_path)

    def test_plain_project_setup_is_idempotent_and_has_no_botainer_metadata(self):
        project, data = self.create()
        self.assertEqual(self.backend.create_project(data)["project"], project)
        self.assertFalse((Path(project["path"]) / ".botainer").exists())
        self.assertEqual(project["executionKind"], "host")
        self.assertFalse(project["capabilities"]["configRead"])
        with self.assertRaises(BackendUnavailable):
            self.backend.create_project(dict(data, name="changed request"))

    def test_open_existing_folder_keeps_files_and_is_separate_from_botainer(self):
        target = self.projects / "existing"
        target.mkdir()
        (target / "notes.txt").write_text("keep")
        project, _ = self.create(path="existing", mode="open")
        self.assertEqual(self.backend.read_file(project["id"], "notes.txt")["text"], "keep")
        self.assertEqual(sorted(p.name for p in target.iterdir()), ["notes.txt"])

    def test_file_access_excludes_symlinks_hardlinks_secrets_and_binary(self):
        project, _ = self.create()
        target = Path(project["path"])
        (target / "a.txt").write_text("hello")
        (target / "credentials.json").write_text("secret")
        (target / "link").symlink_to(target / "a.txt")
        (target / "binary").write_bytes(b"x\0y")
        os.link(target / "a.txt", target / "hardlink")
        for path in ("../outside", "credentials.json", "link", "hardlink", "binary"):
            with self.subTest(path=path), self.assertRaises((ValueError, OSError, BackendUnavailable)):
                self.backend.read_file(project["id"], path)
        names = {e["name"] for e in self.backend.list_files(project["id"])["entries"]}
        self.assertNotIn("link", names)
        self.assertNotIn("credentials.json", names)

    def test_replaced_project_folder_cannot_be_launched_or_read(self):
        project, _ = self.create()
        target = Path(project["path"])
        target.rename(target.with_name("original"))
        target.mkdir()
        with self.assertRaises(BackendUnavailable):
            self.backend.start_session(project["id"], request())
        with self.assertRaises(BackendUnavailable):
            self.backend.list_files(project["id"])
        self.assertEqual(self.backend.calls, [])

    def test_launch_uses_direct_fixed_helper_args_and_durable_request_once(self):
        project, _ = self.create()
        rid = request()
        first = self.backend.start_session(project["id"], rid)["session"]
        second = self.backend.start_session(project["id"], rid)["session"]
        self.assertEqual(first["runtimeId"], second["runtimeId"])
        launches = [call for call in self.backend.calls if call[1][0] == "new-session"]
        self.assertEqual(len(launches), 1)
        argv = launches[0][1]
        self.assertIn("-I", argv)
        self.assertIn("-S", argv)
        self.assertIn("codex", argv)
        self.assertNotIn("--dangerously-skip-permissions", argv)
        self.assertNotIn("--yolo", argv)
        self.assertTrue(first["capabilities"]["attachTerminal"])
        with self.assertRaises(BackendUnavailable):
            self.backend.start_session(project["id"], rid, agent="claude")

    def test_ambiguous_launch_is_persisted_and_never_retried(self):
        project, _ = self.create()
        self.backend.new_failure = True
        rid = request()
        session = self.backend.start_session(project["id"], rid)["session"]
        self.assertEqual(session["state"], "unknown")
        self.backend.start_session(project["id"], rid)
        with self.assertRaises(BackendUnavailable):
            self.backend.start_session(project["id"], request())
        self.assertEqual(sum(c[1][0] == "new-session" for c in self.backend.calls), 1)
        restarted = FakeTmux(self.root, self.profile_path)
        self.assertEqual(restarted.snapshot()["sessions"][0]["state"], "unknown")
        self.assertEqual(restarted.calls, [])

    def test_restart_reobserves_exact_owner_and_owner_change_fences_controls(self):
        _project, session = self.start()
        restarted = FakeTmux(self.root, self.profile_path)
        self.assertEqual(restarted.snapshot()["sessions"][0]["state"], "running")
        restarted.owner_pid = "9999"
        changed = restarted.snapshot()["sessions"][0]
        self.assertEqual(changed["state"], "unknown")
        with self.assertRaises(BackendUnavailable):
            restarted.stop_session(session["contextNamespace"], session["runtimeId"], request())
        self.assertFalse(any(c[1][0] == "kill-pane" for c in restarted.calls))

    def test_socket_replacement_and_additional_panes_are_not_adopted(self):
        _project, _session = self.start()
        self.backend.socket_identity = [88, 99]
        self.assertEqual(self.backend.snapshot()["sessions"][0]["state"], "unknown")
        self.backend.socket_identity = [31, 41]
        self.backend.extra_pane = True
        self.assertEqual(self.backend.snapshot()["sessions"][0]["state"], "unknown")

    def test_one_writer_detach_preserves_owner_and_does_not_take_over_clients(self):
        _project, session = self.start()
        with patch("botainer_dashboard.host_backend.PtyAttachment", FakeAttachment):
            attachment = self.backend.attach(session["contextNamespace"], session["runtimeId"], 100, 30)
            try:
                with self.assertRaises(BackendUnavailable):
                    self.backend.attach(session["contextNamespace"], session["runtimeId"], 100, 30)
                self.assertNotIn("-d", attachment.client.argv)
                self.assertNotIn("-D", attachment.client.argv)
                self.assertIn("-N", attachment.client.argv)
                self.assertEqual(attachment.write(b"hello"), 5)
            finally:
                attachment.close()
            self.assertEqual(self.backend.snapshot()["sessions"][0]["state"], "running")
            self.backend.clients = b"40001\n"
            with self.assertRaises(BackendUnavailable):
                self.backend.attach(session["contextNamespace"], session["runtimeId"], 100, 30)
        self.assertFalse(any(c[1][0] == "kill-pane" for c in self.backend.calls))

    def test_failed_attachment_cleanup_keeps_writer_lease(self):
        _project, session = self.start()
        with patch("botainer_dashboard.host_backend.PtyAttachment", FakeAttachment):
            attachment = self.backend.attach(session["contextNamespace"], session["runtimeId"], 100, 30)
            try:
                attachment.client.failed_close = True
                with self.assertRaises(PermissionError):
                    attachment.close()
                with self.assertRaises(BackendUnavailable):
                    self.backend.attach(session["contextNamespace"], session["runtimeId"], 100, 30)
            finally:
                attachment.client.failed_close = False
                attachment.close()

    def test_stop_is_exact_once_no_kill_server_and_process_presence_stays_unknown(self):
        _project, session = self.start()
        self.backend.probe_failure = True
        rid = request()
        stopped = self.backend.stop_session(session["contextNamespace"], session["runtimeId"], rid)["session"]
        self.assertEqual(stopped["state"], "unknown")
        self.backend.stop_session(session["contextNamespace"], session["runtimeId"], rid)
        self.assertEqual([c[1] for c in self.backend.calls if c[1][0].startswith("kill")], [("kill-pane", "-t", "%0")])

    def test_stop_confirmed_after_absence_has_observed_end_time(self):
        _project, session = self.start()
        stopped = self.backend.stop_session(session["contextNamespace"], session["runtimeId"], request())["session"]
        self.assertEqual(stopped["state"], "stopped")
        self.assertTrue(stopped["recordedEndedAt"])
        self.assertEqual(stopped["endTimeAccuracy"], "observed")

    def test_natural_exit_retains_real_exit_status_not_fake_agent_readiness(self):
        _project, _session = self.start()
        self.backend.dead = True
        self.backend.exit_code = "7"
        result = self.backend.snapshot()["sessions"][0]
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["exitCode"], 7)
        self.assertEqual(result["ownerCleanup"], "completed")
        recorded = result["recordedEndedAt"]
        self.backend.snapshot()
        self.assertEqual(self.backend.snapshot()["sessions"][0]["recordedEndedAt"], recorded)
        self.assertEqual(sum(c[1][0] == "kill-pane" for c in self.backend.calls), 1)

    def test_natural_cleanup_uncertainty_never_retries_or_erases_proven_exit(self):
        _project, _session = self.start()
        self.backend.dead = True
        self.backend.exit_code = "0"
        self.backend.kill_failure = True
        result = self.backend.snapshot()["sessions"][0]
        self.assertEqual(result["state"], "stopped")
        self.assertEqual(result["ownerCleanup"], "unknown")
        self.backend.snapshot()
        self.assertEqual(sum(c[1][0] == "kill-pane" for c in self.backend.calls), 1)

    def test_changed_owner_after_observed_exit_is_not_cleaned(self):
        _project, _session = self.start()
        state = self.backend._load()
        self.backend.dead = True
        self.backend.exit_code = "0"
        self.backend._refresh_session(state["sessions"][0])
        self.backend._save(state)
        self.backend.owner_pid = "9999"
        self.backend._cleanup_dead(state)
        self.assertEqual(state["sessions"][0]["cleanupState"], "owner-unavailable")
        self.assertEqual(state["sessions"][0]["state"], "stopped")
        self.assertFalse(any(c[1][0] == "kill-pane" for c in self.backend.calls))

    def test_cleanup_intent_is_saved_before_exact_pane_removal(self):
        _project, _session = self.start()
        self.backend.dead = True
        real_tmux = self.backend._tmux
        def checking(item, *args, **kwargs):
            if args[0] == "kill-pane":
                persisted = self.backend._load()["sessions"][0]
                self.assertEqual(persisted["state"], "stopped")
                self.assertTrue(persisted["endedAt"])
                self.assertEqual(persisted["cleanupState"], "dispatching")
            return real_tmux(item, *args, **kwargs)
        with patch.object(self.backend, "_tmux", side_effect=checking):
            self.backend.snapshot()

    def test_tmux_policy_removes_command_prefixes_and_clipboard_passthrough(self):
        for setting in ("prefix None", "prefix2 None", "unbind-key -a -T root", "set-clipboard off",
                        "allow-passthrough off", "remain-on-exit on", "exit-unattached off"):
            self.assertIn(setting, _CONFIG)
        self.assertNotIn("run-shell", _CONFIG)

    def test_helper_preserves_native_permissions_disables_claude_external_startup(self):
        info = self.root.stat()
        args = ["helper", "claude", str(self.binary), str(info.st_dev), str(info.st_ino), self.profile["agents"]["claude"]["sha256"]]
        with patch.object(HELPER.sys, "argv", args), patch.object(HELPER.os, "stat", return_value=info), \
                patch.dict(os.environ, {"TMUX": "unrelated", "TMUX_PANE": "%98"}), \
                patch.object(HELPER.os, "execve") as execute:
            HELPER.main()
        executable, argv, env = execute.call_args.args
        self.assertEqual(executable, str(self.binary))
        self.assertIn("--strict-mcp-config", argv)
        self.assertEqual(json.loads(argv[argv.index("--mcp-config") + 1]), {"mcpServers": {}})
        settings = json.loads(argv[argv.index("--settings") + 1])
        self.assertTrue(settings["disableAllHooks"])
        self.assertEqual(settings["env"]["DISABLE_UPDATES"], "1")
        self.assertEqual(settings["env"]["DISABLE_AUTOUPDATER"], "1")
        self.assertEqual(settings["env"]["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"], "1")
        self.assertEqual(settings["env"]["CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL"], "1")
        self.assertEqual(settings["env"]["FORCE_AUTOUPDATE_PLUGINS"], "0")
        for name, value in settings["env"].items():
            self.assertEqual(env[name], value)
        self.assertNotIn("TMUX", env)
        self.assertNotIn("--dangerously-skip-permissions", argv)
        self.assertNotIn("--permission-mode", argv)

    def test_helper_refuses_changed_cwd_before_exec(self):
        info = self.root.stat()
        args = ["helper", "codex", str(self.binary), str(info.st_dev), "99999", self.profile["agents"]["codex"]["sha256"]]
        with patch.object(HELPER.sys, "argv", args), patch.object(HELPER.os, "stat", return_value=info), \
                patch.object(HELPER.os, "execve") as execute, self.assertRaises(SystemExit):
            HELPER.main()
        execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
