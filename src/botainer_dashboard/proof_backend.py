"""Disposable deterministic Botainer proof adapter, never a general runtime backend.

Only the privately prepared, hash-checked plan can select paths or commands.
There is no shell endpoint, image pull, setup command, or implicit source copy.
The constructor does not execute a command. Preparation and live exercise need
a separate controlled harness. A fixture proof does not qualify AI agents or clusters.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import threading
import time
import uuid
from typing import Any

from .backend_errors import BackendUnavailable
from .operations import OperationStore, OperationConflict
from .pty_bridge import PtyAttachment

SID = re.compile(r"[0-9a-f]{16}\Z")
CID = re.compile(r"[0-9a-f]{64}\Z")
MAX_OUTPUT = 2 * 1024 * 1024


def decode_launch_output(output: bytes | str) -> tuple[dict, dict]:
    """Decode the reviewed CLI's summary line followed by one launch result.

    Botainer's fixed ``start --json`` path prints both objects. Never scan past
    garbage or select the last object from ambiguous output. A summary alone is
    not a launch result, even when the CLI exits successfully after declining.
    """
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value: raise ValueError("duplicate JSON key")
            value[key] = item
        return value

    def invalid_constant(value):
        raise ValueError("non-JSON numeric constant")

    try:
        if isinstance(output, bytes): output = output.decode("utf-8")
        if not isinstance(output, str) or len(output.encode("utf-8")) > MAX_OUTPUT:
            raise ValueError("output limit")
        lines = output.splitlines()
        if len(lines) != 2 or any(not line.strip() for line in lines):
            raise ValueError("exactly summary and result required")
        summary, launch = [json.loads(line, object_pairs_hook=unique, parse_constant=invalid_constant) for line in lines]
        summary_fields = {"session_id", "project_root", "project_uuid", "image", "runtime", "network_mode",
                          "network_endpoints", "mounts", "auth_profile", "plugins_enabled", "entrypoint_wraps",
                          "nudge_enabled", "port_forwards", "auth_mode", "git_unprotected", "env_var_names",
                          "env_files", "host_hooks", "capabilities"}
        launch_fields = {"session_id", "container_id", "runtime", "nudge_supported", "project_root", "session_record"}
        if not isinstance(summary, dict) or set(summary) != summary_fields or not isinstance(launch, dict) or set(launch) != launch_fields:
            raise ValueError("unexpected output object")
        if (not isinstance(launch["session_id"], str) or not SID.fullmatch(launch["session_id"])
                or not isinstance(launch["container_id"], str) or not CID.fullmatch(launch["container_id"])
                or launch["runtime"] != "docker" or launch["nudge_supported"] is not False
                or any(summary[key] != launch[key] for key in ("session_id", "project_root", "runtime"))):
            raise ValueError("mismatched launch identity")
        if (not isinstance(launch["project_root"], str) or not isinstance(launch["session_record"], str)
                or not isinstance(summary["project_uuid"], str) or not summary["project_uuid"]):
            raise ValueError("invalid launch paths")
        for key in ("network_endpoints", "mounts", "plugins_enabled", "entrypoint_wraps", "port_forwards",
                    "env_var_names", "env_files", "host_hooks", "capabilities"):
            if not isinstance(summary[key], list): raise ValueError("invalid summary list")
        if any(type(summary[key]) is not bool for key in ("nudge_enabled", "git_unprotected")):
            raise ValueError("invalid summary boolean")
        return summary, launch
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise BackendUnavailable("proof-launch-output-invalid") from error


def digest(path: Path) -> str:
    if path.is_symlink() or path.resolve() != path.absolute() or not path.is_file():
        raise BackendUnavailable("proof-artifact-missing")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path, limit: int = MAX_OUTPUT) -> Any:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise BackendUnavailable("proof-record-invalid")
    return json.loads(path.read_text())


def private_json(path: Path, value: Any) -> None:
    if path.is_symlink() or path.parent.is_symlink():
        raise BackendUnavailable("proof-path-invalid")
    data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    temporary = path.with_name(path.name + f".{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(directory_fd)
        finally: os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def bounded_run(argv: list[str], *, cwd: Path, env: dict[str, str], timeout: float = 20,
                max_output: int = MAX_OUTPUT) -> tuple[int, bytes, bytes]:
    """No shell; bounded stdout+stderr and process group cleanup on failure."""
    process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True, shell=False, close_fds=True)
    selector = selectors.DefaultSelector()
    outputs = {"stdout": bytearray(), "stderr": bytearray()}
    assert process.stdout is not None and process.stderr is not None
    for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
        os.set_blocking(stream.fileno(), False); selector.register(stream, selectors.EVENT_READ, name)
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BackendUnavailable("proof-command-timeout")
            for key, _events in selector.select(min(remaining, .1)):
                data = os.read(key.fileobj.fileno(), 16384)
                if not data:
                    selector.unregister(key.fileobj)
                else:
                    outputs[key.data].extend(data)
                    if sum(map(len, outputs.values())) > max_output:
                        raise BackendUnavailable("proof-command-output-limit")
        result = process.wait(timeout=max(.01, deadline - time.monotonic()))
        return result, bytes(outputs["stdout"]), bytes(outputs["stderr"])
    except BaseException:
        try: os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        process.wait(timeout=5)
        raise
    finally:
        selector.close(); process.stdout.close(); process.stderr.close()


def validate_spec(spec: dict, plan: dict, project_uuid: str) -> None:
    """Validate the actual composed immutable spec against the narrow test grant."""
    project = Path(plan["project"]).resolve()
    state = Path(plan["state_root"]).resolve()
    fixture = Path(plan["fixture_readonly_source"]).resolve()
    project_state = state / "state" / project_uuid
    session_id = spec.get("session_id")
    if not isinstance(session_id, str) or not SID.fullmatch(session_id):
        raise BackendUnavailable("proof-spec-session-invalid")
    session_dir = project_state / "sessions" / session_id
    if (spec.get("project_root") != str(project) or spec.get("project_uuid") != project_uuid
            or spec.get("state_dir") != str(project_state)
            or spec.get("runtime") != "docker" or spec.get("image") != plan["docker"]["image"]
            or spec.get("network", {}).get("mode") != "none"
            or spec.get("plugins_enabled") != ["agent-dashboard-test"]
            or spec.get("entrypoint_wraps") != [["/bin/sh", "/mnt/dashboard-fixture/terminal.sh"]]):
        raise BackendUnavailable("proof-spec-outside-scope")
    for key in ("hooks", "sidecars", "port_forwards", "env_files", "module_env_path_prepends", "capabilities"):
        if spec.get(key):
            raise BackendUnavailable("proof-extra-capability")
    if spec.get("kernel_caps", {}).get("keep"):
        raise BackendUnavailable("proof-extra-kernel-capability")
    binds = spec.get("mount_plan", {}).get("binds", [])
    targets = set()
    for bind in binds:
        source = Path(bind.get("source", ""))
        if not source.is_absolute() or source.is_symlink():
            raise BackendUnavailable("proof-mount-source-invalid")
        source = source.resolve()
        target, mode = bind.get("target"), bind.get("mode")
        if target in targets:
            raise BackendUnavailable("proof-duplicate-mount")
        targets.add(target)
        if target == "/workspace":
            safe = source == project and mode == "rw"
        elif target in {"/packages", "/scratch", "/home/user"}:
            component = {"/packages": "packages", "/scratch": "scratch", "/home/user": "home"}[target]
            safe = source == project_state / component and mode == "rw"
        elif target == "/mnt/dashboard-fixture":
            safe = source == fixture and mode == "ro"
        elif target == "/workspace/.botainer":
            # This version renders null-bind as an RW bind of this generated
            # anchor. It masks the real project config; it is not an RO bind.
            safe = source == project_state / "data" / "null-bind-anchor" and mode == "null-bind"
        elif target in {"/workspace/.botainer/AGENT_ACCESS.txt", "/workspace/.botainer/AGENT_HINTS.md"}:
            safe = source == session_dir / Path(target).name and mode == "ro"
        else:
            safe = False
        if not safe:
            raise BackendUnavailable("proof-mount-outside-scope")
    required = {"/workspace", "/mnt/dashboard-fixture", "/workspace/.botainer",
                "/workspace/.botainer/AGENT_ACCESS.txt", "/workspace/.botainer/AGENT_HINTS.md",
                "/packages", "/scratch", "/home/user"}
    if targets != required:
        raise BackendUnavailable("proof-required-mount-missing")
    env = spec.get("env", {}).get("values", {})
    if any(any(part in key.upper() for part in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "CREDENTIAL")) for key in env):
        raise BackendUnavailable("proof-credential-environment")


def expected_docker_mounts(spec: dict, plan: dict, project_uuid: str) -> set[tuple[str, str, bool]]:
    """Map the validated source version's bind modes to Docker inspect RW."""
    validate_spec(spec, plan, project_uuid)
    return {(str(Path(bind["source"]).resolve()), bind["target"], bind["mode"] in {"rw", "null-bind"})
            for bind in spec["mount_plan"]["binds"]}


