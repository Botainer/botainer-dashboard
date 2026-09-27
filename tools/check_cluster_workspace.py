#!/usr/bin/env python3
"""Explicit live acceptance for the authorized isolated cluster shell only."""
from pathlib import Path
import argparse
import json
import os
import re
import select
import shlex
import signal
import subprocess
import sys
import time
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from botainer_dashboard.cluster_backend import ClusterBackend, ENDED

ROOT=Path(__file__).resolve().parents[1]
ESCAPES=re.compile(rb'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]')
HOST_MARKER_PROBE=r'''
import json,os,re,stat,sys
from pathlib import Path
root=Path(sys.argv[1]);sid=sys.argv[2];name=sys.argv[3]
assert root.is_absolute() and root.resolve(strict=True)==root and root.name.startswith('botainer-dashboard-test-')
assert re.fullmatch('[0-9a-f]{16}',sid) and re.fullmatch('[0-9a-f]{32}[.]host-escape-marker',name)
parent=root/'gui-sessions'/sid
for path in (root,root/'gui-sessions',parent):
    info=path.lstat()
    assert stat.S_ISDIR(info.st_mode) and info.st_uid==os.getuid() and not info.st_mode&0o077
    assert path.resolve(strict=True)==path
assert os.access('/usr/bin/touch',os.X_OK) and Path('/usr/bin/touch').is_file()
marker=parent/name
try: marker.lstat();present=True
except FileNotFoundError: present=False
print(json.dumps({'root':str(root),'session_id':sid,'marker':str(marker),'present':present,'touch_available':True}))
'''

def send(client,text):
    raw=text.encode() if isinstance(text,str) else text
    if not isinstance(raw,bytes):raise TypeError('terminal input must be text or bytes')
    deadline=time.monotonic()+4
    while raw:
        if time.monotonic()>deadline:raise RuntimeError('input deadline')
        count=client.write(raw)
        if type(count) is not int or not 0<=count<=len(raw):raise RuntimeError('invalid terminal write count')
        raw=raw[count:]
        if raw:time.sleep(.02)

def expect(client,pattern,log):
    deadline=time.monotonic()+15;buf=bytearray()
    while time.monotonic()<deadline:
        data=client.read(65536,timeout=.25)
        if data==b'':raise RuntimeError('unexpected terminal EOF')
        if data:
            buf.extend(data);log.extend(data)
            if len(log)>512*1024:raise RuntimeError('acceptance output bound')
            match=re.search(pattern,ESCAPES.sub(b'',buf))
            if match:return match
    raise RuntimeError('terminal response deadline')

def kill_viewer(child):
    """Only our unreaped viewer group; close every local pipe after reaping."""
    try:
        if child.returncode is None:
            try:os.killpg(child.pid,signal.SIGKILL)
            except ProcessLookupError:pass
        child.wait(timeout=3)
    finally:
        for stream in (child.stdin,child.stdout,child.stderr):
            if stream is not None:stream.close()

def viewer_ready(child):
    deadline=time.monotonic()+45;output=bytearray();errors=bytearray()
    streams={child.stdout.fileno():output,child.stderr.fileno():errors}
    for descriptor in streams:os.set_blocking(descriptor,False)
    while streams and time.monotonic()<deadline:
        readable,_,_=select.select(list(streams),[],[],max(0,deadline-time.monotonic()))
        for descriptor in readable:
            data=os.read(descriptor,4096)
            if not data:del streams[descriptor];continue
            streams[descriptor].extend(data)
            if len(output)>4096 or len(errors)>16384:raise RuntimeError('viewer startup output bound')
        if b'\n' in output:
            if bytes(output)!=b'ATTACHED\n':raise RuntimeError('unexpected viewer startup output')
            return
    raise RuntimeError('abrupt-loss child did not attach')

def require_original_job(backend,request,record):
    backend.refresh()
    current=backend._records.get(request)
    if not current or backend._view(current)['state']!='running':raise RuntimeError('negative test allocation is unverified')
    for field in ('request_id','session_id','job_id','job_identity'):
        if current.get(field)!=record.get(field):raise RuntimeError('negative test allocation identity changed')

