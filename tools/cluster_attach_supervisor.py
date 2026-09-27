#!/usr/bin/env python3
"""Compute-node attachment lease: owns a Screen client, never its durable server.

The trusted dispatcher runs this reviewed file through ``srun --unbuffered``
without --pty. All stdio is framed; only this process creates the client PTY.
EOF, explicit close, protocol failure or heartbeat expiry cleans this client.
"""
from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import select
import signal
import socket
import stat
import struct
import subprocess
import sys
import termios
import time
import tty

HEADER = struct.Struct("!cI")
DIMENSIONS = struct.Struct("!HH")
MAX_FRAME = 65536
CHUNK = 32768
MAX_QUEUE = 262144
LEASE_SECONDS = 12.0
BOOTSTRAP = "import fcntl,os,sys,termios;fcntl.ioctl(0,termios.TIOCSCTTY,0);os.execve(sys.argv[1],sys.argv[1:],os.environ)"
OWNER_FIELDS = {"node", "screen", "pid", "start_ticks", "uid", "boot_id", "job_start_time", "screen_sha256"}
EXECUTABLE_VERIFICATION = {"procfs-executable-and-pinned-screen-sha256",
                           "restricted-procfs-pinned-root-setgid-screen"}
# One reviewed binary/system-configuration combination. Another installation
# requires explicit qualification; this is not a generic Screen safety claim.
QUALIFIED_SCREEN_POLICIES = {
    "3e23a70aa26849bd1a67aae2e85a6396abe4bacd0c23a58a3ada6b92908f77c5": {
        "/etc/screenrc": "b2e58047e499bc19df41fed5e6a4fdb9486efc76ee211f4a2e04fdbfe046b628",
        "/usr/local/etc/screenrc": None,
    },
}


class Refused(RuntimeError):
    pass


def frame(kind, data=b""):
    if len(kind) != 1 or len(data) > MAX_FRAME:
        raise Refused("invalid frame")
    return HEADER.pack(kind, len(data)) + data


def take_frames(buffer):
    result = []
    while len(buffer) >= HEADER.size:
        kind, size = HEADER.unpack_from(buffer)
        if size > MAX_FRAME:
            raise Refused("frame exceeds limit")
        if len(buffer) < HEADER.size + size:
            break
        result.append((kind, bytes(buffer[HEADER.size:HEADER.size + size])))
        del buffer[:HEADER.size + size]
    return result


def dimensions(cols, rows):
    if any(type(value) is not int or not 1 <= value <= 65535 for value in (cols, rows)):
        raise Refused("invalid terminal dimensions")
    return struct.pack("HHHH", rows, cols, 0, 0)


def literalize_screen_input(data):
    """Quote every Screen prefix for the pinned ``escape ^Aa`` setting.

    Ctrl-A+a is Screen's literal Ctrl-A command. Translating each prefix before
    queueing prevents split input frames from becoming Screen host commands.
    Other bytes retain their exact values inside the container's terminal.
    """
    require(isinstance(data, bytes) and len(data) <= CHUNK, "invalid terminal input chunk")
    return data.replace(b"\x01", b"\x01a")


def queue_screen_input(pending, data):
    translated = literalize_screen_input(data)
    require(0 < len(data) and len(pending) + len(translated) <= MAX_QUEUE,
            "terminal input exceeds bound after Screen-prefix quoting")
    pending.extend(translated)


def require(condition, reason):
    if not condition:
        raise Refused(reason)


def trusted_screen_metadata(info, *, directory=False):
    require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            and info.st_uid == 0 and not info.st_mode & 0o022,
            "Screen installation must be root-owned and not group/world-writable")
    if not directory:
        require(info.st_mode & 0o111 and 0 < info.st_size <= 32 * 1024 * 1024,
                "invalid Screen executable")


def read_administrator_file(path, *, limit, absent_ok=False):
    """Walk fixed absolute paths without following any symlink component."""
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts, "invalid administrator path")
    parent = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        trusted_screen_metadata(os.fstat(parent), directory=True)
        for component in path.parts[1:-1]:
            try:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            except FileNotFoundError:
                if absent_ok:
                    return None
                raise
            os.close(parent)
            parent = child
            trusted_screen_metadata(os.fstat(parent), directory=True)
        try:
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        except FileNotFoundError:
            if absent_ok:
                return None
            raise
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and not before.st_mode & 0o022,
                    "administrator file must be root-owned and not shared-writable")
            require(before.st_size <= limit, "administrator file exceeds size limit")
            data = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
            require(len(data) <= limit and len(data) == before.st_size
                    and (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns),
                    "administrator file changed while reading")
            return data, before
    finally:
        os.close(parent)


