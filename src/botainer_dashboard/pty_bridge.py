"""Small POSIX attachment-client PTY; no server or durable runtime ownership.

Only explicit trusted argv/cwd/env may reach this internal primitive. This is not
an authorization boundary or a generic command endpoint. A real Botainer adapter
must first prove that losing this client does not stop its durable owner.
"""

from __future__ import annotations

import errno
import fcntl
import math
import os
from pathlib import Path
import select
import signal
import struct
import subprocess
import sys
import termios
import tty
from typing import Mapping, Optional, Sequence


MAX_IO_BYTES = 65536
MAX_WAIT_SECONDS = 5.0
# Acquire a controlling terminal after Popen's setsid, without a threaded
# preexec_fn or a shell. -I -S prevents project/site imports in this fixed helper.
_BOOTSTRAP = (
    "import fcntl,os,sys,termios;"
    "fcntl.ioctl(0,termios.TIOCSCTTY,0);"
    "os.execve(sys.argv[1],sys.argv[1:],os.environ)"
)


def _timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("timeout must be a finite number")
    if not math.isfinite(value) or not 0 <= value <= MAX_WAIT_SECONDS:
        raise ValueError("timeout must be between zero and five seconds")
    return float(value)


def _size(cols: int, rows: int) -> bytes:
    if any(type(value) is not int or not 1 <= value <= 65535 for value in (cols, rows)):
        raise ValueError("rows and columns must be integers between 1 and 65535")
    return struct.pack("HHHH", rows, cols, 0, 0)


