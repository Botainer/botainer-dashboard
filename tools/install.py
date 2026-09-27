#!/usr/bin/env python3
"""Plan or explicitly apply a fresh, offline dashboard wheel installation.

Run with the existing Python intended for the environment. The default is a
read-only plan: no subprocesses, package imports, downloads or filesystem writes.
Apply requires the dashboard wheel SHA-256 printed by the plan. This installs
only the dashboard and the fourteen reviewed cached dependency wheels into a
new versioned directory; it never installs Botainer or starts the dashboard.
"""
from __future__ import annotations

import argparse
import base64
import csv
from email.parser import BytesParser
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import stat
import subprocess
import sys
import sysconfig
import zipfile


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0a1"
METADATA_PATH = "requirements/dashboard-macos-arm64-py313.metadata.json"
LOCK_PATH = "requirements/dashboard-macos-arm64-py313.candidate.txt"
MAX_ARTIFACT = 64 * 1024 * 1024
FORBIDDEN = {"private", ".local", ".git", "dev", "prototype", "tests", "__pycache__", "node_modules"}


class Refused(ValueError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Refused("Duplicate JSON key in installation input")
        result[key] = value
    return result


def json_value(data):
    return json.loads(data, object_pairs_hook=_unique)


def regular_bytes(path, *, limit=MAX_ARTIFACT):
    path = Path(path).absolute()
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise Refused(f"Symbolic links are not supported for installation inputs: {path}")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
            raise Refused("Installation input must be a bounded regular file")
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if len(data) > limit or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise Refused("Installation input changed while reading")
    return data


def safe_member(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise Refused("Invalid wheel member path")
    path = PurePosixPath(value)
    if (path.is_absolute() or str(path) != value or any(p in {".", ".."} or p.casefold() in FORBIDDEN for p in path.parts)
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise Refused("Unsafe wheel member path")
    return value


def target_check():
    if (sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 13)
            or sys.version_info[:3] < (3, 13, 15)
            or sys.platform != "darwin" or os.uname().machine != "arm64"
            or sysconfig.get_config_var("Py_GIL_DISABLED")):
        raise Refused("This offline selection requires normal CPython 3.13.15 or a later 3.13 patch on macOS arm64; 3.13.15 is the exercised interpreter. Other targets need separately reviewed wheels.")
    return {"implementation": "CPython", "python": ".".join(map(str, sys.version_info[:3])),
            "platform": "macOS arm64", "executable": sys.executable, "base_prefix": sys.base_prefix}


def bootstrap_input():
    """Identify the existing interpreter's bundled pip without importing it."""
    if sysconfig.get_config_var("WHEEL_PKG_DIR"):
        raise Refused("This installer supports Python's bundled ensurepip wheel only; an external system wheel directory needs separate review.")
    directory = Path(sysconfig.get_path("stdlib")) / "ensurepip"
    source = regular_bytes(directory / "__init__.py", limit=128 * 1024)
    versions = re.findall(r"(?m)^_PIP_VERSION\s*=\s*['\"]([0-9]+(?:\.[0-9]+)+(?:[a-z0-9.]*)?)['\"]\s*$", source.decode())
    if len(versions) != 1:
        raise Refused("Cannot identify this Python's bundled pip without executing it")
    version = versions[0]
    filename = f"pip-{version}-py3-none-any.whl"
    path = directory / "_bundled" / filename
    data = regular_bytes(path)
    wheel_metadata(data, filename, "pip", version)
    return {"name": "pip", "version": version, "filename": filename, "sha256": digest(data),
            "bytes": len(data), "source": str(path), "ensurepip_sha256": digest(source),
            "note": "Already bundled with the selected Python; copied into the new venv offline, with no pip update or download."}


def dependency_inputs(root):
    sums = {}
    for line in regular_bytes(root / "requirements/SHA256SUMS", limit=1024 * 1024).decode().splitlines():
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([a-f0-9]{64})  (.+)", line)
        if not match or match[2] in sums:
            raise Refused("Invalid dependency checksum inventory")
        sums[match[2]] = match[1]
    inputs = {}
    for name in (METADATA_PATH, LOCK_PATH, "requirements/manifest.json"):
        data = regular_bytes(root / name, limit=2 * 1024 * 1024)
        if sums.get(name) != digest(data):
            raise Refused(f"Reviewed dependency input changed: {name}")
        inputs[name] = data
    metadata, manifest = json_value(inputs[METADATA_PATH]), json_value(inputs["requirements/manifest.json"])
    target = metadata["target"]
    if (target["implementation"], target["python_abi"], target["platform"]) != ("CPython", "cp313", "macOS arm64"):
        raise Refused("Dependency metadata target differs from the supported route")
    packages = metadata["packages"]
    if not isinstance(packages, list) or len(packages) != 14:
        raise Refused("Expected the complete fourteen-package dependency selection")
    pins = {}
    for line in inputs[LOCK_PATH].decode().replace("\\\n", " ").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9._-]+)==([A-Za-z0-9.!+_-]+)\s+--hash=sha256:([0-9a-f]{64})", line.strip())
        if not match or match[1] in pins:
            raise Refused("Invalid exact dependency lock")
        pins[match[1]] = (match[2], match[3])
    expected = {item["name"]: (item["version"], item["wheel"]["digests"]["sha256"]) for item in packages}
    if len(expected) != 14 or expected != pins or metadata["direct_requirements"] != manifest["python"]["direct_requirements"]:
        raise Refused("Dependency lock, metadata and direct requirements disagree")
    for requirement in metadata["direct_requirements"]:
        name, version = requirement.split("==")
        if name not in pins or pins[name][0] != version:
            raise Refused("Direct dependency missing from exact lock")
    return packages, metadata["direct_requirements"], {name: digest(data) for name, data in inputs.items()}


