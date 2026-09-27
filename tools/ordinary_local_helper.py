#!/usr/bin/env python3
"""Fixed ordinary-local operations using the selected Botainer installation.

Native start/init/stop retain Botainer's CLI, hooks, warnings and confirmation.
The temporary Docker compatibility bridge only preserves detached TTY ownership,
forbids image pulls and prevents attachment-client signals killing its container.
It is not a second consent policy and is not an upstream public API.
"""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

REPO = Path(__file__).resolve().parents[1]
if REPO.name == "_resources" and REPO.parent.name == "botainer_dashboard":
    # This helper uses the selected Botainer interpreter. Load only our fixed
    # package; adding the dashboard's site-packages would shadow Botainer's
    # dependencies and could import native wheels built for another Python.
    import importlib.util
    _package = REPO.parent
    _spec = importlib.util.spec_from_file_location("botainer_dashboard", _package / "__init__.py",
                                                   submodule_search_locations=[str(_package)])
    _module = importlib.util.module_from_spec(_spec)
    sys.modules["botainer_dashboard"] = _module
    _spec.loader.exec_module(_module)
else:
    sys.path.insert(0, str(REPO / "src"))

from botainer_dashboard.ordinary_local import (OrdinaryLocalProfile, SID, canonical_path, sha,
    orphan_launch_evidence, request_id)
from botainer_dashboard.inventory import bounded_run, InventoryError
from botainer_dashboard.pairing import read_private_json, write_private_json
from botainer_dashboard.backend_errors import BackendUnavailable
from botainer_dashboard.project_boundaries import protected_local_paths, require_separate_project

CONTAINER_FORMAT = ('{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}},'
    '"created":{{json .Created}},"state":{{json .State.Status}},"started_at":{{json .State.StartedAt}},'
    '"tty":{{json .Config.Tty}},"stdin":{{json .Config.OpenStdin}},"mounts":{{json .Mounts}}}')


def docker(profile, *args):
    d = profile.data["docker"]
    return bounded_run((d["executable"], "--host", d["host"], *args),
        cwd=Path(profile.data["home"]), env=profile.environment, timeout=12)


def verify_daemon(profile):
    value = json.loads(docker(profile, "info", "--format", "{{json .ID}}"))
    if value != profile.data["docker"]["daemon_id"]:
        raise ValueError("Selected Docker daemon identity changed")


def docker_inventory(profile):
    verify_daemon(profile)
    # A renamed live container must remain visible to the project ownership
    # fence. Only native project/session rows are returned to the dashboard.
    ids = docker(profile, "ps", "-a", "--no-trunc", "--format", "{{.ID}}")
    values = ids.decode("ascii").splitlines()
    if len(values) > 2000 or any(not re.fullmatch(r"[0-9a-f]{64}", v) for v in values):
        raise ValueError("Docker inventory is invalid or too large")
    containers = {}
    # Batches bound argv length and avoid one subprocess per historical record.
    for offset in range(0, len(values), 100):
        output = docker(profile, "container", "inspect", "--format", CONTAINER_FORMAT, *values[offset:offset + 100])
        for line in output.splitlines():
            item = json.loads(line)
            if item["id"] not in values or item["name"] in containers:
                raise ValueError("Ambiguous Docker identity")
            containers[item["name"]] = item
    return containers


def container_binding(record, container):
    if (container.get("name") != "/botainer-" + record.session_id[:12]
            or not re.fullmatch(r"[0-9a-f]{64}", container.get("id", ""))
            or record.docker.container_id not in {container["id"], container["name"].lstrip("/")}
            or not any(m.get("Type") == "bind" and m.get("Source") == record.project_root
                       for m in container.get("mounts", []))):
        raise ValueError("Container does not match the recorded project/session")
    return {key: container[key] for key in ("id", "name", "image", "created", "started_at")}