def verify_screen_policy():
    binary, info = read_administrator_file("/usr/bin/screen", limit=32 * 1024 * 1024)
    trusted_screen_metadata(info)
    digest = hashlib.sha256(binary).hexdigest()
    require(digest in QUALIFIED_SCREEN_POLICIES, "Screen binary requires site qualification")
    observed = {}
    for name, expected in QUALIFIED_SCREEN_POLICIES[digest].items():
        result = read_administrator_file(name, limit=65536, absent_ok=True)
        actual = hashlib.sha256(result[0]).hexdigest() if result is not None else None
        require(actual == expected, "Screen system configuration requires site qualification: " + name)
        observed[name] = actual
    return {"screen_sha256": digest, "system_screenrc_sha256": observed,
            "qualification": "exact reviewed binary and system configuration"}


def screen_binary_identity(binary):
    # The resolved system path and every ancestor must remain administrator
    # controlled. Read through an exact non-symlink descriptor and pin bytes.
    for parent in binary.parents:
        trusted_screen_metadata(parent.stat(), directory=True)
    descriptor = os.open(binary, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        trusted_screen_metadata(before)
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            require(total <= 32 * 1024 * 1024, "Screen executable exceeded bound")
            digest.update(chunk)
        after = os.fstat(stream.fileno())
        require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                and total == before.st_size, "Screen executable changed while reading")
    return digest.hexdigest(), before.st_mode


def verify_screen_executable(process, owner):
    binary = Path("/usr/bin/screen").resolve(strict=True)
    expected = owner["screen_sha256"]
    require(isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{64}", expected), "invalid Screen executable digest")
    digest, mode = screen_binary_identity(binary)
    require(digest == expected, "pinned Screen executable changed")
    try:
        observed = (process / "exe").resolve(strict=True)
    except PermissionError:
        # Setgid Screen can make /proc/PID/exe unreadable to its own user. This
        # mode does NOT claim procfs executable identity: the other pinned owner
        # fields and known launch path remain required, plus administrator-owned
        # setgid executable bytes and administrator-controlled parent paths.
        require(mode & stat.S_ISGID, "restricted procfs requires a trusted setgid Screen installation")
        return "restricted-procfs-pinned-root-setgid-screen"
    require(observed == binary, "not the Screen server executable")
    return "procfs-executable-and-pinned-screen-sha256"


def check_trial_root(root):
    require(root.is_absolute() and root.resolve(strict=True) == root
            and root.name.startswith("botainer-dashboard-test-"), "invalid attachment root")
    info = root.stat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077,
            "attachment root is not private")


def check_owner(root, owner):
    require(set(owner) == OWNER_FIELDS, "invalid owner identity")
    check_trial_root(root)
    directory = root / "scr"
    info = directory.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077,
            "Screen directory is not private")
    require(owner["node"] == socket.gethostname() and owner["uid"] == os.getuid(), "owner node/account changed")
    require(type(owner["pid"]) is int and owner["pid"] > 1
            and isinstance(owner["screen"], str)
            and re.fullmatch(str(owner["pid"]) + r"\.botainer-[0-9]+", owner["screen"]), "invalid Screen identity")
    require(Path("/proc/sys/kernel/random/boot_id").read_text().strip() == owner["boot_id"], "compute node rebooted")
    info = (directory / owner["screen"]).lstat()
    require(stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid(), "Screen socket changed")
    process = Path("/proc") / str(owner["pid"])
    require(process.stat().st_uid == os.getuid(), "Screen account changed")
    ticks = (process / "stat").read_text().rsplit(")", 1)[1].split()[19]
    require(ticks == owner["start_ticks"], "Screen process was replaced")
    verification = verify_screen_executable(process, owner)
    require(owner["screen"].split(".", 1)[1].encode() in (process / "cmdline").read_bytes().split(b"\0"),
            "Screen session name changed")
    return verification


def writer_lock(root):
    descriptor = os.open(root / "attach-writer.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077,
                "invalid attachment lock")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Refused("another dashboard attachment owns this trial") from exc
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def close_client(process, master):
    """Signal only this unreaped attach child; never a PID from a saved record."""
    if process.returncode is None:
        try:
            os.killpg(process.pid, signal.SIGHUP)
        except ProcessLookupError:
            pass
    os.close(master)
    try:
        return process.wait(timeout=0.75)
    except subprocess.TimeoutExpired:
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        return process.wait(timeout=0.75)


