"""Explicit native host-agent sessions owned by a private existing tmux server.

These sessions have the user's normal host access; project roots only constrain
dashboard file and launch selection. They are not containers or sandboxes.
One isolated server per run avoids ever targeting an unrelated tmux session.
No process starts while importing or loading a profile.
"""
from __future__ import annotations

from .layout import data_root, resource_root

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import threading
import time
import uuid

from .backend_errors import BackendUnavailable
from .inventory import bounded_run, InventoryError
from .launch_console import private_lock
from .pairing import private_directory, read_private_json, write_private_json
from .pty_bridge import PtyAttachment
from .workspace import _relative, _visible, _read_at, _decode, _revision, WorkspaceError

_HASH = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[A-Za-z0-9_-]{1,48}\Z")
_RUN = re.compile(r"[0-9a-f]{32}\Z")
_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_LIMIT = 2 * 1024 * 1024
_FORMAT = ("#{pid}\t#{session_id}\t#{window_id}\t#{pane_id}\t#{pane_pid}\t"
           "#{session_created}\t#{socket_path}\t#{@dashboard_owner}\t#{session_name}\t"
           "#{pane_dead}\t#{pane_dead_status}\t#{pane_dead_signal}\t#{session_windows}")
_CONFIG = """set-option -g exit-unattached off
set-option -g exit-empty on
set-option -g destroy-unattached off
set-option -g update-environment ''
set-option -g prefix None
set-option -g prefix2 None
unbind-key -a
unbind-key -a -T root
set-option -g status off
set-option -g set-titles off
set-option -g set-clipboard off
set-option -g mouse off
set-option -g history-limit 10000
set-window-option -g remain-on-exit on
set-window-option -g allow-rename off
set-window-option -g allow-set-title off
set-window-option -g allow-passthrough off
set-window-option -g automatic-rename off
set-window-option -g window-size latest
"""