def inventory(profile):
    from botainer.state import dir as state_dir, session_record
    containers = docker_inventory(profile)
    projects, sessions, used = [], [], set()
    for entry in state_dir.list_projects():
        project = {"uuid": entry.uuid, "display_name": entry.display_name or Path(entry.last_path).name,
            "last_path": entry.last_path, "last_session_at": entry.last_session_at, "runtime_unverified": False}
        projects.append(project)
        sessions_root = Path(profile.data["state_root"]) / "state" / entry.uuid / "sessions"
        records = []
        if sessions_root.exists():
            for candidate in session_record.candidate_session_dirs(sessions_root):
                try: records.append(session_record.read(candidate))
                except FileNotFoundError: continue  # native aborted-before-compose directory
                except (ValueError, KeyError, OSError): project["runtime_unverified"] = True
        if len(sessions) + len(records) > 10000: raise ValueError("Session inventory limit")
        known_live_owners = {}
        for record in records:
            if (record.project_uuid != entry.uuid or not SID.fullmatch(record.session_id)
                    or record.runtime != "docker" or not record.docker or record.ended_at):
                continue
            candidate = containers.get("/botainer-" + record.session_id[:12])
            if not candidate or candidate["state"] in {"exited", "dead"}: continue
            try: binding = container_binding(record, candidate)
            except (ValueError, KeyError): continue
            known_live_owners.setdefault(binding["id"], set()).add(record.session_id)
        for record in records:
            if record.project_uuid != entry.uuid or not SID.fullmatch(record.session_id):
                project["runtime_unverified"] = True; continue
            item = {"project_uuid": entry.uuid, "session_id": record.session_id, "runtime": record.runtime,
                "started_at": record.started_at, "ended_at": record.ended_at, "state": "unknown", "interactive": False}
            if record.runtime == "docker" and record.docker and record.docker.container_id:
                container = containers.get("/botainer-" + record.session_id[:12])
                if container is None:
                    # Native records may retain only the original container
                    # name. A live owner of the same workspace under another
                    # name is uncertainty, not evidence this session stopped.
                    related = [c for c in containers.values() if
                        c["id"] == record.docker.container_id or
                        (not known_live_owners.get(c["id"], set()).difference({record.session_id}) and any(
                            m.get("Type") == "bind" and m.get("Source") == record.project_root
                            for m in c.get("mounts", [])))]
                    item["state"] = ("unknown" if any(c["state"] not in {"exited", "dead"}
                        for c in related) else "stopped")
                else:
                    try:
                        binding = container_binding(record, container)
                        if binding["id"] in used: raise ValueError("Duplicate runtime owner")
                        used.add(binding["id"])
                        item.update(binding=binding, state="running" if container["state"] == "running" and not record.ended_at else
                                    "stopped" if container["state"] in {"exited", "dead"} else "unknown",
                                    interactive=container["tty"] is True and container["stdin"] is True)
                    except (ValueError, KeyError): pass
            elif record.ended_at:
                item["state"] = "stopped"
            elif (not record.started_at and not (record.docker and record.docker.container_id)
                    and not (record.apptainer and (record.apptainer.slurm_jobid or record.apptainer.instance_name))):
                # Compose-only records are not executions or pending launches.
                continue
            sessions.append(item)
    # An orphaned owner is not evidence that a project is idle. Fence any known
    # project whose folder is bound into an unmatched live/unverified container.
    for container in containers.values():
        if container["id"] in used or container["state"] in {"exited", "dead"}: continue
        mounts = {m.get("Source") for m in container.get("mounts", []) if m.get("Type") == "bind"}
        for project in projects:
            if project["last_path"] in mounts: project["runtime_unverified"] = True
    return {"projects": projects, "sessions": sessions}


def project_check(data, profile=None):
    path = canonical_path(data["project_path"])
    if profile is not None:
        require_separate_project(path, project_protected_paths(data, profile))
    if profile is not None and not any(Path(root["path"]) in path.parents for root in profile.data["project_roots"]):
        # Existing native projects need no separate folder allowlist. Confirm
        # the exact association in the selected installation's state registry;
        # a copied .botainer/project-id alone never authorizes another folder.
        uid = data.get("project_uuid")
        if not isinstance(uid, str) or str(uuid.UUID(uid)) != uid:
            raise ValueError("Project is not registered with the selected Botainer installation")
        from botainer.state import dir as state_dir
        if state_dir.ensure_user_state_dir(create_if_missing=False).root != Path(profile.data["state_root"]):
            raise ValueError("Selected Botainer state root changed")
        entries = []
        for entry in state_dir.list_projects():
            try: candidate = str(uuid.UUID(entry.uuid))
            except (ValueError, TypeError, AttributeError): continue
            if candidate == uid:
                entries.append(entry)
        if len(entries) != 1 or entries[0].last_path != str(path):
            raise ValueError("Project is not registered at this path with the selected Botainer installation")
    if data.get("project_identity") and [path.stat().st_dev, path.stat().st_ino] != data["project_identity"]:
        raise ValueError("Project directory identity changed")
    if data.get("project_uuid"):
        meta = path / ".botainer"
        if meta.resolve() != meta: raise ValueError("Project metadata must not be a symlink")
        ident = meta / "project-id"
        if ident.resolve() != ident or ident.stat().st_size > 128 or ident.read_text().strip() != data["project_uuid"]:
            raise ValueError("Project UUID changed")
    if data.get("config_revision") and sha(path / ".botainer/config.yaml") != data["config_revision"]:
        raise ValueError("Project configuration changed; review the current text before starting")
    return path


