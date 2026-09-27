"""Multi-project local shell prototype, using the reviewed Botainer pipeline.

This deliberately supports only the existing cached image, no network, no agent
credentials and no hooks. The installed working Botainer state is never used.
Project text is editable; each launch validates one composed immutable spec
before passing that exact object to Botainer's adapter.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import threading

from .backend_errors import BackendUnavailable
from .operations import OperationStore, OperationConflict
from .proof_backend import (ProofBackend, bounded_run, digest, private_json, read_json,
                            decode_launch_output, validate_spec)


def validate_shell_config(raw, plan):
    """An additional prototype execution boundary, not a YAML/schema substitute."""
    required = {"version": "config-v1", "agent": "dashboard-test", "agent_permissions": "prompt",
                "runtime": "docker", "image": plan["docker"]["image"],
                "network": {"mode": "none"}, "plugins_enabled": ["agent-dashboard-test"],
                "inject_credentials": [], "job_profiles": {},
                "mounts": {"extra": [{"source": plan["fixture_readonly_source"],
                    "target": "/mnt/dashboard-fixture", "mode": "ro",
                    "reason": "Local prototype shell"}]}}
    if not isinstance(raw, dict) or set(raw) - set(required) - {"resources", "env"}:
        raise ValueError("Local shell supports only the template fields, resources and APP_ environment values.")
    if any(raw.get(key) != value for key, value in required.items()):
        raise ValueError("Local shell requires the cached image, network off, its fixed plugin/mount and no credentials.")
    resources = raw.get("resources", {})
    if not isinstance(resources, dict) or set(resources) - {"cpu", "memory_mb"}:
        raise ValueError("Local shell resources are cpu and memory_mb only.")
    for name, maximum in (("cpu", 16), ("memory_mb", 8192)):
        if name in resources and (type(resources[name]) is not int or not 1 <= resources[name] <= maximum):
            raise ValueError(f"{name} must be an integer between 1 and {maximum}.")
    environment = raw.get("env", {})
    import re
    if not isinstance(environment, dict) or len(environment) > 32:
        raise ValueError("Local shell permits at most 32 APP_ environment values.")
    for key, value in environment.items():
        if (not isinstance(key, str) or not re.fullmatch(r"APP_[A-Z0-9_]{1,48}", key)
                or any(part in key for part in ("TOKEN", "SECRET", "PASSWORD", "KEY", "CREDENTIAL"))
                or not isinstance(value, str) or len(value) > 1024 or "\0" in value):
            raise ValueError("Use non-secret APP_ environment values only.")


def shell_template(plan):
    # JSON strings are valid YAML scalars, including private paths with spaces.
    return ("# Botainer local shell prototype. CPU/memory and APP_ values are editable.\n"
            "version: config-v1\nagent: dashboard-test\nagent_permissions: prompt\nruntime: docker\n"
            f"image: {plan['docker']['image']}\nnetwork:\n  mode: none\n"
            "plugins_enabled: [agent-dashboard-test]\ninject_credentials: []\njob_profiles: {}\n"
            "resources:\n  cpu: 1\n  memory_mb: 512\nenv: {}\nmounts:\n  extra:\n"
            f"    - source: {json.dumps(plan['fixture_readonly_source'])}\n"
            "      target: /mnt/dashboard-fixture\n      mode: ro\n      reason: Local prototype shell\n")


class ShellProjectBackend(ProofBackend):
    """Reuse verified handle/PTY/stop invariants with a separate project plan."""

    def __init__(self, owner, project):
        self.owner = owner
        self.root = owner.root / "runtimes" / project["uuid"]
        self.plan_path = self.root / "plan.json"
        self.receipt_path = self.root / "prepared.json"
        self.plan = read_json(self.plan_path)
        self.receipt = read_json(self.receipt_path)
        self.project = Path(project["path"])
        self.project_uuid = project["uuid"]
        self.project_id = project["id"]
        self.namespace = "shell:" + hashlib.sha256((str(self.root) + self.receipt["daemon_id"]).encode()).hexdigest()[:32]
        self.registry_path = self.root / "sessions.json"
        self.database = self.root / "operations.sqlite"
        self._thread_lock = threading.RLock()
        self.verify_files()

    def verify_files(self):
        self.owner.baseline.verify_files()
        super().verify_files()
        if self.project.resolve() != self.project or self.project.is_symlink():
            raise BackendUnavailable("workspace-project-moved")
        folder = os.open(self.project, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(folder)
            if [info.st_dev, info.st_ino] != self.plan["project_identity"]:
                raise BackendUnavailable("workspace-project-identity-changed")
            meta = os.open(".botainer", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=folder)
            try:
                descriptor = os.open("project-id", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=meta)
                with os.fdopen(descriptor, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise BackendUnavailable("workspace-project-identity-changed")
                    if stream.read(129).decode("ascii").strip() != self.project_uuid:
                        raise BackendUnavailable("workspace-project-identity-changed")
            finally: os.close(meta)
        finally: os.close(folder)

    def argv(self, *args):
        return [self.plan["interpreter"], "-I", "-B", str(self.owner.helper), str(self.plan_path), "cli", *args]

    def _view(self, entry, state):
        value = super()._view(entry, state)
        value.update(label="Shell", agent="Alpine shell")
        return value

    def inspect_container(self, entry, *, missing_ok=False):
        value = super().inspect_container(entry, missing_ok=missing_ok)
        if value is None: return None
        host = value.get("HostConfig", {})
        if (host.get("Privileged") is not False or host.get("PidMode") not in (None, "")
                or host.get("IpcMode") not in (None, "private") or host.get("CapAdd")
                or host.get("Devices") or host.get("DeviceRequests") or host.get("VolumesFrom")):
            raise BackendUnavailable("shell-container-extra-host-access")
        return value

    def snapshot(self):
        # --rm removes a shell that exits naturally. Confirm absence against the
        # pinned daemon, then finish only this hook-free session's bookkeeping.
        with self.locked():
            self.verify_files(); self.verify_daemon()
            registry = self._registry()
            self._check_unregistered_records(registry)
            sessions = []
            for entry in registry["sessions"]:
                try:
                    record = self._session_record(entry)
                    if record.get("ended_at"):
                        state = "stopped" if self.inspect_container(entry, missing_ok=True) is None else "unknown"
                    else:
                        container = self.inspect_container(entry, missing_ok=True)
                        if entry.get("started_at") and container is None:
                            code, _out, _err = self._run(self.argv("reconcile-exit", entry["session_id"]))
                            if code: raise BackendUnavailable("shell-exit-bookkeeping-unconfirmed")
                            state = "stopped"
                        else:
                            state = "running" if container and container["State"].get("Running") else "unknown"
                except (BackendUnavailable, OSError, ValueError):
                    state = "unknown"
                sessions.append(self._view(entry, state))
            with OperationStore(self.database) as store:
                unresolved = bool(store.unresolved())
            return {"sessions": sessions, "capabilities": {"startSession": not unresolved and all(s["state"] != "unknown" for s in sessions)}}

    def preflight(self):
        self.verify_files(); self.verify_daemon()
        code, stdout, stderr = self._run([self.plan["interpreter"], "-I", "-B", str(self.owner.helper), str(self.plan_path), "preflight"])
        if code:
            private_json(self.root / "preflight-error.json", {"stderr": stderr.decode(errors="replace")})
            raise BackendUnavailable("shell-config-composition-refused")
        result = json.loads(stdout)
        validate_spec(result["spec"], self.plan, self.project_uuid)
        return result

    def start_session(self, project_id, request_id):
        if project_id != self.project_id:
            raise BackendUnavailable("workspace-project-unregistered")
        with self.locked():
            self.verify_files(); self.verify_daemon()
            registry = self._registry()
            self._check_unregistered_records(registry)
            with OperationStore(self.database) as store:
                try: prior = store.get(request_id)
                except KeyError: prior = None
                if prior is not None:
                    if prior.state != "succeeded":
                        raise BackendUnavailable("shell-launch-unresolved")
                    entry = self._record(prior.session_id)
                    container = self.inspect_container(entry, missing_ok=True)
                    state = "running" if container and container["State"].get("Running") else "stopped" if self._session_record(entry).get("ended_at") else "unknown"
                    return {"session": self._view(entry, state)}
                if store.unresolved():
                    raise BackendUnavailable("shell-launch-unresolved")
                # Unknown prior work is fenced; known running sessions can coexist.
                for entry in registry["sessions"]:
                    record = self._session_record(entry)
                    container = self.inspect_container(entry, missing_ok=True)
                    if record.get("ended_at") and container is not None:
                        raise BackendUnavailable("shell-ended-record-container-present")
                    if not record.get("ended_at") and not (container and container["State"].get("Running")):
                        raise BackendUnavailable("shell-existing-session-unknown")
                if sum(not self._session_record(e).get("ended_at") for e in registry["sessions"]) >= 8:
                    raise BackendUnavailable("shell-eight-session-limit")
                revision = digest(self.project / ".botainer/config.yaml")
                intent = "sha256:" + hashlib.sha256((digest(self.plan_path) + revision).encode()).hexdigest()
                try:
                    store.prepare(operation_id=request_id, scope_key="local-shell", checkout=str(self.project),
                                  context_id=self.namespace, config_revision=revision, intent_fingerprint=intent)
                except OperationConflict as exc:
                    raise BackendUnavailable("shell-launch-unresolved") from exc
                if not store.claim(request_id, expected_fingerprint=intent):
                    raise BackendUnavailable("shell-launch-already-dispatched")
                try:
                    code, stdout, stderr = self._run(self.argv("start", revision), timeout=45)
                    private_json(self.root / f"launch-{request_id}.json", {"exit_code": code, "stdout": stdout.decode(errors="replace"), "stderr": stderr.decode(errors="replace")})
                    if code:
                        # A child with a proven pre-launch refusal is safe to fail.
                        if code == 3 and not stdout:
                            store.record_failure(request_id, result_code="config-refused-before-launch")
                            raise BackendUnavailable("shell-config-refused-before-launch")
                        raise BackendUnavailable("shell-launch-outcome-unconfirmed")
                    summary, result = decode_launch_output(stdout)
                    entry = self._persist_launch_entry(registry, self._launch_entry(result, request_id))
                    self._validate_launch_summary(summary, entry)
                    container = self.inspect_container(entry)
                    if not container["State"].get("Running"):
                        raise BackendUnavailable("shell-exited-during-launch")
                    entry["started_at"] = container["State"]["StartedAt"]
                    private_json(self.registry_path, registry)
                    store.record_success(request_id, session_id=entry["session_id"])
                    return {"session": self._view(entry, "running")}
                except BaseException:
                    if store.get(request_id).state == "dispatching":
                        store.record_unknown(request_id, result_code="runtime-outcome-unconfirmed")
                    raise


class SandboxRuntime:
    """Explicitly prepared workspace; constructor performs no runtime dispatch."""

    def __init__(self, root: Path, baseline: ProofBackend):
        self.root = root.resolve()
        self.baseline = baseline
        self.repo = Path(__file__).resolve().parents[2]
        self.helper = self.repo / "tools/shell_botainer_helper.py"
        self.plan = {**baseline.plan, "fixture_readonly_source": str(self.repo / "tests/fixtures/botainer-shell")}
        self.backends = {}

    def prepare_project(self, project):
        """Private state only; never install or modify an existing Botainer."""
        self.baseline.verify_files()
        target = self.root / "runtimes" / project["uuid"]
        if (target / "prepared.json").exists():
            return self.backend(project)
        if target.exists():
            raise BackendUnavailable("shell-partial-preparation-needs-review")
        target.mkdir(parents=True, mode=0o700)
        for name in ("state", "docker-config"):
            (target / name).mkdir(mode=0o700)
        state = target / "state"
        source_state = Path(self.baseline.plan["state_root"])
        shutil.copytree(source_state / "plugins", state / "plugins", symlinks=False)
        for path in source_state.glob("*.lock"):
            shutil.copyfile(path, state / path.name)
        shutil.copyfile(source_state / "policy.yaml", state / "policy.yaml")
        plan = {**self.plan, "project": project["path"], "state_root": str(state),
                "workspace_root": str(self.root), "baseline_plan": str(self.baseline.plan_path),
                "project_uuid": project["uuid"], "project_id": project["id"],
                "project_identity": [project["device"], project["inode"]]}
        private_json(target / "plan.json", plan)
        protected = [target / "plan.json", self.helper, Path(__file__),
                     self.repo / "src/botainer_dashboard/proof_backend.py",
                     self.repo / "tests/fixtures/botainer-shell/terminal.sh"]
        protected += [p for p in state.rglob("*") if p.is_file()]
        receipt = {"plan_sha256": digest(target / "plan.json"), "daemon_id": self.baseline.receipt["daemon_id"],
                   "executables": self.baseline.receipt["executables"],
                   "protected_files": [{"path": str(p), "sha256": digest(p)} for p in protected]}
        private_json(target / "prepared.json", receipt)
        return self.backend(project)

    def backend(self, project):
        key = project["id"]
        if key not in self.backends:
            self.backends[key] = ShellProjectBackend(self, project)
        return self.backends[key]
