"""Declarative cluster settings, separated from private connections and jobs.

Parsing performs no connection, discovery, installation or execution. A valid
profile is not a verified site or permission to launch. Preset caps are local
request ceilings; the scheduler and Botainer policy remain authoritative.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from types import MappingProxyType
from typing import Mapping


MAX_PROFILE_BYTES = 128 * 1024


class ClusterProfileError(ValueError):
    """Invalid or unsafe cluster registration data; nothing was dispatched."""


def _object(value, required, label, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise ClusterProfileError(f"{label} has missing or unsupported fields")
    return value


def _text(value, label, limit=160):
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ClusterProfileError(f"{label} must be bounded nonempty text")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise ClusterProfileError(f"{label} must be valid UTF-8") from exc
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ClusterProfileError(f"{label} must not contain control characters")
    return value


def _identifier(value, label, limit=64):
    value = _text(value, label, limit)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise ClusterProfileError(f"{label} must be a simple identifier")
    return value


def _integer(value, label, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ClusterProfileError(f"{label} must be an integer from {minimum} to {maximum}")
    return value


def _version(value, label):
    return _integer(value, label, 1, 1)


def _path(value, label):
    value = _text(value, label, 4096)
    path = PurePosixPath(value)
    if (not value.startswith("/") or value.startswith("//") or value == "/"
            or str(path) != value or ".." in path.parts or any(char in value for char in "$`~")):
        raise ClusterProfileError(f"{label} must be a normalized absolute path without expansion")
    return value


def _contains(parent, child):
    parent, child = PurePosixPath(parent), PurePosixPath(child)
    return parent == child or parent in child.parents


@dataclass(frozen=True)
class PartitionLimits:
    name: str
    max_cpus: int
    max_memory_mib: int
    max_time_minutes: int
    max_gpus: int

    def as_dict(self):
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True)
class SitePreset:
    version: int
    id: str
    label: str
    scheduler: str
    runtime: str
    attach_route: str
    partitions: tuple[PartitionLimits, ...]

    def as_dict(self):
        """Shareable site data only; never export a user's connection here."""
        return {"version": self.version, "id": self.id, "label": self.label,
                "scheduler": self.scheduler, "runtime": self.runtime,
                "attach_route": self.attach_route,
                "partitions": [item.as_dict() for item in self.partitions]}


@dataclass(frozen=True)
class ClusterLauncher:
    mode: str
    path: str


@dataclass(frozen=True)
class ClusterConnection:
    ssh_alias: str
    remote_python: str
    source_root: str
    state_root: str
    image: str
    trial_root: str
    launcher: ClusterLauncher
    file_roots: Mapping[str, str]

    def as_dict(self):
        return {"ssh_alias": self.ssh_alias, "remote_python": self.remote_python,
                "source_root": self.source_root, "state_root": self.state_root,
                "image": self.image, "trial_root": self.trial_root,
                "launcher": {"mode": self.launcher.mode, "path": self.launcher.path},
                "file_roots": dict(self.file_roots)}


@dataclass(frozen=True)
class JobDefaults:
    partition: str
    cpus: int
    memory_mib: int
    time_minutes: int
    gpus: int
    account: str | None = None
    qos: str | None = None

    def as_dict(self):
        return {name: getattr(self, name) for name in self.__dataclass_fields__ if getattr(self, name) is not None}


@dataclass(frozen=True)
class ClusterProfile:
    version: int
    id: str
    label: str
    preset: SitePreset
    connection: ClusterConnection
    defaults: JobDefaults

    def as_dict(self):
        """Private profile data. Use preset.as_dict() for shareable export."""
        return {"version": self.version, "id": self.id, "label": self.label,
                "preset": self.preset.as_dict(), "connection": self.connection.as_dict(),
                "defaults": self.defaults.as_dict()}

    @property
    def fingerprint(self):
        """Configuration identity, not source/tool identity or live verification."""
        canonical = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(canonical.encode("ascii")).hexdigest()

    def trial_profile(self):
        """Exact legacy preparation fields; no resource/default activation.

        The adapter must enforce the helper's actual supported resource scope,
        verify a fresh per-run root, and retain its resulting profile separately.
        This compatibility view does not authorize arbitrary project execution.
        """
        return {name: getattr(self.connection, name) for name in
                ("ssh_alias", "remote_python", "trial_root", "source_root", "image")}


def parse_site_preset(raw):
    data = _object(raw, {"version", "id", "label", "scheduler", "runtime", "attach_route", "partitions"}, "preset")
    _version(data["version"], "preset version")
    if (data["scheduler"], data["runtime"], data["attach_route"]) != ("slurm", "apptainer", "slurm-screen"):
        raise ClusterProfileError("v1 supports only the declared Slurm/Apptainer/slurm-screen route")
    if not isinstance(data["partitions"], list) or not 1 <= len(data["partitions"]) <= 64:
        raise ClusterProfileError("preset requires 1–64 partition request ceilings")
    limits, names = [], set()
    for raw_partition in data["partitions"]:
        part = _object(raw_partition, {"name", "max_cpus", "max_memory_mib", "max_time_minutes", "max_gpus"}, "partition")
        name = _identifier(part["name"], "partition name")
        if name in names:
            raise ClusterProfileError("duplicate partition name")
        names.add(name)
        limits.append(PartitionLimits(name,
            _integer(part["max_cpus"], "max_cpus", 1, 4096),
            _integer(part["max_memory_mib"], "max_memory_mib", 1, 16 * 1024 * 1024),
            _integer(part["max_time_minutes"], "max_time_minutes", 1, 525600),
            _integer(part["max_gpus"], "max_gpus", 0, 128)))
    return SitePreset(1, _identifier(data["id"], "preset ID"), _text(data["label"], "preset label"),
                      "slurm", "apptainer", "slurm-screen", tuple(limits))