def project_protected_paths(data, profile):
    # Only the service's owner-private query/intent carries this list. Fixed
    # defaults always apply, including when reading a legacy launch receipt.
    extra = data.get("protected_paths", [])
    if not isinstance(extra, list) or len(extra) > 256 or any(not isinstance(p, str) for p in extra):
        raise BackendUnavailable("ordinary-protected-paths-invalid")
    return protected_local_paths(profile, REPO, data_directory=REPO / ".local", extra=extra)


def check_user_mount_boundaries(spec, data, profile):
    # Botainer's reviewed core/plugin binds include intentional state files.
    # User extra mounts must not reopen the host control directories excluded
    # from ordinary project mounts, even if native policy otherwise allows them.
    protected = project_protected_paths(data, profile)
    for bind in spec.mount_plan.binds:
        if bind.provenance == "user":
            require_separate_project(Path(bind.source), protected)


def exact_record(profile, data):
    from botainer.state import session_record
    if not SID.fullmatch(data["session_id"]): raise ValueError("Full session ID required")
    uid = str(uuid.UUID(data["project_uuid"]))
    record = session_record.read(Path(profile.data["state_root"]) / "state" / uid / "sessions" / data["session_id"])
    if (record.session_id != data["session_id"] or record.project_uuid != uid
            or record.project_root != data["project_path"] or record.runtime != "docker"
            or record.docker is None or record.ended_at):
        raise ValueError("The exact original runtime record is unavailable")
    return record


def verify_target(profile, data):
    project_check(data, profile)
    record = exact_record(profile, data)
    containers = docker_inventory(profile)
    container = containers.get("/botainer-" + record.session_id[:12])
    if not container or container["state"] != "running" or container_binding(record, container) != data["binding"]:
        raise ValueError("The original container is no longer confirmed running")
    return record


def exact_container_stopped(profile, binding):
    """Observe the full ID across all names; rename is never proof of absence."""
    verify_daemon(profile)
    ids = docker(profile, "ps", "-a", "--no-trunc", "--format", "{{.ID}}").decode("ascii").splitlines()
    if len(ids) > 10000 or any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in ids):
        raise ValueError("Exact container observation invalid")
    if binding["id"] not in ids: return True
    container = json.loads(docker(profile, "container", "inspect", "--format", CONTAINER_FORMAT, binding["id"]))
    if any(container.get(k) != v for k, v in binding.items()):
        raise ValueError("Original container identity changed")
    return container.get("state") in {"exited", "dead"}


class StopOutput(io.StringIO):
    """Bound native diagnostics while they are written, before JSON transport."""
    LIMIT = 4096

    def __init__(self):
        super().__init__()
        self.truncated = False

    def write(self, text):
        remaining = max(0, self.LIMIT - self.tell())
        if len(text) > remaining:
            self.truncated = True
        super().write(text[:remaining])
        return len(text)

    def diagnostic(self):
        # Native/runtime output remains untrusted text, never HTML or terminal
        # input. Drop escape strings, including a sequence cut by the bound.
        text = self.getvalue()
        text = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\|$)", "", text)
        text = re.sub(r"\x1b[PX^_][^\x1b]*(?:\x1b\\|$)", "", text)
        text = re.sub(r"\x1b\[[0-?]*[ -/]*(?:[@-~]|$)", "", text)
        text = re.sub(r"\x1b.", "", text)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = "".join(c for c in text if c in "\n\t" or c.isprintable()).strip()
        if self.truncated:
            marker = "\n[Further native stop output omitted.]"
            text = text[:self.LIMIT - len(marker)] + marker
        return text


