"""Bounded leased attachment over a trusted SSH argv, with no local PTY.

The compute supervisor owns Screen's attach client. A dead host/network cannot
renew its lease, even when an unrelated OpenSSH master retains the channel.
This primitive accepts only argv constructed by the target-verifying backend.
"""
from __future__ import annotations

from collections import deque
import json
import os
from pathlib import Path
import select
import signal
import struct
import subprocess
import threading
import time
import uuid

from .pty_bridge import _size, _timeout

HEADER = struct.Struct("!cI")
DIMENSIONS = struct.Struct("!HH")
MAX_FRAME = 65536
CHUNK = 32768
MAX_QUEUE = 262144
HEARTBEAT_SECONDS = 2.0
READY_TIMEOUT = 40.0
CLOSE_TIMEOUT = 4.0
EXECUTABLE_VERIFICATION = {"procfs-executable-and-pinned-screen-sha256",
                           "restricted-procfs-pinned-root-setgid-screen"}
MAX_DIAGNOSTICS = 65536


class AttachmentStartError(OSError):
    """Startup failure with bounded private transport evidence, never API text."""

    def __init__(self, message, diagnostics):
        super().__init__(message)
        self.diagnostics = diagnostics


def _frame(kind, data=b""):
    if len(kind) != 1 or len(data) > MAX_FRAME:
        raise ValueError("invalid attachment frame")
    return HEADER.pack(kind, len(data)) + data


def _take_frames(buffer):
    frames = []
    while len(buffer) >= HEADER.size:
        kind, size = HEADER.unpack_from(buffer)
        if size > MAX_FRAME:
            raise OSError("remote attachment frame exceeds bound")
        if len(buffer) < HEADER.size + size:
            break
        frames.append((kind, bytes(buffer[HEADER.size:HEADER.size + size])))
        del buffer[:HEADER.size + size]
    return frames


