"""Workspace routing/isolation with deterministic children; no runtime calls."""
import copy
import threading
import time
import types
import unittest
import uuid
from unittest.mock import patch

from botainer_dashboard.backend_errors import BackendUnavailable
from botainer_dashboard.combined_backend import CombinedBackend as AsyncCombinedBackend


class CombinedBackend(AsyncCombinedBackend):
    """Deterministic fake observation; production behavior is tested separately."""
    def snapshot(self):
        self.refresh()
        return super().snapshot()


def inventory(project, namespace, *, cluster=False):
    return {"mode": "cluster-workspace" if cluster else "local-workspace", "notice": "Selected runtime policy",
        "capabilities": {"startSession": True, "attachTerminal": not cluster, "stopSession": not cluster,
            "filesRead": True, "configRead": True, "configWrite": not cluster,
            "projectCreate": not cluster, "projectRegister": not cluster,
            "dashboardConfigRead": not cluster, "dashboardConfigWrite": not cluster},
        "projects": [{"id": project, "name": project}],
        "sessions": [{"projectId": project, "contextNamespace": namespace, "runtimeId": "same-native-runtime",
                      "state": "running", "capabilities": {"attachTerminal": True, "stopSession": True}}],
        "installations": [{"id": "same-display-installation", "name": "Configured install"}],
        **({"clusterSettings": {"site": project, "profilePath": "private-profile.json"},
            "connectionStatus": "available"} if cluster else {})}


class Child:
    def __init__(self, project, namespace, *, cluster=False):
        self.value = inventory(project, namespace, cluster=cluster)
        self.profile = types.SimpleNamespace(label=project + " label")
        self.calls = []
        self.failure = None
        self.replies = {}

    def snapshot(self):
        if self.failure:
            raise self.failure
        return copy.deepcopy(self.value)

    def __getattr__(self, name):
        def invoke(*args):
            self.calls.append((name, copy.deepcopy(args)))
            if name in self.replies:
                return copy.deepcopy(self.replies[name])
            if name in ("start_session", "stop_session"):
                return {"session": copy.deepcopy(self.value["sessions"][0])}
            if name == "workspace_metadata":
                return {"roots": [{"id": "root", "path": "/trusted/projects"}],
                        "installations": [{"id": "shell", "initialText": "reviewed template"}],
                        "limits": {"textBytes": 65536}, "notice": "Explicit roots only"}
            if name == "create_project":
                return {"project": {"id": "new-local-project", "name": args[0]["name"]}}
            return {"child": self.value["projects"][0]["id"], "method": name}
        return invoke


