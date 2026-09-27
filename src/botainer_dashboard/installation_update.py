"""Read-only review of installed Botainer updates within a saved connection.

This module never installs software, saves profiles, activates controllers or
migrates session authority. The registry binds the returned candidate to its
revision and repeats this inspection before an atomic save. Saved documents are
read as data: the previous interpreter and source tree need not still exist.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

from .connection_prepare import prepare_connection
from .import_policy import trusted_path
from .remote_profile import parse_remote_profile


_HASH = re.compile(r"[0-9a-f]{64}\Z")
_MESSAGES = {
    "request-invalid": "Choose a saved Botainer connection and only change its source folder, Python, or remote launcher path.",
    "profile-unsupported": "This saved profile cannot use the limited installation update flow. Review it through connection setup; no selection was changed.",
    "scope-changed": "The inspection changed account, project, state, control or runtime settings. This requires connection setup, not an installation update.",
    "hooks-review-required": "Previously selected plugin or approved hook files changed or disappeared. Review their host-side code through connection setup; this update does not approve new or changed hooks.",
    "tools-review-required": "A container or terminal management tool changed. This update only reviews Botainer and its Python; review other tools through connection setup.",
    "support-review-required": "An additional pinned support file changed, disappeared or could not be safely read. Review that file through connection setup; this update does not approve changed support code.",
    "python-identity-unavailable": "The previous Python target cannot be identified unambiguously from its saved pins. Use connection setup to review the replacement without discarding other support-file approvals.",
}


class InstallationUpdateError(ValueError):
    """Fixed, portable diagnostics: no imported paths or exception text."""

    def __init__(self, code):
        self.code = "installation-update-" + code
        super().__init__(_MESSAGES[code])


def _fail(code):
    raise InstallationUpdateError(code)


def _encoded(value):
    try:
        raw = (json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False) + "\n").encode()
        if len(raw) > 2 * 1024 * 1024:
            _fail("profile-unsupported")
        return raw
    except (TypeError, ValueError, RecursionError):
        _fail("profile-unsupported")


def _digest(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


def _path(value):
    if (not isinstance(value, str) or not 1 < len(value) <= 4096
            or not value.startswith("/") or value.startswith("//")
            or str(PurePosixPath(value)) != value or ".." in PurePosixPath(value).parts
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        _fail("request-invalid")
    return value


def _pins(value):
    if (not isinstance(value, dict)
            or any(not isinstance(k, str) or not isinstance(v, str) or not _HASH.fullmatch(v)
                   for k, v in value.items())):
        _fail("profile-unsupported")
    return value


def build_update_request(kind, profile_data, changes):
    """Build a bounded inspection from saved settings, without reading old code.

    Callers must obtain the same explicit read-only interpreter/SSH inspection
    acknowledgement required by connection setup before invoking this flow.
    """
    if (kind not in {"local", "remote"} or not isinstance(profile_data, dict)
            or not isinstance(changes, dict)
            or set(changes) - ({"source_root", "python", "launcher"} if kind == "remote"
                               else {"source_root", "python"})):
        _fail("request-invalid")
    for value in changes.values():
        _path(value)
    d = profile_data
    try:
        if type(d["version"]) is not int or d["version"] not in {1, 2}:
            _fail("profile-unsupported")
        request = {"kind": kind, "id": d["id"], "label": d["label"],
                   "source_root": d["source_root"], "state_root": d["state_root"],
                   "read_only_acknowledged": True}
        if kind == "remote":
            parse_remote_profile(d)  # Schema only; never contacts the remote.
            request.update(ssh_alias=d["ssh_alias"], python=d["remote_python"],
                           launcher=d["launcher"]["path"], control_root=d["control_root"],
                           project_roots=list(d["project_roots"].values()))
        else:
            allowed = {"version", "id", "label", "source_root", "python", "home", "state_root",
                       "project_roots", "docker", "environment", "source_hashes", "support_hashes",
                       "approved_hooks", "directory_identities", "terminal_owner", "installation_layout"}
            if set(d) - allowed:
                _fail("profile-unsupported")
            if "terminal_owner" not in d:
                _fail("profile-unsupported")
            request.update(python=d["python"], home=d["home"],
                           control_root=d["terminal_owner"]["control_root"],
                           tmux_path=d["terminal_owner"]["path"],
                           docker_path=d["docker"]["executable"], docker_host=d["docker"]["host"],
                           project_roots=[item["path"] for item in d["project_roots"]])
            _pins(d["source_hashes"]); _pins(d["support_hashes"])
            _pins(d.get("approved_hooks", {}))
        request.update(changes)
        _encoded(d)
        return request
    except InstallationUpdateError:
        raise
    except (KeyError, TypeError, ValueError):
        _fail("profile-unsupported")


def _same(candidate, original, fields):
    if any(candidate.get(field) != original.get(field) for field in fields):
        _fail("scope-changed")


def _retain_pins(original, inspected):
    _pins(original); _pins(inspected)
    if any(inspected.get(path) != digest for path, digest in original.items()):
        _fail("hooks-review-required")
    # Newly discovered installed plugin files are not implicitly approved.
    return dict(original)


def _retained_directory_identity(name, expected):
    """Verify an existing identity omitted by the setup probe's convenience map."""
    if (not isinstance(expected, list) or len(expected) != 2
            or any(type(value) is not int or value < 0 for value in expected)):
        _fail("profile-unsupported")
    try:
        path = Path(name)
        if not path.is_absolute() or path.resolve(strict=True) != path:
            _fail("scope-changed")
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(descriptor)
            current = path.stat()
            after = os.fstat(descriptor)
            if (path.resolve(strict=True) != path
                    or any(not stat.S_ISDIR(info.st_mode) or [info.st_dev, info.st_ino] != expected
                           for info in (before, current, after))):
                _fail("scope-changed")
        finally:
            os.close(descriptor)
        return list(expected)
    except (OSError, ValueError, RuntimeError):
        _fail("scope-changed")


