#!/usr/bin/env python3
"""Operator-only lifecycle proof for one prepared, disposable cluster fixture.

This is not a production remote backend. It submits Botainer's reviewed composed
script once, with a test label and a strict screen prerequisite. It never calls
the current-config-dependent ``hpc attach`` or launches a replacement owner.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import sys


class Refused(RuntimeError):
    pass


def require(condition, reason):
    if not condition:
        raise Refused(reason)


def load(root):
    root = Path(root)
    require(root.is_absolute() and root.resolve(strict=True) == root
            and root.name.startswith("botainer-dashboard-test-"), "invalid trial root")
    require(root.stat().st_uid == os.getuid() and root.stat().st_mode & 0o077 == 0,
            "trial root must be private and owned by this account")
    prepared = json.loads((root / "prepared.json").read_text())
    helper = root / "prepare_cluster_trial.py"
    require(not helper.is_symlink() and hashlib.sha256(helper.read_bytes()).hexdigest()
            == prepared["protected_files_sha256"][helper.name], "preparation helper changed")
    spec = importlib.util.spec_from_file_location("trial_preparation", helper)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    descriptor = module.read_json(root / "descriptor.json")
    require(descriptor["trial_root"] == str(root), "descriptor target mismatch")
    return root, module, descriptor


def tool_paths():
    result = {}
    for name in ("sbatch", "scontrol", "srun", "scancel"):
        found = shutil.which(name)
        require(found is not None, f"existing {name} required")
        result[name] = str(Path(found).resolve(strict=True))
    return result


def env_for(root, helper, descriptor, tools):
    env = helper.safe_environment(root, descriptor)
    env["PATH"] = str(Path(tools["sbatch"]).parent) + ":/usr/bin:/bin"
    env["SCREENDIR"] = str(root / "scr")
    return env


def run(argv, env, *, timeout=20):
    result = subprocess.run(argv, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=timeout, check=False)
    require(len(result.stdout) <= 256 * 1024 and len(result.stderr) <= 65536,
            "command output exceeded proof bounds")
    require(result.returncode == 0,
            f"{Path(argv[0]).name} failed: {result.stderr.decode(errors='replace')[-2000:]}")
    return result.stdout.decode()


def parse_job(text):
    fields = dict(re.findall(r"(?:^|\s)([A-Za-z][A-Za-z0-9_]*)=(\S*)", text))
    require(fields.get("JobId", "").isdigit(), "scheduler returned no exact job")
    return fields


def job_identity(fields):
    return {name: fields.get(name) for name in ("JobId", "UserId", "JobName", "WorkDir", "SubmitTime")}


def observed_job(root, helper, descriptor, runtime):
    tools = runtime["tools"]
    for name, path in tools.items():
        require(helper.sha256(path) == runtime["tool_sha256"][name], "scheduler executable changed")
    env = env_for(root, helper, descriptor, tools)
    fields = parse_job(run([tools["scontrol"], "--oneliner", "show", "job", runtime["job_id"]], env))
    require(fields["JobId"] == runtime["job_id"] and fields.get("JobName") == runtime["job_name"]
            and fields.get("WorkDir") == str(root / "project")
            and fields.get("UserId", "").endswith(f"({os.getuid()})"), "scheduler target mismatch")
    pinned = root / "scheduler-identity.json"
    if pinned.exists():
        require(helper.read_json(pinned) == job_identity(fields), "scheduler job identity changed")
    else:
        helper.new_json(pinned, job_identity(fields))
    return fields, env


def submit(root, helper, descriptor):
    require(not (root / "submission-intent.json").exists(),
            "dispatch already claimed; reconcile it, never retry automatically")
    helper.verify(root, descriptor)
    receipts = sorted((root / "preflights").glob("*/receipt.json"))
    require(len(receipts) == 1, "exactly one reviewed successful preflight is required")
    preflight = helper.read_json(receipts[0])
    evidence = receipts[0].parent
    sid = preflight["session_id"]
    require(isinstance(sid, str) and re.fullmatch(r"[0-9a-f]{16}", sid), "invalid session identity")
    expected_session = root / "state/state" / descriptor["project_uuid"] / "sessions" / sid
    require(preflight["project_uuid"] == descriptor["project_uuid"]
            and preflight["session_directory"] == str(expected_session)
            and expected_session.resolve(strict=True) == expected_session
            and evidence == root / "preflights" / sid
            and evidence.resolve(strict=True) == evidence, "preflight target mismatch")
    require(set(preflight["artifacts_sha256"]) == {
        "spec.json", "plan.json", "argv.json", "unsubmitted.sbatch"
    }, "preflight artifact set changed")
    for name, digest in preflight["artifacts_sha256"].items():
        require(Path(name).name == name and not (evidence / name).is_symlink()
                and helper.sha256(evidence / name) == digest, "preflight artifact changed")
    require(preflight["scheduler"] == {"partition": "day", "time_minutes": 5,
            "cpus": 1, "memory_gb": 1, "gpus": 0}, "unapproved resource envelope")
    name = "botainer-dashboard-test-" + sid
    original = (evidence / "unsubmitted.sbatch").read_text()
    label = "#SBATCH --job-name=botainer-" + descriptor["project_uuid"][:8]
    require(original.count(label + "\n") == 1 and original.count("set -euo pipefail\n") == 1,
            "unrecognized upstream batch script")
    # A missing screen must fail this terminal proof, not select the upstream
    # direct-process fallback. The original rendered artifact stays preserved.
    script = original.replace(label + "\n", "#SBATCH --job-name=" + name + "\n")
    script = script.replace("set -euo pipefail\n", "set -euo pipefail\n"
                            "test -x /usr/bin/screen || exit 78\n"
                            "test -x /usr/bin/apptainer || exit 78\n"
                            "test -x /usr/bin/python3 || exit 78\n"
                            "test \"$(command -v screen)\" = /usr/bin/screen || exit 78\n"
                            "test \"$(command -v apptainer)\" = /usr/bin/apptainer || exit 78\n")
    tools = tool_paths()
    screen_dir = root / "scr"
    require(len(str(screen_dir).encode()) + len('/9999999.botainer-9999999999') < 108,
            "trial path is too long for a private screen socket")
    screen_dir.mkdir(mode=0o700)
    helper.new_file(root / "submitted-test.sbatch", script, 0o700)
    source = globals().get("__dashboard_trial_source__")
    if source is None:
        source = Path(__file__).read_bytes()
    helper.new_file(root / "cluster_runtime_probe.py", source)
    runtime = {"schema": 1, "trial_root": str(root), "job_name": name,
               "project_uuid": descriptor["project_uuid"], "session_id": sid,
               "session_directory": preflight["session_directory"], "tools": tools,
               "tool_sha256": {key: helper.sha256(value) for key, value in tools.items()},
               "script_sha256": helper.sha256(root / "submitted-test.sbatch"),
               "helper_sha256": helper.sha256(root / "cluster_runtime_probe.py"),
               "route": "Botainer-composed script; operator proof dispatcher, not full CLI submission"}
    helper.new_json(root / "submission-intent.json", runtime)
    # Persist the one-shot claim before sbatch can accept anything. A lost
    # acknowledgement leaves this claim in place for explicit reconciliation.
    with (root / "submission-intent.json").open("rb") as claim:
        os.fsync(claim.fileno())
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    env = env_for(root, helper, descriptor, tools)
    output = run([tools["sbatch"], "--parsable", "--nodes=1", "--ntasks=1",
                  "--chdir=" + str(root / "project"), str(root / "submitted-test.sbatch")], env)
    require(re.fullmatch(r"[0-9]+(?:;[A-Za-z0-9_.-]+)?\s*", output),
            "submission outcome is unknown; inspect scheduler before any retry")
    runtime["job_id"] = output.strip().split(";")[0]
    helper.new_json(root / "runtime.json", runtime)
    # Reflect the accepted job in this fixture's Botainer session record only.
    helper.select_copy(root, descriptor)
    from botainer.state import session_record
    session_record.update_runtime(Path(runtime["session_directory"]),
                                  slurm_jobid=runtime["job_id"],
                                  screen_session_id="botainer-" + runtime["job_id"])
    return runtime


NODE_PROBE = r'''
import hashlib,json,os,pathlib,re,socket,stat,sys
directory=pathlib.Path(sys.argv[1]); owner=sys.argv[2]
matches=[p for p in directory.iterdir() if re.fullmatch(r"[0-9]+\."+re.escape(owner),p.name)]
if len(matches)!=1: raise SystemExit("original screen owner is absent or ambiguous")
path=matches[0]; info=path.lstat(); pid=int(path.name.split(".",1)[0])
if not stat.S_ISSOCK(info.st_mode) or info.st_uid!=os.getuid(): raise SystemExit("wrong screen socket owner")
proc=pathlib.Path('/proc')/str(pid)
if proc.stat().st_uid!=os.getuid(): raise SystemExit("wrong screen process owner")
cmd=(proc/'cmdline').read_bytes().split(b'\0')
if owner.encode() not in cmd: raise SystemExit("screen process command identity changed")
ticks=(proc/'stat').read_text().rsplit(')',1)[1].split()[19]
print(json.dumps({'node':socket.gethostname(),'screen':path.name,'pid':pid,'start_ticks':ticks,
 'uid':os.getuid(),'boot_id':pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
 'screen_sha256':hashlib.sha256(pathlib.Path('/usr/bin/screen').resolve(strict=True).read_bytes()).hexdigest()}))
'''


def get_owner(root, helper, descriptor, runtime):
    fields, env = observed_job(root, helper, descriptor, runtime)
    require(fields.get("JobState") == "RUNNING" and fields.get("NumNodes") == "1",
            "test allocation is not running on exactly one node")
    node = fields.get("BatchHost", "")
    require(re.fullmatch(r"[A-Za-z0-9_.-]+", node), "invalid compute node")
    argv = [runtime["tools"]["srun"], "--jobid=" + runtime["job_id"], "--overlap", "--exact",
            "--nodes=1", "--ntasks=1", "--cpus-per-task=1", "--nodelist=" + node]
    owner = json.loads(run([*argv, "/usr/bin/python3", "-I", "-S", "-c", NODE_PROBE,
                           str(root / "scr"), "botainer-" + runtime["job_id"]], env, timeout=35))
    require(owner["node"].split(".")[0] == node.split(".")[0], "compute node mismatch")
    owner["job_start_time"] = fields.get("StartTime")
    saved = root / "owner-identity.json"
    if saved.exists():
        require(helper.read_json(saved) == owner, "original owner identity changed")
    else:
        helper.new_json(saved, owner)
    return owner, argv, env


def action(root, helper, descriptor, name):
    runtime = helper.read_json(root / "runtime.json")
    require(runtime["trial_root"] == str(root) and runtime["project_uuid"] == descriptor["project_uuid"],
            "runtime target mismatch")
    require(helper.sha256(root / "cluster_runtime_probe.py") == runtime["helper_sha256"],
            "runtime helper changed")
    current_source = globals().get("__dashboard_trial_source__")
    if current_source is None:
        current_source = Path(__file__).read_bytes()
    require(hashlib.sha256(current_source).hexdigest() == runtime["helper_sha256"],
            "executing helper differs from the dispatched revision")
    if name in {"owner", "attach"}:
        owner, argv, env = get_owner(root, helper, descriptor, runtime)
        if name == "attach":
            require(os.isatty(0), "attachment needs an SSH PTY")
            argv += ["--pty", "/usr/bin/env", "HOME=" + str(root),
                     "SCREENDIR=" + str(root / "scr"), "TERM=xterm-256color",
                     "/usr/bin/screen", "-r", owner["screen"]]
            os.execve(argv[0], argv, env)
        return owner
    fields, env = observed_job(root, helper, descriptor, runtime)
    if name == "stop":
        if fields["JobState"] not in {"COMPLETED", "CANCELLED", "FAILED", "TIMEOUT", "OUT_OF_MEMORY"}:
            run([runtime["tools"]["scancel"], "--user=" + pwd.getpwuid(os.getuid()).pw_name,
                 "--", runtime["job_id"]], env)
        return {"job_id": runtime["job_id"], "cancellation_requested": True,
                "termination_confirmed": False}
    return {key: fields.get(key) for key in ("JobId", "JobName", "JobState", "BatchHost",
                                           "SubmitTime", "StartTime", "EndTime", "ExitCode")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("submit", "status", "owner", "attach", "stop"))
    parser.add_argument("--trial-root", required=True)
    args = parser.parse_args()
    try:
        root, helper, descriptor = load(args.trial_root)
        if args.action == "submit":
            with contextlib.redirect_stdout(sys.stderr):
                result = submit(root, helper, descriptor)
        else:
            result = action(root, helper, descriptor, args.action)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (Refused, OSError, ValueError, subprocess.SubprocessError, RuntimeError) as exc:
        print(f"cluster runtime proof refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