def wheel_metadata(data, filename, name, version, *, dashboard=False, direct=()):
    if not re.fullmatch(r"[A-Za-z0-9_.+-]+\.whl", filename) or "/" in filename:
        raise Refused("Invalid wheel filename")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
        if not members or len(members) > 5000 or sum(item.file_size for item in members) > 128 * 1024 * 1024:
            raise Refused("Wheel member inventory exceeds limits")
        names = [item.filename for item in members]
        if len(names) != len({name.casefold() for name in names}):
            raise Refused("Wheel has duplicate members")
        for item in members:
            # Reviewed dependency wheels can legitimately contain a tests
            # directory. Enforce the stronger audience exclusion on our wheel.
            path = PurePosixPath(item.filename)
            if (path.is_absolute() or ".." in path.parts or "\\" in item.filename
                    or item.file_size > MAX_ARTIFACT or (item.external_attr >> 16) & 0o170000 == 0o120000):
                raise Refused("Unsafe wheel member")
            if dashboard:
                safe_member(item.filename)
        prefix = name.replace("-", "_") + "-" + version + ".dist-info/"
        metadata_files = [item for item in names if item.endswith(".dist-info/METADATA")]
        if metadata_files != [prefix + "METADATA"]:
            raise Refused("Wheel distribution identity is ambiguous")
        message = BytesParser().parsebytes(archive.read(prefix + "METADATA"))
        if (re.sub(r"[-_.]+", "-", message["Name"] or "").lower() != name
                or message["Version"] != version):
            raise Refused("Wheel name/version differs from reviewed selection")
        if not dashboard:
            return
        if filename != f"botainer_dashboard-{VERSION}-py3-none-any.whl":
            raise Refused("Unexpected dashboard wheel filename or compatibility tag")
        if (set((message["Requires-Python"] or "").split(",")) != {">=3.13.15", "<3.14"}
                or set(message.get_all("Requires-Dist", [])) != set(direct)):
            raise Refused("Dashboard Python/dependency metadata differs from reviewed selection")
        wheel = BytesParser().parsebytes(archive.read(prefix + "WHEEL"))
        if wheel["Root-Is-Purelib"] != "true" or wheel.get_all("Tag") != ["py3-none-any"]:
            raise Refused("Dashboard wheel must contain pure application code")
        marker = "botainer_dashboard/_resources/PACKAGE-MANIFEST.json"
        content = json_value(archive.read(marker))
        if (not isinstance(content, dict) or set(content) != {"schema_version", "distribution", "version", "files"}
                or type(content["schema_version"]) is not int or content["schema_version"] != 1
                or content["distribution"] != "botainer-dashboard" or content["version"] != VERSION
                or not isinstance(content["files"], list) or not 1 <= len(content["files"]) <= 1000):
            raise Refused("Dashboard content manifest is invalid")
        package_names = {marker}
        for entry in content["files"]:
            if not isinstance(entry, dict) or set(entry) != {"source", "path", "sha256"}:
                raise Refused("Dashboard content entry is invalid")
            safe_member(entry["source"])
            path = "botainer_dashboard/" + safe_member(entry["path"])
            if path in package_names or digest(archive.read(path)) != entry["sha256"]:
                raise Refused("Dashboard content inventory/hash is invalid")
            package_names.add(path)
        required = {"botainer_dashboard/" + name for name in ("__init__.py", "cli.py", "layout.py", "launcher.py",
                    "_resources/frontend/index.html", "_resources/frontend/app.js", "_resources/docs/getting-started.md",
                    "_resources/tools/run_dashboard.py", "_resources/tools/host_agent_helper.py")}
        if not required <= package_names or {item for item in names if item.startswith("botainer_dashboard/")} != package_names:
            raise Refused("Dashboard wheel lacks complete application resources")
        if any(not item.startswith(("botainer_dashboard/", prefix)) for item in names):
            raise Refused("Unexpected top-level content in dashboard wheel")
        if archive.read(prefix + "entry_points.txt").decode().strip() != "[console_scripts]\nbotainer-dashboard = botainer_dashboard.cli:main":
            raise Refused("Dashboard console entry point differs from selected command")
        rows = list(csv.reader(io.StringIO(archive.read(prefix + "RECORD").decode())))
        if len(rows) != len(names) or any(len(row) != 3 for row in rows) or {row[0] for row in rows} != set(names):
            raise Refused("Dashboard wheel RECORD inventory is invalid")
        for path, recorded_hash, size in rows:
            if path == prefix + "RECORD":
                if recorded_hash or size:
                    raise Refused("Dashboard RECORD self-entry is invalid")
                continue
            payload = archive.read(path)
            actual = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode()
            if recorded_hash != "sha256=" + actual or size != str(len(payload)):
                raise Refused("Dashboard wheel RECORD hash or size is invalid")