def _local_candidate(original, inspected, context):
    _same(inspected, original, ("id", "label", "home", "state_root", "terminal_owner"))
    expected_docker = {**original["docker"], "executable": context["tools"]["docker_path"]}
    if inspected["docker"] != expected_docker:
        _fail("scope-changed")
    inspected["docker"] = copy.deepcopy(original["docker"])
    old_roots = original["project_roots"]
    if ([row["path"] for row in inspected["project_roots"]]
            != [row["path"] for row in old_roots]):
        _fail("scope-changed")
    inspected["project_roots"] = copy.deepcopy(old_roots)
    # Preparation's convenience PATH is for new connections. An update must not
    # replace a previously reviewed PATH, locale or other environment setting.
    inspected["environment"] = copy.deepcopy(original["environment"])
    old_directories = original.get("directory_identities", {})
    new_directories = inspected.get("directory_identities", {})
    fixed_scope_paths = {original["home"], original["state_root"],
                         original["terminal_owner"]["control_root"],
                         *(item["path"] for item in old_roots)}
    fixed_directories = {p: identity for p, identity in old_directories.items()
                         if p != original["source_root"] or p in fixed_scope_paths}
    for path, identity in fixed_directories.items():
        current = (new_directories[path] if path in new_directories
                   else _retained_directory_identity(path, identity))
        if current != identity:
            _fail("scope-changed")
    # Preserve every old protected-directory identity. Only the selected source
    # directory can acquire a new identity. Do not invent an optional source pin
    # if the original profile did not have one.
    inspected["directory_identities"] = copy.deepcopy(fixed_directories)
    if original["source_root"] in old_directories:
        inspected["directory_identities"][inspected["source_root"]] = new_directories[inspected["source_root"]]
    if "directory_identities" not in original:
        inspected.pop("directory_identities", None)
    old_support, new_support = _pins(original["support_hashes"]), _pins(inspected["support_hashes"])
    state_prefix = original["state_root"].rstrip("/") + "/plugins/"
    old_plugins = {p: digest for p, digest in old_support.items() if p.startswith(state_prefix)}
    retained_plugins = _retain_pins(old_plugins, new_support)
    docker = context["tools"]["docker_path"]
    if docker not in old_support or new_support.get(docker) != old_support[docker]:
        _fail("tools-review-required")
    # The fixed setup probe returns one selected interpreter plus Docker and
    # installed plugins. Existing additional support pins are retained, not
    # mistaken for interpreters or replaced with newly discovered files.
    new_python = {p for p in new_support if not p.startswith(state_prefix)} - {docker}
    if len(new_python) != 1:
        _fail("profile-unsupported")
    if any(path in context["extra_support"] and context["extra_support"][path] != new_support[path]
           for path in new_python):
        _fail("support-review-required")
    inspected["support_hashes"] = {**context["extra_support"], **retained_plugins, docker: old_support[docker],
                                  **{p: new_support[p] for p in new_python}}
    sources = {str(PurePosixPath(inspected["source_root"]) / p): digest
               for p, digest in inspected["source_hashes"].items()}
    inspected["approved_hooks"] = _retain_pins(original.get("approved_hooks", {}),
                                               {**sources, **inspected["support_hashes"]})
    if "approved_hooks" not in original:
        inspected.pop("approved_hooks", None)
    return inspected


