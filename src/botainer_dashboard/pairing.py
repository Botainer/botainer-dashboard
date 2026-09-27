"""Private, finite browser pairing for one exact loopback origin.

Only hashes of the two independent browser secrets persist. A launcher's pairing
token is single use and short lived; it never authorizes ordinary API requests.
The owner must close this store when the service exits to release its file lock.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import time

from .access import AccessDenied

BROWSER_LIFETIME = 30 * 24 * 60 * 60
PAIRING_LIFETIME = 10 * 60
MAX_BROWSERS = 16
_SECRET = re.compile(r"[A-Za-z0-9_-]{43,128}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")


def private_directory(path: Path) -> Path:
    """Create the requested private directory, refusing symlink ancestry."""
    path = Path(path).absolute()
    for component in reversed((path, *path.parents)):
        if component.is_symlink():
            raise ValueError("authentication directory must not contain symlinks")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("authentication directory must be owner-only")
    return path


def _private_fd(path: Path, flags: int) -> int:
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_nlink != 1):
            raise ValueError("authentication file must be a private regular file")
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_private_json(path: Path, *, max_bytes: int = 16384) -> dict:
    # Authentication retains its small default. Bounded runtime journals may
    # explicitly request more space without weakening file ownership checks.
    if type(max_bytes) is not int or not 1 <= max_bytes <= 2 * 1024 * 1024:
        raise ValueError("invalid private document size limit")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate authentication field")
            result[key] = value
        return result
    fd = _private_fd(path, os.O_RDONLY)
    with os.fdopen(fd, "rb") as handle:
        text = handle.read(max_bytes + 1)
    if len(text) > max_bytes:
        raise ValueError("authentication file is too large")
    value = json.loads(text, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError("invalid authentication document")
    return value


def write_private_json(path: Path, value: dict) -> None:
    """Replace atomically; never follow an existing output link."""
    if path.exists() or path.is_symlink():
        fd = _private_fd(path, os.O_RDONLY)
        os.close(fd)
    temporary = path.with_name(path.name + "." + secrets.token_hex(12) + ".tmp")
    fd = _private_fd(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, separators=(",", ":"), allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("ascii")).hexdigest()


@dataclass(frozen=True)
class BrowserCredential:
    bearer_hash: str
    expires: float


@dataclass(frozen=True)
class BrowserGrant:
    cookie: str
    bearer: str
    expires: float
    lifetime: int


class PairingStore:
    def __init__(self, *, origin: str, token: str, state_path: Path | None = None,
                 lifetime: int = BROWSER_LIFETIME, clock=time.time):
        if not re.fullmatch(r"http://127\.0\.0\.1:[1-9][0-9]{0,4}", origin):
            raise ValueError("pairing must be bound to an exact loopback origin")
        if type(lifetime) is not int or not 1 <= lifetime <= BROWSER_LIFETIME:
            raise ValueError("invalid browser lifetime")
        self.origin, self.lifetime, self.clock = origin, lifetime, clock
        self.state_path = Path(state_path) if state_path is not None else None
        self._credentials: dict[str, BrowserCredential] = {}
        self._lock_fd = None
        self._failed = False
        self.issue_token(token)
        try:
            if self.state_path is not None:
                import fcntl
                private_directory(self.state_path.parent)
                self._lock_fd = _private_fd(self.state_path.with_suffix(".lock"), os.O_RDWR | os.O_CREAT)
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if self.state_path.exists() or self.state_path.is_symlink():
                    self._load(read_private_json(self.state_path))
        except BaseException:
            self.close()
            raise

    def issue_token(self, token: str) -> None:
        """Host-side only; preserve browser grants when replacing a pending code."""
        if self._failed:
            raise ValueError("authentication storage is unavailable")
        if not isinstance(token, str) or not _SECRET.fullmatch(token):
            raise ValueError("invalid pairing token")
        self._token_hash = _digest(token)
        self._token_deadline = self.clock() + PAIRING_LIFETIME

    @property
    def token_expires_at(self) -> float:
        return self._token_deadline

    def token_available(self, token: str) -> bool:
        """Read-only check for an unconsumed, unexpired code on this service."""
        return (not self._failed and isinstance(token, str)
                and _SECRET.fullmatch(token) is not None
                and self.clock() < self._token_deadline and self._token_hash is not None
                and hmac.compare_digest(_digest(token), self._token_hash))

    def can_issue_token(self) -> bool:
        now = self.clock()
        return (not self._failed and sum(row.expires > now for row in self._credentials.values())
                < MAX_BROWSERS)

    def _load(self, value: dict) -> None:
        if (set(value) != {"version", "origin", "browsers"} or value["version"] != 1
                or value["origin"] != self.origin or not isinstance(value["browsers"], dict)
                or len(value["browsers"]) > MAX_BROWSERS):
            raise ValueError("authentication state does not match this service")
        now = self.clock()
        for cookie_hash, row in value["browsers"].items():
            if (not _HASH.fullmatch(cookie_hash) or not isinstance(row, dict)
                    or set(row) != {"bearerHash", "expires"}
                    or not isinstance(row["bearerHash"], str) or not _HASH.fullmatch(row["bearerHash"])
                    or type(row["expires"]) not in (int, float) or not math.isfinite(row["expires"])
                    or row["expires"] > now + BROWSER_LIFETIME):
                raise ValueError("invalid authentication state")
            if row["expires"] > now:
                self._credentials[cookie_hash] = BrowserCredential(row["bearerHash"], row["expires"])

    def _save(self) -> None:
        if self.state_path is None:
            return
        try:
            write_private_json(self.state_path, {"version": 1, "origin": self.origin,
                "browsers": {key: {"bearerHash": row.bearer_hash, "expires": row.expires}
                             for key, row in self._credentials.items()}})
        except BaseException:
            # Never keep serving with revocation durability uncertain.
            self._failed = True
            self._credentials.clear()
            raise

    def pair(self, token: str) -> BrowserGrant:
        now = self.clock()
        if not self.token_available(token):
            raise AccessDenied("pairing-token-invalid-or-expired")
        self._credentials = {key: row for key, row in self._credentials.items() if row.expires > now}
        if len(self._credentials) >= MAX_BROWSERS:
            raise AccessDenied("pairing-capacity")
        cookie, bearer = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        expires = now + self.lifetime
        self._token_hash = None
        self._credentials[_digest(cookie)] = BrowserCredential(_digest(bearer), expires)
        self._save()
        return BrowserGrant(cookie, bearer, expires, self.lifetime)

    def credential_for(self, cookie: str) -> BrowserCredential | None:
        if self._failed or not isinstance(cookie, str) or not _SECRET.fullmatch(cookie):
            return None
        row = self._credentials.get(_digest(cookie))
        return row if row is not None and row.expires > self.clock() else None

    def authenticated(self, cookie: str, bearer: str) -> bool:
        row = self.credential_for(cookie)
        return (row is not None and isinstance(bearer, str) and _SECRET.fullmatch(bearer) is not None
                and hmac.compare_digest(row.bearer_hash, _digest(bearer)))

    def revoke(self, cookie: str) -> None:
        if self.credential_for(cookie) is not None:
            del self._credentials[_digest(cookie)]
            self._save()

    def close(self) -> None:
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None
