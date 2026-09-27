"""Bounded, non-executing recognition of an installed Botainer distribution.

This stdlib-only module is also sent inside the fixed inspection/remote helper.
Metadata identifies a layout, not trustworthy software. Executed package/plugin
files still need the ordinary exact profile pins and native selection checks.
RECORD paths and entry-point strings are never evaluated or used as commands.
"""
from __future__ import annotations

import configparser
from email.parser import Parser
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat


class InstallationLayoutError(ValueError):
    pass


def _require(value, message):
    if not value:
        raise InstallationLayoutError(message)


def parse_layout(value):
    """Validate the explicit version-2 descriptor without filesystem access."""
    _require(isinstance(value, dict) and set(value) == {"kind", "metadata_path"}
             and value["kind"] == "wheel", "Unsupported installation layout")
    name = value["metadata_path"]
    _require(isinstance(name, str) and len(name) <= 240
             and re.fullmatch(r"botainer-[A-Za-z0-9][A-Za-z0-9.!+_]*\.dist-info/METADATA", name)
             and str(PurePosixPath(name)) == name, "Invalid Botainer metadata path")
    return dict(value)


def metadata_paths(layout):
    name = parse_layout(layout)["metadata_path"]
    return (name, str(PurePosixPath(name).with_name("entry_points.txt")))


def validate_source_pins(layout, hashes):
    metadata = set(metadata_paths(layout))
    _require(isinstance(hashes, dict) and metadata | {"botainer/__init__.py"} <= hashes.keys()
             and len(hashes) <= 2048, "Installed Botainer source pins are incomplete")
    for name, digest in hashes.items():
        _require(isinstance(name, str) and len(name) <= 512
                 and (name in metadata or name.startswith("botainer/"))
                 and str(PurePosixPath(name)) == name and ".." not in PurePosixPath(name).parts
                 and not any(ord(c) < 32 or ord(c) == 127 for c in name)
                 and isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest),
                 "Installed Botainer source pin is outside its package")


def _read(path):
    path = Path(path)
    _require(path.resolve(strict=True) == path, "Installation metadata contains a symlink")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                 and before.st_size <= 131072, "Invalid installation metadata file")
        raw = stream.read(131073)
        after = os.fstat(stream.fileno())
        _require(len(raw) <= 131072 and (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                 == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
                 "Installation metadata changed during inspection")
    return raw


def _candidates(root):
    candidates = []
    with os.scandir(root) as entries:
        for count, entry in enumerate(entries, 1):
            _require(count <= 10000, "Installation directory exceeds inspection bound")
            if entry.name.lower().startswith("botainer-") and entry.name.lower().endswith(".dist-info"):
                candidates.append(entry.name + "/METADATA")
    return sorted(candidates)


def discover_layout(root):
    """Return None for the original source layout; refuse mixed/ambiguous roots."""
    root = Path(root)
    candidates = _candidates(root)
    if (root / "pyproject.toml").exists():
        _require(not candidates, "Mixed source and installed Botainer layouts")
        return None
    _require(len(candidates) == 1, "Expected one installed Botainer distribution")
    layout = parse_layout({"kind": "wheel", "metadata_path": candidates[0]})
    verify_layout(root, layout)
    return layout


def verify_layout(root, layout, hashes=None):
    """Verify bounded metadata and optional exact pins; never import Botainer."""
    root = Path(root)
    layout = parse_layout(layout)
    _require(root.is_absolute() and root.resolve(strict=True) == root and root.is_dir(),
             "Installation root is not canonical")
    _require(_candidates(root) == [layout["metadata_path"]], "Botainer distribution identity is ambiguous")
    paths = metadata_paths(layout)
    contents = {name: _read(root / name) for name in paths}
    if hashes is not None:
        validate_source_pins(layout, hashes)
        _require(all(hashes.get(name) == hashlib.sha256(raw).hexdigest() for name, raw in contents.items()),
                 "Installed Botainer metadata pins changed")
    metadata = Parser().parsestr(contents[paths[0]].decode("utf-8"), headersonly=True)
    _require(metadata.get_all("Name") == ["botainer"] and len(metadata.get_all("Version", [])) == 1,
             "Invalid Botainer distribution identity")
    version = metadata["Version"]
    _require(isinstance(version, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.!+_]{0,95}", version)
             and paths[0] == "botainer-" + version + ".dist-info/METADATA",
             "Botainer distribution version does not match its directory")
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    try:
        parser.read_string(contents[paths[1]].decode("utf-8"))
        _require(not parser.defaults() and parser.get("console_scripts", "botainer", raw=True) == "botainer.cli.main:main",
                 "Unsupported Botainer console entry point")
    except configparser.Error:
        raise InstallationLayoutError("Invalid Botainer console entry points") from None
    # Native lifecycle.py can prefer editable source plugins in ancestor roots.
    # A wheel profile must not accidentally acquire that different plugin scope.
    for parent in list((root / "botainer/__init__.py").parents)[:6]:
        project = parent / "pyproject.toml"
        if project.exists() or project.is_symlink():
            content = _read(project).decode("utf-8")
            if 'name = "botainer"' in content or "name = 'botainer'" in content:
                _require(not (parent / "plugins").exists(), "Wheel profile has an editable plugin override")
                break
    return layout