def parse_job_defaults(raw, preset):
    """Validate a complete proposed job draft against configured local ceilings.

    This does not override Botainer, prove account access, or mutate saved
    defaults. Callers should revalidate each project's exact launch draft.
    """
    data = _object(raw, {"partition", "cpus", "memory_mib", "time_minutes", "gpus"}, "job defaults", {"account", "qos"})
    partition = _identifier(data["partition"], "default partition")
    limit = next((item for item in preset.partitions if item.name == partition), None)
    if limit is None:
        raise ClusterProfileError("default partition is not registered in the preset")
    return JobDefaults(partition,
        _integer(data["cpus"], "cpus", 1, limit.max_cpus),
        _integer(data["memory_mib"], "memory_mib", 1, limit.max_memory_mib),
        _integer(data["time_minutes"], "time_minutes", 1, limit.max_time_minutes),
        _integer(data["gpus"], "gpus", 0, limit.max_gpus),
        _identifier(data["account"], "account") if "account" in data else None,
        _identifier(data["qos"], "qos") if "qos" in data else None)


def parse_cluster_profile(raw):
    data = _object(raw, {"version", "id", "label", "preset", "connection", "defaults"}, "cluster profile")
    _version(data["version"], "profile version")
    preset = parse_site_preset(data["preset"])
    fields = {"ssh_alias", "remote_python", "source_root", "state_root", "image", "trial_root", "launcher", "file_roots"}
    connection = _object(data["connection"], fields, "connection")
    alias = _identifier(connection["ssh_alias"], "SSH alias", 128)
    paths = {name: _path(connection[name], name) for name in fields - {"ssh_alias", "launcher", "file_roots"}}
    launcher = _object(connection["launcher"], {"mode", "path"}, "launcher")
    if launcher["mode"] not in ("executable", "python_module"):
        raise ClusterProfileError("launcher mode must be executable or python_module")
    launcher_path = _path(launcher["path"], "launcher path")
    roots = connection["file_roots"]
    if not isinstance(roots, dict) or not 1 <= len(roots) <= 32:
        raise ClusterProfileError("connection requires 1–32 explicit private file roots")
    roots = {_identifier(name, "file root ID"): _path(path, "file root") for name, path in roots.items()}
    trial = paths["trial_root"]
    for name in ("source_root", "state_root"):
        if _contains(trial, paths[name]) or _contains(paths[name], trial):
            raise ClusterProfileError("trial root and existing source/state must be separate trees")
    for path in (paths["remote_python"], paths["image"], launcher_path):
        if _contains(trial, path):
            raise ClusterProfileError("existing interpreter, launcher and image must be outside the trial tree")
    if not any(_contains(root, trial) and root != trial for root in roots.values()):
        raise ClusterProfileError("trial root must be a child of an explicitly allowed private file root")
    parsed_connection = ClusterConnection(alias, paths["remote_python"], paths["source_root"],
        paths["state_root"], paths["image"], trial, ClusterLauncher(launcher["mode"], launcher_path),
        MappingProxyType(roots))
    return ClusterProfile(1, _identifier(data["id"], "profile ID"), _text(data["label"], "profile label"),
                          preset, parsed_connection, parse_job_defaults(data["defaults"], preset))


def _unique_pairs(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ClusterProfileError("duplicate JSON field")
        result[name] = value
    return result


def _json(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_PROFILE_BYTES:
        raise ClusterProfileError("cluster JSON exceeds the 128 KiB bound")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs,
                          parse_constant=lambda _value: (_ for _ in ()).throw(ClusterProfileError("nonfinite JSON number")))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ClusterProfileError("cluster JSON must be valid, bounded and unambiguous") from exc


def load_cluster_profile(path):
    """Read owner-only POSIX JSON through non-symlink directory descriptors.

    There is no environment search or default fallback. Even a missing file is
    an explicit error. No remote paths are inspected, created or executed.
    """
    selected = Path(os.path.abspath(path))
    descriptor = None
    try:
        descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        for component in selected.parent.parts[1:]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        parent = os.fstat(descriptor)
        if parent.st_uid != os.getuid() or parent.st_mode & 0o077:
            raise ClusterProfileError("cluster profile parent must be owned by this user and private")
        fd = os.open(selected.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=descriptor)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077 or info.st_nlink != 1):
                raise ClusterProfileError("cluster profile must be an owner-only regular file without hard links")
            raw = stream.read(MAX_PROFILE_BYTES + 1)
    except (OSError, ValueError) as exc:
        if isinstance(exc, ClusterProfileError):
            raise
        raise ClusterProfileError("cluster profile cannot be read safely") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return parse_cluster_profile(_json(raw))
