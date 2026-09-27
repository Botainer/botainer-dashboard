"""Scoped native-CLI shell launch, with a viewer-independent consent terminal.

This bridge is for the separately prepared, hook-free cached-image workspace.
Its private callback receipt is not a Botainer API or a general launch contract.
The original prepared shell workspace and all of its receipts remain unchanged.
"""
from __future__ import annotations

import fcntl
import hashlib
import os
from pathlib import Path
import shutil
import stat
import threading
import time
import uuid

from .backend_errors import BackendUnavailable
from .operations import OperationStore
from .proof_backend import CID, SID, digest, private_json, read_json
from .pty_bridge import PtyAttachment
from .sandbox_runtime import SandboxRuntime, ShellProjectBackend

# Source-only prepared runtime compatibility; installed controllers import the
# shared primitives directly and never load the legacy shell backend classes.
from .launch_console import (LaunchConsole, LaunchViewer, canonical_request,
                             private_lock, read_transcript, TRANSCRIPT_LIMIT,
                             LAUNCH_TIMEOUT, CONSOLE_LIMIT)

HISTORY_LIMIT = 64
FINISHED = {"completed", "declined", "failed", "unknown"}


class NativeShellProjectBackend(ShellProjectBackend):
    def __init__(self, owner, project):
        super().__init__(owner, project)
        self.launch_namespace = "launch:" + self.namespace.split(":", 1)[1]
        self.launches_path = self.root / "native-launches.json"
        self.consoles = {}

    def _launches(self):
        return read_json(self.launches_path) if self.launches_path.exists() else {"launches": []}

    def validate_launch(self, runtime_id):
        canonical_request(runtime_id)
        if not any(entry["request_id"] == runtime_id for entry in self._launches()["launches"]):
            raise BackendUnavailable("native-launch-unregistered")

    def _launch_view(self, entry):
        value = {"contextNamespace": self.launch_namespace, "runtimeId": entry["request_id"],
                 "projectId": self.project_id, "label": "Botainer launch", "agent": "Native CLI",
                 "kind": "launch", "launchState": entry["state"],
                 "state": "unknown" if entry["state"] == "unknown" else "stopped" if entry["state"] in FINISHED else "running",
                 "createdAt": entry["created_at"],
                 "capabilities": {"attachTerminal": entry["request_id"] in self.consoles
                                  or self._transcript(entry["request_id"]).is_file(), "stopSession": False}}
        if entry.get("result_target"):
            value["resultTarget"] = entry["result_target"]
        if entry.get("reason"):
            value["launchReason"] = entry["reason"]
        return value

    def _transcript(self, request):
        return self.root / f"native-transcript-{canonical_request(request)}.bin"

    def _receipt(self, request):
        return self.root / f"native-result-{canonical_request(request)}.json"

    def _owner_lock(self):
        return private_lock(self.root / "native-owner.lock")

    def _trim_consoles(self):
        for request, console in list(self.consoles.items()):
            if len(self.consoles) < CONSOLE_LIMIT:
                break
            if console.done and console.viewer is None and console.cleanup_confirmed:
                del self.consoles[request]

    def _reconcile_launches(self, launches):
        """Called under dispatch lock. No receipt can cause a second dispatch."""
        changed = False
        for entry in launches["launches"]:
            if entry["state"] in FINISHED:
                continue
            console = self.consoles.get(entry["request_id"])
            if console is not None and not console.done:
                continue
            if console is not None and not console.cleanup_confirmed:
                with OperationStore(self.database) as store:
                    store.record_unknown(entry["request_id"], result_code="native-cli-cleanup-unconfirmed")
                entry.update(state="unknown", reason="Launch process cleanup is unconfirmed; no retry is permitted")
                changed = True
                continue
            try:
                owner_fd = self._owner_lock()
            except BlockingIOError:
                continue
            try:
                self._finish_launch(entry, console)
                changed = True
            finally:
                os.close(owner_fd)
        if changed:
            private_json(self.launches_path, launches)

    def _finish_launch(self, entry, console):
        request = entry["request_id"]
        with OperationStore(self.database) as store:
            try:
                result = read_json(self._receipt(request))
                if (result.get("request_id") != request or result.get("config_revision") != entry["config_revision"]
                        or result.get("plan_sha256") != digest(self.plan_path)):
                    raise BackendUnavailable("native-launch-receipt-target-invalid")
                if result.get("phase") == "not-dispatched":
                    store.record_failure(request, result_code="native-cli-no-runtime-dispatch")
                    entry.update(state="declined" if result.get("exit_code") == 0 else "failed",
                                 reason="CLI exited before runtime dispatch")
                    return
                if result.get("phase") != "completed" or result.get("exit_code") != 0:
                    raise BackendUnavailable("native-cli-result-unconfirmed")
                launched = result["result"]
                if (not SID.fullmatch(launched.get("session_id", ""))
                        or not CID.fullmatch(launched.get("container_id", ""))
                        or launched.get("runtime") != "docker"):
                    raise BackendUnavailable("native-cli-result-invalid")
                registry = self._registry()
                candidate = self._launch_entry(launched, request)
                registered = self._persist_launch_entry(registry, candidate)
                self._session_record(registered)
                self.verify_files(); self.verify_daemon()
                container = self.inspect_container(registered)
                if not container.get("State", {}).get("Running"):
                    raise BackendUnavailable("native-cli-owner-unconfirmed")
                registered["started_at"] = container["State"]["StartedAt"]
                private_json(self.registry_path, registry)
                store.record_success(request, session_id=registered["session_id"])
                entry.update(state="completed", result_target={"contextNamespace": self.namespace,
                                                               "runtimeId": registered["session_id"]})
            except Exception:
                if store.get(request).state in {"dispatching", "unknown"}:
                    store.record_unknown(request, result_code="native-cli-outcome-unconfirmed")
                entry.update(state="unknown", reason="Launch outcome needs reconciliation; it will not be retried")

    def snapshot(self):
        with self.locked():
            self.verify_files(); self.verify_daemon()
            launches = self._launches()
            self._reconcile_launches(launches)
            registry = self._registry()
            if not any(e["state"] not in FINISHED for e in launches["launches"]):
                self._check_unregistered_records(registry)
            sessions = []
            for entry in registry["sessions"]:
                try:
                    record = self._session_record(entry)
                    container = self.inspect_container(entry, missing_ok=True)
                    if not record.get("ended_at") and entry.get("started_at") and container is None:
                        code, _out, _err = self._run(self.argv("reconcile-exit", entry["session_id"]))
                        if code:
                            raise BackendUnavailable("shell-exit-bookkeeping-unconfirmed")
                        state = "stopped"
                    else:
                        state = ("stopped" if record.get("ended_at") and container is None else
                                 "running" if not record.get("ended_at") and container and container["State"].get("Running") else "unknown")
                except (BackendUnavailable, OSError, ValueError):
                    state = "unknown"
                sessions.append(self._view(entry, state))
            with OperationStore(self.database) as store:
                unresolved = bool(store.unresolved())
            can_start = not unresolved and all(s["state"] == "stopped" for s in sessions)
            return {"sessions": sessions + [self._launch_view(e) for e in launches["launches"]],
                    "capabilities": {"startSession": can_start, "configWrite": can_start}}

    def start_session(self, project_id, request_id):
        canonical_request(request_id)
        if project_id != self.project_id:
            raise BackendUnavailable("workspace-project-unregistered")
        with self.locked():
            self.verify_files(); self.verify_daemon()
            launches = self._launches()
            self._reconcile_launches(launches)
            for previous in launches["launches"]:
                if previous["request_id"] == request_id:
                    return {"session": self._launch_view(previous)}
            registry = self._registry()
            self._check_unregistered_records(registry)
            for entry in registry["sessions"]:
                if not self._session_record(entry).get("ended_at") or self.inspect_container(entry, missing_ok=True) is not None:
                    raise BackendUnavailable("native-cli-project-already-active")
            with OperationStore(self.database) as store:
                try:
                    store.get(request_id)
                except KeyError:
                    pass
                else:
                    raise BackendUnavailable("native-cli-history-expired-no-retry")
                if store.unresolved():
                    raise BackendUnavailable("native-cli-launch-unresolved")
                self._trim_consoles()
                if len(self.consoles) >= CONSOLE_LIMIT:
                    raise BackendUnavailable("native-cli-close-finished-views")
                owner_fd = self._owner_lock()
                try:
                    revision = digest(self.project / ".botainer/config.yaml")
                    intent = "sha256:" + hashlib.sha256((digest(self.plan_path) + revision).encode()).hexdigest()
                    store.prepare(operation_id=request_id, scope_key="native-local-shell", checkout=str(self.project),
                                  context_id=self.namespace, config_revision=revision, intent_fingerprint=intent)
                    if not store.claim(request_id, expected_fingerprint=intent):
                        raise BackendUnavailable("native-cli-launch-already-dispatched")
                    from datetime import datetime, timezone
                    entry = {"request_id": request_id, "config_revision": revision, "state": "waiting",
                             "created_at": datetime.now(timezone.utc).isoformat()}
                    launches["launches"].append(entry)
                    # Operations retain request IDs permanently. Expired console
                    # history can never turn an old request into a new launch.
                    launches["launches"] = launches["launches"][-HISTORY_LIMIT:]
                    private_json(self.launches_path, launches)
                    argv = [self.plan["interpreter"], "-I", "-B", str(self.owner.native_helper),
                            str(self.plan_path), request_id, revision]
                    console = LaunchConsole(argv, cwd=self.project, env=self.environment(),
                                            transcript=self._transcript(request_id), owner_fd=owner_fd)
                    self.consoles[request_id] = console
                    owner_fd = None
                except Exception:
                    # Popen failure cannot dispatch; any failure after creating
                    # a CLI process is conservatively fenced by its journal.
                    try:
                        store.record_unknown(request_id, result_code="native-cli-start-unconfirmed")
                    except (KeyError, ValueError):
                        pass
                    raise
                finally:
                    if owner_fd is not None:
                        os.close(owner_fd)
                return {"session": self._launch_view(entry)}

    def attach(self, context_namespace, runtime_id, cols, rows):
        if context_namespace != self.launch_namespace:
            return super().attach(context_namespace, runtime_id, cols, rows)
        canonical_request(runtime_id)
        with self.locked():
            self.verify_files()
            if not any(e["request_id"] == runtime_id for e in self._launches()["launches"]):
                raise BackendUnavailable("native-launch-unregistered")
            fd = private_lock(self.root / f"native-writer-{runtime_id}.lock")
            try:
                console = self.consoles.get(runtime_id)
                if console is None:
                    self._trim_consoles()
                    if len(self.consoles) >= CONSOLE_LIMIT:
                        raise BackendUnavailable("native-cli-close-finished-views")
                    console = LaunchConsole.__new__(LaunchConsole)
                    console.condition = threading.Condition()
                    console.buffer = bytearray(read_transcript(self._transcript(runtime_id)))
                    console.base = 0
                    console.done = True
                    console.cleanup_confirmed = True
                    console.viewer = None
                    self.consoles[runtime_id] = console
                viewer = console.attach(fd)
                viewer.resize(cols, rows)
                return viewer
            except Exception:
                os.close(fd)
                raise


class NativeSandboxRuntime(SandboxRuntime):
    def __init__(self, root, baseline):
        super().__init__(root, baseline)
        self.native_helper = self.repo / "tools/native_cli_botainer_helper.py"

    def prepare_project(self, project):
        self.baseline.verify_files()
        target = self.root / "runtimes" / project["uuid"]
        if (target / "prepared.json").exists():
            return self.backend(project)
        if target.exists():
            raise BackendUnavailable("native-shell-partial-preparation-needs-review")
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
                "project_identity": [project["device"], project["inode"]], "native_cli": True}
        private_json(target / "plan.json", plan)
        protected = [target / "plan.json", self.helper, self.native_helper, Path(__file__),
                     self.repo / "src/botainer_dashboard/launch_console.py",
                     self.repo / "src/botainer_dashboard/sandbox_runtime.py",
                     self.repo / "src/botainer_dashboard/proof_backend.py",
                     self.repo / "src/botainer_dashboard/pty_bridge.py",
                     self.repo / "src/botainer_dashboard/operations.py",
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
            self.backends[key] = NativeShellProjectBackend(self, project)
        return self.backends[key]
