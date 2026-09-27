#!/usr/bin/env python3
"""Fixed installed-Botainer actions transported by the trusted local backend.

There is deliberately no shell/command request. Native launch runs Botainer's
CLI, plugin dispatcher and HPC helper. The two known subprocess boundaries are
executed in this dedicated process solely to instrument the sbatch receipt;
their original argv/environment, warnings, consent and cleanup remain native.
The helper is dashboard code, not a modification of the installed Botainer.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import selectors
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import types
import uuid

SID = re.compile(r'[0-9a-f]{16}\Z')
JOB = re.compile(r'[0-9]{1,20}\Z')
TERMINAL = {'COMPLETED', 'CANCELLED', 'FAILED', 'TIMEOUT', 'OUT_OF_MEMORY',
            'NODE_FAIL', 'PREEMPTED', 'BOOT_FAIL', 'DEADLINE'}
MAX_TEXT = 65536
MAX_CURRENT_JOB_CHECKS = 8
MAX_CURRENT_JOB_SECONDS = 24


def require(condition, message):
    if not condition: raise ValueError(message)


def canonical_uuid(value):
    require(isinstance(value, str) and str(uuid.UUID(value)) == value, 'Invalid UUID')
    return value


def regular(path, limit=2 * 1024 * 1024):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'Expected a regular single-link file')
        value = stream.read(limit + 1)
    require(len(value) <= limit, 'File exceeds the read bound')
    return value


def private_directory(path, *, create=False):
    path = Path(path)
    if create: path.mkdir(mode=0o700, parents=False, exist_ok=True)
    require(path.resolve(strict=True) == path, 'Private directory contains a symlink')
    info = path.stat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077,
            'Control directory is not private to this account')
    return path


def atomic_json(path, value):
    raw = (json.dumps(value, sort_keys=True, ensure_ascii=True) + '\n').encode()
    fd, name = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        try: os.unlink(name)
        except FileNotFoundError: pass


@contextmanager
def lock(path):
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid()
                and not info.st_mode & 0o077, 'Invalid control lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally: os.close(fd)


def trusted_environment(profile):
    account = pwd.getpwuid(os.getuid())
    result = {k: v for k, v in os.environ.items()
              if k in {'PATH', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TERM', 'COLORTERM',
                       'MODULEPATH', 'LOADEDMODULES', '_LMFILES_', 'LMOD_CMD', 'LMOD_DIR'}
              or k.startswith('LMOD_')}
    paths = result.get('PATH', '/usr/local/bin:/usr/bin:/bin').split(':')
    require(all(p.startswith('/') and Path(p).resolve() != Path.cwd().resolve() for p in paths),
            'Remote PATH includes a relative or current-directory entry')
    result.update(HOME=account.pw_dir, USER=account.pw_name, LOGNAME=account.pw_name,
                  SHELL=account.pw_shell, MY_BOTAINER=profile['state_root'],
                  TERM='xterm-256color', PYTHONDONTWRITEBYTECODE='1')
    return result


class VerificationRefused(ValueError):
    """Fixed advisory category; private exception detail stays on stderr."""

    def __init__(self, code, detail):
        super().__init__(detail)
        self.diagnostic_code = code


@contextmanager
def verification_stage(code, policy):
    """Preserve refusals while labeling which saved identity could not verify."""
    try:
        yield
    except VerificationRefused:
        raise
    except policy.ImportPolicyError as exc:
        if str(exc) in {'import-path-owner-untrusted', 'import-path-other-account-writable'}:
            category = 'remote-installation-unsafe'
        elif str(exc) in {'import-path-not-canonical', 'import-package-invalid',
                          'import-package-directory-symlink', 'import-package-inspection-limit',
                          'import-package-unreviewed-executable'}:
            category = 'remote-import-layout-unsupported'
        else:
            category = 'remote-check-failed'
        raise VerificationRefused(category, str(exc)) from exc
    except PermissionError as exc:
        raise VerificationRefused('remote-installation-unsafe', str(exc)) from exc
    except (ValueError, FileNotFoundError) as exc:
        raise VerificationRefused(code, str(exc)) from exc


def verify(profile):
    source = Path(profile['source_root'])
    policy = module_from_bundle('import_policy.py')
    with verification_stage('remote-source-changed', policy):
        require(source.resolve(strict=True) == source and source.is_dir(), 'Botainer source identity changed')
    if profile['version'] == 2:
        layout = module_from_bundle('installation_layout.py')
        with verification_stage('remote-source-changed', policy):
            try:
                layout.verify_layout(source, profile['installation_layout'], profile['source_sha256'])
            except layout.InstallationLayoutError as exc:
                code = ('remote-source-changed' if str(exc) == 'Installed Botainer metadata pins changed'
                        else 'remote-import-layout-unsupported')
                raise VerificationRefused(code, str(exc)) from exc
    with verification_stage('remote-source-changed', policy):
        actual = policy.source_modules(source)
        expected = {name for name in profile['source_sha256'] if name.startswith('botainer/') and name.endswith('.py')}
        require(actual == expected, 'Installed Botainer module set changed')
    launcher = profile['launcher']
    with verification_stage('remote-launcher-changed', policy):
        policy.trusted_path(Path(launcher['path']))
        require(hashlib.sha256(regular(launcher['path'])).hexdigest() == launcher['sha256'],
                'Selected Botainer launcher changed')
    with verification_stage('remote-source-changed', policy):
        for name, expected in profile['source_sha256'].items():
            path = source / name
            require(path.resolve(strict=True) == path and path.is_relative_to(source), 'Source pin escaped its root')
            policy.trusted_path(path)
            require(hashlib.sha256(regular(path)).hexdigest() == expected, 'Pinned Botainer source changed: ' + name)
    with verification_stage('remote-interpreter-changed', policy):
        require(Path(profile['remote_python']).resolve(strict=True) == Path(sys.executable).resolve(strict=True),
                'Selected interpreter changed')
        policy.trusted_path(Path(sys.executable).resolve(strict=True))
        require(hashlib.sha256(regular(Path(sys.executable).resolve(), 64 * 1024 * 1024)).hexdigest()
                == profile['remote_python_sha256'], 'Selected interpreter bytes changed')
    with verification_stage('remote-plugins-changed', policy):
        for name, expected in profile['state_plugin_sha256'].items():
            path = Path(profile['state_root']) / name
            require(path.resolve(strict=True) == path, 'Installed plugin pin escaped its root')
            policy.trusted_path(path)
            require(hashlib.sha256(regular(path)).hexdigest() == expected, 'Installed plugin source changed')
    os.environ.clear(); os.environ.update(trusted_environment_cache(profile))
    sys.path.insert(0, str(source))
    import botainer
    require(Path(botainer.__file__).resolve() == source / 'botainer/__init__.py', 'Wrong Botainer import origin')
    from botainer.state import dir as state_dir
    require(state_dir.ensure_user_state_dir(create_if_missing=False).root.resolve() == Path(profile['state_root']).resolve(),
            'Selected Botainer state root changed')


_ENV = None
def trusted_environment_cache(profile):
    require(_ENV is not None, 'Remote environment was not prepared')
    return dict(_ENV)


def bounded(argv, *, cwd, timeout=20, limit=2 * 1024 * 1024):
    process = subprocess.Popen(argv, cwd=cwd, env=dict(os.environ), stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    result = [bytearray(), bytearray()]
    selector = selectors.DefaultSelector()
    try:
        for index, stream in enumerate((process.stdout, process.stderr)):
            os.set_blocking(stream.fileno(), False); selector.register(stream, selectors.EVENT_READ, index)
        deadline = time.monotonic() + timeout
        while selector.get_map():
            require(time.monotonic() < deadline, 'Remote command timed out')
            for key, _event in selector.select(.1):
                data = os.read(key.fileobj.fileno(), 65536)
                if not data: selector.unregister(key.fileobj); continue
                result[key.data].extend(data)
                require(len(result[key.data]) <= limit, 'Remote command output exceeds bound')
        code = process.wait(timeout=1)
        return code, bytes(result[0]), bytes(result[1])
    finally:
        selector.close()
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=2)
        process.stdout.close(); process.stderr.close()


def cli_argv(profile, *args):
    # The reviewed release-local interpreter resolves this exact installation
    # for all native child modules as well as this process. No project cwd import.
    return [profile['remote_python'], '-I', '-B',
            *module_from_bundle('import_policy.py').cache_arguments(), '-m', 'botainer.cli.main', *args]


def catalog(profile):
    code, out, _err = bounded(cli_argv(profile, 'list', '--json'), cwd=Path.home())
    require(code == 0, 'Botainer project discovery failed')
    entries = json.loads(out)
    catalog_index(entries)
    return entries


def catalog_index(entries):
    """Native UUID/path associations must be unambiguous before any control."""
    require(isinstance(entries, list) and len(entries) <= 2000, 'Invalid Botainer project inventory')
    index, paths = {}, set()
    for entry in entries:
        require(isinstance(entry, dict), 'Invalid Botainer project inventory')
        uid = canonical_uuid(entry['uuid'])
        path = entry.get('last_path')
        require(isinstance(path, str) and path, 'Invalid registered project path')
        require(uid not in index and path not in paths, 'Duplicate project identity or path')
        index[uid] = entry; paths.add(path)
    return index


def project_path(profile, data, *, registered=True, _catalog=None):
    """Use native registration for existing projects; roots bound new setup.

    ``_catalog`` is an internal already-validated inventory index. Fixed remote
    actions never accept it from request data; they read the native catalog
    afresh. Inventory alone shares its one complete bounded catalog observation.
    """
    path = Path(data['project_path'])
    require(path.is_absolute() and path.resolve(strict=True) == path and path.is_dir(), 'Project path changed')
    if not registered:
        roots = [Path(v) for v in profile['project_roots'].values()]
        require(all(root.resolve(strict=True) == root and root.is_dir() for root in roots), 'Registered project root changed')
        require(any(path != root and path.is_relative_to(root) for root in roots), 'Project is outside registered roots')
    for name in ('source_root', 'state_root', 'control_root'):
        protected = Path(profile[name])
        require(not path.is_relative_to(protected) and not protected.is_relative_to(path), 'Project overlaps protected control state')
    require(path.stat().st_uid == os.getuid(), 'Project is owned by another account')
    if registered:
        uid = canonical_uuid(data['project_uuid'])
        require((path / '.botainer').resolve(strict=True) == path / '.botainer', 'Project metadata is a symlink')
        require(regular(path / '.botainer/project-id', 256).decode().strip() == uid, 'Project UUID changed')
        index = catalog_index(catalog(profile)) if _catalog is None else _catalog
        require(uid in index and index[uid]['last_path'] == str(path), 'Project registration changed')
    return path


def session_record(profile, uid, sid, *, require_apptainer=True):
    canonical_uuid(uid); require(isinstance(sid, str) and SID.fullmatch(sid), 'Invalid session ID')
    folder = Path(profile['state_root']) / 'state' / uid / 'sessions' / sid
    require(folder.resolve(strict=True) == folder, 'Session path changed')
    record = json.loads(regular(folder / 'spec.json'))
    require(isinstance(record, dict) and record.get('session_id') == sid and record.get('project_uuid') == uid,
            'Session identity changed')
    require(not require_apptainer or record.get('runtime') == 'apptainer', 'Session runtime changed')
    return record


def scheduler_fields(job, *, timeout=12):
    code, out, _err = bounded(['scontrol', 'show', 'job', '-o', job], cwd=Path.home(), timeout=timeout, limit=65536)
    if code != 0: return None
    return dict(re.findall(r'(?:^|\s)([A-Za-z][A-Za-z0-9_]*)=(.*?)(?=\s[A-Za-z][A-Za-z0-9_]*=|$)', out.decode().strip()))


def scheduler(profile, record, *, observations=None):
    handle = (record.get('runtime_handle') or {}).get('apptainer') or {}
    job = str(handle.get('slurm_jobid') or '')
    if not JOB.fullmatch(job): return {'state': 'unknown', 'reason': 'No recorded scheduler job'}
    fields = scheduler_fields(job) if observations is None else observations.get(job)
    if fields is None: return {'state': 'unknown', 'job_id': job, 'reason': 'Scheduler observation unavailable'}
    account = pwd.getpwuid(os.getuid()).pw_name
    require(fields.get('JobId') == job and fields.get('UserId') == f'{account}({os.getuid()})', 'Scheduler account/job changed')
    require(fields.get('WorkDir') == record['project_root'], 'Scheduler project changed')
    scripts = Path(profile['state_root']) / 'state' / record['project_uuid'] / 'sessions' / '_submit-scripts'
    command = Path(fields.get('Command', ''))
    require(command.parent == scripts and command.name.startswith('submit-') and command.suffix == '.sh',
            'Scheduler submission provenance changed')
    # A matching prefix is insufficient. Bind the exact session directory and
    # full UUID present in the native batch script to this recorded scheduler job.
    script = regular(command, 1024 * 1024).decode()
    exact_session = str(scripts.parent / record['session_id'])
    require(exact_session in script and record['project_uuid'] in script, 'Batch script session identity changed')
    raw = fields.get('JobState', '')
    return {'job_id': job, 'state': 'stopped' if raw in TERMINAL else 'running' if raw == 'RUNNING'
            else 'queued' if raw in {'PENDING', 'CONFIGURING', 'SUSPENDED'} else 'unknown',
            'scheduler_state': raw, 'node': fields.get('BatchHost'), 'reason': fields.get('Reason'),
            'started_at': fields.get('StartTime'), 'ended_at': fields.get('EndTime') if raw in TERMINAL else None,
            'time_limit': fields.get('TimeLimit')}


def active_scheduler_jobs():
    """One complete bounded account queue, separate from session ownership.

    Absence from this successful observation means not currently in the queue,
    never a known stop time/outcome. Failed, malformed or truncated output cannot
    turn old records into ended sessions. Array/heterogeneous IDs are accepted
    as queue rows, although ordinary session handles currently use base job IDs.
    """
    account = pwd.getpwuid(os.getuid()).pw_name
    try:
        code, out, err = bounded(['squeue', '--noheader', '--array', '--all', '--states=all', '--user', account,
            '--format=%i|%u|%T'], cwd=Path.home(), timeout=12, limit=65536)
        require(code == 0 and not err.strip() and (not out or out.endswith(b'\n')), 'Queue observation incomplete')
        lines = out.decode().splitlines(); require(len(lines) <= 4096, 'Queue row bound exceeded')
        jobs = {}; seen = set()
        for line in lines:
            fields = [value.strip() for value in line.split('|')]
            require(len(fields) == 3, 'Queue row malformed')
            job, user, state = fields
            require(re.fullmatch(r'[0-9]{1,20}(?:_[0-9]{1,20})?(?:\+[0-9]{1,5})?', job)
                    and user == account and re.fullmatch(r'[A-Z][A-Z_]{0,39}', state)
                    and job not in seen, 'Queue identity changed')
            seen.add(job)
            jobs[job] = state
            base = re.split(r'[_+]', job, maxsplit=1)[0]
            if base != job:
                jobs[base] = state if jobs.get(base, state) == state else 'UNKNOWN'
        return jobs
    except (ValueError, OSError, UnicodeError, subprocess.TimeoutExpired):
        return None


def record_agent(record):
    spec = record.get('spec') or {}
    if not isinstance(spec, dict): return ''
    if spec.get('agent') in {'claude', 'codex'}: return spec['agent']
    plugins = spec.get('plugins_enabled') or []
    if not isinstance(plugins, list): return ''
    # Older SessionSpec records omit agent, but retain the actual composed
    # plugin selection, including CLI overrides. Do not use current config.
    agents = {agent for agent in ('claude', 'codex') for plugin in plugins
              if isinstance(plugin, str) and (plugin == 'agent-' + agent or plugin.startswith('agent-' + agent + '-'))}
    return next(iter(agents)) if len(agents) == 1 else ''


def inventory(profile):
    projects, sessions, records = [], [], []
    native_projects = catalog_index(catalog(profile))
    for row in native_projects.values():
        uid = row['uuid']; path = row.get('last_path', '')
        item = {'uuid': uid, 'path': path, 'name': row.get('display_name') or Path(path).name,
                'last_launch_at': row.get('last_session_at'), 'verified': False, 'path_identity_verified': False}
        # Registry path identity is an observation. The separate project_path
        # check remains the authority for files/config/launch/attach controls.
        try:
            project = Path(path)
            require(project.is_absolute() and project.resolve(strict=True) == project and project.is_dir()
                    and project.stat().st_uid == os.getuid(), 'Project path changed')
            require((project / '.botainer').resolve(strict=True) == project / '.botainer', 'Project metadata changed')
            require(regular(project / '.botainer/project-id', 256).decode().strip() == uid, 'Project UUID changed')
            item['path_identity_verified'] = True
        except (ValueError, OSError, UnicodeError): pass
        try:
            project = project_path(profile, {'project_path': path, 'project_uuid': uid}, _catalog=native_projects)
            import yaml
            try: cfg = yaml.safe_load(regular(project / '.botainer/config.yaml', MAX_TEXT))
            except yaml.YAMLError as exc: raise ValueError('Project configuration is invalid YAML') from exc
            if cfg is None: cfg = {}
            require(isinstance(cfg, dict), 'Project configuration must be a mapping')
            item['agent'] = cfg.get('agent', '')
            item['verified'] = True
        except (ValueError, OSError, UnicodeError):
            item['control_restriction'] = 'project-registration-unavailable'
        projects.append(item)
        directory = Path(profile['state_root']) / 'state' / uid / 'sessions'
        if not directory.is_dir(): continue
        require(directory.resolve() == directory, 'Session inventory path is a symlink')
        children = list(directory.iterdir()); require(len(children) <= 4000, 'Session inventory bound exceeded')
        for child in children:
            if not SID.fullmatch(child.name): continue
            try:
                rec = session_record(profile, uid, child.name, require_apptainer=False)
                handle = (rec.get('runtime_handle') or {}).get('apptainer') or {}
                job = str(handle.get('slurm_jobid') or '')
                value = {'session_id': child.name, 'project_uuid': uid, 'agent': record_agent(rec),
                                 'started_at': rec.get('started_at'), 'ended_at': rec.get('ended_at'),
                                 'runtime': rec.get('runtime'), 'screen': rec.get('screen_session_id'),
                                 'state': 'unknown', 'scheduler_observation': 'unavailable'}
                if JOB.fullmatch(job): value['job_id'] = job
                sessions.append(value); records.append((rec, value, rec.get('project_root') == path))
            except (ValueError, OSError, UnicodeError, TypeError, AttributeError):
                sessions.append({'session_id': child.name, 'project_uuid': uid, 'state': 'unknown',
                                 'scheduler_observation': 'unavailable', 'identity_unavailable': True,
                                 'reason': 'Session or scheduler identity unavailable'})
            require(len(sessions) <= 10000, 'Session inventory total bound exceeded')
    queue = active_scheduler_jobs() if records else {}
    # Inspect each matched current allocation at most once, with both count and
    # total-time caps. Historical sessions never cause per-record Slurm calls.
    observations = {}; deadline = time.monotonic() + MAX_CURRENT_JOB_SECONDS
    current = sorted({value['job_id'] for rec, value, _ in records
                      if rec.get('runtime') == 'apptainer' and value.get('job_id') in (queue or {})},
                     key=lambda job: (queue[job] in TERMINAL, job))
    for job in current[:MAX_CURRENT_JOB_CHECKS]:
        remaining = deadline - time.monotonic()
        if remaining <= 0: break
        try: observations[job] = scheduler_fields(job, timeout=min(6, remaining))
        except (ValueError, OSError, UnicodeError, subprocess.TimeoutExpired): observations[job] = None
    for rec, value, same_path in records:
        job = value.get('job_id')
        if rec.get('runtime') != 'apptainer':
            value['reason'] = 'Recorded session runtime is not Apptainer'; continue
        if not job:
            value['reason'] = 'No recorded scheduler job'; continue
        if queue is None:
            value['reason'] = 'Scheduler observation unavailable'; continue
        if job not in queue:
            value.update(scheduler_observation='not-active',
                         reason='Recorded allocation is not in the current queue; end time and outcome are unknown')
            continue
        terminal = queue[job] in TERMINAL
        value.update(scheduler_observation='not-active' if terminal else 'active', scheduler_state=queue[job])
        def unverified_reason(reason):
            return (f'Scheduler reports allocation {queue[job]}; agent end time and outcome are unknown. ' + reason
                    if terminal else reason)
        if not same_path:
            value.update(identity_unavailable=True, reason=unverified_reason('Session project path differs from current registration')); continue
        if job not in observations:
            value['reason'] = unverified_reason('Session ownership check exceeded the bounded check budget'); continue
        if observations[job] is None:
            value['reason'] = unverified_reason('Session ownership verification is unavailable'); continue
        try:
            observed = scheduler(profile, rec, observations=observations)
            # Allocation times are not agent lifecycle timestamps. Retain the
            # original Botainer record even when scheduler detail is available.
            for key in ('started_at', 'ended_at'):
                if key in observed: observed['scheduler_' + key] = observed.pop(key)
            # This exact-provenance detail is newer than the queue snapshot.
            observed['scheduler_observation'] = 'not-active' if observed.get('scheduler_state') in TERMINAL else 'active'
            value.update(observed)
        except (ValueError, OSError, UnicodeError, KeyError, TypeError):
            value['reason'] = unverified_reason('Current allocation does not establish native session ownership')
    return {'projects': projects, 'sessions': sessions, 'observed_at': datetime.now(timezone.utc).isoformat()}


def safe_parts(value, *, empty=True):
    require(isinstance(value, str) and len(value) <= 1024, 'Invalid relative path')
    if value == '' and empty: return []
    parts = value.split('/')
    secrets = {'credentials', 'credential', 'secret', 'secrets', 'token', 'tokens', 'password',
               'passwords', 'auth', 'login', 'id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519', 'authorized_keys', 'known_hosts'}
    require(len(parts) <= 24 and all(p and not p.startswith('.') and '\\' not in p and
            not any(ord(c) < 32 or ord(c) == 127 for c in p) and len(p.encode()) <= 240 for p in parts), 'Invalid relative path')
    require(all(p.lower().split('.')[0] not in secrets and not p.lower().endswith(('.pem', '.key', '.p12', '.pfx', '.keystore', '.kdbx'))
                for p in parts), 'Private file excluded')
    return parts


def file_read(project, path, *, read=False):
    parts = safe_parts(path, empty=not read)
    fd = os.open(project, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1] if read else parts:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = child
        if read:
            child = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            with os.fdopen(child, 'rb') as stream:
                info = os.fstat(stream.fileno())
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'Only ordinary single-link text files can be read')
                raw = stream.read(MAX_TEXT + 1)
            require(len(raw) <= MAX_TEXT and b'\0' not in raw, 'Text preview bound exceeded')
            return {'path': path, 'text': raw.decode(), 'size': len(raw)}
        entries = []
        with os.scandir(fd) as scan:
            for index, entry in enumerate(scan):
                require(index < 500, 'Directory entry bound exceeded')
                try: safe_parts(entry.name)
                except ValueError: continue
                info = entry.stat(follow_symlinks=False)
                if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)): continue
                entries.append({'name': entry.name, 'path': '/'.join([*parts, entry.name]),
                                'kind': 'directory' if stat.S_ISDIR(info.st_mode) else 'file', 'size': info.st_size})
        return {'path': path, 'entries': sorted(entries, key=lambda row: row['name'])}
    finally: os.close(fd)


def config_read(project):
    directory = project / '.botainer'
    require(directory.resolve(strict=True) == directory, 'Project metadata is a symlink')
    fd = os.open(project, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        child = os.open('.botainer', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
    finally: os.close(fd)
    try: raw = read_at(child, 'config.yaml')
    finally: os.close(child)
    return {'text': raw.decode(), 'revision': hashlib.sha256(raw).hexdigest(),
            'path': str(directory / 'config.yaml'), 'writable': True}


def read_at(directory, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'Expected regular single-link config')
        value = stream.read(MAX_TEXT + 1)
    require(len(value) <= MAX_TEXT, 'Config exceeds text bound')
    return value


def config_save(project, text, revision, *, control):
    fd = os.open(project, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: metadata = os.open('.botainer', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
    finally: os.close(fd)
    name = '.dashboard-config-' + uuid.uuid4().hex
    try:
        base = read_at(metadata, 'config.yaml')
        require(hashlib.sha256(base).hexdigest() == revision, 'Config changed before save')
        recovery = module_from_bundle('config_recovery.py').preserve_config(
            control, str(project), base, text.encode())
        child = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=metadata)
        with os.fdopen(child, 'wb') as stream:
            stream.write(text.encode()); stream.flush(); os.fsync(stream.fileno())
        require(hashlib.sha256(read_at(metadata, 'config.yaml')).hexdigest() == revision, 'Config changed before replacement')
        os.replace(name, 'config.yaml', src_dir_fd=metadata, dst_dir_fd=metadata)
        os.fsync(metadata)
        return recovery
    finally:
        try: os.unlink(name, dir_fd=metadata)
        except FileNotFoundError: pass
        os.close(metadata)


def require_idle(profile, uid):
    directory = Path(profile['state_root']) / 'state' / canonical_uuid(uid) / 'sessions'
    if not directory.exists(): return
    require(directory.resolve(strict=True) == directory, 'Session directory changed')
    for child in directory.iterdir():
        if not SID.fullmatch(child.name): continue
        record = session_record(profile, uid, child.name)
        job = (record.get('runtime_handle', {}).get('apptainer') or {}).get('slurm_jobid')
        if job: require(scheduler(profile, record)['state'] == 'stopped', 'Config is locked while a job is active or unverified')


def verify_enabled_plugins(profile, names):
    from botainer.plugins import lifecycle
    items = lifecycle.list_installed()
    installed = {item.name: item for item in items}
    require(len(installed) == len(items), 'Installed plugin identity is ambiguous')
    for name in names:
        require(name in installed, 'An enabled plugin is not installed')
        directory = installed[name].plugin_dir.resolve(strict=True)
        source, state = Path(profile['source_root']), Path(profile['state_root'])
        if profile['version'] == 2:
            require(directory == state / 'plugins' / name and installed[name].plugin_dir == directory,
                    'Wheel plugin selection differs from its reviewed installed state')
        if directory.is_relative_to(source): base, hashes = source, profile['source_sha256']
        elif directory.is_relative_to(state): base, hashes = state, profile['state_plugin_sha256']
        else: raise ValueError('Enabled plugin is outside the reviewed installation')
        for path in directory.rglob('*'):
            require(not path.is_symlink(), 'Enabled plugin contains a symlink')
            if not path.is_file() or '__pycache__' in path.parts or path.suffix == '.pyc': continue
            # Native hooks also accept executable scripts without a Python
            # suffix. Pin every executable and every file in hook/command
            # directories, plus imported scripts/manifests elsewhere. New
            # plugin types still require source review, not just this filter.
            relative = path.relative_to(directory)
            if (path.stat().st_mode & 0o111 or set(relative.parts[:-1]) & {'hooks', 'host_helper', 'commands'}
                    or path.suffix in {'.py', '.sh', '.yaml', '.yml', '.json', '.js', '.mjs', '.cjs', '.rb', '.pl', '.so'}):
                key = str(path.relative_to(base))
                require(key in hashes and hashlib.sha256(regular(path)).hexdigest() == hashes[key],
                        'Enabled plugin file lacks an exact reviewed pin: ' + name)


def native_hpc_helper(profile):
    if profile['version'] == 1:
        return Path(profile['source_root']) / 'plugins/hpc-launcher/host_helper/submit.py'
    # Resolve the same plugin selected by native lifecycle, never a packaged
    # resource copy that the native dispatcher would not execute.
    verify_enabled_plugins(profile, ['hpc-launcher'])
    return Path(profile['state_root']) / 'plugins/hpc-launcher/host_helper/submit.py'


def module_from_bundle(name):
    value = types.ModuleType('dashboard_' + name.replace('.', '_'))
    value.__file__ = '<dashboard-' + name + '>'
    exec(compile(BUNDLE[name], value.__file__, 'exec'), value.__dict__)
    return value


def config_validate(profile, project, text, revision):
    previous = config_read(project)
    require(previous['revision'] == revision, 'Config changed since it was opened')
    require(isinstance(text, str) and len(text.encode()) <= MAX_TEXT and '\0' not in text, 'Config text exceeds bound')
    validator = module_from_bundle('workspace_botainer_helper.py')
    with tempfile.TemporaryDirectory(prefix='validate-', dir=profile['control_root']) as folder:
        result = validator.validate({'sourceRoot': profile['source_root'], 'text': text,
                                     'previousText': previous['text']}, Path(folder))
    return {key: value for key, value in result.items() if key not in {'raw', 'model'}}


def operation_path(profile, request):
    canonical_uuid(request)
    return Path(profile['control_root']) / ('operation-' + request + '.json')


def reconciled_receipt(profile, data):
    """Read an abandoned pre-dispatch operation without rewriting its history.

    Every native scheduler dispatch first durably writes ``dispatching`` while
    holding this same project lock. Once the existing lock can be acquired
    exclusively, an unchanged earlier phase proves that this command cannot
    still dispatch. Absence from scheduler/process listings is not evidence.
    """
    control = private_directory(profile['control_root'])
    path = operation_path(profile, data['request_id'])
    if not path.exists(): return {'phase': 'unobserved'}
    result = json.loads(regular(path))
    def verify_scope(value):
        require(value.get('request_id') == data['request_id']
                and value.get('profile_fingerprint') == data['profile_fingerprint'], 'Operation receipt scope changed')
        for key in ('operation', 'project_uuid', 'project_path', 'config_revision'):
            if key in data: require(value.get(key) == data[key], 'Operation receipt target changed')
    verify_scope(result)
    if result.get('operation') != 'start' or result.get('phase') not in {'entered', 'before-dispatch'}:
        return result
    uid = canonical_uuid(result['project_uuid'])
    descriptor = None
    try:
        # Do not create a missing lock: the original writer necessarily held
        # this exact file before publishing its first receipt.
        descriptor = os.open(control / ('project-' + uid + '.lock'), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid()
                and not info.st_mode & 0o077, 'Original operation lock changed')
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # A dispatch may have advanced between our first read and lock claim.
        # The read under the lock is the authority; never promote the old read.
        current = json.loads(regular(path)); verify_scope(current)
        if current.get('operation') == 'start' and current.get('phase') in {'entered', 'before-dispatch'}:
            return {**current, 'reconciliation': {'kind': 'pre-dispatch-command-ended',
                    'observedPhase': current['phase'], 'projectLock': 'exclusive-existing'}}
        return current
    except (FileNotFoundError, BlockingIOError):
        return result
    finally:
        if descriptor is not None: os.close(descriptor)


def native_launch(profile, data):
    project = project_path(profile, data)
    request = canonical_uuid(data['request_id'])
    control = private_directory(profile['control_root'], create=True)
    receipt = operation_path(profile, request)
    expected_revision = data['config_revision']
    # All action types share a receipt namespace. A request lock prevents a
    # concurrent caller from reusing one ID under a different project/action
    # lock; the project lock owns the native launch's complete dispatch lifetime.
    with lock(control / ('operation-' + request + '.lock')), lock(control / ('project-' + data['project_uuid'] + '.lock')):
        require(not receipt.exists(), 'This operation was already attempted; it will not be retried')
        common = {'request_id': request, 'project_uuid': data['project_uuid'], 'project_path': str(project),
                  'config_revision': expected_revision, 'profile_fingerprint': data['profile_fingerprint'], 'operation': 'start'}
        atomic_json(receipt, {**common, 'phase': 'entered'})
        try:
            helper_path = native_hpc_helper(profile)
            require(config_read(project)['revision'] == expected_revision, 'Project config changed before launch')
            # These attachment prerequisites precede native composition and
            # credential helpers. An authoritative early refusal is written
            # only by the same lock owner that claimed this request.
            from botainer.core import config as config_module
            require('nudge' in config_module.load_config(project).plugins_enabled,
                    'Reconnect requires Botainer nudge/Screen; enable nudge in project config before launching')
            require(not (Path.home() / '.screenrc').exists() and not (Path.home() / '.screenrc').is_symlink(),
                    'Personal Screen configuration requires attachment qualification')
            supervisor = module_from_bundle('cluster_attach_supervisor.py')
            try:
                # Reject an unsupported login-host policy before composing or
                # submitting work. Compute hosts may differ: their independent
                # attachment check remains required and is not pre-approved here.
                supervisor.verify_screen_policy()
            except (OSError, supervisor.Refused):
                raise ValueError('No job was submitted. Screen on the SSH login machine requires site qualification '
                    'for this dashboard alpha. Ask the dashboard maintainer to review the Screen binary and system '
                    'configuration on both login and compute machines; do not disable the attachment checks.') from None
        except Exception:
            atomic_json(receipt, {**common, 'phase': 'not-dispatched', 'exit_code': 2})
            raise
        original_call, original_run = subprocess.call, subprocess.run
        old_argv, old_cwd = sys.argv[:], Path.cwd()
        entered = False
        dispatched = False
        native_boundaries = set()
        result_record = None
        exit_code = 1

        @contextmanager
        def child_environment(env):
            # Match the native dispatcher environment, including explicit state
            # and project values. This helper is a dedicated single-thread process.
            old = dict(os.environ)
            try:
                os.environ.clear(); os.environ.update(env)
                yield
            finally:
                os.environ.clear(); os.environ.update(old)

        def instrument_submit(plan, spec, *, dry_run, original, outcome_type):
            nonlocal entered, result_record
            try:
                require(not entered and not dry_run and plan.submission_mode == 'submit', 'Unexpected native dispatch path')
                entered = True
                require(spec.project_uuid == data['project_uuid'] and spec.project_root == str(project)
                        and spec.runtime == 'apptainer' and SID.fullmatch(spec.session_id), 'Composed native target changed')
                require(plan.nudge_enabled, 'Composed native session has no durable Screen owner')
                require(config_read(project)['revision'] == expected_revision, 'Project config changed during native preflight')
                atomic_json(receipt, {**common, 'phase': 'before-dispatch', 'session_id': spec.session_id})
                outcome = original(plan, spec, dry_run=dry_run)
            except Exception as exc:
                if dispatched: raise
                # Return through native Outcome so its own single teardown site
                # cleans a composed session refused by our identity boundary.
                print('Dashboard launch identity refused: ' + str(exc)[:1000], file=sys.stderr)
                return outcome_type(2, launched=False)
            if outcome.launched:
                record = session_record(profile, data['project_uuid'], spec.session_id)
                job = str(((record.get('runtime_handle') or {}).get('apptainer') or {}).get('slurm_jobid') or '')
                if JOB.fullmatch(job): result_record = {'session_id': spec.session_id, 'job_id': job}
            return outcome

        def guarded_run(argv, *args, **kwargs):
            nonlocal dispatched
            if isinstance(argv, (list, tuple)) and argv and argv[0] == 'sbatch':
                require(entered and not dispatched and len(argv) == 2, 'Unexpected or duplicate native sbatch dispatch')
                dispatched = True
                current = json.loads(regular(receipt))
                atomic_json(receipt, {**current, 'phase': 'dispatching'})
            return original_run(argv, *args, **kwargs)

        def guarded_call(argv, *args, **kwargs):
            require(not args, 'Unsupported native subprocess call arguments')
            if isinstance(argv, (list, tuple)) and argv and Path(str(argv[0])).resolve() == Path(sys.executable).resolve():
                command = list(argv[1:])
                while command and command[0] in ('-I', '-B'): command.pop(0)
                if command[:5] == ['-m', 'botainer.cli.main', 'plugin', 'hpc-launcher', 'submit']:
                    require(set(kwargs) == {'env'}, 'Native plugin CLI call contract changed')
                    require('plugin-cli' not in native_boundaries, 'Duplicate native plugin dispatch')
                    native_boundaries.add('plugin-cli')
                    from botainer.cli.main import main as cli_main
                    with child_environment(kwargs['env']):
                        try: return cli_main(command[2:]) or 0
                        except SystemExit as exc: return exc.code if type(exc.code) is int else 1
                if command and command[0] == str(helper_path):
                    require(set(kwargs) == {'env'}, 'Native HPC helper call contract changed')
                    require('plugin-cli' in native_boundaries and 'submit-helper' not in native_boundaries,
                            'Unexpected native submit helper dispatch')
                    native_boundaries.add('submit-helper')
                    old_path, old_args = sys.path[:], sys.argv[:]
                    old_common = sys.modules.pop('_common', None)
                    try:
                        sys.path.insert(0, str(helper_path.parent)); sys.argv = command
                        with child_environment(kwargs['env']):
                            spec = importlib.util.spec_from_file_location('dashboard_native_hpc_submit', helper_path)
                            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
                            original = module._do_submit
                            module._do_submit = lambda plan, target, *, dry_run: instrument_submit(plan, target, dry_run=dry_run, original=original, outcome_type=module.Outcome)
                            original_plan = module.make_plan
                            def checked_plan(*args, **kwargs):
                                plan = original_plan(*args, **kwargs)
                                verify_enabled_plugins(profile, plan.plugins_enabled)
                                return plan
                            module.make_plan = checked_plan
                            try: return module.main() or 0
                            except SystemExit as exc: return exc.code if type(exc.code) is int else 1
                    finally:
                        sys.path[:] = old_path; sys.argv = old_args
                        sys.modules.pop('_common', None)
                        if old_common is not None: sys.modules['_common'] = old_common
                if ('botainer.cli.main' in command or any('hpc-launcher' in arg for arg in command)):
                    raise ValueError('Native HPC subprocess boundary changed; refusing untracked dispatch')
            return original_call(argv, *args, **kwargs)

        subprocess.call, subprocess.run = guarded_call, guarded_run
        try:
            os.chdir(project)
            from botainer.cli.main import main as cli_main
            argv = ['hpc', 'submit', '--mode', 'submit']
            agent = data.get('agent')
            require(agent in (None, 'claude', 'codex'), 'Unsupported agent override')
            if agent: argv += ['--agent', agent]
            try: exit_code = cli_main(argv) or 0
            except SystemExit as exc: exit_code = exc.code if type(exc.code) is int else 1
            return exit_code
        except KeyboardInterrupt:
            exit_code = 130; return exit_code
        finally:
            subprocess.call, subprocess.run = original_call, original_run
            sys.argv = old_argv; os.chdir(old_cwd)
            if not dispatched:
                atomic_json(receipt, {**common, 'phase': 'not-dispatched', 'exit_code': exit_code})
            elif result_record is not None:
                atomic_json(receipt, {**common, 'phase': 'completed', 'exit_code': exit_code, 'result': result_record})
            # Any missing result after entering sbatch remains unknown forever.
            # Neither terminal text nor absence from squeue authorizes a retry.


def native_init(profile, data):
    root_id, relative = data['root'], data['directory']
    require(root_id in profile['project_roots'], 'Unknown project root')
    parts = safe_parts(relative, empty=False)
    base = Path(profile['project_roots'][root_id])
    require(base.resolve(strict=True) == base, 'Project root changed')
    target = base.joinpath(*parts)
    request = canonical_uuid(data['request_id']); receipt = operation_path(profile, request)
    control = private_directory(profile['control_root'], create=True)
    with lock(control / ('operation-' + request + '.lock')), lock(control / ('setup-' + hashlib.sha256(str(target).encode()).hexdigest() + '.lock')):
        require(not receipt.exists(), 'This setup operation was already attempted')
        common = {'request_id': request, 'project_path': str(target), 'operation': 'init',
                  'profile_fingerprint': data['profile_fingerprint']}
        atomic_json(receipt, {**common, 'phase': 'entered'})
        if data['mode'] == 'create':
            require(target.parent.resolve(strict=True) == target.parent and target.parent.is_relative_to(base), 'Project parent changed')
            target.mkdir(mode=0o700, exist_ok=False)
        else: require(data['mode'] in {'open', 'register'}, 'Unsupported project setup mode')
        project_path(profile, {'project_path': str(target)}, registered=False)
        require(not any((parent / '.botainer/project-id').exists() for parent in target.parents if parent != Path('/')),
                'Nested Botainer projects are unsupported')
        metadata = target / '.botainer'
        if metadata.exists() or metadata.is_symlink():
            require(metadata.resolve(strict=True) == metadata and metadata.is_dir(), 'Project metadata changed')
            # Opening a registered initialized project is observation/adoption,
            # never a reset or agent switch. Partial and unregistered metadata
            # requires an explicit native CLI repair, not an invented GUI fix.
            uid = canonical_uuid(regular(metadata / 'project-id', 256).decode().strip())
            config_read(target)
            project_path(profile, {'project_path': str(target), 'project_uuid': uid})
            print('Opened the existing registered project; its configuration and identity are unchanged.')
            atomic_json(receipt, {**common, 'phase': 'completed', 'exit_code': 0, 'project_uuid': uid})
            return 0
        from botainer.cli.main import main as cli_main
        previous = Path.cwd(); code = 1
        try:
            os.chdir(target)
            argv = ['init', '--runtime', 'apptainer']
            agent = data.get('agent'); require(agent in (None, 'claude', 'codex'), 'Unsupported agent override')
            if agent: argv += ['--agent', agent]
            name = data.get('name')
            if name:
                require(isinstance(name, str) and len(name) <= 160 and not any(ord(c) < 32 or ord(c) == 127 for c in name), 'Invalid project name')
                argv += ['--name', name]
            try: code = cli_main(argv) or 0
            except SystemExit as exc: code = exc.code if type(exc.code) is int else 1
            if code == 0:
                uid = regular(target / '.botainer/project-id', 256).decode().strip(); canonical_uuid(uid)
                atomic_json(receipt, {**common, 'phase': 'completed', 'exit_code': 0, 'project_uuid': uid})
            else: atomic_json(receipt, {**common, 'phase': 'failed', 'exit_code': code})
            return code
        finally: os.chdir(previous)


def native_stop(profile, data, project):
    """One exact native stop attempt; an uncertain transport is not retried."""
    control = private_directory(profile['control_root'], create=True)
    path = operation_path(profile, data['request_id'])
    common = {'operation': 'stop', 'request_id': data['request_id'], 'project_uuid': data['project_uuid'],
              'project_path': str(project), 'session_id': data['session_id'], 'job_id': data['job_id'],
              'profile_fingerprint': data['profile_fingerprint']}
    with lock(control / ('operation-' + canonical_uuid(data['request_id']) + '.lock')):
        if path.exists():
            previous = json.loads(regular(path))
            require(all(previous.get(key) == value for key, value in common.items()), 'Stop request identity changed')
            return previous.get('result', {'confirmed': False})
        rec = session_record(profile, data['project_uuid'], data['session_id'])
        require(rec['project_root'] == str(project), 'Session project changed')
        observed = scheduler(profile, rec)
        require(observed.get('job_id') == data['job_id'] and observed['state'] in {'running', 'queued', 'stopped'},
                'Job identity/liveness unverified; stop was not dispatched')
        if observed['state'] == 'stopped':
            result = {'confirmed': True, 'observation': observed}
        else:
            atomic_json(path, {**common, 'phase': 'dispatching'})
            code, _out, _err = bounded(cli_argv(profile, 'hpc', 'stop', data['job_id']), cwd=project, timeout=30)
            after = scheduler(profile, rec)
            result = {'confirmed': code == 0 and after['state'] == 'stopped', 'observation': after}
        atomic_json(path, {**common, 'phase': 'completed', 'result': result})
        return result


def prepare(profile):
    control = private_directory(profile['control_root'], create=True)
    source = __dashboard_source__.encode()
    require(len(source) <= 262144, 'Dashboard helper exceeds source bound')
    digest = hashlib.sha256(source).hexdigest()
    path = control / ('helper-' + digest + '.py')
    with lock(control / 'helper-publication.lock'):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        except FileExistsError: pass
        else:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(source); stream.flush(); os.fsync(stream.fileno())
        require(hashlib.sha256(regular(path, 262144)).hexdigest() == digest, 'Published helper changed')
    return {'path': str(path), 'sha256': digest}


def compute_attach(profile, data):
    """Attach only a recorded native Screen owner; this never starts an agent.

    The shared supervisor handles framing, literal Ctrl-A, heartbeat expiry and
    owned-child cleanup. Native user Screen configuration requires a separate
    qualification; this initial site path accepts absent personal screenrc only.
    """
    supervisor = module_from_bundle('cluster_attach_supervisor.py')
    control = private_directory(profile['control_root'])
    uid, sid, job = data['project_uuid'], data['session_id'], data['job_id']
    rec = session_record(profile, uid, sid)
    require(rec['project_root'] == data['project_path'], 'Recorded project changed')
    require(str((rec.get('runtime_handle', {}).get('apptainer') or {}).get('slurm_jobid')) == job
            and rec.get('screen_session_id') == 'botainer-' + job, 'Recorded Screen/job identity changed')
    require(os.environ.get('SLURM_JOB_ID') == job, 'Attachment is outside the selected allocation')
    require(not (Path.home() / '.screenrc').exists() and not (Path.home() / '.screenrc').is_symlink(),
            'Personal Screen configuration requires attachment qualification')
    policy = supervisor.verify_screen_policy()
    environment = dict(os.environ)
    environment.update(TERM='xterm-256color')
    owner_path = control / ('owner-' + uid + '-' + sid + '.json')

    def display():
        code, out, _err = bounded(['/usr/bin/screen', '-ls', 'botainer-' + job], cwd=Path.home(), timeout=4, limit=65536)
        require(code in (0, 1), 'Screen inventory unavailable')
        matches = re.findall(rb'(?m)^\s*([0-9]+\.botainer-' + re.escape(job.encode()) + rb')\s+[^\r\n]*\((Attached|Detached)\)\s*$', out)
        require(len(matches) == 1, 'Exact Screen owner is missing or ambiguous')
        return matches[0][0].decode(), matches[0][1].decode()

    def identity():
        screen, state = display()
        pid = int(screen.split('.', 1)[0]); process = Path('/proc') / str(pid)
        require(process.stat().st_uid == os.getuid(), 'Screen owner account changed')
        cgroup = (process / 'cgroup').read_text()
        require(re.search(r'(?:^|/)job[_-]?' + re.escape(job) + r'(?:/|$)', cgroup, re.MULTILINE),
                'Screen process is outside the selected job cgroup')
        command = (process / 'cmdline').read_bytes().split(b'\0')
        require(('botainer-' + job).encode() in command and b'-D' in command and b'-m' in command,
                'Screen is not the native durable batch owner')
        value = {'node': socket.gethostname(), 'screen': screen, 'pid': pid,
                 'start_ticks': (process / 'stat').read_text().rsplit(')', 1)[1].split()[19],
                 'uid': os.getuid(), 'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                 'job_start_time': data['job_start_time'], 'screen_sha256': policy['screen_sha256']}
        verification = supervisor.verify_screen_executable(process, value)
        return value, state, verification

    with lock(control / ('writer-' + uid + '-' + sid + '.lock')):
        owner, state, verification = identity()
        require(state == 'Detached', 'Another terminal is attached; no automatic takeover is permitted')
        if owner_path.exists(): require(json.loads(regular(owner_path)) == owner, 'Original Screen owner was replaced')
        else: atomic_json(owner_path, owner)

        def checked_state(expected, wait_seconds=0):
            deadline = time.monotonic() + wait_seconds
            while True:
                current, observed, mode = identity()
                require(current == owner and mode == verification, 'Original Screen owner changed')
                if observed == expected: return
                require(time.monotonic() < deadline, 'Original Screen did not confirm ' + expected.lower())
                time.sleep(.05)

        def attached(process):
            checked_state('Attached', 3)
            require(process.poll() is None, 'Owned Screen viewer exited during attachment')
            proc = Path('/proc') / str(process.pid)
            require(proc.stat().st_uid == os.getuid(), 'Viewer account changed')
            command = (proc / 'cmdline').read_bytes().rstrip(b'\0').split(b'\0')
            require(command == [b'/usr/bin/screen', b'-r', owner['screen'].encode()], 'Viewer identity changed')

        def interrupted(signum, _frame): raise ValueError('Remote viewer interrupted by signal ' + str(signum))
        for signum in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT): signal.signal(signum, interrupted)
        return supervisor.supervise(['/usr/bin/screen', '-r', owner['screen']], environment, owner,
                                    verify_attached=attached, verify_detached=lambda: checked_state('Detached', 1),
                                    executable_verification=verification)


def attach(profile, data):
    project = project_path(profile, data)
    rec = session_record(profile, data['project_uuid'], data['session_id'])
    require(rec['project_root'] == str(project), 'Session project changed')
    observed = scheduler(profile, rec)
    require(observed.get('job_id') == data['job_id'] and observed['state'] == 'running'
            and rec.get('screen_session_id') == 'botainer-' + data['job_id'],
            'Existing Screen owner is unavailable; no fresh agent fallback')
    artifact = prepare(profile)
    # Only this fixed helper is run in the exact allocation, with no host shell
    # fallback. It owns a PTY on the compute node; srun itself carries frames.
    data = {**data, 'job_start_time': observed['started_at']}
    node = observed.get('node')
    require(isinstance(node, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,252}', node),
            'Scheduler batch node is unavailable')
    loader = (
        "import hashlib,os,stat,sys;f=os.open(sys.argv[1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK);"
        "i=os.fstat(f);s=os.read(f,262145);os.close(f);"
        "assert stat.S_ISREG(i.st_mode) and i.st_uid==os.getuid() and i.st_nlink==1 and not i.st_mode&0o077;"
        "assert len(s)<=262144 and hashlib.sha256(s).hexdigest()==sys.argv[2];"
        "sys.argv=['<dashboard-remote>',*sys.argv[3:]];"
        "exec(compile(s,'<dashboard-remote>','exec'),{'__name__':'__main__','__file__':'<dashboard-remote>',"
        "'__dashboard_source__':s.decode()})")
    binary = shutil.which('srun'); require(binary is not None, 'Slurm srun is unavailable')
    argv = [binary, '--jobid=' + data['job_id'], '--overlap', '--exact', '--unbuffered',
            '--nodes=1', '--ntasks=1', '--cpus-per-task=1', '--nodelist=' + node,
            profile['remote_python'], '-I', '-B', '-c', loader, artifact['path'], artifact['sha256'],
            json.dumps(profile), 'compute-attach', json.dumps(data)]
    module_from_bundle('import_policy.py').remove_empty_cache_before_exec()
    os.execve(binary, argv, dict(os.environ))


def main():
    global _ENV
    require(len(sys.argv) == 4, 'Expected profile, fixed action and data')
    profile, action, data = json.loads(sys.argv[1]), sys.argv[2], json.loads(sys.argv[3])
    _ENV = trusted_environment(profile)
    # srun supplies these to the fixed compute-side action. They are never
    # accepted from HTTP and must match the exact verified job before attachment.
    if action == 'compute-attach':
        for key in ('SLURM_JOB_ID', 'SLURM_STEP_ID'):
            if key in os.environ: _ENV[key] = os.environ[key]
    verify(profile)
    if action == 'launch': return native_launch(profile, data)
    if action == 'init': return native_init(profile, data)
    if action == 'attach': return attach(profile, data)
    if action == 'compute-attach': return compute_attach(profile, data)
    if action == 'prepare': result = prepare(profile)
    elif action == 'inventory': result = inventory(profile)
    elif action == 'receipt':
        result = reconciled_receipt(profile, data)
    else:
        project = project_path(profile, data)
        if action in {'files', 'file'}: result = file_read(project, data['path'], read=action == 'file')
        elif action == 'config': result = config_read(project)
        elif action in {'validate-config', 'save-config'}:
            control = private_directory(profile['control_root'], create=True)
            with lock(control / ('project-' + data['project_uuid'] + '.lock')):
                result = config_validate(profile, project, data['text'], data['revision'])
                if action == 'save-config':
                    require(result['valid'], 'Config validation refused the save')
                    require_idle(profile, data['project_uuid'])
                    require(config_read(project)['revision'] == data['revision'], 'Config changed before save')
                    recovery = config_save(project, data['text'], data['revision'], control=control)
                    result = {**config_read(project), 'saved': True, 'recovery': recovery}
        elif action == 'stop':
            result = native_stop(profile, data, project)
        else: raise ValueError('Unsupported fixed remote action')
    print(json.dumps(result, ensure_ascii=True)); return 0


# Only fixed JSON actions may emit a diagnostic envelope. Native launch/init
# terminals and the framed attach transport retain their existing byte protocol.
_JSON_ACTIONS = frozenset({'prepare', 'inventory', 'receipt', 'files', 'file',
                           'config', 'validate-config', 'save-config', 'stop'})
_ERROR_CODES = frozenset({'remote-source-changed', 'remote-launcher-changed',
                          'remote-interpreter-changed', 'remote-plugins-changed',
                          'remote-installation-unsafe', 'remote-import-layout-unsupported'})


def run():
    try:
        with module_from_bundle('import_policy.py').source_imports():
            return main()
    except Exception as exc:
        if len(sys.argv) == 4 and sys.argv[2] in _JSON_ACTIONS:
            code = exc.diagnostic_code if isinstance(exc, VerificationRefused) else 'remote-check-failed'
            if code not in _ERROR_CODES: code = 'remote-check-failed'
            print(json.dumps({'protocol': 'botainer-dashboard.remote-error', 'version': 1, 'code': code},
                             ensure_ascii=True))
        print('Dashboard remote operation refused: ' + str(exc)[:1000], file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(run())
