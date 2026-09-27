"""Installed remote Botainer controller; native prompts and fixed SSH actions.

Construction is local only. Discovery is independent of legacy fixture state.
Launch intent is persisted before dispatch and an uncertain request is never
retried. Source receipts identify targets; terminal bytes are presentation only.
"""
from __future__ import annotations

from .layout import data_root

import copy
import hashlib
import json
import os
from pathlib import Path
import shlex
import threading
import time
import uuid

from .backend_errors import BackendUnavailable
from . import ssh_transport
from .launch_console import LaunchConsole, canonical_request, private_lock, read_transcript
from .pairing import private_directory, read_private_json, write_private_json
from .remote_profile import load_remote_profile
from .remote_observations import ROUTINE_ACTIONS, RemoteObservations
from . import installation_layout, import_policy, config_recovery
from .ssh_diagnostics import SshRequestUnavailable, classify_remote_helper_failure, classify_ssh_failure, connection_diagnostic


BOOTSTRAP = (
    "import json,sys;v=json.loads(sys.stdin.buffer.read(1048577));"
    "s=v['source'];sys.argv=['<dashboard-remote>',json.dumps(v['profile']),v['action'],json.dumps(v['data'])];"
    "exec(compile(s,'<dashboard-remote>','exec'),{'__name__':'__main__','__file__':'<dashboard-remote>',"
    "'__dashboard_source__':s})"
)
EXEC_ARTIFACT = (
    "import hashlib,os,stat,sys;f=os.open(sys.argv[1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK);"
    "i=os.fstat(f);s=os.read(f,262145);os.close(f);"
    "assert stat.S_ISREG(i.st_mode) and i.st_uid==os.getuid() and i.st_nlink==1 and not i.st_mode&0o077;"
    "assert len(s)<=262144 and hashlib.sha256(s).hexdigest()==sys.argv[2];"
    "sys.argv=['<dashboard-remote>',*sys.argv[3:]];"
    "exec(compile(s,'<dashboard-remote>','exec'),{'__name__':'__main__','__file__':'<dashboard-remote>',"
    "'__dashboard_source__':s.decode()})"
)


def remote_payload(repo):
    bundle = {name: (repo / 'tools' / name).read_text() for name in
              ('workspace_botainer_helper.py', 'cluster_attach_supervisor.py')}
    bundle['installation_layout.py'] = Path(installation_layout.__file__).read_text()
    bundle['import_policy.py'] = Path(import_policy.__file__).read_text()
    bundle['config_recovery.py'] = Path(config_recovery.__file__).read_text()
    body = (repo / 'tools/remote_botainer_helper.py').read_text().replace('from __future__ import annotations\n', '', 1)
    value = 'from __future__ import annotations\nimport json\nBUNDLE=json.loads(' + repr(json.dumps(bundle)) + ')\n' + body
    if len(value.encode()) > 262144: raise BackendUnavailable('remote-helper-size-limit')
    compile(value, '<dashboard-remote>', 'exec')
    return value


