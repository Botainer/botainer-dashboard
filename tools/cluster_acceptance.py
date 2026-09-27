#!/usr/bin/env python3
"""One explicit isolated trial: prepare, compose, submit once, test, exact cleanup.

Operator use only after site/test authorization and source review. Fixed resource
limits and identity checks remain in the remote helpers. No new dependencies.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
import uuid

from cluster_trial import invoke, load_profile, write_json, write_new
from cluster_terminal_check import exercise

ENDED = {"COMPLETED", "CANCELLED", "FAILED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "PREEMPTED"}


def step(profile_path, action, evidence):
    result, receipt = invoke(profile_path, action)
    evidence.append({"action": action, "receipt": str(receipt), "transport": result})
    print(json.dumps({"stage": action, **result}), flush=True)
    if result["transport_status"] != "complete" or result["returncode"] != 0:
        raise RuntimeError(f"{action} did not succeed; inspect its private receipt; no retry performed")
    return json.loads((receipt / "stdout.bin").read_text())


def run_trial(profile_path):
    profile, raw, parent = load_profile(profile_path)
    receipt = parent / ("acceptance-" + uuid.uuid4().hex)
    receipt.mkdir(mode=0o700)
    # All stages, including cancellation, must retain the initially selected
    # target even if the operator changes their editable profile while we run.
    profile_path = receipt / "profile.json"
    write_new(profile_path, raw)
    evidence = []
    outcome = {"passed": False, "termination_confirmed": False}
    claimed = False
    try:
        step(profile_path, "prepare", evidence)
        step(profile_path, "preflight", evidence)
        claimed = True  # A failed submit reply can still mean a live job.
        runtime = step(profile_path, "submit", evidence)
        outcome["job_id"] = runtime["job_id"]
        deadline = time.monotonic() + 150
        while True:
            status = step(profile_path, "status", evidence)
            outcome["last_status"] = status
            if status["JobState"] == "RUNNING":
                break
            if status["JobState"] in ENDED or time.monotonic() >= deadline:
                raise RuntimeError("test allocation did not become running within the proof window")
            time.sleep(5)
        outcome["terminal"] = exercise(profile, receipt)
        outcome["passed"] = True
    except Exception as exc:
        outcome["error"] = str(exc)
    finally:
        if claimed:
            try:
                step(profile_path, "stop", evidence)
                deadline = time.monotonic() + 45
                while True:
                    status = step(profile_path, "status", evidence)
                    outcome["last_status"] = status
                    if status["JobState"] in ENDED:
                        outcome["termination_confirmed"] = True
                        break
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(2)
            except Exception as exc:
                outcome["cleanup_error"] = str(exc)
        outcome["passed"] = outcome["passed"] and outcome["termination_confirmed"]
        write_json(receipt / "result.json", outcome | {"stages": evidence})
    return outcome, receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    args = parser.parse_args()
    result, receipt = run_trial(args.profile)
    print(json.dumps(result | {"receipt": str(receipt)}), flush=True)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