def stop_orphan_container(profile, data, root, *, owner=None):
    """Exact container-only recovery. Never call native hooks or stored PIDs."""
    request = request_id(data["request_id"])
    result_path = root / ("orphan-" + request + ".result.json")
    if result_path.exists(): raise ValueError("Recovery was already dispatched; reconcile its receipt")
    recoveries = read_private_json(root / "orphan-recoveries.json", max_bytes=2 * 1024 * 1024)
    claims = [item for item in recoveries["recoveries"] if item.get("request_id") == request]
    if (len(claims) != 1 or claims[0].get("state") != "dispatching"
            or claims[0].get("session_id") != data["session_id"] or claims[0].get("binding") != data["binding"]):
        raise ValueError("Recovery claim changed")
    operations = read_private_json(root / "operations.json", max_bytes=2 * 1024 * 1024)
    matches = [entry for entry in operations["operations"] if entry.get("request_id") == data["launch_request_id"]]
    if len(matches) != 1 or matches[0].get("orphan_recovery_request") != request:
        raise ValueError("Original launch recovery changed")
    entry = matches[0]
    if owner is None:
        from botainer_dashboard.ordinary_owner import OrdinaryCliOwner
        selected = profile.data["terminal_owner"]
        owner = OrdinaryCliOwner(Path(selected["control_root"]), Path(selected["path"]),
            selected["sha256"], profile.environment)
    project = {"registeredUuid": data["project_uuid"], "path": data["project_path"]}
    def verify_evidence():
        profile.verify()
        evidence = orphan_launch_evidence(profile, root, entry, project, data["binding"], owner)
        if any(evidence[key] != data.get(key) or evidence[key] != claims[0].get(key) for key in evidence):
            raise ValueError("Original recovery evidence changed")
    verify_evidence()
    verify_target(profile, data)
    # Recheck both native identity and ended launcher immediately before use.
    verify_evidence()
    verify_target(profile, data)
    result = {"request_id": request, "session_id": data["session_id"], "binding": data["binding"],
        "phase": "before-stop", "stopped": False, "helper_cleanup_confirmed": False,
        "launch_request_id": data["launch_request_id"], "launch_intent_sha256": data["launch_intent_sha256"],
        "launch_receipt_sha256": data["launch_receipt_sha256"]}
    write_private_json(result_path, result)
    try:
        docker(profile, "container", "stop", "--time", "10", "--", data["binding"]["id"])
        result["stopped"] = exact_container_stopped(profile, data["binding"])
        verify_evidence()
        result["phase"] = "container-stopped" if result["stopped"] else "stop-unconfirmed"
        write_private_json(result_path, result)
        return result
    except Exception:
        result.update(phase="stop-unconfirmed", stopped=False)
        write_private_json(result_path, result)
        raise


def observe_orphan_stop(profile, data, root):
    """Read-only requalification for an explicitly recorded external stop."""
    from botainer_dashboard.ordinary_owner import OrdinaryCliOwner
    selected = profile.data["terminal_owner"]
    owner = OrdinaryCliOwner(Path(selected["control_root"]), Path(selected["path"]),
        selected["sha256"], profile.environment)
    operations = read_private_json(root / "operations.json", max_bytes=2 * 1024 * 1024)
    entries = [e for e in operations["operations"] if e.get("request_id") == data["launch_request_id"]]
    if len(entries) != 1: raise ValueError("Original launch is unavailable")
    project_check(data, profile)
    exact_record(profile, data)
    evidence = orphan_launch_evidence(profile, root, entries[0],
        {"registeredUuid": data["project_uuid"], "path": data["project_path"]}, data["binding"], owner)
    if any(evidence[k] != data.get(k) for k in evidence): raise ValueError("Original stop evidence changed")
    return {"stopped": exact_container_stopped(profile, data["binding"]), "binding": data["binding"],
        "session_id": data["session_id"], "helper_cleanup_confirmed": False}


def reviewed_hooks(profile):
    from botainer.plugins import hooks
    original = hooks.run_hook
    def guarded(**kwargs):
        verify_hook(profile, kwargs["script_path"], kwargs.get("plugin_name"), kwargs.get("hook_when"))
        native_run = hooks.subprocess.run
        expected = [sys.executable, str(kwargs["script_path"])]
        def isolated_run(argv, *args, **options):
            if argv == expected:
                argv = [sys.executable, "-I", "-B", str(REPO / "tools/ordinary_hook_helper.py"),
                        str(profile.path), str(kwargs["script_path"])]
            return native_run(argv, *args, **options)
        # Native run_hook still owns writable-source rejection, env scrubbing,
        # timeouts and contribution/error parsing. Only its Python child startup
        # is isolated from the project directory.
        hooks.subprocess.run = isolated_run
        try: return original(**kwargs)
        finally: hooks.subprocess.run = native_run
    hooks.run_hook = guarded
    return hooks, original