def new_destination(path):
    path = Path(path).expanduser().absolute()
    if any(ord(c) < 32 or ord(c) == 127 for c in str(path)):
        raise Refused("Destination path contains control characters")
    # Reject symlinks before lexical normalization: resolving first could hide
    # an alias into the running environment or another user's directory.
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise Refused("Destination needs an existing canonical parent directory")
    path = Path(os.path.abspath(path))
    if path.exists() or path.is_symlink():
        raise Refused("Destination already exists; choose a new versioned environment. In-place upgrades are unsupported.")
    if not path.parent.is_dir():
        raise Refused("Destination needs an existing canonical parent directory")
    if path.parent.stat().st_uid != os.getuid():
        raise Refused("Destination parent must belong to the current user")
    if path.parent.stat().st_mode & 0o022:
        raise Refused("Destination parent must not be writable by other users or groups")
    if VERSION not in path.name:
        raise Refused(f"Use a versioned destination name containing {VERSION}, such as botainer-dashboard-{VERSION}.")
    if path.is_relative_to(Path(sys.prefix).resolve()):
        raise Refused("Do not place the new environment inside the running Python environment")
    return path


def installation_plan(wheel, wheelhouse, destination, *, expected_sha256=None, root=ROOT):
    target = target_check()
    destination = new_destination(destination)
    bootstrap = bootstrap_input()
    packages, direct, inventory = dependency_inputs(Path(root))
    wheel = Path(wheel).absolute()
    wheelhouse = Path(wheelhouse).absolute()
    if any(item.is_symlink() for item in (wheelhouse, *wheelhouse.parents)) or not wheelhouse.is_dir():
        raise Refused("Wheel cache must be an existing canonical directory")
    data = regular_bytes(wheel)
    selected_sha = digest(data)
    if expected_sha256 is not None and (not re.fullmatch(r"[a-f0-9]{64}", expected_sha256) or expected_sha256 != selected_sha):
        raise Refused("Dashboard wheel differs from the explicitly selected SHA-256")
    wheel_metadata(data, wheel.name, "botainer-dashboard", VERSION, dashboard=True, direct=direct)
    artifacts = [{"name": "botainer-dashboard", "version": VERSION, "filename": wheel.name,
                  "sha256": selected_sha, "bytes": len(data), "source": str(wheel)}]
    buffered = {wheel.name: data}
    for item in packages:
        filename = item["wheel"]["filename"]
        if Path(filename).name != filename or not filename.endswith(".whl"):
            raise Refused("Invalid reviewed dependency filename")
        payload = regular_bytes(wheelhouse / filename)
        wanted = item["wheel"]["digests"]["sha256"]
        if digest(payload) != wanted or len(payload) != item["wheel"]["size"]:
            raise Refused(f"Cached wheel differs from reviewed bytes: {filename}")
        wheel_metadata(payload, filename, item["name"], item["version"])
        buffered[filename] = payload
        artifacts.append({"name": item["name"], "version": item["version"], "filename": filename,
                          "sha256": wanted, "bytes": len(payload), "source": str(wheelhouse / filename)})
    plan = {"schema_version": 1, "status": "plan-only", "target": target, "destination": str(destination),
            "dashboard_sha256": selected_sha, "artifacts": artifacts, "reviewed_inputs": inventory,
            "downloads": [], "writes": [str(destination) + "/ (new venv, bundled pip, verified wheels, installation receipt)"],
            "state": "No dashboard state is created or migrated; Botainer installations and running sessions are unchanged.",
            "bootstrap": bootstrap,
            "unused_cache_files": "Ignored; only the fifteen listed artifacts can be installed."}
    return plan, buffered


