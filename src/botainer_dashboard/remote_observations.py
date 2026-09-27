"""Finite metadata for routine remote reads, separate from operation evidence.

No request or response body, path, exception text or SSH stderr enters this
journal. Logging failure must not turn a successful read into a remote failure.
Legacy cleanup only removes complete, successful, recognized read receipts;
operation/unknown/failed/malformed evidence stays in place for review.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import re
import stat
import threading
import time
import uuid

from .pairing import private_directory, read_private_json
from .ssh_diagnostics import connection_diagnostic


READ_ACTIONS = frozenset({'inventory', 'files', 'file', 'config', 'validate-config'})
ROUTINE_ACTIONS = READ_ACTIONS | {'receipt'}
MAX_RECEIPT = 4 * 1024 * 1024
MAX_ENTRIES = 64
MAX_BYTES = 65536
MAX_AGE = 7 * 24 * 60 * 60
SCAN_BUDGET = 64
SCAN_INTERVAL = 60
_LEGACY_NAME = re.compile(r'observation-[0-9a-f]{32}\Z')
_DIGEST = re.compile(r'[0-9a-f]{64}\Z')
_TRANSPORT = frozenset({'complete', 'timeout', 'start-error', 'interrupted', 'stdout-limit', 'stderr-limit', 'unknown'})
_OUTCOMES = frozenset({'success', 'transport-error', 'invalid-response', 'exception'})
_ENTRY_FIELDS = {'action', 'at', 'outcome', 'transport', 'returncode', 'stdout_bytes', 'stderr_bytes', 'diagnostic'}
_LEGACY_FILES = {'intent.json': 16384, 'result.json': 16384,
                 'stdout.bin': 4 * 1024 * 1024, 'stderr.bin': 65536}


def _require(ok):
    if not ok:
        raise ValueError('Remote diagnostic history requires review')


def _private(info, *, directory=False):
    _require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
             and info.st_uid == os.getuid() and not info.st_mode & 0o077
             and (directory or info.st_nlink == 1))


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _json(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            _require(key not in result)
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs)


def _timestamp(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 32503680000


class RemoteObservations:
    def __init__(self, root, profile, *, clock=time.time, max_entries=MAX_ENTRIES,
                 max_bytes=MAX_BYTES, max_age=MAX_AGE, scan_budget=SCAN_BUDGET):
        _require(_DIGEST.fullmatch(profile) and type(max_entries) is int and 1 <= max_entries <= MAX_ENTRIES
                 and type(max_bytes) is int and 1024 <= max_bytes <= MAX_BYTES
                 and type(max_age) is int and 1 <= max_age <= MAX_AGE
                 and type(scan_budget) is int and 1 <= scan_budget <= SCAN_BUDGET)
        self.root, self.profile = Path(root), profile
        self.path = self.root / 'routine-observations.json'
        self.clock, self.max_entries, self.max_bytes, self.max_age = clock, max_entries, max_bytes, max_age
        self.scan_budget = scan_budget
        self._lock = threading.Lock()
        self._scan = None
        self._next_scan = 0
        self._legacy = {'complete': False, 'examined': 0, 'removed': 0, 'retained': 0}
        self._status = 'not-yet-written'
        self._receipt_unavailable = False

    def status(self):
        with self._lock:
            return {'status': 'unavailable' if self._receipt_unavailable else self._status,
                    'legacy': dict(self._legacy)}

    @contextmanager
    def _disk_lock(self):
        descriptor = os.open(self.root / 'routine-observations.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            _private(os.fstat(descriptor))
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(descriptor)

    def _load(self):
        try:
            value = read_private_json(self.path, max_bytes=MAX_BYTES)
        except FileNotFoundError:
            return []
        _require(set(value) == {'version', 'profile', 'entries', 'legacy'} and value['version'] == 1
                 and value['profile'] == self.profile and isinstance(value['entries'], list)
                 and len(value['entries']) <= MAX_ENTRIES)
        for entry in value['entries']:
            _require(isinstance(entry, dict) and set(entry) == _ENTRY_FIELDS
                     and entry['action'] in ROUTINE_ACTIONS and _timestamp(entry['at'])
                     and entry['outcome'] in _OUTCOMES and entry['transport'] in _TRANSPORT
                     and (entry['returncode'] is None or type(entry['returncode']) is int and -4096 <= entry['returncode'] <= 4096)
                     and type(entry['stdout_bytes']) is int and 0 <= entry['stdout_bytes'] <= 4 * 1024 * 1024
                     and type(entry['stderr_bytes']) is int and 0 <= entry['stderr_bytes'] <= 65536
                     and (entry['diagnostic'] is None or entry['diagnostic'] == connection_diagnostic(entry['diagnostic'])['code']))
        legacy = value['legacy']
        _require(isinstance(legacy, dict) and set(legacy) == set(self._legacy)
                 and type(legacy['complete']) is bool
                 and all(type(legacy[key]) is int and 0 <= legacy[key] <= 1000000 for key in ('examined', 'removed', 'retained')))
        return value['entries']

    def _save(self, entries, now):
        entries = [entry for entry in entries if 0 <= now - entry['at'] <= self.max_age][-self.max_entries:]
        value = {'version': 1, 'profile': self.profile, 'entries': entries, 'legacy': dict(self._legacy)}
        while entries and len(json.dumps(value, separators=(',', ':'), allow_nan=False).encode()) + 1 > self.max_bytes:
            entries.pop(0)
        _require(len(json.dumps(value, separators=(',', ':'), allow_nan=False).encode()) + 1 <= self.max_bytes)
        self._write_journal(value)
        return entries

    def _write_journal(self, value):
        raw = (json.dumps(value, separators=(',', ':'), allow_nan=False) + '\n').encode()
        _require(len(raw) <= self.max_bytes)
        directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        pending = 'routine-observations.pending'
        try:
            _private(os.fstat(directory), directory=True)
            try:
                info = os.stat(pending, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                _private(info); _require(info.st_size <= MAX_BYTES)
                os.unlink(pending, dir_fd=directory)
            child = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            try:
                with os.fdopen(child, 'wb') as stream:
                    stream.write(raw); stream.flush(); os.fsync(stream.fileno())
                os.replace(pending, self.path.name, src_dir_fd=directory, dst_dir_fd=directory)
                os.fsync(directory)
            finally:
                try: os.unlink(pending, dir_fd=directory)
                except FileNotFoundError: pass
        finally:
            os.close(directory)

    def record(self, action, result, *, outcome, stdout_bytes=0, stderr_bytes=0, diagnostic=None):
        """Best-effort metadata only. All free-form input is excluded."""
        if action not in ROUTINE_ACTIONS:
            raise ValueError('Mutation evidence is not routine diagnostic history')
        with self._lock:
            try:
                now = round(self.clock(), 3)
                _require(_timestamp(now) and outcome in _OUTCOMES)
                code = result.get('returncode')
                entry = {'action': action, 'at': now, 'outcome': outcome,
                         'transport': result.get('transport_status') if result.get('transport_status') in _TRANSPORT else 'unknown',
                         'returncode': code if type(code) is int and -4096 <= code <= 4096 else None,
                         'stdout_bytes': min(max(int(stdout_bytes), 0), 4 * 1024 * 1024),
                         'stderr_bytes': min(max(int(stderr_bytes), 0), 65536),
                         'diagnostic': connection_diagnostic(diagnostic)['code'] if diagnostic is not None else None}
                with self._disk_lock():
                    entries = self._save([*self._load(), entry], now)
                    # Only after a usable journal exists may a proven legacy
                    # read be removed. An unreadable/full journal preserves it.
                    self._migrate_legacy(now)
                    self._save(entries, now)
                self._status = 'review-retained-legacy' if self._legacy['retained'] else 'ok'
            except (OSError, ValueError, TypeError, OverflowError):
                self._status = 'unavailable'
                if self._scan is not None:
                    self._scan.close(); self._scan = None

    def receipt(self, request, raw, scope):
        """Retain first/latest valid native replies, never one directory per poll.

        Original native operations and legacy reconciliation records are not
        touched. Failed, missing and malformed observations cannot erase a good
        snapshot. A journal/storage failure does not invent a remote outcome.
        """
        _require(isinstance(request, str) and str(uuid.UUID(request)) == request)
        _require(isinstance(raw, bytes) and len(raw) <= MAX_RECEIPT)
        value = _json(raw)
        if value == {'phase': 'unobserved'}:
            return
        self._receipt_scope(value, request, scope)
        with self._lock:
            try:
                with self._disk_lock():
                    root = private_directory(self.root / ('reconciliation-' + request))
                    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    try:
                        _private(os.fstat(directory), directory=True)
                        known = set()
                        with os.scandir(directory) as scan:
                            for item in scan:
                                _require(item.name in {'first.json', 'latest.json', 'pending.json'})
                                if item.name == 'pending.json':
                                    continue
                                previous = self._receipt_read(directory, item.name)
                                self._receipt_scope(_json(previous), request, scope)
                                known.add(item.name)
                        if 'first.json' not in known:
                            self._receipt_write(directory, 'first.json', raw)
                        self._receipt_write(directory, 'latest.json', raw)
                    finally:
                        os.close(directory)
                self._receipt_unavailable = False
            except (OSError, ValueError, TypeError):
                self._receipt_unavailable = True

    def _receipt_scope(self, value, request, scope):
        _require(isinstance(value, dict) and value.get('request_id') == request
                 and value.get('profile_fingerprint') == self.profile
                 and isinstance(value.get('operation'), str) and isinstance(value.get('phase'), str)
                 and value.get('operation') in {'start', 'init', 'stop'}
                 and value.get('phase') in {'entered', 'before-dispatch', 'dispatching', 'not-dispatched', 'failed', 'completed'})
        for key in ('operation', 'project_uuid', 'project_path', 'config_revision'):
            if key in scope:
                _require(value.get(key) == scope[key])

    @staticmethod
    def _receipt_read(directory, name):
        child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(child, 'rb') as stream:
            _private(os.fstat(stream.fileno()))
            value = stream.read(MAX_RECEIPT + 1)
        _require(len(value) <= MAX_RECEIPT)
        return value

    @staticmethod
    def _receipt_write(directory, name, raw):
        # A single fixed temporary slot bounds crash leftovers as well as the
        # normal files. No recursive cleanup or unrelated path can be selected.
        pending = 'pending.json'
        try:
            info = os.stat(pending, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            _private(info); os.unlink(pending, dir_fd=directory)
        child = os.open(pending, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            with os.fdopen(child, 'wb') as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            os.replace(pending, name, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            try: os.unlink(pending, dir_fd=directory)
            except FileNotFoundError: pass

    def _migrate_legacy(self, now):
        if self._scan is None:
            if now < self._next_scan:
                return
            self._scan = os.scandir(self.root)
            self._legacy = {'complete': False, 'examined': 0, 'removed': 0, 'retained': 0}
        for _ in range(self.scan_budget):
            try:
                item = next(self._scan)
            except StopIteration:
                self._scan.close(); self._scan = None
                self._next_scan = now + SCAN_INTERVAL
                self._legacy['complete'] = True
                return
            if not item.name.startswith('observation-'):
                continue
            self._legacy['examined'] = min(self._legacy['examined'] + 1, 1000000)
            key = 'removed' if self._remove_completed_read(item.name) else 'retained'
            self._legacy[key] = min(self._legacy[key] + 1, 1000000)

    def _remove_completed_read(self, name):
        """Never recurse, follow links, classify by name alone, or remove an op."""
        descriptor = None
        try:
            _require(_LEGACY_NAME.fullmatch(name))
            descriptor = os.open(self.root / name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK)
            directory = os.fstat(descriptor); _private(directory, directory=True)
            with os.scandir(descriptor) as scan:
                names = []
                for item in scan:
                    names.append(item.name)
                    _require(len(names) <= len(_LEGACY_FILES))
            _require(set(names) == set(_LEGACY_FILES))
            contents, identities = {}, {}
            for filename, limit in _LEGACY_FILES.items():
                child = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
                with os.fdopen(child, 'rb') as stream:
                    before = os.fstat(stream.fileno()); _private(before)
                    _require(before.st_size <= limit)
                    raw = stream.read(limit + 1)
                    _require(len(raw) <= limit and _identity(before) == _identity(os.fstat(stream.fileno())))
                identities[filename] = _identity(before)
                contents[filename] = raw
            intent, result, response = (_json(contents[filename]) for filename in ('intent.json', 'result.json', 'stdout.bin'))
            _require(isinstance(intent, dict) and set(intent) == {'action', 'profile', 'helper_sha256', 'retry'}
                     and intent['action'] in READ_ACTIONS and intent['profile'] == self.profile
                     and isinstance(intent['helper_sha256'], str) and _DIGEST.fullmatch(intent['helper_sha256'])
                     and intent['retry'] is False)
            _require(isinstance(result, dict) and result.get('transport_status') == 'complete'
                     and type(result.get('returncode')) is int and result['returncode'] == 0
                     and result.get('remote_outcome', 'helper-exited') == 'helper-exited'
                     and isinstance(response, dict))
            # Legacy success was JSON-object based. A completed read with an
            # unknown session state is still only a read; interrupted transport
            # or a reconciliation 'receipt' action is never eligible.
            for filename in names:
                info = os.stat(filename, dir_fd=descriptor, follow_symlinks=False)
                _private(info); _require(_identity(info) == identities[filename])
            _require(_identity(os.stat(self.root / name, follow_symlinks=False)) == _identity(directory))
            for filename in names:
                os.unlink(filename, dir_fd=descriptor)
            current = os.stat(self.root / name, follow_symlinks=False)
            _require((current.st_dev, current.st_ino) == (directory.st_dev, directory.st_ino))
            os.rmdir(self.root / name)
            return True
        except (OSError, ValueError, TypeError, KeyError):
            return False
        finally:
            if descriptor is not None:
                os.close(descriptor)
