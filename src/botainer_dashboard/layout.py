"""Fixed application resources and separately selected private operating data.

The package never discovers code in a project or imports from the data directory.
Source installations retain their existing .local identity without migration.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys


def resource_root() -> Path:
    package = Path(__file__).resolve().parent
    installed = package / "_resources"
    if installed.is_dir():
        if not (installed / "PACKAGE-MANIFEST.json").is_file():
            raise ValueError("dashboard package resource inventory is missing; reinstall the reviewed wheel")
        return installed
    source = package.parents[1]
    if source / "src/botainer_dashboard" != package or not (source / "frontend").is_dir():
        raise ValueError("dashboard resources are missing; install the complete reviewed wheel")
    return source


def installed_layout(root: Path) -> bool:
    return Path(root) == Path(__file__).resolve().parent / "_resources"


def default_data_root(root: Path) -> Path:
    if not installed_layout(root):
        return Path(root) / ".local"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Botainer Dashboard"
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg and not Path(xdg).is_absolute():
        raise ValueError("XDG_STATE_HOME must be absolute, or use --data-dir")
    return (Path(xdg) if xdg else Path.home() / ".local/state") / "botainer-dashboard"


def data_root(root: Path, selected: Path | None = None) -> Path:
    path = (Path(selected).expanduser().absolute() if selected is not None
            else default_data_root(root).absolute())
    # Do not resolve symlinks into trusted-looking aliases. Creation performs
    # the same ancestry/ownership checks through private_directory.
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("dashboard data directory must not contain symlinks")
    path = Path(os.path.abspath(path))
    if installed_layout(root) and (path.is_relative_to(Path(root).parent)
                                   or path.is_relative_to(Path(sys.prefix).absolute())):
        raise ValueError("dashboard data must be outside the installed application")
    return path


def prepare_data_root(root: Path, selected: Path | None = None) -> Path:
    """Create operating storage; retain the legacy source .local container.

    Prepared checkouts also keep environments/downloads in .local, which may
    already be owner-writable and publicly traversable. Its auth/run/backend
    subdirectories still enforce their original owner-only permissions.
    Installed and explicitly selected data roots always require owner-only.
    """
    from .pairing import private_directory
    path = data_root(root, selected)
    if installed_layout(root) or selected is not None:
        return private_directory(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if not path.is_dir() or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ValueError("source .local directory must belong to the current user and not be writable by others")
    return path


def runtime_python(root: Path) -> Path:
    return (Path(sys.executable).absolute() if installed_layout(root)
            else Path(root) / ".local/envs/botainer_dashboard/bin/python")


def runtime_valid(root: Path) -> bool:
    executable = runtime_python(root)
    if installed_layout(root):
        # Ordinary venv Python symlinks can resolve to their base interpreter.
        # Validate the environment/package location, not the symlink's target.
        return (sys.implementation.name == "cpython" and sys.version_info[:2] == (3, 13)
                and sys.version_info[:3] >= (3, 13, 15)
                and Path(__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
                and executable.is_file() and os.access(executable, os.X_OK))
    prefix = Path(root) / ".local/envs/botainer_dashboard"
    return (prefix.resolve() == prefix and executable.is_file()
            and executable.resolve().is_relative_to(prefix)
            and os.access(executable, os.X_OK))


def verify_package(root: Path) -> None:
    """Check the installed source/resource inventory without importing plugins.

    This detects an incomplete or changed installation, not a malicious owner
    replacing both the application and its inventory. Publisher verification is
    performed on the release artifact before installation.
    """
    if not installed_layout(root):
        return
    package = Path(root).parent
    manifest = root / "PACKAGE-MANIFEST.json"
    if manifest.is_symlink() or manifest.stat().st_size > 1024 * 1024:
        raise ValueError("invalid dashboard package inventory")
    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate package inventory field")
            result[key] = item
        return result
    value = json.loads(manifest.read_text(), object_pairs_hook=unique)
    if (not isinstance(value, dict) or type(value.get("schema_version")) is not int
            or value["schema_version"] != 1 or value.get("distribution") != "botainer-dashboard"
            or not isinstance(value.get("files"), list) or not 1 <= len(value["files"]) <= 1000):
        raise ValueError("invalid dashboard package inventory")
    seen = set()
    for entry in value["files"]:
        if not isinstance(entry, dict) or set(entry) != {"source", "path", "sha256"}:
            raise ValueError("invalid dashboard package inventory entry")
        name = entry.get("path")
        if (not isinstance(name, str) or not name or "\\" in name
                or Path(name).is_absolute() or any(part in {"", ".", ".."} for part in name.split("/"))
                or name in seen):
            raise ValueError("invalid dashboard package inventory member")
        seen.add(name)
        path = package / name
        if (any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file()
                or path.stat().st_size > 16 * 1024 * 1024
                or hashlib.sha256(path.read_bytes()).hexdigest() != entry.get("sha256")):
            raise ValueError("dashboard package file changed or missing: " + name)
    # Bytecode caches created by Python/pip are not source. Every other regular
    # resource/module in this private package must have an inventory entry.
    actual = {str(p.relative_to(package)) for p in package.rglob("*") if p.is_file()
              and "__pycache__" not in p.parts and p != manifest}
    if actual != seen:
        raise ValueError("dashboard package contains unlisted or missing files")
