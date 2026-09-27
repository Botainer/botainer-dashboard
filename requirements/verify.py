#!/usr/bin/env python3
"""Check dependency inputs and bundled assets offline; never install or import packages."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys


ROOT = Path(__file__).resolve().parent.parent


def fail(message: str) -> None:
    raise ValueError(message)


def source_file(root: Path, name: str) -> Path:
    value = PurePosixPath(name)
    if (not name or "\\" in name or ":" in name or value.is_absolute()
            or str(value) != name or any(part in ("", ".", "..") for part in value.parts)):
        fail(f"Invalid inventory path: {name!r}")
    path = root
    for part in value.parts:
        path /= part
        if path.is_symlink():
            fail(f"Symlink in inventory path: {name}")
    if not path.is_file():
        fail(f"Missing inventory file: {name}")
    return path


def verify(root: Path = ROOT) -> dict[str, int]:
    sums = source_file(root, "requirements/SHA256SUMS")
    entries = {}
    for line in sums.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([a-f0-9]{64})  (.+)", line)
        if not match:
            fail("Malformed checksum entry")
        expected, name = match.groups()
        if name in entries or name == "requirements/SHA256SUMS":
            fail(f"Duplicate or self-referencing checksum: {name}")
        data = source_file(root, name).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            fail(f"Checksum mismatch: {name}")
        entries[name] = data

    # Require a complete inventory of these bounded distribution directories.
    actual = set()
    for directory in ("requirements", "third_party", "frontend/vendor"):
        for path in (root / directory).rglob("*"):
            if path.is_symlink():
                fail(f"Symlink in dependency tree: {path.relative_to(root)}")
            if path.is_file() and path != sums:
                actual.add(path.relative_to(root).as_posix())
    if actual != set(entries):
        fail("Dependency inventory differs from its files")

    load = lambda name: json.loads(entries[name])
    manifest = load("requirements/manifest.json")
    metadata = load(manifest["python"]["artifact_metadata"])
    packages = metadata["packages"]
    pins = {}
    text = entries[manifest["python"]["hashed_requirements"]].decode().replace("\\\n", " ")
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9._-]+)==([A-Za-z0-9.!+_-]+)\s+--hash=sha256:([0-9a-f]{64})", line.strip())
        if not match or match[1] in pins:
            fail("Invalid or duplicate wheel requirement")
        pins[match[1]] = (match[2], match[3])
    expected_pins = {p["name"]: (p["version"], p["wheel"]["digests"]["sha256"]) for p in packages}
    if len(expected_pins) != len(packages) or pins != expected_pins:
        fail("Wheel requirements and metadata disagree")
    if manifest["python"]["direct_requirements"] != metadata["direct_requirements"]:
        fail("Direct dependency declarations disagree")
    for requirement in metadata["direct_requirements"]:
        name, version = requirement.split("==", 1)
        if name not in pins or pins[name][0] != version:
            fail("Direct dependency is absent from pinned selection")

    frontend = load("requirements/frontend.metadata.json")["packages"]
    frontend_pins = load("requirements/frontend.package.json")["dependencies"]
    if {p["name"]: p["version"] for p in frontend} != frontend_pins or len(frontend) != len(frontend_pins):
        fail("Browser package declarations disagree")
    bundles = load(manifest["frontend"]["bundled_inventory"])["packages"]
    if {p["name"]: p["version"] for p in bundles} != frontend_pins or len(bundles) != len(frontend_pins):
        fail("Browser bundle inventory and declared versions disagree")
    bundled_files = set()
    by_name = {p["name"]: p for p in frontend}
    for package in bundles:
        upstream = by_name[package["name"]]
        integrity = package["archive_integrity"]
        if (package["archive_url"] != upstream["dist"]["tarball"]
                or integrity != upstream["dist"]["integrity"]
                or not integrity.startswith("sha512-")
                or len(base64.b64decode(integrity[7:], validate=True)) != 64
                or package["license"] != upstream["license"]):
            fail("Browser archive identity or license metadata disagree")
        expected_assets = {upstream[k] for k in ("main", "module", "style") if upstream.get(k)} | {"LICENSE"}
        prefix = "frontend/vendor/" + package["name"].removeprefix("@xterm/") + "/"
        if {item["path"] for item in package["files"]} != {prefix + p for p in expected_assets}:
            fail("Browser asset or license inventory is incomplete")
        for item in package["files"]:
            name = item["path"]
            if name in bundled_files or name not in entries:
                fail("Duplicate or unverified browser asset")
            data = entries[name]
            if len(data) != item["bytes"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
                fail(f"Browser asset identity mismatch: {name}")
            bundled_files.add(name)
    if bundled_files != {name for name in entries if name.startswith("frontend/vendor/")}:
        fail("Unlisted bundled browser file")
    return {"files": len(entries), "python_packages": len(packages), "browser_packages": len(bundles)}


def main() -> int:
    try:
        counts = verify()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"FAIL dependency integrity: {exc}", file=sys.stderr)
        return 1
    print("PASS dependency integrity: " + ", ".join(f"{value} {key.replace('_', ' ')}" for key, value in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
