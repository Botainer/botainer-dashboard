"""Daily local workspace orchestration with an isolated shell runtime."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import threading

from .backend_errors import BackendUnavailable
from .config import config_format, parse_config_text, yaml_support_available, ConfigError
from .proof_backend import ProofBackend, digest, private_json
from .sandbox_runtime import SandboxRuntime, shell_template, validate_shell_config
from .workspace import (Workspace, WorkspaceRoot, WorkspaceInstallation, BotainerValidator,
                        WorkspaceError, _read_at, _atomic_at, _text)


class DashboardConfigEditor:
    """Exact text and compare-and-save; new installations activate on restart."""
    def __init__(self, path):
        self.path = Path(path).absolute() if path is not None else None

    @contextmanager
    def directory(self):
        if self.path is None or self.path.resolve() != self.path:
            raise WorkspaceError("explicit-regular-dashboard-config-required")
        fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: yield fd
        finally: os.close(fd)

    def read(self):
        with self.directory() as fd:
            data = _read_at(fd, self.path.name)
        result = {"text": data.decode("utf-8"), "revision": hashlib.sha256(data).hexdigest(),
                "path": str(self.path), "format": config_format(self.path), "restartRequired": True}
        if result["format"] == "yaml" and not yaml_support_available():
            result["writable"] = False
            result["readOnlyReason"] = "YAML settings require PyYAML in the dashboard environment; JSON settings remain supported."
        return result

    def validate(self, text, revision):
        _text(text)
        current = self.read()
        if revision != current["revision"]:
            raise WorkspaceError("config-revision-conflict")
        try:
            parse_config_text(text, self.path)
        except (ValueError, RecursionError, TypeError) as exc:
            return {"valid": False, "errors": [str(exc)[:1000]], "revision": revision,
                    "format": config_format(self.path)}
        return {"valid": True, "errors": [], "revision": revision, "format": config_format(self.path), "restartRequired": True,
                "warnings": ["Saved registration settings are read after restart only by launch modes that use this file. Separately selected runtime profiles and running sessions are unchanged."]}

    def save(self, text, revision):
        result = self.validate(text, revision)
        if not result["valid"]: return result | {"saved": False}
        with self.directory() as fd:
            current = _read_at(fd, self.path.name)
            if hashlib.sha256(current).hexdigest() != revision:
                raise WorkspaceError("config-revision-conflict")
            _atomic_at(fd, self.path.name, text.encode("utf-8"))
        return self.read() | result | {"saved": True, "text": text,
                                    "revision": hashlib.sha256(text.encode("utf-8")).hexdigest()}


class LocalBackend:
    def __init__(self, repo, config, *, native_cli=False):
        self.repo = Path(repo).resolve()
        self.native_cli = native_cli
        self.root = self.repo / (".local/native-workspace" if native_cli else ".local/workspace")
        for path in (self.root, self.root / "projects"):
            if path.is_symlink(): raise BackendUnavailable("workspace-root-symlink")
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.baseline = ProofBackend(self.repo / ".local/reviews/disposable-runtime-plan.json")
        if native_cli:
            from .native_cli_runtime import NativeSandboxRuntime
            self.runtime = NativeSandboxRuntime(self.root, self.baseline)
        else:
            self.runtime = SandboxRuntime(self.root, self.baseline)
        self.config = config
        self.config_editor = DashboardConfigEditor(config.source_path)
        self._thread_lock = threading.RLock()
        self._projects = {}
        helper = self.repo / "tools/workspace_botainer_helper.py"
        helper_digest = digest(helper)
        def verify_validator():
            self.baseline.verify_files()
            if digest(helper) != helper_digest:
                raise BackendUnavailable("validator-source-changed-restart-required")
        validator = BotainerValidator(interpreter=Path(self.baseline.plan["interpreter"]),
            source_root=Path(self.baseline.plan["source"]["private_copy"]), helper=helper,
            private_root=self.root / "validation", state_root=Path(self.baseline.plan["state_root"]),
            home=self.baseline.root, verify=verify_validator,
            input_policy=lambda raw: validate_shell_config(raw, self.runtime.plan))
        self.workspace = Workspace(private_root=self.root / "registry",
            roots=[WorkspaceRoot("local", "Local CLI trial projects" if native_cli else "Local prototype projects", self.root / "projects")],
            installations=[WorkspaceInstallation("shell", "Botainer CLI · local Alpine shell" if native_cli else "Isolated Botainer · local Alpine shell", validator,
                                                  shell_template(self.runtime.plan))],
            can_write=self._can_write)
        self._refresh_projects()

    @contextmanager
    def locked(self):
        with self._thread_lock:
            fd = os.open(self.root / "lifecycle.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077:
                    raise BackendUnavailable("workspace-lock-invalid")
                try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise BackendUnavailable("workspace-operation-in-progress") from None
                yield
            finally: os.close(fd)

    def _refresh_projects(self):
        self._projects = {p["id"]: p | {"uuid": p["projectUuid"]} for p in self.workspace.projects()}

    def _runtime(self, project_id, *, prepare=False):
        value = self.workspace.project(project_id)
        record = self.workspace._record(project_id)
        project = value | {"uuid": value["projectUuid"], "device": record["device"], "inode": record["inode"]}
        self._projects[project_id] = project
        if prepare: return self.runtime.prepare_project(project)
        path = self.root / "runtimes" / project["uuid"] / "prepared.json"
        return self.runtime.backend(project) if path.exists() else None

    def _can_write(self, project_id):
        # Called under Workspace's own file lock: never reacquire that lock.
        project = self._projects.get(project_id)
        if project is None: return False
        path = self.root / "runtimes" / project["uuid"]
        if not path.exists(): return True
        if not (path / "prepared.json").exists(): return False
        try:
            snap = self.runtime.backend(project).snapshot()
            if self.native_cli:
                return snap["capabilities"].get("configWrite", False)
            return snap["capabilities"]["startSession"] and all(s["state"] == "stopped" for s in snap["sessions"])
        except (BackendUnavailable, ValueError, OSError): return False

    def snapshot(self):
        with self.locked():
            self._refresh_projects()
            projects, sessions = [], []
            for original in self._projects.values():
                project = dict(original)
                project["capabilities"] = {**project["capabilities"], "startSession": True}
                if self.native_cli:
                    project["capabilities"]["nativeCliLaunch"] = True
                try:
                    backend = self._runtime(project["id"])
                    if backend:
                        snap = backend.snapshot()
                        sessions.extend(snap["sessions"])
                        # Known running shells may coexist; unresolved operations block.
                        from .operations import OperationStore
                        with OperationStore(backend.database) as store:
                            uncertain = bool(store.unresolved())
                        project["capabilities"]["startSession"] = not uncertain and all(s["state"] in {"running", "stopped"} for s in snap["sessions"])
                        project["capabilities"]["configWrite"] = not uncertain and all(s["state"] == "stopped" for s in snap["sessions"])
                        if self.native_cli:
                            project["capabilities"]["startSession"] = snap["capabilities"]["startSession"]
                            project["capabilities"]["configWrite"] = snap["capabilities"].get("configWrite", False)
                except (BackendUnavailable, ValueError, OSError) as exc:
                    project["capabilities"].update(startSession=False, configWrite=False)
                    project["unavailableReason"] = str(exc)
                projects.append(project)
            return {"mode": "native-cli-workspace" if self.native_cli else "local-workspace", "notice": ("Local CLI shell trial: Botainer shows its own warnings and prompts. Cached Alpine, network off, no agent credentials; one active session per project." if self.native_cli else "Local shell prototype: cached Alpine, network off, no agent credentials. Files and config are real; existing Botainer installations are unchanged."),
                "capabilities": {"projectCreate": True, "projectRegister": True, "filesRead": True, "configRead": True,
                    "configWrite": True, "startSession": True, "attachTerminal": True, "stopSession": True,
                    "dashboardConfigRead": self.config.source_path is not None, "dashboardConfigWrite": self.config.source_path is not None},
                "projects": projects, "sessions": sessions,
                "installations": [{"id": f"{machine_id}.{install_id}", "name": install_id, "machine": machine_id,
                    "transport": machine.transport, "executable": install.launcher.path or "Default Botainer location",
                    "stateRoot": install.state_root, "state": "configured; separate from prototype runtime"}
                    for machine_id, machine in self.config.machines.items() for install_id, install in machine.installations.items()]}

    def workspace_metadata(self): return self.workspace.metadata()

    def create_project(self, data):
        with self.locked():
            project = self.workspace.create_project(mode=data["mode"], root_id=data["rootId"], path=data["path"],
                name=data["name"], installation_id=data["installationId"], request_id=data["requestId"])
            self._refresh_projects()
            return {"project": project}

    def list_files(self, project_id, path): return self.workspace.list_files(project_id, path)
    def read_file(self, project_id, path): return self.workspace.read_file(project_id, path)
    def read_config(self, project_id):
        with self.locked():
            self._refresh_projects()
            return self.workspace.read_config(project_id)
    def validate_config(self, project_id, text, revision):
        return self.workspace.validate_config(project_id, text, revision)
    def save_config(self, project_id, text, revision):
        with self.locked():
            self._refresh_projects()
            return self.workspace.save_config(project_id, text, revision)
    def dashboard_config(self): return self.config_editor.read()
    def validate_dashboard_config(self, text, revision): return self.config_editor.validate(text, revision)
    def save_dashboard_config(self, text, revision):
        with self.locked(): return self.config_editor.save(text, revision)

    def start_session(self, project_id, request_id):
        with self.locked():
            backend = self._runtime(project_id, prepare=True)
            return backend.start_session(project_id, request_id)

    def _target(self, namespace, runtime_id):
        self._refresh_projects()
        for project in self._projects.values():
            backend = self._runtime(project["id"])
            if backend and backend.namespace == namespace:
                backend._record(runtime_id)
                return backend
            if backend and self.native_cli and backend.launch_namespace == namespace:
                backend.validate_launch(runtime_id)
                return backend
        raise BackendUnavailable("workspace-session-unregistered")

    def attach(self, namespace, runtime_id, cols, rows):
        with self.locked(): return self._target(namespace, runtime_id).attach(namespace, runtime_id, cols, rows)
    def stop_session(self, namespace, runtime_id, request_id):
        with self.locked(): return self._target(namespace, runtime_id).stop_session(namespace, runtime_id, request_id)