def apply_plan(plan, buffered):
    """Called only after explicit --apply and exact wheel selection; no updates."""
    destination = new_destination(plan["destination"])
    if bootstrap_input() != plan["bootstrap"]:
        raise Refused("Python's bundled pip changed after the installation plan; review a fresh plan")
    if set(buffered) != {item["filename"] for item in plan["artifacts"]}:
        raise Refused("Buffered wheel inventory differs from the installation plan")
    for item in plan["artifacts"]:
        if digest(buffered[item["filename"]]) != item["sha256"]:
            raise Refused("Buffered wheel differs from approved installation plan")
    destination.mkdir(mode=0o700)
    receipt_root = destination / ".dashboard-install"
    receipt_root.mkdir(mode=0o700)
    cache = receipt_root / "wheels"
    cache.mkdir(mode=0o700)
    for filename, payload in buffered.items():
        with (cache / filename).open("xb") as stream:
            stream.write(payload)
    lock = receipt_root / "requirements.txt"
    lock.write_text("".join(f"{item['name']}=={item['version']} --hash=sha256:{item['sha256']}\n" for item in plan["artifacts"]), encoding="utf-8")
    receipt = receipt_root / "receipt.json"
    def record(status):
        receipt.write_text(json.dumps(plan | {"status": status}, indent=2) + "\n", encoding="utf-8")
    record("installing")
    env = {key: value for key, value in os.environ.items() if not key.startswith(("PYTHON", "PIP_")) and key not in {"VIRTUAL_ENV", "CONDA_PREFIX"}}
    env.update(PIP_CONFIG_FILE=os.devnull, PIP_NO_INDEX="1", PIP_DISABLE_PIP_VERSION_CHECK="1", PYTHONDONTWRITEBYTECODE="1")
    python = destination / "bin/python"
    commands = [[sys.executable, "-I", "-B", "-m", "venv", "--without-scm-ignore-files", str(destination)],
                [str(python), "-I", "-B", "-m", "pip", "--isolated", "install", "--no-index", "--no-deps",
                 "--require-hashes", "--only-binary=:all:", "--no-cache-dir", "--no-compile",
                 "--find-links", str(cache), "--requirement", str(lock)]]
    try:
        for command in commands:
            subprocess.run(command, cwd=destination, env=env, check=True, timeout=180)
    except (OSError, subprocess.SubprocessError):
        record("failed-partial-environment-retained")
        raise Refused(f"Installation failed. Only the new directory was written; retained details: {receipt}") from None
    record("installed-not-started")
    return destination / "bin/botainer-dashboard"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True, help="Reviewed dashboard wheel (not source code)")
    parser.add_argument("--wheelhouse", type=Path, required=True, help="Existing cache containing the fourteen exact dependency wheels")
    parser.add_argument("--destination", type=Path, required=True, help="New versioned environment under an existing user-owned directory")
    parser.add_argument("--wheel-sha256", help="Expected dashboard wheel SHA-256; required with --apply")
    parser.add_argument("--apply", action="store_true", help="Explicitly create the new environment and install the listed offline wheels")
    args = parser.parse_args(argv)
    try:
        if args.apply and not args.wheel_sha256:
            raise Refused("--apply requires --wheel-sha256 from the reviewed plan")
        plan, buffered = installation_plan(args.wheel, args.wheelhouse, args.destination, expected_sha256=args.wheel_sha256)
        print(json.dumps(plan, indent=2))
        if args.apply:
            command = apply_plan(plan, buffered)
            print("Installed without starting. Check setup with: " + shlex.join([str(command), "check"]))
        else:
            command = [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--wheel", str(args.wheel.absolute()),
                       "--wheelhouse", str(args.wheelhouse.absolute()), "--destination", plan["destination"],
                       "--wheel-sha256", plan["dashboard_sha256"], "--apply"]
            print("No changes made. After reviewing all artifacts and writes, apply with:\n" + shlex.join(command))
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        parser.exit(2, f"Installation refused: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