def _refuse(code):
    raise BackendUnavailable("host-" + code)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _request(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError()
    except ValueError:
        _refuse("request-id-invalid")
    return value


def _label(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 160 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        _refuse("label-invalid")
    return value


def _path(value, *, directory=True):
    if not isinstance(value, str) or not value.startswith("/") or any(ord(c) < 32 or ord(c) == 127 for c in value):
        _refuse("path-invalid")
    path = Path(value)
    if path.resolve(strict=True) != path or (directory and not path.is_dir()):
        _refuse("canonical-path-required")
    return path


def _hash(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > 512 * 1024 * 1024:
            _refuse("executable-file-invalid")
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        after = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            _refuse("executable-changed-while-reading")
        return digest.hexdigest()


def _identity(path):
    info = path.stat()
    return [info.st_dev, info.st_ino]


def _agent_path(value):
    """Check stored path syntax; changed installations must not hide live owners.

    Filesystem canonicality and exact bytes are checked by verify_agent before
    a new launch. An old path replaced by a symlink is unavailable, not adopted.
    """
    if (not isinstance(value, str) or not value.startswith("/")
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        _refuse("path-invalid")
    path = Path(value)
    if str(path) != value or ".." in path.parts:
        _refuse("canonical-path-required")
    return path


def _profile_document(path):
    before = _hash(path)
    try:
        value = read_private_json(path, max_bytes=65536)
    except RecursionError:
        _refuse("profile-invalid")
    after = _hash(path)
    if before != after:
        _refuse("profile-changed-while-reading")
    return value, after


def _configuration_identity(data):
    """Everything except an existing agent's executable selection stays fixed."""
    value = {key: item for key, item in data.items() if key not in {"version", "original_profile", "agents"}}
    agents = data.get("agents")
    if not isinstance(agents, dict):
        _refuse("agents-invalid")
    value["agents"] = {}
    for key, tool in agents.items():
        if not isinstance(tool, dict):
            _refuse("executable-selection-invalid")
        value["agents"][key] = {name: item for name, item in tool.items() if name not in {"path", "sha256"}}
    return value


class HostProfile:
    def __init__(self, path, *, _original_only=False):
        self.path = _path(str(Path(path).absolute()), directory=False)
        self.data, self.fingerprint = _profile_document(self.path)
        d = self.data
        required = {"version", "id", "label", "tmux", "home", "project_roots", "agents",
                    "default_agent", "environment", "control_root"}
        if (type(d.get("version")) is not int or d["version"] not in {1, 2}
                or set(d) != required | ({"original_profile"} if d["version"] == 2 else set())):
            _refuse("profile-invalid")
        if _original_only and d["version"] != 1:
            _refuse("original-profile-invalid")
        self.original_path, self.original_data = self.path, self.data
        self.identity_fingerprint = self.fingerprint
        if d["version"] == 2:
            original = d["original_profile"]
            if (not isinstance(original, dict) or set(original) != {"path", "sha256"}
                    or not isinstance(original["sha256"], str) or not _HASH.fullmatch(original["sha256"])):
                _refuse("original-profile-invalid")
            self.original_path = _path(original["path"], directory=False)
            if self.original_path.parent != self.path.parent or self.original_path == self.path:
                _refuse("original-profile-invalid")
            self.original_data, self.identity_fingerprint = _profile_document(self.original_path)
            if (self.identity_fingerprint != original["sha256"]
                    or set(self.original_data) != required or type(self.original_data.get("version")) is not int
                    or self.original_data["version"] != 1):
                _refuse("original-profile-invalid")
            baseline = HostProfile(self.original_path, _original_only=True)
            if baseline.fingerprint != self.identity_fingerprint:
                _refuse("original-profile-changed")
            if _configuration_identity(d) != _configuration_identity(self.original_data):
                _refuse("profile-scope-changed")
        if not isinstance(d["id"], str) or not _ID.fullmatch(d["id"]):
            _refuse("profile-id-invalid")
        _label(d["label"])
        _path(d["home"])
        control = _path(d["control_root"])
        info = control.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077 or len(os.fsencode(control)) > 55:
            _refuse("short-private-control-root-required")
        roots = d["project_roots"]
        if not isinstance(roots, dict) or not 1 <= len(roots) <= 32:
            _refuse("project-roots-invalid")
        for key, value in roots.items():
            if not isinstance(key, str) or not _ID.fullmatch(key):
                _refuse("project-roots-invalid")
            _path(value)
        agents = d["agents"]
        if not isinstance(agents, dict) or not agents or set(agents) - {"claude", "codex"} or d["default_agent"] not in agents:
            _refuse("agents-invalid")
        for name, tool in [("tmux", d["tmux"]), *agents.items()]:
            if (not isinstance(tool, dict) or {"path", "sha256"} - set(tool)
                    or set(tool) - {"path", "sha256", "label", "external_tools"}):
                _refuse("executable-selection-invalid")
            executable = (_path(tool["path"], directory=False) if name == "tmux" else _agent_path(tool["path"]))
            if (not isinstance(tool["sha256"], str) or not _HASH.fullmatch(tool["sha256"])
                    or name == "tmux" and (not executable.is_file() or not os.access(executable, os.X_OK))):
                _refuse("executable-selection-invalid")
            if "label" in tool:
                _label(tool["label"])
        if "claude" in agents and agents["claude"].get("external_tools") != "disabled":
            _refuse("claude-external-tools-disabled-required")
        if "codex" in agents and agents["codex"].get("external_tools", "default") != "default":
            _refuse("codex-external-tools-policy-unsupported")
        env = d["environment"]
        if not isinstance(env, dict) or set(env) - {"PATH", "LANG", "LC_ALL", "LC_CTYPE", "USER", "LOGNAME", "TMPDIR"}:
            _refuse("environment-invalid")
        if any(not isinstance(v, str) or any(ord(c) < 32 or ord(c) == 127 for c in v) for v in env.values()):
            _refuse("environment-invalid")
        if not env.get("PATH") or any(not p.startswith("/") or ".." in Path(p).parts for p in env["PATH"].split(":")):
            _refuse("environment-path-invalid")
        self.directories = {p: _identity(_path(p)) for p in [d["home"], d["control_root"], *roots.values()]}
        self.id, self.label = d["id"], d["label"]
        self._tool_metadata = {}
        self.verify()

    def verify(self, *, full=True):
        if _hash(self.path) != self.fingerprint:
            _refuse("profile-changed-restart-required")
        if self.original_path != self.path:
            _, digest = _profile_document(self.original_path)
            if digest != self.identity_fingerprint:
                _refuse("original-profile-changed")
        for name, identity in self.directories.items():
            if _identity(_path(name)) != identity:
                _refuse("profile-directory-changed")
        info = Path(self.data["control_root"]).stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            _refuse("control-root-permissions-changed")
        self._verify_tool(self.data["tmux"], full=full)

    def _verify_tool(self, tool, *, full=True):
        path = _path(tool["path"], directory=False)
        info = path.stat()
        metadata = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_mode)
        key = (str(path), tool["sha256"])
        if (not stat.S_ISREG(info.st_mode) or not os.access(path, os.X_OK)
                or (full or self._tool_metadata.get(key) != metadata) and _hash(path) != tool["sha256"]):
            _refuse("executable-changed-requalification-required")
        self._tool_metadata[key] = metadata

    def verify_agent(self, agent, *, full=True):
        if not isinstance(agent, str) or agent not in self.data["agents"]:
            _refuse("agent-unavailable")
        try:
            self._verify_tool(self.data["agents"][agent], full=full)
        except (OSError, ValueError, BackendUnavailable):
            _refuse("agent-executable-unavailable-review-update")

    @property
    def environment(self):
        return {**self.data["environment"], "HOME": self.data["home"], "TERM": "xterm-256color",
                "DISABLE_AUTOUPDATER": "1", "DISABLE_UPDATES": "1"}


def load_host_profile(path):
    """Read-only profile selection. Does not execute tmux or agents."""
    return HostProfile(path)


class HostAttachment:
    def __init__(self, client, writer):
        self.client, self.writer = client, writer

    def read(self, max_bytes, *, timeout=0):
        return self.client.read(max_bytes, timeout=timeout)

    def write(self, data):
        return self.client.write(data)

    def resize(self, cols, rows):
        return self.client.resize(cols, rows)

    def close(self):
        # Hold writer exclusion if the attachment process cannot be confirmed
        # gone. Never stop or signal the durable tmux server here.
        result = self.client.close(timeout=1)
        if self.writer is not None:
            os.close(self.writer)
            self.writer = None
        return result


class HostAgentBackend:
    execution_kind = "host"

    def __init__(self, root, profile_path, *, data_dir=None):
        self.profile = load_host_profile(profile_path)
        self.repo = Path(root).resolve(strict=True)
        self.root = private_directory(data_root(self.repo, data_dir) / "host-agents" / self.profile.identity_fingerprint)
        self.namespace = "host:" + self.profile.identity_fingerprint[:40]
        self.state_path = self.root / "host-state.json"
        self.thread_lock = threading.RLock()
        self.helper = resource_root() / "tools" / "host_agent_helper.py"
        self.helper_hash = _hash(self.helper)
        self.python = str(Path(sys.executable).absolute())

    @contextmanager
    def _locked(self, *, control=False):
        with self.thread_lock:
            fd = os.open(self.root / "host.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077:
                    _refuse("state-lock-invalid")
                fcntl.flock(fd, fcntl.LOCK_EX)
                self.profile.verify(full=control)
                if _hash(self.helper) != self.helper_hash:
                    _refuse("helper-changed-restart-required")
                yield
            finally:
                os.close(fd)

    def _load(self):
        if not self.state_path.exists():
            return {"version": 1, "profile": self.profile.identity_fingerprint, "projects": [], "sessions": [], "setup": {}, "stops": {}}
        data = read_private_json(self.state_path, max_bytes=_LIMIT)
        if (set(data) != {"version", "profile", "projects", "sessions", "setup", "stops"}
                or data["version"] != 1 or data["profile"] != self.profile.identity_fingerprint
                or not isinstance(data["projects"], list) or not isinstance(data["sessions"], list)
                or not isinstance(data["setup"], dict) or not isinstance(data["stops"], dict)):
            _refuse("state-invalid")
        return data

    def _save(self, state):
        if len(json.dumps(state).encode()) > _LIMIT - 4096:
            _refuse("state-size-limit")
        write_private_json(self.state_path, state)

    def _project(self, state, project_id):
        value = next((p for p in state["projects"] if p["id"] == project_id), None)
        if value is None:
            _refuse("project-unregistered")
        return value

    @contextmanager
    def _walk(self, root_id, parts):
        if root_id not in self.profile.data["project_roots"]:
            _refuse("project-root-unregistered")
        path = self.profile.data["project_roots"][root_id]
        fd = os.open(path, _DIR)
        try:
            if [os.fstat(fd).st_dev, os.fstat(fd).st_ino] != self.profile.directories[path]:
                _refuse("project-root-changed")
            for part in parts:
                child = os.open(part, _DIR, dir_fd=fd)
                os.close(fd)
                fd = child
            yield fd
        finally:
            os.close(fd)

    @contextmanager
    def _project_fd(self, record, parts=()):
        with self._walk(record["rootId"], _relative(record["relativePath"])) as project:
            if [os.fstat(project).st_dev, os.fstat(project).st_ino] != record["identity"]:
                _refuse("project-directory-changed")
            fd = os.dup(project)
            try:
                for part in parts:
                    if not _visible(part):
                        _refuse("file-access-excluded")
                    child = os.open(part, _DIR, dir_fd=fd)
                    os.close(fd)
                    fd = child
                yield fd
            finally:
                os.close(fd)

    def _agents(self):
        result = []
        for key, value in self.profile.data["agents"].items():
            available = True
            try:
                self.profile.verify_agent(key, full=False)
            except BackendUnavailable:
                available = False
            result.append({"id": key, "label": value.get("label", key.title()), "executable": value["path"],
                           "available": available,
                           **({"unavailableReason": "The selected host agent executable is missing or changed. Review its update in Settings → Machines before starting a new session."} if not available else {}),
                           "externalTools": "disabled" if key == "claude" else "default",
                           "startupHooks": "disabled" if key == "claude" else "default", "updates": "disabled" if key == "claude" else "default"})
        return result

    def _project_view(self, item, *, available=True, blocked=False):
        agents = self._agents()
        can_launch = any(agent["available"] for agent in agents)
        return {"id": item["id"], "name": item["name"], "path": item["path"],
                "executionKind": "host", "runtime": "host", "machineLabel": self.profile.label,
                "installationLabel": "Installed host agents", "contextNamespace": self.namespace,
                "lastLaunchAt": item.get("lastLaunchAt"), "availableAgents": agents,
                "defaultAgent": self.profile.data["default_agent"],
                **({"unavailableReason": "Host project folder moved or is unavailable"} if not available else {}),
                **({"startUnavailableReason": "All selected host agent executables are missing or changed. Review their updates in Settings → Machines. Existing sessions retain their terminal owners."} if not can_launch else {}),
                "capabilities": {"startSession": available and not blocked and can_launch, "agentOverride": available,
                                 "filesRead": available, "configRead": False, "configWrite": False}}

    def _session_view(self, item):
        active = item["state"] == "running"
        original = self.profile.original_data["agents"][item["agent"]]
        return {"contextNamespace": self.namespace, "runtimeId": item["id"], "projectId": item["projectId"],
                "label": self.profile.data["agents"][item["agent"]].get("label", item["agent"].title()),
                "agent": item["agent"], "requestedAgent": item["agent"], "runtime": "host", "executionKind": "host",
                "executable": item.get("executable", original["path"]),
                "sha256": item.get("sha256", original["sha256"]),
                "launchProfile": item.get("launchProfile", self.profile.identity_fingerprint),
                "state": item["state"], "createdAt": item["createdAt"], "startedAt": item.get("startedAt"),
                "recordedEndedAt": item.get("endedAt"), "exitCode": item.get("exitCode"),
                "endedObservedAt": item.get("endedAt"), "endTimeAccuracy": "observed" if item.get("endedAt") else None,
                "ownerCleanup": item.get("cleanupState"),
                "capabilities": {"attachTerminal": active, "stopSession": active},
                **({"unavailableReason": "Host session owner could not be verified; no automatic restart"}
                   if item["state"] == "unknown" else {})}

    def _folder(self, item):
        if not _RUN.fullmatch(item["id"]):
            _refuse("session-id-invalid")
        folder = Path(self.profile.data["control_root"]) / item["id"]
        if folder.resolve(strict=True) != folder:
            _refuse("session-control-directory-changed")
        info = folder.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077 or _identity(folder) != item["controlIdentity"]:
            _refuse("session-control-directory-changed")
        return folder

    def _tmux(self, item, *arguments, create=False):
        folder = self._folder(item)
        args = [self.profile.data["tmux"]["path"]]
        if not create:
            args.append("-N")
        args += ["-S", str(folder / "s"), "-f", str(folder / "tmux.conf"), *arguments]
        return bounded_run(tuple(args), cwd=self.root, env=self.profile.environment, timeout=5)

    def _socket(self, item):
        info = os.lstat(self._folder(item) / "s")
        # tmux commonly creates 0660 sockets. The verified 0700 control folder
        # excludes group users; world permissions are never accepted.
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o007:
            _refuse("socket-identity-invalid")
        return [info.st_dev, info.st_ino]

    def _parse_owner(self, item, raw, socket_identity):
        if len(raw) > 8192:
            _refuse("owner-output-invalid")
        parts = raw.decode("utf-8").strip("\r\n").split("\t")
        if len(parts) != 13:
            _refuse("owner-output-invalid")
        pid, sid, wid, pane, child, created, socket_path, nonce, name, dead, code, sig, windows = parts
        if (not re.fullmatch(r"[1-9][0-9]*", pid) or not re.fullmatch(r"\$[0-9]+", sid)
                or not re.fullmatch(r"@[0-9]+", wid) or not re.fullmatch(r"%[0-9]+", pane)
                or not re.fullmatch(r"[1-9][0-9]*", child) or not re.fullmatch(r"[0-9]+", created)
                or socket_path != str(self._folder(item) / "s") or nonce != item["nonce"]
                or name != "dashboard-" + item["id"] or dead not in {"0", "1"} or windows != "1"
                or code and not re.fullmatch(r"[0-9]+", code) or sig and not re.fullmatch(r"[0-9]+", sig)):
            _refuse("owner-output-invalid")
        return {"serverPid": int(pid), "sessionId": sid, "windowId": wid, "paneId": pane,
                "panePid": int(child), "sessionCreated": int(created), "socketIdentity": socket_identity,
                "nonce": nonce}, {"dead": dead == "1", "exitCode": int(code) if code else None,
                                  "exitSignal": int(sig) if sig else None}

    def _observe(self, item, *, initial=False):
        before = self._socket(item)
        target = item.get("owner", {}).get("paneId", "%0")
        raw = self._tmux(item, "display-message", "-p", "-t", target, _FORMAT)
        owner, status = self._parse_owner(item, raw, before)
        if self._socket(item) != before or (not initial and owner != item.get("owner")):
            _refuse("session-owner-changed")
        # No extra pane can silently replace or supplement the selected agent.
        panes = self._tmux(item, "list-panes", "-a", "-F", "#{pane_id}").decode().splitlines()
        if panes != [owner["paneId"]]:
            _refuse("unexpected-server-panes")
        return owner, status

    def _refresh_session(self, item):
        if item["state"] in {"stopped", "failed"}:
            return
        if not item.get("owner"):
            item["state"] = "unknown"
            return
        try:
            _owner, status = self._observe(item)
            if status["dead"]:
                item.update(state="failed" if status["exitCode"] or status["exitSignal"] else "stopped",
                            endedAt=item.get("endedAt") or _now(), exitCode=status["exitCode"], exitSignal=status["exitSignal"],
                            cleanupState="pending")
            else:
                item["state"] = "running"
        except (OSError, ValueError, InventoryError, BackendUnavailable):
            item["state"] = "unknown"

    def _cleanup_dead(self, state):
        """Retire only a proven dead pane after its outcome is durable.

        The isolated server exits when its last pane is removed. An interrupted
        or ambiguous cleanup is recorded and never automatically redispatched.
        A new/changed owner is never adopted or killed.
        """
        for item in state["sessions"]:
            if item.get("cleanupState") == "dispatching":
                item["cleanupState"] = "unknown"
                self._save(state)
            if item.get("cleanupState") != "pending" or item["state"] not in {"stopped", "failed"}:
                continue
            # Caller already persisted the terminal outcome. Re-prove both the
            # immutable owner and dead pane immediately before exact cleanup.
            try:
                _owner, status = self._observe(item)
                if not status["dead"]:
                    _refuse("cleanup-pane-not-dead")
            except (OSError, ValueError, InventoryError, BackendUnavailable):
                item["cleanupState"] = "owner-unavailable"
                self._save(state)
                continue
            item["cleanupState"] = "dispatching"
            self._save(state)
            try:
                self._tmux(item, "kill-pane", "-t", item["owner"]["paneId"])
                item["cleanupState"] = "completed"
            except (OSError, ValueError, InventoryError, BackendUnavailable):
                item["cleanupState"] = "unknown"
            self._save(state)

    def snapshot(self):
        with self._locked():
            state = self._load()
            for item in state["sessions"]:
                self._refresh_session(item)
            self._save(state)
            self._cleanup_dead(state)
            projects = []
            for item in state["projects"]:
                try:
                    with self._project_fd(item):
                        pass
                    available = True
                except (OSError, ValueError, BackendUnavailable):
                    available = False
                blocked = any(s["projectId"] == item["id"] and s["state"] == "unknown" for s in state["sessions"])
                projects.append(self._project_view(item, available=available, blocked=blocked))
            self._save(state)
            agents = self._agents()
            notice = "Host agents run directly on this computer with your account's access. No container isolation."
            if not any(agent["available"] for agent in agents):
                notice += " All selected agent executables are missing or changed; review their updates in Settings → Machines before starting new work. Existing sessions can still be controlled when their terminal owner is verified."
            return {"mode": "host-agents", "kind": "local", "executionKind": "host",
                    "connectionStatus": "available", "notice": notice,
                    "capabilities": {"projectCreate": True, "projectOpen": True, "projectRegister": True},
                    "availableAgents": agents, "defaultAgent": self.profile.data["default_agent"],
                    "projects": projects, "sessions": [self._session_view(s) for s in state["sessions"]],
                    "installations": [{"id": self.profile.id, "name": "Installed host agents", "transport": "local", "executionKind": "host"}]}

    def workspace_metadata(self):
        return {"roots": [{"id": key, "label": key, "path": value} for key, value in self.profile.data["project_roots"].items()],
                "installations": [{"id": self.profile.id, "label": "Installed host agents", "name": "Installed host agents", "executionKind": "host"}],
                "availableAgents": self._agents(), "defaultAgent": self.profile.data["default_agent"],
                "notice": "Register an ordinary folder. This does not create or modify Botainer configuration."}

    def create_project(self, data):
        required = {"rootId", "installationId", "path", "name", "mode", "requestId"}
        if not isinstance(data, dict) or set(data) != required or data["installationId"] != self.profile.id or data["mode"] not in {"create", "open", "register"}:
            _refuse("project-choice-invalid")
        request = _request(data["requestId"])
        parts = _relative(data["path"])
        if not isinstance(data["name"], str):
            _refuse("label-invalid")
        name = _label(data["name"].strip() or parts[-1])
        with self._locked():
            state = self._load()
            previous = state["setup"].get(request)
            if previous:
                if previous["intent"] != data:
                    _refuse("request-id-conflict")
                return {"project": self._project_view(self._project(state, previous["projectId"]))}
            if any(s["requestId"] == request for s in state["sessions"]) or request in state["stops"]:
                _refuse("request-id-conflict")
            if len(state["projects"]) >= 2000:
                _refuse("project-limit")
            if data["mode"] == "create":
                with self._walk(data["rootId"], parts[:-1]) as parent:
                    os.mkdir(parts[-1], 0o700, dir_fd=parent)
            with self._walk(data["rootId"], parts) as fd:
                identity = [os.fstat(fd).st_dev, os.fstat(fd).st_ino]
            path = str(Path(self.profile.data["project_roots"][data["rootId"]]).joinpath(*parts))
            record = next((p for p in state["projects"] if p["path"] == path), None)
            if record is not None and record["identity"] != identity:
                _refuse("project-directory-changed")
            if record is None:
                record = {"id": "host-project-" + uuid.uuid4().hex, "name": name, "path": path,
                          "rootId": data["rootId"], "relativePath": data["path"], "identity": identity}
                state["projects"].append(record)
            state["setup"][request] = {"intent": data, "projectId": record["id"]}
            self._save(state)
            return {"project": self._project_view(record)}

    def start_session(self, project_id, request_id, agent=None):
        request = _request(request_id)
        chosen = agent if agent is not None else self.profile.data["default_agent"]
        if chosen not in self.profile.data["agents"]:
            _refuse("agent-unavailable")
        with self._locked(control=True):
            state = self._load()
            project = self._project(state, project_id)
            existing = next((s for s in state["sessions"] if s["requestId"] == request), None)
            if existing:
                if existing["projectId"] != project_id or existing["agent"] != chosen:
                    _refuse("request-id-conflict")
                self._refresh_session(existing)
                self._save(state)
                self._cleanup_dead(state)
                return {"session": self._session_view(existing)}
            if request in state["setup"] or request in state["stops"]:
                _refuse("request-id-conflict")
            self.profile.verify_agent(chosen)
            for old in state["sessions"]:
                self._refresh_session(old)
            self._save(state)
            self._cleanup_dead(state)
            if any(s["projectId"] == project_id and s["state"] == "unknown" for s in state["sessions"]):
                _refuse("unresolved-launch-no-retry")
            if len(state["sessions"]) >= 512:
                _refuse("session-history-limit")
            with self._project_fd(project):
                pass
            sid = uuid.uuid4().hex
            folder = Path(self.profile.data["control_root"]) / sid
            folder.mkdir(mode=0o700)
            item = {"id": sid, "requestId": request, "projectId": project_id, "agent": chosen,
                    "createdAt": _now(), "state": "dispatching", "nonce": uuid.uuid4().hex,
                    "controlIdentity": _identity(folder), "launchProfile": self.profile.fingerprint,
                    "executable": self.profile.data["agents"][chosen]["path"],
                    "sha256": self.profile.data["agents"][chosen]["sha256"]}
            state["sessions"].append(item)
            self._save(state)  # Dispatch uncertainty is durable before tmux runs.
            config = _CONFIG + "set-option -g @dashboard_owner " + item["nonce"] + "\n"
            fd = os.open(folder / "tmux.conf", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w") as stream:
                stream.write(config)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                # >=2 command arguments makes tmux exec directly; the helper
                # calls execve, supplies no shell, and never lowers permissions.
                self._tmux(item, "new-session", "-d", "-E", "-s", "dashboard-" + sid,
                           "-c", project["path"], "-x", "100", "-y", "30",
                           self.python, "-I", "-S", str(self.helper), chosen,
                           self.profile.data["agents"][chosen]["path"],
                           *(str(v) for v in project["identity"]),
                           self.profile.data["agents"][chosen]["sha256"], create=True)
                owner, status = self._observe(item, initial=True)
                item.update(owner=owner, state="running", startedAt=_now())
                project["lastLaunchAt"] = item["startedAt"]
                if status["dead"]:
                    item.update(state="failed" if status["exitCode"] or status["exitSignal"] else "stopped",
                                endedAt=_now(), exitCode=status["exitCode"], exitSignal=status["exitSignal"], cleanupState="pending")
            except (OSError, ValueError, InventoryError, BackendUnavailable):
                item["state"] = "unknown"
            self._save(state)
            self._cleanup_dead(state)
            return {"session": self._session_view(item)}

    def _session(self, state, namespace, runtime_id):
        if namespace != self.namespace or not isinstance(runtime_id, str) or not _RUN.fullmatch(runtime_id):
            _refuse("session-unregistered")
        item = next((s for s in state["sessions"] if s["id"] == runtime_id), None)
        if item is None:
            _refuse("session-unregistered")
        return item

    def attach(self, namespace, runtime_id, cols, rows):
        with self._locked(control=True):
            state = self._load()
            item = self._session(state, namespace, runtime_id)
            self._refresh_session(item)
            self._save(state)
            if item["state"] != "running":
                _refuse("session-not-running")
            try:
                writer = private_lock(self.root / (runtime_id + ".writer"))
            except BlockingIOError:
                _refuse("terminal-writer-busy")
            try:
                clients = self._tmux(item, "list-clients", "-F", "#{client_pid}").strip()
                if clients:
                    _refuse("terminal-already-attached")
                self._observe(item)
                folder = self._folder(item)
                client = PtyAttachment([self.profile.data["tmux"]["path"], "-N", "-S", str(folder / "s"),
                                        "attach-session", "-E", "-t", item["owner"]["sessionId"]],
                                       cwd=self.root, env=self.profile.environment, cols=cols, rows=rows, cooked=True)
            except BaseException:
                os.close(writer)
                raise
            return HostAttachment(client, writer)

    def stop_session(self, namespace, runtime_id, request_id):
        request = _request(request_id)
        with self._locked(control=True):
            state = self._load()
            item = self._session(state, namespace, runtime_id)
            previous = state["stops"].get(request)
            if previous:
                if previous["runtimeId"] != runtime_id:
                    _refuse("request-id-conflict")
                return {"session": self._session_view(item)}
            if request in state["setup"] or any(s["requestId"] == request for s in state["sessions"]):
                _refuse("request-id-conflict")
            self._refresh_session(item)
            if item["state"] != "running":
                _refuse("stop-owner-unavailable")
            if any(s["runtimeId"] == runtime_id and s["state"] == "unknown" for s in state["stops"].values()):
                _refuse("stop-outcome-unknown-no-retry")
            self._observe(item)
            state["stops"][request] = {"runtimeId": runtime_id, "state": "unknown"}
            self._save(state)
            try:
                self._tmux(item, "kill-pane", "-t", item["owner"]["paneId"])
                self._wait_pane_absent(item["owner"]["panePid"])
                # A successful exact-pane command is tmux's acknowledgement.
                # Never use kill-server or signal a stored operating-system PID.
                item.update(state="stopped", endedAt=_now(), endReason="explicit-stop")
                state["stops"][request]["state"] = "completed"
            except (OSError, ValueError, InventoryError, BackendUnavailable):
                item["state"] = "unknown"
            self._save(state)
            return {"session": self._session_view(item)}

    @staticmethod
    def _wait_pane_absent(pid):
        """Read-only disappearance check; never signal a recycled process ID.

        The original foreground process must be gone. A reused PID remains
        unknown. This does not claim cleanup of intentionally detached host
        descendants, which have ordinary account privileges.
        """
        deadline = time.monotonic() + 2
        while True:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            except PermissionError:
                _refuse("stop-process-identity-unavailable")
            if time.monotonic() >= deadline:
                _refuse("stop-process-still-observed")
            time.sleep(.05)

    def list_files(self, project_id, path=""):
        parts = _relative(path, empty=True)
        with self._locked():
            state = self._load()
            project = self._project(state, project_id)
            with self._project_fd(project, parts) as fd:
                entries, excluded, truncated = [], 0, False
                with os.scandir(fd) as scan:
                    for count, entry in enumerate(scan):
                        if count >= 5000 or len(entries) >= 500:
                            truncated = True
                            break
                        info = entry.stat(follow_symlinks=False)
                        if not _visible(entry.name) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)) or stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                            excluded += 1
                            continue
                        entries.append({"name": entry.name, "path": "/".join((*parts, entry.name)),
                                        "kind": "directory" if stat.S_ISDIR(info.st_mode) else "file",
                                        "size": info.st_size if stat.S_ISREG(info.st_mode) else None})
                entries.sort(key=lambda e: (e["kind"] != "directory", e["name"].casefold()))
                return {"path": path, "entries": entries, "excluded": excluded, "truncated": truncated}

    def read_file(self, project_id, path):
        parts = _relative(path)
        if not _visible(parts[-1]):
            _refuse("file-access-excluded")
        with self._locked():
            project = self._project(self._load(), project_id)
            with self._project_fd(project, parts[:-1]) as fd:
                content = _read_at(fd, parts[-1])
                return {"path": path, "text": _decode(content), "revision": _revision(content), "readOnly": True}

    def close(self):
        """The HTTP service owns no durable agent process; nothing to terminate."""
