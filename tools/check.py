#!/usr/bin/env python3
"""Run offline repository gates with existing Python and Node. Never installs."""

from pathlib import Path
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parent.parent


def check_documents():
    errors = []
    documents = [*ROOT.glob("*.md"), *ROOT.joinpath("docs").rglob("*.md")]
    for path in documents:
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), 1):
            if line != line.rstrip():
                errors.append(f"{path.relative_to(ROOT)}:{line_number}: trailing whitespace")
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
            target = target.strip().split(' "')[0].strip("<>")
            if not target or target.startswith(("#", "https:", "http:", "mailto:", "app:")):
                continue
            target_path = unquote(target.split("#")[0])
            if not (path.parent / target_path).exists():
                errors.append(f"{path.relative_to(ROOT)}: missing link {target}")
    for directory in ("provenance", "requirements"):
        for path in ROOT.joinpath(directory).glob("*.json"):
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError) as exc:
                errors.append(f"{path.relative_to(ROOT)}: invalid JSON: {exc}")
    if errors:
        raise RuntimeError("\n".join(errors))
    print(f"PASS document links/whitespace ({len(documents)} files) and manifest JSON", flush=True)


def git_whitespace_check(root, env):
    """Select only this checkout's Git gate, never an enclosing repository."""
    root = Path(root).resolve()
    marker = root / ".git"
    if not marker.exists() and not marker.is_symlink():
        print("SOURCE EXPORT: no local .git metadata; skipping Git patch checks. "
              "Source tests, dependency integrity and document checks remain required.", flush=True)
        return None
    if not (marker.is_dir() or marker.is_file()):
        raise RuntimeError("Local .git metadata is invalid; refusing an enclosing repository's patch check")
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("Existing Git is required to check this checkout; nothing was installed")
    # Caller-provided Git overrides must not redirect either the identity check
    # or the subsequent diff into another worktree or index.
    git_env = {key: value for key, value in env.items() if not key.startswith("GIT_")}
    result = subprocess.run([git, "-C", str(root), "rev-parse", "--show-toplevel"],
                            cwd=root, env=git_env, capture_output=True, text=True,
                            timeout=5, check=False)
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError("Local Git checkout identity could not be verified")
    if Path(result.stdout.strip()).resolve() != root:
        raise RuntimeError("Git resolved to a different checkout; refusing its patch check")
    return [git, "-C", str(root), "diff", "--check"], git_env


def private_test_command(root):
    suite = Path(root) / "private/tests/unit"
    if not suite.is_dir() or not any(suite.glob("test_*.py")):
        raise RuntimeError("--private requested, but private/tests/unit is absent or empty")
    return [sys.executable, "-B", "-m", "unittest", "discover", "-s", "private/tests/unit", "-v"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pty", action="store_true", help="Also run loopback HTTP/WebSocket and OS PTY integration checks with deterministic local children; needs the existing runtime and socket/device permission")
    parser.add_argument("--private", action="store_true", help="Also run private development tests; fails when the private suite is unavailable")
    args = parser.parse_args(argv)
    private_check = private_test_command(ROOT) if args.private else None
    node = shutil.which("node")
    if not node:
        raise SystemExit("Existing Node is required for offline JS checks; nothing was installed.")
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONDONTWRITEBYTECODE="1")
    git_check = git_whitespace_check(ROOT, env)
    checks = [
        ("Dependency pins and bundled assets", [sys.executable, "-I", "-B", "requirements/verify.py"]),
        ("Python foundation", [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests/unit", "-v"]),
        ("Frontend production", [node, "--test", *[str(path.relative_to(ROOT)) for path in sorted(ROOT.joinpath("tests/frontend").glob("*.test.mjs"))]]),
        ("Simulation model", [node, "--test", "tests/model.test.cjs"]),
        ("Generated prototype", [sys.executable, "-B", "tools/prototype.py", "--check"]),
    ]
    if private_check is not None:
        checks.append(("Private development tests", private_check))
    if args.pty:
        checks.append(("Local HTTP/WebSocket and OS PTY integration", [sys.executable, "-B", "-m", "unittest",
                                                  "discover", "-s", "tests/integration", "-v"]))
    for label, argv in checks:
        print(f"\nCHECK {label}", flush=True)
        subprocess.run(argv, cwd=ROOT, env=env, check=True)
    if git_check is not None:
        print("\nCHECK Patch whitespace", flush=True)
        git_argv, git_env = git_check
        subprocess.run(git_argv, cwd=ROOT, env=git_env, check=True)
    check_documents()
    print("\nPASS source gate. No install, external network, container, SSH, or real Botainer session test occurred.")


if __name__ == "__main__":
    main()