class RemoteBackend:
    def __init__(self, repo, profile_path, *, exchange=None, console_factory=LaunchConsole, data_dir=None):
        self.repo = Path(repo).resolve(); self.profile_path = Path(profile_path).absolute()
        self.profile = load_remote_profile(self.profile_path)
        self.namespace = 'remote:' + self.profile.fingerprint[:32]
        self.launch_namespace = self.namespace + ':launch'
        self.root = private_directory(private_directory(data_root(self.repo, data_dir) / "remotes") / self.profile.fingerprint)
        self.state_path = self.root / 'state.json'
        self._observations = RemoteObservations(self.root, self.profile.fingerprint)
        self.payload = remote_payload(self.repo); self.payload_digest = hashlib.sha256(self.payload.encode()).hexdigest()
        self.driver = ssh_transport
        self.exchange = exchange or self.driver.bounded_exchange; self.console_factory = console_factory
        self._lock = threading.RLock(); self._polling = False; self._last_attempt = 0.; self._observed_at = None
        self._error = 'remote-checking-connection'; self._diagnostic = connection_diagnostic('checking')
        self._data = {'projects': [], 'sessions': []}; self._launches = []; self._consoles = {}; self._fences = {}
        self._source_hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in
                              (self.repo / 'tools/remote_botainer_helper.py', Path(ssh_transport.__file__),
                               self.repo / 'tools/workspace_botainer_helper.py', self.repo / 'tools/cluster_attach_supervisor.py')}
        layout_path = Path(installation_layout.__file__)
        self._source_hashes[str(layout_path)] = hashlib.sha256(layout_path.read_bytes()).hexdigest()
        policy_path = Path(import_policy.__file__)
        self._source_hashes[str(policy_path)] = hashlib.sha256(policy_path.read_bytes()).hexdigest()
        recovery_path = Path(config_recovery.__file__)
        self._source_hashes[str(recovery_path)] = hashlib.sha256(recovery_path.read_bytes()).hexdigest()
        if self.state_path.exists():
            previous = read_private_json(self.state_path, max_bytes=2 * 1024 * 1024)
            if previous.get('fingerprint') != self.profile.fingerprint: raise BackendUnavailable('remote-state-identity-changed')
            self._data = previous['inventory']; self._launches = previous['launches']
            if len(self._launches) > 1000: raise BackendUnavailable('remote-operation-history-limit')
            for entry in self._launches:
                canonical_request(entry['request_id'])
                if entry['state'] == 'waiting': entry.update(state='unknown', reason='Launch connection was interrupted; checking its exact receipt')

    def _save(self):
        value = {'fingerprint': self.profile.fingerprint, 'inventory': self._data, 'launches': self._launches}
        if len(json.dumps(value, sort_keys=True).encode()) > 2 * 1024 * 1024 - 4096:
            raise BackendUnavailable('remote-persisted-state-size-limit')
        write_private_json(self.state_path, value)

    def _connection_error(self):
        if self._error: return self._error
        if self._observed_at is None or time.monotonic() - self._observed_at > 20:
            return 'remote-observation-stale'
        return None

    def _prune_consoles(self):
        changed = False
        for key, console in list(self._consoles.items()):
            if console.done and console.viewer is None and console.cleanup_confirmed:
                entry = next((row for row in self._launches if row['request_id'] == key), None)
                if entry is not None:
                    entry['console_ended'] = True
                    if getattr(console, 'timed_out', False): entry['console_end_reason'] = 'timeout'
                    changed = True
                del self._consoles[key]
        if changed: self._save()
        if len(self._consoles) >= 32: raise BackendUnavailable('remote-console-capacity')

    def _verify(self):
        if load_remote_profile(self.profile_path).fingerprint != self.profile.fingerprint:
            raise BackendUnavailable('remote-profile-changed-restart-required')
        if any(hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest for path, digest in self._source_hashes.items()):
            raise BackendUnavailable('remote-helper-changed-restart-required')

    def _ssh(self, remote, *, terminal=False):
        argv = ['/usr/bin/ssh', '-tt' if terminal else '-T']
        for option in self.driver.SSH_OPTIONS:
            if option.startswith('RequestTTY='): continue
            argv += ['-o', option]
        # Terminal text cannot trigger OpenSSH's local command/forwarding menu.
        argv += ['-o', 'EscapeChar=none', self.profile.data['ssh_alias'], shlex.join(remote)]
        return argv

    def _call(self, action, **data):
        self._verify()
        envelope = {'source': self.payload, 'profile': self.profile.data, 'action': action,
                    'data': {'profile_fingerprint': self.profile.fingerprint, **data}}
        raw = json.dumps(envelope, ensure_ascii=True).encode()
        if len(raw) > 1024 * 1024: raise BackendUnavailable('remote-request-size-limit')
        argv = self._ssh([self.profile.data['remote_python'], '-I', '-B', '-c', BOOTSTRAP])
        routine = action in ROUTINE_ACTIONS
        receipt = None
        if not routine:
            # Mutation/reconciliation evidence has no automatic retention rule.
            # An unknown action is durable by default, never a disposable read.
            receipt = private_directory(self.root / ('operation-' + uuid.uuid4().hex))
            intent = {'action': action, 'profile': self.profile.fingerprint,
                      'helper_sha256': self.payload_digest, 'retry': False}
            for key in ('request_id', 'project_uuid', 'session_id', 'job_id', 'config_revision'):
                if key in data: intent[key] = data[key]
            write_private_json(receipt / 'intent.json', intent)
        try:
            result = self.exchange(argv, raw, timeout=90 if action == 'inventory' else 45,
                                   stdout_limit=4 * 1024 * 1024, stderr_limit=65536, env=self.driver.child_environment())
        except Exception:
            if routine:
                self._observations.record(action, {}, outcome='exception', diagnostic='remote-check-failed')
            raise
        out, err = result.pop('stdout'), result.pop('stderr')
        if receipt is not None:
            stored = out
            if action == 'save-config' and result['transport_status'] == 'complete' and result['returncode'] == 0:
                try:
                    saved = json.loads(out)
                    if isinstance(saved, dict):
                        stored = json.dumps({key: value for key, value in saved.items() if key != 'text'}, ensure_ascii=True).encode()
                except (ValueError, UnicodeError):
                    pass  # Preserve malformed/uncertain operation evidence.
            self.driver.write_new(receipt / 'stdout.bin', stored); self.driver.write_new(receipt / 'stderr.bin', err)
            write_private_json(receipt / 'result.json', result)
        if result['transport_status'] != 'complete' or result['returncode'] != 0:
            diagnostic = classify_remote_helper_failure(result, out) or classify_ssh_failure(result, err)
            if routine:
                self._observations.record(action, result, outcome='transport-error', stdout_bytes=len(out),
                                          stderr_bytes=len(err), diagnostic=diagnostic)
            raise SshRequestUnavailable('remote-' + action + '-unavailable', diagnostic)
        try:
            value = json.loads(out)
            if not isinstance(value, dict): raise BackendUnavailable('remote-reply-invalid')
            if action == 'receipt':
                self._observations.receipt(data['request_id'], out, data)
        except (ValueError, BackendUnavailable):
            if routine:
                self._observations.record(action, result, outcome='invalid-response', stdout_bytes=len(out),
                                          stderr_bytes=len(err), diagnostic='remote-check-failed')
            raise
        if routine:
            self._observations.record(action, result, outcome='success', stdout_bytes=len(out), stderr_bytes=len(err))
        return value

    def _project_id(self, uid):
        return 'remote-project-' + hashlib.sha256((self.namespace + ':' + uid).encode()).hexdigest()[:32]

    def _project(self, project_id):
        for row in self._data['projects']:
            if self._project_id(row['uuid']) == project_id and row.get('verified'):
                return row
        raise BackendUnavailable('remote-project-identity-unavailable')

    def _target_data(self, project_id):
        row = self._project(project_id)
        return {'project_uuid': row['uuid'], 'project_path': row['path']}

    def _launch_view(self, entry):
        request = entry['request_id']
        console = self._consoles.get(request)
        value = {'contextNamespace': self.launch_namespace, 'runtimeId': request, 'projectId': entry['project_id'],
                 'label': 'Project setup' if entry['operation'] == 'init' else 'Launch log', 'kind': 'launch',
                 'state': 'running' if entry['state'] == 'waiting' else 'stopped' if entry['state'] in {'completed', 'declined', 'failed'} else 'unknown',
                 'createdAt': entry['created_at'], 'agent': entry.get('agent') or '',
                 'launchState': entry['state'], 'operation': entry['operation'],
                 'launch': {'state': entry['state'], 'operation': entry['operation']},
                 'capabilities': {'attachTerminal': request in self._consoles or self._transcript(request).is_file(), 'stopSession': False}}
        if entry.get('reason'): value['launchReason'] = entry['reason']
        if entry.get('console_ended') or console is not None and console.done:
            value['consoleEnded'] = True
        if entry.get('console_end_reason') == 'timeout' or console is not None and getattr(console, 'timed_out', False):
            value['consoleEndReason'] = 'timeout'
        if entry.get('result_target'): value['resultTarget'] = entry['result_target']
        if entry.get('result_project'): value['resultProjectId'] = entry['result_project']
        return value

    def _reconcile(self, entry):
        if entry['state'] in {'completed', 'failed', 'declined'}: return
        console = self._consoles.get(entry['request_id'])
        if console is not None and not console.done: return
        if console is not None:
            entry['console_ended'] = True
            if getattr(console, 'timed_out', False): entry['console_end_reason'] = 'timeout'
        if console is not None and not console.cleanup_confirmed:
            entry.update(state='unknown', reason='Launch transport cleanup is unconfirmed; no retry')
            return
        scope = {key: entry[key] for key in ('operation', 'project_uuid', 'project_path', 'config_revision') if key in entry}
        result = self._call('receipt', request_id=entry['request_id'], **scope)
        if result.get('phase') == 'unobserved':
            entry.update(state='unknown', reason='The remote launch receipt is unavailable; this request will not be retried')
            return
        if result.get('request_id') != entry['request_id'] or result.get('profile_fingerprint') != self.profile.fingerprint:
            raise BackendUnavailable('remote-operation-receipt-changed')
        if result.get('project_path') != entry['project_path'] or result.get('operation') != entry['operation']:
            raise BackendUnavailable('remote-operation-target-changed')
        if entry['operation'] == 'start' and (result.get('project_uuid') != entry['project_uuid']
                or result.get('config_revision') != entry['config_revision']):
            raise BackendUnavailable('remote-launch-revision-changed')
        phase = result['phase']
        proof = result.get('reconciliation')
        if entry['operation'] == 'start' and phase in {'entered', 'before-dispatch'} and proof == {
                'kind': 'pre-dispatch-command-ended', 'observedPhase': phase, 'projectLock': 'exclusive-existing'}:
            entry.update(state='failed', reason='Botainer preflight ended before scheduler dispatch; no job was submitted.')
        elif phase == 'not-dispatched':
            entry.update(state='declined', reason='Botainer exited before scheduler dispatch')
        elif phase == 'failed' and entry['operation'] == 'init':
            entry.update(state='failed', reason='Botainer project setup did not complete')
        elif phase == 'completed' and result.get('exit_code') == 0:
            if entry['operation'] == 'init':
                uid = result['project_uuid']; canonical_request(uid)
                if any(row['uuid'] == uid and row['path'] == entry['project_path'] and row.get('verified') for row in self._data['projects']):
                    entry.update(state='completed', result_project=self._project_id(uid), project_id=self._project_id(uid))
            else:
                target = result.get('result', {})
                matches = [row for row in self._data['sessions'] if row['session_id'] == target.get('session_id')
                           and row['project_uuid'] == entry['project_uuid'] and row.get('job_id') == target.get('job_id')
                           and row['state'] in {'running', 'queued', 'stopped'}]
                if len(matches) == 1:
                    entry.update(state='completed', result_target={'contextNamespace': self.namespace,
                                                                   'runtimeId': matches[0]['session_id']})
                else: entry.update(state='unknown', reason='Submitted target requires exact scheduler reconciliation; no retry')
        else: entry.update(state='unknown', reason='The native launch outcome is unconfirmed; no retry')

    def refresh(self):
        try:
            value = self._call('inventory')
            if not isinstance(value.get('projects'), list) or not isinstance(value.get('sessions'), list):
                raise BackendUnavailable('remote-inventory-invalid')
            if len(value['projects']) > 2000 or len(value['sessions']) > 10000:
                raise BackendUnavailable('remote-inventory-size-limit')
            with self._lock:
                self._data = value
                self._error = None; self._diagnostic = None; self._observed_at = time.monotonic()
                pending = [copy.deepcopy(entry) for entry in self._launches if entry['state'] in {'waiting', 'unknown'}]
                self._save()
            # Receipt observations may wait on SSH. Never hold the inventory
            # lock during that wait: views must be able to become stale/unknown.
            for update in pending:
                self._reconcile(update)
                with self._lock:
                    entry = next(row for row in self._launches if row['request_id'] == update['request_id'])
                    if entry['state'] in {'waiting', 'unknown'}: entry.update(update)
                    self._save()
        except (ValueError, OSError, BackendUnavailable, KeyError) as exc:
            with self._lock:
                self._error = exc.code if isinstance(exc, BackendUnavailable) else 'remote-observation-unavailable'
                self._diagnostic = connection_diagnostic(exc.diagnostic_code if isinstance(exc, SshRequestUnavailable) else 'remote-check-failed')
        finally:
            with self._lock: self._last_attempt = time.monotonic(); self._polling = False

    def snapshot(self):
        with self._lock:
            if not self._polling and time.monotonic() - self._last_attempt > 3:
                self._polling = True; threading.Thread(target=self.refresh, daemon=True, name='remote-inventory').start()
            error = self._connection_error()
            diagnostic = self._diagnostic or (connection_diagnostic('remote-check-failed') if error else None)
            projects, sessions = [], []
            for row in self._data['projects']:
                pid = self._project_id(row['uuid']); ready = bool(row.get('verified')) and not error
                unresolved = any(e['project_id'] == pid and e['state'] in {'waiting', 'unknown'} for e in self._launches)
                active = any(s['project_uuid'] == row['uuid'] and (s.get('job_id') or s.get('identity_unavailable'))
                             and s['state'] != 'stopped' for s in self._data['sessions'])
                project = {'id': pid, 'registeredUuid': row['uuid'], 'name': row['name'], 'path': row['path'],
                                 'agent': row.get('agent', ''), 'contextNamespace': self.namespace,
                                 'machineLabel': self.profile.label, 'installationLabel': self.profile.label,
                                 'lastLaunchAt': row.get('last_launch_at'),
                                 'pathIdentityVerified': row.get('path_identity_verified', row.get('verified', False)),
                                 'capabilities': {'startSession': ready and not unresolved, 'agentOverride': ready,
                                                  'nativeCliLaunch': True, 'filesRead': ready, 'configRead': ready,
                                                  'configWrite': ready and not unresolved and not active}}
                if row.get('control_restriction') and not error:
                    project['controlRestriction'] = row['control_restriction']
                    project['unavailableReason'] = ('Project is outside the selected remote control roots.'
                        if row['control_restriction'] == 'outside-project-roots'
                        else 'Project registration is not verified for remote controls.')
                projects.append(project)
            known = {p['id'] for p in projects}
            for entry in self._launches:
                if entry['project_id'] not in known:
                    projects.append({'id': entry['project_id'], 'name': entry.get('name') or Path(entry['project_path']).name,
                                     'path': entry['project_path'], 'contextNamespace': self.namespace,
                                     'machineLabel': self.profile.label, 'setupPending': True, 'capabilities': {}})
                    known.add(entry['project_id'])
            for row in self._data['sessions']:
                state = 'unknown' if error else row['state']
                project_verified = any(p['uuid'] == row['project_uuid'] and p.get('verified') for p in self._data['projects'])
                fence = self._fences.get(row['session_id'])
                sessions.append({'contextNamespace': self.namespace, 'runtimeId': row['session_id'],
                                 'projectId': self._project_id(row['project_uuid']), 'label': row.get('agent') or row['session_id'],
                                 'agent': row.get('agent', ''), 'runtime': row.get('runtime', 'apptainer'), 'state': state,
                                 'createdAt': row.get('started_at'), 'startedAt': row.get('started_at'),
                                 'recordedEndedAt': row.get('ended_at'), 'schedulerState': row.get('scheduler_state'),
                                 'schedulerObservation': 'unavailable' if error else row.get('scheduler_observation', 'unavailable'),
                                 'schedulerStartedAt': row.get('scheduler_started_at'),
                                 'schedulerEndedAt': row.get('scheduler_ended_at'),
                                 'jobId': row.get('job_id'), 'stopScope': 'allocation',
                                 'node': row.get('node'), 'queueReason': row.get('reason'),
                                 'timeLimit': row.get('time_limit'), 'unavailableReason': error or fence or (row.get('reason') if state == 'unknown' else None),
                                 'capabilities': {'attachTerminal': project_verified and state == 'running' and bool(row.get('screen')) and not fence,
                                                  'stopSession': project_verified and state in {'running', 'queued'} and bool(row.get('job_id'))}})
            sessions += [self._launch_view(e) for e in self._launches[-64:]]
            retention = self._observations.status()
            retention_notice = (' Local diagnostic history needs attention; remote results are still shown.'
                                if retention['status'] in {'unavailable', 'review-retained-legacy'} else '')
            return {'mode': 'remote-native', 'projects': projects, 'sessions': sessions,
                    'stale': bool(error), 'error': error,
                    'connectionStatus': 'checking' if error == 'remote-checking-connection' else 'unavailable' if error else 'available',
                    'unavailableReason': error,
                    'capabilities': {'projectCreate': not bool(error), 'projectOpen': not bool(error),
                                     'nativeCliProjectSetup': True, 'nativeCliLaunch': True, 'agentOverride': True},
                    'notice': 'Native Botainer commands on the registered remote installation. Launch warnings and consent appear in the terminal.' + retention_notice,
                    'diagnosticRetention': retention,
                    'installations': [{'id': self.profile.id, 'label': self.profile.label,
                                       'executable': self.profile.data['launcher']['path'], 'stateRoot': self.profile.data['state_root']}],
                    'clusterSettings': {'label': self.profile.label, 'connectionStatus': 'unavailable' if error else 'connected',
                                        'sshAlias': self.profile.data['ssh_alias'], 'connectionDiagnostic': diagnostic}}

    def workspace_metadata(self):
        return {'roots': [{'id': key, 'label': key, 'path': value} for key, value in self.profile.data['project_roots'].items()],
                'installations': [{'id': self.profile.id, 'label': self.profile.label}]}

    def _transcript(self, request): return self.root / ('launch-' + canonical_request(request) + '.bin')

    def _start_console(self, entry, action, data):
        self._verify()
        self._prune_consoles()
        if len(self._launches) >= 1000: raise BackendUnavailable('remote-operation-history-limit')
        if any(e['request_id'] == entry['request_id'] for e in self._launches): raise BackendUnavailable('remote-operation-already-attempted')
        # Publish the immutable dashboard helper only through this fixed action;
        # no startup deployment, package installation or Botainer source change.
        prepared = self._call('prepare')
        expected = self.profile.data['control_root'] + '/helper-' + self.payload_digest + '.py'
        if prepared != {'path': expected, 'sha256': self.payload_digest}: raise BackendUnavailable('remote-helper-preparation-mismatch')
        fd = private_lock(self.root / ('owner-' + entry['request_id'] + '.lock'))
        try:
            self._launches.append(entry); self._save()
            remote = [self.profile.data['remote_python'], '-I', '-B', '-c', EXEC_ARTIFACT, expected,
                      self.payload_digest, json.dumps(self.profile.data), action,
                      json.dumps({'profile_fingerprint': self.profile.fingerprint, **data})]
            console = self.console_factory(self._ssh(remote, terminal=True), cwd=self.repo,
                                           env=self.driver.child_environment(), transcript=self._transcript(entry['request_id']), owner_fd=fd)
            self._consoles[entry['request_id']] = console; fd = None
        except Exception:
            entry.update(state='unknown', reason='Native command startup outcome is unconfirmed; no retry'); self._save()
            raise
        finally:
            if fd is not None: os.close(fd)
        return {'session': self._launch_view(entry)}

    def start_session(self, project_id, request_id, agent=None):
        canonical_request(request_id)
        if agent not in (None, 'claude', 'codex'): raise BackendUnavailable('remote-agent-override-invalid')
        with self._lock:
            for entry in self._launches:
                if entry['request_id'] == request_id:
                    if entry['project_id'] != project_id or entry.get('agent') != agent: raise BackendUnavailable('remote-request-identity-changed')
                    return {'session': self._launch_view(entry)}
            if self._connection_error(): raise BackendUnavailable('remote-connection-unavailable')
            if any(e['project_id'] == project_id and e['state'] in {'waiting', 'unknown'} for e in self._launches):
                raise BackendUnavailable('remote-project-launch-unresolved')
            target = self._target_data(project_id)
            revision = self._call('config', **target)['revision']
            from datetime import datetime, timezone
            entry = {'request_id': request_id, 'operation': 'start', 'state': 'waiting', 'agent': agent,
                     'project_id': project_id, **target, 'config_revision': revision, 'created_at': datetime.now(timezone.utc).isoformat()}
            return self._start_console(entry, 'launch', {**target, 'request_id': request_id, 'config_revision': revision, 'agent': agent})

    def create_project(self, data):
        with self._lock:
            if self._connection_error(): raise BackendUnavailable('remote-connection-unavailable')
            mode = data.get('mode', 'create'); root = data.get('rootId'); directory = data.get('path')
            if root not in self.profile.data['project_roots'] or mode not in {'create', 'open', 'register'}:
                raise BackendUnavailable('remote-project-setup-invalid')
            if data.get('installationId') != self.profile.id: raise BackendUnavailable('remote-installation-unregistered')
            if not isinstance(directory, str) or not directory or directory.startswith('/') or any(p in {'', '.', '..'} for p in directory.split('/')):
                raise BackendUnavailable('remote-project-path-invalid')
            name = data.get('name') or Path(directory).name
            if not isinstance(name, str) or not 1 <= len(name) <= 160 or any(ord(c) < 32 or ord(c) == 127 for c in name):
                raise BackendUnavailable('remote-project-name-invalid')
            if data.get('agent') not in (None, 'claude', 'codex'): raise BackendUnavailable('remote-agent-override-invalid')
            path = self.profile.data['project_roots'][root] + '/' + directory
            request = canonical_request(data.get('requestId') or str(uuid.uuid4()))
            for old in self._launches:
                if old['request_id'] == request:
                    if old['operation'] != 'init' or old['project_path'] != path: raise BackendUnavailable('remote-request-identity-changed')
                    return {'session': self._launch_view(old), 'project': {'id': old['project_id'], 'name': old['name'],
                            'path': path, 'contextNamespace': self.namespace, 'capabilities': {}}}
            pid = 'remote-setup-' + hashlib.sha256((self.namespace + path).encode()).hexdigest()[:32]
            from datetime import datetime, timezone
            entry = {'request_id': request, 'operation': 'init', 'state': 'waiting', 'agent': data.get('agent'),
                     'project_id': pid, 'project_path': path, 'name': name,
                     'created_at': datetime.now(timezone.utc).isoformat()}
            result = self._start_console(entry, 'init', {'request_id': request, 'mode': mode, 'root': root,
                                                       'directory': directory, 'agent': data.get('agent'), 'name': name})
            result['project'] = {'id': pid, 'name': entry['name'], 'path': path, 'contextNamespace': self.namespace,
                                 'setupPending': True, 'capabilities': {}}
            return result

    def attach(self, namespace, runtime_id, cols, rows):
        if namespace == self.launch_namespace:
            canonical_request(runtime_id)
            with self._lock:
                if not any(e['request_id'] == runtime_id for e in self._launches): raise BackendUnavailable('remote-launch-unregistered')
                fd = private_lock(self.root / ('writer-' + runtime_id + '.lock'))
                try:
                    console = self._consoles.get(runtime_id)
                    if console is None:
                        self._prune_consoles()
                        console = LaunchConsole.__new__(LaunchConsole); console.condition = threading.Condition()
                        console.buffer = bytearray(read_transcript(self._transcript(runtime_id))); console.base = 0
                        console.done = True; console.cleanup_confirmed = True; console.viewer = None
                        self._consoles[runtime_id] = console
                    viewer = console.attach(fd); viewer.resize(cols, rows); return viewer
                except Exception:
                    os.close(fd); raise
        if namespace != self.namespace: raise BackendUnavailable('remote-context-unregistered')
        return self._attach_runtime(runtime_id, cols, rows)

    def _session(self, sid):
        matches = [row for row in self._data['sessions'] if row['session_id'] == sid]
        if len(matches) != 1: raise BackendUnavailable('remote-session-unregistered')
        return matches[0]

    def _attach_runtime(self, sid, cols, rows):
        from .cluster_attach import FramedSshAttachment
        with self._lock:
            row = self._session(sid)
            if self._connection_error() or row['state'] != 'running' or not row.get('screen') or sid in self._fences:
                raise BackendUnavailable('remote-session-attachment-unavailable')
            target = self._target_data(self._project_id(row['project_uuid']))
            prepared = self._call('prepare')
            path = self.profile.data['control_root'] + '/helper-' + self.payload_digest + '.py'
            if prepared != {'path': path, 'sha256': self.payload_digest}: raise BackendUnavailable('remote-helper-preparation-mismatch')
            data = {**target, 'session_id': sid, 'job_id': row['job_id'], 'profile_fingerprint': self.profile.fingerprint}
            remote = [self.profile.data['remote_python'], '-I', '-B', '-c', EXEC_ARTIFACT, path,
                      self.payload_digest, json.dumps(self.profile.data), 'attach', json.dumps(data)]
        try:
            client = FramedSshAttachment(self._ssh(remote), cwd=self.repo, env=self.driver.child_environment(), cols=cols, rows=rows)
        except Exception:
            with self._lock: self._fences[sid] = 'Remote viewer cleanup is unconfirmed; the original job is not stopped'
            raise BackendUnavailable('remote-attach-unconfirmed') from None
        from .cluster_attach import FencedSshAttachment
        return FencedSshAttachment(client, self, sid)

    def stop_session(self, namespace, runtime_id, request_id):
        canonical_request(request_id)
        if namespace != self.namespace: raise BackendUnavailable('remote-context-unregistered')
        with self._lock:
            row = self._session(runtime_id)
            if self._connection_error(): raise BackendUnavailable('remote-connection-unavailable')
            target = self._target_data(self._project_id(row['project_uuid']))
        result = self._call('stop', **target, session_id=runtime_id, job_id=row.get('job_id'), request_id=request_id)
        observation = result.get('observation')
        if (not isinstance(row.get('job_id'), str) or not row['job_id']
                or result.get('confirmed') is not True or not isinstance(observation, dict)
                or observation.get('state') != 'stopped' or observation.get('job_id') != row.get('job_id')):
            raise BackendUnavailable('remote-stop-unconfirmed-check-scheduler')
        self.refresh()
        snapshot = self.snapshot()
        matches = [row for row in snapshot['sessions'] if row['contextNamespace'] == namespace and row['runtimeId'] == runtime_id]
        if len(matches) != 1 or matches[0].get('jobId') != row.get('job_id'):
            raise BackendUnavailable('remote-stop-observation-unavailable')
        # Native stop confirms the exact scheduler target ended; refreshing the
        # sidebar is not the authority for this verdict or helper cleanup.
        return {'session': matches[0], 'terminationConfirmed': True, 'helperCleanupConfirmed': False}

    def _project_call(self, action, project_id, **data):
        with self._lock:
            if self._connection_error(): raise BackendUnavailable('remote-connection-unavailable')
            if action == 'save-config' and any(e['project_id'] == project_id and e['state'] in {'waiting', 'unknown'} for e in self._launches):
                raise BackendUnavailable('remote-project-launch-unresolved')
            target = self._target_data(project_id)
        return self._call(action, **target, **data)

    def list_files(self, project_id, path): return self._project_call('files', project_id, path=path)
    def read_file(self, project_id, path): return self._project_call('file', project_id, path=path)
    def read_config(self, project_id): return self._project_call('config', project_id)
    def validate_config(self, project_id, text, revision): return self._project_call('validate-config', project_id, text=text, revision=revision)
    def save_config(self, project_id, text, revision): return self._project_call('save-config', project_id, text=text, revision=revision)
