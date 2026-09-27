"""Private, revision-checked selection of already reviewed runtime profiles.

This registry is a launcher input, not a controller. Changing a selection never
starts a process, connects to SSH, edits an imported profile, or stops a session.
The running service keeps its original selection until a deliberate restart.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat

from .backend_errors import BackendUnavailable
from .control_paths import LOCAL_CONTROL_ROOT_MESSAGES
from .launch_console import private_lock
from .pairing import private_directory, write_private_json


_LIMIT = 2 * 1024 * 1024
_KINDS = {"local", "remote", "host"}
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_ENTRY_FIELDS = {"id", "label", "kind", "profilePath", "profileDigest", "enabled"}
_MESSAGES = {
    "path-invalid": "Use a normal file path without symlinks or parent traversal.",
    "private-storage-required": "Connection files must be private, owner-owned regular files in a private directory.",
    "document-invalid": "The connection document is invalid or exceeds its size limit; restore a reviewed copy.",
    "profile-invalid": "The runtime profile could not be validated; check its fields, existing paths and source pins.",
    "profile-changed": "A stored runtime profile changed; restore its reviewed bytes before proceeding.",
    "profile-missing": "A stored runtime profile is missing; restore it before proceeding.",
    "trust-required": "Review the runtime profile and explicitly confirm trust before adding it.",
    "revision-conflict": "Connections changed in another window; reload and review before saving again.",
    "already-initialized": "Connections are already configured; use the saved registry without profile seed arguments.",
    "not-initialized": "Initialize the connection registry before using it.",
    "id-invalid": "Use a unique connection ID; local container profiles must use local, which is reserved.",
    "label-invalid": "Use a connection label of 1 to 160 printable characters.",
    "duplicate-id": "A connection with this ID already exists; choose a different ID or remove the old selection.",
    "target-conflict": "A connection already selects this account's state or control directory; remove the conflicting selection first.",
    "capacity": "At most one local, sixteen remote and eight host-agent connections may be registered.",
    "not-found": "This connection no longer exists; reload the connection list.",
    "busy": "Another connection update is in progress; reload and retry.",
    "request-invalid": "The connection change has invalid fields; reload and try again.",
    "host-agent-required": "Choose an existing Claude or Codex agent in a saved host connection.",
    "agent-not-found": "The installed agent was not found. Enter its absolute executable path, then inspect it again.",
    "agent-changed": "The executable changed since inspection. Inspect it again before saving.",
    "agent-unchanged": "This executable is already selected; no update is needed.",
    "installation-required": "Choose a saved local or remote Botainer connection. Host agents have a separate executable-update review.",
    "inspection-required": "Review and acknowledge the read-only inspection before checking the installed Botainer update.",
    "installation-changed": "The installation changed after review. Nothing was saved; inspect the update again.",
    "installation-unverified": "The installed update could not be verified. Review the connection check results before saving.",
    "installation-unchanged": "This installation is already selected; no update needs saving.",
}


class ConnectionRegistryError(ValueError):
    """A fixed public code and actionable text, never imported profile content."""

    def __init__(self, code, *, detail=None):
        self.code = "connections-" + code
        message = _MESSAGES[code]
        # BackendUnavailable deliberately permits only fixed public codes.
        if isinstance(detail, BackendUnavailable):
            control_hint = LOCAL_CONTROL_ROOT_MESSAGES.get(detail.code.removeprefix("ordinary-"))
            message += " " + control_hint if control_hint else " (" + detail.code + ")"
        super().__init__(message)


def _fail(code, *, detail=None):
    raise ConnectionRegistryError(code, detail=detail)


def _path(value):
    try:
        path = Path(value).absolute()
    except (TypeError, ValueError):
        _fail("path-invalid")
    if ".." in path.parts or any(ord(c) < 32 or ord(c) == 127 for c in str(path)):
        _fail("path-invalid")
    if any(component.is_symlink() for component in (path, *path.parents)):
        _fail("path-invalid")
    return path


def _json(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                _fail("document-invalid")
            value[key] = item
        return value
    try:
        value = json.loads(raw, object_pairs_hook=unique,
                           parse_constant=lambda _: _fail("document-invalid"))
    except (ValueError, UnicodeError, RecursionError):
        _fail("document-invalid")
    if not isinstance(value, dict):
        _fail("document-invalid")
    return value


def _encode(value):
    try:
        raw = (json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False) + "\n").encode()
    except (TypeError, ValueError, RecursionError):
        _fail("document-invalid")
    if len(raw) > _LIMIT:
        _fail("document-invalid")
    return raw


def _read_bytes(path):
    path = _path(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            before = os.fstat(handle.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                    or before.st_mode & 0o077 or before.st_nlink != 1):
                _fail("private-storage-required")
            if before.st_size > _LIMIT:
                _fail("document-invalid")
            raw = handle.read(_LIMIT + 1)
            after = os.fstat(handle.fileno())
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if (len(raw) > _LIMIT
                or any(getattr(before, field) != getattr(after, field) for field in fields)):
            _fail("document-invalid")
        return raw
    except FileNotFoundError:
        _fail("profile-missing")
    except OSError:
        _fail("private-storage-required")


def _new_bytes(path, raw):
    """Create an immutable private profile; callers never replace its contents."""
    path = _path(path)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError:
        _fail("private-storage-required")


def _loaders():
    # These loaders inspect files and pinned identities; none starts processes.
    from .host_backend import load_host_profile
    from .ordinary_local import load_local_profile
    from .remote_profile import load_remote_profile
    return {"local": load_local_profile, "remote": load_remote_profile, "host": load_host_profile}


class ConnectionRegistry:
    """A private launcher selection with immutable profile copies and CAS writes.

    ``loaders`` is an in-process test seam, never supplied by an HTTP request.
    ``initialize`` is the sole import-by-path operation and belongs to the CLI;
    GUI additions pass parsed profile data, not an arbitrary host file path.
    """

    def __init__(self, path: Path, *, loaders=None):
        self.path = _path(path)
        self.profile_directory = self.path.with_name(self.path.name + ".profiles")
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self._loaders = _loaders() if loaders is None else dict(loaders)
        if set(self._loaders) != _KINDS or any(not callable(v) for v in self._loaders.values()):
            raise ValueError("Provide a read-only validator for each connection kind")

    @contextmanager
    def _locked(self):
        _path(self.path)
        _path(self.lock_path)
        _path(self.profile_directory)
        try:
            private_directory(self.path.parent)
            private_directory(self.profile_directory)
            fd = private_lock(self.lock_path)
        except BlockingIOError:
            _fail("busy")
        except (OSError, ValueError, BackendUnavailable):
            _fail("private-storage-required")
        try:
            yield
        finally:
            os.close(fd)

    def _profile_path(self, kind, digest):
        return self.profile_directory / (kind + "-" + digest + ".json")

    def _metadata(self, kind, data, digest, *, label=None, enabled=True):
        if not isinstance(kind, str) or kind not in _KINDS or not isinstance(data, dict):
            _fail("profile-invalid")
        identifier = data.get("id")
        if (not isinstance(identifier, str) or not _ID.fullmatch(identifier)
                or (kind == "local") != (identifier == "local")):
            _fail("id-invalid")
        label = data.get("label") if label is None else label
        if (not isinstance(label, str) or not 1 <= len(label) <= 160
                or any(ord(c) < 32 or ord(c) == 127 for c in label)):
            _fail("label-invalid")
        return {"id": identifier, "label": label, "kind": kind,
                "profilePath": str(self._profile_path(kind, digest)),
                "profileDigest": digest, "enabled": enabled}

    def _validate_profile(self, kind, path):
        try:
            profile = self._loaders[kind](path)
            # The local schema loader checks selection; verify also checks every
            # existing source/tool pin without invoking them.
            if kind == "local":
                profile.verify()
        except (BackendUnavailable, ValueError, OSError, TypeError, KeyError, AttributeError) as error:
            _fail("profile-invalid", detail=error)
        return profile

    def _prepare(self, kind, raw):
        if not isinstance(kind, str) or kind not in _KINDS:
            _fail("request-invalid")
        data = _json(raw)
        if type(data.get("version")) is not int or data["version"] not in {1, 2}:
            _fail("profile-invalid")
        digest = hashlib.sha256(raw).hexdigest()
        entry = self._metadata(kind, data, digest)
        temporary = self.profile_directory / ("review-" + secrets.token_hex(16) + ".json")
        _new_bytes(temporary, raw)
        try:
            self._validate_profile(kind, temporary)
        finally:
            temporary.unlink(missing_ok=True)
        return entry, data, raw

    def _check_set(self, entries, data):
        if (len(entries) > 25 or sum(e["kind"] == "local" for e in entries) > 1
                or sum(e["kind"] == "remote" for e in entries) > 16
                or sum(e["kind"] == "host" for e in entries) > 8):
            _fail("capacity")
        if len({e["id"] for e in entries}) != len(entries):
            _fail("duplicate-id")
        scopes = set()
        for entry in entries:
            profile = data[entry["id"]]
            kind = entry["kind"]
            claims = []
            if kind == "remote":
                # Different aliases may resolve to the same account. Detect
                # exact-alias conflicts here; do not claim SSH alias identity is
                # independently verified without connecting to the remote.
                claims = [("remote-state", profile.get("ssh_alias"), profile.get("state_root")),
                          ("remote-control", profile.get("ssh_alias"), profile.get("control_root"))]
            elif kind == "host":
                claims = [("local-control", profile.get("control_root"))]
            elif isinstance(profile.get("terminal_owner"), dict):
                claims = [("local-control", profile["terminal_owner"].get("control_root"))]
            for claim in claims:
                if any(not isinstance(item, str) or not item for item in claim):
                    _fail("profile-invalid")
                if claim in scopes:
                    _fail("target-conflict")
                scopes.add(claim)

    def _read(self):
        if not self.path.exists() and not self.path.is_symlink():
            _fail("not-initialized")
        value = _json(_read_bytes(self.path))
        if (set(value) != {"version", "revision", "entries"}
                or type(value["version"]) is not int or value["version"] != 1
                or type(value["revision"]) is not int or not 0 <= value["revision"] < 2**53
                or not isinstance(value["entries"], list) or len(value["entries"]) > 25):
            _fail("document-invalid")
        data = {}
        for entry in value["entries"]:
            if (not isinstance(entry, dict) or set(entry) != _ENTRY_FIELDS
                    or not isinstance(entry.get("kind"), str) or entry["kind"] not in _KINDS
                    or type(entry.get("enabled")) is not bool
                    or not isinstance(entry.get("profileDigest"), str)
                    or not _HASH.fullmatch(entry["profileDigest"])):
                _fail("document-invalid")
            path = self._profile_path(entry["kind"], entry["profileDigest"])
            if entry["profilePath"] != str(path):
                _fail("document-invalid")
            raw = _read_bytes(path)
            if hashlib.sha256(raw).hexdigest() != entry["profileDigest"]:
                _fail("profile-changed")
            profile = _json(raw)
            if self._metadata(entry["kind"], profile, entry["profileDigest"],
                              label=entry["label"], enabled=entry["enabled"]) != entry:
                _fail("document-invalid")
            data[entry["id"]] = profile
        self._check_set(value["entries"], data)
        return value, data

    def _persist_profiles(self, prepared):
        for entry, _, raw in prepared:
            path = Path(entry["profilePath"])
            if path.exists() or path.is_symlink():
                if _read_bytes(path) != raw:
                    _fail("profile-changed")
            else:
                _new_bytes(path, raw)

    def _write(self, value):
        _encode(value)  # Enforce the same bound before touching durable state.
        try:
            write_private_json(self.path, value)
        except (OSError, ValueError):
            _fail("private-storage-required")
        return value

    @staticmethod
    def _revision(value, expected):
        if type(expected) is not int or expected != value["revision"]:
            _fail("revision-conflict")
        if expected >= 2**53 - 1:
            _fail("document-invalid")

    def initialize(self, entries=()):
        """Seed once from CLI-selected private files, preserving exact bytes.

        An existing registry is authoritative. Supplying seed arguments again is
        refused, including when they appear to match an earlier selection.
        """
        if not isinstance(entries, (list, tuple)) or len(entries) > 25:
            _fail("request-invalid")
        with self._locked():
            if self.path.exists() or self.path.is_symlink():
                if entries:
                    _fail("already-initialized")
                return self._read()[0]
            prepared = []
            for source in entries:
                if not isinstance(source, dict) or set(source) != {"kind", "path"}:
                    _fail("request-invalid")
                prepared.append(self._prepare(source["kind"], _read_bytes(source["path"])))
            selected = [entry for entry, _, _ in prepared]
            data = {entry["id"]: profile for entry, profile, _ in prepared}
            self._check_set(selected, data)
            self._persist_profiles(prepared)
            return self._write({"version": 1, "revision": 0, "entries": selected})

    def read(self):
        """Read without creating any directory, file or lock.

        Profiles are immutable and retained, and the manifest is replaced
        atomically. A reader therefore sees a complete old or new selection
        without needing the mutation lock. This also supports CLI dry runs.
        """
        for directory in (self.path.parent, self.profile_directory):
            path = _path(directory)
            try:
                info = path.stat()
            except FileNotFoundError:
                _fail("not-initialized")
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077):
                _fail("private-storage-required")
        return self._read()[0]

    def add(self, kind, profile: dict, expected_revision, trusted=False):
        if trusted is not True:
            _fail("trust-required")
        raw = _encode(profile)
        with self._locked():
            value, data = self._read()
            self._revision(value, expected_revision)
            prepared = self._prepare(kind, raw)
            entry, profile, _ = prepared
            entries = [*value["entries"], entry]
            self._check_set(entries, {**data, entry["id"]: profile})
            self._persist_profiles([prepared])
            return self._write({**value, "revision": value["revision"] + 1, "entries": entries})

    def enable(self, identifier, enabled, expected_revision):
        if not isinstance(identifier, str) or type(enabled) is not bool:
            _fail("request-invalid")
        with self._locked():
            value, _ = self._read()
            self._revision(value, expected_revision)
            entry = next((entry for entry in value["entries"] if entry["id"] == identifier), None)
            if entry is None:
                _fail("not-found")
            if enabled:
                self._validate_profile(entry["kind"], Path(entry["profilePath"]))
            entry["enabled"] = enabled
            return self._write({**value, "revision": value["revision"] + 1})

    def remove(self, identifier, expected_revision):
        """Remove selection only; retain profile, runtime and recovery files."""
        if not isinstance(identifier, str):
            _fail("request-invalid")
        with self._locked():
            value, _ = self._read()
            self._revision(value, expected_revision)
            entries = [entry for entry in value["entries"] if entry["id"] != identifier]
            if len(entries) == len(value["entries"]):
                _fail("not-found")
            return self._write({**value, "revision": value["revision"] + 1, "entries": entries})

    def host_agent_metadata(self, entry):
        """Describe saved selections without probing or executing installed tools.

        This also works in settings-only recovery when tmux or roots are missing.
        Runtime snapshots separately report each agent's executable availability.
        """
        if entry["kind"] != "host":
            return []
        raw = _read_bytes(self._profile_path("host", entry["profileDigest"]))
        if hashlib.sha256(raw).hexdigest() != entry["profileDigest"]:
            _fail("profile-changed")
        agents = _json(raw).get("agents", {})
        if not isinstance(agents, dict):
            _fail("profile-invalid")
        result = []
        for name, tool in agents.items():
            if (name not in {"claude", "codex"} or not isinstance(tool, dict)
                    or not all(isinstance(tool.get(key), str) for key in ("path", "sha256"))):
                _fail("profile-invalid")
            result.append({"id": name, "label": tool.get("label", name.title()),
                           "path": tool["path"], "sha256": tool["sha256"]})
        return result

    def _agent_update_context(self, identifier, agent, expected_revision):
        if not isinstance(identifier, str) or not isinstance(agent, str) or agent not in {"claude", "codex"}:
            _fail("host-agent-required")
        value, _ = self._read()
        self._revision(value, expected_revision)
        entry = next((e for e in value["entries"] if e["id"] == identifier), None)
        if entry is None:
            _fail("not-found")
        if entry["kind"] != "host":
            _fail("host-agent-required")
        profile = self._validate_profile("host", Path(entry["profilePath"]))
        if profile.fingerprint != entry["profileDigest"]:
            _fail("profile-changed")
        if agent not in profile.data["agents"]:
            _fail("host-agent-required")
        return value, entry, profile

    @staticmethod
    def _agent_candidate(profile, agent, path):
        """Inspect one executable; discovery never runs it or searches project data."""
        from .host_backend import _hash
        if not isinstance(path, str) or len(path) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in path):
            _fail("path-invalid")
        if not path:
            # Fixed agent name and already reviewed, absolute PATH entries.
            # No caller-provided command, shell expansion or version execution.
            path = shutil.which(agent, path=profile.data["environment"]["PATH"])
            if path is None:
                _fail("agent-not-found")
        try:
            requested = Path(path)
            if not requested.is_absolute() or ".." in requested.parts:
                _fail("path-invalid")
            resolved = requested.resolve(strict=True)
            if not resolved.is_file() or not os.access(resolved, os.X_OK):
                _fail("agent-not-found")
            digest = _hash(resolved)
            # A symlink (e.g. a package manager's bin entry) may resolve to the
            # versioned binary. Pin that exact target, and refuse a raced link.
            if requested.resolve(strict=True) != resolved:
                _fail("agent-changed")
        except ConnectionRegistryError:
            raise
        except (OSError, ValueError, BackendUnavailable):
            _fail("agent-not-found")
        return {"path": str(resolved), "sha256": digest}

    def prepare_agent_update(self, identifier, agent, path, expected_revision):
        """Read-only review of a replacement; no state/profile changes or execution."""
        value, _entry, profile = self._agent_update_context(identifier, agent, expected_revision)
        candidate = self._agent_candidate(profile, agent, path)
        tool = profile.data["agents"][agent]
        current = {key: tool[key] for key in ("path", "sha256")}
        return {"id": identifier, "agent": agent, "label": tool.get("label", agent.title()),
                "revision": value["revision"], "current": current, "candidate": candidate,
                "changed": candidate != current,
                "notice": "Only this agent's executable changes. Projects, session history and browser pairing are retained. Save, then restart the dashboard to use it for new sessions. Existing sessions keep their original terminal owner. No software is installed or executed by this check."}

    def update_agent(self, identifier, agent, path, sha256, expected_revision, *, confirmed=False):
        """Commit exactly the inspected agent bytes as an immutable revision.

        The original private profile proves unchanged account/folder/control
        scope. Its digest remains the state and browser authorization identity.
        No running controller or terminal owner is replaced by this operation.
        """
        if confirmed is not True:
            _fail("trust-required")
        if not isinstance(sha256, str) or not _HASH.fullmatch(sha256) or not isinstance(path, str) or not path:
            _fail("request-invalid")
        with self._locked():
            value, entry, profile = self._agent_update_context(identifier, agent, expected_revision)
            candidate = self._agent_candidate(profile, agent, path)
            if candidate != {"path": path, "sha256": sha256}:
                _fail("agent-changed")
            tool = profile.data["agents"][agent]
            if all(tool[key] == candidate[key] for key in candidate):
                _fail("agent-unchanged")
            data = {**profile.data, "version": 2,
                    "original_profile": {"path": str(profile.original_path), "sha256": profile.identity_fingerprint},
                    "agents": {**profile.data["agents"], agent: {**tool, **candidate}}}
            prepared = self._prepare("host", _encode(data))
            # Revalidate the exact chosen bytes after building/validating the
            # revision too. Another agent may remain stale and be reviewed next.
            checked = self._validate_profile("host", Path(entry["profilePath"]))
            if self._agent_candidate(checked, agent, path) != candidate:
                _fail("agent-changed")
            new_entry = {**prepared[0], "label": entry["label"], "enabled": entry["enabled"]}
            self._persist_profiles([prepared])
            entries = [new_entry if e["id"] == identifier else e for e in value["entries"]]
            return self._write({**value, "revision": value["revision"] + 1, "entries": entries})

    def _installation_context(self, identifier, expected_revision=None, profile_digest=None):
        """Read the retained selection without requiring its old files to exist."""
        if not isinstance(identifier, str):
            _fail("installation-required")
        value, profiles = self._read()
        if expected_revision is not None:
            self._revision(value, expected_revision)
        entry = next((item for item in value['entries'] if item['id'] == identifier), None)
        if entry is None:
            _fail("not-found")
        if entry['kind'] not in {'local', 'remote'}:
            _fail("installation-required")
        if profile_digest is not None and profile_digest != entry['profileDigest']:
            _fail("revision-conflict")
        return value, entry, profiles

    def installation_update_settings(self, identifier):
        value, entry, profiles = self._installation_context(identifier)
        profile = profiles[identifier]
        settings = {'python': profile.get('python' if entry['kind'] == 'local' else 'remote_python'),
                    'source_root': profile.get('source_root')}
        if entry['kind'] == 'remote':
            settings['launcher'] = profile.get('launcher', {}).get('path')
        if not all(isinstance(path, str) for path in settings.values()):
            _fail('profile-invalid')
        return {'id': identifier, 'kind': entry['kind'], 'revision': value['revision'],
                'profileDigest': entry['profileDigest'], 'settings': settings}

    def prepare_installation_update(self, identifier, changes, expected_revision, profile_digest,
                                    *, acknowledged=False):
        if acknowledged is not True:
            _fail('inspection-required')
        if (type(expected_revision) is not int or not isinstance(profile_digest, str)
                or not _HASH.fullmatch(profile_digest)):
            _fail('request-invalid')
        value, entry, profiles = self._installation_context(identifier, expected_revision, profile_digest)
        from .installation_update import prepare_installation_update
        result = prepare_installation_update(entry['kind'], profiles[identifier], changes)
        # Inspection can involve a slow remote read. A newer selection must not
        # accidentally inherit the review of the old account/installation.
        self._installation_context(identifier, expected_revision, profile_digest)
        return {**result, 'id': identifier, 'revision': value['revision'],
                'profileDigest': entry['profileDigest'],
                'candidateDigest': result.get('candidate_sha256'),
                'changed': isinstance(result.get('profile'), dict) and result['profile'] != profiles[identifier],
                'requiresRestart': result.get('requires_restart', True),
                'requiresPairing': result.get('requires_pairing', True),
                'preservesRemoteHistory': entry['kind'] != 'remote'}

    def update_installation(self, identifier, changes, candidate_digest, expected_revision,
                            profile_digest, *, acknowledged=False, confirmed=False):
        """Reinspect and save an exact reviewed update; never activate it here.

        Source/target mutation after the final inspection remains detectable by
        the runtime's normal verification. No old remote receipt or browser
        grant is rewritten to the new revision.
        """
        if confirmed is not True:
            _fail('trust-required')
        if not isinstance(candidate_digest, str) or not _HASH.fullmatch(candidate_digest):
            _fail('request-invalid')
        result = self.prepare_installation_update(identifier, changes, expected_revision,
                                                 profile_digest, acknowledged=acknowledged)
        if not isinstance(result.get('profile'), dict):
            _fail('installation-unverified')
        raw = _encode(result['profile'])
        if result.get('candidateDigest') != candidate_digest or hashlib.sha256(raw).hexdigest() != candidate_digest:
            _fail('installation-changed')
        with self._locked():
            value, old_entry, profiles = self._installation_context(identifier, expected_revision, profile_digest)
            if result['profile'] == profiles[identifier]:
                _fail('installation-unchanged')
            prepared = self._prepare(old_entry['kind'], raw)
            new_entry = {**prepared[0], 'label': old_entry['label'], 'enabled': old_entry['enabled']}
            if new_entry['id'] != identifier:
                _fail('installation-changed')
            entries = [new_entry if item['id'] == identifier else item for item in value['entries']]
            self._check_set(entries, {**profiles, identifier: prepared[1]})
            self._persist_profiles([prepared])
            return self._write({**value, 'revision': value['revision'] + 1, 'entries': entries})

    def selected_profiles(self):
        """Desired paths for the next launcher; this is not live activation."""
        entries = [entry for entry in self.read()["entries"] if entry["enabled"]]
        local = next((Path(entry["profilePath"]) for entry in entries if entry["kind"] == "local"), None)
        return {"local_profile": local,
                "remote_profiles": tuple(Path(e["profilePath"]) for e in entries if e["kind"] == "remote"),
                "host_profiles": tuple(Path(e["profilePath"]) for e in entries if e["kind"] == "host")}
