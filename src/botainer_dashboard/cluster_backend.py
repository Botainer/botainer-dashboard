"""Explicit selected-site shell adapter; isolated state and durable remote jobs.

Preparation is an operator action. Starting the HTTP service never deploys a
helper or installs software. Current scope is one persistent isolated project
per profile, one live allocation per project, no AI credentials or host shell.
"""
from __future__ import annotations
import copy
from contextlib import contextmanager, nullcontext
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import stat
import threading
import time
import uuid

from .backend_errors import BackendUnavailable
from . import ssh_transport
from .cluster_profiles import load_cluster_profile
from .cluster_attach import FencedSshAttachment as ClusterAttachment
from .pairing import private_directory, read_private_json, write_private_json
from .ssh_diagnostics import SshRequestUnavailable, classify_ssh_failure, connection_diagnostic

ENDED = {'COMPLETED','CANCELLED','FAILED','TIMEOUT','OUT_OF_MEMORY','NODE_FAIL','PREEMPTED','BOOT_FAIL','DEADLINE'}
GOOD_END = {'COMPLETED','CANCELLED','TIMEOUT'}
CONTROL_WAIT_SECONDS = 5.0

def source_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module

def shell_payload(repo):
    names = ('prepare_cluster_trial.py','cluster_runtime_probe.py','cluster_attach_supervisor.py')
    bundle = {}
    for name in names:
        p=repo/'tools'/name
        if p.is_symlink(): raise BackendUnavailable('cluster-helper-symlink')
        bundle[name]=p.read_text()
    body=(repo/'tools/cluster_workspace_helper.py').read_text()
    # Preserve future imports at the beginning. This prelude contains only
    # repository-authored source strings, never request/config values.
    body=body.replace('from __future__ import annotations\n','',1)
    payload=('from __future__ import annotations\nimport json\nBUNDLE=json.loads('+repr(json.dumps(bundle))+')\n'+body).encode()
    if len(payload)>262144: raise BackendUnavailable('cluster-helper-size-limit')
    compile(payload,'<cluster-workspace-payload>','exec')
    return payload

def session_state(record, stale=False):
    state=record.get('status',{}).get('JobState')
    if stale or record.get('observation_error') or record.get('local_error'): return 'unknown'
    if state in ENDED: return 'stopped' if state in GOOD_END else 'failed'
    if state == 'RUNNING': return 'running'
    if state in {'PENDING','CONFIGURING','SUSPENDED'}: return 'queued'
    if record.get('local_stage')=='starting': return 'starting'
    return 'unknown'

def validate_record(record, expected_request=None):
    if not isinstance(record,dict): raise BackendUnavailable('cluster-record-invalid')
    key=record.get('request_id')
    try:
        if not isinstance(key,str) or str(uuid.UUID(key))!=key or (expected_request is not None and key!=expected_request): raise ValueError()
    except ValueError: raise BackendUnavailable('cluster-record-identity-invalid') from None
    if not isinstance(record.get('status',{}),dict): raise BackendUnavailable('cluster-record-status-invalid')
    if any(not isinstance(k,str) or (v is not None and (not isinstance(v,str) or len(v)>1024)) for k,v in record.get('status',{}).items()):
        raise BackendUnavailable('cluster-record-status-invalid')
    if 'session_id' in record and (not isinstance(record['session_id'],str) or not re.fullmatch(r'[0-9a-f]{16}',record['session_id'])): raise BackendUnavailable('cluster-record-session-invalid')
    if 'job_id' in record and (not isinstance(record['job_id'],str) or not re.fullmatch(r'[0-9]{1,20}',record['job_id']) or 'session_id' not in record):
        raise BackendUnavailable('cluster-record-job-invalid')
    for field in ('created_at','observed_at'):
        if field in record and (type(record[field]) not in (int,float) or not 0<=record[field]<=32503680000):
            raise BackendUnavailable('cluster-record-time-invalid')
    return record

