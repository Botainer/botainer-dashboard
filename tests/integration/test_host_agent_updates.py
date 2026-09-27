"""Synthetic upgrade acceptance across registry, launcher, ownership and pairing.

The real profile/registry/backend code runs against disposable private files.
Only the narrow tmux transport and terminal attachment are inert substitutes;
no agent, subprocess, tmux server, network or installed configuration is used.
This establishes upgrade identity behavior, not actual agent compatibility.
"""
import copy
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

from botainer_dashboard.access import LoopbackAccess
from botainer_dashboard.backend_errors import BackendUnavailable
from botainer_dashboard.connections import ConnectionRegistry, ConnectionRegistryError
from botainer_dashboard.host_backend import HostAgentBackend, HostProfile
from botainer_dashboard.launcher import authentication_profile, select_connections_backend, select_installed_backend
from botainer_dashboard.pairing import PairingStore, read_private_json, write_private_json


def request_id():
    return str(uuid.uuid4())


class InertOwner(HostAgentBackend):
    """Reply to the fixed owner protocol without interpreting command strings."""

    def __init__(self, *args):
        super().__init__(*args)
        self.calls = []
        self.owner_pid = "21001"
        self.dead = set()

    def _socket(self, _item):
        return [31, 41]

    def _tmux(self, item, *args, create=False):
        self.calls.append((item["id"], args, create))
        if args[0] == "new-session":
            return b""
        if args[0] == "display-message":
            fields = [self.owner_pid, "$0", "@0", "%0", "22001", "1700000000",
                      str(self._folder(item) / "s"), item["nonce"], "dashboard-" + item["id"],
                      "1" if item["id"] in self.dead else "0", "", "", "1"]
            return ("\t".join(fields) + "\n").encode()
        if args[0] == "list-panes":
            return b"%0\n"
        if args[0] == "list-clients":
            return b""
        if args[0] == "kill-pane":
            self.dead.add(item["id"])
            return b""
        raise AssertionError("unexpected owner operation: " + repr(args))

    def _wait_pane_absent(self, _pid):
        return None


class InertAttachment:
    def __init__(self, argv, **_kwargs):
        self.argv = argv

    def close(self, **_kwargs):
        return 0

    def write(self, data):
        return len(data)