def recover_cleanup_record(backend,request):
    """Adopt only this exact launch request after the trusted remote observer
    has checked scheduler ownership. Never retry a launch or search by name.
    """
    backend.refresh();record=backend._records.get(request)
    if backend._error or not record or record.get('request_id')!=request or record.get('observation_error'):
        raise RuntimeError('launch request could not be reconciled for cleanup')
    sid=record.get('session_id');job=record.get('job_id');identity=record.get('job_identity')
    if (not isinstance(sid,str) or not re.fullmatch(r'[0-9a-f]{16}',sid) or
        not isinstance(job,str) or not re.fullmatch(r'[0-9]{1,20}',job) or not isinstance(identity,dict) or
        identity.get('JobId')!=job or identity.get('JobName')!='botainer-dashboard-test-'+sid or
        identity.get('WorkDir')!=backend.profile.connection.trial_root+'/project' or
        not isinstance(identity.get('UserId'),str) or not re.fullmatch(r'[^()]+\([0-9]+\)',identity['UserId']) or
        not isinstance(identity.get('SubmitTime'),str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}',identity['SubmitTime'])):
        raise RuntimeError('exact session and observed scheduler identity unavailable for cleanup')
    return record

def host_marker_absent(backend,request,record,name,receipt,label):
    """Read-only login-node check of this job's otherwise unbound private path."""
    require_original_job(backend,request,record);backend._verify()
    connection=backend.profile.connection
    remote=[connection.remote_python,'-I','-S','-c',HOST_MARKER_PROBE,
        connection.trial_root,record['session_id'],name]
    argv=['/usr/bin/ssh','-T']
    for option in backend.driver.SSH_OPTIONS:argv.extend(['-o',option])
    argv.extend([connection.ssh_alias,shlex.join(remote)])
    backend.driver.write_json(receipt/(label+'-intent.json'),{'argv':argv,'read_only':True,'request_id':request})
    result=backend.driver.bounded_exchange(argv,b'',timeout=20,stdout_limit=16384,stderr_limit=16384,
        env=backend.driver.child_environment())
    stdout=result.pop('stdout');stderr=result.pop('stderr')
    backend.driver.write_new(receipt/(label+'-stdout.bin'),stdout)
    backend.driver.write_new(receipt/(label+'-stderr.bin'),stderr)
    backend.driver.write_json(receipt/(label+'-result.json'),result)
    if result['transport_status']!='complete' or result['returncode']!=0:raise RuntimeError('host marker observation unavailable')
    value=json.loads(stdout)
    expected=connection.trial_root+'/gui-sessions/'+record['session_id']+'/'+name
    if value!={'root':connection.trial_root,'session_id':record['session_id'],'marker':expected,
               'present':False,'touch_available':True}:
        raise RuntimeError('host-only escape marker present or observation invalid')
    require_original_job(backend,request,record)
    return expected

def screen_negative_checks(client,backend,request,record,token,pid,receipt,log):
    """Literal shortcut delivery + absent host marker + same shell; not a
    comprehensive proof against every possible host escape or Screen defect.

    OSC83 syntax: https://www.gnu.org/software/screen/manual/html_node/Control-Sequences.html
    """
    nonce=uuid.uuid4().hex;name=nonce+'.host-escape-marker'
    marker=host_marker_absent(backend,request,record,name,receipt,'escape-before')
    # Keep the Screen command grammar unambiguous; decline unusual path syntax
    # instead of treating shell quoting as Screen's quoting language.
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+',marker):raise RuntimeError('negative test requires a simple absolute marker path')
    parent=marker.rsplit('/',1)[0]
    send(client,"test ! -d "+shlex.quote(parent)+" && printf 'HOST_PATH_UNBOUND:%s\\n' "+shlex.quote(nonce)+"\n")
    expect(client,b'HOST_PATH_UNBOUND:'+nonce.encode()+rb'\r?\n',log)
    command='screen -t dashboard-escape-test /usr/bin/touch '+marker
    # dd receives bytes under raw/no-echo mode, so shell editing and command
    # echo cannot counterfeit the received hex bytes or execute the test input.
    payload=b'\x01c\x01\x03\x01:'+command.encode()+b'\r\x01!\r'
    capture=("bd_saved=$(stty -g); stty raw -echo; printf 'RAW_READY:%s\\n' "+shlex.quote(nonce)+
        "; bd_bytes=$(dd bs=1 count="+str(len(payload))+" 2>/dev/null | od -An -v -tx1); stty \"$bd_saved\"; "+
        "printf 'RAW_RESULT:%s\\n%s\\nRAW_ID:%s:%s:%s\\n' "+shlex.quote(nonce)+" \"$bd_bytes\" "+
        shlex.quote(nonce)+" \"$BD_TEST\" \"$$\"\n")
    send(client,capture);expect(client,b'RAW_READY:'+nonce.encode()+rb'\r?\n',log)
    # A prefix in its own frame also tests the quote boundary across frames.
    for part in re.split(rb'(\x01)',payload):
        if part:send(client,part)
    literal=expect(client,b'RAW_RESULT:'+nonce.encode()+rb'\r?\n([0-9a-f \r\n]+)RAW_ID:'+
        nonce.encode()+b':'+token.encode()+rb':([0-9]+)\r?\n',log)
    if bytes.fromhex(literal.group(1).decode())!=payload or literal.group(2)!=pid:
        raise RuntimeError('Screen shortcuts did not remain literal input to the original container shell')
    host_marker_absent(backend,request,record,name,receipt,'escape-after-input')
    # This is terminal output interpreted by Screen, not a user input shortcut.
    # If accepted, the only attempted host command is touch of our own unique
    # marker, in a directory proved unavailable inside the container.
    send(client,"printf '\\033]83;%s\\007' "+shlex.quote(command)+
        "; printf 'OSC_DONE:%s:%s:%s\\n' "+shlex.quote(nonce)+" \"$BD_TEST\" \"$$\"\n")
    if expect(client,b'OSC_DONE:'+nonce.encode()+b':'+token.encode()+rb':([0-9]+)\r?\n',log).group(1)!=pid:
        raise RuntimeError('OSC83 test replaced the original container shell')
    host_marker_absent(backend,request,record,name,receipt,'escape-after-output')
    return name

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--profile',type=Path,required=True)
    p.add_argument('--hold-session');args=p.parse_args()
    backend=ClusterBackend(ROOT,args.profile)
    if args.hold_session:
        backend.refresh();client=backend.attach(backend.namespace,args.hold_session,100,30)
        try:
            print('ATTACHED',flush=True)
            while True:
                if client.read(65536,timeout=1)==b'':raise RuntimeError('viewer terminal ended')
        finally:client.close()
    receipt=backend.root/('acceptance-'+uuid.uuid4().hex);receipt.mkdir(mode=0o700)
    print('Private acceptance receipt: '+str(receipt),flush=True)
    request=str(uuid.uuid4());result={'passed':False,'request_id':request};record=None
    client=None;child=None;log=bytearray()
    try:
        print('Submitting one labelled five-minute shell job.',flush=True)
        record=backend._call('start',request_id=request)
        deadline=time.monotonic()+150
        while time.monotonic()<deadline:
            backend.refresh();record=backend._records.get(request,record)
            state=record.get('status',{}).get('JobState')
            if state=='RUNNING':break
            if state in ENDED:raise RuntimeError('allocation ended before attach')
            time.sleep(3)
        if state!='RUNNING':raise RuntimeError('queue wait deadline')
        print('Running; checking real shell, resize and files.',flush=True)
        client=backend.attach(backend.namespace,request,100,30)
        token=uuid.uuid4().hex
        send(client,"BD_TEST="+token+"; printf 'IDENTITY:%s:%s\\n' \"$BD_TEST\" \"$$\"\n")
        first=expect(client,rb'IDENTITY:'+token.encode()+rb':([0-9]+)\r?\n',log).group(1)
        send(client,"printf '__%s__\\n' 'hello'; printf 'made in container\\n' > gui-test.txt\n")
        expect(client,rb'__hello__',log)
        client.resize(120,37);time.sleep(.2);send(client,'stty size\n');expect(client,rb'37\s+120',log)
        print('Checking literal Screen shortcuts and the bounded OSC83 host marker attempt.',flush=True)
        marker=screen_negative_checks(client,backend,request,record,token,first,receipt,log)
        client.close();client=None
        file=backend.read_file(backend.project_id,'gui-test.txt')
        if file['text']!='made in container\n':raise RuntimeError('remote file mismatch')
        config=backend.read_config(backend.project_id)
        if len(config['revision'])!=64 or config['writable']:raise RuntimeError('config scope mismatch')
        client=backend.attach(backend.namespace,request,120,37)
        send(client,"printf 'IDENTITY:%s:%s\\n' \"$BD_TEST\" \"$$\"\n")
        if expect(client,rb'IDENTITY:'+token.encode()+rb':([0-9]+)\r?\n',log).group(1)!=first:raise RuntimeError('normal reconnect replaced process')
        client.close();client=None
        print('Normal reconnect passed; killing our separate viewer process to test lease expiry.',flush=True)
        child=subprocess.Popen([sys.executable,'-B',str(Path(__file__).resolve()),'--profile',str(args.profile),'--hold-session',request],
            cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
        viewer_ready(child)
        kill_viewer(child);child=None
        time.sleep(15)
        backend.refresh()
        client=backend.attach(backend.namespace,request,120,37)
        send(client,"printf 'IDENTITY:%s:%s\\n' \"$BD_TEST\" \"$$\"\n")
        if expect(client,rb'IDENTITY:'+token.encode()+rb':([0-9]+)\r?\n',log).group(1)!=first:raise RuntimeError('abrupt reconnect replaced process')
        client.close();client=None
        host_marker_absent(backend,request,record,marker,receipt,'escape-after-reconnect')
        result.update(passed=True,normal_reconnect=True,abrupt_viewer_loss_reconnect=True,echo=True,resize=[37,120],files=True,config_read=True,
            screen_shortcuts_literal=True,osc83_host_marker_absent=True,negative_test_same_shell=True,
            negative_test_scope='Exact literal shortcut input, absent host-only marker, unchanged shell PID/token; not a comprehensive host-escape proof.')
    except Exception as exc:result['error']=str(exc)
    finally:
        if client:
            try:client.close()
            except Exception as exc:result['attachment_cleanup_error']=str(exc)
        if child:
            try:kill_viewer(child)
            except Exception as exc:result['viewer_cleanup_error']=str(exc)
        if record is None:
            try:
                record=recover_cleanup_record(backend,request)
                result['cleanup_record_recovered']=True
            except Exception as exc:
                result.update(cleanup_unconfirmed=True,reconciliation_needed=True,cleanup_error=str(exc))
        if record and record.get('session_id'):
            try:
                backend._call('stop',session_id=record['session_id'])
                deadline=time.monotonic()+30
                while time.monotonic()<deadline:
                    backend.refresh();current=backend._records[request]
                    if current.get('status',{}).get('JobState') in ENDED:
                        result['termination_confirmed']=True;break
                    time.sleep(1)
            except Exception as exc:result['cleanup_error']=str(exc)
        if not result.get('termination_confirmed'):
            result.update(cleanup_unconfirmed=True,reconciliation_needed=True)
        result['passed']=result['passed'] and result.get('termination_confirmed',False)
        backend.driver.write_new(receipt/'terminal.bin',bytes(log))
        backend.driver.write_json(receipt/'result.json',result)
    print(json.dumps(result|{'receipt':str(receipt)}),flush=True)
    return 0 if result['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
