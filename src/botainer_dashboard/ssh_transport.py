"""Bounded SSH process transport for fixed, target-verified dashboard actions.

This module accepts internal argv, not browser-supplied commands. It preserves
unknown remote outcomes and never retries or signals a shared SSH master.
"""
from __future__ import annotations

import os
from pathlib import Path
import selectors
import signal
import subprocess
import time

MAX_SOURCE = 262144
STDOUT_LIMIT = 2 * 1024 * 1024
STDERR_LIMIT = 64 * 1024
TIMEOUT = 60.0

SSH_OPTIONS = (
    "BatchMode=yes", "ConnectTimeout=8", "StrictHostKeyChecking=yes",
    "ClearAllForwardings=yes", "ForwardAgent=no", "ForwardX11=no",
    "ControlMaster=no", "PermitLocalCommand=no", "ServerAliveInterval=15",
    "ServerAliveCountMax=2", "RequestTTY=no",
)


class Refused(ValueError):
    """An input or local safety precondition was refused before dispatch."""


def child_environment() -> dict[str, str]:
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "C", "LC_ALL": "C"}
    for name in ("HOME", "SSH_AUTH_SOCK"):
        if name in os.environ:
            env[name] = os.environ[name]
    return env


def bounded_exchange(argv: list[str], payload: bytes, *, timeout: float = TIMEOUT,
                     stdout_limit: int = STDOUT_LIMIT, stderr_limit: int = STDERR_LIMIT,
                     env: dict[str, str] | None = None) -> dict:
    """Bound real pipe I/O, including a child that never reads its stdin.

    Killing the local SSH process group does not establish the remote outcome.
    A multiplex master is outside this new child group and is never signalled.
    """
    if timeout <= 0 or stdout_limit < 0 or stderr_limit < 0 or len(payload) > MAX_SOURCE:
        raise Refused("Invalid transport bound")
    outputs = {"stdout": bytearray(), "stderr": bytearray()}
    limits = {"stdout": stdout_limit, "stderr": stderr_limit}
    started = time.monotonic()
    result = {"transport_status": "complete", "remote_outcome": "unknown", "returncode": None}
    try:
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=env, shell=False,
                                   start_new_session=True, bufsize=0)
    except OSError as exc:
        return {**result, "transport_status": "start-error", "remote_outcome": "not-dispatched",
                "error_type": type(exc).__name__, "stdout": b"", "stderr": b"",
                "elapsed_seconds": round(time.monotonic() - started, 3)}
    selector = selectors.DefaultSelector()
    try:
        for name in ("stdin", "stdout", "stderr"):
            stream = getattr(process, name)
            os.set_blocking(stream.fileno(), False)
            if name == "stdin" and not payload:
                stream.close()
            else:
                selector.register(stream, selectors.EVENT_WRITE if name == "stdin" else selectors.EVENT_READ, name)
        offset = 0
        while selector.get_map():
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                result["transport_status"] = "timeout"
                break
            exceeded = False
            for key, _ in selector.select(min(remaining, 0.1)):
                stream, name = key.fileobj, key.data
                if name == "stdin":
                    try:
                        offset += os.write(stream.fileno(), payload[offset:offset + 65536])
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        offset = len(payload)
                    if offset >= len(payload):
                        selector.unregister(stream)
                        stream.close()
                    continue
                try:
                    data = os.read(stream.fileno(), min(65536, limits[name] - len(outputs[name]) + 1))
                except BlockingIOError:
                    continue
                if not data:
                    selector.unregister(stream)
                    stream.close()
                    continue
                available = limits[name] - len(outputs[name])
                outputs[name].extend(data[:available])
                if len(data) > available:
                    result["transport_status"] = name + "-limit"
                    exceeded = True
                    break
            if exceeded:
                break
        if result["transport_status"] == "complete":
            try:
                process.wait(timeout=max(0.001, timeout - (time.monotonic() - started)))
            except subprocess.TimeoutExpired:
                result["transport_status"] = "timeout"
    except BaseException as exc:
        result.update(transport_status="interrupted", error_type=type(exc).__name__)
    finally:
        selector.close()
        # Do not poll/reap before signalling. A reaped leader's PID can already
        # belong to another process group. Our wait above sets returncode when
        # it reaps; otherwise this owned child remains unreaped through killpg.
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait()
        for name in ("stdin", "stdout", "stderr"):
            getattr(process, name).close()
    result.update(returncode=process.returncode, stdout=bytes(outputs["stdout"]),
                  stderr=bytes(outputs["stderr"]), elapsed_seconds=round(time.monotonic() - started, 3))
    if result["transport_status"] == "complete" and process.returncode != 255:
        result["remote_outcome"] = "helper-exited" if process.returncode == 0 else "helper-reported-failure"
    return result


def write_new(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