class HostAgentUpdateAcceptance(unittest.TestCase):
    def setUp(self):
        base = "/private/tmp" if Path("/private/tmp").is_dir() else "/tmp"
        self.temp = tempfile.TemporaryDirectory(prefix="bd-up-", dir=base)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.control = self.root / "c"
        self.control.mkdir(mode=0o700)
        self.projects = self.root / "projects"
        self.projects.mkdir(mode=0o700)
        self.tools = {name: self.binary(name + "-v1") for name in ("tmux", "codex", "claude")}
        self.original = {"version": 1, "id": "host-fixture", "label": "Host fixture",
            "home": str(self.root), "tmux": self.pin(self.tools["tmux"]),
            "project_roots": {"work": str(self.projects)}, "control_root": str(self.control),
            "agents": {"codex": self.pin(self.tools["codex"]),
                       "claude": {**self.pin(self.tools["claude"]), "external_tools": "disabled"}},
            "default_agent": "codex", "environment": {"PATH": "/usr/bin:/bin"}}
        self.registry = ConnectionRegistry(self.root / "connections.json")
        self.registry.initialize()
        saved = self.registry.add("host", self.original, 0, trusted=True)
        self.original_path = Path(saved["entries"][0]["profilePath"])
        self.original_bytes = self.original_path.read_bytes()
        self.no_subprocess = patch("subprocess.Popen", side_effect=AssertionError("no process may execute"))
        self.no_subprocess.start()
        self.addCleanup(self.no_subprocess.stop)
        self.fake_owner = patch("botainer_dashboard.host_backend.HostAgentBackend", InertOwner)
        self.fake_owner.start()
        self.addCleanup(self.fake_owner.stop)

    def binary(self, name):
        path = self.root / name
        path.write_text("Inert executable fixture " + name + "; never execute.\n")
        path.chmod(0o700)
        return path

    @staticmethod
    def pin(path):
        return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def launch(self):
        combined, mode, manager = select_connections_backend(
            self.root, SimpleNamespace(machines={}), self.registry.path)
        return combined._children["host-fixture"], mode, manager

    def create(self, backend):
        return backend.create_project({"rootId": "work", "installationId": "host-fixture",
            "path": "example", "name": "Example", "mode": "create", "requestId": request_id()})["project"]

    def update(self, agent, path):
        revision = self.registry.read()["revision"]
        review = self.registry.prepare_agent_update("host-fixture", agent, str(path), revision)
        self.assertEqual(review["candidate"], self.pin(path))
        self.assertTrue(review["changed"])
        saved = self.registry.update_agent("host-fixture", agent, review["candidate"]["path"],
            review["candidate"]["sha256"], revision, confirmed=True)
        self.assertEqual(saved["revision"], revision + 1)
        return saved

    def test_missing_agent_keeps_dashboard_original_terminal_and_other_agent_usable(self):
        backend, _, _ = self.launch()
        project = self.create(backend)
        session = backend.start_session(project["id"], request_id(), agent="codex")["session"]
        owner = copy.deepcopy(backend._load()["sessions"][0]["owner"])
        self.tools["codex"].unlink()
        restarted, _, _ = self.launch()
        self.assertEqual(restarted.snapshot()["sessions"][0]["state"], "running")
        self.assertEqual(restarted._load()["sessions"][0]["owner"], owner)
        with self.assertRaises(BackendUnavailable):
            restarted.start_session(project["id"], request_id(), agent="codex")
        self.assertFalse(any(call[1][0] == "new-session" for call in restarted.calls))
        with patch("botainer_dashboard.host_backend.PtyAttachment", InertAttachment):
            attachment = restarted.attach(session["contextNamespace"], session["runtimeId"], 100, 30)
            self.assertEqual(attachment.write(b"inert input"), 11)
            attachment.close()
        self.assertEqual(restarted.start_session(project["id"], request_id(), agent="claude")["session"]["state"], "running")
        stopped = restarted.stop_session(session["contextNamespace"], session["runtimeId"], request_id())
        self.assertEqual(stopped["session"]["state"], "stopped")

    def test_agent_replaced_by_symlink_does_not_hide_existing_session_after_restart(self):
        backend, _, _ = self.launch()
        project = self.create(backend)
        session = backend.start_session(project["id"], request_id(), agent="codex")["session"]
        replacement = self.binary("codex-v2")
        self.tools["codex"].unlink()
        self.tools["codex"].symlink_to(replacement)
        restarted, _, _ = self.launch()
        self.assertEqual(restarted.snapshot()["sessions"][0]["state"], "running")
        with self.assertRaises(BackendUnavailable):
            restarted.start_session(project["id"], request_id(), agent="codex")
        self.assertFalse(any(call[1][0] == "new-session" for call in restarted.calls))
        with patch("botainer_dashboard.host_backend.PtyAttachment", InertAttachment):
            attachment = restarted.attach(session["contextNamespace"], session["runtimeId"], 100, 30)
            attachment.close()

    def test_two_reviewed_updates_preserve_history_owner_and_existing_browser_grant(self):
        backend, mode, manager = self.launch()
        project = self.create(backend)
        first = backend.start_session(project["id"], request_id(), agent="codex")["session"]
        original_record = copy.deepcopy(backend._load()["sessions"][0])
        original_root, original_namespace = backend.root, backend.namespace
        auth_root = authentication_profile(self.root, None, mode)
        access = LoopbackAccess.create(57432)
        store = PairingStore(origin=access.origin, token=access.token, state_path=auth_root / "browsers.json")
        grant = store.pair(access.token)
        store.close()
        latest = {}
        for agent in ("codex", "claude"):
            with self.subTest(agent=agent):
                self.tools[agent].unlink()
                latest[agent] = self.binary(agent + "-v2")
                self.update(agent, latest[agent])
                self.assertTrue(manager.snapshot()["pendingRestart"])
                backend, updated_mode, manager = self.launch()
                self.assertEqual(updated_mode, mode)
                self.assertEqual(backend.root, original_root)
                self.assertEqual(backend.namespace, original_namespace)
                self.assertEqual(backend.profile.identity_fingerprint, hashlib.sha256(self.original_bytes).hexdigest())
                self.assertEqual(backend.profile.original_path, self.original_path)
                self.assertFalse(manager.snapshot()["pendingRestart"])
                state = backend._load()
                self.assertEqual([row["id"] for row in state["projects"]], [project["id"]])
                self.assertEqual(state["sessions"][0], original_record)
                self.assertEqual(backend.snapshot()["sessions"][0]["state"], "running")
                self.assertEqual(backend.snapshot()["sessions"][0]["executable"], str(self.tools["codex"]))
                self.assertEqual(self.original_path.read_bytes(), self.original_bytes)
                self.assertEqual(authentication_profile(self.root, None, updated_mode), auth_root)
                renewed = PairingStore(origin=access.origin, token=LoopbackAccess.create(57432).token,
                                      state_path=auth_root / "browsers.json")
                try:
                    self.assertTrue(renewed.authenticated(grant.cookie, grant.bearer))
                    self.assertFalse(renewed.authenticated(grant.cookie, "x" * 43))
                finally:
                    renewed.close()
        for agent, path in latest.items():
            fresh = backend.start_session(project["id"], request_id(), agent=agent)["session"]
            self.assertEqual(fresh["executable"], str(path))
            record = next(s for s in backend._load()["sessions"] if s["id"] == fresh["runtimeId"])
            self.assertEqual(record["launchProfile"], backend.profile.fingerprint)
            self.assertEqual(record["sha256"], self.pin(path)["sha256"])
        backend.owner_pid = "9999"
        with self.assertRaises(BackendUnavailable):
            backend.attach(first["contextNamespace"], first["runtimeId"], 100, 30)
        with self.assertRaises(BackendUnavailable):
            backend.stop_session(first["contextNamespace"], first["runtimeId"], request_id())
        self.assertFalse(any(call[1][0] == "kill-pane" for call in backend.calls))

    def test_executable_swap_after_review_cannot_be_saved_as_approved_bytes(self):
        candidate = self.binary("codex-v2")
        review = self.registry.prepare_agent_update("host-fixture", "codex", str(candidate), 1)
        before = self.registry.path.read_bytes()
        candidate.write_text("changed after the review; never execute\n")
        with self.assertRaises(ConnectionRegistryError):
            self.registry.update_agent("host-fixture", "codex", str(candidate),
                review["candidate"]["sha256"], 1, confirmed=True)
        self.assertEqual(self.registry.path.read_bytes(), before)
        self.assertEqual(self.original_path.read_bytes(), self.original_bytes)

    def test_same_identity_cannot_expand_roots_environment_or_agent_privileges(self):
        saved = self.update("codex", self.binary("codex-v2"))
        selected = Path(saved["entries"][0]["profilePath"])
        updated = read_private_json(selected)
        wider = self.root / "additional-projects"
        wider.mkdir(mode=0o700)
        variants = [lambda p: p["project_roots"].update(extra=str(wider)),
                    lambda p: p["environment"].update(USER="different-account"),
                    lambda p: p["agents"]["claude"].update(external_tools="default"),
                    lambda p: p.update(default_agent="claude"),
                    lambda p: p.update(label="New scope presented as old")]
        for index, change in enumerate(variants):
            data = copy.deepcopy(updated)
            change(data)
            altered = selected.parent / ("altered-" + str(index) + ".json")
            write_private_json(altered, data)
            with self.subTest(index=index), self.assertRaisesRegex(BackendUnavailable, "profile-scope-changed"):
                HostProfile(altered)
        self.assertEqual(self.original_path.read_bytes(), self.original_bytes)

    def test_full_profile_scope_change_gets_new_identity_and_no_old_browser_grant(self):
        original, mode, _ = self.launch()
        access = LoopbackAccess.create(57432)
        auth_root = authentication_profile(self.root, None, mode)
        store = PairingStore(origin=access.origin, token=access.token, state_path=auth_root / "browsers.json")
        grant = store.pair(access.token)
        store.close()
        new_scope = copy.deepcopy(self.original)
        new_scope["project_roots"]["additional"] = str(self.root)
        path = self.root / "separately-reviewed-scope.json"
        write_private_json(path, new_scope)
        combined, changed_mode = select_installed_backend(self.root, None, (), host_paths=(path,))
        changed = combined._children["host-fixture"]
        self.assertNotEqual(changed_mode, mode)
        self.assertNotEqual(changed.namespace, original.namespace)
        changed_auth = authentication_profile(self.root, None, changed_mode)
        self.assertNotEqual(changed_auth, auth_root)
        store = PairingStore(origin=access.origin, token=LoopbackAccess.create(57432).token,
                             state_path=changed_auth / "browsers.json")
        try:
            self.assertFalse(store.authenticated(grant.cookie, grant.bearer))
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
