"""Explicit local launch; no installation, automatic Botainer dispatch or host shell."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import socket
import sys
import uuid

from .layout import resource_root, data_root, prepare_data_root, installed_layout, runtime_valid, verify_package
from .access import LoopbackAccess
from .config import load_config
from .pairing import PairingStore, private_directory, read_private_json, write_private_json
from .pairing_control import PairingControl
from .service_control import ServiceControl


def authentication_profile(root: Path, config_path: Path | None, mode: str, *, data_dir: Path | None = None) -> Path:
    """Separate config/backend identities without putting private paths in names."""
    identity = json.dumps({"config": str(config_path.resolve()) if config_path else None,
                           "mode": mode}, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(identity.encode()).hexdigest()
    auth_root = private_directory(data_root(root, data_dir) / "auth")
    return private_directory(auth_root / digest)


def remembered_port(profile: Path) -> int:
    path = profile / "endpoint.json"
    if not path.exists() and not path.is_symlink():
        return 0
    value = read_private_json(path)
    if (set(value) != {"version", "port"} or value["version"] != 1
            or type(value["port"]) is not int or not 1 <= value["port"] <= 65535):
        raise ValueError("invalid saved dashboard endpoint")
    return value["port"]


def select_backend(root, config, *, proof=False, workspace=False, cluster_paths=(), native_cli=False,
                   local_profile=None, remote_paths=(), host_paths=(), data_dir=None):
    """Select explicit prepared controllers; selection never starts a runtime.

    Stable, sorted profile identities keep browser pairing independent of CLI
    ordering. Duplicate site IDs are refused before any controller is selected.
    """
    if local_profile is not None or remote_paths or host_paths:
        if proof or workspace or cluster_paths or native_cli:
            raise ValueError("installed profiles cannot be mixed with prepared trial modes")
        config_path = getattr(config, "source_path", None)
        return select_installed_backend(root, local_profile, remote_paths, host_paths=host_paths,
                                        **({"protected_paths": (config_path,)} if config_path is not None else {}),
                                        **({"data_dir": data_dir} if data_dir is not None else {}))
    if native_cli and (not workspace or proof):
        raise ValueError("--native-cli requires --workspace and cannot use --proof")
    if proof and (workspace or cluster_paths):
        raise ValueError("choose --proof separately from workspaces")
    if proof:
        from .proof_backend import ProofBackend
        return ProofBackend(root / ".local/reviews/disposable-runtime-plan.json"), "disposable-terminal-proof"
    if not workspace and not cluster_paths:
        return RegistrationBackend(config), "registrations"
    from .cluster_profiles import load_cluster_profile
    if len(cluster_paths) > 16:
        raise ValueError("at most 16 cluster profiles may be selected")
    profiles = [(path, load_cluster_profile(path)) for path in cluster_paths]
    ids = [profile.id for _, profile in profiles]
    if len(ids) != len(set(ids)) or "local" in ids:
        raise ValueError("cluster profile IDs must be unique and distinct from local")
    local = None
    if workspace:
        from .local_backend import LocalBackend
        local = LocalBackend(root, config, native_cli=True) if native_cli else LocalBackend(root, config)
    clusters = {}
    if profiles:
        from .cluster_backend import ClusterBackend
        clusters = {profile.id: ClusterBackend(root, path) for path, profile in profiles}
    if local is not None and not clusters:
        return local, "native-cli-workspace" if native_cli else "local-workspace"
    if local is None and len(clusters) == 1:
        child = next(iter(clusters.values()))
        return child, "cluster-workspace:" + child.profile.fingerprint
    from .combined_backend import CombinedBackend
    identity = {"local": bool(local), "clusters": {key: child.profile.fingerprint for key, child in sorted(clusters.items())}}
    if native_cli:
        identity["native_cli"] = True
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return CombinedBackend(local, clusters=clusters), "combined-workspace:" + digest


def select_installed_backend(root, local_path, remote_paths, *, host_paths=(), data_dir=None,
                             protected_paths=()):
    """Validate explicit installations before constructing their controllers."""
    from .ordinary_local import OrdinaryLocalBackend, load_local_profile
    from .remote_profile import load_remote_profile
    from .remote_backend import RemoteBackend
    from .combined_backend import CombinedBackend
    from .host_backend import HostAgentBackend, load_host_profile
    if len(remote_paths) > 16:
        raise ValueError("at most 16 remote installations may be selected")
    local_profile = load_local_profile(local_path) if local_path is not None else None
    remotes = [(path, load_remote_profile(path)) for path in remote_paths]
    if len(host_paths) > 8:
        raise ValueError("at most 8 host-agent profiles may be selected")
    hosts = [(path, load_host_profile(path)) for path in host_paths]
    local_id = local_profile.id if local_profile else "local"
    ids = [profile.id for _, profile in (*remotes, *hosts)]
    if len(ids) != len(set(ids)) or local_id in ids:
        raise ValueError("location IDs must be unique and distinct from the local location")
    # Profile order never changes browser identity. Changing a selected
    # installation does, so old grants cannot silently control a new target.
    identity = {"local": local_profile.fingerprint if local_profile else None,
                "remotes": {profile.id: profile.fingerprint for _, profile in remotes}}
    if hosts:
        # Executable-only revisions prove unchanged account/folder/control scope
        # against retained original bytes. Preserve browser grants for that same
        # connection; a full scope change still gets a new authorization identity.
        identity["hosts"] = {profile.id: profile.identity_fingerprint for _, profile in hosts}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    storage = {"data_dir": data_dir} if data_dir is not None else {}
    local_controls = []
    if local_profile:
        local_controls.extend(Path(profile.data["control_root"]) for _, profile in hosts)
        local_controls.extend(Path(profile.path) for _, profile in hosts)
        # Remote control directories belong to another filesystem. Their local
        # profile files are trusted inputs here and must not become project data.
        local_controls.extend(Path(path) for path, _ in remotes)
        local_controls.extend(Path(path) for path in protected_paths)
        for _, profile in hosts:
            local_controls.append(Path(profile.data["tmux"]["path"]))
            local_controls.extend(Path(agent["path"]) for agent in profile.data["agents"].values())
            local_controls.append(Path(profile.original_path))
    local = OrdinaryLocalBackend(root, local_path, **storage,
        **({"protected_paths": local_controls} if local_controls else {})) if local_profile else None
    clusters = {profile.id: RemoteBackend(root, path, **storage) for path, profile in remotes}
    clusters.update({profile.id: HostAgentBackend(root, path, **storage) for path, profile in hosts})
    actual = [(local_profile, local)] if local_profile else []
    actual.extend((profile, clusters[profile.id]) for _, profile in remotes)
    actual.extend((profile, clusters[profile.id]) for _, profile in hosts)
    for selected, controller in actual:
        if (controller.profile.id != selected.id
                or controller.profile.fingerprint != selected.fingerprint):
            raise ValueError("installation profile changed while starting; restart with the current profile")
    return CombinedBackend(local, clusters=clusters, local_id=local_id,
                           local_label=local_profile.label if local_profile else "This computer"), "installed:" + digest


class RegistrationBackend:
    """Show configured installations without executing any of them."""

    def __init__(self, config):
        self.config = config

    def snapshot(self):
        installations = []
        for machine_id, machine in self.config.machines.items():
            for install_id, install in machine.installations.items():
                installations.append({
                    "id": f"{machine_id}.{install_id}", "machine": machine_id,
                    "name": install_id, "transport": machine.transport,
                    "executable": install.launcher.path or "Default Botainer location",
                    "stateRoot": install.state_root, "state": "configured; not probed",
                })
        return {"mode": "registrations", "notice": "Installations are configured. Live runtime controls require a verified backend.",
                "capabilities": {}, "projects": [], "sessions": [], "installations": installations}

    def start_session(self, *_args):
        from .backend_errors import BackendUnavailable
        raise BackendUnavailable("runtime-not-enabled")

    stop_session = start_session
    attach = start_session


class EmptyConnectionsBackend(RegistrationBackend):
    def snapshot(self):
        return {"mode": "connections", "notice": "Open Settings → Machines to add this computer or an SSH cluster.",
                "capabilities": {}, "workspaces": [], "projects": [], "sessions": [], "installations": []}


class SetupConnectionsBackend(EmptyConnectionsBackend):
    def snapshot(self):
        return {**super().snapshot(), "mode": "connections-setup",
                "notice": "Setup only: no machine controllers are active. Open Settings → Machines to repair the saved selection, then restart without --setup-only to connect."}


def select_connections_backend(root, config, path, *, local_profile=None, remote_paths=(), host_paths=(), setup_only=False, data_dir=None):
    """Import initial profiles once, then consume the saved selection at startup."""
    from .connections import ConnectionRegistry
    from .connection_routes import ConnectionManager
    registry = ConnectionRegistry(path)
    seed = ([{"kind": "local", "path": local_profile}] if local_profile else [])
    seed += [{"kind": "remote", "path": p} for p in remote_paths]
    seed += [{"kind": "host", "path": p} for p in host_paths]
    registry.initialize(seed)
    saved = registry.read()
    if setup_only:
        # Repair remains possible after an installed executable/source changes.
        # Registry integrity is still mandatory; no selected runtime is loaded.
        mode = "connections-setup:" + hashlib.sha256(str(path.absolute()).encode()).hexdigest()
        return SetupConnectionsBackend(config), mode, ConnectionManager(registry, active_entries=[])
    # Consume one immutable revision: a settings save in another service must
    # not make the active-controller list describe different selected profiles.
    enabled = [entry for entry in saved["entries"] if entry["enabled"]]
    selected = {
        "local_profile": next((Path(e["profilePath"]) for e in enabled if e["kind"] == "local"), None),
        "remote_profiles": tuple(Path(e["profilePath"]) for e in enabled if e["kind"] == "remote"),
        "host_profiles": tuple(Path(e["profilePath"]) for e in enabled if e["kind"] == "host"),
    }
    if selected["local_profile"] or selected["remote_profiles"] or selected["host_profiles"]:
        config_path = getattr(config, "source_path", None)
        backend, mode = select_installed_backend(root, selected["local_profile"], selected["remote_profiles"],
                                                 host_paths=selected["host_profiles"],
                                                 protected_paths=(registry.path,) + ((config_path,) if config_path is not None else ()),
                                                 **({"data_dir": data_dir} if data_dir is not None else {}))
    else:
        backend = EmptyConnectionsBackend(config)
        mode = "connections-empty:" + hashlib.sha256(str(path.absolute()).encode()).hexdigest()
    return backend, mode, ConnectionManager(registry, active_entries=saved["entries"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    trial_help = lambda message: argparse.SUPPRESS if installed_layout(resource_root()) else message
    parser.add_argument("--data-dir", type=Path, help="Private dashboard operating data, outside the installed package")
    parser.add_argument("--config", type=Path, help="Trusted dashboard installation configuration")
    parser.add_argument("--port", type=int, help="Reuse this profile's previous loopback port by default; 0 chooses a new origin")
    parser.add_argument("--proof", action="store_true", help=trial_help("Use the prepared, explicitly approved disposable terminal backend"))
    parser.add_argument("--workspace", action="store_true", help=trial_help("Local projects, files, config and isolated cached-image shell sessions"))
    parser.add_argument("--native-cli", action="store_true", help=trial_help("Use a separate local trial workspace with real Botainer CLI launch prompts; requires --workspace"))
    parser.add_argument("--cluster-profile", type=Path, action="append", default=[], help=trial_help("Prepared private cluster profile; repeat for multiple sites, combine with --workspace for local + remote"))
    parser.add_argument("--local-profile", type=Path, help="Reviewed existing local Botainer installation and project roots")
    parser.add_argument("--remote-profile", type=Path, action="append", default=[], help="Reviewed existing remote Botainer installation; repeat for multiple locations")
    parser.add_argument("--host-profile", type=Path, action="append", default=[], help="Reviewed local installed agents without containers; repeat for distinct host profiles")
    parser.add_argument("--connections", type=Path, help="Private saved machine selection and GUI setup; optional explicit profiles seed it only on first start")
    parser.add_argument("--setup-only", action="store_true", help="Open machine setup without loading runtime controllers; requires --connections, useful after an installation changes")
    parser.add_argument("--open", action="store_true", help="Open the loopback dashboard in the existing default browser; never put a pairing token in its URL")
    args = parser.parse_args(argv)
    if args.setup_only and not args.connections:
        parser.error("--setup-only requires --connections PATH")
    if args.proof and (args.workspace or args.cluster_profile):
        parser.error("choose --proof separately from workspaces")
    if args.connections and (args.proof or args.workspace or args.native_cli or args.cluster_profile):
        parser.error("saved connections cannot be combined with trial modes")
    if args.port is not None and not 0 <= args.port <= 65535:
        parser.error("port must be 0–65535")
    root = resource_root()
    state = data_root(root, args.data_dir)
    if installed_layout(root) and (args.proof or args.workspace or args.cluster_profile or args.native_cli):
        parser.error("prepared trials require the original development checkout")
    if args.data_dir is not None and (args.proof or args.workspace or args.cluster_profile or args.native_cli):
        parser.error("prepared trials use their original checkout state")
    if not runtime_valid(root) or (not installed_layout(root) and
            Path(sys.prefix).resolve() != (root / ".local/envs/botainer_dashboard").resolve()):
        parser.error("run with the installed dashboard command or the prepared source environment")
    try:
        verify_package(root)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(f"cannot verify dashboard package: {error}")
    import uvicorn
    from .service import create_app, WS_MAX_SIZE, WS_MAX_QUEUE
    os.umask(0o077)
    try:
        state = prepare_data_root(root, args.data_dir)
    except (OSError, ValueError) as error:
        parser.error(f"cannot open dashboard data directory: {error}")
    config = load_config(args.config)
    try:
        connection_manager = None
        if args.connections:
            backend, mode, connection_manager = select_connections_backend(root, config, args.connections,
                local_profile=args.local_profile, remote_paths=args.remote_profile, host_paths=args.host_profile,
                setup_only=args.setup_only, data_dir=state)
        else:
            backend, mode = select_backend(root, config, proof=args.proof, workspace=args.workspace,
                cluster_paths=args.cluster_profile, native_cli=args.native_cli,
                local_profile=args.local_profile, remote_paths=args.remote_profile, host_paths=args.host_profile,
                data_dir=state)
    except (OSError, ValueError, RuntimeError) as error:
        repair = ("\nIf an installed source or executable changed, rerun with --setup-only to review its update or disable the connection in Settings → Machines."
                  if args.connections and not args.setup_only else "")
        parser.error(f"cannot select dashboard installations: {error}{repair}")
    assets = ("xterm/lib/xterm.js", "xterm/css/xterm.css", "addon-fit/lib/addon-fit.js",
              "addon-search/lib/addon-search.js")
    vendor = next((path for path in (root / "frontend/vendor", root / ".local/frontend/vendor")
                   if all((path / name).is_file() for name in assets)), None)
    if vendor is None:
        parser.error("reviewed local terminal assets are missing")
    run_root = private_directory(state / "run")
    run_dir = run_root / uuid.uuid4().hex
    run_dir.mkdir(mode=0o700)
    try:
        profile = authentication_profile(root, config.source_path, mode, data_dir=state)
        selected_port = remembered_port(profile) if args.port is None else args.port
    except (OSError, ValueError) as error:
        parser.error(f"cannot open private browser pairing state: {error}")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    pairings = None
    pairing_control = None
    service_control = None
    server = None
    app = None
    try:
        try:
            listener.bind(("127.0.0.1", selected_port))
        except OSError:
            parser.error("cannot bind the selected loopback port; stop the other dashboard or use --port 0 for a separate browser pairing")
        listener.listen(32)
        listener.setblocking(False)
        access = LoopbackAccess.create(listener.getsockname()[1])
        try:
            pairings = PairingStore(origin=access.origin, token=access.token,
                                   state_path=profile / f"browsers-{access.port}.json")
            write_private_json(profile / "endpoint.json", {"version": 1, "port": access.port})
        except (OSError, ValueError) as error:
            parser.error(f"cannot open private browser pairing state: {error}")
        credentials = run_dir / "access.json"
        pairing_control = PairingControl(run_dir, pairings, token=access.token,
                                         metadata={"url": access.origin, "pid": os.getpid(), "mode": mode})

        def request_shutdown():
            # This is the service owner's request, never a Botainer Stop/Cancel.
            # Fence new operations before Uvicorn begins draining its connections.
            app.state.service_stopping = True
            server.should_exit = True

        service_control = ServiceControl(run_dir,
            metadata={"url": access.origin, "pid": os.getpid(), "mode": mode},
            is_ready=lambda: bool(server and server.started), request_shutdown=request_shutdown)
        app = create_app(access=access, backend=backend, static_root=root / "frontend", vendor_root=vendor,
                         pairing_store=pairings, connection_manager=connection_manager, docs_root=root / "docs",
                         pairing_control=pairing_control, service_control=service_control,
                         installed_command=installed_layout(root))
        server = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", port=access.port, loop="asyncio", http="h11",
            ws="websockets", ws_max_size=WS_MAX_SIZE, ws_max_queue=WS_MAX_QUEUE,
            ws_per_message_deflate=False, proxy_headers=False, access_log=False,
            log_level="warning", limit_concurrency=32, timeout_keep_alive=5,
            h11_max_incomplete_event_size=16384, timeout_graceful_shutdown=5,
        ))
        print(f"Dashboard: {access.origin}", flush=True)
        print(f"Dashboard instance: {run_dir.name}", flush=True)
        print(f"Dashboard data: {state}", flush=True)
        command_prefix = [sys.executable, "-I", "-B", str(root / "tools/dashboard.py")]
        data_flags = ["--data-dir", str(state)] if args.data_dir is not None or installed_layout(root) else []
        for action in ("status", "stop"):
            owner_command = shlex.join(command_prefix + [action, "--instance", run_dir.name] + data_flags)
            print(f"To {action}: {owner_command}", flush=True)
        print(f"Private pairing file: {credentials}", flush=True)
        pairing_command = shlex.join(command_prefix + ["pair", "--instance", run_dir.name] + data_flags)
        print("To display a usable one-time code, run in another terminal:", flush=True)
        print(f"  {pairing_command}", flush=True)
        print("Codes expire after 10 minutes and work once. Request another code if needed; no service restart is required.", flush=True)
        print("A paired browser stays paired for 30 days; Sign out forgets it.", flush=True)
        print("Stopping this service disconnects viewers without requesting session Stop/Cancel. Finish setup or launch prompts first; those terminals may be interrupted.", flush=True)
        if args.setup_only:
            print("Setup only: no machine controllers are active. Repair Settings → Machines, then restart without --setup-only.", flush=True)
        if args.open:
            import threading
            import webbrowser
            # The listener is already bound. Browser opening does not carry
            # credentials and must not block service startup.
            threading.Thread(target=lambda: webbrowser.open(access.origin), daemon=True).start()
        try:
            server.run(sockets=[listener])
        except KeyboardInterrupt:
            print("Dashboard stopped. No session Stop/Cancel was requested.", flush=True)
    finally:
        listener.close()
        try:
            if pairing_control is not None:
                pairing_control.close()
        finally:
            try:
                if pairings is not None:
                    pairings.close()
            finally:
                # Release only after the server loop and listener have closed.
                # A failed mailbox must never imply that the service stopped.
                if service_control is not None:
                    service_control.close()


if __name__ == "__main__":
    main()