def verify_hook(profile, path, plugin, when):
    try:
        profile.verify_hook(path)
    except BackendUnavailable:
        # Paths/plugin names are data, not terminal escapes or shell fragments.
        detail = json.dumps({"plugin": str(plugin), "phase": str(when), "script": str(path)}, ensure_ascii=True)
        print("[dashboard] Startup stopped: this hook has not been enabled for dashboard execution. "
              "This does not mean software is missing.\n[dashboard] " + detail, file=sys.stderr)
        raise


def verify_start_hooks(profile, spec, *, durable=False):
    # Review the entire native spec before any host hook executes, including
    # cleanup hooks. Approving only the first hook could otherwise start a
    # credential helper before a later unapproved hook aborts composition.
    if not durable and {"agent-claude-broker", "agent-codex-broker"}.intersection(spec.plugins_enabled):
        print("[dashboard] Startup stopped: this credential broker follows the Botainer launcher process. "
              "The dashboard's detached CLI would exit while the container continues, so authentication "
              "could stop shortly after launch. A persistent native CLI owner is required. "
              "No startup hook has run and no container has been dispatched by this request.", file=sys.stderr)
        raise BackendUnavailable("ordinary-broker-owner-required")
    if durable and "nudge" in spec.plugins_enabled:
        print("[dashboard] Startup stopped: this project enables Botainer's inner Screen wrapper. "
              "Its detach/cleanup behavior has not yet been qualified with a persistent CLI owner. "
              "No startup hook has run; project settings were not changed.", file=sys.stderr)
        raise BackendUnavailable("ordinary-nested-screen-owner-unqualified")
    for hook in spec.hooks:
        verify_hook(profile, hook.script_path, hook.plugin, hook.when)


def corrected_docker_argv(argv, spec, detach):
    """Keep the native argv/mount/security policy; correct only transport flags."""
    argv = list(argv)
    if detach:
        if argv[:4] != ["docker", "run", "--rm", "-d"]:
            raise ValueError("Docker compatibility bridge needs source requalification")
        # Under native nudge Screen must own foreground docker; otherwise Docker
        # owns the detached TTY directly. Neither uses a dashboard host shell.
        argv[3] = "-it" if "nudge" in spec.plugins_enabled else "-dit"
        argv[4:4] = ["--pull", "never"]
    return argv


STARTUP_FAILURES = {
    "ordinary-startup-observation-failed": "Docker startup could not be observed reliably",
    "ordinary-startup-binding-invalid": "the observed container could not be matched to the native session",
    "ordinary-startup-owner-replaced": "the container identity changed during startup",
    "ordinary-startup-tty-unavailable": "the container does not have the expected terminal",
    "ordinary-startup-stdin-unavailable": "the container does not have writable terminal input",
    "ordinary-startup-ended-before-ready": "the container ended before its terminal was confirmed",
    "ordinary-startup-state-unconfirmed": "the container entered an unexpected startup state",
    "ordinary-startup-timeout": "the container has not reached a confirmed running state yet",
}


