#!/usr/bin/env python3
"""Start, inspect or stop the dashboard; check local prerequisites.

Startup uses the existing dashboard environment; no command installs software.
check validates local files, while status/stop manage the service without probing
Docker/SSH. pair displays a usable one-time browser code for a running service;
it never starts or restarts a service. --pairing-code also accepts an exact
private access-file path for older services.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
import os
import re
from pathlib import Path
import sys
from urllib.parse import urlsplit


from botainer_dashboard.layout import resource_root, data_root, installed_layout, runtime_python, runtime_valid, verify_package

ROOT = resource_root()
DEFAULT_CONNECTIONS = Path(".local/connections/selection.json")
VENDOR_ASSETS = ("xterm/lib/xterm.js", "xterm/css/xterm.css",
                 "addon-fit/lib/addon-fit.js", "addon-search/lib/addon-search.js")


@dataclass(frozen=True)
class LaunchPlan:
    root: Path
    local: bool
    profiles: tuple[Path, ...]
    config: Path | None
    port: int | None
    open_browser: bool
    native_cli: bool = False
    local_profile: Path | None = None
    remote_profiles: tuple[Path, ...] = ()
    host_profiles: tuple[Path, ...] = ()
    connections: Path | None = None
    setup_only: bool = False
    data_dir: Path | None = None

    @property
    def interpreter(self):
        return runtime_python(self.root)

    def argv(self):
        result = [str(self.interpreter), "-I", "-B", str(self.root / "tools/run_dashboard.py")]
        if self.data_dir is not None:
            result.extend(("--data-dir", str(self.data_dir)))
        if self.connections is not None:
            result.extend(("--connections", str(self.connections)))
        if self.setup_only:
            result.append("--setup-only")
        if self.local_profile is not None:
            result.extend(("--local-profile", str(self.local_profile)))
        elif self.local:
            result.append("--workspace")
        for profile in self.remote_profiles:
            result.extend(("--remote-profile", str(profile)))
        for profile in self.host_profiles:
            result.extend(("--host-profile", str(profile)))
        if self.native_cli:
            result.append("--native-cli")
        for profile in self.profiles:
            result.extend(("--cluster-profile", str(profile)))
        if self.config is not None:
            result.extend(("--config", str(self.config)))
        if self.port is not None:
            result.extend(("--port", str(self.port)))
        if self.open_browser:
            result.append("--open")
        return result


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    trial_help = lambda message: argparse.SUPPRESS if installed_layout(ROOT) else message
    value.add_argument("command", nargs="?", choices=("start", "check", "status", "stop", "pair"),
                       help="Default: start in this terminal. status/stop manage only the selected data directory's service records")
    value.add_argument("--data-dir", type=Path, help="Private dashboard data directory; defaults to per-user state for an installed wheel, .local for source")
    value.add_argument("--instance", metavar="RUN_ID", help="Exact dashboard instance ID reported by status")
    value.add_argument("--json", action="store_true", help="Machine-readable status; never includes pairing codes")
    value.add_argument("--pairing-code", type=Path, metavar="ACCESS_FILE",
                       help="Display a usable code for this exact running service in your terminal; no restart or runtime actions")
    value.add_argument("--check", action="store_true", help="Check local files/settings only; do not start or connect")
    modes = value.add_mutually_exclusive_group()
    modes.add_argument("--local-only", action="store_true", help=trial_help("Local workspace without any cluster connection"))
    modes.add_argument("--cluster-only", action="store_true", help=trial_help("Selected cluster workspace(s) without the local Docker workspace"))
    value.add_argument("--cluster-profile", action="append", type=Path, default=[], metavar="PATH",
                       help=trial_help("Prepared private trial profile; repeat for multiple clusters; never selected automatically"))
    value.add_argument("--config", type=Path, help="Dashboard registrations; defaults to config.json in the selected data directory when present")
    value.add_argument("--port", type=int, help="Startup port (default: remembered; 0: available port), or exact port to select for status/stop/pair")
    value.add_argument("--open", action="store_true", help="Ask the launcher to open its loopback URL in an existing browser")
    value.add_argument("--native-cli", action="store_true", help=trial_help("Separate local shell trial with Botainer's native terminal launch prompts"))
    value.add_argument("--local-profile", type=Path, help="Reviewed existing local Botainer installation")
    value.add_argument("--remote-profile", action="append", type=Path, default=[], help="Reviewed existing SSH Botainer installation; repeat for multiple locations")
    value.add_argument("--host-profile", action="append", type=Path, default=[], help="Reviewed installed local agents without containers; no automatic installation")
    value.add_argument("--connections", type=Path, help="Private saved machine selection; defaults to connections/selection.json in the selected data directory")
    value.add_argument("--setup-only", action="store_true", help="Open machine setup without loading runtime controllers; uses the saved default selection unless --connections is supplied")
    return value


def present(path):
    # A broken symlink must cause validation failure, not silently remove a site.
    return path.exists() or path.is_symlink()


def plan_for(args, *, root=ROOT):
    root = Path(root).resolve()
    state = data_root(root, args.data_dir)
    connections = args.connections
    explicit_trial = args.local_only or args.cluster_only or bool(args.cluster_profile) or args.native_cli
    if installed_layout(root) and explicit_trial:
        raise ValueError("prepared trial modes require the development checkout; use Settings → Machines")
    if args.data_dir is not None and explicit_trial:
        raise ValueError("prepared trial modes use their original checkout state; omit --data-dir")
    explicit_installed = args.local_profile is not None or bool(args.remote_profile) or bool(args.host_profile)
    if connections is None and not explicit_trial and not explicit_installed:
        # First launch opens empty machine setup. Subsequent launches reuse only
        # the selection explicitly saved there, never a discovered trial profile.
        connections = state / "connections/selection.json"
    if args.setup_only and connections is None:
        raise ValueError("--setup-only requires --connections PATH when a runtime route is specified")
    ordinary = explicit_installed or connections is not None
    if ordinary and (args.local_only or args.cluster_only or args.cluster_profile or args.native_cli):
        raise ValueError("installed profiles cannot be mixed with prepared trial flags")
    if args.local_only and args.cluster_profile:
        raise ValueError("--local-only cannot be combined with --cluster-profile")
    if args.native_cli and args.cluster_only:
        raise ValueError("--native-cli requires a local workspace")
    if args.port is not None and not 0 <= args.port <= 65535:
        raise ValueError("port must be 0–65535")
    profiles = tuple(path.absolute() for path in args.cluster_profile)
    if args.cluster_only and not profiles:
        raise ValueError("no cluster profile selected; supply --cluster-profile PATH for an already prepared workspace")
    if len(profiles) > 16:
        raise ValueError("at most 16 cluster profiles may be selected")
    if len(set(profiles)) != len(profiles):
        raise ValueError("the same cluster profile was selected more than once")
    config = args.config.absolute() if args.config is not None else None
    if config is None and present(state / "config.json"):
        config = state / "config.json"
    remote_profiles = tuple(path.absolute() for path in args.remote_profile)
    if len(remote_profiles) > 16 or len(set(remote_profiles)) != len(remote_profiles):
        raise ValueError("select at most 16 distinct remote profiles")
    host_profiles = tuple(path.absolute() for path in args.host_profile)
    if len(host_profiles) > 8 or len(set(host_profiles)) != len(host_profiles):
        raise ValueError("select at most 8 distinct host-agent profiles")
    return LaunchPlan(root, args.local_profile is not None if ordinary else not args.cluster_only,
                      profiles, config, args.port, args.open, args.native_cli,
                      args.local_profile.absolute() if args.local_profile else None, remote_profiles, host_profiles,
                      connections.absolute() if connections else None, args.setup_only,
                      state if args.data_dir is not None or installed_layout(root) else None)


def selected_installations(plan):
    """Read selection only. --check must not create a registry or its locks."""
    selected = {"local_profile": plan.local_profile, "remote_profiles": plan.remote_profiles, "host_profiles": plan.host_profiles}
    if plan.connections is not None and present(plan.connections):
        if plan.local_profile is not None or plan.remote_profiles or plan.host_profiles:
            raise ValueError("The connection selection already exists. Use --connections alone; explicit profiles seed only a new selection.")
        from botainer_dashboard.connections import ConnectionRegistry
        selected = ConnectionRegistry(plan.connections).selected_profiles()
    return selected


def inspect_prerequisites(plan):
    """Return local file/schema checks; never instantiate an executing backend."""
    # These two production loaders use only the standard library and perform
    # bounded read-only parsing. Disable import bytecode writes for --check.
    sys.dont_write_bytecode = True
    source = str(ROOT / "src")
    if not installed_layout(ROOT) and source not in sys.path:
        sys.path.insert(0, source)
    from botainer_dashboard.config import load_config

    checks = []
    try:
        runtime_ok = runtime_valid(plan.root)
    except (OSError, ValueError, RuntimeError):
        runtime_ok = False
    checks.append((runtime_ok, "Dashboard Python environment is present" if runtime_ok else
                   "Dashboard Python environment is missing or incompatible; see Installation"))
    try:
        verify_package(plan.root)
        checks.append((True, "Application resource inventory is intact" if installed_layout(plan.root) else "Source checkout selected"))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        checks.append((False, "Dashboard package needs attention: " + str(exc)))
    launcher = plan.root / "tools/run_dashboard.py"
    checks.append((launcher.is_file(), "Dashboard launcher is present" if launcher.is_file() else
                   "tools/run_dashboard.py is missing; restore the reviewed checkout"))
    asset_roots = (plan.root / "frontend/vendor", plan.root / ".local/frontend/vendor")
    assets_present = any(all((base / name).is_file() for name in VENDOR_ASSETS) for base in asset_roots)
    checks.append((assets_present, "Reviewed terminal assets are present" if assets_present else
                   "Reviewed terminal assets are missing; do not download replacements automatically"))
    try:
        load_config(plan.config)
        checks.append((True, "Dashboard registration settings parse correctly"))
    except (OSError, ValueError, RuntimeError) as exc:
        checks.append((False, "Dashboard registration settings need attention: " + str(exc)))
    if plan.local and plan.local_profile is None:
        approval = plan.root / ".local/reviews/disposable-runtime-plan.json"
        ok = approval.is_file() and not approval.is_symlink()
        checks.append((ok, "Local preparation record is present; launcher will verify its artifacts" if ok else
                       "Local preparation record is missing; use --cluster-only or restore the approved preparation"))
    if plan.profiles:
        from botainer_dashboard.cluster_profiles import load_cluster_profile
    profile_ids, fingerprints = set(), set()
    for index, path in enumerate(plan.profiles, 1):
        try:
            profile = load_cluster_profile(path)
            if profile.id == "local":
                raise ValueError("cluster profile ID 'local' is reserved for this computer")
            if profile.id in profile_ids or profile.fingerprint in fingerprints:
                raise ValueError("selected cluster identities are duplicated")
            profile_ids.add(profile.id)
            fingerprints.add(profile.fingerprint)
            checks.append((True, f"Cluster profile {index} parses with private-file safeguards; remote preparation is unchecked"))
        except (OSError, ValueError, RuntimeError) as exc:
            checks.append((False, f"Cluster profile {index} needs attention: {exc}"))
    try:
        selected = selected_installations(plan)
        if plan.connections is not None:
            checks.append((True, "Saved machine selection is readable" if present(plan.connections) else
                           "A private machine selection will be created on launch; this check creates nothing"))
    except (OSError, ValueError, RuntimeError) as exc:
        checks.append((False, "Machine selection needs attention: " + str(exc)))
        selected = {"local_profile": None, "remote_profiles": (), "host_profiles": ()}
    local_profile, remote_profiles, host_profiles = (selected[name] for name in ("local_profile", "remote_profiles", "host_profiles"))
    if plan.setup_only:
        # Saved selections were integrity-checked above without consulting their
        # installed source/tools. That is the point of the repair launch.
        if not present(plan.connections):
            from botainer_dashboard.pairing import read_private_json
            seed_paths = ([local_profile] if local_profile else []) + list(remote_profiles) + list(host_profiles)
            for index, path in enumerate(seed_paths, 1):
                try:
                    if any(p.is_symlink() for p in (path, *path.parents)):
                        raise ValueError("profile paths must not contain symlinks")
                    read_private_json(path, max_bytes=2 * 1024 * 1024)
                    checks.append((True, f"Profile seed {index} is private readable JSON; complete profile and installed-file validation occurs during first import"))
                except (OSError, ValueError, RuntimeError) as exc:
                    checks.append((False, f"Profile seed {index} needs attention: {exc}"))
        checks.append((True, "Setup only: no runtime controllers will load; installed sources, executables and remote access are intentionally unchecked"))
    elif local_profile is not None or remote_profiles or host_profiles:
        from botainer_dashboard.ordinary_local import load_local_profile
        from botainer_dashboard.remote_profile import load_remote_profile
        from botainer_dashboard.host_backend import load_host_profile
        installed_ids = set()
        for kind, path, loader in ([("Local", local_profile, load_local_profile)] if local_profile else []) + [
                ("Remote", path, load_remote_profile) for path in remote_profiles] + [
                ("Host agent", path, load_host_profile) for path in host_profiles]:
            try:
                profile = loader(path)
                if profile.id in installed_ids or (kind != "Local" and profile.id == "local" and local_profile is None):
                    raise ValueError("duplicate or reserved location ID")
                installed_ids.add(profile.id)
                checks.append((True, f"{kind} installed profile parses; live identity and runtime remain unchecked"))
            except (OSError, ValueError, RuntimeError) as exc:
                checks.append((False, f"{kind} installed profile needs attention: {exc}"))
    return checks


def show_pairing_code(access_file, *, root=ROOT, expected_service=None):
    """Explicit owner request; refuse capture before reading or issuing a secret."""
    if os.name != "posix":
        print("The dashboard owner command currently requires macOS or Linux.")
        return 2
    if not sys.stdout.isatty():
        print("Run the pairing command in your own interactive terminal; codes are not printed to pipes or log files.", file=sys.stderr)
        return 2
    sys.dont_write_bytecode = True
    source = str(Path(root) / "src")
    if not installed_layout(ROOT) and source not in sys.path:
        sys.path.insert(0, source)
    from botainer_dashboard.pairing_control import request_pairing_code
    try:
        result = (request_pairing_code(access_file) if expected_service is None else
                  request_pairing_code(access_file, expected_service=expected_service))
    except (OSError, ValueError, TimeoutError) as error:
        print(f"Pairing code unavailable: {error}", file=sys.stderr)
        print("No service was started or restarted. Check dashboard status and the selected service's terminal.", file=sys.stderr)
        return 2
    print(f"Pair this browser at: {result['url']}")
    print(f"One-time pairing code: {result['token']}")
    expires = datetime.fromtimestamp(result["expiresAt"], timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"Expires: {expires}. Works once; keep the code private.")
    return 0


def manage_service(args, *, root=ROOT):
    """Same-account service control; never load profiles, signal PIDs or use HTTP."""
    if os.name != "posix":
        print("Dashboard service control currently requires macOS or Linux.", file=sys.stderr)
        return 2
    if args.command == "pair" and not sys.stdout.isatty():
        print("Run pair in your own interactive terminal; codes are not printed to pipes or log files.", file=sys.stderr)
        return 2
    sys.dont_write_bytecode = True
    source = str(ROOT / "src")
    if not installed_layout(ROOT) and source not in sys.path:
        sys.path.insert(0, source)
    from botainer_dashboard.service_control import (
        discover_services, inspect_service, request_service_action, wait_for_stopped,
    )
    try:
        run_root = data_root(Path(root), args.data_dir) / "run"
        records = ([inspect_service(run_root / args.instance / "service.json")]
                   if args.instance else discover_services(run_root))
        if args.port is not None:
            records = [record for record in records if urlsplit(record.get("url", "")).port == args.port]
        if args.command == "status":
            if args.json:
                print(json.dumps({"services": records}, sort_keys=True))
            else:
                visible = [record for record in records if record["status"] not in {"stopped", "unmanaged"}]
                if args.instance or args.port is not None:
                    visible = records
                for record in visible:
                    print(f"{record['runId']}  {record.get('url', 'address unavailable')}  {record['status']}")
                hidden = len(records) - len(visible)
                if hidden:
                    print(f"{hidden} older stopped/unmanaged record(s) omitted; --json includes them.")
                if not visible:
                    print("No active managed dashboard found in this data directory.")
                if any(r['status'] == 'unmanaged' for r in records):
                    print("Older services without control records require Ctrl+C in their original terminal; their liveness is unknown.")
                print("Service status only; agents, Docker and SSH were not checked.")
            if any(r["status"] not in {"stopped", "unmanaged", "running", "starting", "stopping"} for r in records):
                return 2
            if any(r.get("responsive") for r in records):
                return 0
            return 2 if any(r["status"] == "unmanaged" for r in records) else 1
        candidates = [record for record in records if record["status"] not in {"stopped", "unmanaged"}]
        if not candidates:
            print("No active managed dashboard selected." + (" Start the dashboard first. For an older running service, use its printed --pairing-code command."
                  if args.command == "pair" else " Older services require Ctrl+C in their original terminal."))
            return 1
        if len(candidates) != 1:
            print(f"More than one possible service: run status, then {args.command} --port PORT or {args.command} --instance RUN_ID. No action was requested.", file=sys.stderr)
            return 2
        record = candidates[0]
        if args.command == "pair":
            if not record.get("responsive") or record["status"] != "running":
                print("The selected dashboard is not confirmed ready. Check status or its service terminal; no pairing code was requested.", file=sys.stderr)
                return 2
            run_id = record.get("runId")
            if not isinstance(run_id, str) or re.fullmatch(r"[0-9a-f]{32}", run_id) is None:
                raise ValueError("invalid selected service identity")
            run = run_root / run_id
            if Path(record["serviceFile"]) != run / "service.json":
                raise ValueError("selected service belongs to another data directory")
            identity = {key: record[key] for key in ("runId", "url", "pid", "mode")}
            return show_pairing_code(run / "access.json", root=root, expected_service=identity)
        if not record.get("responsive"):
            print("Selected service control is unavailable or unconfirmed. Use its original terminal; no process was signalled.", file=sys.stderr)
            return 2
        result = request_service_action(record["serviceFile"], "stop")
        print(f"Shutdown requested: {result['runId']}  {result['url']}")
        if not wait_for_stopped(record["serviceFile"], timeout=10.0):
            print("Shutdown is not yet confirmed. Check status again; no force-stop or session Stop/Cancel was issued.", file=sys.stderr)
            return 2
        print("Dashboard stopped. Browser views disconnected; no session Stop/Cancel was requested.")
        return 0
    except (OSError, ValueError, TimeoutError) as error:
        print(f"Dashboard service control unavailable: {error}. No process was signalled.", file=sys.stderr)
        return 2


def main(argv=None, *, root=ROOT):
    command = parser()
    args = command.parse_args(argv)
    if args.pairing_code is not None:
        defaults = vars(command.parse_args([]))
        if any(value != defaults[key] for key, value in vars(args).items() if key not in {"pairing_code", "data_dir"}):
            command.error("--pairing-code cannot be combined with startup, selection or --check options")
        return show_pairing_code(args.pairing_code, root=root)
    if args.command in {"status", "stop", "pair"}:
        defaults = vars(command.parse_args([]))
        allowed = {"command", "port", "instance", "json", "data_dir"}
        if any(value != defaults[key] for key, value in vars(args).items() if key not in allowed):
            command.error("status/stop/pair accept --data-dir and either --port or --instance; --json is status-only")
        if args.port is not None and not 1 <= args.port <= 65535:
            command.error("status/stop/pair port must be 1–65535")
        if args.instance is not None and re.fullmatch(r"[0-9a-f]{32}", args.instance) is None:
            command.error("--instance must be the exact 32-character lowercase hexadecimal ID from status")
        if args.instance is not None and args.port is not None:
            command.error("select by --instance or --port, not both")
        if args.json and args.command != "status":
            command.error("--json is supported only with status")
        return manage_service(args, root=root)
    if args.instance is not None or args.json:
        command.error("--instance requires status/stop/pair; --json requires status")
    if args.command == "check":
        args.check = True
    try:
        plan = plan_for(args, root=root)
    except (ValueError, OSError, RuntimeError) as exc:
        command.error(str(exc))
    if os.name != "posix":
        print("The dashboard service currently requires macOS or Linux; native Windows hosting is unsupported.")
        return 2
    print("Dashboard data: " + str(data_root(plan.root, plan.data_dir)))
    checks = inspect_prerequisites(plan)
    for ok, message in checks:
        print(("OK: " if ok else "NEEDS ATTENTION: ") + message)
    selection = ("saved machine selection (Settings → Machines)" if plan.connections else
        ("local installation" if plan.local_profile else "local trial" if plan.local else "no local Botainer") +
        f"; {len(plan.profiles)} cluster trial(s); {len(plan.remote_profiles)} remote installation(s); {len(plan.host_profiles)} native host profile(s)")
    print("Selected: " + selection)
    if plan.setup_only:
        print("Setup only: machine controls are inactive. Repair Settings → Machines, then restart without --setup-only.")
    print("Not checked: dependency import integrity, Docker daemon/image, SSH login, remote helper, scheduler, host agent/tmux identity or running sessions.")
    if not all(ok for ok, _message in checks):
        print("Nothing was started or installed. See docs/getting-started.md.")
        if plan.connections and not plan.setup_only:
            print("If an installed source or executable changed, rerun with --setup-only to repair the saved selection in Settings → Machines.")
        return 2
    if args.check:
        print("Local prerequisite check passed. No service, browser, Docker or SSH connection was started.")
        return 0
    print("Starting the foreground dashboard. Ctrl+C stops the service without requesting session Stop/Cancel. Finish setup or launch prompts before stopping.", flush=True)
    try:
        # Replace this process: Ctrl+C targets the real foreground launcher.
        # No shell, service discovery, background daemon, or credentials in argv.
        os.execv(str(plan.interpreter), plan.argv())
    except OSError as exc:
        print(f"Could not execute the installed dashboard runtime: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