def _remote_candidate(original, inspected):
    _same(inspected, original, ("id", "label", "ssh_alias", "state_root", "control_root"))
    if list(inspected["project_roots"].values()) != list(original["project_roots"].values()):
        _fail("scope-changed")
    inspected["project_roots"] = copy.deepcopy(original["project_roots"])
    inspected["state_plugin_sha256"] = _retain_pins(original["state_plugin_sha256"],
                                                    inspected["state_plugin_sha256"])
    parse_remote_profile(inspected)
    return inspected


def _resolve_selector(value):
    return str(Path(value).resolve(strict=True))


def _retain_support(pins):
    """Read exact additional inputs; never import or execute their contents."""
    remaining = 384 * 1024 * 1024
    try:
        for name, expected in pins.items():
            path = Path(name)
            trusted_path(path)
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                before = os.fstat(stream.fileno())
                limit = min(256 * 1024 * 1024, remaining)
                if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                    _fail("support-review-required")
                digest = hashlib.sha256(); read = 0
                while True:
                    chunk = stream.read(min(1024 * 1024, limit - read + 1))
                    if not chunk:
                        break
                    read += len(chunk)
                    if read > limit:
                        _fail("support-review-required")
                    digest.update(chunk)
                after = os.fstat(stream.fileno())
            fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode", "st_uid")
            if (any(getattr(before, field) != getattr(after, field) for field in fields)
                    or digest.hexdigest() != expected):
                _fail("support-review-required")
            trusted_path(path)
            remaining -= read
        return dict(pins)
    except (OSError, ValueError, RuntimeError):
        _fail("support-review-required")


def _local_support_context(profile):
    """Retain tool trust before preparation can execute the Docker CLI."""
    try:
        docker_path = _resolve_selector(_path(profile["docker"]["executable"]))
        tmux_path = _path(profile["terminal_owner"]["path"])
        expected = {"docker_path": docker_path,
                    "docker_sha256": profile["support_hashes"][docker_path],
                    "tmux_path": tmux_path,
                    "tmux_sha256": profile["terminal_owner"]["sha256"]}
        if any(not isinstance(expected[key], str) or not _HASH.fullmatch(expected[key])
               for key in ("docker_sha256", "tmux_sha256")):
            _fail("profile-unsupported")
        support = _pins(profile["support_hashes"])
        state_prefix = profile["state_root"].rstrip("/") + "/plugins/"
        remaining = set(support) - {docker_path} - {p for p in support if p.startswith(state_prefix)}
        python = profile["python"]
        try:
            target = _resolve_selector(python)
        except (OSError, ValueError, RuntimeError):
            target = None
        if target in remaining:
            previous_python = target
        elif python in remaining:
            previous_python = python
        elif len(remaining) == 1:
            previous_python = next(iter(remaining))
        else:
            _fail("python-identity-unavailable")
        extras = {p: support[p] for p in remaining - {previous_python}}
        return {"tools": expected, "previous_python": previous_python,
                "extra_support": _retain_support(extras)}
    except InstallationUpdateError as error:
        if error.code == "installation-update-request-invalid":
            _fail("profile-unsupported")
        raise
    except (OSError, RuntimeError):
        _fail("tools-review-required")
    except (KeyError, TypeError, ValueError):
        _fail("profile-unsupported")


