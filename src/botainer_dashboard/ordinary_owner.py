"""Private tmux ownership for a native Botainer CLI console.

This internal primitive accepts only already-reviewed argv/cwd/environment. It
does not discover programs, execute a shell, launch on observation, or replace
an uncertain owner. One server per operation retains the CLI across browser and
service disconnects, including its last screen after the CLI exits.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
import time
import uuid

from .backend_errors import BackendUnavailable
from .control_paths import ControlRootError, validate_local_control_root
from .inventory import InventoryError, bounded_run
from .pairing import read_private_json, write_private_json
from .pty_bridge import PtyAttachment


_HEX = re.compile(r"[0-9a-f]{32}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_FORMAT = ("#{pid}\t#{session_id}\t#{window_id}\t#{pane_id}\t#{pane_pid}\t"
           "#{session_created}\t#{socket_path}\t#{@dashboard_cli_owner}\t#{session_name}\t"
           "#{pane_dead}\t#{pane_dead_status}\t#{pane_dead_signal}\t#{session_windows}")
_CONFIG = """set-option -g exit-unattached off
set-option -g exit-empty on
set-option -g destroy-unattached off
set-option -g update-environment ''
set-option -g prefix None
set-option -g prefix2 None
unbind-key -a
unbind-key -a -T root
set-option -g status off
set-option -g set-titles off
set-option -g set-clipboard off
set-option -g mouse off
set-option -g history-limit 2000
set-window-option -g remain-on-exit on
set-window-option -g allow-rename off
set-window-option -g allow-set-title off
set-window-option -g allow-passthrough off
set-window-option -g automatic-rename off
set-window-option -g window-size latest
"""
_UNVERIFIED = "ordinary-owner-unverified"
_CAPTURE_LIMIT = 262144


def _refuse(code=_UNVERIFIED):
    raise BackendUnavailable(code)


def _identity(info):
    return [info.st_dev, info.st_ino]


def _canonical(path, *, directory=True):
    path = Path(path)
    if (not path.is_absolute() or ".." in path.parts or path.resolve(strict=True) != path
            or any(ord(c) < 32 or ord(c) == 127 for c in str(path))):
        _refuse()
    info = path.lstat()
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        _refuse()
    return path, info


def _private_folder(path):
    path, info = _canonical(path)
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        _refuse()
    return path, info


def _private_bytes(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_nlink != 1 or info.st_size > limit):
            _refuse()
        data = stream.read(limit + 1)
        if len(data) > limit:
            _refuse()
        return data


def _environment(env):
    value = dict(env)
    if any(not isinstance(key, str) or not key or "=" in key or "\0" in key
           or not isinstance(text, str) or "\0" in text for key, text in value.items()):
        _refuse("ordinary-owner-environment-invalid")
    # Parent terminal and dynamic-loader state must not redirect this private
    # server. The caller still owns the broader reviewed native CLI environment.
    if any(key in {"TMUX", "TMUX_PANE"} or key.startswith(("LD_", "DYLD_")) for key in value):
        _refuse("ordinary-owner-environment-invalid")
    return value


class OrdinaryOwnerAttachment:
    """The writer lease outlives uncertainty about this client process only."""
    def __init__(self, client, writer_fd, owner, record):
        self.client, self.writer_fd = client, writer_fd
        self.owner, self.record = owner, record
        self.next_observation = 0
        self.ended = False

    def _observe(self):
        if time.monotonic() >= self.next_observation:
            status = self.owner.observe(self.record)
            if status["state"] == "unknown":
                _refuse()
            self.ended = status["state"] == "ended"
            self.next_observation = time.monotonic() + 1

    def read(self, max_bytes, *, timeout=0):
        self._observe()
        data = self.client.read(max_bytes, timeout=0 if self.ended else timeout)
        # remain-on-exit retains output and ownership, not a permanently
        # writable browser console. Drain rendered bytes, then finish this view.
        return b"" if self.ended and data is None else data

    def write(self, data):
        self._observe()
        if self.ended:
            raise BrokenPipeError("native CLI console has ended")
        return self.client.write(data)

    def resize(self, cols, rows):
        return self.client.resize(cols, rows)

    def close(self):
        # PtyAttachment kills only its freshly created client process group.
        # It never contains the already-detached tmux server or CLI pane.
        result = self.client.close(timeout=1)
        if self.writer_fd is not None:
            os.close(self.writer_fd)
            self.writer_fd = None
        return result


class OrdinaryCliOwner:
    def __init__(self, control_root, tmux_path, tmux_sha256, environment, *, runner=None, attachment_factory=None):
        try:
            self.root, info = validate_local_control_root(control_root)
        except ControlRootError:
            _refuse()
        if not isinstance(tmux_sha256, str) or not _HASH.fullmatch(tmux_sha256):
            _refuse()
        self.root_identity = _identity(info)
        self.tmux_path = Path(tmux_path)
        self.tmux_sha256 = tmux_sha256
        self.environment = _environment(environment)
        self.runner = runner or bounded_run
        self.attachment_factory = attachment_factory or PtyAttachment
        self._verify_tool()

    def _verify_tool(self):
        path, info = _canonical(self.tmux_path, directory=False)
        if not os.access(path, os.X_OK) or info.st_size > 64 * 1024 * 1024:
            _refuse()
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            digest = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
            after = os.fstat(stream.fileno())
        fields = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_mode)
        if fields(before) != fields(after) or fields(info) != fields(after) or digest.hexdigest() != self.tmux_sha256:
            _refuse()

    def _root(self):
        try:
            root, info = validate_local_control_root(self.root)
        except ControlRootError:
            _refuse()
        if _identity(info) != self.root_identity:
            _refuse()
        return root

    def _folder(self, record):
        if (not isinstance(record, dict) or type(record.get("version")) is not int or record["version"] != 1
                or not isinstance(record.get("id"), str) or not _HEX.fullmatch(record["id"])
                or not isinstance(record.get("nonce"), str) or not _HEX.fullmatch(record["nonce"])
                or record.get("rootIdentity") != self.root_identity
                or record.get("tmuxPath") != str(self.tmux_path) or record.get("tmuxSha256") != self.tmux_sha256):
            _refuse()
        folder, info = _private_folder(self._root() / record["id"])
        if _identity(info) != record.get("controlIdentity"):
            _refuse()
        expected = (_CONFIG + "set-option -g @dashboard_cli_owner " + record["nonce"] + "\n").encode()
        if _private_bytes(folder / "tmux.conf", 8192) != expected:
            _refuse()
        return folder

    def _run(self, record, *args, create=False, env=None):
        self._verify_tool()
        folder = self._folder(record)
        argv = (str(self.tmux_path), *( () if create else ("-N",)), "-S", str(folder / "s"),
                "-f", str(folder / "tmux.conf"), *args)
        return self.runner(argv, cwd=self.root, env=self.environment if env is None else env, timeout=5)

    def _socket(self, record):
        info = (self._folder(record) / "s").lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o007:
            _refuse()
        return _identity(info)

    def _observe(self, record, *, initial=False):
        before = self._socket(record)
        saved = record.get("owner")
        if not initial and not isinstance(saved, dict):
            _refuse()
        target = "%0" if initial else saved.get("paneId")
        if not isinstance(target, str) or not re.fullmatch(r"%[0-9]+", target):
            _refuse()
        raw = self._run(record, "display-message", "-p", "-t", target, _FORMAT)
        if not isinstance(raw, bytes) or len(raw) > 8192:
            _refuse()
        parts = raw.decode("utf-8").strip("\r\n").split("\t")
        if len(parts) != 13:
            _refuse()
        pid, sid, wid, pane, child, created, socket_path, nonce, name, dead, code, sig, windows = parts
        if (not re.fullmatch(r"[1-9][0-9]*", pid) or not re.fullmatch(r"\$[0-9]+", sid)
                or not re.fullmatch(r"@[0-9]+", wid) or not re.fullmatch(r"%[0-9]+", pane)
                or not re.fullmatch(r"[1-9][0-9]*", child) or not re.fullmatch(r"[0-9]+", created)
                or socket_path != str(self._folder(record) / "s") or nonce != record["nonce"]
                or name != "ordinary-" + record["id"] or dead not in {"0", "1"} or windows != "1"
                or code and not re.fullmatch(r"[0-9]+", code) or sig and not re.fullmatch(r"[0-9]+", sig)):
            _refuse()
        owner = {"serverPid": int(pid), "sessionId": sid, "windowId": wid, "paneId": pane,
                 "panePid": int(child), "sessionCreated": int(created), "socketIdentity": before, "nonce": nonce}
        if self._socket(record) != before or (not initial and owner != saved):
            _refuse()
        panes = self._run(record, "list-panes", "-a", "-F", "#{pane_id}")
        if panes != (pane + "\n").encode() or self._socket(record) != before:
            _refuse()
        status = {"state": "ended" if dead == "1" else "running", "exitCode": int(code) if code else None,
                  "exitSignal": int(sig) if sig else None}
        return owner, status

    def launch(self, operation_id, argv, *, cwd, env, cols=100, rows=30):
        if not isinstance(operation_id, str) or not _HEX.fullmatch(operation_id):
            _refuse("ordinary-owner-operation-invalid")
        if (isinstance(argv, (str, bytes)) or not isinstance(argv, (tuple, list)) or not 2 <= len(argv) <= 128
                or any(not isinstance(arg, str) or "\0" in arg or ";" in arg for arg in argv)
                or not Path(argv[0]).is_absolute()):
            _refuse("ordinary-owner-argv-invalid")
        directory, _ = _canonical(cwd)
        # Keep the selected virtualenv launcher spelling in argv: replacing it
        # with its resolved interpreter changes Python's environment selection.
        # The caller pins that launcher's resolved target; we require it to be
        # an existing executable without resolving argv itself into a new one.
        executable, _ = _canonical(Path(argv[0]).resolve(strict=True), directory=False)
        if not os.access(executable, os.X_OK):
            _refuse("ordinary-owner-argv-invalid")
        environment = _environment(env)
        if type(cols) is not int or type(rows) is not int or not 2 <= cols <= 500 or not 1 <= rows <= 300:
            _refuse("ordinary-owner-dimensions-invalid")
        self._verify_tool()
        folder = self._root() / operation_id
        try:
            folder.mkdir(mode=0o700)
        except FileExistsError:
            _refuse("ordinary-owner-already-dispatched")
        record = {"version": 1, "id": operation_id, "nonce": uuid.uuid4().hex,
                  "rootIdentity": self.root_identity, "controlIdentity": _identity(folder.lstat()),
                  "tmuxPath": str(self.tmux_path), "tmuxSha256": self.tmux_sha256,
                  "state": "unknown", "code": _UNVERIFIED}
        config = (_CONFIG + "set-option -g @dashboard_cli_owner " + record["nonce"] + "\n").encode()
        fd = os.open(folder / "tmux.conf", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(config); stream.flush(); os.fsync(stream.fileno())
        self._folder(record)
        # Persist dispatch uncertainty before the first executable action.
        write_private_json(folder / "record.json", record)
        try:
            self._run(record, "new-session", "-d", "-E", "-s", "ordinary-" + operation_id,
                      "-c", str(directory), "-x", str(cols), "-y", str(rows), *argv, create=True, env=environment)
            owner, status = self._observe(record, initial=True)
            record.update(owner=owner, **status)
            record.pop("code", None)
        except (OSError, ValueError, InventoryError, BackendUnavailable):
            pass  # The sole dispatch is durable; never retry or adopt another owner.
        write_private_json(folder / "record.json", record)
        return record

    def recover(self, operation_id):
        if not isinstance(operation_id, str) or not _HEX.fullmatch(operation_id):
            _refuse("ordinary-owner-operation-invalid")
        try:
            folder, _ = _private_folder(self._root() / operation_id)
            record = read_private_json(folder / "record.json", max_bytes=16384)
            if record.get("id") != operation_id:
                _refuse()
            self._folder(record)
            return {**record, **self.observe(record)}
        except (OSError, ValueError, InventoryError, BackendUnavailable):
            raise BackendUnavailable(_UNVERIFIED) from None

    def _verified(self, record):
        try:
            return self._observe(record)
        except (OSError, ValueError, InventoryError, BackendUnavailable):
            raise BackendUnavailable(_UNVERIFIED) from None

    def observe(self, record):
        try:
            _owner, status = self._observe(record)
            return status
        except (OSError, ValueError, InventoryError, BackendUnavailable):
            return {"state": "unknown", "exitCode": None, "exitSignal": None, "code": _UNVERIFIED}

    def attach(self, record, writer_fd, cols, rows):
        _owner, status = self._verified(record)
        if status["state"] != "running":
            _refuse("ordinary-owner-not-running")
        if self._run(record, "list-clients", "-F", "#{client_pid}").strip():
            _refuse("ordinary-owner-already-attached")
        self._verified(record)
        folder = self._folder(record)
        client = self.attachment_factory(
            [str(self.tmux_path), "-N", "-S", str(folder / "s"), "-f", str(folder / "tmux.conf"),
             "attach-session", "-E", "-t", record["owner"]["sessionId"]],
            cwd=self.root, env=self.environment, cols=cols, rows=rows, cooked=True)
        return OrdinaryOwnerAttachment(client, writer_fd, self, record)

    def capture(self, record, max_bytes=_CAPTURE_LIMIT, *, allow_running=False):
        if type(max_bytes) is not int or not 1 <= max_bytes <= _CAPTURE_LIMIT or type(allow_running) is not bool:
            _refuse("ordinary-owner-capture-invalid")
        _owner, status = self._verified(record)
        if status["state"] != "ended" and not allow_running:
            _refuse("ordinary-owner-not-ended")
        output = self._run(record, "capture-pane", "-p", "-e", "-J", "-S", "-", "-E", "-", "-t", record["owner"]["paneId"])
        self._verified(record)
        if not isinstance(output, bytes) or len(output) > 4 * 1024 * 1024:
            _refuse()
        marker = b"[Earlier terminal output omitted.]\r\n"
        if len(output) <= max_bytes:
            return output
        return marker[:max_bytes] if max_bytes <= len(marker) else marker + output[-(max_bytes - len(marker)):]
