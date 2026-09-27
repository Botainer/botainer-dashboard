"""Ordinary local Botainer integration through its native, interactive CLI.

The private profile selects an existing installation, state root, project roots
and Docker daemon. It does not install anything. Runtime dispatch belongs to the
reviewed helper; browser requests supply identities and choices, never argv.
"""
from __future__ import annotations

from .layout import data_root

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import threading
import time
import uuid

from .backend_errors import BackendUnavailable
from .control_paths import ControlRootError, validate_local_control_root
from .inventory import bounded_run, InventoryError
from .import_policy import ImportPolicyError, source_modules, trusted_path
from .launch_console import LaunchConsole, private_lock, read_transcript, CONSOLE_LIMIT
from .pairing import private_directory, read_private_json, write_private_json
from .pty_bridge import PtyAttachment
from .project_boundaries import protected_local_paths, require_separate_project
from .workspace import (Workspace, WorkspaceRoot, WorkspaceInstallation, BotainerValidator,
                        WorkspaceError, _relative, _read_at, _decode, _revision)

SID = re.compile(r"[0-9a-f]{16}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")
AGENTS = {"claude", "codex"}
FINISHED = {"completed", "declined", "failed", "unknown"}
STARTUP_UNCONFIRMED = ("Botainer dispatched this startup, but its original container and writable terminal are not yet verified. "
    "The original launcher is retained; no replacement or automatic retry will be started.")
LAUNCH_FAILURES = {
    "ordinary-project-overlaps-protected-paths": (
        "The project or an extra mount overlaps dashboard or Botainer application/control files. "
        "No container was dispatched. Keep private data, terminal controls and installed host code "
        "outside container project folders and extra mounts; review this project's mount settings."),
    "ordinary-hook-review-required-no-installation-permitted": (
        "The dashboard has not enabled a startup or cleanup hook used by this project. "
        "No container was dispatched. This is a dashboard integration limit, not a request "
        "to install software. The launch log identifies the hook."),
    "ordinary-broker-owner-required": (
        "This project's credential broker needs a persistent Botainer launcher. The dashboard's "
        "current detached launch path cannot keep it alive reliably. No container was dispatched. "
        "The dashboard integration needs fixing; changing your project credentials is not required."),
    "ordinary-nested-screen-owner-unqualified": (
        "This project enables Botainer's Screen wrapper through nudge. That combination is not yet "
        "qualified with the dashboard's persistent launcher. No container was dispatched; "
        "the project settings were not changed."),
}


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 256 * 1024 * 1024:
            raise BackendUnavailable("ordinary-source-file-invalid")
        digest = hashlib.sha256()
        for chunk in iter(lambda: handle.read(1024 * 1024), b""): digest.update(chunk)
        return digest.hexdigest()


def request_id(value):
    try: valid = isinstance(value, str) and str(uuid.UUID(value)) == value
    except ValueError: valid = False
    if not valid:
        raise BackendUnavailable("ordinary-request-id-invalid")
    return value


def canonical_path(value, *, directory=True):
    if not isinstance(value, str) or not value.startswith("/") or any(ord(c) < 32 for c in value):
        raise BackendUnavailable("ordinary-profile-path-invalid")
    path = Path(value)
    if path.resolve(strict=True) != path or (directory and not path.is_dir()):
        raise BackendUnavailable("ordinary-profile-path-not-canonical")
    return path


def orphan_launch_evidence(profile, root, entry, project, binding, owner):
    """Qualify an ended native launcher independently from terminal attachment.

    This does not bless a new owner or run cleanup. The native receipt remains
    historical evidence, including older before-dispatch receipts without a
    container ID; the fresh native inventory must independently bind that SID.
    """
    request = request_id(entry["request_id"])
    sid = entry.get("native_session_id") or entry.get("result_target", {}).get("runtimeId")
    if (entry.get("operation") != "start" or not isinstance(sid, str) or not SID.fullmatch(sid)
            or entry.get("terminal_owner_id") != uuid.UUID(request).hex
            or not isinstance(binding, dict) or set(binding) != {"id", "name", "image", "created", "started_at"}
            or any(not isinstance(v, str) or not v for v in binding.values())
            or not HASH.fullmatch(binding["id"]) or binding["name"] != "/botainer-" + sid[:12]
            or binding["started_at"].startswith("0001-01-01T00:00:00")):
        raise BackendUnavailable("ordinary-orphan-evidence-invalid")
    intent_path = root / (request + ".intent.json")
    intent = read_private_json(intent_path)
    receipt_path = root / (request + ".result.json")
    receipt = read_private_json(receipt_path)
    if (sha(intent_path) != entry["intent_sha256"] or intent.get("action") != "start"
            or intent.get("request_id") != request or intent.get("profile_sha256") != profile.digest
            or intent.get("terminal_owner_id") != entry["terminal_owner_id"]
            or intent.get("project_uuid") != project["registeredUuid"]
            or intent.get("project_path") != project["path"] or entry.get("project_path") != project["path"]
            or receipt.get("request_id") != request or receipt.get("intent_sha256") != entry["intent_sha256"]
            or receipt.get("session_id") != sid
            or receipt.get("phase") not in {"before-dispatch", "dispatch-unconfirmed", "runtime-returned", "runtime-ended"}):
        raise BackendUnavailable("ordinary-orphan-evidence-invalid")
    path = canonical_path(project["path"])
    if [path.stat().st_dev, path.stat().st_ino] != intent.get("project_identity"):
        raise BackendUnavailable("ordinary-orphan-project-changed")
    candidate = receipt.get("candidate_binding")
    if (receipt.get("binding") is not None and receipt["binding"] != binding
            or candidate is not None and candidate != {k: binding[k] for k in ("id", "name", "image", "created")}):
        raise BackendUnavailable("ordinary-orphan-container-changed")
    record = owner.recover(entry["terminal_owner_id"])
    previous = entry.get("terminal_owner")
    if (not isinstance(previous, dict) or not previous.get("owner")
            or previous["owner"] != record.get("owner") or owner.observe(record)["state"] != "ended"):
        raise BackendUnavailable("ordinary-orphan-owner-not-ended")
    return {"session_id": sid, "launch_request_id": request, "launch_intent_sha256": entry["intent_sha256"],
        "launch_receipt_sha256": sha(receipt_path), "terminal_owner_id": entry["terminal_owner_id"],
        "project_identity": intent["project_identity"]}


class LaunchPhaseViewer:
    """Fence the consent-view input once this same owner becomes the session."""
    def __init__(self, client, receipt, entry, *, journal=None):
        self.client, self.receipt, self.entry, self.closed = client, receipt, entry, False
        self.journal = journal

    def _ready(self):
        try: result = read_private_json(self.receipt)
        except FileNotFoundError: return False
        if (result.get("request_id") != self.entry["request_id"]
                or result.get("intent_sha256") != self.entry["intent_sha256"]):
            raise BackendUnavailable("ordinary-launch-receipt-changed")
        phase = result.get("phase")
        if not isinstance(phase, str): raise BackendUnavailable("ordinary-launch-receipt-invalid")
        if phase in {"runtime-returned", "runtime-ended"}: return True
        if phase == "dispatch-unconfirmed" and self.journal is not None:
            operations = read_private_json(self.journal, max_bytes=2 * 1024 * 1024)
            entries = operations.get("operations")
            if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
                raise BackendUnavailable("ordinary-launch-journal-invalid")
            for entry in entries:
                if (entry.get("request_id") != self.entry["request_id"]
                        or entry.get("intent_sha256") != self.entry["intent_sha256"]): continue
                target = entry.get("result_target", {})
                if not isinstance(target, dict): raise BackendUnavailable("ordinary-launch-journal-invalid")
                return (entry.get("state") == "completed" and entry.get("startup_reconciled") is True
                    and target.get("runtimeId") == result.get("session_id"))
        return False

    def read(self, max_bytes, *, timeout=0):
        if self.closed: return b""
        if self._ready(): self.close(); return b""
        return self.client.read(max_bytes, timeout=timeout)

    def write(self, data):
        if self.closed or self._ready():
            self.close(); raise BrokenPipeError("Launch view has become a read-only log")
        return self.client.write(data)

    def resize(self, cols, rows):
        if not self.closed: self.client.resize(cols, rows)

    def close(self):
        if not self.closed:
            result = self.client.close(); self.closed = True; return result


