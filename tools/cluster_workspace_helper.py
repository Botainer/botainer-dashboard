#!/usr/bin/env python3
"""Pinned, isolated Slurm shell workspace. Invoked only by the trusted adapter.

BUNDLE is supplied by the repository launcher, never by terminal/project content.
No packages, credentials, image downloads, generic host commands or live-state
writes. An unresolved submission fences later starts. Attach never creates an owner.
"""
from __future__ import annotations
import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
import types
import uuid

ENDED = {"COMPLETED", "CANCELLED", "FAILED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "PREEMPTED", "BOOT_FAIL", "DEADLINE"}
SHELL = '''#!/bin/sh
export PS1='container:/workspace$ '
printf '\\nBotainer dashboard cluster workspace — isolated container shell\\n'
printf 'Files under /workspace persist between sessions. No AI credentials are injected.\\n'
exec /bin/sh -i
'''
SCREENRC = '''startup_message off
escape ^Aa
defescape ^Aa
bind a meta
shell /bin/false
acldel :window:
multiuser off
printcmd ""
zmodem off
bind c
bind ^C
bind :
bind !
'''

def require(ok, reason):
    if not ok: raise RuntimeError(reason)

def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def read_json(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as f:
        info = os.fstat(f.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid(), "unsafe record")
        raw = f.read(2 * 1024 * 1024 + 1)
    require(len(raw) <= 2 * 1024 * 1024, "record too large")
    return json.loads(raw)

def atomic(path, value):
    path = Path(path)
    temp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(value, f, sort_keys=True); f.write('\n'); f.flush(); os.fsync(f.fileno())
    os.replace(temp, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)

def module(name):
    data = BUNDLE[name].encode()
    result = types.ModuleType('dashboard_' + name.replace('.', '_'))
    result.__file__ = '<pinned-' + name + '>'
    result.__dict__['__dashboard_trial_source__'] = data
    sys.modules[result.__name__] = result
    exec(compile(data, result.__file__, 'exec'), result.__dict__)
    return result

def bounded(argv, env, timeout=20):
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=env, start_new_session=True)
    outputs = [bytearray(), bytearray()]
    selector = selectors.DefaultSelector()
    deadline = time.monotonic() + timeout
    try:
        for index, stream in enumerate((process.stdout, process.stderr)):
            os.set_blocking(stream.fileno(), False); selector.register(stream, selectors.EVENT_READ, index)
        while selector.get_map():
            require(time.monotonic() < deadline, "scheduler command timed out; outcome unknown")
            for key, _ in selector.select(0.1):
                chunk = os.read(key.fd, 16384)
                if not chunk: selector.unregister(key.fileobj); continue
                outputs[key.data].extend(chunk)
                require(len(outputs[key.data]) <= 256 * 1024, "scheduler output limit")
        process.wait(timeout=max(0.01, deadline-time.monotonic()))
        require(process.returncode == 0, 'scheduler command failed: ' + outputs[1][-1000:].decode(errors='replace'))
        return outputs[0].decode()
    finally:
        selector.close()
        if process.returncode is None:
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
        process.wait(); process.stdout.close(); process.stderr.close()

def prepare(root, data):
    h = module('prepare_cluster_trial.py')
    h.FIXTURE = SHELL
    h.prepare(types.SimpleNamespace(trial_root=str(root), source_root=data['source_root'],
        image=data['image'], interpreter=data['remote_python']))
    descriptor = read_json(root/'descriptor.json')
    defaults = data['defaults']
    require(defaults['gpus'] == 0 and 'qos' not in defaults, 'GPU and QoS shell workflows unqualified')
    require(defaults['memory_mib'] % 1024 == 0, 'memory must be whole GiB for this Botainer adapter')
    scheduler = {k:defaults[k] for k in ('partition','cpus','time_minutes','gpus')}
    scheduler['memory_gb'] = defaults['memory_mib']//1024
    if 'account' in defaults: scheduler['account'] = defaults['account']
    require(re.fullmatch(r'[A-Za-z0-9_.-]+', scheduler['partition']), 'invalid partition')
    require(1 <= scheduler['cpus'] <= 16 and 1 <= scheduler['memory_gb'] <= 64 and
            1 <= scheduler['time_minutes'] <= 240, 'prototype resource limit exceeded')
    descriptor['scheduler'] = scheduler
    descriptor['terminal'] = 'container-shell'
    config = read_json(root/'project/.botainer/config.yaml')
    config['plugins']['hpc-launcher'] = scheduler
    atomic(root/'descriptor.json', descriptor); atomic(root/'project/.botainer/config.yaml', config)
    receipt = read_json(root/'prepared.json')
    receipt['protected_files_sha256'] = {str(p.relative_to(root)):digest(p) for p in h.immutable_inputs(root)}
    atomic(root/'prepared.json', receipt)
    h.new_file(root/'dashboard_remote.py', globals()['__dashboard_trial_source__'])
    h.new_file(root/'cluster_attach_supervisor.py', BUNDLE['cluster_attach_supervisor.py'])
    h.new_file(root/'.screenrc', SCREENRC)
    (root/'gui-sessions').mkdir(mode=0o700); (root/'scr').mkdir(mode=0o700)
    require(len(str(root/'scr').encode()) + 28 < 108, 'workspace path too long for Screen socket')
    atomic(root/'gui-install.json', {'version':1, 'profile_fingerprint':data['profile_fingerprint'],
        'files': {n:digest(root/n) for n in ('dashboard_remote.py','cluster_attach_supervisor.py','.screenrc')},
        'project_uuid':descriptor['project_uuid']})
    atomic(root/'gui-registry.json', {'requests':{}})
    return {'prepared':True, 'project_uuid':descriptor['project_uuid'], 'project_path':str(root/'project')}

def load(root):
    require(root.is_absolute() and root.resolve(strict=True) == root and root.name.startswith('botainer-dashboard-test-'), 'invalid workspace root')
    info = root.stat()
    require(info.st_uid == os.getuid() and info.st_mode & 0o077 == 0, 'workspace must be private')
    install = read_json(root/'gui-install.json')
    require(set(install['files']) == {'dashboard_remote.py','cluster_attach_supervisor.py','.screenrc'}, 'helper manifest changed')
    for name, expected in install['files'].items():
        require(not (root/name).is_symlink() and digest(root/name)==expected, 'installed helper changed')
    source = globals().get('__dashboard_trial_source__') or Path(__file__).read_bytes()
    require(hashlib.sha256(source).hexdigest() == install['files']['dashboard_remote.py'], 'helper revision mismatch')
    h = module('prepare_cluster_trial.py')
    descriptor = read_json(root/'descriptor.json')
    require(descriptor['trial_root'] == str(root) and descriptor['project_uuid']==install['project_uuid'], 'workspace identity changed')
    return h, descriptor, install

@contextlib.contextmanager
def locked(root):
    fd = os.open(root/'gui-operation.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid() and info.st_nlink==1, 'invalid lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally: os.close(fd)

def environment(root, h, descriptor, tools):
    env = h.safe_environment(root, descriptor)
    env['PATH'] = str(Path(tools['sbatch']).parent)+':/usr/bin:/bin'
    env['SCREENDIR'] = str(root/'scr')
    env['SYSSCREENRC'] = '/dev/null'
    env['SYSTEM_SCREENRC'] = '/dev/null'
    return env

def observe(root, h, descriptor, record):
    require(record.get('job_id', '').isdigit(), 'submission unresolved')
    tools = record['tools']
    for name, path in tools.items(): require(digest(path)==record['tool_sha256'][name], 'scheduler tool changed')
    env = environment(root,h,descriptor,tools)
    probe = module('cluster_runtime_probe.py')
    fields = probe.parse_job(bounded([tools['scontrol'],'--oneliner','show','job',record['job_id']],env))
    require(fields['JobId']==record['job_id'] and fields.get('JobName')==record['job_name'] and
        fields.get('WorkDir')==str(root/'project') and fields.get('UserId','').endswith(f'({os.getuid()})'), 'job identity mismatch')
    identity = probe.job_identity(fields)
    if record.get('job_identity'): require(identity==record['job_identity'], 'allocation identity changed')
    record['job_identity'] = identity
    record['status'] = {k:fields.get(k) for k in ('JobState','Reason','BatchHost','StartTime','EndTime','TimeLimit','ExitCode','NumNodes')}
    record['observed_at'] = time.time()
    return fields, env

def qualify_batch_script(original, project_uuid, sid):
    """Keep Botainer's native owner; apply only the prepared site's restrictions.

    Older fork-and-poll scripts must be re-prepared from a qualified source,
    never silently repaired here. The trial still requires its exact Screen
    executable/config and runtime policy checks;
    these restrictions are independent of Botainer's session lifetime logic.
    """
    old_name = '#SBATCH --job-name=botainer-'+project_uuid[:8]+'\n'
    native_owner = '    exec screen -D -m -S "$SCREEN_SID" '
    lines = original.splitlines(keepends=True)
    owners = [index for index, line in enumerate(lines) if line.startswith(native_owner)]
    require(len(owners) == 1, 'unrecognized native nonforking Screen owner; re-prepare with qualified Botainer')
    require(not any(re.match(r'\s*(?:exec\s+)?screen\s+-dmS\b', line) or
                    re.match(r'\s*(?:while\s+)?screen\s+-ls\b', line) for line in lines),
            'legacy Screen fork/poll script refused; re-prepare with qualified Botainer')
    screen_commands = [index for index, line in enumerate(lines)
                       if re.match(r'\s*(?:exec\s+)?(?:/usr/bin/)?screen\s+', line)]
    require(screen_commands == owners, 'unexpected additional Screen command')
    require(original.count(old_name)==1 and original.count('set -euo pipefail\n')==1,
            'unrecognized batch script')
    # Replace only the command prefix, preserving the cage argv byte-for-byte.
    index = owners[0]
    lines[index] = ('    exec /usr/bin/screen -c "$HOME/.screenrc" -D -m -S "$SCREEN_SID" '+
                    lines[index][len(native_owner):])
    name = 'botainer-dashboard-test-'+sid
    script = ''.join(lines).replace(old_name, '#SBATCH --job-name='+name+'\n')
    script = script.replace('set -euo pipefail\n',
        'set -euo pipefail\n'+'test -x /usr/bin/screen && test -x /usr/bin/apptainer && test -x /usr/bin/python3 || exit 78\n'+
        'test "$(command -v screen)" = /usr/bin/screen && test "$(command -v apptainer)" = /usr/bin/apptainer || exit 78\n'+
        '/usr/bin/python3 -I -S "$HOME/cluster_attach_supervisor.py" --root "$HOME" --check-policy || exit 78\n')
    return name, script

def start(root,h,descriptor,registry,request):
    require(isinstance(request,str) and str(uuid.UUID(request))==request, 'invalid request identity')
    if request in registry['requests']: return registry['requests'][request]
    require(len(registry['requests']) < 200, 'prototype history limit reached')
    for record in registry['requests'].values():
        if record.get('status',{}).get('JobState') not in ENDED:
            if record.get('job_id'): observe(root,h,descriptor,record)
            require(record.get('status',{}).get('JobState') in ENDED, 'workspace already active or unresolved')
    record = {'request_id':request, 'created_at':time.time(), 'stage':'preparing'}
    registry['requests'][request] = record; atomic(root/'gui-registry.json',registry)
    # Persisted intent precedes composition too: a failed attempt never repeats
    # hooks or submission on a replay of the same request ID.
    # Composition intentionally scrubs the environment; resolve the reviewed
    # site scheduler tools before entering that isolated Botainer environment.
    tools = module('cluster_runtime_probe.py').tool_paths()
    with contextlib.redirect_stdout(sys.stderr): preflight = h.compose(root,descriptor)
    sid = preflight['session_id']
    require(isinstance(sid,str) and re.fullmatch(r'[0-9a-f]{16}',sid), 'invalid session identity')
    evidence = root/'preflights'/sid
    expected_session=root/'state/state'/descriptor['project_uuid']/'sessions'/sid
    require(preflight['project_uuid']==descriptor['project_uuid'] and
        preflight['session_directory']==str(expected_session) and expected_session.resolve(strict=True)==expected_session and
        preflight['evidence_directory']==str(evidence) and evidence.resolve(strict=True)==evidence,
        'composition target mismatch')
    require(set(preflight['artifacts_sha256'])=={'spec.json','plan.json','argv.json','unsubmitted.sbatch'},'composition artifact set mismatch')
    record['session_id'] = sid
    for name,d in preflight['artifacts_sha256'].items(): require(digest(evidence/name)==d, 'composition artifact changed')
    original = (evidence/'unsubmitted.sbatch').read_text()
    name, script = qualify_batch_script(original, descriptor['project_uuid'], sid)
    directory = root/'gui-sessions'/sid; directory.mkdir(mode=0o700)
    h.new_file(directory/'submitted.sbatch',script,0o700)
    record.update(stage='dispatch-claimed',job_name=name,tools=tools,tool_sha256={k:digest(v) for k,v in tools.items()})
    atomic(root/'gui-registry.json',registry)
    env = environment(root,h,descriptor,tools)
    reply = bounded([tools['sbatch'],'--parsable','--no-requeue','--nodes=1','--ntasks=1','--chdir='+str(root/'project'),str(directory/'submitted.sbatch')],env)
    require(re.fullmatch(r'[0-9]+(?:;[A-Za-z0-9_.-]+)?\s*',reply), 'ambiguous submission reply')
    record.update(job_id=reply.strip().split(';')[0],stage='submitted')
    atomic(root/'gui-registry.json',registry)
    from botainer.state import session_record
    session_record.update_runtime(Path(preflight['session_directory']),slurm_jobid=record['job_id'],screen_session_id='botainer-'+record['job_id'])
    observe(root,h,descriptor,record); atomic(root/'gui-registry.json',registry)
    return record

def owner(root,h,descriptor,record):
    fields, env = observe(root,h,descriptor,record)
    require(fields['JobState']=='RUNNING' and fields.get('NumNodes')=='1', 'allocation is not running on one node')
    node = fields.get('BatchHost',''); require(re.fullmatch(r'[A-Za-z0-9_.-]+',node), 'invalid node')
    argv = [record['tools']['srun'],'--jobid='+record['job_id'],'--overlap','--exact','--nodes=1','--ntasks=1','--cpus-per-task=1','--nodelist='+node]
    probe = module('cluster_runtime_probe.py')
    value = json.loads(bounded([*argv,'/usr/bin/python3','-I','-S','-c',probe.NODE_PROBE,str(root/'scr'),'botainer-'+record['job_id']],env,timeout=15))
    require(value['node'].split('.')[0]==node.split('.')[0], 'node identity mismatch')
    value['job_start_time'] = fields.get('StartTime')
    path = root/'gui-sessions'/record['session_id']/'owner.json'
    if path.exists(): require(read_json(path)==value, 'original owner changed')
    else: atomic(path,value)
    return value,argv,env

def safe_parts(value):
    require(isinstance(value,str) and len(value)<=1024, 'invalid file path')
    if not value: return []
    parts=value.split('/')
    require(len(parts)<=24 and all(p and not p.startswith('.') and '\\' not in p and
        not any(ord(c)<32 or ord(c)==127 for c in p) for p in parts), 'invalid file path')
    secrets={'credentials','credential','secret','secrets','token','tokens','password','passwords','auth','login','id_rsa','id_ed25519','authorized_keys','known_hosts'}
    require(all(p.lower().split('.')[0] not in secrets and not p.lower().endswith(('.pem','.key','.p12','.pfx','.keystore','.kdbx')) for p in parts), 'private file excluded')
    return parts

def files(root,path,read=False):
    parts=safe_parts(path)
    fd=os.open(root/'project',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for part in parts[:-1] if read else parts:
            new=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=new
        if read:
            require(bool(parts),'file required')
            child=os.open(parts[-1],os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW,dir_fd=fd)
            with os.fdopen(child,'rb') as f:
                info=os.fstat(f.fileno());require(stat.S_ISREG(info.st_mode) and info.st_nlink==1,'regular unlinked file required')
                data=f.read(65537)
            require(len(data)<=65536 and b'\0' not in data,'text preview limit')
            return {'path':path,'text':data.decode('utf-8'),'size':len(data)}
        entries=[]
        candidates=[]
        with os.scandir(fd) as scan:
            for entry in scan:
                candidates.append(entry)
                require(len(candidates)<=500,'directory entry limit')
        for entry in sorted(candidates,key=lambda e:e.name):
            try: safe_parts(entry.name)
            except RuntimeError: continue
            info=entry.stat(follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)): continue
            entries.append({'name':entry.name,'path':'/'.join([*parts,entry.name]),'kind':'directory' if stat.S_ISDIR(info.st_mode) else 'file','size':info.st_size})
            require(len(entries)<=500,'directory entry limit')
        return {'path':path,'entries':entries}
    finally: os.close(fd)

def read_config(root):
    fd=os.open(root/'project',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        child=os.open('.botainer',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
        os.close(fd);fd=child
        child=os.open('config.yaml',os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW,dir_fd=fd)
        with os.fdopen(child,'rb') as f:
            info=os.fstat(f.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_nlink==1,'config must be regular and unlinked')
            raw=f.read(65537)
        require(len(raw)<=65536,'config preview limit')
        return {'text':raw.decode(),'revision':hashlib.sha256(raw).hexdigest(),
            'path':str(root/'project/.botainer/config.yaml'),'writable':False}
    finally: os.close(fd)

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=('prepare','status','start','stop','attach','files','file','config'))
    p.add_argument('--root',required=True);p.add_argument('--data',default='{}');args=p.parse_args()
    root=Path(args.root);data=json.loads(args.data)
    if args.action=='prepare':
        with contextlib.redirect_stdout(sys.stderr): result=prepare(root,data)
    else:
        h,descriptor,install=load(root)
        require(data.get('fingerprint')==install['profile_fingerprint'],'profile mismatch')
        if args.action in ('files','file'): result=files(root,data['path'],args.action=='file')
        elif args.action=='config': result=read_config(root)
        else:
            with locked(root):
                registry=read_json(root/'gui-registry.json')
                if args.action=='start': result=start(root,h,descriptor,registry,data['request_id'])
                elif args.action=='status':
                    for rec in registry['requests'].values():
                        if rec.get('job_id') and rec.get('status',{}).get('JobState') not in ENDED:
                            try: observe(root,h,descriptor,rec);rec.pop('observation_error',None)
                            except (RuntimeError,OSError,ValueError,subprocess.SubprocessError): rec['observation_error']='scheduler-observation-unavailable'
                    atomic(root/'gui-registry.json',registry)
                    result={'project_uuid':descriptor['project_uuid'],'sessions':list(registry['requests'].values())}
                else:
                    matches=[r for r in registry['requests'].values() if r.get('session_id')==data['session_id']]
                    require(len(matches)==1,'session not registered');rec=matches[0]
                    if args.action=='stop':
                        fields,env=observe(root,h,descriptor,rec)
                        if fields['JobState'] not in ENDED:
                            bounded([rec['tools']['scancel'],'--user='+pwd.getpwuid(os.getuid()).pw_name,'--',rec['job_id']],env)
                        observe(root,h,descriptor,rec);atomic(root/'gui-registry.json',registry);result=rec
                    else:
                        own,argv,env=owner(root,h,descriptor,rec);atomic(root/'gui-registry.json',registry)
            if args.action=='attach':
                argv += ['--unbuffered','/usr/bin/python3','-I','-S',str(root/'cluster_attach_supervisor.py'),'--root',str(root),'--owner-json',json.dumps(own,separators=(',',':'))]
                os.execve(argv[0],argv,env)
    print(json.dumps(result,sort_keys=True))

if __name__=='__main__':
    try: main()
    except (OSError,RuntimeError,ValueError,KeyError,subprocess.SubprocessError) as exc:
        print('cluster workspace refused: '+str(exc),file=sys.stderr);raise SystemExit(2)
