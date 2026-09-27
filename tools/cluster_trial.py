#!/usr/bin/env python3
"""Operator-only, bounded SSH transport for the isolated cluster trial.

Uses an existing SSH alias/interpreter; installs nothing. The private profile has
exactly ssh_alias, remote_python, trial_root, source_root and image. Its directory
must be private (0700), and the profile 0600. Each invocation writes a new private
receipt before dispatch. A lost connection or timeout never triggers a retry.
This tool is not an application endpoint or a general remote command runner.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import shlex
import signal
import stat
import subprocess
import sys
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "tools/prepare_cluster_trial.py"
RUNTIME_HELPER = ROOT / "tools/cluster_runtime_probe.py"
PREPARATION_ACTIONS = frozenset({"prepare", "preflight"})
RUNTIME_ACTIONS = frozenset({"submit", "status", "owner", "stop"})
ACTIONS = tuple(sorted(PREPARATION_ACTIONS | RUNTIME_ACTIONS))
MAX_SOURCE = 262144
MAX_PROFILE = 16384
STDOUT_LIMIT = 2 * 1024 * 1024
STDERR_LIMIT = 64 * 1024
TIMEOUT = 60.0
PROFILE_FIELDS = {"ssh_alias", "remote_python", "trial_root", "source_root", "image"}
BOOTSTRAP = (
    "import sys\n"
    f"source=sys.stdin.buffer.read({MAX_SOURCE + 1})\n"
    f"if not source or len(source)>{MAX_SOURCE}: raise SystemExit('invalid helper size')\n"
    "exec(compile(source,'<dashboard-cluster-trial>','exec'),"
    "{'__name__':'__main__','__file__':'<dashboard-cluster-trial>',"
    "'__dashboard_trial_source__':source})\n"
)
# Keep the prepared source command available without packaging its trial CLI.
# This path is fixed by the tool location, never by a profile or request value.
sys.path.insert(0, str(ROOT / "src"))
from botainer_dashboard.ssh_transport import (SSH_OPTIONS, Refused, child_environment,
                                             bounded_exchange, write_new)


def remote_path(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise Refused(f"{label} must be an absolute remote path")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise Refused(f"{label} contains control characters")
    path = PurePosixPath(value)
    if not value.startswith("/") or value.startswith("//") or str(path) != value:
        raise Refused(f"{label} must be an absolute normalized remote path")
    if value == "/" or ".." in path.parts:
        raise Refused(f"{label} must identify a specific remote path")
    return value


def validate_profile(profile: object) -> dict[str, str]:
    if not isinstance(profile, dict) or set(profile) != PROFILE_FIELDS:
        raise Refused("Profile must contain exactly: " + ", ".join(sorted(PROFILE_FIELDS)))
    alias = profile["ssh_alias"]
    if not isinstance(alias, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", alias):
        raise Refused("ssh_alias must be a simple SSH config alias")
    clean = {"ssh_alias": alias}
    for name in sorted(PROFILE_FIELDS - {"ssh_alias"}):
        clean[name] = remote_path(profile[name], name)
    trial = PurePosixPath(clean["trial_root"])
    source = PurePosixPath(clean["source_root"])
    if trial == source or trial in source.parents or source in trial.parents:
        raise Refused("Trial and working source paths must be separate trees")
    for name in ("remote_python", "image"):
        if PurePosixPath(clean[name]) == trial or trial in PurePosixPath(clean[name]).parents:
            raise Refused(f"{name} must exist outside the new trial tree")
    return clean


def _check_private_parent(path: Path) -> Path:
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if part.is_symlink():
            raise Refused("Private receipt/profile directory must not contain symlinks")
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise Refused("Profile directory must be owned by this user with mode 0700")
    return path


def load_profile(path: Path) -> tuple[dict[str, str], bytes, Path]:
    parent = _check_private_parent(path.parent)
    path = parent / path.name
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise Refused("Profile must be a user-owned regular file with mode 0600")
        raw = stream.read(MAX_PROFILE + 1)
    if len(raw) > MAX_PROFILE:
        raise Refused("Profile exceeds size limit")
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise Refused("Profile contains a duplicate field")
            result[key] = value
        return result
    try:
        profile = json.loads(raw, object_pairs_hook=unique_pairs)
    except (ValueError, UnicodeError) as exc:
        raise Refused("Profile is not valid unambiguous JSON") from exc
    return validate_profile(profile), raw, parent


def helper_source(action: str) -> bytes:
    if action in PREPARATION_ACTIONS:
        path = HELPER
    elif action in RUNTIME_ACTIONS:
        path = RUNTIME_HELPER
    else:
        raise Refused("Unsupported trial action")
    if path.is_symlink():
        raise Refused("Repository helper must not be a symlink")
    with path.open("rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise Refused("Repository helper must be a regular file")
        source = stream.read(MAX_SOURCE + 1)
    if not source or len(source) > MAX_SOURCE:
        raise Refused("Repository helper exceeds size limit or is empty")
    compile(source, "<dashboard-cluster-trial>", "exec")
    return source


def ssh_argv(profile: dict[str, str], action: str) -> list[str]:
    profile = validate_profile(profile)
    if action not in ACTIONS:
        raise Refused("Unsupported trial action")
    remote = [profile["remote_python"], "-I", "-B", "-c", BOOTSTRAP,
              action, "--trial-root", profile["trial_root"]]
    if action == "prepare":
        remote.extend(["--source-root", profile["source_root"], "--image", profile["image"],
                       "--interpreter", profile["remote_python"]])
    argv = ["/usr/bin/ssh", "-T"]
    for option in SSH_OPTIONS:
        argv.extend(["-o", option])
    # SSH's remote side invokes the account shell. Quote every argument once;
    # neither this local process nor profile values supply shell program text.
    argv.extend([profile["ssh_alias"], shlex.join(remote)])
    return argv



def write_json(path: Path, value: object) -> None:
    write_new(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


def invoke(profile_path: Path, action: str) -> tuple[dict, Path]:
    profile, raw_profile, parent = load_profile(profile_path)
    argv = ssh_argv(profile, action)
    source = helper_source(action)
    receipt = parent / ("cluster-trial-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex)
    receipt.mkdir(mode=0o700)
    write_new(receipt / "helper.py", source)
    write_json(receipt / "intent.json", {
        "action": action, "argv": argv, "profile_sha256": hashlib.sha256(raw_profile).hexdigest(),
        "helper_sha256": hashlib.sha256(source).hexdigest(), "helper_bytes": len(source),
        "timeout_seconds": TIMEOUT, "stdout_limit": STDOUT_LIMIT, "stderr_limit": STDERR_LIMIT,
        "retry": "never automatic; unknown outcome requires remote reconciliation",
    })
    try:
        result = bounded_exchange(argv, source, env=child_environment())
    except BaseException as exc:
        write_json(receipt / "interrupted.json", {"remote_outcome": "unknown", "error_type": type(exc).__name__,
                                                "retry": "Do not repeat until remote state is reconciled"})
        raise
    write_new(receipt / "stdout.bin", result.pop("stdout"))
    write_new(receipt / "stderr.bin", result.pop("stderr"))
    write_json(receipt / "result.json", result)
    return result, receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, type=Path, help="Private existing JSON profile (0600, parent 0700)")
    parser.add_argument("action", choices=ACTIONS)
    args = parser.parse_args(argv)
    try:
        result, receipt = invoke(args.profile, args.action)
    except (Refused, OSError, SyntaxError) as exc:
        print(f"Cluster trial refused or interrupted: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({**result, "receipt": str(receipt)}, sort_keys=True))
    if result["remote_outcome"] == "unknown":
        print("Remote outcome is unknown. Reconcile remote state before repeating; no retry was attempted.", file=sys.stderr)
    return 0 if result["transport_status"] == "complete" and result["returncode"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