class ProofAttachment:
    def __init__(self, client: PtyAttachment, writer_fd: int):
        self.client = client; self.writer_fd = writer_fd

    def read(self, max_bytes: int, *, timeout: float = 0):
        return self.client.read(max_bytes, timeout=timeout)

    def write(self, data: bytes): return self.client.write(data)
    def resize(self, cols: int, rows: int): return self.client.resize(cols, rows)

    def close(self):
        # Kill only the attachment client group. The patched Docker attach has
        # signal forwarding disabled; loss-of-client survival is the proof.
        result = self.client.close(timeout=1)
        if self.writer_fd is not None:
            os.close(self.writer_fd); self.writer_fd = None
        return result


class ProofBackend:
    """One approved disposable project; lifecycle commands only through Botainer."""
    def __init__(self, plan_path: Path):
        self.plan_path = Path(plan_path).resolve()
        self.plan = read_json(self.plan_path)
        self.root = Path(self.plan["source"]["private_copy"]).parent
        self.receipt_path = self.root / "prepared.json"
        self.receipt = read_json(self.receipt_path)
        if self.receipt.get("plan_sha256") != digest(self.plan_path):
            raise BackendUnavailable("proof-plan-changed")
        self.project = Path(self.plan["project"]).resolve()
        self.project_uuid = self.receipt["project_uuid"]
        self.project_id = "proof-" + self.project_uuid
        self.namespace = "proof:" + hashlib.sha256((str(self.plan["state_root"]) + self.receipt["daemon_id"]).encode()).hexdigest()[:32]
        self.registry_path = self.root / "sessions.json"
        self.database = self.root / "operations.sqlite"
        self._thread_lock = threading.RLock()
        self.verify_files()

    def verify_files(self):
        if digest(self.plan_path) != self.receipt["plan_sha256"]:
            raise BackendUnavailable("proof-plan-changed")
        for executable in self.receipt.get("executables", []):
            if str(Path(executable["invocation"]).resolve()) != executable["canonical"]:
                raise BackendUnavailable("proof-executable-link-changed")
        for entry in self.receipt["protected_files"]:
            path = Path(entry["path"])
            if digest(path) != entry["sha256"]:
                raise BackendUnavailable("proof-artifact-changed")

    def environment(self):
        return {"PATH": str(Path(self.plan["docker"]["executable"]).parent) + ":/usr/bin:/bin:/usr/sbin:/sbin",
                "HOME": str(self.root), "MY_BOTAINER": self.plan["state_root"],
                "DOCKER_HOST": self.plan["docker"]["endpoint"], "DOCKER_CONFIG": str(self.root / "docker-config"),
                "LANG": "en_US.UTF-8", "TERM": "xterm-256color", "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1", "BOTAINER_NO_TIPS": "1"}

    def argv(self, *args):
        return [self.plan["interpreter"], "-I", "-B", str(self.root / "bootstrap.py"), "cli", *args]

    def _run(self, argv, timeout=20):
        return bounded_run(argv, cwd=self.project, env=self.environment(), timeout=timeout)

    def _docker(self, *args, timeout=15):
        return self._run([self.plan["docker"]["executable"], "--host", self.plan["docker"]["endpoint"], *args], timeout)

    def verify_daemon(self):
        code, stdout, _ = self._docker("info", "--format", "{{json .ID}}")
        if code or json.loads(stdout) != self.receipt["daemon_id"]:
            raise BackendUnavailable("proof-daemon-changed")
        code, stdout, _ = self._docker("image", "inspect", self.plan["docker"]["image"])
        value = json.loads(stdout) if not code else []
        if len(value) != 1 or value[0].get("Id") != self.plan["docker"]["image"]:
            raise BackendUnavailable("proof-image-changed")

    @contextmanager
    def locked(self):
        with self._thread_lock:
            fd = os.open(self.root / "dispatch.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd); raise BackendUnavailable("proof-operation-in-progress")
            try: yield
            finally: os.close(fd)

    def _registry(self):
        return read_json(self.registry_path) if self.registry_path.exists() else {"sessions": []}

    def _check_unregistered_records(self, registry):
        known = {entry["session_id"] for entry in registry["sessions"]}
        sessions_dir = Path(self.plan["state_root"]) / "state" / self.project_uuid / "sessions"
        if sessions_dir.exists():
            for path in sessions_dir.glob("*/spec.json"):
                record = read_json(path)
                handle = record.get("runtime_handle", {}).get("docker")
                if isinstance(handle, dict) and handle.get("container_id") and not record.get("ended_at") and record.get("session_id") not in known:
                    raise BackendUnavailable("proof-unregistered-runtime-record")

    def _record(self, sid):
        if not isinstance(sid, str) or not SID.fullmatch(sid):
            raise BackendUnavailable("proof-session-invalid")
        entries = [value for value in self._registry()["sessions"] if value["session_id"] == sid]
        if len(entries) != 1: raise BackendUnavailable("proof-session-unregistered")
        return entries[0]

    def _session_record(self, entry):
        value = read_json(Path(entry["record_dir"]) / "spec.json")
        docker_handle = value.get("runtime_handle", {}).get("docker")
        if (value.get("session_id") != entry["session_id"] or value.get("project_uuid") != self.project_uuid
                or value.get("project_root") != str(self.project)
                or not isinstance(docker_handle, dict) or docker_handle.get("container_id") != entry["container_id"]):
            raise BackendUnavailable("proof-session-record-changed")
        validate_spec(value["spec"], self.plan, self.project_uuid)
        return value

    def inspect_container(self, entry, *, missing_ok=False):
        code, stdout, stderr = self._docker("container", "inspect", entry["container_id"])
        if code:
            # Docker may be offline, not merely missing. Require an independent
            # successful exact-daemon query before interpreting the error.
            self.verify_daemon()
            if missing_ok and b"No such container" in stderr: return None
            raise BackendUnavailable("proof-container-unavailable")
        values = json.loads(stdout)
        if len(values) != 1: raise BackendUnavailable("proof-container-ambiguous")
        value = values[0]
        if (value.get("Id") != entry["container_id"] or value.get("Image") != self.plan["docker"]["image"]
                or value.get("HostConfig", {}).get("NetworkMode") != "none"
                or value.get("HostConfig", {}).get("PortBindings")
                or not value.get("Config", {}).get("OpenStdin") or not value.get("Config", {}).get("Tty")
                or value.get("Config", {}).get("Entrypoint") != ["/bin/sh"]
                or value.get("Config", {}).get("Cmd") != ["/mnt/dashboard-fixture/terminal.sh"]):
            raise BackendUnavailable("proof-container-outside-scope")
        if entry.get("started_at") and value.get("State", {}).get("StartedAt") != entry["started_at"]:
            raise BackendUnavailable("proof-original-container-changed")
        spec = self._session_record(entry)["spec"]
        expected = expected_docker_mounts(spec, self.plan, self.project_uuid)
        actual = {(mount.get("Source"), mount.get("Destination"), mount.get("RW")) for mount in value.get("Mounts", []) if mount.get("Type") == "bind"}
        if actual != expected: raise BackendUnavailable("proof-container-mounts-changed")
        return value

    def _view(self, entry, state):
        return {"contextNamespace": self.namespace, "runtimeId": entry["session_id"], "projectId": self.project_id,
                "label": "Deterministic terminal", "agent": "test fixture", "state": state,
                "createdAt": entry.get("started_at"), "capabilities": {"attachTerminal": state == "running", "stopSession": state == "running"}}

    def snapshot(self):
        self.verify_files()
        sessions = []
        with self.locked():
            self.verify_daemon()
            registry = self._registry()
            for entry in registry["sessions"]:
                try:
                    record = self._session_record(entry)
                    container = self.inspect_container(entry, missing_ok=True)
                    state = "running" if container and container["State"].get("Running") else "stopped" if record.get("ended_at") else "unknown"
                except (BackendUnavailable, ValueError, KeyError):
                    state = "unknown"
                sessions.append(self._view(entry, state))
            with OperationStore(self.database) as store:
                unresolved = bool(store.unresolved())
        return {"mode": "botainer-proof", "notice": "Disposable Botainer terminal proof using an isolated patched source and cached Alpine. This is a deterministic test process, not an AI agent.",
                "capabilities": {"startSession": not unresolved and all(s["state"] == "stopped" for s in sessions), "stopSession": True, "attachTerminal": True,
                                 "projectCreate": False, "configRead": False, "configWrite": False, "filesRead": False},
                "projects": [{"id": self.project_id, "name": "Disposable terminal proof", "path": str(self.project), "machineLabel": "This Mac", "installationLabel": "Isolated patched Botainer"}],
                "sessions": sessions}

    def preflight(self):
        self.verify_files(); self.verify_daemon()
        code, stdout, stderr = self._run([self.plan["interpreter"], "-I", "-B", str(self.root / "bootstrap.py"), "preflight"])
        private_json(self.root / "preflight-command.json", {"exit_code": code, "stderr": stderr.decode(errors="replace")})
        if code: raise BackendUnavailable("proof-composition-refused")
        result = json.loads(stdout)
        validate_spec(result["spec"], self.plan, self.project_uuid)
        argv = result["argv"]
        if argv[:4] != ["docker", "run", "--rm", "-dit"] or argv[argv.index("--pull") + 1] != "never":
            raise BackendUnavailable("proof-detached-argv-invalid")
        private_json(self.root / "preflight.json", result)
        return result

    def _launch_entry(self, result, request_id):
        sid = result["session_id"]
        expected = Path(self.plan["state_root"]).resolve() / "state" / self.project_uuid / "sessions" / sid
        if result["session_record"] != str(expected) or result["project_root"] != str(self.project):
            raise BackendUnavailable("proof-launch-record-invalid")
        return {"session_id": sid, "container_id": result["container_id"],
                "record_dir": str(expected), "request_id": request_id}

    def _persist_launch_entry(self, registry, candidate):
        matches = [entry for entry in registry["sessions"] if any(entry.get(key) == candidate[key]
                   for key in ("session_id", "container_id", "request_id"))]
        if matches:
            if len(matches) != 1 or any(matches[0].get(key) != value for key, value in candidate.items() if key != "started_at"):
                raise BackendUnavailable("proof-launch-registry-conflict")
            if candidate.get("started_at"):
                if matches[0].get("started_at") not in (None, candidate["started_at"]):
                    raise BackendUnavailable("proof-original-container-changed")
                matches[0]["started_at"] = candidate["started_at"]
                private_json(self.registry_path, registry)
            return matches[0]
        registry["sessions"].append(candidate)
        private_json(self.registry_path, registry)
        return candidate

    def _validate_launch_summary(self, summary, entry):
        record = self._session_record(entry)
        spec = record["spec"]
        expected = {
            "session_id": entry["session_id"], "project_root": str(self.project), "project_uuid": self.project_uuid,
            "image": self.plan["docker"]["image"], "runtime": "docker", "network_mode": "none",
            "network_endpoints": [], "mounts": [{key: bind[key] for key in ("source", "target", "mode")}
                                                 for bind in spec["mount_plan"]["binds"]],
            "auth_profile": "default", "plugins_enabled": ["agent-dashboard-test"],
            "entrypoint_wraps": [["/bin/sh", "/mnt/dashboard-fixture/terminal.sh"]],
            "nudge_enabled": False, "port_forwards": [], "auth_mode": "none", "git_unprotected": False,
            "env_var_names": sorted(spec.get("env", {}).get("values", {})),
            "env_files": [], "host_hooks": [], "capabilities": [],
        }
        if summary != expected or record.get("ended_at"):
            raise BackendUnavailable("proof-launch-summary-or-record-changed")

    def recover_launch(self, request_id, *, expected_started_at):
        """Explicitly reconcile one saved unknown launch; never dispatch a command.

        This is a local operator tool, not an HTTP endpoint or automatic retry.
        Persist the exact cleanup handle before checking runtime observations.
        A failed check retains that handle and leaves the operation unknown.
        """
        try:
            if not isinstance(request_id, str) or str(uuid.UUID(request_id)) != request_id:
                raise ValueError("canonical UUID required")
        except (ValueError, AttributeError) as error:
            raise BackendUnavailable("proof-recovery-request-invalid") from error
        if (not isinstance(expected_started_at, str)
                or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z", expected_started_at)):
            raise BackendUnavailable("proof-recovery-start-identity-required")
        with self.locked():
            self.verify_files()
            fingerprint = "sha256:" + hashlib.sha256(json.dumps(self.receipt, sort_keys=True).encode()).hexdigest()
            with OperationStore(self.database) as store:
                try: operation = store.get(request_id)
                except KeyError as error: raise BackendUnavailable("proof-recovery-operation-missing") from error
                if (operation.state != "unknown" or operation.scope_key != "local-disposable-proof"
                        or operation.checkout != str(self.project) or operation.context_id != self.namespace
                        or operation.config_revision != "v1" or operation.intent_fingerprint != fingerprint):
                    raise BackendUnavailable("proof-recovery-operation-mismatch")
                receipt = read_json(self.root / f"launch-{request_id}.json")
                if (not isinstance(receipt, dict) or set(receipt) != {"exit_code", "stdout", "stderr"}
                        or type(receipt["exit_code"]) is not int or receipt["exit_code"] != 0
                        or not isinstance(receipt["stdout"], str) or not isinstance(receipt["stderr"], str)):
                    raise BackendUnavailable("proof-recovery-receipt-invalid")
                summary, result = decode_launch_output(receipt["stdout"])
                registry = self._registry()
                candidate = self._launch_entry(result, request_id)
                candidate["started_at"] = expected_started_at
                entry = self._persist_launch_entry(registry, candidate)
                self._validate_launch_summary(summary, entry)
                matching_records = []
                sessions_dir = Path(self.plan["state_root"]) / "state" / self.project_uuid / "sessions"
                for path in sessions_dir.glob("*/spec.json"):
                    record = read_json(path)
                    handle = record.get("runtime_handle", {}).get("docker", {})
                    if record.get("session_id") == entry["session_id"] or (isinstance(handle, dict) and handle.get("container_id") == entry["container_id"]):
                        matching_records.append(path)
                if matching_records != [Path(entry["record_dir"]) / "spec.json"]:
                    raise BackendUnavailable("proof-recovery-record-ambiguous")
                self.verify_daemon()
                inspected = self.inspect_container(entry)
                started_at = inspected.get("State", {}).get("StartedAt")
                if inspected.get("State", {}).get("Running") is not True or started_at != expected_started_at:
                    raise BackendUnavailable("proof-recovery-runtime-unconfirmed")
                entry["started_at"] = started_at
                private_json(self.registry_path, registry)
                private_json(self.root / f"container-{entry['session_id']}.json", inspected)
                store.record_success(request_id, session_id=entry["session_id"])
                return {"session": self._view(entry, "running")}

    def start_session(self, project_id, request_id):
        if project_id != self.project_id: raise BackendUnavailable("proof-project-unregistered")
        with self.locked():
            self.verify_files(); self.verify_daemon()
            registry = self._registry()
            self._check_unregistered_records(registry)
            # Idempotent request replay returns its original exact session, even
            # while that session is live. It never passes through a new launch.
            with OperationStore(self.database) as previous_store:
                try: completed = previous_store.get(request_id)
                except KeyError: completed = None
                if completed and completed.state == "succeeded":
                    entry = self._record(completed.session_id)
                    container = self.inspect_container(entry, missing_ok=True)
                    state = "running" if container and container["State"].get("Running") else "unknown"
                    return {"session": self._view(entry, state)}
            for entry in registry["sessions"]:
                if not self._session_record(entry).get("ended_at") or self.inspect_container(entry, missing_ok=True) is not None:
                    raise BackendUnavailable("proof-existing-session-unresolved")
            intent = "sha256:" + hashlib.sha256(json.dumps(self.receipt, sort_keys=True).encode()).hexdigest()
            with OperationStore(self.database) as store:
                try:
                    prior = store.get(request_id)
                except KeyError:
                    prior = None
                if prior and prior.state == "succeeded":
                    return {"session": self._view(self._record(prior.session_id), "unknown")}
                self.preflight()
                try:
                    store.prepare(operation_id=request_id, scope_key="local-disposable-proof", checkout=str(self.project),
                                  context_id=self.namespace, config_revision="v1", intent_fingerprint=intent)
                except OperationConflict as error:
                    raise BackendUnavailable("proof-launch-unresolved") from error
                if not store.claim(request_id, expected_fingerprint=intent):
                    raise BackendUnavailable("proof-launch-already-dispatched")
                try:
                    code, stdout, stderr = self._run(self.argv("start", "--runtime", "docker", "--detach", "--yes", "--json", "--no-auto-onboard"), timeout=45)
                    private_json(self.root / f"launch-{request_id}.json", {"exit_code": code, "stdout": stdout.decode(errors="replace"), "stderr": stderr.decode(errors="replace")})
                    if code: raise BackendUnavailable("proof-launch-outcome-unknown")
                    summary, result = decode_launch_output(stdout)
                    sid = result["session_id"]
                    # Persist the exact handle before post-launch inspection so a
                    # failed scope/identity check never loses the cleanup target.
                    entry = self._persist_launch_entry(registry, self._launch_entry(result, request_id))
                    self._validate_launch_summary(summary, entry)
                    inspected = self.inspect_container(entry)
                    if not inspected["State"].get("Running"): raise BackendUnavailable("proof-process-not-running")
                    entry["started_at"] = inspected["State"]["StartedAt"]
                    private_json(self.registry_path, registry)
                    private_json(self.root / f"container-{sid}.json", inspected)
                    store.record_success(request_id, session_id=sid)
                    return {"session": self._view(entry, "running")}
                except BaseException:
                    store.record_unknown(request_id, result_code="runtime-outcome-unconfirmed")
                    raise

    def attach(self, context_namespace, runtime_id, cols, rows):
        if context_namespace != self.namespace: raise BackendUnavailable("proof-context-unregistered")
        self.verify_files()
        entry = self._record(runtime_id)
        if self._session_record(entry).get("ended_at"): raise BackendUnavailable("proof-session-ended")
        fd = os.open(self.root / f"writer-{runtime_id}.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Returning an attachment lets the service announce readiness. Check
            # the original live runtime here, before any PTY accepts input; the
            # child bootstrap also rechecks identity immediately before exec.
            if not isinstance(entry.get("started_at"), str) or not entry["started_at"]:
                raise BackendUnavailable("proof-original-container-unconfirmed")
            self.verify_daemon()
            container = self.inspect_container(entry)
            if container.get("State", {}).get("Running") is not True:
                raise BackendUnavailable("proof-process-not-running")
            client = PtyAttachment(self.argv("attach", runtime_id), cwd=self.project, env=self.environment(), cols=cols, rows=rows, terminal_newlines=True)
            return ProofAttachment(client, fd)
        except BaseException:
            os.close(fd); raise

    def stop_session(self, context_namespace, runtime_id, request_id):
        if context_namespace != self.namespace: raise BackendUnavailable("proof-context-unregistered")
        with self.locked():
            self.verify_files(); self.verify_daemon()
            entry = self._record(runtime_id); record = self._session_record(entry)
            if record.get("ended_at"):
                if self.inspect_container(entry, missing_ok=True) is not None: raise BackendUnavailable("proof-ended-but-container-present")
                return {"session": self._view(entry, "stopped"), "alreadyStopped": True}
            self.inspect_container(entry)
            code, stdout, stderr = self._run(self.argv("stop", runtime_id), timeout=40)
            private_json(self.root / f"stop-{runtime_id}.json", {"exit_code": code, "stdout": stdout.decode(errors="replace"), "stderr": stderr.decode(errors="replace")})
            if code: raise BackendUnavailable("proof-stop-not-confirmed")
            if self.inspect_container(entry, missing_ok=True) is not None or not self._session_record(entry).get("ended_at"):
                raise BackendUnavailable("proof-stop-not-confirmed")
            return {"session": self._view(entry, "stopped")}
