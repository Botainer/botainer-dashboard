"""Explicit workspace composition; no discovery, execution or target rewriting.

Each child remains responsible for authoritative runtime checks. This router
only dispatches identities advertised by that exact child. Failed observations
retain stale inventory, never imply that a session ended, and disable controls.
"""
from __future__ import annotations

import copy
import re
import threading
import time
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import PurePosixPath

from .backend_errors import BackendUnavailable
from .workspace import _relative

_ID = re.compile(r"[A-Za-z0-9_.:-]{1,160}\Z")
_DASHBOARD_CAPS = ("dashboardConfigRead", "dashboardConfigWrite")
OBSERVATION_INTERVAL = 2.0
MAX_OBSERVATION_AGE = 15.0
_KNOWN_STATES = frozenset({"running", "starting", "queued", "stopped", "failed"})
_CONTROL_RESTRICTIONS = frozenset({"outside-project-roots", "legacy-project-identity",
                                  "installation-relink-required", "project-registration-unavailable"})
_CONNECTION_REASONS = {
    **dict.fromkeys(("ordinary-source-changed-requalification-required", "ordinary-source-module-set-changed",
                    "ordinary-installation-changed-requalification-required",
                    "ordinary-installed-layout-changed-requalification-required"), "botainer-installation-changed"),
    **dict.fromkeys(("ordinary-profile-changed-restart-required", "ordinary-dashboard-helper-changed-restart-required",
                    "remote-profile-changed-restart-required", "remote-helper-changed-restart-required",
                    "cluster-profile-changed-restart-required", "cluster-helper-changed-restart-required",
                    "validator-source-changed-restart-required", "host-profile-changed-restart-required",
                    "host-helper-changed-restart-required"), "dashboard-restart-required"),
}
_CONNECTION_DIAGNOSTICS = {
    "botainer-installation-changed": (
        "The Botainer installation no longer matches its saved verification.",
        "Review this machine's installation in Settings → Machines. An installation update needs renewed verification; "
        "restarting the dashboard alone will not repair the saved verification."),
    "dashboard-restart-required": (
        "The dashboard helper or selected machine profile changed.",
        "Restart the dashboard to load the current helper and profile, then check this machine again."),
    "workspace-check-failed": (
        "The workspace connection could not be verified.",
        "Check this machine in Settings → Machines and review its private diagnostic evidence. "
        "Existing session activity remains unverified."),
}


def _connection_diagnostic(*reasons):
    """Advisory fixed text only; child prose and unknown codes are never echoed."""
    code = next((_CONNECTION_REASONS[reason] for reason in reasons
                 if isinstance(reason, str) and len(reason) <= 160 and reason in _CONNECTION_REASONS),
                "workspace-check-failed")
    message, recovery = _CONNECTION_DIAGNOSTICS[code]
    return {"code": code, "message": message, "recovery": recovery}


