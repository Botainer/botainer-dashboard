"""Bounded native CLI console and private operation locks for installed backends.

Terminal bytes are presentation, never lifecycle or consent evidence. Closing a
viewer does not close its launch console; cleanup uncertainty retains ownership.
"""
from __future__ import annotations

import fcntl
import os
import stat
import threading
import time
import uuid

from .backend_errors import BackendUnavailable
from .pty_bridge import PtyAttachment

TRANSCRIPT_LIMIT = 1024 * 1024
LAUNCH_TIMEOUT = 300
CONSOLE_LIMIT = 32


def canonical_request(value):
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError()
    except (ValueError, AttributeError, TypeError) as error:
        raise BackendUnavailable("native-launch-request-invalid") from error
    return value


def private_lock(path):
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o077):
            raise BackendUnavailable("native-lock-file-invalid")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_transcript(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o077
                or info.st_size > TRANSCRIPT_LIMIT):
            raise BackendUnavailable("native-transcript-invalid")
        data = stream.read(TRANSCRIPT_LIMIT + 1)
        if len(data) > TRANSCRIPT_LIMIT:
            raise BackendUnavailable("native-transcript-invalid")
        return data


class LaunchConsole:
    """The service owns the CLI; opening/closing a viewer never closes its PTY."""

    def __init__(self, argv, *, cwd, env, transcript, owner_fd, attachment_factory=PtyAttachment):
        self.condition = threading.Condition()
        self.buffer = bytearray()
        self.base = 0
        self.done = False
        self.exit_code = None
        self.failed = False
        self.timed_out = False
        self.cleanup_confirmed = False
        self.viewer = None
        self.transcript = transcript
        self.owner_fd = owner_fd
        self.client = attachment_factory(argv, cwd=cwd, env=env, cols=100, rows=30, cooked=True)
        self.thread = threading.Thread(target=self._pump, daemon=True, name="native-launch-console")
        self.thread.start()

    def _pump(self):
        deadline = time.monotonic() + LAUNCH_TIMEOUT
        try:
            while time.monotonic() < deadline:
                data = self.client.read(65536, timeout=.1)
                if data == b"":
                    break
                if data:
                    with self.condition:
                        self.buffer.extend(data)
                        excess = len(self.buffer) - TRANSCRIPT_LIMIT
                        if excess > 0:
                            del self.buffer[:excess]
                            self.base += excess
                        self.condition.notify_all()
            else:
                self.timed_out = True
                self.failed = True
        except Exception:
            self.failed = True
        finally:
            try:
                self.exit_code = self.client.close(timeout=1)
                self.cleanup_confirmed = True
            except Exception:
                self.failed = True
            try:
                # Exact terminal bytes are private data. The retained tail is
                # bounded, and never parsed to infer consent or runtime identity.
                fd = os.open(self.transcript, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(self.buffer); stream.flush(); os.fsync(stream.fileno())
            except Exception:
                self.failed = True
            with self.condition:
                self.done = True
                self.condition.notify_all()
            if self.cleanup_confirmed:
                os.close(self.owner_fd)
                self.owner_fd = None

    def attach(self, writer_fd):
        with self.condition:
            if self.viewer is not None:
                raise BackendUnavailable("native-launch-writer-busy")
            viewer = LaunchViewer(self, writer_fd)
            self.viewer = viewer
            return viewer


class LaunchViewer:
    def __init__(self, console, writer_fd):
        self.console = console
        self.cursor = console.base
        self.writer_fd = writer_fd
        self.closed = False

    def read(self, max_bytes, *, timeout=0):
        if type(max_bytes) is not int or not 1 <= max_bytes <= 65536:
            raise ValueError("invalid read size")
        with self.console.condition:
            if self.closed:
                return b""
            deadline = time.monotonic() + timeout
            while True:
                self.cursor = max(self.cursor, self.console.base)
                offset = self.cursor - self.console.base
                data = bytes(self.console.buffer[offset:offset + max_bytes])
                if data:
                    self.cursor += len(data)
                    return data
                if self.console.done:
                    return b""
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self.console.condition.wait(remaining)

    def write(self, data):
        with self.console.condition:
            if self.closed or self.console.done:
                raise BrokenPipeError("launch console has ended")
            return self.console.client.write(data)

    def resize(self, cols, rows):
        with self.console.condition:
            if not self.closed and not self.console.done:
                self.console.client.resize(cols, rows)

    def close(self):
        with self.console.condition:
            self.closed = True
            if self.console.viewer is self:
                self.console.viewer = None
            if self.writer_fd is not None:
                os.close(self.writer_fd)
                self.writer_fd = None
            self.console.condition.notify_all()
        return 0