def observe_startup(profile, data, session_id, *, timeout=15):
    """Observe one native dispatch; never interpret a created container as failed.

    The retry window is bounded; each Docker observation also has its existing
    command timeout. The first qualified identity remains pinned across polls.
    StartedAt is excluded until running because Docker fills it in after create.
    A returned failure describes observation, not permission to kill or retry.
    """
    deadline = time.monotonic() + timeout
    candidate = None

    def unconfirmed(code):
        result = {"phase": "dispatch-unconfirmed", "session_id": session_id, "failure_code": code}
        if candidate is not None:
            result["candidate_binding"] = candidate
        return result

    while True:
        try:
            record = exact_record(profile, {**data, "session_id": session_id})
            containers = docker_inventory(profile)
            container = containers.get("/botainer-" + session_id[:12])
        except (OSError, ValueError, KeyError, TypeError, AttributeError, BackendUnavailable, InventoryError):
            return unconfirmed("ordinary-startup-observation-failed")
        if container is not None:
            try:
                binding = container_binding(record, container)
                identity = {key: binding[key] for key in ("id", "name", "image", "created")}
                if any(not isinstance(value, str) or not value for value in identity.values()):
                    raise ValueError("Invalid immutable container identity")
            except (ValueError, KeyError, TypeError, AttributeError):
                return unconfirmed("ordinary-startup-binding-invalid")
            if candidate is None:
                candidate = identity
            elif identity != candidate:
                return unconfirmed("ordinary-startup-owner-replaced")
            if container.get("tty") is not True:
                return unconfirmed("ordinary-startup-tty-unavailable")
            if container.get("stdin") is not True:
                return unconfirmed("ordinary-startup-stdin-unavailable")
            state = container.get("state")
            if not isinstance(state, str):
                return unconfirmed("ordinary-startup-state-unconfirmed")
            if state == "running":
                started = binding["started_at"]
                if (not isinstance(started, str) or not started
                        or started.startswith("0001-01-01T00:00:00")):
                    return unconfirmed("ordinary-startup-observation-failed")
                return {"phase": "runtime-returned", "session_id": session_id, "binding": binding}
            if state in {"exited", "dead"}:
                return unconfirmed("ordinary-startup-ended-before-ready")
            if state != "created":
                return unconfirmed("ordinary-startup-state-unconfirmed")
        # Missing and created both mean pending startup. Preserve a candidate
        # through temporary absence; never accept a different replacement ID.
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return unconfirmed("ordinary-startup-timeout")
        time.sleep(min(.2, remaining))