class FramedSshAttachment:
    """PtyAttachment-compatible read/write/resize/close; no input replay.

    Its background thread handles bounded pipe I/O and the heartbeat. close()
    succeeds only after the compute supervisor acknowledges original-owner
    survival and client detachment. Missing acknowledgement raises OSError so
    the caller can retain its writer fence. Nothing controls an SSH master.
    """

    def __init__(self, argv, *, cwd, env, cols=80, rows=24, expected_owner=None):
        _size(cols, rows)
        if isinstance(argv, (str, bytes)):
            raise ValueError("an explicit trusted argv is required")
        argv = tuple(argv)
        if not argv or any(not isinstance(arg, str) or "\0" in arg for arg in argv):
            raise ValueError("invalid attachment argv")
        executable = Path(argv[0])
        if not executable.is_absolute() or not executable.is_file() or not os.access(executable, os.X_OK):
            raise ValueError("existing absolute executable required")
        cwd = Path(cwd)
        if not cwd.is_absolute() or not cwd.is_dir():
            raise ValueError("existing absolute cwd required")
        environment = dict(env)
        if any(not isinstance(key, str) or not key or "=" in key or "\0" in key
               or not isinstance(value, str) or "\0" in value for key, value in environment.items()):
            raise ValueError("invalid explicit environment")
        self._condition = threading.Condition()
        self._nonce = uuid.uuid4().hex
        self._expected_owner = dict(expected_owner) if expected_owner is not None else None
        self.owner = None
        self.executable_verification = None
        self._outgoing = deque([_frame(b"H", json.dumps({"cols": cols, "rows": rows, "nonce": self._nonce}).encode())])
        self._queued = len(self._outgoing[0])
        self._sending = b""
        self._sending_started = False
        self._terminal = bytearray()
        self._stderr = bytearray()
        self._stderr_truncated = False
        self._ready = False
        self._closing = False
        self._cleanup = False
        self._finished = False
        self._error = None
        self._deadline = time.monotonic() + READY_TIMEOUT
        self._process = subprocess.Popen(argv, cwd=str(cwd), env=environment, stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True,
                                         start_new_session=True, shell=False, bufsize=0)
        for stream in (self._process.stdin, self._process.stdout, self._process.stderr):
            os.set_blocking(stream.fileno(), False)
        self._thread = threading.Thread(target=self._pump, name="cluster-attachment", daemon=True)
        self._thread.start()
        with self._condition:
            while not self._ready and not self._finished and self._error is None:
                remaining = self._deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)
            ready = self._ready and self._error is None
        if not ready:
            try:
                self.close()
            except OSError:
                pass
            with self._condition:
                diagnostics = {"stderr": bytes(self._stderr), "stderr_truncated": self._stderr_truncated,
                    "passenger_returncode": self._process.returncode, "passenger_finished": self._finished,
                    "remote_cleanup_receipt": self._cleanup}
            raise AttachmentStartError(self._error or "remote attachment handshake timed out", diagnostics)

    @property
    def pid(self):
        return self._process.pid

    @property
    def closed(self):
        with self._condition:
            return self._finished

    def _enqueue(self, payload):
        if self._queued + len(payload) > MAX_QUEUE:
            return False
        self._outgoing.append(payload)
        self._queued += len(payload)
        self._condition.notify_all()
        return True

    def _received(self, kind, data):
        if kind == b"A":
            if self._ready or len(data) > 8192:
                raise OSError("invalid remote handshake")
            value = json.loads(data)
            if (set(value) != {"nonce", "owner", "executable_verification"}
                    or value["nonce"] != self._nonce or not isinstance(value["owner"], dict)
                    or value["executable_verification"] not in EXECUTABLE_VERIFICATION):
                raise OSError("remote handshake identity mismatch")
            if self._expected_owner is not None and value["owner"] != self._expected_owner:
                raise OSError("remote original owner changed")
            self.owner = value["owner"]
            self.executable_verification = value["executable_verification"]
            self._ready = True
        elif kind == b"O":
            if not self._ready or not 0 < len(data) <= CHUNK:
                raise OSError("invalid terminal output frame")
            if not self._closing:
                if len(self._terminal) + len(data) > MAX_QUEUE:
                    raise OSError("remote output exceeds queue bound")
                self._terminal.extend(data)
        elif kind == b"D":
            if len(data) > 1024:
                raise OSError("invalid cleanup receipt")
            value = json.loads(data)
            if value != {"nonce": self._nonce, "cleanup": "confirmed"}:
                raise OSError("cleanup receipt identity mismatch")
            self._cleanup = True
        elif kind == b"E":
            if len(data) > 2048:
                raise OSError("remote error exceeds bound")
            self._error = "remote attachment: " + data.decode("utf-8", errors="replace")
            # Keep draining to receive any verified cleanup receipt following it.
            self._closing = True
            self._deadline = min(self._deadline, time.monotonic() + CLOSE_TIMEOUT) if not self._ready else time.monotonic() + CLOSE_TIMEOUT
        else:
            raise OSError("unsupported remote attachment frame")
        self._condition.notify_all()

    def _pump(self):
        incoming = bytearray()
        stdout = self._process.stdout.fileno()
        stderr = self._process.stderr.fileno()
        stdin = self._process.stdin.fileno()
        stderr_open = True
        last_heartbeat = time.monotonic()
        try:
            while True:
                now = time.monotonic()
                with self._condition:
                    if (self._closing or not self._ready) and now >= self._deadline:
                        raise OSError("remote attachment cleanup timed out" if self._closing else "remote attachment handshake timed out")
                    if not self._closing and now - last_heartbeat >= HEARTBEAT_SECONDS:
                        if self._enqueue(_frame(b"P")):
                            last_heartbeat = now
                    if not self._sending and self._outgoing:
                        self._sending = self._outgoing.popleft()
                        self._sending_started = False
                    read_output = self._closing or len(self._terminal) <= MAX_QUEUE - MAX_FRAME
                    readers = ([stdout] if read_output else []) + ([stderr] if stderr_open else [])
                    writers = [stdin] if self._sending else []
                readable, writable, _ = select.select(readers, writers, [], 0.05)
                if stdin in writable:
                    with self._condition:
                        try:
                            count = os.write(stdin, self._sending)
                        except BlockingIOError:
                            count = 0
                        self._sending = self._sending[count:]
                        self._sending_started = self._sending_started or count > 0
                        self._queued -= count
                        self._condition.notify_all()
                if stderr in readable:
                    data = os.read(stderr, CHUNK)
                    if not data:
                        stderr_open = False
                    else:
                        with self._condition:
                            available = MAX_DIAGNOSTICS - len(self._stderr)
                            self._stderr.extend(data[:available])
                            if len(data) > available:
                                self._stderr_truncated = True
                                raise OSError("SSH diagnostics exceeded bound")
                if stdout in readable:
                    data = os.read(stdout, CHUNK)
                    if not data:
                        with self._condition:
                            if incoming or not self._cleanup:
                                raise OSError("remote attachment ended without confirmed cleanup")
                        break
                    incoming.extend(data)
                    if len(incoming) > MAX_FRAME + HEADER.size + CHUNK:
                        raise OSError("remote framing exceeds bound")
                    with self._condition:
                        for kind, payload in _take_frames(incoming):
                            self._received(kind, payload)
                        if self._cleanup:
                            break
        except BaseException as exc:
            with self._condition:
                self._error = self._error or (str(exc) or type(exc).__name__)
                self._condition.notify_all()
        finally:
            # A pre-supervisor dispatcher failure reports only on stderr. EOF
            # on stdout can arrive first; retain its bounded diagnostic tail
            # before closing the passenger pipes. Never put it in public errors.
            deadline = time.monotonic() + 0.15
            while stderr_open and time.monotonic() < deadline:
                try:
                    if not select.select([stderr], [], [], max(0, deadline - time.monotonic()))[0]:
                        break
                    data = os.read(stderr, CHUNK)
                    if not data:
                        break
                    with self._condition:
                        available = MAX_DIAGNOSTICS - len(self._stderr)
                        self._stderr.extend(data[:available])
                        if len(data) > available:
                            self._stderr_truncated = True
                            break
                except (OSError, ValueError):
                    break
            # This is only the newly spawned SSH passenger; never poll/reap and
            # then signal its potentially recycled process group.
            denied = False
            group_unknown = False
            try:
                if self._process.returncode is None:
                    try:
                        os.killpg(self._process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    except PermissionError:
                        # macOS can deny signalling a group during natural
                        # exit. Reap, then probe only; never signal that PID.
                        denied = True
                self._process.wait(timeout=0.75)
                if denied:
                    try:
                        os.killpg(self._process.pid, 0)
                    except ProcessLookupError:
                        pass
                    else:
                        group_unknown = True
            except (OSError, subprocess.TimeoutExpired):
                group_unknown = True
            if group_unknown:
                with self._condition:
                    self._error = "local SSH child cleanup unconfirmed"
                    self._cleanup = False
            for stream in (self._process.stdin, self._process.stdout, self._process.stderr):
                stream.close()
            with self._condition:
                self._finished = True
                self._outgoing.clear()
                self._sending = b""
                self._queued = 0
                self._condition.notify_all()

    def read(self, max_bytes=65536, *, timeout=0):
        if type(max_bytes) is not int or not 1 <= max_bytes <= 65536:
            raise ValueError("invalid read size")
        wait = _timeout(timeout)
        deadline = time.monotonic() + wait
        with self._condition:
            while not self._terminal and not self._finished and self._error is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)
            if self._terminal:
                data = bytes(self._terminal[:max_bytes])
                del self._terminal[:max_bytes]
                self._condition.notify_all()
                return data
            if self._error:
                raise OSError(self._error)
            return b"" if self._finished else None

    def write(self, data):
        if not isinstance(data, bytes) or len(data) > 65536:
            raise ValueError("write requires at most 65536 bytes")
        with self._condition:
            if self._closing or self._finished or self._error:
                raise BrokenPipeError("attachment is ending")
            count = min(len(data), CHUNK, max(0, MAX_QUEUE - self._queued - HEADER.size))
            if count:
                self._enqueue(_frame(b"I", data[:count]))
            return count

    def resize(self, cols, rows):
        _size(cols, rows)
        with self._condition:
            if self._closing or self._finished or self._error:
                raise BrokenPipeError("attachment is ending")
            if not self._enqueue(_frame(b"R", DIMENSIONS.pack(cols, rows))):
                raise BlockingIOError("attachment control queue is full")

    def close(self, *, timeout=CLOSE_TIMEOUT):
        wait = _timeout(timeout)
        deadline = time.monotonic() + min(wait, CLOSE_TIMEOUT)
        with self._condition:
            if not self._finished:
                self._closing = True
                self._terminal.clear()
                self._outgoing.clear()
                if not self._sending_started:
                    self._sending = b""
                # A partially written frame must finish; all wholly unsent
                # input is discarded before the connection's close frame.
                self._queued = len(self._sending)
                self._enqueue(_frame(b"C"))
                self._deadline = deadline
                while not self._finished:
                    remaining = deadline + 0.8 - time.monotonic()
                    if remaining <= 0:
                        break
                    self._condition.wait(remaining)
            if not self._finished or not self._cleanup:
                raise OSError(self._error or "remote attachment cleanup unconfirmed")
            return self._process.returncode


class FencedSshAttachment:
    def __init__(self,client,backend,key):self.client,self.backend,self.key=client,backend,key
    def read(self,*args,**kwargs):return self.client.read(*args,**kwargs)
    def write(self,*args,**kwargs):return self.client.write(*args,**kwargs)
    def resize(self,*args,**kwargs):return self.client.resize(*args,**kwargs)
    def close(self):
        try:return self.client.close()
        except Exception:
            with self.backend._lock:self.backend._fences[self.key]='cluster-remote-detach-unconfirmed'
            raise
