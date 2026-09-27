"""Setuptools guard for an explicitly staged, reviewed application wheel.

The source checkout is not an installation tree. A release builder supplies
the complete immutable resource tree and its portable content inventory first.
This guard never installs, downloads, discovers state, or approves source bytes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from setuptools.command.build_py import build_py


MANIFEST = "_resources/PACKAGE-MANIFEST.json"
FORBIDDEN = {"private", ".local", ".git", "dev", "prototype", "tests", "__pycache__", "node_modules"}


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate package manifest key")
        result[key] = value
    return result


def canonical_path(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("invalid package path")
    path = PurePosixPath(value)
    if (path.is_absolute() or str(path) != value or any(p in {".", ".."} for p in path.parts)
            or any(p.casefold() in FORBIDDEN for p in path.parts)
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError("unsafe package path")
    return path


def validate_package_tree(package):
    package = Path(package)
    marker = package / MANIFEST
    if any(p.is_symlink() for p in (marker, *marker.parents)) or not marker.is_file():
        raise ValueError("Build a reviewed wheel from a staged resource tree; raw source installation is unsupported. See docs/installation.md.")
    if marker.stat().st_size > 1024 * 1024:
        raise ValueError("package manifest exceeds limit")
    manifest = json.loads(marker.read_text(encoding="utf-8"), object_pairs_hook=_unique)
    if (not isinstance(manifest, dict) or set(manifest) != {"schema_version", "distribution", "version", "files"}
            or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1
            or manifest["distribution"] != "botainer-dashboard" or not isinstance(manifest["version"], str)
            or not isinstance(manifest["files"], list) or not 1 <= len(manifest["files"]) <= 1000):
        raise ValueError("invalid package manifest")
    expected = {MANIFEST}
    folded = {MANIFEST.casefold()}
    for item in manifest["files"]:
        if not isinstance(item, dict) or set(item) != {"source", "path", "sha256"}:
            raise ValueError("invalid package manifest entry")
        canonical_path(item["source"])
        relative = str(canonical_path(item["path"]))
        if relative.casefold() in folded:
            raise ValueError("duplicate package destination")
        if not isinstance(item["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"]):
            raise ValueError("invalid package digest")
        path = package / relative
        if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
            raise ValueError("package file is missing or a symlink")
        if path.stat().st_size > 16 * 1024 * 1024 or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("package file differs from staged inventory")
        expected.add(relative)
        folded.add(relative.casefold())
    actual = set()
    for path in package.rglob("*"):
        if path.is_symlink():
            raise ValueError("package contains a symlink")
        if path.is_file():
            actual.add(path.relative_to(package).as_posix())
    if actual != expected:
        raise ValueError("package contains unlisted or missing files")
    return manifest


class CheckedBuildPy(build_py):
    def run(self):
        manifest = validate_package_tree(Path(self.get_package_dir("botainer_dashboard")))
        if manifest["version"] != self.distribution.metadata.version:
            raise ValueError("package manifest version differs from distribution metadata")
        super().run()