def native_operation(profile, data, intent_path, cli_main):
    from botainer.core import composition
    from botainer.adapters.docker import DockerAdapter
    project = project_check(data, profile)
    if Path.cwd() != project: raise ValueError("CLI working directory changed")
    result_path = Path(data["result_path"])
    if result_path.parent != intent_path.parent or result_path.name != data["request_id"] + ".result.json":
        raise ValueError("Invalid result receipt path")
    common = {"request_id": data["request_id"], "intent_sha256": sha(intent_path)}
    durable = data.get("terminal_owner_id") is not None
    if durable:
        selected = profile.data.get("terminal_owner")
        if not selected or data["terminal_owner_id"] != uuid.UUID(data["request_id"]).hex:
            raise ValueError("Persistent CLI owner selection changed")
        expected_socket = str(Path(selected["control_root"]) / data["terminal_owner_id"] / "s")
        if os.environ.get("TMUX", "").split(",")[0] != expected_socket or os.environ.get("TMUX_PANE") != "%0":
            raise ValueError("Native CLI is not inside its selected persistent terminal")
    entered, launched, unconfirmed, code, failure_code = False, None, None, 1, None
    original_launch, original_render = composition.launch, DockerAdapter.render_argv
    original_compose = composition.compose_session

    def compose(*args, **kwargs):
        spec = original_compose(*args, **kwargs)
        if data["action"] == "start":
            check_user_mount_boundaries(spec, data, profile)
            verify_start_hooks(profile, spec, durable=durable)
        return spec

    def render(adapter, spec, *, detach=False, interactive=True):
        argv = original_render(adapter, spec, detach=detach, interactive=interactive)
        if durable:
            if detach or not interactive or argv[:4] != ["docker", "run", "--rm", "-it"]:
                raise ValueError("Persistent CLI Docker argv needs requalification")
            return [*argv[:4], "--pull", "never", *argv[4:]]
        return corrected_docker_argv(argv, spec, detach)

    def dispatch(spec, *, detach=False):
        nonlocal entered, launched, unconfirmed
        if entered: raise ValueError("A launch operation cannot dispatch twice")
        project_check(data, profile); profile.verify(); verify_daemon(profile)
        check_user_mount_boundaries(spec, data, profile)
        if (detach != (not durable) or spec.runtime != "docker" or spec.project_uuid != data["project_uuid"]
                or spec.project_root != str(project) or Path(spec.state_dir) != Path(profile.data["state_root"]) / "state" / spec.project_uuid):
            raise ValueError("Native CLI composed a different target")
        entered = True
        write_private_json(result_path, {**common, "phase": "before-dispatch", "session_id": spec.session_id})
        handle = original_launch(spec, detach=not durable)
        observed = observe_startup(profile, data, spec.session_id)
        if observed["phase"] == "runtime-returned":
            launched = {"session_id": spec.session_id, "binding": observed["binding"]}
            write_private_json(result_path, {**common, **observed})
        elif durable:
            # Foreground Docker starts asynchronously. Throwing here would run
            # native post-session cleanup while its child may still be starting,
            # stopping the credential broker and closing the owning terminal.
            # Keep the original native attach/wait path responsible for lifetime.
            unconfirmed = observed
            write_private_json(result_path, {**common, **unconfirmed})
            print("[dashboard] Startup was dispatched, but " + STARTUP_FAILURES[observed["failure_code"]]
                  + ". Botainer keeps ownership of this attempt; the dashboard will not start it again.", file=sys.stderr)
        else:
            raise BackendUnavailable(observed["failure_code"])
        return handle

    hooks, original_hook = reviewed_hooks(profile)
    composition.launch, DockerAdapter.render_argv, composition.compose_session = dispatch, render, compose
    try:
        if data["action"] == "start":
            # Exclude another live/unknown execution before native composition
            # resets project-local anchors. A later external CLI race remains an
            # upstream coordination contract; no unsafe retry is attempted.
            before = inventory(profile)
            if (any(p["uuid"] == data["project_uuid"] and p.get("runtime_unverified") for p in before["projects"])
                    or any(s["project_uuid"] == data["project_uuid"] and s["state"] not in {"stopped", "failed"} for s in before["sessions"])):
                raise ValueError("This project has a live or unverified session")
            argv = ["start", "--runtime=docker", "--no-auto-onboard"] if durable else ["start", "--runtime=docker", "--detach", "--no-auto-onboard"]
            if data.get("agent") is not None:
                if data["agent"] not in {"claude", "codex"}: raise ValueError("Unsupported agent")
                argv.append("--agent=" + data["agent"])
        elif data["action"] == "init":
            argv = ["init", "--runtime=docker"]
        else: raise ValueError("Unsupported native action")
        code = cli_main(argv)
        code = code if type(code) is int else 0
        if data["action"] == "init" and code == 0:
            uid = str(uuid.UUID((project / ".botainer/project-id").read_text().strip()))
            if not (project / ".botainer/config.yaml").is_file(): raise ValueError("Native initialization is incomplete")
            write_private_json(result_path, {**common, "phase": "completed", "exit_code": 0, "project_uuid": uid})
        return code
    except KeyboardInterrupt:
        code = 130; raise
    except SystemExit as error:
        code = error.code if type(error.code) is int else 1; raise
    except BackendUnavailable as error:
        failure_code = error.code
        raise
    finally:
        composition.launch, DockerAdapter.render_argv = original_launch, original_render
        composition.compose_session = original_compose
        hooks.run_hook = original_hook
        if data["action"] == "start":
            if not entered:
                diagnostic = {"failure_code": failure_code} if failure_code else {}
                write_private_json(result_path, {**common, "phase": "not-dispatched", "exit_code": code, **diagnostic})
            elif unconfirmed is not None:
                write_private_json(result_path, {**common, **unconfirmed, "cli_ended": True, "exit_code": code})
            elif launched and (durable or code == 0):
                write_private_json(result_path, {**common, "phase": "runtime-ended" if durable else "completed", "exit_code": code, **launched})
        elif code != 0:
            # Init may have written partial project files, but it never dispatches
            # a runtime. Keep the transcript and let a deliberate Open inspect it.
            write_private_json(result_path, {**common, "phase": "setup-failed", "exit_code": code})


