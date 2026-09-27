"""Independent owner-only service lifecycle mailbox; no HTTP or process signals.

The lifecycle lock remains held when this control channel fails. Pairing control
has its own lifetime and cannot prove whether the service is running or stopped.
All requests select one immutable run/lock identity, never a PID to signal.
"""
from __future__ import annotations

import fcntl
import math
import os
from pathlib import Path
import re
import secrets
import stat
import time

from .pairing import _private_fd, read_private_json, write_private_json


_ID = re.compile(r"[0-9a-f]{32}\Z")
_LIMIT = 4096
_MAX_WAIT = 10.0
_MAX_RUNS = 256
_BASE = {"version", "runId", "url", "pid", "mode", "ownerIdentity"}
_STATES = {"starting", "running", "stopping", "control-unavailable", "stopped"}
_REQUEST = {"version", "runId", "url", "ownerIdentity", "requestId", "action", "issuedAt", "expiresAt"}
_RESPONSE = {"version", "runId", "url", "ownerIdentity", "requestId", "action", "status"}


class ServiceControlError(ValueError):
    """A fixed owner-control error with no private payload in its message."""


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _timeout(value):
    if not _number(value) or not 0 < value <= _MAX_WAIT:
        raise ServiceControlError("Service request timeout must be greater than zero and at most ten seconds.")
    return float(value)


def _origin(value):
    if not isinstance(value, str):
        return False
    match = re.fullmatch(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})", value)
    return bool(match and int(match[1]) <= 65535)


def _identity(value):
    return (isinstance(value, list) and len(value) == 2
            and all(type(item) is int and 0 <= item <= 2 ** 64 - 1 for item in value))


def _directory(path, *, run=True):
    path = Path(path).absolute()
    if ".." in path.parts or (run and not _ID.fullmatch(path.name)):
        raise ServiceControlError("Invalid service control directory.")
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ServiceControlError("Service control paths must not contain symlinks.")
    info = path.stat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise ServiceControlError("Service control directory must be private and owner-only.")
    return path


def _exists(path):
    return path.exists() or path.is_symlink()


def _file_identity(path):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
        raise ServiceControlError("Service control requires private regular files.")
    return [info.st_dev, info.st_ino]


def _read(path):
    # Atomic publication can unlink the old inode between open and validation.
    # Retry only a different safe replacement, at most three times.
    for attempt in range(3):
        before = _file_identity(path)
        try:
            value = read_private_json(path, max_bytes=_LIMIT)
            if _file_identity(path) != before:
                if attempt == 2:
                    raise ServiceControlError("Service control file changed repeatedly.")
                continue
            return value
        except (ValueError, RecursionError):
            if attempt == 2 or _file_identity(path) == before:
                raise ServiceControlError("Invalid service control file.") from None
    raise ServiceControlError("Service control file changed repeatedly.")


def _descriptor(path):
    path = Path(path).absolute()
    if path.name != "service.json":
        raise ServiceControlError("Select the exact service.json file for this dashboard.")
    run = _directory(path.parent)
    value = _read(path)
    if (set(value) != _BASE | {"status"} or type(value.get("version")) is not int
            or value["version"] != 1 or value.get("runId") != run.name
            or not _origin(value.get("url")) or type(value.get("pid")) is not int or value["pid"] <= 0
            or not isinstance(value.get("mode"), str) or not 1 <= len(value["mode"]) <= 512
            or any(ord(c) < 32 or ord(c) == 127 for c in value["mode"])
            or not _identity(value.get("ownerIdentity")) or not isinstance(value.get("status"), str)
            or value["status"] not in _STATES):
        raise ServiceControlError("Invalid service lifecycle descriptor.")
    return run, value


def _same_descriptor(path, original):
    _, value = _descriptor(path)
    if any(value[key] != original[key] for key in _BASE):
        raise ServiceControlError("Service identity changed; request refused.")
    return value


def _validate_lock(fd, path, expected):
    info = os.fstat(fd)
    if ([info.st_dev, info.st_ino] != expected or _file_identity(path) != expected
            or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600):
        raise ServiceControlError("Service ownership changed; request refused.")


def _held(fd, path, expected):
    _validate_lock(fd, path, expected)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        _validate_lock(fd, path, expected)
        return True
    fcntl.flock(fd, fcntl.LOCK_UN)
    _validate_lock(fd, path, expected)
    return False


