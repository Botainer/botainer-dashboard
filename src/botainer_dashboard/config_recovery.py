"""Bounded private recovery copies before project config replacement.

This is recovery, not compare-and-swap. An external editor that does not share
the lifecycle lock can still write between the final read and replacement.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import time
from uuid import uuid4

MAX_TEXT = 65536
SLOTS = 16
MAX_RECORD = 12 * MAX_TEXT + 4096


class ConfigRecoveryError(ValueError):
    pass


def _require(condition):
    if not condition:
        raise ConfigRecoveryError("config-recovery-unavailable")


def _private(info, *, directory=False):
    _require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
             and info.st_uid == os.getuid() and not info.st_mode & 0o077
             and (directory or info.st_nlink == 1))


@contextmanager
def _directory(root):
    root = Path(root)
    _require(root.is_absolute() and root.resolve(strict=True) == root)
    parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        _private(os.fstat(parent), directory=True)
        try:
            os.mkdir("config-recovery", 0o700, dir_fd=parent)
        except FileExistsError:
            pass
        child = os.open("config-recovery", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            _private(os.fstat(child), directory=True)
            yield child
        finally:
            os.close(child)
    finally:
        os.close(parent)


def preserve_config(root, target, base, draft):
    """Keep the last 16 attempts per control root; refuse save if storage fails.

    Only fixed slots belonging to this format are replaced, under a cross-process
    lock. No project names or paths enter filenames. Unknown files are untouched.
    Config text may contain secrets: this directory must never be exported.
    """
    try:
        _require(isinstance(target, str) and len(target) <= 8192)
        for value in (base, draft):
            _require(isinstance(value, bytes) and len(value) <= MAX_TEXT and b"\0" not in value)
        record = {"version": 1, "id": uuid4().hex, "created_ns": time.time_ns(), "sequence": 0,
                  "target_sha256": hashlib.sha256(target.encode()).hexdigest(),
                  "base_sha256": hashlib.sha256(base).hexdigest(),
                  "draft_sha256": hashlib.sha256(draft).hexdigest(),
                  "base": base.decode("utf-8"), "draft": draft.decode("utf-8")}
        with _directory(root) as directory:
            lock = os.open("recovery.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                           0o600, dir_fd=directory)
            try:
                _private(os.fstat(lock))
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                candidates = []
                for index in range(SLOTS):
                    name = f"slot-{index:02d}.json"
                    try:
                        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                    except FileNotFoundError:
                        candidates.append((-1, name)); continue
                    with os.fdopen(fd, "rb") as stream:
                        info = os.fstat(stream.fileno()); _private(info)
                        value = stream.read(MAX_RECORD + 1)
                    _require(len(value) <= MAX_RECORD)
                    previous = json.loads(value)
                    _require(isinstance(previous, dict) and set(previous) == set(record)
                             and previous.get("version") == 1
                             and type(previous.get("created_ns")) is int
                             and type(previous.get("sequence")) is int
                             and 0 <= previous["sequence"] < 2**63 - 1)
                    candidates.append((previous["sequence"], name))
                name = min(candidates)[1]
                record["sequence"] = max(item[0] for item in candidates) + 1
                raw = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode()
                _require(len(raw) <= MAX_RECORD)
                # One fixed staging file bounds crash leftovers. Its contents
                # are never executed; remove only a verified private file.
                pending = "pending.json"
                try:
                    info = os.stat(pending, dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    _private(info); os.unlink(pending, dir_fd=directory)
                fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory)
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
                    os.replace(pending, name, src_dir_fd=directory, dst_dir_fd=directory)
                    os.fsync(directory)
                finally:
                    try: os.unlink(pending, dir_fd=directory)
                    except FileNotFoundError: pass
            finally:
                os.close(lock)
        return {"id": record["id"], "path": str(Path(root) / "config-recovery" / name)}
    except (OSError, ValueError, UnicodeError, TypeError):
        raise ConfigRecoveryError("config-recovery-unavailable") from None
