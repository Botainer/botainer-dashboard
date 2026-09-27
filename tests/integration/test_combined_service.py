"""Real HTTP authorization/routing across two deterministic workspace backends.

No SSH, containers or Botainer. This gate complements actual GUI acceptance.
"""
import copy
from types import SimpleNamespace
from uuid import uuid4
import unittest

from botainer_dashboard.backend_errors import BackendUnavailable
from botainer_dashboard.combined_backend import CombinedBackend
import test_workspace_service as support


class SiteFixture:
    def __init__(self):
        self.profile = SimpleNamespace(label='Example cluster')
        self.calls = []
        self.offline = False
        self.project = {'id':'remote.project','name':'Remote project'}
        self.session = {'projectId':'remote.project','contextNamespace':'remote.namespace',
                        'runtimeId':'shared-id','state':'running','label':'Remote shell',
                        'capabilities':{'attachTerminal':True,'stopSession':True}}

    def snapshot(self):
        if self.offline: raise BackendUnavailable('test-site-offline')
        return {'projects':[self.project], 'sessions':[self.session],
                'capabilities':{'filesRead':True,'configRead':True,'configWrite':False,'startSession':True}}

    def read_file(self, project, path):
        self.calls.append(('file',project,path))
        return {'text':'remote literal content'}

    def read_config(self, project):
        self.calls.append(('config',project))
        return {'text':'remote: true','revision':'remote-revision','writable':False}

    def start_session(self, project, request):
        self.calls.append(('start',project,request))
        return {'session':copy.deepcopy(self.session)}

    def stop_session(self, namespace, runtime, request):
        self.calls.append(('stop',namespace,runtime,request))
        return {'session':dict(self.session,state='stopped')}

    def read_terminal_history(self, namespace, runtime):
        self.calls.append(('history',namespace,runtime))
        return {'text':'Selected terminal output\n','kind':'terminal-snapshot'}


class CombinedServiceTests(unittest.TestCase):
    # Reuse only server/auth helpers, not the other test methods.
    shutdown = support.WorkspaceServiceTests.shutdown
    request = support.WorkspaceServiceTests.request
    post = support.WorkspaceServiceTests.post
    get = support.WorkspaceServiceTests.get

    def setUp(self):
        from unittest.mock import patch
        fixture = support.WorkspaceFixture
        def factory(root):
            self.local = fixture(root)
            original = self.local.snapshot
            self.local.snapshot = lambda: original() | {'capabilities':{
                'projectCreate':True,'projectRegister':True,'filesRead':True,'configRead':True,'configWrite':True,
                'dashboardConfigRead':True,'dashboardConfigWrite':True}}
            self.remote = SiteFixture()
            return CombinedBackend(self.local, clusters={'site':self.remote})
        with patch('test_workspace_service.WorkspaceFixture', side_effect=factory):
            support.WorkspaceServiceTests.setUp(self)
        self.backend.refresh()

    def create(self, **changes):
        body={'workspaceId':'local','mode':'create','rootId':'local','path':'sample','name':'Sample',
              'installationId':'fixture','requestId':str(uuid4())}
        return body | changes

    def test_create_requires_workspace_and_routes_only_selected_registered_root(self):
        body=self.create()
        self.post('/api/projects', {key:value for key,value in body.items() if key!='workspaceId'},400)
        self.post('/api/projects',body | {'workspaceId':'site'},409)
        self.post('/api/projects',body | {'rootId':'unregistered'},409)
        project=self.post('/api/projects',body)['project']
        self.assertEqual(project['workspaceId'],'local')
        self.assertEqual(self.remote.calls,[])
        self.assertEqual(self.local.calls.count('create'),1)

    def test_remote_file_and_config_never_route_to_local_and_writes_stay_refused(self):
        self.post('/api/projects/remote.project/file',{'path':'notes.txt'})
        document=self.get('/api/projects/remote.project/config')
        self.assertFalse(document['writable'])
        self.post('/api/projects/remote.project/config/validate',{'text':'change','revision':'remote-revision'},409)
        self.post('/api/projects/remote.project/config/save',{'text':'change','revision':'remote-revision','requestId':str(uuid4())},409)
        self.assertEqual(self.remote.calls,[('file','remote.project','notes.txt'),('config','remote.project')])
        self.assertEqual(self.local.calls,[])

    def test_session_action_needs_namespace_and_unknown_target_never_dispatches(self):
        request=str(uuid4())
        self.post('/api/sessions/shared-id/stop',{'contextNamespace':'wrong.namespace','requestId':request},409)
        self.post('/api/sessions/missing-id/stop',{'contextNamespace':'remote.namespace','requestId':request},409)
        self.assertEqual(self.remote.calls,[])
        result=self.post('/api/sessions/shared-id/stop',{'contextNamespace':'remote.namespace','requestId':request})
        self.assertEqual(result['session']['workspaceId'],'site')
        self.assertEqual(self.remote.calls,[('stop','remote.namespace','shared-id',request)])

    def test_one_site_failure_keeps_local_controls_and_remote_unknown(self):
        self.get('/api/state')
        self.remote.offline=True
        self.backend.refresh()
        value=self.get('/api/state')
        self.assertEqual(value['sessions'][0]['state'],'unknown')
        self.assertFalse(value['sessions'][0]['capabilities']['stopSession'])
        self.post('/api/projects/remote.project/sessions',{'requestId':str(uuid4())},409)
        self.assertEqual(self.post('/api/projects',self.create())['project']['workspaceId'],'local')
        self.assertEqual(self.remote.calls,[])

    def test_history_routes_exact_target_and_requires_its_own_capability(self):
        route='/api/sessions/shared-id/history'
        body={'contextNamespace':'remote.namespace'}
        self.post(route,body,409)
        self.remote.session['capabilities']['readTerminalHistory']=True
        self.backend.refresh()
        self.post(route,{'contextNamespace':'wrong.namespace'},409)
        self.post('/api/sessions/another-id/history',body,409)
        self.assertEqual(self.remote.calls,[])
        self.assertEqual(self.local.calls,[])
        result=self.post(route,body)
        self.assertEqual(result,{'text':'Selected terminal output\n','kind':'terminal-snapshot'})
        self.assertEqual(self.remote.calls,[('history','remote.namespace','shared-id')])
        self.assertEqual(self.local.calls,[])
        self.remote.offline=True
        self.backend.refresh()
        self.post(route,body,409)
        self.assertEqual(self.remote.calls,[('history','remote.namespace','shared-id')])

    def test_machine_selection_does_not_relax_auth_or_origin(self):
        body=self.create()
        self.assertEqual(self.request('POST','/api/projects',body,authenticate=False)[0],401)
        self.assertEqual(self.request('POST','/api/projects',body,headers={'Authorization':''})[0],401)
        self.assertEqual(self.request('POST','/api/projects',body,headers={'Origin':'http://127.0.0.1:1'})[0],403)
        self.assertEqual(self.local.calls,[])
        self.assertEqual(self.remote.calls,[])


if __name__=='__main__': unittest.main()