def main():
    if len(sys.argv) != 3: raise ValueError("Expected a reviewed local profile and private request")
    profile = OrdinaryLocalProfile(Path(sys.argv[1])); profile.verify()
    path = Path(sys.argv[2])
    data = read_private_json(path)
    if data.get("profile_sha256") != profile.digest: raise ValueError("Profile revision changed")
    # Source-selected import is explicit, never the project's current directory.
    sys.path.insert(0, profile.data["source_root"])
    import botainer
    if Path(botainer.__file__).resolve().parent != Path(profile.data["source_root"]) / "botainer":
        raise ValueError("Selected Botainer source was not imported")
    from botainer.cli.main import main as cli_main
    action = data["action"]
    if action == "inventory":
        print(json.dumps(inventory(profile))); return 0
    if action == "orphan-observe-stopped":
        print(json.dumps(observe_orphan_stop(profile, data, path.parent))); return 0
    if action in {"start", "init"}: return native_operation(profile, data, path, cli_main)
    if action not in {"attach", "stop"}: raise ValueError("Unsupported helper operation")
    if action == "attach":
        raise BackendUnavailable("ordinary-external-terminal-owner-unverified")
    if data.get("stop_mode") == "orphan-container":
        print(json.dumps(stop_orphan_container(profile, data, path.parent))); return 0
    if data.get("terminal_owner_id") is None:
        raise BackendUnavailable("ordinary-external-cleanup-owner-unverified")
    record = verify_target(profile, data)
    if Path.cwd() != Path(data["project_path"]):
        os.chdir(data["project_path"])
    from botainer.cli import stop as stop_module
    from botainer.core import composition
    original_stop = stop_module._do_runtime_stop
    original_post = composition.run_post_session_hooks
    foreground_cleanup = data.get("terminal_owner_id") is not None
    if foreground_cleanup:
        from botainer_dashboard.ordinary_owner import OrdinaryCliOwner
        selected = profile.data.get("terminal_owner")
        if not selected or data["terminal_owner_id"] != uuid.UUID(data["launch_request_id"]).hex:
            raise ValueError("Stop cleanup owner changed")
        owner = OrdinaryCliOwner(Path(selected["control_root"]), Path(selected["path"]), selected["sha256"], profile.environment)
        observed = owner.observe(owner.recover(data["terminal_owner_id"]))
        receipt = read_private_json(path.parent / (data["launch_request_id"] + ".result.json"))
        phase = receipt.get("phase")
        matching_binding = (phase in {"runtime-returned", "runtime-ended"}
                            and receipt.get("binding") == data["binding"])
        if phase == "dispatch-unconfirmed":
            # Backend qualification does not rewrite the helper's historical
            # receipt. The caller's full current binding was independently
            # verified above; require all pinned creation identity fields too.
            candidate = {key: data["binding"][key] for key in ("id", "name", "image", "created")}
            matching_binding = (receipt.get("candidate_binding") == candidate
                                and receipt.get("cli_ended", False) is False
                                and receipt.get("failure_code") in {
                                    "ordinary-startup-timeout", "ordinary-startup-observation-failed"})
        if (observed["state"] != "running" or receipt.get("request_id") != data["launch_request_id"]
                or receipt.get("intent_sha256") != data["launch_intent_sha256"]
                or receipt.get("session_id") != data["session_id"] or not matching_binding):
            raise ValueError("Original foreground cleanup owner is unverified")
        def owned_post(spec):
            if spec.session_id != data["session_id"] or spec.project_uuid != data["project_uuid"]:
                raise ValueError("Cleanup target changed")
            # The durable native start's finally block owns cleanup. Calling the
            # same PID-based broker stop hook concurrently can signal a reused
            # PID. Container termination and CLI cleanup are observed separately.
        composition.run_post_session_hooks = owned_post
    def guarded_stop(target, *, force):
        if target.session_id != data["session_id"] or force: raise ValueError("Stop target changed")
        verify_target(profile, data)
        # Pin the full immutable Docker ID after rechecking the native record.
        target.docker.container_id = data["binding"]["id"]
        return original_stop(target, force=False)
    stop_module._do_runtime_stop = guarded_stop
    hooks, original_hook = reviewed_hooks(profile)
    try:
        output = StopOutput()
        with redirect_stdout(output), redirect_stderr(output):
            code = cli_main(["stop", "--", data["session_id"]])
        observation_error = None
        try:
            # A name can disappear or be reused while native Stop runs. Only
            # observation of the original full ID on the selected daemon may
            # confirm exit/absence. Native exit status alone is not a verdict.
            stopped = exact_container_stopped(profile, data["binding"])
        except (OSError, ValueError, KeyError, TypeError, AttributeError, BackendUnavailable, InventoryError):
            stopped = False
            observation_error = "ordinary-stop-observation-unconfirmed"
        print(json.dumps({"stopped": stopped, "exit_code": code,
            "foreground_cleanup": foreground_cleanup, "helper_cleanup_confirmed": False,
            "observation_error": observation_error, "diagnostic": output.diagnostic(),
            "diagnostic_truncated": output.truncated})); return 0
    finally:
        stop_module._do_runtime_stop = original_stop; hooks.run_hook = original_hook
        composition.run_post_session_hooks = original_post


if __name__ == "__main__":
    try:
        from botainer_dashboard.import_policy import source_imports
        with source_imports():
            raise SystemExit(main())
    except Exception as error:
        print("Dashboard native operation refused: " + str(error), file=sys.stderr)
        raise SystemExit(2)
