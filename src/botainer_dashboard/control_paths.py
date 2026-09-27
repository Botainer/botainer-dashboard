"""Read-only local terminal control-root checks shared by setup and runtime.

This module uses only the standard library: its exact reviewed source also runs
inside the isolated installation probe, without importing the inspected package.
It never creates a directory, resolves a selection on the caller's behalf, or
changes permissions. Existing terminal owners must keep their original root.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat


LOCAL_CONTROL_ROOT_MAX_BYTES = 55
LOCAL_CONTROL_ROOT_MESSAGES = {
    "control-root-path-invalid": "The private dashboard control folder (control_root) must use a normalized absolute path without symlinks, parent traversal or control characters.",
    "control-root-not-canonical": "The private dashboard control folder (control_root) must use its real absolute path, without symlinks.",
    "control-root-missing": "The private dashboard control folder (control_root) does not exist. Create a dedicated folder with mode 700 for the login account, then inspect again. Inspection does not create folders.",
    "control-root-unavailable": "The private dashboard control folder (control_root) is inaccessible. Check its parent folders and account permissions.",
    "control-root-not-directory": "The private dashboard control folder (control_root) must be an existing directory.",
    "control-root-not-private": "The private dashboard control folder (control_root) must belong to the login account and have exactly mode 700. Inspection never changes permissions.",
    "control-root-too-long": "The private dashboard control folder (control_root) must be at most 55 filesystem-encoded bytes, including its full absolute path. Choose a shorter dedicated folder outside projects; non-ASCII characters can use more than one byte. Do not move a folder that owns existing terminals.",
}


class ControlRootError(ValueError):
    def __init__(self, code, *, resolved_path=None):
        self.code, self.resolved_path = code, resolved_path
        super().__init__(code)


def validate_local_control_root(value):
    """Return the selected canonical path and stat, or a fixed diagnostic code."""
    try:
        value = os.fspath(value)
    except TypeError:
        raise ControlRootError("control-root-path-invalid") from None
    if (not isinstance(value, str) or not value.startswith("/")
            or value.startswith("//") or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ControlRootError("control-root-path-invalid")
    path = Path(value)
    if str(path) != value or ".." in path.parts:
        raise ControlRootError("control-root-path-invalid")
    # Keep the existing owner's conservative socket-path budget. Count bytes,
    # not characters: Unix-domain socket limits apply to the encoded pathname.
    if len(os.fsencode(path)) > LOCAL_CONTROL_ROOT_MAX_BYTES:
        raise ControlRootError("control-root-too-long")
    try:
        resolved = path.resolve(strict=True)
        info = path.lstat()
    except FileNotFoundError:
        raise ControlRootError("control-root-missing") from None
    except (OSError, RuntimeError):
        raise ControlRootError("control-root-unavailable") from None
    if resolved != path:
        raise ControlRootError("control-root-not-canonical", resolved_path=str(resolved))
    if not stat.S_ISDIR(info.st_mode):
        raise ControlRootError("control-root-not-directory")
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ControlRootError("control-root-not-private")
    return path, info