class ClusterBackend:
    def __init__(self, repo, profile_path):
        self.repo=Path(repo).resolve(); self.profile_path=Path(profile_path).absolute()
        self.profile=load_cluster_profile(self.profile_path)
        defaults=self.profile.defaults
        if defaults.gpus or defaults.qos or defaults.memory_mib%1024:
            raise BackendUnavailable('cluster-shell-resource-combination-unqualified')
        if defaults.cpus>16 or defaults.memory_mib>65536 or defaults.time_minutes>240:
            raise BackendUnavailable('cluster-shell-prototype-resource-ceiling')
        self.namespace='cluster:'+self.profile.fingerprint[:32]
        self.project_id=self.profile.id+'.workspace'
        self.root=private_directory(private_directory(self.repo/'.local/clusters')/self.profile.fingerprint)
        self.payload=shell_payload(self.repo)
        self.payload_digest=hashlib.sha256(self.payload).hexdigest()
        # Reuse the previously reviewed bounded SSH primitive by an explicit
        # trusted repository path; this is bundled-resource work for packaging.
        self.driver=source_module(self.repo/'tools/cluster_trial.py','dashboard_cluster_driver')
        self._lock=threading.RLock(); self._polling=False; self._working=False
        self._control_lock=threading.Lock()
        self._last_attempt=0.; self._observed=0.; self._error='cluster-checking-connection'
        self._connection_diagnostic=connection_diagnostic('checking')
        self._records={}; self._fences={}
        self.state_path=self.root/'state.json'
        if self.state_path.exists():
            old=read_private_json(self.state_path, max_bytes=2*1024*1024)
            if old.get('fingerprint')!=self.profile.fingerprint: raise BackendUnavailable('cluster-profile-state-mismatch')
            self._records=old['records']; self._observed=old.get('observed',0)
            if not isinstance(self._records,dict) or len(self._records)>200: raise BackendUnavailable('cluster-records-invalid')
            for key,record in self._records.items():validate_record(record,key)
            for record in self._records.values():
                if record.get('local_stage')=='starting':
                    record['local_error']='cluster-start-interrupted-check-remote-state'
        self._source_hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in
            [self.repo/'tools/cluster_trial.py',Path(ssh_transport.__file__),self.repo/'tools/cluster_workspace_helper.py',
             self.repo/'tools/prepare_cluster_trial.py',self.repo/'tools/cluster_runtime_probe.py',
             self.repo/'tools/cluster_attach_supervisor.py']}

    def _verify(self):
        if load_cluster_profile(self.profile_path).fingerprint!=self.profile.fingerprint:
            raise BackendUnavailable('cluster-profile-changed-restart-required')
        if any(hashlib.sha256(Path(p).read_bytes()).hexdigest()!=d for p,d in self._source_hashes.items()):
            raise BackendUnavailable('cluster-helper-changed-restart-required')

    def _argv(self,action,data):
        remote=[self.profile.connection.remote_python,'-I','-B','-c',self.driver.BOOTSTRAP,
                action,'--root',self.profile.connection.trial_root,'--data',json.dumps(data,separators=(',',':'))]
        argv=['/usr/bin/ssh','-T']
        for option in self.driver.SSH_OPTIONS: argv.extend(['-o',option])
        return [*argv,self.profile.connection.ssh_alias,shlex.join(remote)]

    def _call(self,action,**data):
        # These dispatcher actions take the same remote nonblocking operation
        # lock. Avoid racing our own background status against attach/start/stop.
        # File/config reads do not take that remote lock and stay independent.
        with self._control_guard() if action in {'status','start','stop'} else nullcontext():
            return self._exchange(action,**data)

    @contextmanager
    def _control_guard(self):
        if not self._control_lock.acquire(timeout=CONTROL_WAIT_SECONDS):
            raise BackendUnavailable('cluster-control-preparation-busy')
        try: yield
        finally: self._control_lock.release()

    def _exchange(self,action,**data):
        self._verify()
        data={'fingerprint':self.profile.fingerprint,**data}
        argv=self._argv(action,data)
        artifact=self._payload_artifact()
        receipt=private_directory(self.root/('request-'+uuid.uuid4().hex))
        self.driver.write_json(receipt/'intent.json',{'action':action,'argv':argv,'payload_sha256':self.payload_digest,
            'payload_artifact':str(artifact.relative_to(self.root)),'retry':False})
        result=self.driver.bounded_exchange(argv,self.payload,timeout=50 if action in {'prepare','start'} else 20,
            stdout_limit=1024*1024,stderr_limit=65536,env=self.driver.child_environment())
        stdout=result.pop('stdout'); stderr=result.pop('stderr')
        self.driver.write_new(receipt/'stdout.bin',stdout);self.driver.write_new(receipt/'stderr.bin',stderr)
        self.driver.write_json(receipt/'result.json',result)
        if result['transport_status']!='complete' or result['returncode']!=0:
            raise SshRequestUnavailable('cluster-'+action+'-unavailable-inspect-private-receipt',
                classify_ssh_failure(result,stderr))
        try: value=json.loads(stdout)
        except (ValueError,UnicodeError): raise BackendUnavailable('cluster-reply-invalid') from None
        if not isinstance(value,dict): raise BackendUnavailable('cluster-reply-invalid')
        return value

    def _payload_artifact(self):
        """One immutable helper per digest; existing request receipts stay intact."""
        directory=private_directory(self.root/'artifacts')
        path=directory/(self.payload_digest+'.py')
        lock=os.open(directory/'publication.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
        try:
            info=os.fstat(lock)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_nlink!=1 or info.st_mode&0o077):
                raise BackendUnavailable('cluster-payload-lock-invalid')
            deadline=time.monotonic()+3
            while True:
                try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                except BlockingIOError:
                    if time.monotonic()>=deadline:raise BackendUnavailable('cluster-payload-publication-busy')
                    time.sleep(.02)
            # Every controller reads under this short publication lock, so a
            # concurrent request cannot inspect a partly written artifact.
            try: self.driver.write_new(path,self.payload)
            except FileExistsError: pass
            fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            with os.fdopen(fd,'rb') as handle:
                info=os.fstat(handle.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or
                        info.st_nlink!=1 or info.st_mode&0o077 or info.st_size>262144):
                    raise BackendUnavailable('cluster-payload-artifact-invalid')
                data=handle.read(262145)
        finally: os.close(lock)
        if hashlib.sha256(data).hexdigest()!=self.payload_digest:
            raise BackendUnavailable('cluster-payload-artifact-changed')
        return path

    def prepare_remote(self):
        c=self.profile.connection
        return self._call('prepare',source_root=c.source_root,image=c.image,remote_python=c.remote_python,
            defaults=self.profile.defaults.as_dict(),profile_fingerprint=self.profile.fingerprint)

    def _save(self):
        write_private_json(self.state_path,{'fingerprint':self.profile.fingerprint,'records':self._records,'observed':self._observed})

    def refresh(self):
        try:
            value=self._call('status')
            records=value['sessions']
            if not isinstance(records,list) or len(records)>200: raise BackendUnavailable('cluster-inventory-limit')
            normalized={}
            for record in records:
                validate_record(record);key=record['request_id']
                if key in normalized:
                    raise BackendUnavailable('cluster-record-identity-invalid')
                normalized[key]=record
            with self._lock:
                for key,record in self._records.items():
                    if key not in normalized:
                        kept=copy.deepcopy(record)
                        if kept.get('local_stage')!='starting' and kept.get('status',{}).get('JobState') not in ENDED:
                            kept['observation_error']='cluster-record-missing-reconciliation-required'
                        normalized[key]=kept
                self._records=normalized;self._observed=time.time();self._error=None
                self._connection_diagnostic=None;self._save()
        except (BackendUnavailable,OSError,ValueError,KeyError) as exc:
            with self._lock:
                self._error=exc.code if isinstance(exc,BackendUnavailable) else 'cluster-inventory-unavailable'
                self._connection_diagnostic=connection_diagnostic(
                    exc.diagnostic_code if isinstance(exc,SshRequestUnavailable) else 'remote-check-failed')
        finally:
            with self._lock:self._polling=False;self._last_attempt=time.monotonic()

    def _view(self,record):
        stale=bool(self._error) or time.time()-self._observed>30
        state=session_state(record,stale)
        req=record['request_id']; status=record.get('status',{})
        # Cancellation can destroy the owner before a viewer acknowledges its
        # detach. Retain the internal fence, but a freshly confirmed ended job
        # no longer needs a live-owner detach warning in its stopped view.
        live_fence=self._fences.get(req) if state not in {'stopped','failed'} else None
        reason=live_fence or self._error or record.get('local_error') or record.get('observation_error')
        return {'contextNamespace':self.namespace,'runtimeId':req,'projectId':self.project_id,
            'label':'Cluster shell · '+(record.get('job_id') or req[:8]),'agent':'container shell',
            'state':state,'createdAt':iso(record.get('created_at')),
            'lastObservedAt':iso(record.get('observed_at')),'lastKnownState':status.get('JobState'),
            'schedulerState':status.get('JobState'),'queueReason':status.get('Reason'),
            'jobId':record.get('job_id'),'stopScope':'allocation',
            'node':status.get('BatchHost'),'timeLimit':status.get('TimeLimit'),'unavailableReason':reason,
            'capabilities':{'attachTerminal':state=='running' and not reason,
                'stopSession':bool(record.get('job_id')) and state not in {'stopped','failed'} and not self._working and not self._error}}

    def snapshot(self):
        with self._lock:
            if not self._polling and not self._working and (self._last_attempt==0 or time.monotonic()-self._last_attempt>=4):
                self._polling=True;threading.Thread(target=self.refresh,daemon=True).start()
            sessions=[self._view(r) for r in self._records.values()]
            can_start=not self._error and not self._working and all(s['state'] in {'stopped','failed'} for s in sessions)
            c=self.profile.connection;d=self.profile.defaults
            return {'mode':'cluster-workspace','connectionStatus':'checking' if self._error=='cluster-checking-connection' else 'unavailable' if self._error else 'available',
                'lastObservedAt':iso(self._observed),'connectionError':self._error,
                'notice':'Isolated remote container shell workspace. Files persist; host networking is available. No AI credentials. Live Botainer projects are not managed by this prototype.',
                'capabilities':{'startSession':can_start,'attachTerminal':False,'stopSession':False,'filesRead':not self._error,
                    'configRead':not self._error,'configWrite':False,'projectCreate':False,'projectRegister':False},
                'projects':[{'id':self.project_id,'name':self.profile.label+' workspace','path':c.trial_root+'/project',
                    'machineLabel':self.profile.label,'installationLabel':'Isolated copy · '+self.profile.id,'unavailableReason':self._error}],
                'sessions':sessions,'installations':[{'id':self.profile.id,'name':self.profile.label,
                    'machine':self.profile.label,'transport':'ssh','executable':c.launcher.path,'stateRoot':c.state_root,
                    'state':'source selected; execution isolated in '+c.trial_root}],
                'clusterSettings':{'site':self.profile.label,'preset':self.profile.preset.label,'sshAlias':c.ssh_alias,
                    'connectionDiagnostic':copy.deepcopy(self._connection_diagnostic) if self._error else None,
                    'partition':d.partition,'timeMinutes':d.time_minutes,'cpus':d.cpus,'memoryMiB':d.memory_mib,
                    'profilePath':str(self.profile_path),'qualification':'Single isolated shell workspace; no real AI-agent qualification'}}

    def start_session(self,project_id,request_id):
        if project_id!=self.project_id: raise BackendUnavailable('cluster-project-unregistered')
        try:
            if str(uuid.UUID(request_id))!=request_id: raise ValueError()
        except ValueError: raise BackendUnavailable('cluster-request-id-invalid') from None
        with self._lock:
            self._verify()
            if request_id in self._records: return {'session':self._view(self._records[request_id])}
            if self._working or self._error or any(self._view(r)['state'] not in {'stopped','failed'} for r in self._records.values()):
                raise BackendUnavailable('cluster-workspace-active-or-unverified')
            record={'request_id':request_id,'local_stage':'starting','created_at':time.time()}
            self._records[request_id]=record;self._working=True;self._save()
            threading.Thread(target=self._start_worker,args=(request_id,),daemon=True).start()
            return {'session':self._view(record)}

    def _start_worker(self,request_id):
        try:
            deadline=time.monotonic()+22
            while self._polling and time.monotonic()<deadline: time.sleep(0.05)
            if self._polling: raise BackendUnavailable('cluster-observation-in-progress')
            result=validate_record(self._call('start',request_id=request_id),request_id)
            with self._lock:self._records[request_id]=result;self._observed=time.time();self._save()
        except (BackendUnavailable,OSError,ValueError) as exc:
            with self._lock:
                self._records[request_id]['local_error']='cluster-start-outcome-unconfirmed';self._save()
        finally:
            with self._lock:self._working=False;self._last_attempt=0

    def _target(self,namespace,runtime_id):
        if namespace!=self.namespace or runtime_id not in self._records: raise BackendUnavailable('cluster-session-unregistered')
        record=self._records[runtime_id]
        if not re.fullmatch(r'[0-9a-f]{16}',record.get('session_id','')): raise BackendUnavailable('cluster-session-not-submitted')
        return record

    def stop_session(self,namespace,runtime_id,request_id):
        with self._lock:
            record=self._target(namespace,runtime_id)
            if self._working: raise BackendUnavailable('cluster-operation-in-progress')
            self._working=True
        try:
            result=validate_record(self._call('stop',session_id=record['session_id']),runtime_id)
            with self._lock:self._records[runtime_id]=result;self._observed=time.time();self._save()
            return {'session':self._view(result),'terminationConfirmed':result.get('status',{}).get('JobState') in ENDED}
        finally:
            with self._lock:self._working=False;self._last_attempt=0

    def attach(self,namespace,runtime_id,cols,rows):
        # Release at the ready handshake, not at terminal close. A live terminal
        # must never block inventory or an explicit Stop of its allocation.
        with self._control_guard():
            return self._attach(namespace,runtime_id,cols,rows)

    def _attach(self,namespace,runtime_id,cols,rows):
        from .cluster_attach import AttachmentStartError, FramedSshAttachment
        with self._lock:
            self._verify();record=self._target(namespace,runtime_id)
            if self._view(record)['state']!='running' or runtime_id in self._fences:
                raise BackendUnavailable('cluster-terminal-unavailable')
            data={'fingerprint':self.profile.fingerprint,'session_id':record['session_id']}
            # The pinned installed helper verifies itself and the exact original
            # owner before starting the framed compute-side attachment supervisor.
            remote=[self.profile.connection.remote_python,'-I','-B',self.profile.connection.trial_root+'/dashboard_remote.py',
                'attach','--root',self.profile.connection.trial_root,'--data',json.dumps(data,separators=(',',':'))]
            argv=['/usr/bin/ssh','-T']
            for option in self.driver.SSH_OPTIONS:argv.extend(['-o',option])
            argv += [self.profile.connection.ssh_alias,shlex.join(remote)]
        receipt=private_directory(self.root/('attachment-'+uuid.uuid4().hex))
        self.driver.write_json(receipt/'intent.json',{'request_id':runtime_id,'argv':argv,'cols':cols,'rows':rows})
        try: client=FramedSshAttachment(argv,cwd=self.repo,env=self.driver.child_environment(),cols=cols,rows=rows)
        except Exception as exc:
            # A failed constructor can leave remote cleanup uncertain. Disable
            # further attempts in this controller; the remote lease also expires.
            with self._lock:self._fences[runtime_id]='cluster-attachment-unconfirmed-restart-controller-after-lease'
            failure={'ready':False,'error':str(exc)[:2000],'cleanup':'unconfirmed'}
            if isinstance(exc,AttachmentStartError):
                diagnostics=dict(exc.diagnostics)
                stderr=diagnostics.pop('stderr')
                self.driver.write_new(receipt/'stderr.bin',stderr)
                failure['transport']=diagnostics
            self.driver.write_json(receipt/'result.json',failure)
            raise BackendUnavailable('cluster-attachment-unconfirmed') from None
        try:
            self.driver.write_json(receipt/'result.json',{'ready':True,'owner':client.owner,
                'executable_verification':getattr(client,'executable_verification','not-reported')})
        except (OSError,ValueError):
            try:client.close()
            except Exception:
                with self._lock:self._fences[runtime_id]='cluster-remote-detach-unconfirmed'
            raise BackendUnavailable('cluster-attachment-receipt-unavailable') from None
        return ClusterAttachment(client,self,runtime_id)

    def list_files(self,project_id,path):
        if project_id!=self.project_id: raise BackendUnavailable('cluster-project-unregistered')
        return self._call('files',path=path)
    def read_file(self,project_id,path):
        if project_id!=self.project_id: raise BackendUnavailable('cluster-project-unregistered')
        return self._call('file',path=path)
    def read_config(self,project_id):
        if project_id!=self.project_id: raise BackendUnavailable('cluster-project-unregistered')
        return self._call('config')


def iso(value):
    if not isinstance(value,(int,float)):return None
    from datetime import datetime,timezone
    return datetime.fromtimestamp(value,timezone.utc).isoformat()
