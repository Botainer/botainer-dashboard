"""Opt-in, read-only inventory for one explicitly reviewed local Botainer.

Importing or constructing this module never executes Botainer. The launcher must
select this backend explicitly and supply a reviewed identity manifest. The
fixed CLI calls run with an empty private PATH, so status cannot consult ambient
Docker/Slurm tools. Upstream liveness booleans are therefore observations only;
this adapter never represents them as proven running/stopped lifecycle state.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import threading
import time
from types import MappingProxyType
from typing import Callable, Mapping
from uuid import UUID

from .backend_errors import BackendUnavailable
from .context import ExecutionContext

MAX_STDOUT_BYTES = 4 * 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024
MAX_IDENTITY_BYTES = 32 * 1024 * 1024
COMMAND_TIMEOUT = 8.0
MAX_RECORDS = 10000
CAPABILITIES = {name: False for name in (
    "startSession", "stopSession", "attachTerminal", "configRead", "configWrite",
    "filesRead", "projectCreate",
)}


class InventoryError(RuntimeError):
    """A fixed public inventory failure, never subprocess output or raw paths."""

    def __init__(self, code: str):
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,79}", code):
            raise ValueError("fixed inventory error required")
        self.code = code
        super().__init__(code)


def _regular_bytes(path: Path, limit: int) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise InventoryError("identity-not-regular")
            content = handle.read(limit + 1)
    except OSError:
        raise InventoryError("identity-unreadable") from None
    if len(content) > limit:
        raise InventoryError("identity-size-limit")
    return content


def _hash(path: Path, limit: int = MAX_IDENTITY_BYTES) -> str:
    return hashlib.sha256(_regular_bytes(path, limit)).hexdigest()


def _absolute(path: Path) -> Path:
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("profile paths must be explicit absolute paths")
    return path


def _digest_map(value: Mapping, *, relative: bool) -> Mapping:
    result = {}
    for name, digest in value.items():
        path = Path(name)
        if (relative and (path.is_absolute() or ".." in path.parts)) or (not relative and not path.is_absolute()):
            raise ValueError("invalid reviewed identity path")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("reviewed SHA-256 required")
        result[str(path)] = digest
    if not result:
        raise ValueError("reviewed identity manifest cannot be empty")
    return MappingProxyType(result)


@dataclass(frozen=True)
class ReviewedInventoryProfile:
    context: ExecutionContext
    source_root: Path
    source_hashes: Mapping[str, str]
    executable_sha256: str
    interpreter_target: Path
    support_hashes: Mapping[str, str]
    state_identity: tuple[int, int]
    home: Path
    tool_directory: Path
    cwd: Path
    label: str
    version: str

    def __post_init__(self):
        if not isinstance(self.context, ExecutionContext) or self.context.launcher.mode != "python_module":
            raise ValueError("inventory requires an explicit reviewed Python-module launcher")
        for name in ("source_root", "interpreter_target", "home", "tool_directory", "cwd"):
            object.__setattr__(self, name, _absolute(getattr(self, name)))
        if not re.fullmatch(r"[0-9a-f]{64}", self.executable_sha256):
            raise ValueError("reviewed executable SHA-256 required")
        if (not isinstance(self.state_identity, tuple) or len(self.state_identity) != 2
                or any(type(value) is not int or value < 0 for value in self.state_identity)):
            raise ValueError("reviewed state device/inode required")
        for value in (self.label, self.version):
            if not isinstance(value, str) or not value or len(value) > 200 or any(ord(c) < 32 for c in value):
                raise ValueError("bounded profile label/version required")
        object.__setattr__(self, "source_hashes", _digest_map(self.source_hashes, relative=True))
        object.__setattr__(self, "support_hashes", _digest_map(self.support_hashes, relative=False))

    @property
    def namespace(self) -> str:
        payload = json.dumps(self.context.namespace_key, separators=(",", ":")).encode()
        return "ctx-" + hashlib.sha256(payload).hexdigest()

    @property
    def argv_prefix(self) -> tuple[str, ...]:
        return (str(self.context.launcher.executable), "-I", "-B", "-m", "botainer.cli.main")

    @property
    def environment(self) -> dict[str, str]:
        return {"HOME": str(self.home), "MY_BOTAINER": str(self.context.state_root),
                "PATH": str(self.tool_directory), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
                "NO_COLOR": "1"}

    def verify(self) -> None:
        """Recheck supplied identity, including additions and symlink replacements.

        The caller reviews the manifest; this method does not decide whether a
        source tree or editable loader is trustworthy merely because it hashes.
        """
        try:
            if self.context.launcher.executable.resolve(strict=True) != self.interpreter_target:
                raise InventoryError("interpreter-target-changed")
            if _hash(self.interpreter_target) != self.executable_sha256:
                raise InventoryError("interpreter-bytes-changed")
            root = self.context.state_root
            info = root.stat()
            if root.resolve(strict=True) != root or not stat.S_ISDIR(info.st_mode):
                raise InventoryError("state-root-changed")
            if (info.st_dev, info.st_ino) != self.state_identity:
                raise InventoryError("state-root-changed")
            if self.source_root.resolve(strict=True) != self.source_root:
                raise InventoryError("source-root-changed")
            observed = {}
            total = 0
            for current, directories, files in os.walk(self.source_root, followlinks=False):
                for name in directories:
                    if (Path(current) / name).is_symlink():
                        raise InventoryError("source-symlink")
                for name in files:
                    path = Path(current) / name
                    content = _regular_bytes(path, MAX_IDENTITY_BYTES - total)
                    total += len(content)
                    observed[str(path.relative_to(self.source_root))] = hashlib.sha256(content).hexdigest()
            if observed != self.source_hashes:
                raise InventoryError("source-tree-changed")
            for name, expected in self.support_hashes.items():
                if _hash(Path(name)) != expected:
                    raise InventoryError("installation-registration-changed")
            tool_info = self.tool_directory.stat()
            if (self.tool_directory.resolve(strict=True) != self.tool_directory
                    or not stat.S_ISDIR(tool_info.st_mode) or tool_info.st_uid != os.getuid()
                    or tool_info.st_mode & 0o077 or next(self.tool_directory.iterdir(), None) is not None):
                raise InventoryError("tool-path-not-empty-private")
            if not self.cwd.is_dir() or self.cwd.resolve(strict=True) != self.cwd:
                raise InventoryError("working-directory-changed")
            if not self.home.is_dir():
                raise InventoryError("home-unavailable")
        except (OSError, RuntimeError) as exc:
            if isinstance(exc, InventoryError):
                raise
            raise InventoryError("identity-unavailable") from None


def bounded_run(argv: tuple[str, ...], *, cwd: Path, env: Mapping[str, str],
                timeout: float = COMMAND_TIMEOUT) -> bytes:
    """Run only a caller-reviewed fixed command, bounding both pipes and lifetime."""
    if not isinstance(argv, tuple) or not argv or not Path(argv[0]).is_absolute():
        raise ValueError("explicit immutable argv required")
    if not 0 < timeout <= 30:
        raise ValueError("bounded timeout required")
    process = subprocess.Popen(argv, cwd=cwd, env=dict(env), stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                               close_fds=True, start_new_session=True)
    output, error = bytearray(), bytearray()
    selector = selectors.DefaultSelector()
    deadline = time.monotonic() + timeout
    try:
        for stream, name in ((process.stdout, "stdout"), (process.stderr, "stderr")):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise InventoryError("inventory-command-timeout")
            for key, _mask in selector.select(min(remaining, 0.1)):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                target, limit = ((output, MAX_STDOUT_BYTES) if key.data == "stdout" else (error, MAX_STDERR_BYTES))
                if len(target) + len(chunk) > limit:
                    raise InventoryError("inventory-command-output-limit")
                target.extend(chunk)
        # Normal exit is reaped once. Timeout/error cleanup below kills the
        # unreaped group before wait, so a recycled PID is never signalled.
        code = process.wait(timeout=max(0.1, deadline - time.monotonic()))
        if code != 0:
            raise InventoryError("inventory-command-failed")
        return bytes(output)
    except subprocess.TimeoutExpired:
        raise InventoryError("inventory-command-timeout") from None
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=2)


def _decode(content: bytes):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise InventoryError("inventory-json-ambiguous")
            result[key] = value
        return result
    if not isinstance(content, bytes) or len(content) > MAX_STDOUT_BYTES:
        raise InventoryError("inventory-command-output-limit")
    try:
        return json.loads(content.decode("utf-8"), object_pairs_hook=unique)
    except (ValueError, UnicodeError, RecursionError):
        raise InventoryError("inventory-json-invalid") from None


def _text(value, limit=1000) -> str:
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise InventoryError("inventory-schema-invalid")
    return value


def _uuid(value) -> str:
    value = _text(value, 36)
    try:
        if str(UUID(value)) != value:
            raise ValueError()
    except ValueError:
        raise InventoryError("inventory-project-identity-invalid") from None
    return value


def _project_location(raw: str, uuid: str) -> tuple[str, bool]:
    # This is the exact upstream sentinel for a registered record lacking path
    # history. Preserve that record without inventing a filesystem target.
    if raw == "(no path recorded)":
        return "", False
    path = Path(_text(raw, 4096))
    if not path.is_absolute() or ".." in path.parts:
        raise InventoryError("inventory-project-path-invalid")
    try:
        canonical = path.resolve(strict=False)
        identity = canonical / ".botainer" / "project-id"
        valid = (canonical.is_dir() and identity.resolve(strict=True).is_relative_to(canonical)
                 and _regular_bytes(identity, 256).decode("ascii").strip() == uuid)
        return str(canonical), valid
    except (OSError, UnicodeError, RuntimeError):
        return str(path), False


class InventoryBackend:
    def __init__(self, profile: ReviewedInventoryProfile, *, runner: Callable = bounded_run):
        self.profile = profile
        self._runner = runner
        self._lock = threading.Lock()
        self._previous: dict | None = None

    def _base(self) -> dict:
        profile = self.profile
        return {"mode": "inventory", "capabilities": dict(CAPABILITIES), "projects": [], "sessions": [],
                "notice": "Registered Botainer records only. Runtime liveness is unverified; session controls remain disabled.",
                "installations": [{"id": profile.context.context_id, "label": profile.label,
                                   "executable": str(profile.context.launcher.executable),
                                   "stateRoot": str(profile.context.state_root), "version": profile.version,
                                   "identity": "review-required"}]}

    def _call(self, arguments: tuple[str, ...]):
        self.profile.verify()
        value = self._runner(self.profile.argv_prefix + arguments, cwd=self.profile.cwd,
                             env=self.profile.environment, timeout=COMMAND_TIMEOUT)
        self.profile.verify()
        return _decode(value)

    def snapshot(self) -> dict:
        with self._lock:
            try:
                projects = self._call(("list", "--json"))
                status = self._call(("status", "--global", "--all", "--json"))
                value = self._normalize(projects, status)
                self._previous = deepcopy(value)
                return value
            except (InventoryError, OSError, ValueError) as exc:
                value = deepcopy(self._previous) if self._previous is not None else self._base()
                value["stale"] = True
                value["error"] = exc.code if isinstance(exc, InventoryError) else "inventory-unavailable"
                value["notice"] = "Inventory refresh failed. Previous records may be stale; runtime state is unknown and controls are disabled."
                value["installations"][0]["identity"] = "unverified"
                for session in value["sessions"]:
                    session["state"] = "unknown"
                    session["stale"] = True
                return value

    def _normalize(self, projects, status) -> dict:
        if (not isinstance(projects, list) or len(projects) > MAX_RECORDS or not isinstance(status, dict)
                or not isinstance(status.get("sessions"), list) or len(status["sessions"]) > MAX_RECORDS):
            raise InventoryError("inventory-schema-invalid")
        result = self._base()
        by_uuid = {}
        for row in projects:
            if not isinstance(row, dict):
                raise InventoryError("inventory-schema-invalid")
            uuid = _uuid(row.get("uuid"))
            if uuid in by_uuid:
                raise InventoryError("inventory-project-identity-ambiguous")
            path, verified = _project_location(row.get("last_path"), uuid)
            project_id = "project-" + hashlib.sha256((self.profile.namespace + ":" + uuid).encode()).hexdigest()
            project = {"id": project_id, "registeredUuid": uuid,
                       "name": _text(row.get("display_name") or Path(path).name or uuid[:8], 200), "path": path,
                       "pathIdentityVerified": verified, "contextNamespace": self.profile.namespace,
                       "machineLabel": self.profile.context.site_id, "installationLabel": self.profile.label,
                       # list.last_session_at is the latest recorded launch,
                       # not terminal activity or this poll's observation time.
                       "lastLaunchAt": _text(row.get("last_session_at") or "", 80),
                       # This is the recorded end of that same latest-started
                       # session. It may be a later reconciliation observation;
                       # neither an empty nor present value establishes liveness.
                       "lastSessionRecordedEndedAt": _text(row.get("last_session_ended_at") or "", 80),
                       # Compatibility for older consumers; new navigation uses
                       # the explicitly named lastLaunchAt field above.
                       "lastActiveAt": _text(row.get("last_session_at") or "", 80),
                       "capabilities": dict(CAPABILITIES)}
            by_uuid[uuid] = project
            result["projects"].append(project)
        seen = set()
        for row in status["sessions"]:
            if not isinstance(row, dict):
                raise InventoryError("inventory-schema-invalid")
            uuid = _uuid(row.get("project_uuid"))
            sid = _text(row.get("session_id"), 16)
            if not re.fullmatch(r"[0-9a-f]{16}", sid) or sid in seen or uuid not in by_uuid:
                raise InventoryError("inventory-session-identity-ambiguous")
            seen.add(sid)
            if type(row.get("alive")) is not bool:
                raise InventoryError("inventory-schema-invalid")
            result["sessions"].append({
                "contextNamespace": self.profile.namespace, "runtimeId": sid,
                "projectId": by_uuid[uuid]["id"], "label": sid, "state": "unknown",
                "reportedAlive": row["alive"], "livenessVerified": False,
                "runtime": _text(row.get("runtime"), 40), "createdAt": _text(row.get("started_at") or "", 80),
                "startedAt": _text(row.get("started_at") or "", 80),
                "recordedEndedAt": _text(row.get("ended_at") or "", 80),
                "capabilities": dict(CAPABILITIES),
            })
        result["stale"] = False
        result["observedAt"] = datetime.now(timezone.utc).isoformat()
        result["installations"][0]["identity"] = "reviewed-files-match"
        return result

    def start_session(self, project_id, request_id):
        raise BackendUnavailable("runtime-controls-not-proven")

    def stop_session(self, context_namespace, runtime_id, request_id):
        raise BackendUnavailable("runtime-controls-not-proven")

    def attach(self, context_namespace, runtime_id, cols, rows):
        raise BackendUnavailable("runtime-controls-not-proven")
