#!/usr/bin/env python3
"""Inspect an existing installation and optionally save a private candidate.

No installation, activation, Botainer invocation, hook approval or SSH config
edit occurs. Use --help for the required fields and separate review boundary.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from botainer_dashboard.connection_prepare import prepare_connection


def _write_candidate(destination, profile):
    path = Path(destination).absolute()
    parent = path.parent
    if parent.resolve(strict=True) != parent:
        raise ValueError("The output parent must be a real directory, not a symlink.")
    info = parent.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("Use an existing private output directory owned by this account with mode 700.")
    raw = (json.dumps(profile, indent=2, sort_keys=True) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def main(arguments=None, *, runner=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", required=True, choices=("local", "remote"))
    parser.add_argument("--id", required=True, help="Stable connection ID")
    parser.add_argument("--label", required=True, help="Name shown in the dashboard")
    parser.add_argument("--ssh-alias", help="Existing SSH alias; no passwords or SSH flags")
    parser.add_argument("--python", help="Absolute existing interpreter path on the selected machine")
    parser.add_argument("--source-root", help="Botainer import root containing botainer/: a source checkout or the selected Python's installed package directory")
    parser.add_argument("--state-root", help="Existing MY_BOTAINER root (default: login home/.botainer)")
    parser.add_argument("--launcher", help="Absolute existing Botainer script; aliases/functions are not accepted")
    parser.add_argument("--project-root", action="append", required=True, help="Existing allowed parent project folder; repeat as needed")
    parser.add_argument("--control-root", required=True, help="Existing private mode-700 dashboard control directory")
    parser.add_argument("--docker-path", help="Existing local Docker executable")
    parser.add_argument("--docker-host", help="Explicit local unix:/// Docker socket")
    parser.add_argument("--tmux-path", help="Existing local tmux executable")
    parser.add_argument("--home", help="Local login home override")
    parser.add_argument("--inspect", action="store_true", required=True,
        help="Acknowledge executing the selected Python read/hash probe and optional Docker info or chosen SSH connection")
    parser.add_argument("--output", help="Write only the candidate profile to a NEW file in an existing private directory; never activates it")
    args = vars(parser.parse_args(arguments))
    output = args.pop("output")
    args["read_only_acknowledged"] = args.pop("inspect")
    args["project_roots"] = args.pop("project_root")
    result = prepare_connection(args, runner=runner)
    if output and result["profile"] is not None:
        try:
            _write_candidate(output, result["profile"])
            result["candidate_written"] = True
        except (OSError, ValueError) as error:
            result["candidate_written"] = False
            result["checks"].append({"name": "Candidate output", "status": "failed",
                "message": str(error) if type(error) is ValueError else "Could not create a new private candidate file; existing files are never replaced."})
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["profile"] is not None and result.get("candidate_written", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
