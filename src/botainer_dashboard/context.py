"""Immutable local installation and target snapshots.

These are trusted registration data, not authorization or proof that an
executable is Botainer. A later identity probe and runner must check loaded code,
state-layout compatibility, project identity and changed files before execution.
Constructing a snapshot only reads filesystem metadata; it never creates paths.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional, Tuple
from uuid import UUID


class ContextError(ValueError):
    """A context or target cannot be represented safely and explicitly."""


def _text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContextError(f"{label} must be a nonempty string")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ContextError(f"{label} must not contain control characters")
    return value


def _identifier(value: str, label: str) -> str:
    _text(value, label)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise ContextError(f"{label} must be a simple registration identifier")
    return value


def _absolute_path(value: Path, label: str) -> Path:
    if not isinstance(value, (str, Path)):
        raise ContextError(f"{label} must be an absolute path")
    _text(str(value), label)
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ContextError(f"{label} must be absolute, without parent traversal")
    return path


def canonical_directory(value: Path, label: str) -> Path:
    """Resolve an existing directory, allowing spaces and shell punctuation."""
    path = _absolute_path(value, label)
    try:
        result = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ContextError(f"{label} cannot be resolved") from exc
    if not result.is_dir():
        raise ContextError(f"{label} must be an existing directory")
    return result


@dataclass(frozen=True)
class Launcher:
    executable: Path
    mode: Literal["executable", "python_module"] = "executable"

    def __post_init__(self) -> None:
        if self.mode not in ("executable", "python_module"):
            raise ContextError("unsupported launcher mode")
        path = _absolute_path(self.executable, "launcher executable")
        # Do NOT resolve the executable's final symlink: a venv's python often
        # points to its base interpreter, but invoking that base loses the venv.
        parent = canonical_directory(path.parent, "launcher directory")
        selected = parent / path.name
        if not selected.is_file() or not os.access(selected, os.X_OK):
            raise ContextError("selected launcher must be an existing executable file")
        object.__setattr__(self, "executable", selected)

    @property
    def argv_prefix(self) -> Tuple[str, ...]:
        if self.mode == "python_module":
            return (str(self.executable), "-I", "-m", "botainer.cli.main")
        return (str(self.executable),)


@dataclass(frozen=True)
class ExecutionContext:
    context_id: str
    site_id: str
    account: str
    launcher: Launcher
    state_root: Path
    revision: int = 1
    transport: Literal["local"] = "local"

    def __post_init__(self) -> None:
        _identifier(self.context_id, "context ID")
        _identifier(self.site_id, "site ID")
        _text(self.account, "account")
        if not isinstance(self.launcher, Launcher):
            raise ContextError("context requires an explicit Launcher")
        if type(self.revision) is not int or self.revision < 1:
            raise ContextError("context revision must be a positive integer")
        if self.transport != "local":
            raise ContextError("only local execution contexts are supported by this foundation")
        path = _absolute_path(self.state_root, "state root")
        try:
            root = path.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise ContextError("state root cannot be resolved") from exc
        # Match the audited MY_BOTAINER character policy, without duplicating
        # its version-dependent location policy. Compatibility remains unproven.
        if not re.fullmatch(r"[A-Za-z0-9._/~-]+", str(root)):
            raise ContextError("state root contains characters unsupported by MY_BOTAINER")
        if root.exists() and not root.is_dir():
            raise ContextError("state root must be a directory when present")
        object.__setattr__(self, "state_root", root)

    @property
    def namespace_key(self) -> Tuple[str, str, str]:
        """Different executables sharing this key share the same inventory."""
        return (self.site_id, self.account, str(self.state_root))


@dataclass(frozen=True)
class RuntimeTarget:
    context: ExecutionContext
    project_root: Path
    runtime: Literal["docker", "apptainer"] = "docker"
    project_id: Optional[str] = None
    session_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.context, ExecutionContext):
            raise ContextError("target requires an ExecutionContext snapshot")
        if self.runtime not in ("docker", "apptainer"):
            raise ContextError("runtime must be explicit: docker or apptainer")
        object.__setattr__(self, "project_root", canonical_directory(
            self.project_root, "project directory"))
        if self.project_id is not None:
            _text(self.project_id, "project ID")
            try:
                canonical = str(UUID(self.project_id))
            except ValueError as exc:
                raise ContextError("project ID must be a canonical UUID") from exc
            if canonical != self.project_id:
                raise ContextError("project ID must be a canonical UUID")
        if self.session_id is not None:
            if not isinstance(self.session_id, str) or not re.fullmatch(
                    r"[0-9a-f]{16}", self.session_id):
                raise ContextError("session ID must be the full 16-character lowercase hex ID")
            if self.project_id is None:
                raise ContextError("a session target also requires its project ID")

    @property
    def checkout_key(self) -> Tuple[str, str, str]:
        """Shared checkouts stay shared even across different state roots."""
        return (self.context.site_id, self.context.account, str(self.project_root))

    @property
    def session_key(self) -> Optional[Tuple[str, str, str, str, str]]:
        if self.session_id is None:
            return None
        return (*self.context.namespace_key, self.project_id, self.session_id)