class CombinedBackendTests(unittest.TestCase):
    def setUp(self):
        self.local = Child("local-project", "local:namespace")
        self.research = Child("research-project", "cluster:research", cluster=True)
        self.other = Child("other-project", "cluster:other", cluster=True)
        self.backend = CombinedBackend(self.local, {"research": self.research, "other": self.other})

    def test_terminal_history_same_runtime_id_stays_in_advertising_namespace(self):
        for child in (self.local, self.research, self.other):
            child.value["sessions"][0]["capabilities"]["readTerminalHistory"] = True
        self.research.replies["read_terminal_history"] = {"text": "Selected terminal only", "kind": "terminal-snapshot"}
        result = self.backend.read_terminal_history("cluster:research", "same-native-runtime")
        self.assertEqual(result, self.research.replies["read_terminal_history"])
        self.assertEqual(self.research.calls, [("read_terminal_history", ("cluster:research", "same-native-runtime"))])
        self.assertEqual(self.local.calls, [])
        self.assertEqual(self.other.calls, [])
        self.research.read_terminal_history = None
        with self.assertRaisesRegex(BackendUnavailable, "combined-operation-unavailable"):
            self.backend.read_terminal_history("cluster:research", "same-native-runtime")
        self.assertEqual(self.local.calls, [])
        self.assertEqual(self.other.calls, [])

    def native_folder_backend(self, backend_class=CombinedBackend):
        self.local.value["mode"] = "ordinary-local"
        self.local.value["projects"][0].update(path="/trusted/projects/team/existing", name="Existing work",
            unavailableReason="Outside the Botainer control root", capabilities={"startSession": False})
        self.research.value["projects"][0]["path"] = "/trusted/projects/team/existing"
        host = Child("host-project", "host:profile")
        host.execution_kind = "host"
        host.profile = types.SimpleNamespace(id="native-profile", label="This computer · Host agents",
            data={"project_roots": {"all": "/trusted/projects", "team": "/trusted/projects/team"}})
        host.value.update(mode="host-agents", executionKind="host", connectionStatus="available")
        host.value["capabilities"]["projectOpen"] = True
        host.value["projects"][0]["executionKind"] = "host"
        host.replies["workspace_metadata"] = {
            "roots": [{"id": "all", "path": "/trusted/projects", "label": "Work"},
                      {"id": "team", "path": "/trusted/projects/team", "label": "Team"}],
            "installations": [{"id": "native-profile"}]}
        host.replies["create_project"] = {"project": {"id": "opened-host-project", "name": "Existing work",
            "path": "/trusted/projects/team/existing", "executionKind": "host",
            "capabilities": {"startSession": True, "configRead": False, "configWrite": False}}}
        backend = backend_class(self.local, {"research": self.research, "host": host})
        backend.refresh()
        return backend, host

    def test_native_folder_choices_are_local_only_and_do_not_call_metadata_or_start(self):
        backend, host = self.native_folder_backend()
        snapshot = backend.snapshot()
        project = next(item for item in snapshot["projects"] if item["id"] == "local-project")
        self.assertFalse(project["capabilities"]["startSession"])
        self.assertEqual(project["nativeHostWorkspaces"], [{"id": "host", "label": "This computer · Host agents"}])
        self.assertTrue(all("nativeHostWorkspaces" not in item for item in snapshot["projects"]
                            if item["workspaceId"] != "local"))
        self.assertFalse(self.local.calls or self.research.calls or host.calls)
        self.assertNotIn("nativeHostWorkspaces", backend._cache["local"]["projects"][0])

    def test_native_folder_open_uses_known_path_longest_root_and_publishes_host_identity(self):
        backend, host = self.native_folder_backend()
        request = str(uuid.uuid4())
        source = copy.deepcopy(self.local.value)
        generation = backend._generation["host"]
        result = backend.open_host_project("local-project", "host", request)
        self.assertEqual(host.calls, [("workspace_metadata", ()), ("create_project", ({
            "mode": "open", "rootId": "team", "installationId": "native-profile", "path": "existing",
            "name": "Existing work", "requestId": request},))])
        self.assertFalse(self.local.calls or self.research.calls)
        self.assertEqual(source, self.local.value)
        self.assertEqual(result["project"]["workspaceId"], "host")
        self.assertEqual(result["project"]["executionKind"], "host")
        self.assertEqual(backend._project_claims["opened-host-project"], "host")
        self.assertEqual(backend._generation["host"], generation + 1)
        self.assertNotIn("host", backend._attempted)
        self.assertTrue(any(item["id"] == "opened-host-project" for item in backend._cache["host"]["projects"]))
        self.assertNotIn("session", result)

    def test_native_folder_remote_same_path_and_host_source_are_refused(self):
        backend, host = self.native_folder_backend()
        for source in ("research-project", "host-project", "missing-project"):
            with self.subTest(source=source), self.assertRaisesRegex(BackendUnavailable, "source-local-required"):
                backend.open_host_project(source, "host", str(uuid.uuid4()))
        self.assertFalse(self.local.calls or self.research.calls or host.calls)

    def test_native_folder_target_must_be_explicit_host_profile_with_open_capability(self):
        backend, host = self.native_folder_backend()
        for target in ("local", "research", "missing"):
            with self.subTest(target=target), self.assertRaisesRegex(BackendUnavailable, "target-unavailable"):
                backend.open_host_project("local-project", target, str(uuid.uuid4()))
        host.value["capabilities"]["projectOpen"] = False
        with self.assertRaisesRegex(BackendUnavailable, "target-unavailable"):
            backend.open_host_project("local-project", "host", str(uuid.uuid4()))
        self.assertFalse(host.calls)
        self.assertNotIn("nativeHostWorkspaces", backend.snapshot()["projects"][0])

    def test_native_folder_source_workspace_failure_or_checking_disables_shortcut(self):
        for state in ("failure", "checking", "prepared"):
            with self.subTest(state=state):
                backend, host = self.native_folder_backend()
                if state == "failure":
                    self.local.failure = BackendUnavailable("ordinary-observation-failed")
                elif state == "checking":
                    self.local.value["connectionStatus"] = "checking"
                else:
                    self.local.value["mode"] = "local-workspace"
                with self.assertRaisesRegex(BackendUnavailable, "source-unavailable"):
                    backend.open_host_project("local-project", "host", str(uuid.uuid4()))
                self.assertNotIn("nativeHostWorkspaces", backend.snapshot()["projects"][0])
                self.assertFalse(host.calls)
                self.local.failure = None
                self.local.value.pop("connectionStatus", None)

    def test_native_folder_expired_source_or_host_observation_refuses_without_waiting(self):
        for workspace_id in ("local", "host"):
            with self.subTest(workspace_id=workspace_id):
                backend, host = self.native_folder_backend(AsyncCombinedBackend)
                backend._observed[workspace_id] = time.monotonic() - 16
                with self.assertRaisesRegex(BackendUnavailable,
                        "source-unavailable" if workspace_id == "local" else "target-unavailable"):
                    backend.open_host_project("local-project", "host", str(uuid.uuid4()))
                self.assertFalse(host.calls)
                self.assertNotIn("nativeHostWorkspaces", backend.snapshot()["projects"][0])

    def test_native_folder_outside_roots_or_invalid_relative_path_never_registers(self):
        paths = ("/other/work", "/trusted/projects-elsewhere/work", "/trusted/projects",
                 "/trusted/projects/../secret", "/trusted/projects/.hidden/work",
                 "/trusted/projects/team//work", "/trusted/projects/team/./work")
        for path in paths:
            with self.subTest(path=path):
                backend, host = self.native_folder_backend()
                self.local.value["projects"][0]["path"] = path
                with self.assertRaisesRegex(BackendUnavailable, "project-outside-roots"):
                    backend.open_host_project("local-project", "host", str(uuid.uuid4()))
                self.assertEqual(host.calls, [("workspace_metadata", ())])
                self.assertNotIn("nativeHostWorkspaces", backend.snapshot()["projects"][0])

    def test_native_folder_invalid_request_or_changed_host_metadata_never_registers(self):
        backend, host = self.native_folder_backend()
        with self.assertRaisesRegex(BackendUnavailable, "request-id-invalid"):
            backend.open_host_project("local-project", "host", "not-a-uuid")
        self.assertFalse(host.calls)
        host.replies["workspace_metadata"]["roots"].append({"id": "extra", "path": "/other"})
        with self.assertRaisesRegex(BackendUnavailable, "target-unavailable"):
            backend.open_host_project("local-project", "host", str(uuid.uuid4()))
        self.assertEqual(host.calls, [("workspace_metadata", ())])

    def test_native_folder_source_changed_during_metadata_lookup_is_refused(self):
        backend, host = self.native_folder_backend()
        metadata = host.replies["workspace_metadata"]
        def changing_metadata():
            with backend._lock:
                backend._cache["local"]["projects"][0]["path"] = "/trusted/projects/team/other"
            return metadata
        host.workspace_metadata = changing_metadata
        with self.assertRaisesRegex(BackendUnavailable, "source-changed"):
            backend.open_host_project("local-project", "host", str(uuid.uuid4()))
        self.assertFalse(host.calls)

    def test_native_folder_reply_cannot_introduce_session_or_different_folder(self):
        for changed in ({"session": {}}, {"project": {"id": "wrong", "path": "/other", "executionKind": "host"}}):
            with self.subTest(changed=changed):
                backend, host = self.native_folder_backend()
                host.replies["create_project"].update(changed)
                with self.assertRaisesRegex(BackendUnavailable, "reply-invalid"):
                    backend.open_host_project("local-project", "host", str(uuid.uuid4()))
                self.assertNotIn("opened-host-project", backend._project_claims)

    def test_host_identity_survives_unavailability_and_does_not_route_to_container(self):
        host = Child('host-project', 'host:profile')
        host.execution_kind = 'host'
        host.value.update(executionKind='host', mode='host-agents', kind='local',
                          availableAgents=[{'id': 'codex', 'label': 'Codex', 'executable': '/trusted/codex'}],
                          defaultAgent='codex')
        host.value['projects'][0].update(executionKind='host', name='Same visible name')
        host.value['sessions'][0]['executionKind'] = 'host'
        backend = CombinedBackend(self.local, {'host': host})
        snapshot = backend.snapshot()
        location = next(x for x in snapshot['workspaces'] if x['id'] == 'host')
        self.assertEqual(location['kind'], 'local')
        self.assertEqual(location['executionKind'], 'host')
        self.assertEqual(location['availableAgents'][0]['id'], 'codex')
        backend.attach('host:profile', 'same-native-runtime', 80, 24)
        self.assertEqual(host.calls[-1][0], 'attach')
        self.assertEqual(self.local.calls, [])
        host.failure = BackendUnavailable('host-owner-unverified')
        snapshot = backend.snapshot()
        location = next(x for x in snapshot['workspaces'] if x['id'] == 'host')
        self.assertEqual(location['executionKind'], 'host')
        self.assertEqual(location['kind'], 'local')
        session = next(x for x in snapshot['sessions'] if x.get('executionKind') == 'host')
        self.assertEqual(session['state'], 'unknown')
        self.assertFalse(session['capabilities']['attachTerminal'])
        with self.assertRaises(BackendUnavailable):
            backend.attach('host:profile', 'same-native-runtime', 80, 24)
        self.assertEqual(self.local.calls, [])

    def test_snapshot_keeps_native_targets_and_materializes_workspace_capabilities(self):
        snap = self.backend.snapshot()
        self.assertEqual([item["id"] for item in snap["workspaces"]], ["local", "research", "other"])
        self.assertEqual([item["workspaceId"] for item in snap["projects"]], ["local", "research", "other"])
        self.assertEqual([item["runtimeId"] for item in snap["sessions"]], ["same-native-runtime"] * 3)
        self.assertEqual([item["contextNamespace"] for item in snap["sessions"]],
                         ["local:namespace", "cluster:research", "cluster:other"])
        self.assertEqual(snap["capabilities"], {"dashboardConfigRead": True, "dashboardConfigWrite": True})
        self.assertFalse(snap["projects"][1]["capabilities"]["configWrite"])
        self.assertTrue(snap["projects"][0]["capabilities"]["configWrite"])
        self.assertTrue(snap["sessions"][1]["capabilities"]["attachTerminal"])
        self.assertNotIn("clusterSettings", snap["workspaces"][0])
        self.assertEqual(snap["workspaces"][1]["clusterSettings"]["site"], "research-project")
        snap["projects"][0]["name"] = "caller mutation"
        self.assertEqual(self.backend.snapshot()["projects"][0]["name"], "local-project")
        self.assertNotIn("workspaceId", self.local.value["projects"][0])

    def test_project_reads_and_start_route_to_exact_selected_child(self):
        self.backend.list_files("research-project", "folder")
        self.backend.read_file("other-project", "document.txt")
        self.backend.read_config("research-project")
        result = self.backend.start_session("research-project", "request-id")
        self.assertEqual(result["session"]["workspaceId"], "research")
        self.assertEqual(self.research.calls, [("list_files", ("research-project", "folder")),
            ("read_config", ("research-project",)), ("start_session", ("research-project", "request-id"))])
        self.assertEqual(self.other.calls, [("read_file", ("other-project", "document.txt"))])
        self.assertEqual(self.local.calls, [])

    def test_terminal_and_stop_use_exact_namespace_runtime_pair_without_prefixes(self):
        self.backend.attach("cluster:other", "same-native-runtime", 132, 40)
        result = self.backend.stop_session("cluster:research", "same-native-runtime", "stop-request")
        self.assertEqual(result["session"]["workspaceId"], "research")
        self.assertEqual(self.other.calls, [("attach", ("cluster:other", "same-native-runtime", 132, 40))])
        self.assertEqual(self.research.calls, [("stop_session", ("cluster:research", "same-native-runtime", "stop-request"))])
        self.assertEqual(self.local.calls, [])

    def test_unknown_project_namespace_or_runtime_never_guesses_a_child(self):
        cases = (("read_file", ("missing-project", "notes.txt")),
                 ("start_session", ("missing-project", "request")),
                 ("attach", ("cluster:missing", "same-native-runtime", 80, 24)),
                 ("stop_session", ("cluster:research", "missing-runtime", "request")))
        for method, args in cases:
            with self.subTest(method=method), self.assertRaises(BackendUnavailable):
                getattr(self.backend, method)(*args)
        self.assertFalse(self.local.calls or self.research.calls or self.other.calls)

    def test_orphan_stop_requires_exact_local_recovery_scope_and_dedicated_method(self):
        row = self.local.value["sessions"][0]
        with self.assertRaises(BackendUnavailable):
            self.backend.stop_orphan_session("local:namespace", "same-native-runtime", "request")
        self.assertEqual(self.local.calls, [])
        row.update(stopMode="orphan-container", stopScope="container")
        self.local.replies["stop_orphan_session"] = {"session": copy.deepcopy(row), "terminationConfirmed": True}
        result = self.backend.stop_orphan_session("local:namespace", "same-native-runtime", "request")
        self.assertTrue(result["terminationConfirmed"])
        self.assertEqual(self.local.calls, [("stop_orphan_session", ("local:namespace", "same-native-runtime", "request"))])
        self.assertEqual(self.research.calls, [])
        for update in [{"stopScope": "allocation"}, {"stopMode": "normal"},
                       {"capabilities": {"stopSession": False}}]:
            with self.subTest(update=update):
                self.local.value["sessions"][0] = {**row, **update}
                with self.assertRaises(BackendUnavailable):
                    self.backend.stop_orphan_session("local:namespace", "same-native-runtime", "request2")
        self.assertEqual(len(self.local.calls), 1)
        self.local.value["sessions"][0] = row
        self.local.stop_orphan_session = None
        with self.assertRaises(BackendUnavailable):
            self.backend.stop_orphan_session("local:namespace", "same-native-runtime", "request3")
        self.assertEqual(len(self.local.calls), 1)

    def test_cluster_config_cannot_inherit_local_write_permission(self):
        for method in ("validate_config", "save_config"):
            with self.subTest(method=method), self.assertRaises(BackendUnavailable):
                getattr(self.backend, method)("research-project", "text", "revision")
        self.backend.save_config("local-project", "text", "revision")
        self.assertEqual(self.local.calls, [("save_config", ("local-project", "text", "revision"))])
        self.assertEqual(self.research.calls, [])

    def test_child_failure_preserves_unknown_stale_sessions_and_other_workspace_controls(self):
        self.backend.snapshot()
        self.research.failure = RuntimeError("private remote path and diagnostic must not leak")
        snap = self.backend.snapshot()
        research = next(item for item in snap["sessions"] if item["workspaceId"] == "research")
        self.assertEqual((research["state"], research["lastKnownState"]), ("unknown", "running"))
        self.assertTrue(research["stale"])
        self.assertFalse(any(research["capabilities"].values()))
        self.assertNotIn("private remote path", str(snap))
        self.assertEqual(snap["workspaces"][1]["status"], "unavailable")
        self.assertEqual(snap["workspaces"][0]["status"], "available")
        with self.assertRaises(BackendUnavailable):
            self.backend.stop_session("cluster:research", "same-native-runtime", "request")
        self.backend.attach("local:namespace", "same-native-runtime", 80, 24)
        self.assertEqual(self.research.calls, [])
        self.research.failure = None
        recovered = self.backend.snapshot()
        self.assertEqual(recovered["sessions"][1]["state"], "running")
        self.assertNotIn("stale", recovered["sessions"][1])

    def test_explicit_child_connection_failure_disables_every_entity_action(self):
        self.backend.snapshot()
        self.research.value["connectionStatus"] = "checking"
        snap = self.backend.snapshot()
        self.assertEqual(snap["workspaces"][1]["status"], "checking")
        self.assertEqual(snap["sessions"][1]["state"], "unknown")
        self.assertFalse(any(snap["projects"][1]["capabilities"].values()))
        with self.assertRaises(BackendUnavailable):
            self.backend.read_config("research-project")
        self.assertEqual(self.research.calls, [])

    def test_installation_change_hint_survives_failed_and_checking_observations_until_recovery(self):
        for reason in ("ordinary-source-changed-requalification-required", "ordinary-source-module-set-changed",
                       "ordinary-installation-changed-requalification-required",
                       "ordinary-installed-layout-changed-requalification-required"):
            with self.subTest(reason=reason):
                before = self.backend.snapshot()["sessions"][0]
                self.local.value.update(connectionStatus="unavailable", unavailableReason=reason)
                failed = self.backend.snapshot()
                diagnostic = failed["workspaces"][0]["connectionDiagnostic"]
                self.assertEqual(diagnostic["code"], "botainer-installation-changed")
                self.assertIn("saved verification", diagnostic["message"])
                self.assertIn("Settings → Machines", diagnostic["recovery"])
                self.assertIn("restarting the dashboard alone will not repair", diagnostic["recovery"])
                self.assertEqual(failed["workspaces"][0]["status"], "unavailable")
                self.assertEqual(failed["workspaces"][0]["unavailableReason"], "combined-workspace-unavailable")
                self.assertEqual(failed["workspaces"][1]["status"], "available")
                self.assertNotIn("connectionDiagnostic", failed["workspaces"][1])
                session = failed["sessions"][0]
                self.assertEqual((session["state"], session["lastKnownState"]), ("unknown", "running"))
                self.assertEqual(session["lastKnownAt"], before["lastKnownAt"])
                self.assertTrue(session["stale"])
                self.assertFalse(any(session["capabilities"].values()))
                self.assertFalse(any(failed["projects"][0]["capabilities"].values()))
                self.assertEqual(self.backend.snapshot()["workspaces"][0]["connectionDiagnostic"], diagnostic)
                self.local.value.update(connectionStatus="checking")
                self.local.value.pop("unavailableReason")
                checking = self.backend.snapshot()
                self.assertEqual(checking["workspaces"][0]["connectionDiagnostic"], diagnostic)
                self.assertEqual(checking["workspaces"][0]["status"], "unavailable")
                self.local.value["connectionStatus"] = "available"
                recovered = self.backend.snapshot()
                self.assertNotIn("connectionDiagnostic", recovered["workspaces"][0])
                self.assertEqual(recovered["sessions"][0]["state"], "running")
                self.assertTrue(recovered["sessions"][0]["capabilities"]["attachTerminal"])

    def test_known_helper_and_profile_exceptions_have_fixed_restart_hint(self):
        self.backend.snapshot()
        for reason in ("ordinary-dashboard-helper-changed-restart-required", "ordinary-profile-changed-restart-required",
                       "remote-helper-changed-restart-required", "remote-profile-changed-restart-required",
                       "cluster-helper-changed-restart-required", "cluster-profile-changed-restart-required",
                       "validator-source-changed-restart-required", "host-helper-changed-restart-required",
                       "host-profile-changed-restart-required"):
            with self.subTest(reason=reason):
                self.local.failure = BackendUnavailable(reason)
                failed = self.backend.snapshot()
                diagnostic = failed["workspaces"][0]["connectionDiagnostic"]
                self.assertEqual(diagnostic["code"], "dashboard-restart-required")
                self.assertIn("Restart the dashboard", diagnostic["recovery"])
                self.assertEqual(failed["sessions"][0]["state"], "unknown")
                self.assertFalse(any(failed["sessions"][0]["capabilities"].values()))
                self.assertEqual(failed["workspaces"][1]["status"], "available")
        self.local.failure = None
        self.assertNotIn("connectionDiagnostic", self.backend.snapshot()["workspaces"][0])

    def test_connection_hint_never_echoes_unknown_codes_child_text_or_forged_diagnostic(self):
        private_text = "PRIVATE-DIAGNOSTIC /private/project?credential=example"
        self.local.failure = RuntimeError(private_text)
        failed = self.backend.snapshot()
        generic = failed["workspaces"][0]["connectionDiagnostic"]
        self.assertEqual(generic["code"], "workspace-check-failed")
        self.assertNotIn(private_text, str(failed))
        self.local.failure = BackendUnavailable("ordinary-source-changed-requalification-required-unrecognized")
        self.assertEqual(self.backend.snapshot()["workspaces"][0]["connectionDiagnostic"], generic)
        self.local.failure = None
        self.local.value.update(connectionStatus="unavailable", connectionDiagnostic={
            "code": "botainer-installation-changed", "message": private_text, "recovery": private_text})
        for reason in (private_text, "ordinary-source-changed-requalification-required " + private_text,
                       "ordinary-source-changed-requalification-required" + "x" * 200,
                       {"code": "ordinary-source-changed-requalification-required"}, [private_text]):
            with self.subTest(reason=reason):
                self.local.value.update(unavailableReason=reason, error=reason)
                snapshot = self.backend.snapshot()
                self.assertEqual(snapshot["workspaces"][0]["connectionDiagnostic"], generic)
                self.assertNotIn(private_text, str(snapshot))
        self.local.value.update(connectionStatus="available")
        self.local.value.pop("unavailableReason")
        self.local.value.pop("error")
        self.assertNotIn("connectionDiagnostic", self.backend.snapshot()["workspaces"][0])

    def test_error_field_and_remote_connection_settings_diagnostics_are_preserved_separately(self):
        self.backend.snapshot()
        self.research.value.update(connectionStatus="unavailable", error="remote-profile-changed-restart-required")
        remote_diagnostic = {"code": "ssh-authentication-required", "message": "SSH could not finish authentication.",
                             "recovery": "Complete login in your system terminal."}
        self.research.value["clusterSettings"]["connectionDiagnostic"] = remote_diagnostic
        workspace = self.backend.snapshot()["workspaces"][1]
        self.assertEqual(workspace["connectionDiagnostic"]["code"], "dashboard-restart-required")
        self.assertEqual(workspace["clusterSettings"]["connectionDiagnostic"], remote_diagnostic)
        workspace["connectionDiagnostic"]["message"] = "browser mutation"
        self.assertNotIn("browser mutation", str(self.backend.snapshot()))

    def test_project_control_restrictions_preserve_observed_runtime_states_but_disable_all_controls(self):
        markers = ("outside-project-roots", "legacy-project-identity", "installation-relink-required",
                   "project-registration-unavailable")
        for marker in markers:
            with self.subTest(marker=marker):
                self.local.value["projects"][0].update(controlRestriction=marker,
                    unavailableReason="Controls are not enabled for this folder")
                original = copy.deepcopy(self.local.value["sessions"][0])
                self.local.value["sessions"] = [dict(original, runtimeId=state + "-runtime", state=state)
                    for state in ("running", "stopped", "failed", "unknown")]
                snapshot = self.backend.snapshot()
                project = snapshot["projects"][0]
                sessions = [session for session in snapshot["sessions"] if session["workspaceId"] == "local"]
                self.assertNotIn("stale", project)
                self.assertFalse(any(project["capabilities"].values()))
                self.assertEqual([s["state"] for s in sessions], ["running", "stopped", "failed", "unknown"])
                for session in sessions:
                    self.assertNotIn("stale", session)
                    self.assertFalse(any(session["capabilities"].values()))
                    self.assertEqual(session["controlRestriction"], marker)
                for method, args in (("attach", ("local:namespace", "running-runtime", 80, 24)),
                        ("stop_session", ("local:namespace", "running-runtime", "request")),
                        ("start_session", ("local-project", "request")), ("read_config", ("local-project",))):
                    with self.assertRaises(BackendUnavailable):
                        getattr(self.backend, method)(*args)
                self.assertFalse(self.local.calls)

    def test_control_restrictions_do_not_mask_actual_workspace_or_project_observation_failure(self):
        project = self.local.value["projects"][0]
        project.update(controlRestriction="outside-project-roots", unavailableReason="Folder control restricted")
        self.assertEqual(self.backend.snapshot()["sessions"][0]["state"], "running")
        self.local.failure = BackendUnavailable("observation-failed")
        failed = self.backend.snapshot()["sessions"][0]
        self.assertEqual(failed["state"], "unknown")
        self.assertEqual(failed["lastKnownState"], "running")
        self.assertTrue(failed["stale"])
        self.local.failure = None
        self.assertEqual(self.backend.snapshot()["sessions"][0]["state"], "running")
        project["stale"] = True
        self.assertEqual(self.backend.snapshot()["sessions"][0]["state"], "unknown")
        project.pop("stale")
        project["controlRestriction"] = "unrecognized-restriction"
        self.assertEqual(self.backend.snapshot()["sessions"][0]["state"], "unknown")

    def test_missing_session_under_read_only_project_is_retained_unknown_without_inventing_end(self):
        self.local.value["projects"][0].update(controlRestriction="legacy-project-identity",
                                             unavailableReason="Historical project identity")
        self.local.value["sessions"][0]["state"] = "stopped"
        self.assertEqual(self.backend.snapshot()["sessions"][0]["state"], "stopped")
        self.local.value["sessions"] = []
        snapshot = self.backend.snapshot()
        missing = next(session for session in snapshot["sessions"] if session["workspaceId"] == "local")
        self.assertEqual(missing["state"], "unknown")
        self.assertEqual(missing["lastKnownState"], "stopped")
        self.assertTrue(missing["stale"])
        self.assertFalse(any(missing["capabilities"].values()))

    def test_session_own_unknown_reason_or_staleness_survives_project_control_restriction(self):
        self.local.value["projects"][0].update(controlRestriction="outside-project-roots",
                                             unavailableReason="Folder control restricted")
        session = self.local.value["sessions"][0]
        session.update(state="unknown", unavailableReason="Exact runtime owner was not verified")
        observed = self.backend.snapshot()["sessions"][0]
        self.assertEqual(observed["state"], "unknown")
        self.assertEqual(observed["unavailableReason"], "Exact runtime owner was not verified")
        session.update(state="running", stale=True)
        stale = self.backend.snapshot()["sessions"][0]
        self.assertEqual(stale["state"], "unknown")
        self.assertTrue(stale["stale"])

    def test_snapshot_stale_flag_cannot_be_reinterpreted_as_only_control_restriction(self):
        self.local.value["projects"][0].update(controlRestriction="outside-project-roots",
                                             unavailableReason="Folder control restricted")
        self.local.value["stale"] = True
        self.assertEqual(self.backend.snapshot()["sessions"][0]["state"], "unknown")

    def test_actual_ordinary_adapter_dto_preserves_history_under_folder_restrictions(self):
        # Reuse the ordinary adapter's inert filesystem/profile fixture. Its
        # real snapshot performs registration checks; only runtime inventory
        # is supplied by a deterministic runner, with no native CLI execution.
        from test_ordinary_local import OrdinaryFixture, REQUEST2
        cases = (("outside-without-metadata", "project-registration-unavailable"), ("legacy", "legacy-project-identity"),
                 ("mismatched-id", "project-registration-unavailable"), ("runtime-uncertain", None))
        for case, marker in cases:
            with self.subTest(case=case):
                fixture = OrdinaryFixture()
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                project = fixture.raw["projects"][0]
                if case in {"outside-without-metadata", "runtime-uncertain"}:
                    outside = fixture.root / "outside"
                    outside.mkdir()
                    project["last_path"] = str(outside)
                elif case == "legacy":
                    project["uuid"] = "0123456789ab"
                else:
                    (fixture.project / ".botainer/project-id").write_text(REQUEST2)
                if case == "runtime-uncertain":
                    project["runtime_unverified"] = True
                running = fixture.running() | {"project_uuid": project["uuid"]}
                ended = {key: value for key, value in running.items() if key != "binding"}
                ended.update(session_id="c" * 16, state="stopped", ended_at="recorded-end")
                fixture.raw["sessions"] = [running, ended]
                backend = CombinedBackend(fixture.backend)
                snapshot = backend.snapshot()
                self.assertEqual(snapshot["projects"][0].get("controlRestriction"), marker)
                self.assertEqual([session["state"] for session in snapshot["sessions"]],
                                 ["running", "stopped"] if marker else ["unknown", "unknown"])
                self.assertTrue(all(not any(session["capabilities"].values()) for session in snapshot["sessions"]))
                self.assertEqual(fixture.consoles, [])
                self.assertTrue(all(request["action"] == "inventory" for request in fixture.requests))

    def test_partial_project_failure_retains_dropped_sessions_without_disabling_healthy_project(self):
        second = {"id": "second-local-project", "name": "Second"}
        self.local.value["projects"].append(second)
        self.backend.snapshot()
        self.local.value["projects"][0]["unavailableReason"] = "runtime-inspection-failed"
        self.local.value["sessions"] = []
        snap = self.backend.snapshot()
        stale = next(item for item in snap["sessions"] if item["projectId"] == "local-project")
        self.assertEqual(stale["state"], "unknown")
        self.assertEqual(stale["lastKnownState"], "running")
        self.assertEqual(stale["unavailableReason"], "runtime-inspection-failed")
        self.assertEqual(snap["projects"][0]["unavailableReason"], "runtime-inspection-failed")
        self.assertFalse(stale["capabilities"]["attachTerminal"])
        self.assertEqual(snap["workspaces"][0]["status"], "available")
        self.backend.read_config("second-local-project")
        with self.assertRaises(BackendUnavailable):
            self.backend.read_config("local-project")
        self.assertEqual(self.local.calls, [("read_config", ("second-local-project",))])

    def test_all_project_failures_do_not_disable_local_setup_or_dashboard_settings(self):
        self.backend.snapshot()
        self.local.value["projects"][0]["unavailableReason"] = "runtime-inspection-failed"
        self.local.value["sessions"] = []
        snap = self.backend.snapshot()
        self.assertEqual(snap["workspaces"][0]["status"], "available")
        self.assertTrue(snap["workspaces"][0]["capabilities"]["projectCreate"])
        self.assertTrue(snap["capabilities"]["dashboardConfigWrite"])
        self.assertFalse(snap["projects"][0]["capabilities"]["startSession"])
        self.assertEqual(snap["sessions"][0]["state"], "unknown")

    def wait_observers(self, backend):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with backend._lock:
                if not backend._inflight:
                    return
            time.sleep(.001)
        self.fail("offline observation workers did not complete")

    def test_initial_snapshot_reports_checking_without_waiting_or_enabling_controls(self):
        release = threading.Event()
        original = self.local.snapshot
        def initial_observation():
            release.wait(3)
            return original()
        self.local.snapshot = initial_observation
        backend = AsyncCombinedBackend(self.local)
        try:
            started = time.monotonic()
            snap = backend.snapshot()
            self.assertLess(time.monotonic() - started, .5)
            self.assertEqual(snap["workspaces"][0]["status"], "checking")
            self.assertEqual(snap["workspaces"][0]["notice"], "Checking workspace connection.")
            self.assertFalse(any(snap["workspaces"][0]["capabilities"].values()))
            self.assertFalse(any(snap["capabilities"].values()))
            self.assertEqual(snap["projects"], [])
            self.assertEqual(snap["sessions"], [])
        finally:
            release.set()
            self.wait_observers(backend)

    def test_first_check_after_idle_is_checking_but_stale_controls_remain_fenced(self):
        backend = AsyncCombinedBackend(self.local)
        backend.refresh()
        before = copy.deepcopy(backend._cache["local"]["sessions"][0])
        baseline = backend._observed["local"]
        entered, release = threading.Event(), threading.Event()
        original = self.local.snapshot
        calls = []
        def blocked():
            calls.append("observation")
            entered.set()
            release.wait(3)
            return original()
        self.local.snapshot = blocked
        clock = [baseline + 3600]
        try:
            with patch("botainer_dashboard.combined_backend.time.monotonic", side_effect=lambda: clock[0]):
                first = backend.snapshot()
                self.assertTrue(entered.wait(1))
                for age in (0, 15):
                    clock[0] = baseline + 3600 + age
                    pending = first if age == 0 else backend.snapshot()
                    workspace, session = pending["workspaces"][0], pending["sessions"][0]
                    self.assertEqual((workspace["status"], workspace["observationStatus"]), ("checking", "checking"))
                    self.assertIn("last known", workspace["notice"])
                    self.assertNotIn("connectionDiagnostic", workspace)
                    self.assertEqual(workspace["lastObservationAt"], backend._reported_at["local"])
                    self.assertEqual((session["state"], session["lastKnownState"]), ("unknown", "running"))
                    self.assertEqual(session["lastKnownAt"], before["lastKnownAt"])
                    self.assertTrue(session["stale"])
                    self.assertFalse(any(session["capabilities"].values()))
                    self.assertFalse(any(workspace["capabilities"].values()))
                    self.assertFalse(any(pending["projects"][0]["capabilities"].values()))
                    with self.assertRaisesRegex(BackendUnavailable, "combined-session-operation-unavailable"):
                        backend.attach("local:namespace", "same-native-runtime", 80, 24)
                clock[0] = baseline + 3615.001
                delayed = backend.snapshot()
                self.assertEqual((delayed["workspaces"][0]["status"], delayed["workspaces"][0]["observationStatus"]),
                                 ("unavailable", "delayed"))
                self.assertEqual(calls, ["observation"])
                self.assertEqual(self.local.calls, [])
                self.assertFalse(any(delayed["sessions"][0]["capabilities"].values()))
        finally:
            release.set()
            self.wait_observers(backend)
        recovered = backend.snapshot()
        self.assertEqual(recovered["workspaces"][0]["status"], "available")
        self.assertEqual(recovered["sessions"][0]["state"], "running")
        self.assertTrue(recovered["sessions"][0]["capabilities"]["attachTerminal"])
        self.assertNotIn("stale", recovered["sessions"][0])
        self.wait_observers(backend)

    def test_slow_refresh_retains_report_then_expires_at_original_freshness_boundary(self):
        backend = AsyncCombinedBackend(self.local, {"research": self.research})
        backend.refresh()
        original = self.local.snapshot
        entered, release = threading.Event(), threading.Event()
        def slow():
            entered.set()
            release.wait(3)
            return original()
        self.local.snapshot = slow
        before = copy.deepcopy(backend._cache["local"]["sessions"][0])
        baseline = backend._observed["local"]
        clock = [baseline + 3]
        try:
            with patch("botainer_dashboard.combined_backend.time.monotonic", side_effect=lambda: clock[0]):
                backend.snapshot()
                self.assertTrue(entered.wait(1))
                # Let the independent fast child finish at this clock value
                # before advancing time past the old observation's deadline.
                for _ in range(100):
                    with backend._lock:
                        if backend._observed["research"] >= clock[0]:
                            break
                    time.sleep(.001)
                else:
                    self.fail("healthy child did not complete independently")
                # An ordinary pending refresh must not flash a warning or revoke
                # a still-fresh observation, even past the refresh interval.
                for age in (8, 15):
                    clock[0] = baseline + age
                    snap = backend.snapshot()
                    local, session = snap["workspaces"][0], snap["sessions"][0]
                    self.assertEqual((local["status"], local["observationStatus"]), ("available", "refreshing"))
                    self.assertEqual(session["state"], "running")
                    self.assertTrue(session["capabilities"]["attachTerminal"])
                clock[0] = baseline + 15.001
                snap = backend.snapshot()
                local, session = snap["workspaces"][0], snap["sessions"][0]
                self.assertEqual((local["status"], local["observationStatus"]), ("unavailable", "delayed"))
                self.assertEqual((session["state"], session["lastKnownState"]), ("unknown", "running"))
                self.assertEqual(session["lastKnownAt"], before["lastKnownAt"])
                self.assertFalse(any(session["capabilities"].values()))
                self.assertFalse(any(snap["projects"][0]["capabilities"].values()))
                with self.assertRaisesRegex(BackendUnavailable, "combined-session-operation-unavailable"):
                    backend.attach("local:namespace", "same-native-runtime", 80, 24)
                self.assertEqual(self.local.calls, [])
                self.assertEqual(snap["workspaces"][1]["status"], "available")
                self.assertNotIn("observationStatus", backend._cache["local"])
                self.local.value["sessions"][0]["state"] = "queued"
        finally:
            release.set()
            self.wait_observers(backend)
        recovered = backend.snapshot()
        self.assertEqual(recovered["workspaces"][0]["status"], "available")
        self.assertEqual(recovered["sessions"][0]["state"], "queued")
        self.assertEqual(recovered["sessions"][0]["lastKnownState"], "queued")
        self.assertNotEqual(recovered["sessions"][0]["lastKnownAt"], before["lastKnownAt"])
        self.assertNotIn("stale", recovered["sessions"][0])
        self.wait_observers(backend)

    def test_failure_stays_failed_while_retry_pending_and_child_checking_is_not_recovery(self):
        backend = AsyncCombinedBackend(self.local)
        backend.refresh()
        before = copy.deepcopy(backend._cache["local"]["sessions"][0])
        self.local.failure = BackendUnavailable("ordinary-source-changed-requalification-required")
        backend.refresh()
        failed = backend.snapshot()
        self.assertEqual(failed["workspaces"][0]["observationStatus"], "failed")
        diagnostic = failed["workspaces"][0]["connectionDiagnostic"]
        self.assertEqual(diagnostic["code"], "botainer-installation-changed")
        self.assertEqual(failed["sessions"][0]["lastKnownAt"], before["lastKnownAt"])
        entered, release = threading.Event(), threading.Event()
        original = self.local.snapshot
        def retry():
            entered.set()
            release.wait(3)
            return original()
        self.local.snapshot = retry
        backend._attempted.clear()
        try:
            backend.snapshot()
            self.assertTrue(entered.wait(1))
            pending = backend.snapshot()
            self.assertEqual(pending["workspaces"][0]["observationStatus"], "failed")
            self.assertEqual(pending["workspaces"][0]["connectionDiagnostic"], diagnostic)
            self.assertFalse(any(pending["sessions"][0]["capabilities"].values()))
            self.local.failure = None
            self.local.value["connectionStatus"] = "checking"
        finally:
            release.set()
            self.wait_observers(backend)
        checking = backend.snapshot()
        self.assertEqual(checking["workspaces"][0]["observationStatus"], "failed")
        self.assertEqual(checking["workspaces"][0]["status"], "unavailable")
        self.assertEqual(checking["workspaces"][0]["connectionDiagnostic"], diagnostic)
        self.local.snapshot = original
        self.local.value["connectionStatus"] = "available"
        backend.refresh()
        recovered = backend.snapshot()
        self.assertEqual(recovered["workspaces"][0]["observationStatus"], "current")
        self.assertNotIn("connectionDiagnostic", recovered["workspaces"][0])
        self.assertEqual(recovered["sessions"][0]["state"], "running")

    def test_unavailable_child_dto_preserves_exact_target_last_report_without_inventing_one(self):
        self.backend.snapshot()
        before = copy.deepcopy(self.backend._cache["local"]["sessions"][0])
        self.local.value.update(connectionStatus="unavailable", stale=True, error="synthetic-disconnect")
        self.local.value["sessions"][0].update(state="unknown", stale=True)
        self.local.value["sessions"].append({"contextNamespace": "local:namespace", "runtimeId": "never-observed",
            "projectId": "local-project", "state": "unknown", "stale": True})
        snap = self.backend.snapshot()
        old, unknown = snap["sessions"][:2]
        self.assertEqual(snap["workspaces"][0]["observationStatus"], "failed")
        self.assertEqual((old["lastKnownState"], old["lastKnownAt"]), ("running", before["lastKnownAt"]))
        self.assertNotIn("lastKnownAt", unknown)
        self.assertNotEqual(unknown.get("lastKnownState"), "running")
        self.assertFalse(any(old["capabilities"].values()))

    def test_expired_observation_without_pending_check_is_stale_not_failed(self):
        backend = AsyncCombinedBackend(self.local)
        backend.refresh()
        backend._observed["local"] = time.monotonic() - 16
        backend._attempted["local"] = time.monotonic()
        snap = backend.snapshot()
        self.assertEqual(snap["workspaces"][0]["observationStatus"], "stale")
        self.assertEqual(snap["sessions"][0]["state"], "unknown")
        self.assertFalse(any(snap["sessions"][0]["capabilities"].values()))

    def test_initial_slow_check_is_delayed_without_an_invented_last_report(self):
        backend = AsyncCombinedBackend(self.local)
        # Represent an already-running initial observer without calling a child.
        backend._inflight.add("local")
        backend._attempted["local"] = time.monotonic() - 16
        snap = backend.snapshot()
        self.assertEqual(snap["workspaces"][0]["observationStatus"], "delayed")
        self.assertEqual(snap["workspaces"][0]["status"], "unavailable")
        self.assertNotIn("lastObservationAt", snap["workspaces"][0])
        self.assertEqual(snap["sessions"], [])
        self.assertFalse(any(snap["workspaces"][0]["capabilities"].values()))

    def test_child_checking_responses_do_not_reset_the_delayed_check_indicator(self):
        backend = AsyncCombinedBackend(self.research)
        self.research.value["connectionStatus"] = "checking"
        backend.refresh()
        self.assertEqual(backend.snapshot()["workspaces"][0]["observationStatus"], "checking")
        baseline = backend._checking_since["local"] = time.monotonic() - 16
        backend.refresh()
        snap = backend.snapshot()
        self.assertEqual(backend._checking_since["local"], baseline)
        self.assertEqual(snap["workspaces"][0]["observationStatus"], "delayed")
        self.assertEqual(snap["workspaces"][0]["status"], "unavailable")
        self.assertNotIn("lastObservationAt", snap["workspaces"][0])
        self.assertFalse(any(snap["sessions"][0]["capabilities"].values()))
        self.research.value["connectionStatus"] = "available"
        backend.refresh()
        self.assertEqual(backend.snapshot()["workspaces"][0]["observationStatus"], "current")
        self.assertNotIn("local", backend._checking_since)

    def test_blocked_local_observer_never_blocks_snapshot_or_healthy_research_attach_stop(self):
        backend = AsyncCombinedBackend(self.local, {"research": self.research})
        backend.refresh()
        entered, release, dispatched = threading.Event(), threading.Event(), threading.Event()
        calls, errors = [], []
        original = self.local.snapshot
        def blocked():
            calls.append("observation")
            entered.set()
            release.wait(3)
            return original()
        self.local.snapshot = blocked
        with backend._lock:
            backend._attempted.clear()
        started = time.monotonic()
        backend.snapshot()
        self.assertLess(time.monotonic() - started, .5)
        self.assertTrue(entered.wait(1))
        def controls():
            try:
                backend.attach("cluster:research", "same-native-runtime", 120, 35)
                backend.stop_session("cluster:research", "same-native-runtime", "stop-request")
            except Exception as error:
                errors.append(error)
            finally:
                dispatched.set()
        worker = threading.Thread(target=controls, daemon=True)
        try:
            # Repeated browser polling cannot create more observers for the
            # blocked child. Age both its cache and the existing attempt; a new
            # check after inactivity has a separate bounded checking state.
            with backend._lock:
                backend._observed["local"] = time.monotonic() - 16
                backend._attempted["local"] = time.monotonic() - 16
            for _ in range(8):
                snap = backend.snapshot()
            self.assertEqual(calls, ["observation"])
            self.assertEqual(snap["workspaces"][0]["status"], "unavailable")
            self.assertEqual(snap["sessions"][0]["state"], "unknown")
            self.assertEqual(snap["sessions"][0]["lastKnownState"], "running")
            self.assertEqual(snap["workspaces"][1]["status"], "available")
            worker.start()
            self.assertTrue(dispatched.wait(.5), "Research controls waited behind blocked local Docker observation")
            self.assertEqual(errors, [])
            self.assertEqual([name for name, _args in self.research.calls], ["attach", "stop_session"])
        finally:
            release.set()
            if worker.ident is not None:
                worker.join(2)
            self.wait_observers(backend)

    def test_start_result_is_visible_immediately_and_older_inflight_inventory_cannot_erase_it(self):
        backend = AsyncCombinedBackend(self.local, {"research": self.research})
        backend.refresh()
        original = self.research.snapshot
        old = original()
        entered, release = threading.Event(), threading.Event()
        def delayed_old_observation():
            entered.set()
            release.wait(3)
            return old
        self.research.snapshot = delayed_old_observation
        with backend._lock:
            backend._attempted.pop("research")
        backend.snapshot()
        self.assertTrue(entered.wait(1))
        new = copy.deepcopy(old["sessions"][0])
        new.update(runtimeId="new-native-runtime", state="starting")
        self.research.replies["start_session"] = {"session": new}
        try:
            result = backend.start_session("research-project", "new-request")
            self.assertEqual(result["session"]["runtimeId"], "new-native-runtime")
            snap = backend.snapshot()
            self.assertTrue(any(item["runtimeId"] == "new-native-runtime" for item in snap["sessions"]))
            self.research.value["sessions"].append(new)
            self.research.snapshot = original
        finally:
            release.set()
            self.wait_observers(backend)
        with backend._lock:
            self.assertTrue(any(item["runtimeId"] == "new-native-runtime" for item in backend._cache["research"]["sessions"]))
        self.assertTrue(any(item["runtimeId"] == "new-native-runtime" for item in backend.snapshot()["sessions"]))
        self.wait_observers(backend)

    def test_project_and_namespace_collisions_block_dispatch(self):
        for collision in ("project", "namespace"):
            with self.subTest(collision=collision):
                local = Child("one", "namespace:one")
                remote = Child("two", "namespace:two", cluster=True)
                combined = CombinedBackend(local, {"remote": remote})
                if collision == "project":
                    remote.value["projects"][0]["id"] = "one"
                    remote.value["sessions"][0]["projectId"] = "one"
                else:
                    remote.value["sessions"][0]["contextNamespace"] = "namespace:one"
                with self.assertRaisesRegex(BackendUnavailable, "collision"):
                    combined.start_session("one", "request")
                self.assertFalse(local.calls or remote.calls)

    def test_disappeared_identity_cannot_be_reassigned_to_another_workspace(self):
        self.backend.snapshot()
        self.research.value["projects"] = []
        self.research.value["sessions"] = []
        self.backend.snapshot()
        self.other.value["projects"][0]["id"] = "research-project"
        self.other.value["sessions"][0]["projectId"] = "research-project"
        with self.assertRaisesRegex(BackendUnavailable, "project-collision"):
            self.backend.read_config("research-project")
        self.assertFalse(self.local.calls or self.research.calls or self.other.calls)

    def test_project_creation_requires_explicit_local_workspace_and_both_registered_choices(self):
        data = {"workspaceId": "local", "mode": "create", "rootId": "root", "path": "new",
                "name": "New project", "installationId": "shell", "requestId": "request"}
        for change in ({"workspaceId": "research"}, {"workspaceId": "missing"}, {"rootId": "missing"},
                       {"installationId": "remote-shell"}, {"mode": "unknown"}):
            with self.subTest(change=change), self.assertRaises(BackendUnavailable):
                self.backend.create_project(data | change)
        without_workspace = dict(data)
        del without_workspace["workspaceId"]
        with self.assertRaises(BackendUnavailable):
            self.backend.create_project(without_workspace)
        self.assertFalse(any(name == "create_project" for name, _args in self.local.calls))
        result = self.backend.create_project(data)
        self.assertEqual(result["project"]["workspaceId"], "local")
        forwarded = next(args[0] for name, args in self.local.calls if name == "create_project")
        self.assertNotIn("workspaceId", forwarded)
        self.assertEqual(forwarded["rootId"], "root")
        self.assertEqual(forwarded["installationId"], "shell")
        self.assertEqual(self.research.calls, [])

    def test_metadata_and_overall_dashboard_config_are_explicitly_local(self):
        metadata = self.backend.workspace_metadata()
        self.assertTrue(all(item["workspaceId"] == "local" for name in ("roots", "installations")
                            for item in metadata[name]))
        self.backend.dashboard_config()
        self.backend.validate_dashboard_config("text", "revision")
        self.backend.save_dashboard_config("text", "revision")
        self.assertEqual([name for name, _ in self.local.calls], ["workspace_metadata", "dashboard_config",
                         "validate_dashboard_config", "save_dashboard_config"])
        self.assertFalse(self.research.calls or self.other.calls)

    def test_remote_native_setup_is_scoped_and_publishes_its_console(self):
        self.research.value["mode"] = "installed-remote"
        self.research.value["capabilities"].update(projectOpen=True, nativeCliProjectSetup=True)
        self.research.replies["workspace_metadata"] = {"roots": [{"id": "remote-root"}],
            "installations": [{"id": "remote-install"}]}
        setup = {"projectId": "new-remote", "contextNamespace": "setup:research", "runtimeId": "setup-1",
                 "kind": "launch", "operation": "init", "capabilities": {"attachTerminal": True}}
        self.research.replies["create_project"] = {"project": {"id": "new-remote", "name": "New"}, "session": setup}
        data = {"workspaceId": "research", "mode": "open", "rootId": "remote-root", "path": "new",
                "name": "New", "installationId": "remote-install", "requestId": "request"}
        with self.assertRaises(BackendUnavailable):
            self.backend.create_project(data | {"rootId": "root"})
        result = self.backend.create_project(data)
        self.assertEqual(result["project"]["workspaceId"], "research")
        self.assertEqual(result["session"]["workspaceId"], "research")
        self.assertEqual(result["session"]["runtimeId"], "setup-1")
        self.assertEqual(self.backend._namespace_claims["setup:research"], "research")
        self.assertFalse(any(name == "create_project" for name, _args in self.local.calls))
        self.assertEqual(self.backend.snapshot()["workspaces"][1]["mode"], "installed-remote")

    def test_agent_override_requires_selected_project_capability(self):
        with self.assertRaisesRegex(BackendUnavailable, "operation-unavailable"):
            self.backend.start_session("research-project", "request", agent="codex")
        self.assertFalse(self.research.calls)
        self.research.value["capabilities"]["agentOverride"] = True
        captured = []
        def start(project_id, request_id, agent=None):
            captured.append((project_id, request_id, agent))
            return {"session": self.research.value["sessions"][0]}
        self.research.start_session = start
        result = self.backend.start_session("research-project", "request", agent="codex")
        self.assertEqual(captured, [("research-project", "request", "codex")])
        self.assertEqual(result["session"]["workspaceId"], "research")
        with self.assertRaisesRegex(BackendUnavailable, "invalid"):
            self.backend.start_session("research-project", "request", agent="codex --unsafe")

    def test_wrong_child_control_reply_is_not_rebound_or_rewritten(self):
        wrong = copy.deepcopy(self.research.value["sessions"][0])
        wrong["contextNamespace"] = "cluster:other"
        self.research.replies["stop_session"] = {"session": wrong}
        with self.assertRaisesRegex(BackendUnavailable, "reply-invalid"):
            self.backend.stop_session("cluster:research", "same-native-runtime", "request")
        self.assertEqual(self.other.calls, [])
        self.assertEqual(self.backend.snapshot()["sessions"][1]["contextNamespace"], "cluster:research")

    def test_zero_clusters_and_duplicate_backend_registration(self):
        self.assertEqual(len(CombinedBackend(self.local).snapshot()["workspaces"]), 1)
        for clusters in ({"local": self.research}, {"duplicate": self.local}, {"a": self.research, "b": self.research}):
            with self.subTest(keys=list(clusters)), self.assertRaisesRegex(BackendUnavailable, "collision"):
                CombinedBackend(self.local, clusters)

    def test_remote_only_composition_has_no_phantom_local_settings_or_creator(self):
        backend = CombinedBackend(None, {"research": self.research, "other": self.other})
        snap = backend.snapshot()
        self.assertEqual([(item["id"], item["kind"]) for item in snap["workspaces"]],
                         [("research", "cluster"), ("other", "cluster")])
        self.assertFalse(any(snap["capabilities"].values()))
        metadata = backend.workspace_metadata()
        self.assertEqual(metadata["roots"], [])
        self.assertEqual(metadata["installations"], [])
        for method, args in (("dashboard_config", ()), ("validate_dashboard_config", ("text", "revision")),
                             ("save_dashboard_config", ("text", "revision")),
                             ("create_project", ({"workspaceId": "local"},)),
                             ("create_project", ({"workspaceId": "research"},))):
            with self.subTest(method=method), self.assertRaises(BackendUnavailable):
                getattr(backend, method)(*args)
        self.assertFalse(self.research.calls or self.other.calls)
        backend.attach("cluster:research", "same-native-runtime", 80, 24)
        self.assertEqual(self.research.calls, [("attach", ("cluster:research", "same-native-runtime", 80, 24))])

    def test_local_identifier_stays_reserved_without_local_and_empty_composition_is_rejected(self):
        with self.assertRaisesRegex(BackendUnavailable, "collision"):
            CombinedBackend(None, {"local": self.research})
        with self.assertRaisesRegex(BackendUnavailable, "empty"):
            CombinedBackend(None)


if __name__ == "__main__":
    unittest.main()