class OrdinaryLocalProfile:
    """Explicit reviewed selection; hashes detect changes, not trustworthiness."""

    def __init__(self, path):
        self.path = Path(path).absolute()
        self.data = read_private_json(self.path, max_bytes=2 * 1024 * 1024)
        self.digest = sha(self.path)
        d = self.data
        required = {"version", "id", "label", "python", "source_root", "state_root", "home",
                    "project_roots", "docker", "environment", "source_hashes", "support_hashes"}
        if d.get("version") == 2:
            required.add("installation_layout")
        if (set(d) - required - {"approved_hooks", "directory_identities", "terminal_owner"} or required - set(d)
                or type(d["version"]) is not int or d["version"] not in {1, 2}
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", d["id"])):
            raise BackendUnavailable("ordinary-profile-invalid")
        if not isinstance(d["label"], str) or not 1 <= len(d["label"]) <= 160:
            raise BackendUnavailable("ordinary-profile-label-invalid")
        for name in ("source_root", "state_root", "home"):
            canonical_path(d[name])
        # Preserve a venv interpreter symlink: resolving it changes sys.prefix.
        python = Path(d["python"])
        if not python.is_absolute() or not python.is_file() or not os.access(python, os.X_OK):
            raise BackendUnavailable("ordinary-python-unavailable")
        if not isinstance(d["project_roots"], list) or not 1 <= len(d["project_roots"]) <= 32:
            raise BackendUnavailable("ordinary-project-roots-invalid")
        ids = set()
        for item in d["project_roots"]:
            if set(item) != {"id", "label", "path"} or item["id"] in ids:
                raise BackendUnavailable("ordinary-project-roots-invalid")
            canonical_path(item["path"]); ids.add(item["id"])
        docker = d["docker"]
        if (set(docker) != {"executable", "host", "daemon_id"} or not docker["host"].startswith("unix:///")
                or not docker["daemon_id"] or not Path(docker["executable"]).is_absolute()):
            raise BackendUnavailable("ordinary-docker-selection-invalid")
        for mapping in (d["source_hashes"], d["support_hashes"], d.get("approved_hooks", {})):
            if not isinstance(mapping, dict) or any(not isinstance(k, str) or not isinstance(v, str) or not HASH.fullmatch(v) for k, v in mapping.items()):
                raise BackendUnavailable("ordinary-source-manifest-invalid")
        if not d["source_hashes"] or not d["support_hashes"]:
            raise BackendUnavailable("ordinary-source-manifest-required")
        for name in d["source_hashes"]:
            p = Path(name)
            if p.is_absolute() or ".." in p.parts:
                raise BackendUnavailable("ordinary-source-manifest-invalid")
        source_pins = {str(Path(d["source_root"]) / name): digest
                       for name, digest in d["source_hashes"].items()}
        for name, digest in d.get("approved_hooks", {}).items():
            pins = {mapping[name] for mapping in (source_pins, d["support_hashes"]) if name in mapping}
            if pins != {digest}:
                raise BackendUnavailable("ordinary-hook-approval-must-match-reviewed-source")
            canonical_path(name, directory=False)
        self._verify_layout()
        env = d["environment"]
        if (not isinstance(env, dict) or set(env) - {"PATH", "LANG", "LC_ALL", "USER", "LOGNAME", "TMPDIR"}
                or not isinstance(env.get("PATH"), str)):
            raise BackendUnavailable("ordinary-environment-invalid")
        for part in env["PATH"].split(":"):
            if not part.startswith("/") or ".." in Path(part).parts:
                raise BackendUnavailable("ordinary-tool-path-invalid")
        self.python_target = python.resolve(strict=True)
        self.docker_target = Path(docker["executable"]).resolve(strict=True)
        for target in (self.python_target, self.docker_target):
            if str(target) not in d["support_hashes"]:
                raise BackendUnavailable("ordinary-interpreter-and-docker-review-required")
        self._verify_tool_selection()
        if "terminal_owner" in d:
            owner = d["terminal_owner"]
            if not isinstance(owner, dict) or set(owner) != {"path", "sha256", "control_root"}:
                raise BackendUnavailable("ordinary-terminal-owner-invalid")
            self._verify_control_root()
            canonical_path(owner["path"], directory=False)
            if not HASH.fullmatch(owner["sha256"]) or sha(Path(owner["path"])) != owner["sha256"]:
                raise BackendUnavailable("ordinary-terminal-owner-changed")
        identity = json.dumps([str(Path(d["home"])), str(Path(d["state_root"]))], separators=(",", ":"))
        self.namespace = "ordinary:" + hashlib.sha256(identity.encode()).hexdigest()[:40]
        self.launch_namespace = "launch:" + self.namespace.split(":", 1)[1]
        self.state_identity = tuple((Path(d["state_root"]).stat().st_dev, Path(d["state_root"]).stat().st_ino))

    def _verify_control_root(self):
        try:
            validate_local_control_root(self.data["terminal_owner"]["control_root"])
        except ControlRootError as error:
            raise BackendUnavailable("ordinary-" + error.code) from None

    @property
    def id(self): return self.data["id"]

    @property
    def label(self): return self.data["label"]

    @property
    def fingerprint(self): return self.digest

    @property
    def environment(self):
        return {**self.data["environment"], "HOME": self.data["home"], "MY_BOTAINER": self.data["state_root"],
                "DOCKER_HOST": self.data["docker"]["host"], "BOTAINER_NO_TIPS": "1", "TERM": "xterm-256color"}

    def verify(self):
        if sha(self.path) != self.digest:
            raise BackendUnavailable("ordinary-profile-changed-restart-required")
        root = canonical_path(self.data["state_root"])
        if (root.stat().st_dev, root.stat().st_ino) != self.state_identity:
            raise BackendUnavailable("ordinary-state-root-changed")
        self._verify_tool_selection()
        if "terminal_owner" in self.data:
            owner = self.data["terminal_owner"]
            self._verify_control_root()
            if sha(Path(owner["path"])) != owner["sha256"]:
                raise BackendUnavailable("ordinary-terminal-owner-changed")
        source = Path(self.data["source_root"])
        self._verify_layout()
        expected_python = {p for p in self.data["source_hashes"] if p.startswith("botainer/") and p.endswith(".py")}
        try:
            actual_python = source_modules(source)
        except ImportPolicyError as error:
            raise BackendUnavailable("ordinary-" + str(error)) from None
        if not expected_python or actual_python != expected_python:
            raise BackendUnavailable("ordinary-source-module-set-changed")
        for name, expected in self.data["source_hashes"].items():
            path = Path(self.data["source_root"]) / name
            if path.resolve() != path or sha(path) != expected:
                raise BackendUnavailable("ordinary-source-changed-requalification-required")
            self._verify_import_path(path)
        for name, expected in {**self.data["support_hashes"], **self.data.get("approved_hooks", {})}.items():
            if sha(Path(name)) != expected:
                raise BackendUnavailable("ordinary-installation-changed-requalification-required")
            # Docker Desktop normally lives below root:admin /Applications.
            # Its selected CLI remains canonical and hash-pinned; only that
            # platform-managed ancestor can use the narrowly scoped allowance.
            # Python, package inputs and hooks always retain the strict policy.
            docker_tool = (Path(name) == self.docker_target
                           and Path(name) != self.python_target
                           and name not in self.data.get("approved_hooks", {})
                           and not Path(name).is_relative_to(source))
            self._verify_import_path(Path(name), allow_macos_docker_app=docker_tool)
        for name, expected in self.data.get("directory_identities", {}).items():
            path = canonical_path(name)
            if [path.stat().st_dev, path.stat().st_ino] != expected:
                raise BackendUnavailable("ordinary-directory-identity-changed")

    @staticmethod
    def _verify_import_path(path, *, allow_macos_docker_app=False):
        try:
            trusted_path(path, allow_macos_docker_app=allow_macos_docker_app)
        except ImportPolicyError as error:
            raise BackendUnavailable("ordinary-" + str(error)) from None

    def _verify_tool_selection(self):
        docker = shutil.which("docker", path=self.data["environment"]["PATH"])
        if (Path(self.data["python"]).resolve(strict=True) != self.python_target
                or Path(self.data["docker"]["executable"]).resolve(strict=True) != self.docker_target
                or not docker or Path(docker).resolve(strict=True) != self.docker_target):
            raise BackendUnavailable("ordinary-native-tool-selection-changed")

    def _verify_layout(self):
        if self.data["version"] == 2:
            from .installation_layout import verify_layout
            try:
                verify_layout(Path(self.data["source_root"]), self.data["installation_layout"],
                              self.data["source_hashes"])
            except (OSError, ValueError, RuntimeError):
                raise BackendUnavailable("ordinary-installed-layout-changed-requalification-required") from None

    def verify_hook(self, path):
        path = Path(path)
        # A file identity manifest is not permission to execute every installed
        # hook. Source-prioritized copies need their own explicit hook approval.
        expected = self.data.get("approved_hooks", {}).get(str(path))
        if not expected or path.resolve() != path or sha(path) != expected:
            raise BackendUnavailable("ordinary-hook-review-required-no-installation-permitted")
        self._verify_import_path(path)


def load_local_profile(path):
    """Read and validate an existing profile; no process, runtime or connection."""
    return OrdinaryLocalProfile(path)


class OrdinaryLocalBackend:
    def __init__(self, repo, profile_path, *, runner=bounded_run, console_factory=LaunchConsole, data_dir=None,
                 protected_paths=()):
        self.repo = Path(repo).resolve()
        self.profile = OrdinaryLocalProfile(profile_path)
        self.namespace = self.profile.namespace
        self.launch_namespace = self.profile.launch_namespace
        self.root = private_directory(data_root(self.repo, data_dir) / "ordinary-local" / self.namespace.split(":", 1)[1])
        self.protected_paths = protected_local_paths(self.profile, self.repo,
            data_directory=data_root(self.repo, data_dir), extra=protected_paths)
        self._boundary_module = Path(__file__).with_name("project_boundaries.py")
        self._boundary_sha = sha(self._boundary_module)
        self._control_module = Path(__file__).with_name("control_paths.py")
        self._control_sha = sha(self._control_module)
        self._import_module = Path(__file__).with_name("import_policy.py")
        self._import_sha = sha(self._import_module)
        self.helper = self.repo / "tools/ordinary_local_helper.py"
        self._helper_sha = sha(self.helper)
        self._hook_helper = self.repo / "tools/ordinary_hook_helper.py"
        self._hook_helper_sha = sha(self._hook_helper)
        self._module_sha = sha(Path(__file__))
        self._validator_sha = sha(self.repo / "tools/workspace_botainer_helper.py")
        self.runner, self.console_factory = runner, console_factory
        self._lock = threading.RLock()
        self._consoles = {}
        self.owner = None
        if "terminal_owner" in self.profile.data:
            from .ordinary_owner import OrdinaryCliOwner
            selected = self.profile.data["terminal_owner"]
            self.owner = OrdinaryCliOwner(Path(selected["control_root"]), Path(selected["path"]),
                selected["sha256"], self.profile.environment)
            self._owner_module = Path(__file__).with_name("ordinary_owner.py")
            self._owner_sha = sha(self._owner_module)
        self._snapshot = None
        self._last = 0.
        self._bindings_path = self.root / "runtime-bindings.json"
        self._operations_path = self.root / "operations.json"
        self._recoveries_path = self.root / "orphan-recoveries.json"
        validator = BotainerValidator(interpreter=Path(self.profile.data["python"]),
            source_root=Path(self.profile.data["source_root"]), helper=self.repo / "tools/workspace_botainer_helper.py",
            private_root=self.root / "validation", state_root=Path(self.profile.data["state_root"]),
            home=Path(self.profile.data["home"]), verify=self.verify)
        self.workspace = Workspace(private_root=self.root / "workspace",
            roots=[WorkspaceRoot(item["id"], item["label"], Path(item["path"])) for item in self.profile.data["project_roots"]],
            installations=[WorkspaceInstallation(self.profile.data["id"], self.profile.data["label"], validator, "")],
            can_write=self._can_write)

    def verify(self):
        self.profile.verify()
        if (sha(self.helper) != self._helper_sha or sha(Path(__file__)) != self._module_sha
                or sha(self._boundary_module) != self._boundary_sha
                or sha(self._control_module) != self._control_sha
                or sha(self._import_module) != self._import_sha
                or sha(self._hook_helper) != self._hook_helper_sha
                or sha(self.repo / "tools/workspace_botainer_helper.py") != self._validator_sha
                or self.owner and sha(self._owner_module) != self._owner_sha):
            raise BackendUnavailable("ordinary-dashboard-helper-changed-restart-required")

    @contextmanager
    def locked(self):
        with self._lock:
            try: fd = private_lock(self.root / "operations.lock")
            except BlockingIOError:
                raise BackendUnavailable("ordinary-operation-in-progress") from None
            try: yield
            finally: os.close(fd)

    def _json(self, path, default):
        return read_private_json(path, max_bytes=2 * 1024 * 1024) if path.exists() else default

    def _operations(self):
        value = self._json(self._operations_path, {"operations": []})
        if not isinstance(value.get("operations"), list) or len(value["operations"]) > 2000:
            raise BackendUnavailable("ordinary-operation-history-invalid")
        return value

    def _recoveries(self):
        value = self._json(self._recoveries_path, {"recoveries": []})
        if (not isinstance(value.get("recoveries"), list) or len(value["recoveries"]) > 2000
                or any(not isinstance(entry, dict) for entry in value["recoveries"])):
            raise BackendUnavailable("ordinary-recovery-history-invalid")
        return value

    def _orphan_evidence(self, launch, project, binding):
        if (not self.owner or project["capabilities"].get("filesRead") is not True
                or any(e.get("session_id") == launch.get("native_session_id", launch.get("result_target", {}).get("runtimeId"))
                       for e in self._recoveries()["recoveries"])):
            raise BackendUnavailable("ordinary-orphan-recovery-unavailable")
        self.verify()
        return orphan_launch_evidence(self.profile, self.root, launch, project, binding, self.owner)

    def _call(self, action, data=None):
        self.verify()
        request = self.root / ("query-" + uuid.uuid4().hex + ".json")
        write_private_json(request, {**(data or {}), "action": action, "profile_sha256": self.profile.digest,
                                     "protected_paths": [str(p) for p in self.protected_paths]})
        try:
            output = self.runner((self.profile.data["python"], "-I", "-B", str(self.helper),
                str(self.profile.path), str(request)), cwd=self.root, env=self.profile.environment, timeout=30)
            result = json.loads(output)
            if not isinstance(result, dict): raise ValueError("invalid response")
            self.verify()
            return result
        finally:
            request.unlink(missing_ok=True)

    def _project_id(self, uid):
        return "project-" + hashlib.sha256((self.namespace + ":" + str(uuid.UUID(uid))).encode()).hexdigest()

    def _register(self, path, uid, name, *, discovered=False):
        path = canonical_path(str(path))
        require_separate_project(path, self.protected_paths)
        candidates = []
        for root_id, root in tuple(self.workspace.roots.items()):
            if root.project_only:
                continue
            try: relative = str(path.relative_to(root.path))
            except ValueError: continue
            try: parts = _relative(relative)
            except WorkspaceError: continue
            candidates.append((root_id, relative, parts))
        if not candidates:
            if not discovered:
                raise BackendUnavailable("ordinary-project-not-discovered")
            # Native inventory supplies the exact UUID/path association. Bind
            # only this directory; configured roots remain setup destinations.
            # Persist the canonical path in the root identity as well as UUID.
            # Otherwise a same-inode move would be silently accepted on restart.
            root_id = "native-" + hashlib.sha256((str(uuid.UUID(uid)) + ":" + str(path)).encode()).hexdigest()[:40]
            self.workspace.register_project_root(WorkspaceRoot(root_id, name, path, project_only=True))
            candidates.append((root_id, "", ()))
        for root_id, relative, parts in candidates:
            with self.workspace._walk(root_id, parts) as fd:
                info = os.fstat(fd)
                metadata = os.open(".botainer", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    if _read_at(metadata, "project-id", 128).decode().strip() != uid:
                        raise BackendUnavailable("ordinary-project-identity-mismatch")
                finally: os.close(metadata)
            with self.workspace._locked():
                registry = self.workspace._registry()
                ident = self._project_id(uid)
                previous = next((p for p in registry["projects"] if p["id"] == ident), None)
                item = {"id": ident, "rootId": root_id, "relativePath": relative, "name": name,
                        "installationId": self.profile.data["id"], "projectUuid": uid,
                        "device": info.st_dev, "inode": info.st_ino, "requestId": ""}
                if previous:
                    if any(previous[k] != item[k] for k in ("rootId", "relativePath", "device", "inode")):
                        raise BackendUnavailable("ordinary-project-moved-explicit-relink-required")
                    if previous["installationId"] != item["installationId"]:
                        raise BackendUnavailable("ordinary-project-installation-alias-changed-relink-required")
                    return self.workspace._view(previous)
                registry["projects"].append(item); self.workspace._persist(registry)
                return self.workspace._view(item)
        raise BackendUnavailable("ordinary-project-registration-unavailable")

    @staticmethod
    def _operation_project_id(entry):
        # Keep the original operation/receipt identity intact while routing a
        # completed setup transcript to its now-known canonical project. This
        # also handles completed journals written by the initial adapter.
        if entry["operation"] == "init" and entry["state"] == "completed" and entry.get("result_project_id"):
            return entry["result_project_id"]
        return entry["project_id"]

    def _launch_view(self, entry):
        active = entry["state"] not in FINISHED
        value = {"contextNamespace": self.launch_namespace, "runtimeId": entry["request_id"],
            "projectId": self._operation_project_id(entry), "kind": "launch", "operation": entry["operation"],
            "label": "Botainer project setup" if entry["operation"] == "init" else "Botainer launch",
            "agent": "Native CLI", "launchState": entry["state"],
            "state": "running" if active else "unknown" if entry["state"] == "unknown" else "stopped",
            "createdAt": entry["created_at"], "capabilities": {"attachTerminal": entry["request_id"] in self._consoles
                or (self.root / (entry["request_id"] + ".bin")).exists()
                or bool(entry.get("terminal_owner_id") and active and self.owner), "stopSession": False,
                "readTerminalHistory": bool(entry.get("terminal_owner_id") and self.owner)
                    or (self.root / (entry["request_id"] + ".bin")).exists()}}
        if entry.get("result_target"): value["resultTarget"] = entry["result_target"]
        if entry.get("result_project_id"): value["resultProjectId"] = entry["result_project_id"]
        if entry.get("reason"): value["launchReason"] = entry["reason"]
        return value

    def _owner_record(self, entry):
        if not self.owner or entry.get("terminal_owner_id") != uuid.UUID(entry["request_id"]).hex:
            raise BackendUnavailable("ordinary-terminal-owner-unavailable")
        record = self.owner.recover(entry["terminal_owner_id"])
        previous = entry.get("terminal_owner")
        if previous and previous.get("owner") and previous["owner"] != record.get("owner"):
            raise BackendUnavailable("ordinary-terminal-owner-changed")
        return record

    def _capture_owner(self, entry, record, *, allow_running=False):
        path = self.root / (entry["request_id"] + ".bin")
        if path.exists(): return
        captured = self.owner.capture(record, allow_running=allow_running)
        data = b"[dashboard] Saved terminal screen and scrollback snapshot.\r\n" + captured.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())

    def _reconcile(self, operations):
        changed = False
        for entry in operations["operations"]:
            if entry.get("orphan_recovery_request"): continue
            durable = bool(entry.get("terminal_owner_id"))
            if entry["state"] in FINISHED and not (durable and entry["state"] == "unknown"): continue
            owner_record, owner_status = None, None
            if durable:
                # Retain the attested native identity even while the terminal
                # owner is temporarily unavailable. Such a container must never
                # be mistaken for an external CLI run and attached directly.
                try:
                    hint = read_private_json(self.root / (entry["request_id"] + ".result.json"))
                    hinted = hint.get("session_id")
                    if (hint.get("request_id") == entry["request_id"] and hint.get("intent_sha256") == entry["intent_sha256"]
                            and isinstance(hinted, str) and SID.fullmatch(hinted) and hinted != entry.get("native_session_id")):
                        entry["native_session_id"] = hinted; changed = True
                except (OSError, ValueError, BackendUnavailable): pass
                try:
                    owner_record = self._owner_record(entry)
                    owner_status = self.owner.observe(owner_record)
                    if owner_status["state"] == "unknown":
                        raise BackendUnavailable("ordinary-terminal-owner-unverified")
                    if entry["state"] == "unknown" and owner_status["state"] == "running":
                        # Recover this exact persisted owner, never relaunch.
                        entry.update(state="waiting"); entry.pop("reason", None); changed = True
                    result_file = self.root / (entry["request_id"] + ".result.json")
                    if not result_file.exists() and owner_status["state"] == "running": continue
                except (OSError, ValueError, BackendUnavailable, InventoryError):
                    entry.update(state="unknown", reason="Persistent CLI owner cannot be verified; no automatic retry")
                    changed = True; continue
            console = self._consoles.get(entry["request_id"])
            if console and not console.done: continue
            if console and not console.cleanup_confirmed:
                entry.update(state="unknown", reason="CLI cleanup unconfirmed; no retry")
                changed = True; continue
            try:
                fd = private_lock(self.root / (entry["request_id"] + ".owner.lock"))
            except BlockingIOError: continue
            try:
                result = read_private_json(self.root / (entry["request_id"] + ".result.json"))
                if (result.get("request_id") != entry["request_id"] or result.get("intent_sha256") != entry["intent_sha256"]):
                    raise ValueError("result target changed")
                if not isinstance(result.get("phase"), str): raise ValueError("invalid result phase")
                if result.get("phase") == "not-dispatched":
                    if durable and owner_status["state"] == "running": continue
                    entry.update(state="declined" if result.get("exit_code") in (0, 3, 130) else "failed")
                    # Interpret only fixed diagnostics in a target-bound receipt,
                    # never terminal prose or arbitrary exception text.
                    failure = result.get("failure_code")
                    if entry["state"] == "failed" and isinstance(failure, str) and failure in LAUNCH_FAILURES:
                        entry.update(reason=LAUNCH_FAILURES[failure], failure_code=failure)
                elif entry["operation"] == "init" and result.get("phase") == "setup-failed":
                    entry.update(state="failed", reason="Native setup did not complete; inspect its transcript")
                elif durable and result.get("phase") == "dispatch-unconfirmed":
                    entry.update(state="waiting" if owner_status["state"] == "running" else "unknown",
                        reason=STARTUP_UNCONFIRMED)
                    if not isinstance(result.get("candidate_binding"), dict):
                        entry["reason"] += " No container identity was recorded; manual reconciliation is required."
                    changed = True
                    # Only fresh qualified inventory may resolve this receipt.
                    # Keep the native receipt intact and its owner alive.
                    if owner_status["state"] == "running": continue
                elif (result.get("phase") == "completed" and result.get("exit_code") == 0
                        or durable and result.get("phase") in {"runtime-returned", "runtime-ended"}):
                    if entry["operation"] == "init":
                        uid = str(uuid.UUID(result["project_uuid"]))
                        project = self._register(entry["project_path"], uid, entry["project_name"])
                        entry.update(state="completed", result_project_id=project["id"])
                    else:
                        sid = result["session_id"]
                        if not SID.fullmatch(sid): raise ValueError("invalid session")
                        binding = result["binding"]
                        if (not isinstance(binding, dict) or not HASH.fullmatch(binding.get("id", ""))
                                or binding.get("name") != "/botainer-" + sid[:12]):
                            raise ValueError("invalid runtime binding")
                        bindings = self._json(self._bindings_path, {"sessions": {}})
                        previous = bindings["sessions"].get(sid)
                        if previous is not None and previous != binding: raise ValueError("runtime binding changed")
                        bindings["sessions"][sid] = binding
                        write_private_json(self._bindings_path, bindings)
                        entry.update(state="completed", result_target={"contextNamespace": self.namespace, "runtimeId": sid})
                elif durable and owner_status["state"] == "running":
                    # Dispatch has begun but native runtime identity is not yet
                    # confirmed. Keep the same console, never submit again.
                    continue
                else:
                    raise ValueError("ambiguous CLI result")
            except (TypeError, ValueError, KeyError, OSError, BackendUnavailable, WorkspaceError):
                entry.update(state="unknown", reason="CLI outcome requires reconciliation; no automatic retry")
            finally: os.close(fd)
            if durable:
                try: self._capture_owner(entry, owner_record, allow_running=owner_status["state"] == "running")
                except (OSError, ValueError, BackendUnavailable, InventoryError):
                    # A log capture failure cannot erase proven dispatch or turn
                    # a real runtime into a safe-to-retry failed launch.
                    entry["log_capture_failed"] = True
            changed = True
        if changed: write_private_json(self._operations_path, operations)

    def _reconcile_startups(self, operations, raw, projects, bindings):
        """Resolve only an attested startup candidate, never infer a new owner."""
        changed = False
        for entry in operations["operations"]:
            if (entry.get("operation") != "start" or not entry.get("terminal_owner_id")
                    or entry["state"] not in {"waiting", "unknown"}): continue
            try:
                receipt = read_private_json(self.root / (entry["request_id"] + ".result.json"))
                if receipt.get("phase") != "dispatch-unconfirmed": continue
                sid, candidate = receipt.get("session_id"), receipt.get("candidate_binding")
                if (receipt.get("request_id") != entry["request_id"]
                        or receipt.get("intent_sha256") != entry["intent_sha256"]
                        or not isinstance(sid, str) or not SID.fullmatch(sid)
                        or receipt.get("cli_ended", False) is not False
                        or receipt.get("failure_code") not in {"ordinary-startup-timeout", "ordinary-startup-observation-failed"}
                        or not isinstance(candidate, dict) or set(candidate) != {"id", "name", "image", "created"}
                        or any(not isinstance(value, str) or not value for value in candidate.values())
                        or not HASH.fullmatch(candidate["id"]) or candidate["name"] != "/botainer-" + sid[:12]):
                    continue
                intent_path = self.root / (entry["request_id"] + ".intent.json")
                intent = read_private_json(intent_path)
                if (sha(intent_path) != entry["intent_sha256"] or intent.get("action") != "start"
                        or intent.get("request_id") != entry["request_id"]
                        or intent.get("profile_sha256") != self.profile.digest
                        or intent.get("terminal_owner_id") != entry["terminal_owner_id"]
                        or intent.get("project_path") != entry["project_path"]): continue
                matching_projects = [p for p in projects if p["id"] == entry["project_id"]]
                if len(matching_projects) != 1: continue
                project = matching_projects[0]
                if (project.get("registeredUuid") != intent.get("project_uuid")
                        or project.get("path") != entry["project_path"] or project.get("unavailableReason")
                        or project.get("capabilities", {}).get("filesRead") is not True): continue
                path = canonical_path(entry["project_path"])
                if [path.stat().st_dev, path.stat().st_ino] != intent.get("project_identity"): continue
                matches = [item for item in raw["sessions"] if item.get("session_id") == sid]
                if len(matches) != 1: continue
                session = matches[0]
                actual = session.get("binding")
                if (session.get("project_uuid") != project["registeredUuid"] or session.get("runtime") != "docker"
                        or session.get("state") != "running" or session.get("interactive") is not True
                        or session.get("ended_at") or not isinstance(actual, dict)
                        or any(actual.get(key) != value for key, value in candidate.items())
                        or not isinstance(actual.get("started_at"), str)
                        or actual["started_at"] in ("", "0001-01-01T00:00:00Z")): continue
                previous = bindings["sessions"].get(sid)
                if previous is not None and previous != actual:
                    # Older inventory code could retain a Docker-created row.
                    # Only its never-started timestamp may advance here; a
                    # previously running owner must never become a replacement.
                    if (not isinstance(previous, dict)
                            or any(previous.get(key) != value for key, value in candidate.items())
                            or previous.get("started_at") not in (None, "", "0001-01-01T00:00:00Z")): continue
                owner_record = self._owner_record(entry)
                if self.owner.observe(owner_record)["state"] != "running": continue
                bindings["sessions"][sid] = dict(actual)
                write_private_json(self._bindings_path, bindings)
                entry.update(state="completed", startup_reconciled=True, qualified_binding=dict(actual),
                    native_session_id=sid, result_target={"contextNamespace": self.namespace, "runtimeId": sid})
                entry.pop("reason", None)
                try: self._capture_owner(entry, owner_record, allow_running=True)
                except (OSError, ValueError, BackendUnavailable, InventoryError): entry["log_capture_failed"] = True
                changed = True
            except (OSError, ValueError, TypeError, KeyError, BackendUnavailable, InventoryError):
                continue  # Preserve the unresolved request; never retry dispatch.
        if changed: write_private_json(self._operations_path, operations)

    def _can_write(self, project_id):
        if not self._snapshot or self._snapshot.get("connectionStatus") != "available": return False
        project = next((p for p in self._snapshot["projects"] if p["id"] == project_id), None)
        if not project or not project["capabilities"].get("configWrite"): return False
        try:
            require_separate_project(project["path"], self.protected_paths)
            raw = self._call("inventory")
            uid = project["registeredUuid"]
            matches = []
            for item in raw["projects"]:
                try: candidate = str(uuid.UUID(item["uuid"]))
                except (ValueError, TypeError, AttributeError): continue
                if candidate == uid:
                    matches.append(item)
            if len(matches) != 1:
                return False
            current = matches[0]
            return current.get("last_path") == project["path"] and not current.get("runtime_unverified") and not any(s["project_uuid"] == uid
                and s["state"] not in {"stopped", "failed"} for s in raw["sessions"])
        except (OSError, ValueError, KeyError, StopIteration, BackendUnavailable, InventoryError): return False

    def _prune_consoles(self):
        for key, console in list(self._consoles.items()):
            if console.done and console.viewer is None and console.cleanup_confirmed:
                del self._consoles[key]

    def _writer_lock(self, runtime_id):
        try: return private_lock(self.root / (runtime_id + ".writer.lock"))
        except BlockingIOError: raise BackendUnavailable("ordinary-terminal-writer-busy") from None

    def snapshot(self):
        with self.locked():
            if self._snapshot is not None and self._last and time.monotonic() - self._last < 2:
                return json.loads(json.dumps(self._snapshot))
            operations = self._operations(); self._reconcile(operations)
            try:
                raw = self._call("inventory")
                projects, sessions = [], []
                if not isinstance(raw.get("projects"), list) or not isinstance(raw.get("sessions"), list):
                    raise ValueError("invalid inventory")
                registrations = {}
                for item in raw["projects"]:
                    try:
                        uid = str(uuid.UUID(item["uuid"]))
                        canonical = uid == item["uuid"]
                    except (ValueError, TypeError, AttributeError):
                        uid = str(item["uuid"]); canonical = False
                    if uid in registrations:
                        registrations[uid]["ambiguous"] = True
                        registrations[uid]["canonical"] |= canonical
                    else:
                        registrations[uid] = {"item": item, "canonical": canonical, "ambiguous": False}
                for uid, registration in registrations.items():
                    item, canonical = registration["item"], registration["canonical"]
                    ident = self._project_id(uid) if canonical else "legacy-" + hashlib.sha256((self.namespace + ":" + uid).encode()).hexdigest()
                    project = {"id": ident, "registeredUuid": uid, "name": item["display_name"],
                        "path": item["last_path"], "machineLabel": self.profile.data["label"],
                        "installationLabel": self.profile.data["id"], "contextNamespace": self.namespace,
                        "lastLaunchAt": item.get("last_session_at"), "capabilities": {"nativeCliLaunch": True,
                            "nativeCliProjectSetup": True, "agentOverride": True}}
                    try:
                        if registration["ambiguous"]:
                            raise BackendUnavailable("ordinary-project-registration-ambiguous")
                        if not canonical:
                            raise BackendUnavailable("ordinary-legacy-project-identity-read-only")
                        self._register(item["last_path"], uid, item["display_name"], discovered=True)
                        project["capabilities"].update(filesRead=True, configRead=True, configWrite=True, startSession=True)
                    except (OSError, ValueError, WorkspaceError, BackendUnavailable) as exc:
                        project["unavailableReason"] = "The registered project folder is missing, moved, linked or its Botainer identity has changed. Check its location in Botainer; no replacement folder is opened."
                        # Native runtime inventory succeeded. Folder eligibility
                        # controls actions, not the observed state of its sessions.
                        project["controlRestriction"] = "project-registration-unavailable"
                        if isinstance(exc, BackendUnavailable) and exc.code == "ordinary-project-registration-ambiguous":
                            project["unavailableReason"] = "Botainer reports multiple registrations for this project identity. Resolve the duplicate registration before opening files or controlling sessions."
                            project["controlRestriction"] = "project-registration-ambiguous"
                        elif isinstance(exc, BackendUnavailable) and exc.code == "ordinary-legacy-project-identity-read-only":
                            project["unavailableReason"] = "Legacy Botainer project identity: visible read-only; existing files were not changed"
                            project["identityCompatibility"] = "legacy-read-only"
                            project["controlRestriction"] = "legacy-project-identity"
                        elif isinstance(exc, BackendUnavailable) and exc.code == "ordinary-project-installation-alias-changed-relink-required":
                            project["unavailableReason"] = "Registered installation alias changed; explicit relinking is required"
                            project["controlRestriction"] = "installation-relink-required"
                        elif isinstance(exc, BackendUnavailable) and exc.code == "ordinary-project-overlaps-protected-paths":
                            project["unavailableReason"] = ("This folder overlaps dashboard or Botainer application/control files. "
                                "Opening it as a container project could expose trusted host files. Use a separate project folder; "
                                "keep application installations, private data and terminal control folders outside it.")
                            project["controlRestriction"] = "protected-project-path"
                        project["capabilities"].update(filesRead=False, configRead=False, configWrite=False, startSession=False)
                    if (item.get("runtime_unverified") and not registration["ambiguous"]
                            and project.get("controlRestriction") != "protected-project-path"):
                        project.pop("controlRestriction", None)
                        project["capabilities"].update(configWrite=False, startSession=False)
                        project["unavailableReason"] = "Runtime records or container ownership need reconciliation"
                    projects.append(project)
                known = {p["registeredUuid"]: p for p in projects}
                bindings = self._json(self._bindings_path, {"sessions": {}})
                self._reconcile_startups(operations, raw, projects, bindings)
                seen_sessions = {}
                for item in raw["sessions"]:
                    if item.get("project_uuid") not in known or not SID.fullmatch(item.get("session_id", "")):
                        raise ValueError("session identity invalid")
                    project = known[item["project_uuid"]]
                    sid = item["session_id"]; state = item.get("state", "unknown")
                    ambiguous = project.get("controlRestriction") == "project-registration-ambiguous"
                    if sid in seen_sessions:
                        if ambiguous and seen_sessions[sid] == project["id"]:
                            continue  # One unverified row, never duplicate routable identities.
                        raise ValueError("session identity repeated")
                    seen_sessions[sid] = project["id"]
                    if ambiguous:
                        state = "unknown"
                    launch = next((e for e in operations["operations"] if e.get("result_target", {}).get("runtimeId") == sid
                        or e.get("terminal_owner_id") and e.get("native_session_id") == sid), None)
                    unresolved_owner = any(e.get("terminal_owner_id") and e["project_id"] == project["id"]
                        and e["state"] not in {"completed", "declined", "failed"} for e in operations["operations"])
                    actual = item.get("binding")
                    previous = bindings["sessions"].get(sid)
                    if actual and previous and actual != previous:
                        state = "unknown"
                    elif actual and state == "running" and not unresolved_owner:
                        bindings["sessions"][sid] = actual
                    # Verified project registration bounds every mutation, not
                    # only file access. Missing/moved projects remain visible.
                    controllable = project["capabilities"].get("filesRead") is True
                    requested = launch.get("agent") if launch else None
                    sessions.append({"contextNamespace": self.namespace, "runtimeId": sid, "projectId": project["id"],
                        "label": sid, "agent": item.get("agent") or "Agent not recorded", "runtime": item["runtime"],
                        "requestedAgent": requested,
                        "state": state, "createdAt": item.get("started_at"), "startedAt": item.get("started_at"),
                        "recordedEndedAt": item.get("ended_at"), "capabilities": {
                            "attachTerminal": controllable and state == "running" and bool(actual) and item.get("interactive") is True,
                            "readTerminalHistory": controllable and bool(launch and launch.get("terminal_owner_id") and self.owner),
                            "stopSession": controllable and state == "running" and bool(actual)}})
                    if not launch or not launch.get("terminal_owner_id"):
                        sessions[-1]["capabilities"].update(attachTerminal=False, stopSession=False)
                        sessions[-1]["controlRestriction"] = "external-terminal-owner-unverified"
                        sessions[-1]["unavailableReason"] = ("This session was started elsewhere. Its original terminal owner "
                            "and cleanup ownership cannot be verified, so the dashboard cannot take over or stop it safely.")
                    if state == "running" and controllable and item.get("interactive") is not True:
                        sessions[-1]["unavailableReason"] = ("This container is running, but it was started without a writable "
                            "terminal. The dashboard cannot attach to this original session")
                    if launch and launch.get("terminal_owner_id") and state == "running":
                        try:
                            if launch["state"] != "completed": raise BackendUnavailable("ordinary-launch-unverified")
                            owner_status = self.owner.observe(self._owner_record(launch))
                            if owner_status["state"] != "running": raise BackendUnavailable("ordinary-terminal-owner-unavailable")
                        except (OSError, ValueError, BackendUnavailable, InventoryError):
                            sessions[-1]["capabilities"]["attachTerminal"] = False
                            sessions[-1]["capabilities"]["stopSession"] = False
                            sessions[-1]["unavailableReason"] = "The container is running, but its original Botainer CLI owner cannot be verified. No replacement agent will be started."
                            try:
                                if not controllable: raise BackendUnavailable("ordinary-project-control-scope-unavailable")
                                self._orphan_evidence(launch, project, actual)
                                if previous is not None and previous != actual:
                                    raise BackendUnavailable("ordinary-orphan-container-changed")
                                bindings["sessions"][sid] = dict(actual)
                                sessions[-1].update(stopMode="orphan-container", stopScope="container",
                                    containerId=actual["id"], containerName=actual["name"],
                                    controlRestriction="original-owner-ended",
                                    unavailableReason="The original Botainer launcher has ended. Its terminal cannot be recovered. "
                                        "Stop leftover container can stop this exact container; credential-helper cleanup remains unverified.")
                                sessions[-1]["capabilities"]["stopSession"] = True
                            except (OSError, ValueError, TypeError, KeyError, BackendUnavailable, InventoryError):
                                if any(e.get("session_id") == sid for e in self._recoveries()["recoveries"]):
                                    sessions[-1]["controlRestriction"] = "orphan-stop-unconfirmed"
                                    sessions[-1]["unavailableReason"] = ("A stop attempt for this container needs reconciliation. "
                                        "No stop or launch will be retried automatically.")
                    elif unresolved_owner:
                        sessions[-1]["capabilities"].update(attachTerminal=False, stopSession=False)
                        sessions[-1]["unavailableReason"] = ("A Botainer startup for this project is unverified. "
                            "The dashboard will not attach to a different terminal or start a replacement.")
                    if ambiguous:
                        sessions[-1]["capabilities"] = {key: False for key in sessions[-1]["capabilities"]}
                        sessions[-1]["controlRestriction"] = "project-registration-ambiguous"
                        sessions[-1]["unavailableReason"] = project["unavailableReason"]
                    if state not in {"stopped", "failed"}:
                        project["capabilities"].update(startSession=False, configWrite=False)
                        if state == "unknown":
                            project["startUnavailableReason"] = ("A session's state is unverified. Refresh and resolve that session before starting another.")
                        elif "startUnavailableReason" not in project:
                            project["startUnavailableReason"] = (
                                "A session is already running for this project outside the dashboard. Continue in its original terminal; finish or stop it there before starting another session here."
                                if sessions[-1].get("controlRestriction") == "external-terminal-owner-unverified" else
                                "A session is already running for this project. Select its running session to connect or inspect its status. End that session before starting another.")
                # Runtime launch completion is distinct from the native CLI's
                # eventual post-session cleanup. Do not let Docker disappearance
                # reopen a project while its previous hook owner still runs.
                for launch in operations["operations"]:
                    if not launch.get("terminal_owner_id") or launch["state"] != "completed": continue
                    project = next((p for p in projects if p["id"] == launch["project_id"]), None)
                    if project is None: continue
                    try: cleanup = self.owner.observe(self._owner_record(launch))["state"]
                    except (OSError, ValueError, BackendUnavailable, InventoryError): cleanup = "unknown"
                    if cleanup != "ended":
                        project["capabilities"].update(startSession=False, configWrite=False)
                        if (not project.get("unavailableReason") and
                                not any(s["projectId"] == project["id"] and s["state"] == "running" for s in sessions)):
                            project["unavailableReason"] = ("Botainer is finishing session cleanup. A new session can start after its original launcher ends."
                                if cleanup == "running" else "The previous Botainer launcher's cleanup status is unverified. Reconnect to its original owner before starting more work.")
                write_private_json(self._bindings_path, bindings)
                result = {"mode": "ordinary-local", "connectionStatus": "available", "lastObservedAt": now(),
                    "notice": "Existing Botainer installation. Native CLI warnings and prompts govern new runs.",
                    "projects": projects, "sessions": sessions, "capabilities": {"projectCreate": True,
                        "projectOpen": True, "projectRegister": True, "nativeCliProjectSetup": True},
                    "installations": [{"id": self.profile.data["id"], "name": self.profile.data["label"],
                        "transport": "local", "executable": self.profile.data["python"],
                        "stateRoot": self.profile.data["state_root"], "sourceRoot": self.profile.data["source_root"]}]}
            except (OSError, ValueError, KeyError, BackendUnavailable, InventoryError) as exc:
                result = json.loads(json.dumps(self._snapshot)) if self._snapshot else {"mode": "ordinary-local", "projects": [], "sessions": [], "installations": [], "capabilities": {}}
                result.update(connectionStatus="unavailable", notice="Local Botainer observation failed; existing work is unverified")
                result["unavailableReason"] = (str(exc) if isinstance(exc, (BackendUnavailable, InventoryError))
                    and re.fullmatch(r"[a-z][a-z0-9-]{0,100}", str(exc)) else "ordinary-inventory-invalid-or-unreadable")
                result["capabilities"] = {key: False for key in result["capabilities"]}
                for entity in result["projects"] + result["sessions"]:
                    entity["capabilities"] = {key: False for key in entity.get("capabilities", {})}
                    entity["stale"] = True
                    if "runtimeId" in entity: entity["state"] = "unknown"
            result["sessions"] = [s for s in result["sessions"] if s.get("kind") != "launch"]
            referenced_projects = {self._operation_project_id(e) for e in operations["operations"]}
            obsolete_setups = {e["project_id"] for e in operations["operations"]
                if self._operation_project_id(e) != e["project_id"]} - referenced_projects
            # A failed observation may have retained a pre-completion cache.
            # Remove only setup identities that no remaining operation needs.
            result["projects"] = [p for p in result["projects"] if p["id"] not in obsolete_setups]
            for entry in operations["operations"]:
                operation_project = self._operation_project_id(entry)
                if operation_project not in {p["id"] for p in result["projects"]}:
                    result["projects"].append({"id": operation_project, "name": entry["project_name"],
                        "path": entry["project_path"], "machineLabel": self.profile.data["label"],
                        "capabilities": {"nativeCliProjectSetup": True, "startSession": False, "configWrite": False}})
                if entry["state"] not in {"completed", "declined", "failed"}:
                    for project in result["projects"]:
                        if project["path"] == entry["project_path"]:
                            project["capabilities"].update(startSession=False, configWrite=False)
                if entry.get("helper_cleanup_unverified"):
                    for project in result["projects"]:
                        if project["path"] == entry["project_path"]:
                            project["capabilities"].update(startSession=False, configWrite=False)
                            project["controlRestriction"] = "cleanup-unverified"
                            project["unavailableReason"] = ("The leftover container is stopped, but credential-helper cleanup "
                                "has not been verified. Starting another session and saving configuration remain blocked until cleanup is reconciled.")
            result["sessions"].extend(self._launch_view(e) for e in operations["operations"][-64:])
            self._snapshot, self._last = result, time.monotonic()
            return json.loads(json.dumps(result))

    def _project(self, project_id):
        snapshot = self.snapshot()
        return next((p for p in snapshot["projects"] if p["id"] == project_id), None)

    def _start_console(self, *, operation, project, request, agent=None):
        request_id(request)
        if agent is not None and agent not in AGENTS: raise BackendUnavailable("ordinary-agent-unsupported")
        with self.locked():
            self.verify()
            operations = self._operations(); self._reconcile(operations)
            for existing in operations["operations"]:
                if existing["request_id"] == request:
                    if existing["project_path"] != project["path"] or existing["operation"] != operation or existing.get("agent") != agent:
                        raise BackendUnavailable("ordinary-request-id-conflict")
                    return {"session": self._launch_view(existing)}
            if any(e["project_path"] == project["path"] and e["state"] not in {"completed", "declined", "failed"} for e in operations["operations"]):
                raise BackendUnavailable("ordinary-project-operation-unresolved")
            if any(e["project_path"] == project["path"] and e.get("helper_cleanup_unverified") for e in operations["operations"]):
                raise BackendUnavailable("ordinary-project-cleanup-unverified")
            self._prune_consoles()
            if len(self._consoles) >= CONSOLE_LIMIT or len(operations["operations"]) >= 2000:
                raise BackendUnavailable("ordinary-history-capacity")
            p = canonical_path(project["path"]); info = p.stat()
            require_separate_project(p, self.protected_paths)
            intent = {"action": operation, "request_id": request, "profile_sha256": self.profile.digest,
                "project_path": str(p), "project_identity": [info.st_dev, info.st_ino],
                "project_uuid": project.get("registeredUuid"), "agent": agent,
                "config_revision": sha(p / ".botainer/config.yaml") if operation == "start" else None,
                "result_path": str(self.root / (request + ".result.json"))}
            intent["protected_paths"] = [str(p) for p in self.protected_paths]
            durable = self.owner is not None and operation == "start"
            if durable: intent["terminal_owner_id"] = uuid.UUID(request).hex
            path = self.root / (request + ".intent.json")
            if path.exists(): raise BackendUnavailable("ordinary-request-already-recorded")
            write_private_json(path, intent)
            entry = {"request_id": request, "operation": operation, "project_id": project["id"],
                "project_path": project["path"], "project_name": project["name"], "agent": agent,
                "state": "waiting", "created_at": now(), "intent_sha256": sha(path)}
            if durable:
                entry["terminal_owner_id"] = intent["terminal_owner_id"]
                operations["operations"].append(entry); write_private_json(self._operations_path, operations)
                try:
                    record = self.owner.launch(entry["terminal_owner_id"], [self.profile.data["python"], "-I", "-B",
                        str(self.helper), str(self.profile.path), str(path)], cwd=p, env=self.profile.environment)
                    entry["terminal_owner"] = record
                    if record.get("state") == "unknown":
                        entry.update(state="unknown", reason="Persistent CLI startup outcome unconfirmed; no automatic retry")
                except Exception:
                    entry.update(state="unknown", reason="Persistent CLI startup outcome unconfirmed; no automatic retry")
                    write_private_json(self._operations_path, operations)
                    raise
                write_private_json(self._operations_path, operations); self._last = 0
                return {"session": self._launch_view(entry)}
            owner_fd = private_lock(self.root / (request + ".owner.lock"))
            try:
                operations["operations"].append(entry); write_private_json(self._operations_path, operations)
                self._consoles[request] = self.console_factory([self.profile.data["python"], "-I", "-B", str(self.helper),
                    str(self.profile.path), str(path)], cwd=p, env=self.profile.environment,
                    transcript=self.root / (request + ".bin"), owner_fd=owner_fd)
            except Exception:
                os.close(owner_fd); entry.update(state="unknown", reason="CLI startup outcome unconfirmed")
                write_private_json(self._operations_path, operations); raise
            self._last = 0
            return {"session": self._launch_view(entry)}

    def start_session(self, project_id, request_id, agent=None):
        project = self._project(project_id)
        if not project or not project["capabilities"].get("startSession"):
            # An existing request is still returnable without another dispatch.
            with self.locked():
                for entry in self._operations()["operations"]:
                    if entry["request_id"] == request_id and entry["project_id"] == project_id:
                        if entry["operation"] != "start" or entry.get("agent") != agent: raise BackendUnavailable("ordinary-request-id-conflict")
                        return {"session": self._launch_view(entry)}
            raise BackendUnavailable("ordinary-project-active-or-unverified")
        return self._start_console(operation="start", project=project, request=request_id, agent=agent)

    def workspace_metadata(self):
        return self.workspace.metadata()

    def create_project(self, data):
        if data.get("mode") not in {"create", "open", "register"} or data.get("installationId") != self.profile.data["id"]:
            raise BackendUnavailable("ordinary-project-choice-invalid")
        parts = _relative(data["path"])
        name = data.get("name", "").strip() or parts[-1]
        if len(name) > 160 or any(ord(c) < 32 for c in name): raise BackendUnavailable("ordinary-name-invalid")
        root_id = data["rootId"]
        if root_id not in self.workspace.roots or self.workspace.roots[root_id].project_only:
            raise BackendUnavailable("ordinary-project-root-unregistered")
        request_id(data["requestId"])
        with self.locked():
            require_separate_project(self.workspace.roots[root_id].path.joinpath(*parts), self.protected_paths)
            for entry in self._operations()["operations"]:
                if entry["request_id"] == data["requestId"]:
                    expected = str(self.workspace.roots[root_id].path.joinpath(*parts))
                    if entry["project_path"] != expected or entry["operation"] != "init": raise BackendUnavailable("ordinary-request-id-conflict")
                    return {"project": {"id": self._operation_project_id(entry), "name": entry["project_name"], "path": expected}, "session": self._launch_view(entry)}
            if data["mode"] == "create":
                with self.workspace._walk(root_id, parts[:-1]) as parent:
                    os.mkdir(parts[-1], 0o700, dir_fd=parent)
            with self.workspace._walk(root_id, parts) as fd:
                path = self.workspace.roots[root_id].path.joinpath(*parts)
                try:
                    meta = os.open(".botainer", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                except FileNotFoundError:
                    uid = None
                else:
                    try:
                        uid = str(uuid.UUID(_read_at(meta, "project-id", 128).decode().strip()))
                        _read_at(meta, "config.yaml")
                    finally: os.close(meta)
            if uid:
                project = self._register(path, uid, name); self._last = 0
                return {"project": project}
            project = {"id": "setup-" + hashlib.sha256((self.namespace + str(path)).encode()).hexdigest()[:40],
                       "name": name, "path": str(path)}
        result = self._start_console(operation="init", project=project, request=data["requestId"])
        return {"project": project, **result}

    def attach(self, namespace, runtime_id, cols, rows):
        if namespace == self.launch_namespace:
            request_id(runtime_id)
            with self.locked():
                entry = next((e for e in self._operations()["operations"] if e["request_id"] == runtime_id), None)
                if entry is None:
                    raise BackendUnavailable("ordinary-launch-unregistered")
                fd = self._writer_lock(runtime_id)
                try:
                    if entry.get("terminal_owner_id") and entry["state"] not in FINISHED:
                        self.verify()
                        client = self.owner.attach(self._owner_record(entry), fd, cols, rows)
                        return LaunchPhaseViewer(client, self.root / (runtime_id + ".result.json"), entry,
                            journal=self._operations_path)
                    console = self._consoles.get(runtime_id)
                    if console is None:
                        self._prune_consoles()
                        if len(self._consoles) >= CONSOLE_LIMIT: raise BackendUnavailable("ordinary-console-capacity")
                        console = LaunchConsole.__new__(LaunchConsole)
                        console.condition = threading.Condition(); console.buffer = bytearray(read_transcript(self.root / (runtime_id + ".bin")))
                        console.base = 0; console.done = True; console.cleanup_confirmed = True; console.viewer = None
                        self._consoles[runtime_id] = console
                    viewer = console.attach(fd); viewer.resize(cols, rows); return viewer
                except Exception: os.close(fd); raise
        session, project, binding = self._target(namespace, runtime_id, "attachTerminal")
        with self.locked():
            entry = next((e for e in self._operations()["operations"]
                if e.get("result_target", {}).get("runtimeId") == runtime_id and e.get("terminal_owner_id")), None)
            fd = self._writer_lock(entry["request_id"] if entry else runtime_id)
            try:
                if entry:
                    self.verify()
                    return self.owner.attach(self._owner_record(entry), fd, cols, rows)
                raise BackendUnavailable("ordinary-external-terminal-owner-unverified")
            except Exception: os.close(fd); raise

    def _target(self, namespace, sid, capability):
        if namespace != self.namespace or not SID.fullmatch(sid): raise BackendUnavailable("ordinary-session-unregistered")
        self._last = 0; snapshot = self.snapshot()
        session = next((s for s in snapshot["sessions"] if s["runtimeId"] == sid and s["contextNamespace"] == namespace), None)
        if not session or not session["capabilities"].get(capability): raise BackendUnavailable("ordinary-session-unverified")
        project = next(p for p in snapshot["projects"] if p["id"] == session["projectId"])
        if not project["capabilities"].get("filesRead"):
            raise BackendUnavailable("ordinary-project-control-scope-unavailable")
        binding = self._json(self._bindings_path, {"sessions": {}})["sessions"][sid]
        return session, project, binding

    def stop_session(self, namespace, sid, request_id, *, expected_stop_mode="normal"):
        return self._stop_session(namespace, sid, request_id, expected_stop_mode)

    def stop_orphan_session(self, namespace, sid, request_id):
        return self.stop_session(namespace, sid, request_id, expected_stop_mode="orphan-container")

    def reconcile_orphan_stop(self, namespace, sid, request, evidence_path):
        """Local developer recovery, not an HTTP action or a runtime mutation.

        Import an explicit private stop receipt only after independently proving
        current absence/termination and the exact original launcher's exit.
        """
        request_id(request)
        path = canonical_path(str(Path(evidence_path).absolute()), directory=False)
        if not path.is_relative_to(self.repo / "private") or namespace != self.namespace or not SID.fullmatch(sid):
            raise BackendUnavailable("ordinary-orphan-reconciliation-evidence-invalid")
        proof = read_private_json(path)
        if (proof.get("stopped") is not True or proof.get("session_id") != sid
                or proof.get("daemon_id") != self.profile.data["docker"]["daemon_id"]):
            raise BackendUnavailable("ordinary-orphan-reconciliation-evidence-invalid")
        with self.locked():
            self.verify()
            operations = self._operations()
            entries = [e for e in operations["operations"] if e.get("request_id") == proof.get("launch_request_id")]
            if len(entries) != 1: raise BackendUnavailable("ordinary-orphan-reconciliation-evidence-invalid")
            entry = entries[0]
            intent = read_private_json(self.root / (entry["request_id"] + ".intent.json"))
            project = {"registeredUuid": intent["project_uuid"], "path": intent["project_path"]}
            evidence = orphan_launch_evidence(self.profile, self.root, entry, project, proof["binding"], self.owner)
            if any(evidence[k] != proof.get(k) for k in ("launch_request_id", "launch_intent_sha256", "launch_receipt_sha256")):
                raise BackendUnavailable("ordinary-orphan-reconciliation-evidence-invalid")
            previous = self._json(self._bindings_path, {"sessions": {}})["sessions"].get(sid)
            if previous is not None and previous != proof["binding"]:
                raise BackendUnavailable("ordinary-orphan-container-changed")
            recoveries = self._recoveries()
            existing = [e for e in recoveries["recoveries"]
                if e.get("request_id") == request or e.get("session_id") == sid]
            prior_attempt, helper_receipt_sha = None, None
            if existing:
                if (len(existing) != 1 or existing[0].get("request_id") != request
                        or existing[0].get("session_id") != sid or existing[0].get("namespace") != namespace
                        or existing[0].get("state") not in {"unknown", "dispatching"}
                        or existing[0].get("binding") != proof["binding"]
                        or entry.get("orphan_recovery_request") != request
                        or proof.get("recovery_request_id") != request
                        or any(existing[0].get(k) != v for k, v in evidence.items())):
                    raise BackendUnavailable("ordinary-orphan-recovery-conflict")
                helper_receipt_path = self.root / ("orphan-" + request + ".result.json")
                helper_receipt = read_private_json(helper_receipt_path)
                helper_receipt_sha = sha(helper_receipt_path)
                if (proof.get("helper_receipt_sha256") != helper_receipt_sha
                        or helper_receipt.get("request_id") != request or helper_receipt.get("session_id") != sid
                        or helper_receipt.get("binding") != proof["binding"]
                        or helper_receipt.get("phase") not in {"before-stop", "stop-unconfirmed", "container-stopped"}
                        or any(helper_receipt.get(k) != evidence[k]
                            for k in ("launch_request_id", "launch_intent_sha256", "launch_receipt_sha256"))):
                    raise BackendUnavailable("ordinary-orphan-recovery-conflict")
                prior_attempt = json.loads(json.dumps(existing[0]))
            elif len(recoveries["recoveries"]) >= 2000:
                raise BackendUnavailable("ordinary-history-capacity")
            result = self._call("orphan-observe-stopped", {"session_id": sid, "binding": proof["binding"],
                "project_uuid": project["registeredUuid"], "project_path": project["path"], **evidence})
            if (result.get("stopped") is not True or result.get("binding") != proof["binding"]
                    or result.get("session_id") != sid
                    or self.owner.observe(self._owner_record(entry))["state"] != "ended"):
                raise BackendUnavailable("ordinary-orphan-stop-unconfirmed")
            current = {"contextNamespace": namespace, "runtimeId": sid, "projectId": entry["project_id"],
                "state": "stopped", "stopMode": "orphan-container", "stopScope": "container",
                "capabilities": {"attachTerminal": False, "stopSession": False}}
            recovery = {"request_id": request, "namespace": namespace, "session_id": sid, "binding": proof["binding"],
                "state": "completed", "completed_at": now(), "external_evidence_sha256": sha(path),
                "result_session": current, "helper_cleanup_confirmed": False, **evidence}
            if prior_attempt is None:
                recoveries["recoveries"].append(recovery)
            else:
                # Preserve the ambiguous attempt and exact helper receipt; this
                # records a new read-only observation, never another dispatch.
                existing[0].update(recovery)
                existing[0].setdefault("reconciliations", []).append({"at": now(),
                    "prior_attempt": prior_attempt, "helper_receipt_sha256": helper_receipt_sha,
                    "external_evidence_sha256": sha(path), "observation": result})
            write_private_json(self._recoveries_path, recoveries)
            entry.update(state="failed", orphan_recovery_request=request, orphan_recovery_completed=True,
                helper_cleanup_unverified=True,
                reason="The leftover container was stopped. Its original launcher had ended; credential-helper cleanup remains unverified.")
            write_private_json(self._operations_path, operations)
            self._last = 0
            return {"session": current, "terminationConfirmed": True, "helperCleanupConfirmed": False}

    def _stop_session(self, namespace, sid, request, expected_stop_mode):
        request_id(request)
        if expected_stop_mode not in {"normal", "orphan-container"}:
            raise BackendUnavailable("ordinary-stop-mode-invalid")
        with self.locked():
            recoveries = self._recoveries()
            previous = next((e for e in recoveries["recoveries"] if e.get("request_id") == request), None)
            if previous:
                if expected_stop_mode != "orphan-container":
                    raise BackendUnavailable("ordinary-stop-mode-changed")
                if previous.get("namespace") != namespace or previous.get("session_id") != sid:
                    raise BackendUnavailable("ordinary-request-id-conflict")
                if previous.get("state") != "completed":
                    raise BackendUnavailable("ordinary-orphan-stop-unconfirmed")
                return {"session": previous["result_session"], "terminationConfirmed": True,
                        "helperCleanupConfirmed": False}
        session, project, binding = self._target(namespace, sid, "stopSession")
        if session.get("stopMode", "normal") != expected_stop_mode:
            raise BackendUnavailable("ordinary-stop-mode-changed")
        with self.locked():
            operations = self._operations()
            entry = next((e for e in operations["operations"]
                if (e.get("result_target", {}).get("runtimeId") == sid or e.get("native_session_id") == sid)
                and e.get("terminal_owner_id")), None)
            if session.get("stopMode") == "orphan-container":
                evidence = self._orphan_evidence(entry, project, binding)
                recoveries = self._recoveries()
                if len(recoveries["recoveries"]) >= 2000:
                    raise BackendUnavailable("ordinary-history-capacity")
                recovery = {"request_id": request, "namespace": namespace, "session_id": sid,
                    "binding": binding, "state": "dispatching", "created_at": now(), **evidence}
                recoveries["recoveries"].append(recovery)
                write_private_json(self._recoveries_path, recoveries)
                entry.update(state="unknown", orphan_recovery_request=request,
                    reason="Container stop outcome requires reconciliation; no automatic retry")
                write_private_json(self._operations_path, operations)
                try:
                    result = self._call("stop", {"stop_mode": "orphan-container", "request_id": request,
                        "session_id": sid, "project_uuid": project["registeredUuid"],
                        "project_path": project["path"], "binding": binding, **evidence})
                    receipt = read_private_json(self.root / ("orphan-" + request + ".result.json"))
                    if (receipt != result or result.get("request_id") != request
                            or result.get("session_id") != sid or result.get("binding") != binding
                            or result.get("stopped") is not True
                            or self.owner.observe(self._owner_record(entry))["state"] != "ended"):
                        raise BackendUnavailable("ordinary-orphan-stop-unconfirmed")
                    recovery.update(state="completed", completed_at=now(),
                        receipt_sha256=sha(self.root / ("orphan-" + request + ".result.json")))
                    current = dict(session, state="stopped", capabilities={**session["capabilities"], "stopSession": False})
                    recovery["result_session"] = current
                    entry.update(state="failed", reason="The leftover container was stopped. Its original launcher had ended; "
                        "credential-helper cleanup remains unverified.", orphan_recovery_completed=True,
                        helper_cleanup_unverified=True)
                    write_private_json(self._operations_path, operations)
                    write_private_json(self._recoveries_path, recoveries)
                    self._last = 0
                    return {"session": current, "terminationConfirmed": True, "helperCleanupConfirmed": False}
                except Exception:
                    recovery.update(state="unknown", reason="Container stop outcome requires reconciliation; no automatic retry")
                    write_private_json(self._recoveries_path, recoveries)
                    self._last = 0
                    raise BackendUnavailable("ordinary-orphan-stop-unconfirmed") from None
            owner_data = {"terminal_owner_id": entry["terminal_owner_id"], "launch_request_id": entry["request_id"],
                "launch_intent_sha256": entry["intent_sha256"]} if entry else {}
            result = self._call("stop", {"session_id": sid, "project_uuid": project["registeredUuid"],
                "project_path": project["path"], "binding": binding, **owner_data})
            self._last = 0
        snapshot = self.snapshot()
        current = next((s for s in snapshot["sessions"]
            if s["runtimeId"] == sid and s["contextNamespace"] == namespace), session)
        diagnostic = result.get("diagnostic", "")
        observation_error = result.get("observation_error")
        if (type(result.get("stopped")) is not bool or not isinstance(diagnostic, str)
                or observation_error not in {None, "ordinary-stop-observation-unconfirmed"}):
            raise BackendUnavailable("ordinary-stop-result-unconfirmed")
        # The helper already strips terminal syntax; bound and remove control
        # characters again at the browser DTO boundary. Never persist this text
        # into shared logs or use it to determine whether Stop succeeded.
        text = "".join(c for c in diagnostic[:4096] if c in "\n\t" or c.isprintable())
        stopped = result["stopped"] and observation_error is None
        if not stopped and current.get("state") in {"stopped", "failed"}:
            current = {**current, "state": "unknown", "capabilities": {
                **current.get("capabilities", {}), "attachTerminal": False, "stopSession": False}}
        return {"session": current, "terminationConfirmed": stopped,
            "helperCleanupConfirmed": False, "diagnostic": text,
            "diagnosticTruncated": result.get("diagnostic_truncated") is True or len(diagnostic) > 4096,
            "observationError": observation_error,
            "nativeExitCode": result.get("exit_code") if type(result.get("exit_code")) is int else None}

    def _check_project_boundary(self, project_id):
        with self.workspace._locked():
            record = self.workspace._record(project_id)
            root = self.workspace.roots.get(record["rootId"])
        if root is None:
            raise BackendUnavailable("ordinary-project-registration-unavailable")
        require_separate_project(root.path / record["relativePath"], self.protected_paths)
        return root

    def _check_discovered_project(self, project_id, capability):
        root = self._check_project_boundary(project_id)
        if root.project_only:
            # An observed registration can be removed between periodic polls.
            # Read-only access also needs a fresh native association, not just
            # the inventory cache that feeds the sidebar.
            with self._lock:
                self._last = 0
                project = self._project(project_id)
            if not project or not project["capabilities"].get(capability):
                raise BackendUnavailable("ordinary-project-registration-unavailable")

    def list_files(self, project_id, path):
        self._check_discovered_project(project_id, "filesRead")
        return self.workspace.list_files(project_id, path)

    def read_terminal_history(self, namespace, runtime_id):
        """Bounded read-only output; never starts, attaches or sends pane input."""
        if namespace == self.launch_namespace:
            request_id(runtime_id)
        elif namespace == self.namespace and isinstance(runtime_id, str) and SID.fullmatch(runtime_id):
            # Snapshot refresh takes the operation lock itself. Validate the
            # current runtime capability before acquiring it for the read.
            self._target(namespace, runtime_id, "readTerminalHistory")
        else: raise BackendUnavailable("ordinary-history-target-invalid")
        with self.locked():
            self.verify()
            entries = self._operations()["operations"]
            if namespace == self.launch_namespace:
                entry = next((e for e in entries if e["request_id"] == runtime_id), None)
            else:
                entry = next((e for e in entries if e.get("result_target", {}).get("runtimeId") == runtime_id
                    or e.get("native_session_id") == runtime_id), None)
            if entry is None: raise BackendUnavailable("ordinary-history-unavailable")
            saved = self.root / (entry["request_id"] + ".bin")
            if namespace == self.launch_namespace and entry["state"] in FINISHED and saved.exists():
                raw = read_transcript(saved)
            elif entry.get("terminal_owner_id"):
                record = self._owner_record(entry)
                try: raw = self.owner.capture(record, allow_running=True)
                except InventoryError:
                    raise BackendUnavailable("ordinary-history-capture-unavailable") from None
            elif saved.exists(): raw = read_transcript(saved)
            else: raise BackendUnavailable("ordinary-history-unavailable")
            truncated = len(raw) > 262144 or raw.startswith(b"[Earlier terminal output omitted.]")
            text = raw[-262144:].decode("utf-8", errors="replace")
            # Output is untrusted text. Strip terminal control syntax for the
            # plain-text inspector; it is never parsed as HTML or executed.
            text = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", text)
            text = re.sub(r"\x1b[PX^_][^\x1b]*(?:\x1b\\)", "", text)
            text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
            text = text.replace("\r\n", "\n").replace("\r", "\n")
            text = "".join(c for c in text if c in "\n\t" or ord(c) >= 32 and ord(c) != 127)
            encoded = text.encode("utf-8")
            if len(encoded) > 262144:
                text = encoded[-262144:].decode("utf-8", errors="ignore")
                truncated = True
            return {"text": text, "kind": "terminal-snapshot", "truncated": truncated}
    def read_file(self, project_id, path):
        self._check_discovered_project(project_id, "filesRead")
        return self.workspace.read_file(project_id, path)

    def read_config(self, project_id):
        self._check_discovered_project(project_id, "configRead")
        return self.workspace.read_config(project_id)

    def validate_config(self, project_id, text, revision):
        self._check_discovered_project(project_id, "configRead")
        return self.workspace.validate_config(project_id, text, revision)

    def save_config(self, project_id, text, revision):
        self._check_project_boundary(project_id)
        self._last = 0; self.snapshot()
        with self.locked():
            return self.workspace.save_config(project_id, text, revision)
