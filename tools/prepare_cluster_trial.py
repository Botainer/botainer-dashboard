#!/usr/bin/env python3
"""Prepare one private cluster fixture using existing software; never submit.

Run with the existing Botainer interpreter. ``prepare`` copies source and
registers only three local plugins. ``preflight`` composes using that copy and
writes private session state; it is NOT a side-effect-free preview. Neither
command executes Slurm, Apptainer, a shell, a package manager or an AI agent.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys
import uuid


PLUGIN = "agent-dashboard-cluster-test"
LIMIT_FILE = 4 * 1024 * 1024
LIMIT_SOURCE = 32 * 1024 * 1024
FIXTURE = r'''#!/bin/sh
# Repository-authored deterministic test. Input is data, never shell syntax.
set -eu
cd /workspace
stty -echo
token=$(cat /proc/sys/kernel/random/uuid)
start_ticks=$(awk '{print $22}' /proc/$$/stat)
identity() {
    printf 'IDENTITY token=%s pid=%s start=%s cwd=%s\n' "$token" "$$" "$start_ticks" "$PWD"
}
trap 'printf "STOPPED token=%s\n" "$token"; exit 0' TERM INT
printf 'BOTAINER DASHBOARD CLUSTER TEST -- no AI credentials; host networking\n'
printf 'READY\n'
identity
while IFS= read -r line; do
    case "$line" in
        identity) identity ;;
        size) printf 'SIZE '; stty size ;;
        hello) printf 'ECHO hello\n' ;;
        'echo '*) printf 'ECHO %s\n' "${line#echo }" ;;
        quit|exit) printf 'BYE\n'; exit 0 ;;
        *) printf 'UNKNOWN -- use identity, size, hello, echo TEXT, or exit\n' ;;
    esac
done
printf 'EOF\n'
'''
MANIFEST = f'''apiVersion: botainer-plugin-v1
name: {PLUGIN}
version: 0.0.1
description: Clearly marked deterministic dashboard cluster test; no AI agent
license: Apache-2.0
maintainer: Botainer dashboard contributors
botainer_min_version: 0.1.0a0
tier: first-party
kind: agent
trust_required: declarative
runtimes: [apptainer]
capabilities: []
hooks: []
commands: []
contributes:
  entrypoint_wrap:
    layer: inner
    command: [/bin/sh, /mnt/dashboard-fixture/terminal.sh]
'''


class TrialError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise TrialError(message)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def new_file(path, data, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    require(not path.parent.is_symlink(), "refusing symlink parent")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data.encode() if isinstance(data, str) else data)


def new_json(path, value):
    new_file(path, json.dumps(value, sort_keys=True, indent=2) + "\n")


def read_json(path):
    path = Path(path)
    require(not path.is_symlink() and path.stat().st_size <= LIMIT_SOURCE,
            "invalid private receipt")
    return json.loads(path.read_text())


def regular(path, limit=LIMIT_FILE):
    path = Path(path)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_size <= limit,
            f"not an allowed regular file: {path}")
    return info


def canonical(path):
    value = Path(path).expanduser()
    require(value.is_absolute() and ".." not in value.parts,
            "paths must be absolute without parent traversal")
    result = value.resolve(strict=True)
    require(str(value) == str(result), "source/trial/image paths must be canonical, without symlinks")
    return result


def fresh_root(path, home):
    value = Path(path).expanduser()
    require(value.is_absolute() and ".." not in value.parts,
            "trial root must be absolute without parent traversal")
    require(value.name.startswith("botainer-dashboard-test-"),
            "trial root must be clearly named botainer-dashboard-test-...")
    parent = canonical(value.parent)
    home = Path(home).resolve(strict=True)
    require(parent == home or parent.is_relative_to(home), "trial root must be beneath HOME")
    require(not value.exists() and not value.is_symlink(),
            "trial root already exists; do not overwrite or retry partial preparation")
    require(re.fullmatch(r"[A-Za-z0-9._/~-]+", str(value)) is not None,
            "trial path is outside the restricted trial path alphabet")
    return value


def source_files(source):
    """Return bounded regular files without following directory symlinks."""
    selected = []
    for top in ("botainer", "plugins", "licenses"):
        start = source / top
        if top == "licenses" and not start.exists():
            continue
        require(start.is_dir() and not start.is_symlink(), f"missing source directory: {top}")
        for directory, dirs, names in os.walk(start, followlinks=False):
            for name in dirs:
                require(not (Path(directory) / name).is_symlink(), "source directory symlink refused")
            dirs[:] = [name for name in dirs if name not in ("__pycache__", ".git")]
            for name in names:
                path = Path(directory) / name
                require(not path.is_symlink(), "source file symlink refused")
                if name.endswith((".pyc", ".pyo")):
                    continue
                selected.append(path)
    for name in ("pyproject.toml", "LICENSE", "LICENSE.md", "LICENSE.txt", "NOTICE", "THIRD-PARTY-LICENSES.md"):
        path = source / name
        if path.exists() or path.is_symlink():
            selected.append(path)
    require(source / "pyproject.toml" in selected, "source must include pyproject.toml")
    require(any(path.name.startswith("LICENSE") for path in selected), "source license is required")
    total = sum(regular(path).st_size for path in selected)
    require(total <= LIMIT_SOURCE and len(selected) <= 10000, "source copy exceeds trial bounds")
    return sorted(selected)


def policies():
    """Observe administrative policy; never replace it with trial policy."""
    paths = [Path("/etc/botainer/policy.yaml")]
    override = os.environ.get("BOTAINER_SITE_POLICY")
    if override:
        candidate = Path(override)
        require(candidate.is_absolute(), "site policy override must be absolute")
        require(candidate.exists() and candidate.stat().st_uid == 0,
                "configured site policy override must exist and be root-owned")
        paths.insert(0, candidate)
    for path in paths:
        if path.exists():
            regular(path)
    return {str(path): (sha256(path) if path.exists() else None) for path in paths}, override


def safe_environment(root, descriptor):
    interpreter = Path(descriptor["interpreter"]["invocation"])
    username = pwd.getpwuid(os.getuid()).pw_name
    env = {"HOME": str(root), "PATH": f"{interpreter.parent}:/usr/bin:/bin",
           "LANG": "C.UTF-8", "TERM": "xterm-256color", "USER": username,
           "LOGNAME": username, "MY_BOTAINER": str(root / "state"),
           "BOTAINER_PROJECT_ROOT": str(root / "project"),
           "BOTAINER_PROJECT_UUID": descriptor["project_uuid"],
           "BOTAINER_PROFILE": "default", "BOTAINER_NO_TIPS": "1",
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}
    if descriptor.get("site_policy_override"):
        env["BOTAINER_SITE_POLICY"] = descriptor["site_policy_override"]
    return env


def immutable_inputs(root):
    """Select preparation inputs, excluding Botainer-owned evolving state.

    Composition updates project meta.json and creates session/output/masking
    files. Those belong in individual preflight/runtime receipts, not the
    preparation digest. Configuration, source and plugin registry remain pinned.
    """
    files = []
    for relative in ("source", "fixture", "state/plugins"):
        directory = root / relative
        require(directory.is_dir() and not directory.is_symlink(),
                f"missing immutable input directory: {relative}")
        for path in directory.rglob("*"):
            require(not path.is_symlink(), "immutable input symlink refused")
            if path.is_file():
                files.append(path)
    for relative in ("project/.botainer/config.yaml", "project/.botainer/project-id",
                     "state/policy.yaml", "descriptor.json", "prepare_cluster_trial.py",
                     "README-TEST.txt"):
        path = root / relative
        require(path.is_file() and not path.is_symlink(),
                f"missing immutable input file: {relative}")
        files.append(path)
    return sorted(files)


def prepare(args):
    root = fresh_root(args.trial_root, Path.home())
    source = canonical(args.source_root)
    image = canonical(args.image)
    image_stat = regular(image, 32 * 1024**3)
    require(image.suffix == ".sif" and image_stat.st_size > 0, "existing nonempty SIF required")
    require(re.fullmatch(r"[A-Za-z0-9._/~+-]+", str(image)) is not None,
            "image path is outside the restricted trial path alphabet")
    interpreter = Path(args.interpreter).expanduser()
    require(interpreter.is_absolute() and interpreter.is_file() and os.access(interpreter, os.X_OK),
            "existing absolute executable interpreter required")
    require(interpreter.parent.resolve() == interpreter.parent, "interpreter parent must be canonical")
    files = source_files(source)
    policy_records, override = policies()
    descriptor = {"schema": 1, "label": "Botainer dashboard disposable cluster TEST",
                  "trial_root": str(root), "source_root": str(source),
                  "source_copy": str(root / "source"), "state_root": str(root / "state"),
                  "project_root": str(root / "project"), "project_uuid": str(uuid.uuid4()),
                  "image": {"path": str(image), "sha256": sha256(image),
                            "size": image_stat.st_size},
                  "interpreter": {"invocation": str(interpreter), "canonical": str(interpreter.resolve()),
                                  "sha256": sha256(interpreter.resolve())},
                  "site_policies": policy_records, "site_policy_override": override,
                  "scheduler": {"partition": "day", "time_minutes": 5, "cpus": 1,
                                "memory_gb": 1, "gpus": 0},
                  "network": "host network; no network isolation claimed",
                  "credentials": "none; no user state or credentials copied"}
    root.mkdir(mode=0o700)
    original_hashes = {}
    for path in files:
        relative = path.relative_to(source)
        before = regular(path)
        data = path.read_bytes()
        after = path.lstat()
        require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns),
                "source changed while copying")
        new_file(root / "source" / relative, data, 0o700 if before.st_mode & 0o111 else 0o600)
        original_hashes[str(relative)] = hashlib.sha256(data).hexdigest()
    new_file(root / "source/plugins" / PLUGIN / "botainer-plugin.yaml", MANIFEST)
    new_file(root / "fixture/terminal.sh", FIXTURE)
    new_file(root / "README-TEST.txt", "Disposable Botainer dashboard cluster TEST. No real agent, credentials or user project.\nHost networking is available. No automatic submission or software installation.\n")
    new_file(root / "project/.botainer/project-id", descriptor["project_uuid"] + "\n")
    # JSON is valid YAML, preserving paths as data without string interpolation.
    config = {"version": "config-v1", "agent": "", "agent_permissions": "prompt",
              "runtime": "apptainer", "image": str(image), "network": {"mode": "internet"},
              "plugins_enabled": [PLUGIN, "nudge"], "inject_credentials": [], "job_profiles": {},
              "plugins": {"hpc-launcher": descriptor["scheduler"]},
              "mounts": {"extra": [{"source": str(root / "fixture"),
                                     "target": "/mnt/dashboard-fixture", "mode": "ro",
                                     "reason": "Deterministic dashboard cluster test only"}]}}
    new_json(root / "project/.botainer/config.yaml", config)
    new_file(root / "state/policy.yaml", "version: policy-v1\nnetwork:\n  default_mode: internet\n")
    helper_source = globals().get("__dashboard_trial_source__")
    if helper_source is None:
        helper_source = Path(__file__).read_bytes()
    require(isinstance(helper_source, bytes) and len(helper_source) <= 262144,
            "invalid bootstrap helper source")
    new_file(root / "prepare_cluster_trial.py", helper_source)
    new_json(root / "descriptor.json", descriptor)
    # Only this fixed helper is run, under the named pre-existing interpreter.
    run_private(root, descriptor, "_register")
    protected = {str(path.relative_to(root)): sha256(path) for path in immutable_inputs(root)}
    receipt = {"status": "prepared only; no Slurm submission or runtime launch",
               "schema": 1, "trial_root": str(root), "project_uuid": descriptor["project_uuid"],
               "source_original_files_sha256": original_hashes,
               "protected_files_sha256": protected, "image": descriptor["image"],
               "interpreter": descriptor["interpreter"], "site_policies": policy_records}
    new_json(root / "prepared.json", receipt)
    return receipt


def run_private(root, descriptor, action):
    completed = subprocess.run([descriptor["interpreter"]["invocation"], "-I", "-B",
                                str(root / "prepare_cluster_trial.py"), action,
                                "--trial-root", str(root)], cwd=root / "project",
                               env=safe_environment(root, descriptor), capture_output=True,
                               text=True, timeout=120, check=False)
    require(completed.returncode == 0, f"private {action} failed: {completed.stderr[-16000:]}")
    require(len(completed.stdout) <= LIMIT_SOURCE and len(completed.stderr) <= LIMIT_SOURCE,
            "private command exceeded output bound")
    if completed.stderr:
        sys.stderr.write(completed.stderr)
    return json.loads(completed.stdout)


def verify(root, descriptor):
    require(str(root) == descriptor["trial_root"], "trial descriptor root mismatch")
    require(root.stat().st_uid == os.getuid() and root.stat().st_mode & 0o077 == 0,
            "trial root must be private and owned by this account")
    receipt = read_json(root / "prepared.json")
    require(set(receipt["protected_files_sha256"])
            == {str(path.relative_to(root)) for path in immutable_inputs(root)},
            "immutable input file set changed")
    for relative, expected in receipt["protected_files_sha256"].items():
        path = root / relative
        require(not Path(relative).is_absolute() and ".." not in Path(relative).parts,
                "invalid protected path")
        require(path.resolve().is_relative_to(root) and not path.is_symlink() and sha256(path) == expected,
                f"prepared artifact changed: {relative}")
    executable = descriptor["interpreter"]
    require(str(Path(executable["invocation"]).resolve()) == executable["canonical"]
            and sha256(Path(executable["canonical"])) == executable["sha256"], "interpreter changed")
    image = descriptor["image"]
    path = canonical(image["path"])
    require(regular(path, 32 * 1024**3).st_size == image["size"] and sha256(path) == image["sha256"],
            "selected image changed")
    for name, digest in descriptor["site_policies"].items():
        require((sha256(Path(name)) if Path(name).exists() else None) == digest, "administrative policy changed")


def select_copy(root, descriptor):
    require(sys.version_info >= (3, 10), "Botainer requires existing Python >=3.10")
    require(Path(sys.executable) == Path(descriptor["interpreter"]["invocation"]),
            "private operation must use the selected interpreter invocation")
    os.environ.clear()
    os.environ.update(safe_environment(root, descriptor))
    sys.path.insert(0, str(root / "source"))
    import botainer
    require(Path(botainer.__file__).resolve().is_relative_to(root / "source/botainer"),
            "Botainer import escaped the isolated source")


def register(root, descriptor):
    select_copy(root, descriptor)
    from botainer.core import config, identity, policy
    from botainer.plugins import builtin
    from botainer.state import dir as state_dir
    # Validate policy before the bundled installer (which has a broad fallback).
    effective = policy.intersect(policy.load_site_policy(), policy.load_user_policy())
    require("first-party" in effective.plugins.allowed_tiers, "site denies first-party test plugins")
    state_dir.ensure_user_state_dir(create_if_missing=True)
    for name in (PLUGIN, "nudge", "hpc-launcher"):
        builtin.install_bundled(root / "source/plugins" / name)
    config.load_config(root / "project")
    identity.resolve_identity(root / "project", identity_accept=False)
    return {"registered": [PLUGIN, "nudge", "hpc-launcher"], "launched": False}


def validate_spec(spec, argv, root, descriptor):
    require(spec.runtime == "apptainer" and spec.image == descriptor["image"]["path"],
            "composition changed runtime/image")
    require(spec.project_uuid == descriptor["project_uuid"] and spec.project_root == str(root / "project"),
            "composition changed project identity")
    require(set(spec.plugins_enabled) == {PLUGIN, "nudge"}, "unexpected enabled plugin")
    require(not spec.hooks and not spec.sidecars and not spec.port_forwards and not spec.env_files,
            "fixture must have no hooks, sidecars, ports or environment files")
    require(spec.network.mode.value == "internet", "fixture must disclose host networking")
    require(list(argv[:2]) == ["apptainer", "exec"], "unexpected runtime argv")
    for flag in ("--containall", "--cleanenv", "--no-privs", "--drop-caps"):
        require(flag in argv, f"missing runtime guard: {flag}")
    require(argv[argv.index("--drop-caps") + 1] == "all", "capabilities were not dropped")
    require(argv[argv.index(spec.image) + 1:] == ["/bin/sh", "/mnt/dashboard-fixture/terminal.sh"],
            "unexpected fixture command")
    project_state = root / "state/state" / descriptor["project_uuid"]
    allowed_ro = {Path(path).resolve() for path, digest in descriptor["site_policies"].items() if digest}
    binds = []
    for bind in spec.mount_plan.binds:
        source = Path(bind.source).resolve(strict=True)
        allowed = (source == root / "project" or source.is_relative_to(root / "project")
                   or source == project_state or source.is_relative_to(project_state)
                   or source == root / "fixture")
        require(allowed or (source in allowed_ro and bind.mode.value == "ro"),
                f"composed bind escaped the isolated project/state: {source}")
        require(source != project_state, "whole project state must not be container-mounted")
        if source == root / "fixture":
            require(bind.mode.value == "ro", "fixture must be read-only")
        require(bind.mode.value in ("ro", "rw", "null-bind"), "unexpected special bind")
        if bind.mode.value == "null-bind":
            require(source == project_state / "data/null-bind-anchor" and bind.target == "/workspace/.botainer",
                    "unexpected masking bind")
        binds.append({"source": str(source), "target": bind.target, "mode": bind.mode.value})
    require(dict(spec.env.values) == {"HOME": "/home/user", "BOTAINER_AGENT_PERMISSIONS": "prompt"},
            "composed environment differs from the two fixed credential-free fixture values")
    return binds


def compose(root, descriptor):
    verify(root, descriptor)
    select_copy(root, descriptor)
    from botainer.core import composition
    sys.path.insert(0, str(root / "source/plugins/hpc-launcher/host_helper"))
    from _common import make_plan
    project = root / "project"
    spec, argv = composition.compose_agent_exec_for_hpc(project, image_override=descriptor["image"]["path"])
    binds = validate_spec(spec, argv, root, descriptor)
    plan = make_plan(project)
    require(not plan.agent_name and plan.nudge_enabled, "not a credential-free screen fixture")
    for name, expected in descriptor["scheduler"].items():
        require(getattr(plan, name) == expected, f"scheduler setting changed: {name}")
    plan = plan.__class__(**{**plan.__dict__, "agent_exec_argv": tuple(argv),
                           "session_id": spec.session_id,
                           "session_dir": str(Path(spec.state_dir) / "sessions" / spec.session_id)})
    # Creates only isolated host-side output/mount directories; no Slurm call.
    plan.prepare_host_paths()
    evidence = root / "preflights" / spec.session_id
    evidence.mkdir(parents=True, mode=0o700)
    new_json(evidence / "spec.json", spec.model_dump(mode="json"))
    new_json(evidence / "plan.json", {key: str(value) if isinstance(value, Path) else value
                                     for key, value in dataclasses.asdict(plan).items()})
    new_json(evidence / "argv.json", argv)
    new_file(evidence / "unsubmitted.sbatch", plan.render_sbatch_script())
    result = {"status": "composed into private state only; no submission or runtime launch",
              "session_id": spec.session_id, "project_uuid": descriptor["project_uuid"],
              "evidence_directory": str(evidence), "session_directory": plan.session_dir,
              "script_path": str(evidence / "unsubmitted.sbatch"),
              "plan_path": str(evidence / "plan.json"), "spec_path": str(evidence / "spec.json"),
              "binds": binds, "environment": dict(spec.env.values),
              "argv": argv, "scheduler": descriptor["scheduler"],
              "limitations": ["Host networking is available; fixture makes no network requests.",
                              "Compute screen/runtime/image/mount readiness remains untested.",
                              "Upstream sbatch script falls back to direct execution if screen is missing; review before use.",
                              "Use a separately reviewed strict existing-owner attachment; do not call generic hpc attach.",
                              "No AI agent/helper lifetime, GUI, SSH, Slurm or allocation acceptance is established."],
              "artifacts_sha256": {path.name: sha256(path) for path in evidence.iterdir()}}
    new_json(evidence / "receipt.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    prepare_parser = sub.add_parser("prepare", help="copy local source and register isolated fixture")
    for flag in ("trial-root", "source-root", "image", "interpreter"):
        prepare_parser.add_argument("--" + flag, required=True)
    for name in ("preflight", "_register", "_compose"):
        sub.add_parser(name).add_argument("--trial-root", required=True)
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            result = prepare(args)
        else:
            root = canonical(args.trial_root)
            descriptor = read_json(root / "descriptor.json")
            require(str(root) == descriptor["trial_root"], "trial root mismatch")
            if args.action == "_register":
                require(not (root / "prepared.json").exists(), "registration is one-shot")
                result = register(root, descriptor)
            elif args.action == "_compose":
                result = compose(root, descriptor)
            else:
                verify(root, descriptor)
                result = run_private(root, descriptor, "_compose")
        print(json.dumps(result, sort_keys=True))
    except (TrialError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"cluster trial refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