def _identity(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise BackendUnavailable("combined-identity-invalid")
    return value


def _caps(value):
    if not isinstance(value, dict) or any(not isinstance(key, str) or type(enabled) is not bool
                                          for key, enabled in value.items()):
        raise BackendUnavailable("combined-capabilities-invalid")
    return dict(value)


def _stale(entity, reason, *, session=False):
    entity = copy.deepcopy(entity)
    entity["stale"] = True
    entity["unavailableReason"] = reason
    entity["capabilities"] = {name: False for name in entity.get("capabilities", {})}
    if session:
        if "lastKnownState" not in entity:
            entity["lastKnownState"] = entity.get("state")
        entity["state"] = "unknown"
    return entity


def _disable_controls(entity):
    entity = copy.deepcopy(entity)
    entity["capabilities"] = {name: False for name in entity.get("capabilities", {})}
    return entity


def _host_folder_match(path, roots):
    """Lexical eligibility only; the host child verifies actual folder identity."""
    if (not isinstance(path, str) or not path.startswith("/") or path.startswith("//")
            or str(PurePosixPath(path)) != path):
        return None
    candidate = PurePosixPath(path)
    matches = []
    for root in roots:
        try:
            relative = str(candidate.relative_to(root["path"]))
            _relative(relative)
        except ValueError:
            continue
        matches.append((len(PurePosixPath(root["path"]).parts), root["id"], relative))
    return max(matches) if matches else None


class CombinedBackend:
    """Combine an optional explicit local backend and named existing clusters."""

    requires_workspace_id = True

    def __init__(self, local_backend, clusters=None, *, local_id="local", local_label="This computer"):
        self.local_id = _identity(local_id)
        if not isinstance(local_label, str) or not local_label or len(local_label) > 160:
            raise BackendUnavailable("combined-workspace-label-invalid")
        if clusters is None:
            clusters = {}
        if not isinstance(clusters, Mapping):
            raise BackendUnavailable("combined-workspaces-invalid")
        self._children = {self.local_id: local_backend} if local_backend is not None else {}
        self._labels = {self.local_id: local_label} if local_backend is not None else {}
        for workspace_id, child in clusters.items():
            _identity(workspace_id)
            if workspace_id == self.local_id or workspace_id in self._children or any(child is previous for previous in self._children.values()):
                raise BackendUnavailable("combined-workspace-collision")
            if child is None:
                raise BackendUnavailable("combined-workspaces-invalid")
            self._children[workspace_id] = child
            label = getattr(getattr(child, "profile", None), "label", workspace_id)
            self._labels[workspace_id] = label if isinstance(label, str) else workspace_id
        if not self._children:
            raise BackendUnavailable("combined-workspaces-empty")
        self._execution_kinds = {key: getattr(child, "execution_kind", None)
                                 for key, child in self._children.items()}
        # Profiles were validated by the launcher. Copy only static selection
        # metadata: polling must not inspect folders or call children under the
        # router lock merely to advertise a native-folder shortcut.
        self._host_selections = {}
        for key, child in self._children.items():
            if self._execution_kinds[key] != "host":
                continue
            profile = getattr(child, "profile", None)
            data = getattr(profile, "data", {})
            roots = data.get("project_roots", {}) if isinstance(data, dict) else {}
            installation = getattr(profile, "id", None)
            if not isinstance(roots, dict) or not roots or not isinstance(installation, str):
                continue
            if any(not isinstance(root_id, str) or not isinstance(path, str)
                   or not path.startswith("/") or path.startswith("//")
                   or str(PurePosixPath(path)) != path for root_id, path in roots.items()):
                continue
            self._host_selections[key] = {"installation": installation,
                "roots": [{"id": root_id, "path": path} for root_id, path in roots.items()]}
        self._lock = threading.RLock()
        self._cache = {}
        self._current = {}
        self._project_claims = {}
        self._namespace_claims = {}
        self._observed = {}
        self._reported_at = {}
        self._checking_since = {}
        self._attempted = {}
        self._inflight = set()
        self._errors = {}
        self._connection_diagnostics = {}
        self._collisions = {}
        self._generation = {key: 0 for key in self._children}

    def _normalize(self, workspace_id, raw, *, reported_at=None):
        if not isinstance(raw, dict) or not isinstance(raw.get("projects"), list) or not isinstance(raw.get("sessions"), list):
            raise BackendUnavailable("combined-snapshot-invalid")
        result = copy.deepcopy(raw)
        # Workspace hints are authored here from exact known failure codes.
        result.pop("connectionDiagnostic", None)
        base = _caps(raw.get("capabilities", {}))
        projects, sessions, targets, restricted = {}, [], set(), set()
        observation_available = (raw.get("connectionStatus", "available") == "available"
                                 and not raw.get("stale") and not raw.get("error")
                                 and not raw.get("unavailableReason"))
        previous_sessions = {(item["contextNamespace"], item["runtimeId"]): item
                             for item in self._cache.get(workspace_id, {}).get("sessions", [])}
        for project in result["projects"]:
            if not isinstance(project, dict):
                raise BackendUnavailable("combined-snapshot-invalid")
            key = _identity(project.get("id"))
            if key in projects:
                raise BackendUnavailable("combined-project-collision")
            project["workspaceId"] = workspace_id
            project["capabilities"] = base | _caps(project.get("capabilities", {}))
            if project.get("unavailableReason"):
                if (observation_available and not project.get("stale")
                        and project.get("controlRestriction") in _CONTROL_RESTRICTIONS):
                    # A verified runtime observation remains useful when this
                    # dashboard is not allowed to control its project folder.
                    project = _disable_controls(project)
                    restricted.add(key)
                else:
                    project = _stale(project, project["unavailableReason"])
            projects[key] = project
        for session in result["sessions"]:
            if not isinstance(session, dict):
                raise BackendUnavailable("combined-snapshot-invalid")
            target = (_identity(session.get("contextNamespace")), _identity(session.get("runtimeId")))
            if target in targets or session.get("projectId") not in projects:
                raise BackendUnavailable("combined-session-identity-invalid")
            targets.add(target)
            session["workspaceId"] = workspace_id
            session["capabilities"] = base | _caps(session.get("capabilities", {}))
            project = projects[session["projectId"]]
            previous = previous_sessions.get(target)
            if (reported_at and observation_available and not project.get("stale")
                    and not session.get("stale") and session.get("state") in _KNOWN_STATES):
                # Receipt time, not a claim about when a caching child probed
                # the runtime. Historical state never grants a capability.
                session["lastKnownState"] = session["state"]
                session["lastKnownAt"] = reported_at
            elif (previous and previous["projectId"] == session["projectId"]
                    and (not observation_available or project.get("stale") or session.get("stale")
                         or session.get("state") not in _KNOWN_STATES)):
                if previous.get("lastKnownState") in _KNOWN_STATES:
                    session["lastKnownState"] = previous["lastKnownState"]
                    if "lastKnownAt" in previous:
                        session["lastKnownAt"] = previous["lastKnownAt"]
            if project.get("stale") or session.get("stale"):
                session = _stale(session, session.get("unavailableReason") or project.get("unavailableReason")
                                 or "Session observation is unavailable", session=True)
            elif project["id"] in restricted:
                session = _disable_controls(session)
                session["controlRestriction"] = project["controlRestriction"]
                session["unavailableReason"] = session.get("unavailableReason") or project["unavailableReason"]
            sessions.append(session)
        # A restriction is not proof that a missing session ended. Preserve an
        # omitted exact target as unknown, including genuine inspection failures.
        for old in self._cache.get(workspace_id, {}).get("sessions", []):
            target = (old["contextNamespace"], old["runtimeId"])
            project = projects.get(old["projectId"])
            if target not in targets and project and (project.get("stale") or project["id"] in restricted):
                sessions.append(_stale(old, project["unavailableReason"], session=True))
        result["projects"], result["sessions"] = list(projects.values()), sessions
        result["capabilities"] = base
        installations = result.get("installations", [])
        if not isinstance(installations, list) or any(not isinstance(item, dict) for item in installations):
            raise BackendUnavailable("combined-snapshot-invalid")
        result["installations"] = [item | {"workspaceId": workspace_id} for item in installations]
        return result

    def _unavailable(self, workspace_id):
        previous = self._cache.get(workspace_id, {})
        return {"notice": previous.get("notice", "Workspace observation is unavailable."),
                "connectionDiagnostic": copy.deepcopy(self._connection_diagnostics.get(workspace_id)
                                                        or _connection_diagnostic()),
                "capabilities": {name: False for name in previous.get("capabilities", {})},
                "projects": [_stale(item, "combined-workspace-unavailable") for item in previous.get("projects", [])],
                "sessions": [_stale(item, "combined-workspace-unavailable", session=True) for item in previous.get("sessions", [])],
                "installations": copy.deepcopy(previous.get("installations", [])),
                **({key: copy.deepcopy(previous[key]) for key in
                    ("executionKind", "availableAgents", "defaultAgent") if key in previous}),
                **({"clusterSettings": copy.deepcopy(previous["clusterSettings"])} if "clusterSettings" in previous else {})}

    def _claim(self, projects, namespaces, workspace_id, snapshot):
        for item in snapshot["projects"]:
            key = item["id"]
            if key in projects and projects[key] != workspace_id:
                raise BackendUnavailable("combined-project-collision")
            projects[key] = workspace_id
        for item in snapshot["sessions"]:
            key = item["contextNamespace"]
            if key in namespaces and namespaces[key] != workspace_id:
                raise BackendUnavailable("combined-namespace-collision")
            namespaces[key] = workspace_id

    def _begin_observation(self, workspace_id):
        if workspace_id in self._inflight:
            return None
        self._inflight.add(workspace_id)
        self._attempted[workspace_id] = time.monotonic()
        return self._generation[workspace_id]

    def _observe_one(self, workspace_id, generation):
        # A slow Docker/SSH observation never holds the router lock. There is at
        # most one observer per child, even if that child's call remains blocked.
        try:
            raw = self._children[workspace_id].snapshot()
            with self._lock:
                if generation != self._generation[workspace_id]:
                    return  # An operation published newer target state.
                reported_at = datetime.now(timezone.utc).isoformat()
                value = self._normalize(workspace_id, raw, reported_at=reported_at)
                projects, namespaces = dict(self._project_claims), dict(self._namespace_claims)
                self._claim(projects, namespaces, workspace_id, value)
                self._project_claims, self._namespace_claims = projects, namespaces
                self._cache[workspace_id] = value
                self._observed[workspace_id] = time.monotonic()
                if value.get("connectionStatus") == "checking":
                    self._checking_since.setdefault(workspace_id, self._observed[workspace_id])
                else:
                    self._checking_since.pop(workspace_id, None)
                if (value.get("connectionStatus", "available") == "available"
                        and not value.get("stale") and not value.get("error")
                        and not value.get("unavailableReason")):
                    self._reported_at[workspace_id] = reported_at
                    self._errors.pop(workspace_id, None)
                    self._connection_diagnostics.pop(workspace_id, None)
                elif value.get("connectionStatus") != "checking":
                    self._errors[workspace_id] = "combined-workspace-unavailable"
                    self._connection_diagnostics[workspace_id] = _connection_diagnostic(
                        value.get("unavailableReason"), value.get("error"))
                self._collisions.pop(workspace_id, None)
        except Exception as error:
            with self._lock:
                if generation == self._generation[workspace_id]:
                    self._errors[workspace_id] = "combined-workspace-unavailable"
                    self._connection_diagnostics[workspace_id] = _connection_diagnostic(
                        error.code if isinstance(error, BackendUnavailable) else None)
                    if isinstance(error, BackendUnavailable) and "collision" in error.code:
                        self._collisions[workspace_id] = error.code
        finally:
            with self._lock:
                self._inflight.discard(workspace_id)
                if generation != self._generation[workspace_id]:
                    self._attempted.pop(workspace_id, None)

    def refresh(self):
        """Explicit synchronous observation for operator/offline use, not routes.

        Calls child methods without the router lock and respects an existing
        in-flight observer. Normal HTTP requests use nonblocking snapshot().
        """
        for workspace_id in self._children:
            with self._lock:
                generation = self._begin_observation(workspace_id)
            if generation is not None:
                self._observe_one(workspace_id, generation)

    def _observation_status(self, workspace_id, value, now):
        """Presentation only; the independent freshness/capability fence remains."""
        connection = value.get("connectionStatus", "available")
        if (workspace_id in self._errors or connection not in ("available", "checking")
                or connection != "checking" and (value.get("unavailableReason") or value.get("error") or value.get("stale"))):
            # Retain failure during a retry, until a successful result replaces
            # it. Starting another observer is not evidence of recovery.
            return "failed"
        pending = workspace_id in self._inflight or connection == "checking"
        if workspace_id not in self._cache:
            return "delayed" if now - self._attempted.get(workspace_id, now) > MAX_OBSERVATION_AGE else "checking"
        expired = now - self._observed.get(workspace_id, float("-inf")) > MAX_OBSERVATION_AGE
        if expired:
            # Opening the dashboard after inactivity starts a new observation.
            # Its old cache is still fenced, but a just-started check has not
            # already timed out. Only an attempt that began after cache expiry
            # gets this checking period; an earlier slow refresh keeps the
            # original freshness deadline and cannot extend usable controls.
            attempted = self._attempted.get(workspace_id, float("-inf"))
            if (workspace_id in self._inflight and connection == "available"
                    and attempted - self._observed.get(workspace_id, now) > MAX_OBSERVATION_AGE
                    and now - attempted <= MAX_OBSERVATION_AGE):
                return "checking"
            return "delayed" if pending else "stale"
        if connection == "checking":
            return "delayed" if now - self._checking_since.get(workspace_id, now) > MAX_OBSERVATION_AGE else "checking"
        return "refreshing" if pending else "current"

    def snapshot(self):
        with self._lock:
            now = time.monotonic()
            for workspace_id in self._children:
                if now - self._attempted.get(workspace_id, float("-inf")) >= OBSERVATION_INTERVAL:
                    generation = self._begin_observation(workspace_id)
                    if generation is not None:
                        threading.Thread(target=self._observe_one, args=(workspace_id, generation), daemon=True).start()
            if self._collisions:
                raise BackendUnavailable(next(iter(self._collisions.values())))
            current, health = {}, {}
            for workspace_id in self._children:
                value = self._cache.get(workspace_id, {})
                observation_status = self._observation_status(workspace_id, value, now)
                checking = observation_status == "checking"
                unavailable = (workspace_id in self._errors or workspace_id not in self._cache
                    or now - self._observed.get(workspace_id, float("-inf")) > MAX_OBSERVATION_AGE
                    or value.get("connectionStatus", "available") != "available"
                    or bool(value.get("unavailableReason") or value.get("stale") or value.get("error")))
                if unavailable:
                    value = self._unavailable(workspace_id)
                    if checking:
                        value["notice"] = ("Refreshing workspace connection; showing last known projects."
                                           if workspace_id in self._cache else "Checking workspace connection.")
                # Add presentation metadata to a copy: never overwrite the
                # last successful cache with a transient in-flight status.
                value = copy.deepcopy(value)
                value["observationStatus"] = observation_status
                if workspace_id in self._reported_at:
                    value["lastObservationAt"] = self._reported_at[workspace_id]
                for entity in value["projects"] + value["sessions"]:
                    entity["observationStatus"] = observation_status
                health[workspace_id] = "checking" if checking else "unavailable" if unavailable else "available"
                current[workspace_id] = value
            self._current = current
            workspaces = []
            for workspace_id, value in current.items():
                item = {"id": workspace_id, "label": self._labels[workspace_id],
                        "kind": "local" if workspace_id == self.local_id or self._execution_kinds.get(workspace_id) == "host" or value.get("executionKind") == "host" else "cluster",
                        "mode": value.get("mode", "unavailable"),
                        "status": health[workspace_id], "notice": value.get("notice", ""),
                        "capabilities": value["capabilities"],
                        "observationStatus": value["observationStatus"]}
                if "lastObservationAt" in value:
                    item["lastObservationAt"] = value["lastObservationAt"]
                if self._execution_kinds.get(workspace_id) == "host" or value.get("executionKind") == "host":
                    item["executionKind"] = "host"
                    item["availableAgents"] = copy.deepcopy(value.get("availableAgents", []))
                    item["defaultAgent"] = value.get("defaultAgent")
                if health[workspace_id] == "unavailable":
                    item["unavailableReason"] = "combined-workspace-unavailable"
                    item["connectionDiagnostic"] = value["connectionDiagnostic"]
                elif health[workspace_id] == "checking":
                    item["unavailableReason"] = "combined-workspace-checking"
                if "clusterSettings" in value:
                    item["clusterSettings"] = value["clusterSettings"]
                if "lastObservedAt" in value:
                    item["lastObservedAt"] = value["lastObservedAt"]
                workspaces.append(item)
            local_caps = current.get(self.local_id, {}).get("capabilities", {})
            projects = copy.deepcopy([item for value in current.values() for item in value["projects"]])
            for project in projects:
                project.pop("nativeHostWorkspaces", None)
                if (project["workspaceId"] != self.local_id or health[self.local_id] != "available"
                        or current[self.local_id].get("mode") != "ordinary-local"
                        or self._execution_kinds.get(self.local_id) == "host"):
                    continue
                choices = [{"id": key, "label": self._labels[key]}
                    for key, selection in self._host_selections.items()
                    if health[key] == "available" and current[key]["capabilities"].get("projectOpen")
                    and _host_folder_match(project.get("path"), selection["roots"])]
                if choices:
                    project["nativeHostWorkspaces"] = choices
            return copy.deepcopy({"mode": "combined-workspace", "notice": "Choose a workspace to see its runtime and settings.",
                "capabilities": {name: bool(local_caps.get(name)) for name in _DASHBOARD_CAPS},
                "workspaces": workspaces,
                "projects": projects,
                "sessions": [item for value in current.values() for item in value["sessions"]],
                "installations": [item for value in current.values() for item in value["installations"]]})

    def _project_route(self, project_id, capability):
        _identity(project_id)
        self.snapshot()
        with self._lock:
            workspace_id = self._project_claims.get(project_id)
            value = self._current.get(workspace_id, {})
            project = next((item for item in value.get("projects", []) if item["id"] == project_id), None)
            if project is None:
                raise BackendUnavailable("combined-project-unregistered")
            if not project.get("capabilities", {}).get(capability):
                raise BackendUnavailable("combined-project-operation-unavailable")
            return workspace_id, self._children[workspace_id]

    def _session_route(self, namespace, runtime_id, capability):
        _identity(namespace), _identity(runtime_id)
        self.snapshot()
        with self._lock:
            workspace_id = self._namespace_claims.get(namespace)
            value = self._current.get(workspace_id, {})
            session = next((item for item in value.get("sessions", [])
                            if (item["contextNamespace"], item["runtimeId"]) == (namespace, runtime_id)), None)
            if session is None:
                raise BackendUnavailable("combined-session-unregistered")
            if not session.get("capabilities", {}).get(capability):
                raise BackendUnavailable("combined-session-operation-unavailable")
            return workspace_id, self._children[workspace_id]

    @staticmethod
    def _invoke(child, method, *args):
        function = getattr(child, method, None)
        if not callable(function):
            raise BackendUnavailable("combined-operation-unavailable")
        return function(*args)

    def _session_result(self, result, workspace_id, *, project_id=None, target=None):
        with self._lock:
            value = self._cache[workspace_id]
            if not isinstance(result, dict) or not isinstance(result.get("session"), dict):
                raise BackendUnavailable("combined-session-reply-invalid")
            session = result["session"]
            if project_id is not None and session.get("projectId") != project_id:
                raise BackendUnavailable("combined-session-reply-invalid")
            if target is not None and (session.get("contextNamespace"), session.get("runtimeId")) != target:
                raise BackendUnavailable("combined-session-reply-invalid")
            checked = self._normalize(workspace_id, {"projects": value["projects"], "sessions": [session],
                                                     "capabilities": value["capabilities"]})
            self._claim(self._project_claims, self._namespace_claims, workspace_id, checked)
            result = copy.deepcopy(result)
            result["session"] = checked["sessions"][0]
            key = (session["contextNamespace"], session["runtimeId"])
            value["sessions"] = [item for item in value["sessions"]
                if (item["contextNamespace"], item["runtimeId"]) != key] + [copy.deepcopy(result["session"])]
            self._generation[workspace_id] += 1
            self._attempted.pop(workspace_id, None)
            return result

    def start_session(self, project_id, request_id, agent=None):
        workspace_id, child = self._project_route(project_id, "startSession")
        if agent is not None:
            if agent not in ("claude", "codex"):
                raise BackendUnavailable("combined-agent-override-invalid")
            self._project_route(project_id, "agentOverride")
            result = child.start_session(project_id, request_id, agent=agent)
            return self._session_result(result, workspace_id, project_id=project_id)
        return self._session_result(self._invoke(child, "start_session", project_id, request_id),
                                    workspace_id, project_id=project_id)

    def attach(self, namespace, runtime_id, cols, rows):
        _workspace_id, child = self._session_route(namespace, runtime_id, "attachTerminal")
        return self._invoke(child, "attach", namespace, runtime_id, cols, rows)

    def read_terminal_history(self, namespace, runtime_id):
        _workspace_id, child = self._session_route(namespace, runtime_id, "readTerminalHistory")
        return self._invoke(child, "read_terminal_history", namespace, runtime_id)

    def stop_session(self, namespace, runtime_id, request_id):
        workspace_id, child = self._session_route(namespace, runtime_id, "stopSession")
        return self._session_result(self._invoke(child, "stop_session", namespace, runtime_id, request_id),
                                    workspace_id, target=(namespace, runtime_id))

    def stop_orphan_session(self, namespace, runtime_id, request_id):
        workspace_id, child = self._session_route(namespace, runtime_id, "stopSession")
        with self._lock:
            session = next(item for item in self._current[workspace_id]["sessions"]
                           if (item["contextNamespace"], item["runtimeId"]) == (namespace, runtime_id))
            if session.get("stopMode") != "orphan-container" or session.get("stopScope") != "container":
                raise BackendUnavailable("combined-stop-mode-changed")
        return self._session_result(self._invoke(child, "stop_orphan_session", namespace, runtime_id, request_id),
                                    workspace_id, target=(namespace, runtime_id))

    def _project_call(self, method, capability, project_id, *args):
        _workspace_id, child = self._project_route(project_id, capability)
        return self._invoke(child, method, project_id, *args)

    def list_files(self, project_id, path): return self._project_call("list_files", "filesRead", project_id, path)
    def read_file(self, project_id, path): return self._project_call("read_file", "filesRead", project_id, path)
    def read_config(self, project_id): return self._project_call("read_config", "configRead", project_id)
    def validate_config(self, project_id, text, revision):
        return self._project_call("validate_config", "configWrite", project_id, text, revision)
    def save_config(self, project_id, text, revision):
        return self._project_call("save_config", "configWrite", project_id, text, revision)

    def workspace_metadata(self):
        snapshot = self.snapshot()
        value = {"roots": [], "installations": [], "workspaces": snapshot["workspaces"],
                 "notice": "Choose a location, installation and project folder."}
        for workspace in snapshot["workspaces"]:
            workspace_id = workspace["id"]
            if workspace["status"] != "available" or not any(workspace["capabilities"].get(cap)
                    for cap in ("projectCreate", "projectRegister", "projectOpen")):
                continue
            metadata = copy.deepcopy(self._invoke(self._children[workspace_id], "workspace_metadata"))
            if not isinstance(metadata, dict):
                raise BackendUnavailable("combined-workspace-metadata-invalid")
            if workspace_id == self.local_id:
                value.update({key: item for key, item in metadata.items()
                              if key not in ("roots", "installations", "workspaces")})
            for field in ("roots", "installations"):
                items = metadata.get(field)
                if not isinstance(items, list):
                    raise BackendUnavailable("combined-workspace-metadata-invalid")
                seen = set()
                for item in items:
                    if not isinstance(item, dict) or _identity(item.get("id")) in seen:
                        raise BackendUnavailable("combined-workspace-metadata-invalid")
                    seen.add(item["id"])
                    item["workspaceId"] = workspace_id
                value[field].extend(items)
        return value

    def create_project(self, data):
        if not isinstance(data, dict) or data.get("workspaceId") not in self._children:
            raise BackendUnavailable("combined-project-workspace-unavailable")
        workspace_id = data["workspaceId"]
        metadata = self.workspace_metadata()
        if (data.get("rootId") not in {item["id"] for item in metadata["roots"] if item["workspaceId"] == workspace_id}
                or data.get("installationId") not in {item["id"] for item in metadata["installations"] if item["workspaceId"] == workspace_id}):
            raise BackendUnavailable("combined-project-registration-unregistered")
        capability = {"create": "projectCreate", "register": "projectRegister", "open": "projectOpen"}.get(data.get("mode"))
        with self._lock:
            if not capability or not self._current[workspace_id]["capabilities"].get(capability):
                raise BackendUnavailable("combined-project-creation-unavailable")
        forwarded = dict(data)
        del forwarded["workspaceId"]
        result = self._invoke(self._children[workspace_id], "create_project", forwarded)
        return self._project_result(result, workspace_id)

    def _project_result(self, result, workspace_id):
        if not isinstance(result, dict) or not isinstance(result.get("project"), dict):
            raise BackendUnavailable("combined-project-reply-invalid")
        sessions = [result["session"]] if "session" in result else []
        with self._lock:
            checked = self._normalize(workspace_id, {"projects": [result["project"]], "sessions": sessions,
                "capabilities": self._current[workspace_id]["capabilities"]})
            self._claim(self._project_claims, self._namespace_claims, workspace_id, checked)
            project = checked["projects"][0]
            cached = self._cache[workspace_id]
            cached["projects"] = [item for item in cached["projects"] if item["id"] != project["id"]] + [copy.deepcopy(project)]
            for session in checked["sessions"]:
                target = (session["contextNamespace"], session["runtimeId"])
                cached["sessions"] = [item for item in cached["sessions"]
                    if (item["contextNamespace"], item["runtimeId"]) != target] + [copy.deepcopy(session)]
            self._generation[workspace_id] += 1
            self._attempted.pop(workspace_id, None)
        reply = copy.deepcopy(result) | {"project": checked["projects"][0]}
        if sessions:
            reply["session"] = checked["sessions"][0]
        return reply

    def _native_folder_source(self, source_project_id, host_workspace_id):
        """Read one fresh local catalog identity, independent of Botainer controls."""
        def available(workspace_id):
            value = self._cache.get(workspace_id, {})
            return (workspace_id not in self._errors and workspace_id in self._cache
                and time.monotonic() - self._observed.get(workspace_id, float("-inf")) <= MAX_OBSERVATION_AGE
                and value.get("connectionStatus", "available") == "available"
                and not value.get("unavailableReason"))

        if (self._project_claims.get(source_project_id) != self.local_id
                or self._execution_kinds.get(self.local_id) == "host"):
            raise BackendUnavailable("combined-native-source-local-required")
        if not available(self.local_id) or self._cache[self.local_id].get("mode") != "ordinary-local":
            raise BackendUnavailable("combined-native-source-unavailable")
        source = next((project for project in self._cache[self.local_id]["projects"]
                       if project["id"] == source_project_id), None)
        if source is None:
            raise BackendUnavailable("combined-native-source-unavailable")
        if (host_workspace_id not in self._host_selections or not available(host_workspace_id)
                or not self._cache[host_workspace_id]["capabilities"].get("projectOpen")):
            raise BackendUnavailable("combined-native-target-unavailable")
        return copy.deepcopy(source)

    def open_host_project(self, source_project_id, host_workspace_id, request_id):
        """Save a known local folder for explicit native use; never launch or init."""
        _identity(source_project_id), _identity(host_workspace_id)
        try:
            if not isinstance(request_id, str) or str(uuid.UUID(request_id)) != request_id:
                raise ValueError()
        except ValueError:
            raise BackendUnavailable("combined-native-request-id-invalid") from None
        self.snapshot()
        with self._lock:
            source = self._native_folder_source(source_project_id, host_workspace_id)
            selection = copy.deepcopy(self._host_selections[host_workspace_id])
        child = self._children[host_workspace_id]
        metadata = self._invoke(child, "workspace_metadata")
        if (not isinstance(metadata, dict) or not isinstance(metadata.get("roots"), list)
                or not isinstance(metadata.get("installations"), list)):
            raise BackendUnavailable("combined-workspace-metadata-invalid")
        roots = [{"id": item.get("id"), "path": item.get("path")} for item in metadata["roots"]
                 if isinstance(item, dict)]
        if (roots != selection["roots"] or not any(isinstance(item, dict)
                and item.get("id") == selection["installation"] for item in metadata["installations"])):
            raise BackendUnavailable("combined-native-target-unavailable")
        match = _host_folder_match(source.get("path"), roots)
        if match is None:
            raise BackendUnavailable("combined-native-project-outside-roots")
        _, root_id, relative = match
        with self._lock:
            latest = self._native_folder_source(source_project_id, host_workspace_id)
            if (latest.get("path"), latest.get("name")) != (source.get("path"), source.get("name")):
                raise BackendUnavailable("combined-native-source-changed")
        result = self._invoke(child, "create_project", {"mode": "open", "rootId": root_id,
            "installationId": selection["installation"], "path": relative,
            "name": source.get("name", ""), "requestId": request_id})
        if (not isinstance(result, dict) or set(result) != {"project"}
                or not isinstance(result["project"], dict)
                or result["project"].get("path") != source["path"]
                or result["project"].get("executionKind") != "host"):
            raise BackendUnavailable("combined-project-reply-invalid")
        return self._project_result(result, host_workspace_id)

    # This JSON file is the dashboard's overall local configuration. Cluster
    # profiles are exposed separately in each workspace's clusterSettings.
    def _dashboard_call(self, method, *args):
        if self.local_id not in self._children:
            raise BackendUnavailable("combined-dashboard-config-unavailable")
        return self._invoke(self._children[self.local_id], method, *args)

    def dashboard_config(self): return self._dashboard_call("dashboard_config")
    def validate_dashboard_config(self, text, revision):
        return self._dashboard_call("validate_dashboard_config", text, revision)
    def save_dashboard_config(self, text, revision):
        return self._dashboard_call("save_dashboard_config", text, revision)
