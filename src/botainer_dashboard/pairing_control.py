"""Owner-only pairing requests over a bounded private file mailbox.

There is no network endpoint or runtime-controller access here. The service
calls ``poll`` on the same event-loop thread that handles browser pairing.
The CLI supplies an exact access-file path and never constructs a PairingStore.
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

from .pairing import PAIRING_LIFETIME, _private_fd, read_private_json, write_private_json


_ID = re.compile(r"[0-9a-f]{32}\Z")
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43,128}\Z")
_MAX_WAIT = 10.0
_LIMIT = 4096
_REQUEST_KEYS = {"version", "runId", "url", "requestId", "action", "issuedAt", "expiresAt"}
_BASE_KEYS = {"version", "runId", "url", "pid", "mode", "status"}


class PairingControlError(ValueError):
    """A local control failure with no token or request content in its message."""


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _origin(value):
    if not isinstance(value, str):
        return False
    match = re.fullmatch(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})", value)
    return bool(match and int(match[1]) <= 65535)


def _run_directory(path):
    path = Path(path).absolute()
    if ".." in path.parts or not _ID.fullmatch(path.name):
        raise PairingControlError("Invalid pairing service directory.")
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise PairingControlError("Pairing paths must not contain symlinks.")
    info = path.stat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise PairingControlError("Pairing service directory must be private and owner-only.")
    return path


def _read_identity(path):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077 or info.st_nlink != 1):
        raise PairingControlError("Pairing mailbox must be a private regular file.")
    return info.st_dev, info.st_ino


def _read(path):
    """Reopen a concurrently replaced mailbox; never relax fd validation.

    Atomic publication can unlink the old inode after open but before the
    shared reader checks its link count. Retry only after a distinct, private
    replacement is observed. Stable unsafe/malformed files still fail, and
    repeated replacement gets at most three fully validated read attempts.
    """
    path = Path(path)
    for attempt in range(3):
        before = path.lstat()  # Identity hint only; the opened fd decides safety.
        identity = before.st_dev, before.st_ino
        try:
            return read_private_json(path, max_bytes=_LIMIT)
        except ValueError:
            if attempt == 2 or _read_identity(path) == identity:
                raise


def _exists(path):
    return path.exists() or path.is_symlink()


def _descriptor(path):
    path = Path(path).absolute()
    if path.name != "access.json":
        raise PairingControlError("Select the launcher's exact access.json file.")
    run = _run_directory(path.parent)
    value = _read(path)
    if (value.get("version") != 2 or type(value.get("version")) is not int
            or value.get("runId") != run.name or not _origin(value.get("url"))
            or type(value.get("pid")) is not int or value["pid"] <= 0
            or not isinstance(value.get("mode"), str) or not 1 <= len(value["mode"]) <= 512
            or not isinstance(value.get("status"), str)
            or value["status"] not in {"ready", "needs-code", "unavailable", "stopped"}):
        raise PairingControlError("This service does not provide a valid pairing-control descriptor.")
    expected = _BASE_KEYS | ({"token", "expiresAt"} if value["status"] == "ready" else set())
    if set(value) != expected or (value["status"] == "ready" and (
            not isinstance(value["token"], str) or not _TOKEN.fullmatch(value["token"])
            or not _number(value["expiresAt"]))):
        raise PairingControlError("Invalid pairing-control descriptor.")
    return run, value


def _active(fd, path):
    """Check the original lock inode; do not trust a PID or create a lock."""
    info, current = os.fstat(fd), path.lstat()
    if ((info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
            or not stat.S_ISREG(current.st_mode) or current.st_uid != os.getuid()
            or current.st_nlink != 1 or current.st_mode & 0o077):
        raise PairingControlError("Pairing service ownership changed; request refused.")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return
    fcntl.flock(fd, fcntl.LOCK_UN)
    raise PairingControlError("That service's owner pairing control is no longer active.")


def _remove_matching(path, run_id, request_id=None):
    """Remove only this private mailbox record, never an unrelated new request."""
    try:
        if not _exists(path):
            return
        value = _read(path)
        if value.get("runId") == run_id and (request_id is None or value.get("requestId") == request_id):
            path.unlink()
    except (OSError, ValueError):
        # A malformed/unsafe file is not ours to unlink by assumption.
        pass


class PairingControl:
    def __init__(self, run_dir: Path, store, *, token: str, metadata: dict):
        self.run_dir = _run_directory(run_dir)
        if (not isinstance(metadata, dict) or set(metadata) != {"url", "pid", "mode"}
                or metadata["url"] != store.origin or not _origin(metadata["url"])
                or type(metadata["pid"]) is not int or metadata["pid"] <= 0
                or not isinstance(metadata["mode"], str) or not 1 <= len(metadata["mode"]) <= 512
                or not isinstance(token, str) or not _TOKEN.fullmatch(token)):
            raise PairingControlError("Invalid local pairing-control metadata.")
        self.store, self._token = store, token
        self._base = {"version": 2, "runId": self.run_dir.name, **metadata}
        self.access_file = self.run_dir / "access.json"
        self._owner_fd = None
        self._closed = False
        self._last_request = None
        self._last_response = None
        self._last_descriptor = None
        try:
            self._owner_fd = _private_fd(self.run_dir / "owner.lock", os.O_RDWR | os.O_CREAT)
            fcntl.flock(self._owner_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            client = _private_fd(self.run_dir / "client.lock", os.O_RDWR | os.O_CREAT)
            os.close(client)
            self._publish()
        except BaseException:
            if self._owner_fd is not None:
                os.close(self._owner_fd)
                self._owner_fd = None
            raise

    def _publish(self):
        value = dict(self._base)
        if self._closed:
            # This mailbox may close after an I/O error while the app continues.
            value["status"] = "stopped"
        elif not self.store.can_issue_token():
            value["status"] = "unavailable"
        elif self.store.token_available(self._token):
            value.update(status="ready", token=self._token, expiresAt=self.store.token_expires_at)
        else:
            value["status"] = "needs-code"
        if value != self._last_descriptor:
            write_private_json(self.access_file, value)
            self._last_descriptor = value

    def poll(self):
        """Handle at most one small request; no runtime or network operations."""
        if self._closed:
            return
        _run_directory(self.run_dir)
        self._publish()  # Remove a spent/expired code even without an owner request.
        now = time.time()
        if self._last_request and self._last_request["expiresAt"] <= now:
            for name in ("request.json", "response.json"):
                _remove_matching(self.run_dir / name, self.run_dir.name, self._last_request["requestId"])
            self._last_request = self._last_response = None
        path = self.run_dir / "request.json"
        if not _exists(path):
            if self._last_request:
                _remove_matching(self.run_dir / "response.json", self.run_dir.name,
                                 self._last_request["requestId"])
            return
        try:
            request = _read(path)
        except (OSError, ValueError):
            return  # Do not turn corrupt or unsafe files into issuance requests.
        if (set(request) != _REQUEST_KEYS or type(request.get("version")) is not int
                or request["version"] != 1 or request.get("runId") != self.run_dir.name
                or request.get("url") != self.store.origin
                or not isinstance(request.get("requestId"), str) or not _ID.fullmatch(request["requestId"])
                or request.get("action") != "pairing-code"
                or not _number(request.get("issuedAt")) or not _number(request.get("expiresAt"))
                or request["issuedAt"] > now + 0.5
                or not 0 < request["expiresAt"] - request["issuedAt"] <= _MAX_WAIT):
            return
        if request["expiresAt"] <= time.time():
            for name in ("request.json", "response.json"):
                _remove_matching(self.run_dir / name, self.run_dir.name, request["requestId"])
            return
        if self._last_request and request["requestId"] == self._last_request["requestId"]:
            if request != self._last_request:
                return
            reply = self._last_response
            if reply["status"] == "ok" and not self.store.token_available(reply["token"]):
                # Repeating a request never mints twice, but a known spent code
                # must not be presented as usable by replaying its old receipt.
                reply = {key: reply[key] for key in ("version", "runId", "url", "requestId")}
                reply.update(status="error", error="pairing-code-no-longer-valid")
                self._last_response = reply
        else:
            reply = {"version": 1, "runId": self.run_dir.name, "url": self.store.origin,
                     "requestId": request["requestId"]}
            if not self.store.can_issue_token():
                reply.update(status="error", error="pairing-unavailable")
            else:
                if not self.store.token_available(self._token):
                    self._token = secrets.token_urlsafe(32)
                    self.store.issue_token(self._token)
                reply.update(status="ok", token=self._token, expiresAt=self.store.token_expires_at)
                self._publish()
            self._last_request, self._last_response = request, reply
        response_path = self.run_dir / "response.json"
        try:
            if not _exists(response_path) or _read(response_path) != reply:
                write_private_json(response_path, reply)
        except FileNotFoundError:
            # The client can acknowledge and unlink its receipt between our
            # existence check and read/write validation. A still-pending
            # request is retried on the next poll; other failures stay fatal.
            return

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            try:
                self._publish()
            except (OSError, ValueError):
                # Active-owner validation still fails after releasing the lock.
                pass
            for name in ("request.json", "response.json"):
                _remove_matching(self.run_dir / name, self.run_dir.name)
        finally:
            self._token = ""
            self._last_request = self._last_response = self._last_descriptor = None
            if self._owner_fd is not None:
                os.close(self._owner_fd)
                self._owner_fd = None


def request_pairing_code(access_file: Path, timeout=5.0, *, expected_service=None) -> dict:
    """Ask one exact active service for its usable code; never start a service."""
    if not _number(timeout) or not 0 < timeout <= _MAX_WAIT:
        raise PairingControlError("Pairing request timeout must be greater than zero and at most ten seconds.")
    run, descriptor = _descriptor(access_file)
    if expected_service is not None:
        # Discovery chooses one lifecycle owner. Bind the pairing mailbox to
        # that same run before requesting a code; never follow a replacement.
        keys = {"runId", "url", "pid", "mode"}
        if (type(expected_service) is not dict or set(expected_service) != keys
                or any(type(expected_service[key]) is not type(descriptor[key])
                       or expected_service[key] != descriptor[key] for key in keys)):
            raise PairingControlError("Pairing control does not match the selected dashboard service.")
    if descriptor["status"] == "stopped":
        raise PairingControlError("Owner pairing control is stopped for that service; this does not mean its sessions have stopped.")
    owner_fd = client_fd = None
    request_id = secrets.token_hex(16)
    written = False
    try:
        owner_fd = _private_fd(run / "owner.lock", os.O_RDWR)
        _active(owner_fd, run / "owner.lock")
        client_fd = _private_fd(run / "client.lock", os.O_RDWR)
        try:
            fcntl.flock(client_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PairingControlError("Another owner pairing request is in progress; try again shortly.") from None
        # The descriptor may have changed while opening locks, but identity must not.
        _, current = _descriptor(access_file)
        if any(current[key] != descriptor[key] for key in ("runId", "url", "pid", "mode")):
            raise PairingControlError("Pairing service identity changed; request refused.")
        issued = time.time()
        deadline = time.monotonic() + timeout
        request = {"version": 1, "runId": run.name, "url": descriptor["url"],
                   "requestId": request_id, "action": "pairing-code", "issuedAt": issued,
                   "expiresAt": issued + timeout}
        write_private_json(run / "request.json", request)
        written = True
        while time.monotonic() < deadline:
            _run_directory(run)
            _active(owner_fd, run / "owner.lock")
            path = run / "response.json"
            if _exists(path):
                reply = _read(path)
                if reply.get("requestId") == request_id:
                    base = {"version", "runId", "url", "requestId", "status"}
                    if (type(reply.get("version")) is not int or reply["version"] != 1
                            or reply.get("runId") != run.name or reply.get("url") != descriptor["url"]):
                        raise PairingControlError("Pairing response identity does not match the selected service.")
                    if reply.get("status") == "error" and set(reply) == base | {"error"}:
                        if reply["error"] == "pairing-code-no-longer-valid":
                            raise PairingControlError("That code was already used or expired. Request a pairing code again.")
                        if reply["error"] == "pairing-unavailable":
                            raise PairingControlError("The service cannot issue a pairing code; browser capacity or authentication storage needs attention.")
                        raise PairingControlError("Pairing response contains an unknown failure code.")
                    if (set(reply) != base | {"token", "expiresAt"} or reply.get("status") != "ok"
                            or not isinstance(reply.get("token"), str) or not _TOKEN.fullmatch(reply["token"])
                            or not _number(reply.get("expiresAt"))
                            or not time.time() < reply["expiresAt"] <= time.time() + PAIRING_LIFETIME + 1):
                        raise PairingControlError("Pairing response is invalid or expired.")
                    return {key: reply[key] for key in ("url", "token", "expiresAt")}
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        raise PairingControlError("The selected service did not answer in time; no saved or stale code was returned.")
    finally:
        if written:
            for name in ("request.json", "response.json"):
                _remove_matching(run / name, run.name, request_id)
        if client_fd is not None:
            os.close(client_fd)
        if owner_fd is not None:
            os.close(owner_fd)