def prepare_installation_update(kind, profile_data, changes, *, runner=None):
    """Inspect a replacement without installing, saving or activating it.

    The caller must re-run this function immediately before save, compare the
    reviewed candidate digest, then atomically check the saved revision and old
    profile digest. Passing a client-supplied profile directly to save is unsafe.
    """
    request = build_update_request(kind, profile_data, changes)
    context = _local_support_context(profile_data) if kind == "local" else None
    options = {"expected_local_tools": context["tools"]} if context else {}
    result = prepare_connection(request, runner=runner, **options)
    if result.get("failure_code") == "installation-tools-changed":
        _fail("tools-review-required")
    if result.get("profile") is None:
        return result
    original = copy.deepcopy(profile_data)
    inspected = copy.deepcopy(result["profile"])
    try:
        inspected = (_local_candidate(original, inspected, context) if kind == "local"
                     else _remote_candidate(original, inspected))
    except InstallationUpdateError:
        raise
    except (KeyError, TypeError, ValueError):
        _fail("profile-unsupported")
    source_key = "source_hashes" if kind == "local" else "source_sha256"
    old_sources, new_sources = _pins(original[source_key]), _pins(inspected[source_key])
    added = len(new_sources.keys() - old_sources.keys())
    removed = len(old_sources.keys() - new_sources.keys())
    changed = sum(old_sources[p] != new_sources[p] for p in old_sources.keys() & new_sources.keys())
    summary = ["Saved connection, project folder, state and control settings remain unchanged.",
               f"Source fingerprint selection: {added} added, {removed} removed, {changed} changed. Added fingerprints can reflect more complete inspection rather than edited files.",
               "Previously selected installed plugin files are retained only when their bytes are unchanged; newly discovered installed plugins are not approved.",
               "No software is installed and no running session is changed by saving this review."]
    python_field = "python" if kind == "local" else "remote_python"
    if original[python_field] != inspected[python_field]:
        summary.append("The selected Python path changes. Review that installed interpreter as part of this update.")
    elif kind == "remote" and original["remote_python_sha256"] != inspected["remote_python_sha256"]:
        summary.append("The selected Python interpreter's bytes changed.")
    elif kind == "local" and original["support_hashes"] != inspected["support_hashes"]:
        summary.append("The selected Python interpreter target or bytes changed.")
    if original["source_root"] != inspected["source_root"]:
        summary.append("The selected Botainer source folder changes.")
    if kind == "remote":
        if original["launcher"] != inspected["launcher"]:
            summary.append("The selected Botainer launcher path or bytes changed.")
        summary.append("Review includes Botainer and the plugin code bundled with the selected source/package. Separately approved host hooks and installed state plugins are not expanded.")
        summary.append("The replacement uses a new remote history identity. Previous dashboard launch receipts remain archived; they are not transferred into the new connection.")
    else:
        summary.append("Previously approved local host-side hooks keep their exact paths and bytes; this update grants no new hook approval.")
    checks = [({"name": "Startup hooks", "status": "passed",
                "message": "Previous hook approvals are retained unchanged. Newly discovered hooks are not approved by this update; projects requiring them need connection setup review."}
               if kind == "local" and check.get("name") == "Startup hooks" else check)
              for check in result.get("checks", [])]
    return {**result, "profile": inspected, "candidate_sha256": _digest(inspected),
            "current_sha256": _digest(original), "summary": summary, "checks": checks,
            "requires_restart": True, "requires_pairing": True,
            "instructions": [
                "Review the exact installed code selection, then save. Saving does not activate the replacement.",
                "Finish or reconcile pending native CLI prompts before restarting the dashboard. Existing session owners are not replaced or automatically relaunched.",
                "Restart to activate the selection, then pair this browser again. A software update does not establish successful launch or reconnect testing on this machine."]}
