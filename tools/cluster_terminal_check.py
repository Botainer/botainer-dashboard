#!/usr/bin/env python3
"""Exercise only the prepared cluster fixture's fixed protocol through two PTYs.

No submission, cancellation, image pull or generic remote shell. The installed
remote helper verifies the exact original owner before each resume-only attach.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from botainer_dashboard.pty_bridge import PtyAttachment
from cluster_trial import load_profile, child_environment, SSH_OPTIONS, write_new, write_json

IDENTITY = re.compile(rb"IDENTITY token=([0-9a-f-]{36}) pid=([0-9]+) start=([0-9]+) cwd=/workspace")
TERMINAL_ESCAPES = re.compile(rb"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]")


def attach_argv(profile):
    remote = [profile["remote_python"], "-I", "-B",
              profile["trial_root"] + "/cluster_runtime_probe.py", "attach",
              "--trial-root", profile["trial_root"]]
    argv = ["/usr/bin/ssh", "-tt"]
    for option in SSH_OPTIONS:
        if not option.startswith("RequestTTY="):
            argv.extend(["-o", option])
    argv.extend([profile["ssh_alias"], shlex.join(remote)])
    return argv


def read_until(client, pattern, transcript, *, timeout=30):
    buffer = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = client.read(65536, timeout=0.25)
        if data == b"":
            raise RuntimeError("attachment ended before expected fixture output")
        if data:
            transcript.extend(data)
            buffer.extend(data)
            if len(transcript) > 512 * 1024:
                raise RuntimeError("fixture output exceeded bound")
            # screen can insert redraw/title controls between a protocol label
            # and its value during SIGWINCH; keep raw evidence but match text.
            match = re.search(pattern, TERMINAL_ESCAPES.sub(b"", buffer))
            if match:
                return match
    raise RuntimeError("fixture output deadline exceeded")


def send(client, text):
    data = text.encode("ascii")
    deadline = time.monotonic() + 3
    while data and time.monotonic() < deadline:
        data = data[client.write(data):]
        if data:
            time.sleep(0.02)
    if data:
        raise RuntimeError("fixture input deadline exceeded")


def detach(client, transcript):
    """Request Screen's ordinary detach and require the SSH channel to finish."""
    send(client, "\x01d")
    read_until(client, rb"\[detached from [0-9]+\.botainer-[0-9]+\]", transcript, timeout=10)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        data = client.read(65536, timeout=0.25)
        if data == b"":
            return
        if data:
            transcript.extend(data)
            if len(transcript) > 512 * 1024:
                raise RuntimeError("fixture output exceeded bound")
    raise RuntimeError("detached display did not release its SSH channel")


def exercise(profile, receipt):
    argv = attach_argv(profile)
    env = child_environment() | {"TERM": "xterm-256color"}
    identities = []
    for index in range(2):
        transcript = bytearray()
        client = PtyAttachment(argv, cwd=ROOT, env=env, cols=100, rows=30)
        try:
            # screen replays the fixture's screen, including its READY marker.
            read_until(client, rb"READY", transcript)
            # Require a fresh response before inspecting identity. Old Screen
            # redraws must never count as evidence of a live original process.
            challenge = "barrier-" + uuid.uuid4().hex
            send(client, "echo " + challenge + "\n")
            read_until(client, ("ECHO " + challenge).encode("ascii"), transcript)
            send(client, "identity\n")
            match = read_until(client, IDENTITY, transcript)
            identities.append([value.decode() for value in match.groups()])
            send(client, "echo hello\n")
            read_until(client, rb"ECHO hello", transcript)
            client.resize(120, 37)
            time.sleep(0.2)
            send(client, "size\n")
            read_until(client, rb"SIZE\s+37\s+120", transcript)
            detach(client, transcript)
        finally:
            # This check covers normal detach, not abrupt client loss.
            # Never take over an attached viewer.
            client.close()
            write_new(receipt / f"terminal-{index + 1}.bin", bytes(transcript))
        if index == 0:
            time.sleep(1)
    if identities[0] != identities[1]:
        raise RuntimeError("reattachment did not preserve the original fixture process")
    return {"same_process": True, "identity": identities[0], "attachments": 2,
            "echo_hello": True, "resize_rows_cols": [37, 120],
            "normal_detach_reconnect": True,
            "abrupt_client_loss": "unqualified; viewer cleanup after abrupt client loss requires verification",
            "scope": "actual SSH/Slurm/Apptainer/screen PTY; no browser or real AI agent claim"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    args = parser.parse_args()
    profile, _raw, parent = load_profile(args.profile)
    receipt = parent / ("terminal-check-" + uuid.uuid4().hex)
    receipt.mkdir(mode=0o700)
    write_json(receipt / "intent.json", {"argv": attach_argv(profile),
                                        "commands": ["echo unique-barrier", "identity", "echo hello", "size", "Ctrl-a d"],
                                        "attachments": 2, "starts_or_stops_jobs": False})
    try:
        result = exercise(profile, receipt)
    except Exception as exc:
        result = {"passed": False, "error": str(exc), "job_state": "not inferred; inspect exact test allocation"}
        write_json(receipt / "result.json", result)
        print(json.dumps(result | {"receipt": str(receipt)}))
        return 1
    write_json(receipt / "result.json", result)
    print(json.dumps(result | {"passed": True, "receipt": str(receipt)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
