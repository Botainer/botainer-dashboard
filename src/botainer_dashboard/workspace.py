"""Bounded local project registration, text access and exact-text config saves.

The trusted launcher supplies allowed roots and reviewed validators. Browser
requests never select an executable, state root, host path or subprocess option.
All project traversal uses directory descriptors and refuses symbolic links.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import threading
from typing import Callable
from uuid import UUID, uuid4

from .inventory import bounded_run, InventoryError
from .backend_errors import BackendUnavailable
from .config_recovery import preserve_config, ConfigRecoveryError

MAX_TEXT_BYTES = 64 * 1024
MAX_ENTRIES = 500
MAX_PROJECTS = 2000
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
_SECRET_NAMES = {"credential", "credentials", "secret", "secrets", "id_rsa", "id_ed25519", "id_ecdsa",
                 "id_dsa", "authorized_keys", "known_hosts", "token", "tokens", "password", "passwords"}
_SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".keystore", ".kdbx"}


class WorkspaceError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _identifier(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}", value):
        raise WorkspaceError("invalid-identifier")
    return value


def _relative(value: object, *, empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, str) or len(value) > 1024 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise WorkspaceError("invalid-relative-path")
    if value == "" and empty:
        return ()
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise WorkspaceError("invalid-relative-path") from None
    parts = value.split("/")
    if any(not p or p in (".", "..") or p.startswith(".") or "\\" in p for p in parts):
        raise WorkspaceError("invalid-relative-path")
    if len(parts) > 24 or any(len(p.encode()) > 240 for p in parts):
        raise WorkspaceError("invalid-relative-path")
    return tuple(parts)


def _visible(name: str) -> bool:
    lower = name.lower()
    return (not name.startswith(".") and lower not in _SECRET_NAMES
            and not any(lower.startswith(secret + ".") for secret in _SECRET_NAMES)
            and lower not in {"auth.json", "auth.yaml", "auth.yml", "login.json"}
            and PurePosixPath(lower).suffix not in _SECRET_SUFFIXES
            and not any(ord(c) < 32 or ord(c) == 127 for c in name))


def _text(value: object) -> bytes:
    if not isinstance(value, str) or "\x00" in value:
        raise WorkspaceError("utf8-text-required")
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        raise WorkspaceError("utf8-text-required") from None
    if len(encoded) > MAX_TEXT_BYTES:
        raise WorkspaceError("text-size-limit")
    return encoded


def _read_at(directory: int, name: str, limit: int = MAX_TEXT_BYTES) -> bytes:
    descriptor = os.open(name, _FILE_FLAGS, dir_fd=directory)
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise WorkspaceError("regular-unlinked-file-required")
        content = handle.read(limit + 1)
        if len(content) > limit:
            raise WorkspaceError("text-size-limit")
        return content


def _decode(content: bytes) -> str:
    try:
        text = content.decode("utf-8")
    except UnicodeError:
        raise WorkspaceError("utf8-text-required") from None
    _text(text)
    return text


def _revision(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _atomic_at(directory: int, name: str, content: bytes, *, expected: bytes | None = None) -> None:
    temporary = ".dashboard-" + uuid4().hex + ".tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if expected is not None and _read_at(directory, name) != expected:
            raise WorkspaceError("config-revision-conflict")
        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass


@dataclass(frozen=True)
class WorkspaceRoot:
    id: str
    label: str
    path: Path
    # Internal exact-project grants are not parent folders for project setup.
    project_only: bool = False


@dataclass(frozen=True)
class WorkspaceInstallation:
    id: str
    label: str
    validate: Callable[[str, str | None], dict]
    initial_text: str


class Workspace:
    """Local-only workspace registry, independent of session lifetime.

    ``can_write(project_id)`` must return True only after the runtime owner has
    excluded live, queued and uncertain sessions. The wrapper should hold its
    owning-host lifecycle lock over save/start to prevent a launch/save race.
    """

    def __init__(self, *, private_root: Path, roots: list[WorkspaceRoot],
                 installations: list[WorkspaceInstallation],
                 can_write: Callable[[str], bool] | None = None):
        self.private_root = Path(private_root)
        self.private_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if (self.private_root.resolve() != self.private_root or
                self.private_root.stat().st_mode & 0o077):
            raise WorkspaceError("private-workspace-directory-required")
        self.roots = {_identifier(root.id): root for root in roots}
        self.installations = {_identifier(item.id): item for item in installations}
        if len(self.roots) != len(roots) or len(self.installations) != len(installations):
            raise WorkspaceError("duplicate-registration")
        self.root_identities = {}
        for root in roots:
            if not root.path.is_absolute() or root.path.resolve(strict=True) != root.path:
                raise WorkspaceError("canonical-root-required")
            with self._root_fd(root.id, check=False) as descriptor:
                info = os.fstat(descriptor)
                self.root_identities[root.id] = (info.st_dev, info.st_ino)
        for installation in installations:
            _text(installation.initial_text)
        self.can_write = can_write or (lambda _project_id: False)
        self.thread_lock = threading.RLock()

    def register_project_root(self, root: WorkspaceRoot) -> None:
        """Bind an already discovered project, never its parent or siblings.

        Only the trusted native-inventory adapter calls this method. Browser
        setup cannot create these grants or use them as creation roots.
        """
        if root.project_only is not True:
            raise WorkspaceError("exact-project-root-required")
        _identifier(root.id)
        with self.thread_lock:
            previous = self.roots.get(root.id)
            if previous is not None:
                if previous.path != root.path or not previous.project_only:
                    raise WorkspaceError("project-root-changed")
                with self._root_fd(root.id):
                    return
            if (not root.path.is_absolute() or root.path.resolve(strict=True) != root.path
                    or len(self.roots) >= MAX_PROJECTS + 32):
                raise WorkspaceError("canonical-root-required")
            descriptor = os.open(root.path, _DIR_FLAGS)
            try:
                info = os.fstat(descriptor)
                self.roots[root.id] = root
                self.root_identities[root.id] = (info.st_dev, info.st_ino)
            finally:
                os.close(descriptor)

    @contextmanager
    def _locked(self):
        with self.thread_lock:
            descriptor = os.open(self.private_root / "workspace.lock",
                                 os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077:
                    raise WorkspaceError("private-workspace-lock-required")
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
            except OSError:
                raise WorkspaceError("workspace-files-unavailable") from None
            finally:
                os.close(descriptor)

    @contextmanager
    def _root_fd(self, root_id: str, *, check: bool = True):
        try:
            root = self.roots[root_id]
        except (KeyError, TypeError):
            raise WorkspaceError("unknown-root") from None
        descriptor = os.open(root.path, _DIR_FLAGS)
        try:
            info = os.fstat(descriptor)
            if check and (info.st_dev, info.st_ino) != self.root_identities[root_id]:
                raise WorkspaceError("root-identity-changed")
            yield descriptor
        finally:
            os.close(descriptor)

    @contextmanager
    def _walk(self, root_id: str, parts: tuple[str, ...]):
        with self._root_fd(root_id) as root:
            descriptor = os.dup(root)
            try:
                for part in parts:
                    child = os.open(part, _DIR_FLAGS, dir_fd=descriptor)
                    os.close(descriptor)
                    descriptor = child
                yield descriptor
            finally:
                os.close(descriptor)

    def _registry(self) -> dict:
        with self._private_fd() as descriptor:
            try:
                content = _read_at(descriptor, "projects.json", 2 * 1024 * 1024)
            except FileNotFoundError:
                return {"version": 1, "projects": []}
        try:
            value = json.loads(content)
            if (set(value) != {"version", "projects"} or value["version"] != 1
                    or not isinstance(value["projects"], list) or len(value["projects"]) > MAX_PROJECTS):
                raise ValueError()
            expected = {"id", "rootId", "relativePath", "name", "installationId", "projectUuid", "device", "inode", "requestId"}
            for item in value["projects"]:
                if not isinstance(item, dict) or set(item) != expected:
                    raise ValueError()
                _identifier(item["id"]); _identifier(item["rootId"]); _identifier(item["installationId"])
                # Empty paths are valid only for exact-project roots; access
                # additionally enforces the loaded root type below.
                _relative(item["relativePath"], empty=True)
                UUID(item["projectUuid"])
                if type(item["device"]) is not int or type(item["inode"]) is not int:
                    raise ValueError()
            if len({item["id"] for item in value["projects"]}) != len(value["projects"]):
                raise ValueError()
            return value
        except (ValueError, TypeError, KeyError):
            raise WorkspaceError("workspace-registry-invalid") from None

    @contextmanager
    def _private_fd(self):
        descriptor = os.open(self.private_root, _DIR_FLAGS)
        try:
            yield descriptor
        finally:
            os.close(descriptor)

    def _persist(self, value: dict) -> None:
        with self._private_fd() as descriptor:
            _atomic_at(descriptor, "projects.json", json.dumps(value, ensure_ascii=True).encode())

    def _record(self, project_id: str) -> dict:
        for item in self._registry()["projects"]:
            if item["id"] == project_id:
                if item["installationId"] not in self.installations:
                    raise WorkspaceError("installation-unavailable")
                return item
        raise WorkspaceError("unknown-project")

    @contextmanager
    def _project_fd(self, record: dict):
        root = self.roots.get(record["rootId"])
        if root is None:
            raise WorkspaceError("unknown-root")
        if root.project_only and record["relativePath"] != "":
            raise WorkspaceError("exact-project-path-required")
        with self._walk(record["rootId"], _relative(record["relativePath"], empty=root.project_only)) as descriptor:
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) != (record["device"], record["inode"]):
                raise WorkspaceError("project-identity-changed")
            child = os.open(".botainer", _DIR_FLAGS, dir_fd=descriptor)
            try:
                uid = _read_at(child, "project-id", 128).decode("ascii").strip()
                if uid != record["projectUuid"]:
                    raise WorkspaceError("project-identity-changed")
            finally:
                os.close(child)
            yield descriptor

    def _view(self, record: dict) -> dict:
        installation = self.installations[record["installationId"]]
        return {key: record[key] for key in ("id", "name", "rootId", "installationId", "relativePath", "projectUuid")} | {
            "path": str(self.roots[record["rootId"]].path / record["relativePath"]),
            "machineLabel": "This machine", "installationLabel": installation.label,
            "capabilities": {"filesRead": True, "configRead": True, "configWrite": True,
                             "startSession": False, "stopSession": False, "attachTerminal": False}}

    def metadata(self) -> dict:
        with self.thread_lock:
            roots = [{"id": root.id, "label": root.label, "path": str(root.path)}
                     for root in self.roots.values() if not root.project_only]
        return {"roots": roots,
                "installations": [{"id": item.id, "label": item.label, "initialText": item.initial_text} for item in self.installations.values()],
                "limits": {"textBytes": MAX_TEXT_BYTES, "directoryEntries": MAX_ENTRIES},
                "notice": "New projects use the configured parent folders. File access stays within each registered project; hidden, credential and linked files are excluded."}

    def projects(self) -> list[dict]:
        with self._locked():
            return [self._view(item) for item in self._registry()["projects"]]

    def project(self, project_id: str) -> dict:
        with self._locked():
            record = self._record(project_id)
            with self._project_fd(record):
                return self._view(record)

    def create_project(self, *, mode: str, root_id: str, path: str, name: str,
                       installation_id: str, request_id: str = "") -> dict:
        if mode not in ("create", "register"):
            raise WorkspaceError("invalid-project-mode")
        parts = _relative(path)
        if root_id not in self.roots or installation_id not in self.installations:
            raise WorkspaceError("unknown-registration")
        if self.roots[root_id].project_only:
            raise WorkspaceError("project-root-not-for-setup")
        if not isinstance(name, str) or not name.strip() or len(name) > 160 or any(ord(c) < 32 for c in name):
            raise WorkspaceError("invalid-project-name")
        _text(name)
        if request_id:
            _identifier(request_id)
        installation = self.installations[installation_id]
        with self._locked():
            registry = self._registry()
            for record in registry["projects"]:
                same_target = record["rootId"] == root_id and record["relativePath"] == path and record["installationId"] == installation_id
                if request_id and record["requestId"] == request_id:
                    if not same_target or record["name"] != name.strip():
                        raise WorkspaceError("request-id-conflict")
                    with self._project_fd(record):
                        return self._view(record)
                if same_target:
                    raise WorkspaceError("project-already-registered")
            if len(registry["projects"]) >= MAX_PROJECTS:
                raise WorkspaceError("project-limit")
            if mode == "create":
                result = installation.validate(installation.initial_text, None)
                if result.get("valid") is not True:
                    raise WorkspaceError("initial-config-invalid")
                with self._walk(root_id, parts[:-1]) as parent:
                    try:
                        os.mkdir(parts[-1], 0o700, dir_fd=parent)
                    except FileExistsError:
                        raise WorkspaceError("project-folder-already-exists") from None
                    child = os.open(parts[-1], _DIR_FLAGS, dir_fd=parent)
                    try:
                        os.mkdir(".botainer", 0o700, dir_fd=child)
                        config_dir = os.open(".botainer", _DIR_FLAGS, dir_fd=child)
                        try:
                            _atomic_at(config_dir, "project-id", (str(uuid4()) + "\n").encode())
                            _atomic_at(config_dir, "config.yaml", _text(installation.initial_text))
                        finally:
                            os.close(config_dir)
                    finally:
                        os.close(child)
            with self._walk(root_id, parts) as descriptor:
                info = os.fstat(descriptor)
                config_dir = os.open(".botainer", _DIR_FLAGS, dir_fd=descriptor)
                try:
                    raw_uid = _read_at(config_dir, "project-id", 128).decode("ascii").strip()
                    uid = str(UUID(raw_uid))
                    if uid != raw_uid:
                        raise WorkspaceError("invalid-project-identity")
                    _decode(_read_at(config_dir, "config.yaml"))
                except (UnicodeError, ValueError) as exc:
                    if isinstance(exc, WorkspaceError):
                        raise
                    raise WorkspaceError("invalid-project-identity") from None
                finally:
                    os.close(config_dir)
                for item in registry["projects"]:
                    if item["projectUuid"] == uid and item["installationId"] == installation_id:
                        raise WorkspaceError("project-copy-identity-conflict")
                record = {"id": "project-" + uuid4().hex, "rootId": root_id, "relativePath": path,
                          "name": name.strip(), "installationId": installation_id,
                          "projectUuid": uid, "device": info.st_dev, "inode": info.st_ino,
                          "requestId": request_id}
            registry["projects"].append(record)
            self._persist(registry)
            return self._view(record)

    @contextmanager
    def _content_fd(self, project_id: str, parts: tuple[str, ...]):
        record = self._record(project_id)
        with self._project_fd(record) as project:
            descriptor = os.dup(project)
            try:
                for part in parts:
                    if not _visible(part):
                        raise WorkspaceError("file-access-excluded")
                    child = os.open(part, _DIR_FLAGS, dir_fd=descriptor)
                    os.close(descriptor)
                    descriptor = child
                yield descriptor
            finally:
                os.close(descriptor)

    def list_files(self, project_id: str, path: str = "") -> dict:
        parts = _relative(path, empty=True)
        with self._locked(), self._content_fd(project_id, parts) as descriptor:
            entries, excluded, scanned, truncated = [], 0, 0, False
            with os.scandir(descriptor) as scan:
                for entry in scan:
                    scanned += 1
                    if scanned > 5000 or len(entries) >= MAX_ENTRIES:
                        truncated = True
                        break
                    if not _visible(entry.name):
                        excluded += 1
                        continue
                    info = entry.stat(follow_symlinks=False)
                    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)) or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1):
                        excluded += 1
                        continue
                    entries.append({"name": entry.name, "path": "/".join((*parts, entry.name)),
                                    "kind": "directory" if stat.S_ISDIR(info.st_mode) else "file",
                                    "size": info.st_size if stat.S_ISREG(info.st_mode) else None})
            entries.sort(key=lambda item: (item["kind"] != "directory", item["name"].casefold()))
            return {"path": path, "entries": entries, "excluded": excluded, "truncated": truncated}

    def read_file(self, project_id: str, path: str) -> dict:
        parts = _relative(path)
        if not _visible(parts[-1]):
            raise WorkspaceError("file-access-excluded")
        with self._locked(), self._content_fd(project_id, parts[:-1]) as descriptor:
            content = _read_at(descriptor, parts[-1])
            return {"path": path, "text": _decode(content), "revision": _revision(content), "readOnly": True}

    @contextmanager
    def _config_fd(self, project_id: str):
        record = self._record(project_id)
        with self._project_fd(record) as project:
            descriptor = os.open(".botainer", _DIR_FLAGS, dir_fd=project)
            try:
                yield record, descriptor
            finally:
                os.close(descriptor)

    def read_config(self, project_id: str) -> dict:
        with self._locked(), self._config_fd(project_id) as (_record, descriptor):
            content = _read_at(descriptor, "config.yaml")
            return {"path": ".botainer/config.yaml", "text": _decode(content),
                    "revision": _revision(content), "writable": self.can_write(project_id)}

    def _validate(self, record: dict, descriptor: int, text: str, revision: str) -> tuple[dict, bytes]:
        encoded = _text(text)
        current = _read_at(descriptor, "config.yaml")
        if not isinstance(revision, str) or revision != _revision(current):
            raise WorkspaceError("config-revision-conflict")
        result = self.installations[record["installationId"]].validate(text, _decode(current))
        if not isinstance(result, dict) or type(result.get("valid")) is not bool:
            raise WorkspaceError("validator-result-invalid")
        return result | {"revision": revision, "textDigest": _revision(encoded)}, current

    def validate_config(self, project_id: str, text: str, revision: str) -> dict:
        with self._locked(), self._config_fd(project_id) as (record, descriptor):
            result, _current = self._validate(record, descriptor, text, revision)
            return result

    def save_config(self, project_id: str, text: str, revision: str) -> dict:
        with self._locked(), self._config_fd(project_id) as (record, descriptor):
            if not self.can_write(project_id):
                raise WorkspaceError("project-session-active-or-unknown")
            result, current = self._validate(record, descriptor, text, revision)
            if not result["valid"]:
                return result | {"saved": False}
            # Validation may run a child process. Check both revision and runtime
            # again before the replacement; the caller also owns its start lock.
            if current != _read_at(descriptor, "config.yaml"):
                raise WorkspaceError("config-revision-conflict")
            if not self.can_write(project_id):
                raise WorkspaceError("project-session-active-or-unknown")
            encoded = _text(text)
            try:
                recovery = preserve_config(self.private_root, record["projectUuid"], current, encoded)
            except ConfigRecoveryError as error:
                raise WorkspaceError(str(error)) from None
            # Recheck after lifecycle checks, recovery and staging. This catches
            # observed conflicts, but is not atomic against external editors.
            _atomic_at(descriptor, "config.yaml", encoded, expected=current)
            return result | {"saved": True, "revision": _revision(encoded), "text": text,
                             "recovery": recovery}


class BotainerValidator:
    """Run a fixed helper through an existing, identity-checked Botainer Python.

    ``verify`` is a trusted registration check, supplied by the launcher, which
    pins the interpreter, source tree, helper and relevant policy/plugin inputs.
    Construction never imports or executes the selected Botainer installation.
    """

    def __init__(self, *, interpreter: Path, source_root: Path, helper: Path,
                 private_root: Path, state_root: Path, home: Path,
                 verify: Callable[[], None],
                 model_policy: Callable[[dict], None] | None = None,
                 input_policy: Callable[[dict], None] | None = None):
        self.interpreter, self.source_root, self.helper = map(Path, (interpreter, source_root, helper))
        self.private_root, self.state_root, self.home = map(Path, (private_root, state_root, home))
        self.verify = verify
        self.model_policy = model_policy
        self.input_policy = input_policy
        if any(not item.is_absolute() for item in (self.interpreter, self.source_root, self.helper,
                                                  self.private_root, self.state_root, self.home)):
            raise WorkspaceError("validator-paths-must-be-absolute")
        self.private_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.private_root.resolve() != self.private_root or self.private_root.stat().st_mode & 0o077:
            raise WorkspaceError("private-validator-directory-required")
        self.tool_directory = self.private_root / "empty-tools"
        self.tool_directory.mkdir(mode=0o700, exist_ok=True)

    def __call__(self, text: str, previous_text: str | None) -> dict:
        _text(text)
        if previous_text is not None:
            _text(previous_text)
        self.verify()
        if self.tool_directory.is_symlink() or next(self.tool_directory.iterdir(), None) is not None:
            raise WorkspaceError("validator-tool-path-changed")
        with tempfile.TemporaryDirectory(prefix="validation-", dir=self.private_root) as folder:
            # -B disables writes, not reads. A fresh private cache namespace also
            # prevents installed bytecode from overriding the reviewed source.
            cache = Path(folder) / "empty-pycache"
            cache.mkdir(mode=0o700)
            request = Path(folder) / "request.json"
            with request.open("x", encoding="utf-8") as handle:
                os.chmod(request, 0o600)
                json.dump({"sourceRoot": str(self.source_root), "text": text, "previousText": previous_text}, handle)
            try:
                output = bounded_run((str(self.interpreter), "-I", "-B", "-X", "pycache_prefix=" + str(cache),
                                      str(self.helper), str(request)),
                                     cwd=Path(folder), env={"HOME": str(self.home), "MY_BOTAINER": str(self.state_root),
                                     "PATH": str(self.tool_directory), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "NO_COLOR": "1"})
                if len(output) > 512 * 1024:
                    raise WorkspaceError("validator-output-limit")
                result = json.loads(output)
            except (InventoryError, UnicodeError, ValueError, OSError):
                raise WorkspaceError("botainer-validator-unavailable") from None
        self.verify()
        if not isinstance(result, dict) or type(result.get("valid")) is not bool:
            raise WorkspaceError("validator-result-invalid")
        model = result.pop("model", None)
        raw = result.pop("raw", None)
        for policy, value in ((self.input_policy, raw), (self.model_policy, model)):
            if result["valid"] and policy is not None:
                if not isinstance(value, dict):
                    raise WorkspaceError("validator-result-invalid")
                try:
                    policy(value)
                except (ValueError, BackendUnavailable) as exc:
                    result["valid"] = False
                    result.setdefault("errors", []).append(str(exc)[:1000])
        return result