def _remove_matching(path, run_id, request_id):
    try:
        value = _read(path)
        if value.get("runId") == run_id and value.get("requestId") == request_id:
            path.unlink()
    except (OSError, ValueError):
        pass


def _result(path, descriptor, status, *, responsive=False, action=None):
    value = {key: descriptor[key] for key in _BASE}
    value.update(serviceFile=str(path), status=status, responsive=responsive)
    if action is not None:
        value["action"] = action
    return value


class ServiceControl:
    def __init__(self, run_dir, *, metadata, is_ready, request_shutdown):
        self.run_dir = _directory(run_dir)
        if (not isinstance(metadata, dict) or set(metadata) != {"url", "pid", "mode"}
                or not _origin(metadata.get("url")) or type(metadata.get("pid")) is not int
                or metadata["pid"] <= 0 or not isinstance(metadata.get("mode"), str)
                or not 1 <= len(metadata["mode"]) <= 512
                or any(ord(c) < 32 or ord(c) == 127 for c in metadata["mode"])
                or not callable(is_ready) or not callable(request_shutdown)):
            raise ServiceControlError("Invalid service control metadata or callbacks.")
        self.descriptor_path = self.run_dir / "service.json"
        self._owner_fd = None
        self._closed = self._failed = self._stopping = self._shutdown_called = False
        self._last_request = self._last_response = self._last_descriptor = None
        self.is_ready, self.request_shutdown = is_ready, request_shutdown
        try:
            self._owner_fd = _private_fd(self.run_dir / "service-owner.lock", os.O_RDWR | os.O_CREAT)
            fcntl.flock(self._owner_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if _exists(self.descriptor_path):
                raise ServiceControlError("A service run directory must never be reused.")
            info = os.fstat(self._owner_fd)
            self._base = {"version": 1, "runId": self.run_dir.name, **metadata,
                          "ownerIdentity": [info.st_dev, info.st_ino]}
            client = _private_fd(self.run_dir / "service-client.lock", os.O_RDWR | os.O_CREAT)
            try:
                info = os.fstat(client)
                self._client_identity = [info.st_dev, info.st_ino]
            finally:
                os.close(client)
            self._publish()
        except BaseException:
            if self._owner_fd is not None:
                os.close(self._owner_fd)
                self._owner_fd = None
            raise

    def _state(self):
        if self._closed:
            return "stopped"
        if self._failed:
            return "control-unavailable"
        if self._stopping:
            return "stopping"
        try:
            ready = self.is_ready()
        except Exception:
            raise ServiceControlError("Service readiness check failed.") from None
        if type(ready) is not bool:
            raise ServiceControlError("Service readiness callback must return a boolean.")
        return "running" if ready else "starting"

    def _publish(self):
        _directory(self.run_dir)
        _validate_lock(self._owner_fd, self.run_dir / "service-owner.lock", self._base["ownerIdentity"])
        value = {**self._base, "status": self._state()}
        if value != self._last_descriptor:
            write_private_json(self.descriptor_path, value)
            self._last_descriptor = value
        return value

    def _cleanup(self, request_id, names=("service-request.json", "service-response.json")):
        # Clients hold this lock while publishing and removing their nonce.
        # A check-then-unlink without it could delete a newly published request.
        fd = _private_fd(self.run_dir / "service-client.lock", os.O_RDWR)
        try:
            _validate_lock(fd, self.run_dir / "service-client.lock", self._client_identity)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return  # The client cleans its own nonce; never wait for it.
            _validate_lock(fd, self.run_dir / "service-client.lock", self._client_identity)
            for name in names:
                _remove_matching(self.run_dir / name, self.run_dir.name, request_id)
        finally:
            os.close(fd)

    def poll(self):
        """Handle one bounded owner request, never a backend or process signal."""
        if self._closed or self._failed:
            return
        descriptor = self._publish()
        now = time.time()
        if self._last_request and self._last_request["expiresAt"] <= now:
            self._cleanup(self._last_request["requestId"])
            self._last_request = self._last_response = None
        path = self.run_dir / "service-request.json"
        if not _exists(path):
            if self._last_request:
                self._cleanup(self._last_request["requestId"], ("service-response.json",))
            return
        try:
            request = _read(path)
        except (OSError, ValueError):
            return
        if (set(request) != _REQUEST or type(request.get("version")) is not int or request["version"] != 1
                or request.get("runId") != self.run_dir.name or request.get("url") != self._base["url"]
                or not _identity(request.get("ownerIdentity")) or request["ownerIdentity"] != self._base["ownerIdentity"]
                or not isinstance(request.get("requestId"), str) or not _ID.fullmatch(request["requestId"])
                or not isinstance(request.get("action"), str) or request["action"] not in {"status", "stop"}
                or not _number(request.get("issuedAt")) or not _number(request.get("expiresAt"))
                or request["issuedAt"] > now + 0.5 or not 0 < request["expiresAt"] - request["issuedAt"] <= _MAX_WAIT):
            return
        if request["expiresAt"] <= time.time():
            self._cleanup(request["requestId"])
            return
        if self._last_request and request["requestId"] == self._last_request["requestId"]:
            if request != self._last_request:
                return
            reply = self._last_response
        else:
            if request["action"] == "stop":
                self._stopping = True
                descriptor = self._publish()
            reply = {key: request[key] for key in ("version", "runId", "url", "ownerIdentity", "requestId", "action")}
            reply["status"] = descriptor["status"]
            self._last_request, self._last_response = request, reply
        response = self.run_dir / "service-response.json"
        try:
            if not _exists(response) or _read(response) != reply:
                write_private_json(response, reply)
        except FileNotFoundError:
            if request["action"] == "stop" and not self._shutdown_called:
                self.failed()
                raise ServiceControlError("Shutdown acknowledgement publication is unconfirmed.") from None
            return
        # Acknowledgement is published before asking Uvicorn to exit. One fixed
        # callback per service, including different stop nonces and exceptions.
        if request["action"] == "stop" and not self._shutdown_called:
            _validate_lock(self._owner_fd, self.run_dir / "service-owner.lock", self._base["ownerIdentity"])
            self._shutdown_called = True
            try:
                self.request_shutdown()
            except Exception:
                self.failed()
                raise ServiceControlError("Shutdown callback failed; service completion is unconfirmed.") from None

    def failed(self):
        """Disable this facility, retaining the service lifetime ownership lock."""
        if self._closed:
            return
        self._failed = True
        try:
            self._publish()
        except (OSError, ValueError):
            pass

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            try:
                self._publish()
            except (OSError, ValueError):
                pass
            # Responses contain no credentials. Leave the last bounded receipt
            # for a racing client; that client removes only its own nonce.
        finally:
            if self._owner_fd is not None:
                os.close(self._owner_fd)
                self._owner_fd = None


def request_service_action(service_file, action, timeout=2.0):
    timeout = _timeout(timeout)
    if not isinstance(action, str) or action not in {"status", "stop"}:
        raise ServiceControlError("Service action must be status or stop.")
    path = Path(service_file).absolute()
    run, descriptor = _descriptor(path)
    owner = client = None
    written = False
    request_id = secrets.token_hex(16)
    try:
        owner = _private_fd(run / "service-owner.lock", os.O_RDWR)
        if not _held(owner, run / "service-owner.lock", descriptor["ownerIdentity"]):
            _same_descriptor(path, descriptor)
            return _result(path, descriptor, "stopped", action=action)
        if descriptor["status"] == "control-unavailable":
            raise ServiceControlError("The service is still owned, but its lifecycle control is unavailable; use its foreground terminal.")
        client = _private_fd(run / "service-client.lock", os.O_RDWR)
        try:
            fcntl.flock(client, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ServiceControlError("Another service-control request is in progress; try again shortly.") from None
        _same_descriptor(path, descriptor)
        client_identity = _file_identity(run / "service-client.lock")
        _validate_lock(client, run / "service-client.lock", client_identity)
        issued = time.time()
        deadline = time.monotonic() + timeout
        request = {"version": 1, "runId": run.name, "url": descriptor["url"],
                   "ownerIdentity": descriptor["ownerIdentity"], "requestId": request_id,
                   "action": action, "issuedAt": issued, "expiresAt": issued + timeout}
        write_private_json(run / "service-request.json", request)
        written = True
        while time.monotonic() < deadline:
            _same_descriptor(path, descriptor)
            _validate_lock(client, run / "service-client.lock", client_identity)
            held = _held(owner, run / "service-owner.lock", descriptor["ownerIdentity"])
            response = run / "service-response.json"
            if _exists(response):
                try:
                    reply = _read(response)
                except FileNotFoundError:
                    reply = {}
                if reply.get("requestId") == request_id:
                    if (set(reply) != _RESPONSE or type(reply.get("version")) is not int
                            or not _identity(reply.get("ownerIdentity"))
                            or any(reply.get(key) != request[key] for key in _RESPONSE - {"status"})
                            or not isinstance(reply.get("status"), str)
                            or reply["status"] not in {"starting", "running", "stopping"}
                            or (action == "stop" and reply["status"] != "stopping")):
                        raise ServiceControlError("Service response does not match this request and owner.")
                    _same_descriptor(path, descriptor)
                    _validate_lock(owner, run / "service-owner.lock", descriptor["ownerIdentity"])
                    _validate_lock(client, run / "service-client.lock", client_identity)
                    return _result(path, descriptor, reply["status"], responsive=True, action=action)
            if not held:
                return _result(path, descriptor, "stopped", action=action)
            time.sleep(min(0.025, max(0, deadline - time.monotonic())))
        raise ServiceControlError("The service did not answer in time; its lifecycle state is unconfirmed.")
    finally:
        if written:
            for name in ("service-request.json", "service-response.json"):
                _remove_matching(run / name, run.name, request_id)
        if client is not None:
            os.close(client)
        if owner is not None:
            os.close(owner)


def wait_for_stopped(service_file, timeout=5.0):
    """True only when this descriptor's original lifecycle owner releases its lock."""
    timeout = _timeout(timeout)
    path = Path(service_file).absolute()
    run, descriptor = _descriptor(path)
    owner = _private_fd(run / "service-owner.lock", os.O_RDWR)
    try:
        deadline = time.monotonic() + timeout
        while True:
            _same_descriptor(path, descriptor)
            if not _held(owner, run / "service-owner.lock", descriptor["ownerIdentity"]):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(0.025, max(0, deadline - time.monotonic())))
    finally:
        os.close(owner)


def _inspect_service(path, timeout):
    record = {"runId": path.parent.name, "serviceFile": str(path), "status": "unknown", "responsive": False}
    owner = None
    try:
        run = _directory(path.parent)
        if not _exists(path):
            record["status"] = "unmanaged"
        else:
            _, descriptor = _descriptor(path)
            owner = _private_fd(run / "service-owner.lock", os.O_RDWR)
            held = _held(owner, run / "service-owner.lock", descriptor["ownerIdentity"])
            _same_descriptor(path, descriptor)
            state = "stopped" if not held else "control-unavailable" if descriptor["status"] == "control-unavailable" else "unresponsive"
            record = _result(path, descriptor, state)
            if held and state != "control-unavailable" and timeout > 0:
                try:
                    record = request_service_action(path, "status", timeout=timeout)
                except (OSError, ValueError):
                    # Recheck the original owner after a timeout; never turn
                    # an identity change or mailbox error into stopped.
                    _same_descriptor(path, descriptor)
                    if not _held(owner, run / "service-owner.lock", descriptor["ownerIdentity"]):
                        record["status"] = "stopped"
    except (OSError, ValueError):
        record = {"runId": path.parent.name, "serviceFile": str(path), "status": "unknown", "responsive": False}
    finally:
        if owner is not None:
            os.close(owner)
    return record


def inspect_service(service_file, timeout=0.5):
    """Describe one exact run without scanning history or reading pairing files.

Unknown covers unsafe, missing or invalid control state. Unmanaged means an
existing valid private run directory has no lifecycle descriptor, not that a
legacy process is alive. Only original owner-lock release proves stopped.
"""
    timeout = _timeout(timeout)
    path = Path(service_file).absolute()
    if path.name != "service.json" or not _ID.fullmatch(path.parent.name):
        raise ServiceControlError("Select one exact run's service.json file.")
    return _inspect_service(path, timeout)


def discover_services(run_root, *, timeout=2.0):
    """Bounded current-checkout discovery; timeout is a total probe budget.

No pairing file is opened. Legacy run directories report unmanaged, never
running/stopped based on a PID or the existence of their files.
"""
    timeout = _timeout(timeout)
    root = Path(run_root).absolute()
    if not _exists(root):
        return []
    root = _directory(root, run=False)
    names = []
    with os.scandir(root) as entries:
        for entry in entries:
            if len(names) >= _MAX_RUNS:
                raise ServiceControlError("Too many service run records; select an exact dashboard instance with --instance RUN_ID.")
            names.append(entry.name)
    deadline = time.monotonic() + timeout
    results = []
    for name in sorted(names):
        if not _ID.fullmatch(name):
            continue
        path = root / name / "service.json"
        results.append(_inspect_service(path, max(0, min(0.25, deadline - time.monotonic()))))
    return results