class PtyAttachment:
    """Spawn one attachment client in its own session/process group.

    Reads return bytes, None on timeout, and b'' at EOF. Writes are nonblocking
    and may be partial; the caller must account for the returned byte count.
    There is no hidden input queue. Use from one owner/thread at a time.
    Importing this module performs no I/O; constructing this class executes argv.
    """

    def __init__(self, argv: Sequence[str], *, cwd: Path,
                 env: Mapping[str, str], cols: int = 80, rows: int = 24,
                 terminal_newlines: bool = False, cooked: bool = False):
        if isinstance(argv, (str, bytes)):
            raise ValueError("argv must be an explicit argument sequence")
        args = tuple(argv)
        if not args or any(not isinstance(arg, str) or "\0" in arg for arg in args):
            raise ValueError("argv must contain strings without NUL bytes")
        executable = Path(args[0])
        if not executable.is_absolute() or not executable.is_file() or not os.access(executable, os.X_OK):
            raise ValueError("attachment executable must be an existing absolute executable")
        directory = Path(cwd)
        if not directory.is_absolute() or not directory.is_dir():
            raise ValueError("cwd must be an existing absolute directory")
        environment = dict(env)
        if any(not isinstance(key, str) or not key or "=" in key or "\0" in key
               or not isinstance(value, str) or "\0" in value
               for key, value in environment.items()):
            raise ValueError("invalid explicit environment")
        if not os.path.isabs(sys.executable):
            raise RuntimeError("an absolute trusted Python bootstrap interpreter is required")
        if type(terminal_newlines) is not bool:
            raise ValueError("terminal_newlines must be an explicit boolean")
        if type(cooked) is not bool:
            raise ValueError("cooked must be an explicit boolean")
        dimensions = _size(cols, rows)
        master, slave = os.openpty()
        try:
            if cooked:
                # Native CLI prompts need ordinary terminal line editing, echo,
                # Enter translation, EOF and signals. Container attach clients
                # retain the existing raw default and may set their own mode.
                attributes = termios.tcgetattr(slave)
                attributes[0] &= ~(termios.IGNCR | termios.INLCR | termios.ISTRIP)
                attributes[0] |= termios.BRKINT | termios.ICRNL | termios.IXON
                attributes[1] |= termios.OPOST | termios.ONLCR
                attributes[2] = (attributes[2] & ~(termios.CSIZE | termios.PARENB)) | termios.CS8 | termios.CREAD
                attributes[3] |= termios.ICANON | termios.ECHO | termios.ECHOE | termios.ECHOK | termios.ISIG | termios.IEXTEN
                for name, value in (("VEOF", b"\x04"), ("VINTR", b"\x03"),
                                    ("VERASE", b"\x7f"), ("VKILL", b"\x15")):
                    attributes[6][getattr(termios, name)] = value
                termios.tcsetattr(slave, termios.TCSANOW, attributes)
            else:
                tty.setraw(slave, when=termios.TCSANOW)
            if terminal_newlines and not cooked:
                # A CLI banner expects normal terminal LF-to-CRLF output. Only
                # this opt-in initial state changes; a later tty.setraw (such as
                # Docker interactive attach) disables translation as usual.
                attributes = termios.tcgetattr(slave)
                attributes[1] |= termios.OPOST | termios.ONLCR
                termios.tcsetattr(slave, termios.TCSANOW, attributes)
            fcntl.ioctl(slave, termios.TIOCSWINSZ, dimensions)
            os.set_blocking(master, False)
            process = subprocess.Popen(
                [sys.executable, "-I", "-S", "-c", _BOOTSTRAP, *args],
                stdin=slave, stdout=slave, stderr=slave,
                cwd=str(directory), env=environment, close_fds=True,
                start_new_session=True, shell=False,
            )
        except BaseException:
            os.close(master)
            raise
        finally:
            os.close(slave)
        self._fd: Optional[int] = master
        self._process = process
        self._eof = False
        self._group_cleanup_confirmed = False

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def closed(self) -> bool:
        return self._fd is None

    def fileno(self) -> int:
        if self._fd is None:
            raise ValueError("attachment is closed")
        return self._fd

    def read(self, max_bytes: int = MAX_IO_BYTES, *, timeout: float = 0) -> Optional[bytes]:
        if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_IO_BYTES:
            raise ValueError("read size must be between 1 and 65536 bytes")
        wait = _timeout(timeout)
        fd = self.fileno()
        if self._eof:
            return b""
        if not select.select([fd], [], [], wait)[0]:
            return None
        try:
            data = os.read(fd, max_bytes)
        except BlockingIOError:
            return None
        except OSError as exc:
            if exc.errno != errno.EIO:
                raise
            data = b""  # Linux PTYs report EIO where other POSIX systems give EOF.
        if not data:
            self._eof = True
        return data

    def write(self, data: bytes) -> int:
        fd = self.fileno()
        if not isinstance(data, bytes) or len(data) > MAX_IO_BYTES:
            raise ValueError("write requires at most 65536 bytes")
        if self._eof:
            raise BrokenPipeError("attachment output has ended")
        try:
            return os.write(fd, data)
        except BlockingIOError:
            return 0

    def resize(self, cols: int, rows: int) -> None:
        fcntl.ioctl(self.fileno(), termios.TIOCSWINSZ, _size(cols, rows))

    def _confirm_group_absent(self) -> None:
        """Read-only probe after reap; never signal a possibly recycled group."""
        try:
            os.killpg(self.pid, 0)
        except ProcessLookupError:
            self._group_cleanup_confirmed = True
            return
        except PermissionError:
            pass
        raise PermissionError(errno.EPERM, "attachment group cleanup is unconfirmed")

    def _close_master(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def close(self, *, timeout: float = 1.0) -> int:
        """Kill/reap only this attachment group; allow bounded natural-exit races.

        On macOS a just-exiting group may reject SIGKILL before its leader is
        waitable. Give natural exit the caller's bounded interval, then require
        the group to be absent. A reaped PID is used only for read-only probes.
        Unknown cleanup preserves the PTY and raises, so no implicit HUP is sent
        through a live client and a later caller cannot mistake it for success.
        This is not proof of durable runtime ownership or remote-owner survival.
        """
        wait = _timeout(timeout)
        if self._process.returncode is not None:
            if not self._group_cleanup_confirmed:
                self._confirm_group_absent()
            self._close_master()
            return self._process.returncode
        try:
            try:
                os.killpg(self.pid, signal.SIGKILL)
                self._group_cleanup_confirmed = True
            except ProcessLookupError:
                self._group_cleanup_confirmed = True
            except PermissionError as denied:
                try:
                    result = self._process.wait(timeout=wait)
                except subprocess.TimeoutExpired:
                    raise denied from None
                self._confirm_group_absent()
                return result
        finally:
            if self._group_cleanup_confirmed:
                self._close_master()
        return self._process.wait(timeout=wait)

    def __enter__(self) -> PtyAttachment:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