def send_final(data, output_fd=1):
    deadline = time.monotonic() + 0.75
    while data and time.monotonic() < deadline:
        if not select.select([], [output_fd], [], max(0, deadline - time.monotonic()))[1]:
            break
        try:
            data = data[os.write(output_fd, data):]
        except BlockingIOError:
            continue
        except BrokenPipeError:
            break


def supervise(argv, env, owner, *, verify_attached, verify_detached,
              executable_verification, input_fd=0, output_fd=1, lease_seconds=LEASE_SECONDS):
    """Internal trusted-argv primitive; CLI first validates owner and owns lock.

    No terminal byte is parsed as a control frame: input I and output O payloads
    are opaque. A lease renews only on a complete valid control/input frame.
    """
    require(0 < lease_seconds <= LEASE_SECONDS, "invalid lease")
    require(executable_verification in EXECUTABLE_VERIFICATION, "invalid executable verification mode")
    os.set_blocking(input_fd, False)
    os.set_blocking(output_fd, False)
    received = bytearray()
    outgoing = bytearray()
    pending_input = bytearray()
    process = None
    master = None
    nonce = None
    closing = False
    failure = None
    last_activity = time.monotonic()
    try:
        while not closing:
            remaining = lease_seconds - (time.monotonic() - last_activity)
            if remaining <= 0:
                raise Refused("attachment heartbeat expired")
            readers = [input_fd]
            if master is not None and len(outgoing) <= MAX_QUEUE - CHUNK - HEADER.size:
                readers.append(master)
            writers = ([output_fd] if outgoing else []) + ([master] if master is not None and pending_input else [])
            ready_r, ready_w, _ = select.select(readers, writers, [], min(remaining, 0.1))
            if input_fd in ready_r:
                chunk = os.read(input_fd, CHUNK)
                if not chunk:
                    closing = True
                else:
                    received.extend(chunk)
                    require(len(received) <= MAX_FRAME + HEADER.size + CHUNK, "input framing overflow")
                    for kind, data in take_frames(received):
                        if nonce is None:
                            require(kind == b"H" and len(data) <= 1024, "initial handshake required")
                            hello = json.loads(data)
                            require(set(hello) == {"cols", "rows", "nonce"}, "invalid handshake")
                            require(isinstance(hello["nonce"], str) and re.fullmatch(r"[0-9a-f]{32}", hello["nonce"]), "invalid handshake nonce")
                            size = dimensions(hello["cols"], hello["rows"])
                            nonce = hello["nonce"]
                            master, slave = os.openpty()
                            try:
                                tty.setraw(slave, when=termios.TCSANOW)
                                fcntl.ioctl(slave, termios.TIOCSWINSZ, size)
                                process = subprocess.Popen([sys.executable, "-I", "-S", "-c", BOOTSTRAP, *argv],
                                                           stdin=slave, stdout=slave, stderr=slave,
                                                           env=env, close_fds=True, start_new_session=True, shell=False)
                            finally:
                                os.close(slave)
                            os.set_blocking(master, False)
                            verify_attached(process)
                            outgoing.extend(frame(b"A", json.dumps({"nonce": nonce, "owner": owner,
                                "executable_verification": executable_verification}, sort_keys=True).encode()))
                        elif kind == b"P":
                            require(not data, "invalid heartbeat")
                        elif kind == b"I":
                            queue_screen_input(pending_input, data)
                        elif kind == b"R":
                            require(len(data) == DIMENSIONS.size, "invalid resize")
                            cols, rows = DIMENSIONS.unpack(data)
                            fcntl.ioctl(master, termios.TIOCSWINSZ, dimensions(cols, rows))
                        elif kind == b"C":
                            require(not data, "invalid close")
                            closing = True
                        else:
                            raise Refused("unsupported attachment frame")
                        last_activity = time.monotonic()
                        if closing:
                            break
            if closing:
                break
            if master in ready_r:
                try:
                    chunk = os.read(master, CHUNK)
                except OSError as exc:
                    if exc.errno != errno.EIO:
                        raise
                    chunk = b""
                if not chunk:
                    closing = True
                else:
                    outgoing.extend(frame(b"O", chunk))
            if output_fd in ready_w:
                try:
                    del outgoing[:os.write(output_fd, outgoing)]
                except BlockingIOError:
                    pass
            if master in ready_w and pending_input:
                try:
                    del pending_input[:os.write(master, pending_input)]
                except BlockingIOError:
                    pass
    except BaseException as exc:
        failure = str(exc)[:1024] or type(exc).__name__
    finally:
        cleanup = process is None
        try:
            if process is not None:
                close_client(process, master)
                master = None
                verify_detached()
                cleanup = True
            elif master is not None:
                os.close(master)
                master = None
        except BaseException as exc:
            failure = "attachment cleanup unconfirmed: " + (str(exc)[:512] or type(exc).__name__)
        # Discard queued output/input on close. The final frame is never delayed
        # behind unbounded terminal output. A partial frame must be finished.
        # outgoing may start mid-frame after a short pipe write; preserve it
        # to retain framing. Send the bounded remainder and receipts together;
        # if the peer stays blocked, never send a new header after truncation.
        final = bytes(outgoing)
        if failure:
            final += frame(b"E", failure.encode("utf-8", errors="replace")[:2048])
        if cleanup and nonce is not None:
            final += frame(b"D", json.dumps({"nonce": nonce, "cleanup": "confirmed"}).encode())
        send_final(final, output_fd)
    return 0 if cleanup and not failure else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--owner-json")
    action.add_argument("--check-policy", action="store_true")
    args = parser.parse_args()
    if args.check_policy:
        try:
            check_trial_root(Path(args.root))
            print(json.dumps(verify_screen_policy(), sort_keys=True))
            return 0
        except (OSError, RuntimeError, ValueError) as exc:
            print("Screen site policy refused: " + str(exc), file=sys.stderr)
            return 2
    lock = None
    def interrupted(signum, _frame):
        raise Refused("attachment supervisor interrupted by signal " + str(signum))
    for signum in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, interrupted)
    try:
        root = Path(args.root)
        verify_screen_policy()
        require(len(args.owner_json.encode()) <= 4096, "owner descriptor too large")
        owner = json.loads(args.owner_json)
        verification = check_owner(root, owner)
        lock = writer_lock(root)
        require(check_owner(root, owner) == verification, "owner verification mode changed")
        env = {"HOME": str(root), "SCREENDIR": str(root / "scr"), "TERM": "xterm-256color",
               "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
               "SYSSCREENRC": "/dev/null", "SYSTEM_SCREENRC": "/dev/null"}

        def screen_state(expected):
            result = subprocess.run(["/usr/bin/screen", "-ls", owner["screen"]], env=env,
                                    stdin=subprocess.DEVNULL, capture_output=True, timeout=0.5, check=False)
            require(len(result.stdout) <= 16384 and len(result.stderr) <= 16384, "Screen status exceeded bound")
            return result.returncode == 0 and re.search(
                rb"(?m)^\s*" + re.escape(owner["screen"].encode()) + rb"\s+[^\r\n]*\("
                + expected.encode() + rb"\)\s*$", result.stdout) is not None

        def detached(wait_seconds=0):
            deadline = time.monotonic() + wait_seconds
            while True:
                require(check_owner(root, owner) == verification, "owner verification mode changed")
                if screen_state("Detached"):
                    return
                require(time.monotonic() < deadline, "original Screen owner has not confirmed detached")
                time.sleep(0.05)

        def attached(process):
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                require(check_owner(root, owner) == verification, "owner verification mode changed")
                child = Path("/proc") / str(process.pid)
                require(child.stat().st_uid == os.getuid(), "attach client account changed")
                command = (child / "cmdline").read_bytes().rstrip(b"\0").split(b"\0")
                if (command and command[0].rsplit(b"/", 1)[-1].lower() == b"screen"
                        and command[1:] == [b"-r", owner["screen"].encode()] and screen_state("Attached")):
                    return
                time.sleep(0.05)
            raise Refused("Screen client did not confirm attachment")

        detached()  # Never claim or take over an already-attached display.
        return supervise(["/usr/bin/screen", "-r", owner["screen"]], env, owner,
                         verify_attached=attached, verify_detached=lambda: detached(0.75),
                         executable_verification=verification)
    except BaseException as exc:
        os.set_blocking(1, False)
        send_final(frame(b"E", (str(exc)[:1024] or type(exc).__name__).encode()))
        return 2
    finally:
        if lock is not None:
            os.close(lock)


if __name__ == "__main__":
    raise SystemExit(main())
