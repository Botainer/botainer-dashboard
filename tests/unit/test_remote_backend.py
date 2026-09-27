import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

from botainer_dashboard.backend_errors import BackendUnavailable
from botainer_dashboard.pairing import write_private_json
from botainer_dashboard.remote_backend import RemoteBackend, remote_payload
from botainer_dashboard.remote_profile import parse_remote_profile, RemoteProfileError


REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('remote_helper_test', REPO / 'tools/remote_botainer_helper.py')
HELPER = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(HELPER)
HELPER.BUNDLE = {name: (REPO / 'src/botainer_dashboard' / name).read_text()
                 for name in ('config_recovery.py', 'import_policy.py')}
UID = '11111111-1111-4111-8111-111111111111'
OTHER_UID = '33333333-3333-4333-8333-333333333333'
REQUEST = '22222222-2222-4222-8222-222222222222'
SID = 'abcdef0123456789'


def profile():
    required = ['pyproject.toml', 'botainer/plugins/lifecycle.py', 'botainer/cli/main.py', 'botainer/cli/hpc.py', 'botainer/cli/plugin.py',
                'botainer/plugins/manifest.py', 'plugins/hpc-launcher/host_helper/submit.py',
                'plugins/hpc-launcher/host_helper/_common.py']
    return {'version': 1, 'id': 'site-main', 'label': 'Test site', 'ssh_alias': 'cluster',
            'remote_python': '/opt/botainer/bin/python', 'remote_python_sha256': 'a' * 64,
            'source_root': '/opt/botainer/source', 'state_root': '/home/test/.botainer',
            'launcher': {'path': '/home/test/bin/botainer', 'sha256': 'b' * 64},
            'source_sha256': {name: 'c' * 64 for name in required}, 'state_plugin_sha256': {},
            'control_root': '/home/test/.dashboard-control', 'project_roots': {'work': '/home/test/projects'}}


def wheel_profile():
    result = profile(); result['version'] = 2
    result['installation_layout'] = {'kind': 'wheel', 'metadata_path': 'botainer-0.1.0a5.dist-info/METADATA'}
    result['source_sha256'] = {name: value for name, value in result['source_sha256'].items() if name.startswith('botainer/')}
    result['source_sha256'].update({name: 'c' * 64 for name in ('botainer/__init__.py',
        'botainer-0.1.0a5.dist-info/METADATA', 'botainer-0.1.0a5.dist-info/entry_points.txt')})
    result['state_plugin_sha256'] = {'plugins/hpc-launcher/' + name: 'd' * 64 for name in
        ('botainer-plugin.yaml', 'host_helper/submit.py', 'host_helper/_common.py')}
    return result


class RemoteProfileTests(unittest.TestCase):
    def test_wheel_profile_requires_metadata_and_installed_hpc_pins(self):
        self.assertEqual(parse_remote_profile(wheel_profile()).data['version'], 2)
        for field, name in (('source_sha256', 'botainer-0.1.0a5.dist-info/entry_points.txt'),
                            ('state_plugin_sha256', 'plugins/hpc-launcher/host_helper/submit.py')):
            value = wheel_profile(); value[field].pop(name)
            with self.subTest(field=field), self.assertRaises(RemoteProfileError): parse_remote_profile(value)
        for name in ('plugins/hpc-launcher/host_helper/submit.py', 'another_package/__init__.py'):
            value = wheel_profile(); value['source_sha256'][name] = 'd' * 64
            with self.subTest(name=name), self.assertRaises(RemoteProfileError): parse_remote_profile(value)
        value = wheel_profile(); value['version'] = 1
        with self.assertRaises(RemoteProfileError): parse_remote_profile(value)

    def test_profile_is_explicit_and_deterministic(self):
        value = profile()
        self.assertEqual(parse_remote_profile(value).fingerprint, parse_remote_profile(dict(reversed(list(value.items())))).fingerprint)
        for field, bad in [('ssh_alias', '-oProxyCommand=bad'), ('state_root', '/home/../etc'),
                           ('source_root', '/'), ('remote_python_sha256', 'bad')]:
            changed = dict(value); changed[field] = bad
            with self.subTest(field=field), self.assertRaises(RemoteProfileError): parse_remote_profile(changed)

    def test_missing_source_pins_and_traversal_refuse(self):
        value = profile(); value['source_sha256'].pop('botainer/cli/main.py')
        with self.assertRaises(RemoteProfileError): parse_remote_profile(value)
        value = profile(); value['state_plugin_sha256']['../outside'] = 'a' * 64
        with self.assertRaises(RemoteProfileError): parse_remote_profile(value)

    def test_payload_bundles_only_dashboard_code_and_compiles(self):
        value = remote_payload(REPO)
        self.assertLess(len(value.encode()), 262144)
        compile(value, '<remote-test>', 'exec')
        self.assertIn('workspace_botainer_helper.py', value)
        self.assertIn('installation_layout.py', value)


class InstalledWheelPluginTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source, self.state = self.root / 'site-packages', self.root / 'state'
        self.plugin = self.state / 'plugins/hpc-launcher'; self.plugin.mkdir(parents=True)
        self.script = self.plugin / 'host_helper/submit.py'; self.script.parent.mkdir()
        self.script.write_text('raise RuntimeError("must not execute")\n')
        self.profile = {**wheel_profile(), 'source_root': str(self.source), 'state_root': str(self.state),
            'state_plugin_sha256': {'plugins/hpc-launcher/host_helper/submit.py': hashlib.sha256(self.script.read_bytes()).hexdigest()}}
        self.items = [types.SimpleNamespace(name='hpc-launcher', plugin_dir=self.plugin)]
        self.modules = {name: types.ModuleType(name) for name in ('botainer', 'botainer.plugins', 'botainer.plugins.lifecycle')}
        self.modules['botainer.plugins.lifecycle'].list_installed = lambda: self.items
        self.modules['botainer.plugins'].lifecycle = self.modules['botainer.plugins.lifecycle']

    def test_uses_native_selected_pinned_state_helper(self):
        with patch.dict(sys.modules, self.modules):
            self.assertEqual(HELPER.native_hpc_helper(self.profile), self.script)
        self.script.write_text('# changed installed helper\n')
        with patch.dict(sys.modules, self.modules), self.assertRaisesRegex(ValueError, 'exact reviewed pin'):
            HELPER.native_hpc_helper(self.profile)

    def test_bundled_source_foreign_duplicate_or_symlink_plugins_refuse(self):
        bundled = self.source / 'botainer/_builtin_plugins/hpc-launcher'; bundled.mkdir(parents=True)
        alias = self.state / 'alias'; alias.symlink_to(self.plugin, target_is_directory=True)
        for directory in (bundled, self.root, alias):
            self.items[:] = [types.SimpleNamespace(name='hpc-launcher', plugin_dir=directory)]
            with self.subTest(directory=directory), patch.dict(sys.modules, self.modules), self.assertRaises(ValueError):
                HELPER.native_hpc_helper(self.profile)
        self.items[:] = [types.SimpleNamespace(name='hpc-launcher', plugin_dir=self.plugin)] * 2
        with patch.dict(sys.modules, self.modules), self.assertRaisesRegex(ValueError, 'ambiguous'):
            HELPER.native_hpc_helper(self.profile)

    def test_unpinned_new_helper_refuses(self):
        (self.script.parent / '_common.py').write_text('# new native module\n')
        with patch.dict(sys.modules, self.modules), self.assertRaisesRegex(ValueError, 'exact reviewed pin'):
            HELPER.native_hpc_helper(self.profile)


class RemoteFileTests(unittest.TestCase):
    def test_changed_registered_root_symlink_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); destination = root / 'other'; destination.mkdir()
            project = destination / 'project'; project.mkdir()
            alias = root / 'configured'; alias.symlink_to(destination, target_is_directory=True)
            chosen = {**profile(), 'project_roots': {'work': str(alias)}}
            with self.assertRaises(ValueError):
                HELPER.project_path(chosen, {'project_path': str(project)}, registered=False)

    def test_files_are_bounded_and_cannot_follow_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root / 'visible.txt').write_text('hello')
            (root / 'credentials.json').write_text('private')
            (root / 'linked.txt').symlink_to(root / 'visible.txt')
            self.assertEqual([e['name'] for e in HELPER.file_read(root, '')['entries']], ['visible.txt'])
            self.assertEqual(HELPER.file_read(root, 'visible.txt', read=True)['text'], 'hello')
            for path in ('../visible.txt', 'credentials.json', '.botainer/config.yaml', 'linked.txt'):
                with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                    HELPER.file_read(root, path, read=True)

    def test_hardlink_and_binary_are_not_text_previews(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root / 'value').write_bytes(b'x\0y')
            with self.assertRaises(ValueError): HELPER.file_read(root, 'value', read=True)
            (root / 'plain').write_text('hello'); os.link(root / 'plain', root / 'alias')
            with self.assertRaises(ValueError): HELPER.file_read(root, 'alias', read=True)

    def test_config_save_is_revision_checked_and_does_not_follow_metadata_link(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); metadata = root / '.botainer'; metadata.mkdir()
            path = metadata / 'config.yaml'; path.write_text('agent: codex\n')
            revision = hashlib.sha256(path.read_bytes()).hexdigest()
            control = root / 'control'; control.mkdir(mode=0o700)
            recovery = HELPER.config_save(root, 'agent: claude\n', revision, control=control)
            self.assertEqual(json.loads(Path(recovery['path']).read_text())['base'], 'agent: codex\n')
            self.assertEqual(path.read_text(), 'agent: claude\n')
            with self.assertRaises(ValueError): HELPER.config_save(root, 'agent: codex\n', revision, control=control)
            metadata.rename(root / 'moved'); metadata.symlink_to(root / 'moved', target_is_directory=True)
            with self.assertRaises(OSError): HELPER.config_save(root, 'agent: codex\n', hashlib.sha256(path.read_bytes()).hexdigest(), control=control)

    def test_remote_config_external_edit_during_recovery_is_preserved(self):
        from botainer_dashboard import config_recovery
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); metadata = root / '.botainer'; metadata.mkdir()
            target = metadata / 'config.yaml'; target.write_text('base\n')
            control = root / 'control'; control.mkdir(mode=0o700)
            original = config_recovery.preserve_config
            def concurrent_edit(*args):
                value = original(*args); target.write_text('External edit\n'); return value
            with patch.object(HELPER, 'module_from_bundle', return_value=config_recovery), \
                    patch.object(config_recovery, 'preserve_config', side_effect=concurrent_edit):
                with self.assertRaisesRegex(ValueError, 'changed before replacement'):
                    HELPER.config_save(root, 'draft\n', hashlib.sha256(b'base\n').hexdigest(), control=control)
            self.assertEqual(target.read_text(), 'External edit\n')
            self.assertEqual(json.loads(next((control / 'config-recovery').glob('slot-*.json')).read_text())['draft'], 'draft\n')


class RemoteRegisteredProjectTests(unittest.TestCase):
    """Existing-project authority is native registration, not setup parents."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / 'existing'
        self.metadata = self.project / '.botainer'; self.metadata.mkdir(parents=True)
        (self.metadata / 'project-id').write_text(UID)
        self.setup = self.root / 'new-projects'; self.setup.mkdir()
        self.profile = {**profile(), 'project_roots': {'work': str(self.setup)}}
        self.row = {'uuid': UID, 'last_path': str(self.project), 'display_name': 'Existing project'}
        self.data = {'project_uuid': UID, 'project_path': str(self.project)}

    def test_exact_registered_project_outside_setup_roots_is_usable_without_widening_roots(self):
        with patch.object(HELPER, 'catalog', return_value=[self.row]) as discovery:
            self.assertEqual(HELPER.project_path(self.profile, self.data), self.project)
            self.assertEqual(HELPER.project_path(self.profile, self.data), self.project)
        self.assertEqual(discovery.call_count, 2, 'Each action must verify current native registration')
        self.assertEqual(self.profile['project_roots'], {'work': str(self.setup)})
        with self.assertRaisesRegex(ValueError, 'outside registered roots'):
            HELPER.project_path(self.profile, self.data, registered=False)

    def test_unavailable_setup_parent_does_not_block_existing_project_identity(self):
        self.setup.rmdir()
        with patch.object(HELPER, 'catalog', return_value=[self.row]):
            self.assertEqual(HELPER.project_path(self.profile, self.data), self.project)
        with self.assertRaises((OSError, ValueError)):
            HELPER.project_path(self.profile, self.data, registered=False)

    def test_missing_changed_or_ambiguous_catalog_never_grants_controls(self):
        variants = [[], [{**self.row, 'last_path': str(self.setup)}],
                    [self.row, {**self.row, 'last_path': str(self.setup)}],
                    [self.row, {**self.row, 'uuid': OTHER_UID}],
                    [{**self.row, 'uuid': OTHER_UID}]]
        for rows in variants:
            with self.subTest(rows=rows), patch.object(HELPER, 'catalog', return_value=rows), self.assertRaises(ValueError):
                HELPER.project_path(self.profile, self.data)
        for rows in variants[2:4]:
            with patch.object(HELPER, 'catalog', return_value=rows), self.assertRaisesRegex(ValueError, 'Duplicate'):
                HELPER.inventory(self.profile)

    def test_project_and_metadata_links_uuid_change_and_foreign_owner_remain_refused(self):
        with patch.object(HELPER, 'catalog', return_value=[self.row]):
            link = self.root / 'linked-project'; link.symlink_to(self.project, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'Project path changed'):
                HELPER.project_path(self.profile, {**self.data, 'project_path': str(link)})
            moved = self.project / 'metadata-original'; self.metadata.rename(moved)
            self.metadata.symlink_to(moved, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'metadata is a symlink'):
                HELPER.project_path(self.profile, self.data)
            self.metadata.unlink(); moved.rename(self.metadata)
            (self.metadata / 'project-id').write_text(OTHER_UID)
            with self.assertRaisesRegex(ValueError, 'UUID changed'):
                HELPER.project_path(self.profile, self.data)
            (self.metadata / 'project-id').write_text(UID)
            with patch.object(HELPER.os, 'getuid', return_value=os.getuid() + 1), self.assertRaisesRegex(ValueError, 'another account'):
                HELPER.project_path(self.profile, self.data)

    def test_protected_source_state_and_control_overlap_are_refused_in_both_directions(self):
        with patch.object(HELPER, 'catalog', return_value=[self.row]):
            for field in ('source_root', 'state_root', 'control_root'):
                for protected in (self.project, self.project / 'protected', self.root):
                    with self.subTest(field=field, protected=protected), self.assertRaisesRegex(ValueError, 'protected control state'):
                        HELPER.project_path({**self.profile, field: str(protected)}, self.data)

    def test_request_cannot_supply_a_trusted_inventory_index(self):
        data = {**self.data, 'path': '', '_catalog': {UID: self.row}, 'registered': False}
        with patch.object(sys, 'argv', ['helper', json.dumps(self.profile), 'files', json.dumps(data)]), \
                patch.object(HELPER, 'trusted_environment', return_value={}), patch.object(HELPER, 'verify'), \
                patch.object(HELPER, 'catalog', return_value=[]), patch.object(HELPER, 'file_read') as read:
            with self.assertRaisesRegex(ValueError, 'registration changed'):
                HELPER.main()
        read.assert_not_called()


class RemoteInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / 'outside-controls'; (self.project / '.botainer').mkdir(parents=True)
        (self.project / '.botainer/project-id').write_text(UID)
        # JSON is a YAML subset; this parser double keeps these identity and
        # scheduler tests independent of a locally installed YAML dependency.
        (self.project / '.botainer/config.yaml').write_text('{"agent":"codex"}\n')
        parser = types.SimpleNamespace(safe_load=json.loads, YAMLError=ValueError)
        self.yaml = patch.dict(sys.modules, {'yaml': parser}); self.yaml.start(); self.addCleanup(self.yaml.stop)
        self.allowed = self.root / 'allowed'; self.allowed.mkdir()
        self.state = self.root / 'botainer-state'
        self.sessions = self.state / 'state' / UID / 'sessions'; self.sessions.mkdir(parents=True)
        self.profile = {**profile(), 'state_root': str(self.state), 'project_roots': {'work': str(self.allowed)}}
        self.catalog = [{'uuid': UID, 'last_path': str(self.project), 'display_name': 'Existing project'}]

    def record(self, sid=SID, job='12345', **changes):
        folder = self.sessions / sid; folder.mkdir(exist_ok=True)
        value = {'project_uuid': UID, 'session_id': sid, 'project_root': str(self.project), 'runtime': 'apptainer',
                 'runtime_handle': {'apptainer': {'slurm_jobid': job}}, 'started_at': '2026-01-01T01:02:03Z',
                 'ended_at': None, 'screen_session_id': 'botainer-' + sid,
                 'spec': {'plugins_enabled': ['git', 'agent-codex-key']}, **changes}
        (folder / 'spec.json').write_text(json.dumps(value))
        return value

    def fields(self, sid=SID, job='12345', **changes):
        scripts = self.sessions / '_submit-scripts'; scripts.mkdir(exist_ok=True)
        script = scripts / ('submit-' + sid + '.sh')
        script.write_text(str(self.sessions / sid) + ' ' + UID)
        account = HELPER.pwd.getpwuid(os.getuid()).pw_name
        return {'JobId': job, 'UserId': f'{account}({os.getuid()})', 'WorkDir': str(self.project),
                'Command': str(script), 'JobState': 'RUNNING', 'StartTime': '2026-01-01T01:00:00', **changes}

    def inventory(self, queue, fields=None):
        with patch.object(HELPER, 'catalog', return_value=self.catalog), \
                patch.object(HELPER, 'active_scheduler_jobs', return_value=queue), \
                patch.object(HELPER, 'scheduler_fields', return_value=fields) as details:
            result = HELPER.inventory(self.profile)
        return result, details

    def test_registered_existing_project_outside_setup_roots_preserves_allocation_and_record_times(self):
        self.record(); fields = self.fields()
        result, details = self.inventory({'12345': 'RUNNING'}, fields)
        project, session = result['projects'][0], result['sessions'][0]
        self.assertTrue(project['path_identity_verified']); self.assertTrue(project['verified'])
        self.assertNotIn('control_restriction', project)
        self.assertEqual(session['state'], 'running'); self.assertEqual(session['scheduler_observation'], 'active')
        self.assertEqual(session['started_at'], '2026-01-01T01:02:03Z'); self.assertIsNone(session['ended_at'])
        self.assertEqual(session['scheduler_started_at'], fields['StartTime'])
        self.assertEqual(session['agent'], 'codex'); self.assertEqual(details.call_count, 1)

    def test_inventory_uses_one_complete_catalog_for_multiple_existing_projects(self):
        second = self.root / 'another-existing'; (second / '.botainer').mkdir(parents=True)
        (second / '.botainer/project-id').write_text(OTHER_UID)
        (second / '.botainer/config.yaml').write_text('{"agent":"claude"}\n')
        rows = [self.catalog[0], {'uuid': OTHER_UID, 'last_path': str(second), 'display_name': 'Another'}]
        with patch.object(HELPER, 'catalog', return_value=rows) as catalog:
            result = HELPER.inventory(self.profile)
        catalog.assert_called_once_with(self.profile)
        self.assertEqual(len(result['projects']), 2)
        self.assertTrue(all(p['verified'] for p in result['projects']))

    def test_missing_config_retains_project_observation_without_controls(self):
        (self.project / '.botainer/config.yaml').unlink()
        result, _ = self.inventory({})
        self.assertTrue(result['projects'][0]['path_identity_verified'])
        self.assertFalse(result['projects'][0]['verified'])
        self.assertEqual(result['projects'][0]['control_restriction'], 'project-registration-unavailable')

    def test_shared_or_reused_allocation_does_not_prove_session_and_queries_job_only_once(self):
        self.record(); self.record(sid='1234567890abcdef')
        result, details = self.inventory({'12345': 'RUNNING'}, self.fields(WorkDir='/different/project'))
        self.assertEqual(details.call_count, 1)
        for session in result['sessions']:
            self.assertEqual(session['state'], 'unknown'); self.assertEqual(session['scheduler_observation'], 'active')
            self.assertIn('does not establish native session ownership', session['reason'])

    def test_cached_job_fields_still_check_exact_script_identity_for_each_session(self):
        other = '1234567890abcdef'
        self.record(); self.record(sid=other)
        result, details = self.inventory({'12345': 'RUNNING'}, self.fields())
        self.assertEqual(details.call_count, 1)
        rows = {row['session_id']: row for row in result['sessions']}
        self.assertEqual(rows[SID]['state'], 'running')
        self.assertEqual(rows[other]['state'], 'unknown')
        self.assertIn('does not establish native session ownership', rows[other]['reason'])

    def test_retained_terminal_queue_row_is_never_an_active_unverified_allocation(self):
        self.record()
        for fields in (self.fields(JobState='COMPLETED', WorkDir='/different/project'), None):
            with self.subTest(fields=fields):
                result, _ = self.inventory({'12345': 'COMPLETED'}, fields)
                session = result['sessions'][0]
                self.assertEqual(session['state'], 'unknown')
                self.assertEqual(session['scheduler_observation'], 'not-active')
                self.assertEqual(session['scheduler_state'], 'COMPLETED')
                self.assertIn('Scheduler reports allocation COMPLETED', session['reason'])
                self.assertIn('agent end time and outcome are unknown', session['reason'])
                self.assertIsNone(session['ended_at'])

    def test_verified_scheduler_detail_keeps_state_and_observation_consistent_after_queue_snapshot(self):
        self.record()
        for state, expected, observation in [('COMPLETED', 'stopped', 'not-active'), ('RUNNING', 'running', 'active')]:
            with self.subTest(state=state):
                result, _ = self.inventory({'12345': 'COMPLETED'}, self.fields(JobState=state))
                self.assertEqual(result['sessions'][0]['state'], expected)
                self.assertEqual(result['sessions'][0]['scheduler_observation'], observation)

    def test_successful_empty_queue_marks_history_without_claiming_end_or_querying_every_record(self):
        self.record(ended_at='2026-01-01T02:00:00Z'); self.record(sid='1234567890abcdef', job='54321')
        result, details = self.inventory({}); details.assert_not_called()
        for session in result['sessions']:
            self.assertEqual(session['state'], 'unknown'); self.assertEqual(session['scheduler_observation'], 'not-active')
            self.assertIn('end time and outcome are unknown', session['reason'])
        self.assertEqual(next(s for s in result['sessions'] if s['session_id'] == SID)['ended_at'], '2026-01-01T02:00:00Z')

    def test_failed_queue_or_missing_handle_is_not_evidence_of_an_end(self):
        self.record(); self.record(sid='1234567890abcdef', job=None, started_at=None)
        result, details = self.inventory(None); details.assert_not_called()
        self.assertTrue(all(s['scheduler_observation'] == 'unavailable' and s['state'] == 'unknown' for s in result['sessions']))
        self.assertEqual(next(s for s in result['sessions'] if not s.get('job_id'))['reason'], 'No recorded scheduler job')

    def test_other_runtime_is_a_retained_record_not_a_scheduler_control_target(self):
        self.record(runtime='mock')
        result, details = self.inventory({'12345': 'RUNNING'}, self.fields()); details.assert_not_called()
        self.assertEqual(result['sessions'][0]['runtime'], 'mock')
        self.assertEqual(result['sessions'][0]['scheduler_observation'], 'unavailable')
        with self.assertRaises(ValueError): HELPER.session_record(self.profile, UID, SID)

    def test_current_detail_queries_have_a_count_limit_and_are_never_repeated_per_session(self):
        queue = {}
        for index in range(HELPER.MAX_CURRENT_JOB_CHECKS + 4):
            job = str(1000 + index); self.record(sid=f'{index:016x}', job=job); queue[job] = 'RUNNING'
        result, details = self.inventory(queue)
        self.assertEqual(details.call_count, HELPER.MAX_CURRENT_JOB_CHECKS)
        self.assertTrue(all(s['scheduler_observation'] == 'active' and s['state'] == 'unknown' for s in result['sessions']))
        self.assertTrue(all(call.kwargs['timeout'] <= 6 for call in details.call_args_list))

    def test_detail_time_budget_and_active_priority_leave_unchecked_owners_unknown(self):
        self.record(); self.record(sid='1234567890abcdef', job='100')
        with patch.object(HELPER.time, 'monotonic', side_effect=[0, 1, 25]):
            result, details = self.inventory({'100': 'COMPLETED', '12345': 'RUNNING'})
        self.assertEqual(details.call_count, 1); self.assertEqual(details.call_args.args[0], '12345')
        limited = next(s for s in result['sessions'] if s['job_id'] == '100')
        self.assertEqual(limited['state'], 'unknown'); self.assertIn('bounded check budget', limited['reason'])

    def test_queue_parser_requires_complete_account_scoped_unambiguous_output(self):
        account = HELPER.pwd.getpwuid(os.getuid()).pw_name
        valid = f'12345|{account}|RUNNING\n'.encode()
        failures = [(1, valid, b''), (0, valid, b'partial warning'), (0, valid.rstrip(), b''),
                    (0, valid + valid, b''), (0, b'12345|some-other-user|RUNNING\n', b''),
                    (0, b'12345|broken\n', b''), (0, b'\xff\n', b'')]
        for response in failures:
            with self.subTest(response=response), patch.object(HELPER, 'bounded', return_value=response):
                self.assertIsNone(HELPER.active_scheduler_jobs())
        with patch.object(HELPER, 'bounded', side_effect=ValueError('output exceeds bound')):
            self.assertIsNone(HELPER.active_scheduler_jobs())
        with patch.object(HELPER, 'bounded', return_value=(0, valid, b'')) as query:
            self.assertEqual(HELPER.active_scheduler_jobs(), {'12345': 'RUNNING'})
        argv = query.call_args.args[0]
        self.assertIn('--array', argv); self.assertIn('--all', argv); self.assertIn('--states=all', argv)
        self.assertEqual(argv[argv.index('--user') + 1], account)
        with patch.object(HELPER, 'bounded', return_value=(0, f'12345_1|{account}|RUNNING\n12345_2|{account}|PENDING\n'.encode(), b'')):
            self.assertEqual(HELPER.active_scheduler_jobs()['12345'], 'UNKNOWN')
        for state in ('SUSPENDED', 'STOPPED', 'CONFIGURING'):
            with patch.object(HELPER, 'bounded', return_value=(0, f'12345|{account}|{state}\n'.encode(), b'')):
                self.assertEqual(HELPER.active_scheduler_jobs()['12345'], state)

    def test_invalid_project_configuration_never_leaves_control_verification_enabled(self):
        (self.project / '.botainer/config.yaml').write_text('[]')
        parser = types.SimpleNamespace(safe_load=lambda text: [], YAMLError=ValueError)
        with patch.object(HELPER, 'catalog', return_value=self.catalog), \
                patch.object(HELPER, 'project_path', return_value=self.project), patch.dict(sys.modules, {'yaml': parser}):
            result = HELPER.inventory(self.profile)
        self.assertFalse(result['projects'][0]['verified'])

    def test_agent_metadata_comes_from_session_selection_and_ambiguous_plugins_remain_unknown(self):
        self.assertEqual(HELPER.record_agent({'spec': {'plugins_enabled': ['agent-claude-max']}}), 'claude')
        self.assertEqual(HELPER.record_agent({'spec': {'plugins_enabled': ['agent-claude', 'agent-codex']}}), '')


class RemoteSetupAndStopTests(unittest.TestCase):
    def setUp(self):
        from botainer_dashboard.import_policy import source_imports
        scope = source_imports(); scope.__enter__(); self.addCleanup(scope.__exit__, None, None, None)
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve(); self.control = self.root / 'control'; self.control.mkdir(mode=0o700)
        self.projects = self.root / 'projects'; self.projects.mkdir()
        self.project = self.projects / 'existing'; self.project.mkdir()
        self.profile = {**profile(), 'control_root': str(self.control), 'project_roots': {'work': str(self.projects)}}
        self.data = {'request_id': REQUEST, 'profile_fingerprint': 'f' * 64, 'root': 'work',
                     'directory': 'existing', 'mode': 'open', 'agent': 'claude', 'name': 'Requested name'}

    def test_open_registered_project_does_not_init_or_rewrite(self):
        meta = self.project / '.botainer'; meta.mkdir()
        (meta / 'project-id').write_text(UID); (meta / 'config.yaml').write_text('agent: codex\n')
        with patch.object(HELPER, 'project_path', return_value=self.project), redirect_stdout(io.StringIO()):
            self.assertEqual(HELPER.native_init(self.profile, self.data), 0)
        self.assertEqual((meta / 'config.yaml').read_text(), 'agent: codex\n')
        receipt = json.loads((self.control / ('operation-' + REQUEST + '.json')).read_text())
        self.assertEqual(receipt['phase'], 'completed'); self.assertEqual(receipt['project_uuid'], UID)

    def test_setup_reused_receipt_does_not_create_another_directory(self):
        HELPER.atomic_json(self.control / ('operation-' + REQUEST + '.json'), {'phase': 'entered'})
        with self.assertRaises(ValueError):
            HELPER.native_init(self.profile, {**self.data, 'directory': 'another', 'mode': 'create'})
        self.assertFalse((self.projects / 'another').exists())

    def test_native_stop_unknown_result_is_not_redispatched(self):
        data = {'request_id': REQUEST, 'project_uuid': UID, 'session_id': SID, 'job_id': '12345',
                'profile_fingerprint': 'f' * 64}
        with patch.object(HELPER, 'session_record', return_value={'project_root': str(self.project)}), \
                patch.object(HELPER, 'scheduler', return_value={'state': 'running', 'job_id': '12345'}), \
                patch.object(HELPER, 'bounded', side_effect=OSError('reply lost')) as dispatch:
            with self.assertRaises(OSError): HELPER.native_stop(self.profile, data, self.project)
            self.assertEqual(HELPER.native_stop(self.profile, data, self.project), {'confirmed': False})
            self.assertEqual(dispatch.call_count, 1)
            with self.assertRaises(ValueError): HELPER.native_stop(self.profile, {**data, 'job_id': '99999'}, self.project)

    def test_native_stop_exact_confirmed_result_is_replayable(self):
        data = {'request_id': REQUEST, 'project_uuid': UID, 'session_id': SID, 'job_id': '12345',
                'profile_fingerprint': 'f' * 64}
        with patch.object(HELPER, 'session_record', return_value={'project_root': str(self.project)}), \
                patch.object(HELPER, 'scheduler', side_effect=[{'state': 'running', 'job_id': '12345'}, {'state': 'stopped', 'job_id': '12345'}]), \
                patch.object(HELPER, 'bounded', return_value=(0, b'', b'')) as dispatch:
            result = HELPER.native_stop(self.profile, data, self.project)
            self.assertTrue(result['confirmed'])
            self.assertEqual(HELPER.native_stop(self.profile, data, self.project), result)
            self.assertEqual(dispatch.call_count, 1)

    def test_unknown_or_active_job_blocks_config_save(self):
        folder = self.root / 'state' / UID / 'sessions' / SID; folder.mkdir(parents=True)
        chosen = {**self.profile, 'state_root': str(self.root)}
        with patch.object(HELPER, 'session_record', return_value={'runtime_handle': {'apptainer': {'slurm_jobid': '12345'}}}), \
                patch.object(HELPER, 'scheduler', return_value={'state': 'unknown'}):
            with self.assertRaises(ValueError): HELPER.require_idle(chosen, UID)
        with patch.object(HELPER, 'session_record', return_value={'runtime_handle': {'apptainer': {'slurm_jobid': '12345'}}}), \
                patch.object(HELPER, 'scheduler', return_value={'state': 'stopped'}):
            HELPER.require_idle(chosen, UID)

    def test_save_action_confirms_exact_written_text_and_new_revision(self):
        metadata = self.project / '.botainer'; metadata.mkdir()
        target = metadata / 'config.yaml'; target.write_text('agent: codex\n')
        revision = hashlib.sha256(target.read_bytes()).hexdigest(); changed = 'agent: codex\nenv: {}\n'
        data = {'project_path': str(self.project), 'project_uuid': UID, 'revision': revision, 'text': changed}
        output = io.StringIO()
        with patch.object(sys, 'argv', ['helper', json.dumps(self.profile), 'save-config', json.dumps(data)]), \
                patch.object(HELPER, 'trusted_environment', return_value={}), patch.object(HELPER, 'verify'), \
                patch.object(HELPER, 'project_path', return_value=self.project), \
                patch.object(HELPER, 'config_validate', return_value={'valid': True}), patch.object(HELPER, 'require_idle'), \
                redirect_stdout(output):
            self.assertEqual(HELPER.main(), 0)
        result = json.loads(output.getvalue())
        self.assertIs(result['saved'], True); self.assertEqual(result['text'], changed)
        self.assertEqual(result['revision'], hashlib.sha256(target.read_bytes()).hexdigest())
        self.assertNotEqual(result['revision'], revision)
        self.assertNotIn('saved', HELPER.config_read(self.project))

    def test_unpinned_extensionless_hook_is_refused_before_composition(self):
        source = self.root / 'source'; plugin = source / 'plugins/example'; (plugin / 'hooks').mkdir(parents=True)
        hook = plugin / 'hooks/pre_session'; hook.write_text('#!/bin/sh\nexit 0\n'); hook.chmod(0o755)
        modules = {name: types.ModuleType(name) for name in ('botainer', 'botainer.plugins', 'botainer.plugins.lifecycle')}
        modules['botainer.plugins.lifecycle'].list_installed = lambda: [types.SimpleNamespace(name='example', plugin_dir=plugin)]
        modules['botainer.plugins'].lifecycle = modules['botainer.plugins.lifecycle']
        chosen = {**self.profile, 'source_root': str(source), 'source_sha256': {}}
        with patch.dict(sys.modules, modules):
            with self.assertRaises(ValueError): HELPER.verify_enabled_plugins(chosen, ['example'])
            chosen['source_sha256']['plugins/example/hooks/pre_session'] = hashlib.sha256(hook.read_bytes()).hexdigest()
            HELPER.verify_enabled_plugins(chosen, ['example'])


class NativeRemoteReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve(); self.project = self.root / 'project'; self.project.mkdir()
        self.control = self.root / 'control'; self.control.mkdir(mode=0o700)
        self.source = self.root / 'source'; self.helper = self.source / 'plugins/hpc-launcher/host_helper/submit.py'
        self.helper.parent.mkdir(parents=True)
        self.cleanup_file = self.root / 'cleanups'
        self.helper.write_text('''from types import SimpleNamespace
from typing import NamedTuple
import subprocess
class Outcome(NamedTuple):
    rc: int
    launched: bool
def make_plan(*args, **kwargs): return SimpleNamespace(plugins_enabled=['nudge'])
def _do_submit(plan, spec, *, dry_run):
    result = subprocess.run(['sbatch', '/exact/native/script.sh'], capture_output=True, text=True)
    return Outcome(result.returncode, result.returncode == 0)
def main():
    print('Native warning before confirmation')
    if input('Launch this native session? [y/N] ') != 'y':
        open(CLEANUP_FILE, 'a').write('cleanup\\n')
        return 3
    spec = SimpleNamespace(project_uuid=PROJECT_UUID, project_root=PROJECT_PATH, runtime='apptainer', session_id=SESSION_ID)
    plan = SimpleNamespace(submission_mode='submit', nudge_enabled=True)
    outcome = _do_submit(plan, spec, dry_run=False)
    if not outcome.launched: open(CLEANUP_FILE, 'a').write('cleanup\\n')
    return outcome.rc
'''.replace('CLEANUP_FILE', repr(str(self.cleanup_file))).replace('PROJECT_UUID', repr(UID))
            .replace('PROJECT_PATH', repr(str(self.project))).replace('SESSION_ID', repr(SID)))
        self.profile = {**profile(), 'control_root': str(self.control), 'source_root': str(self.source)}
        self.data = {'project_path': str(self.project), 'project_uuid': UID, 'request_id': REQUEST,
                     'config_revision': 'd' * 64, 'profile_fingerprint': 'e' * 64, 'agent': 'codex'}
        self.calls = []
        def cli(argv):
            self.calls.append(list(argv))
            if argv[:2] == ['hpc', 'submit']:
                return subprocess.call([sys.executable, '-I', '-B', '-m', 'botainer.cli.main',
                                        'plugin', 'hpc-launcher', 'submit', *argv[2:]], env=dict(os.environ))
            self.assertEqual(argv[:3], ['plugin', 'hpc-launcher', 'submit'])
            return subprocess.call([sys.executable, str(self.helper), *argv[3:]],
                                   env={**os.environ, 'BOTAINER_PROJECT_ROOT': str(self.project)})
        self.modules = {name: types.ModuleType(name) for name in
                        ('botainer', 'botainer.cli', 'botainer.cli.main', 'botainer.core', 'botainer.core.config')}
        self.modules['botainer.cli.main'].main = cli
        self.modules['botainer.core.config'].load_config = lambda p: types.SimpleNamespace(plugins_enabled=['nudge'])
        self.modules['botainer.core'].config = self.modules['botainer.core.config']
        self.screen = types.SimpleNamespace(Refused=type('ScreenRefused', (RuntimeError,), {}),
            verify_screen_policy=unittest.mock.Mock(return_value={'qualification': 'test policy'}))

    def invoke(self, answer, run=None, revisions=None):
        run = run or (lambda *args, **kwargs: types.SimpleNamespace(returncode=0))
        revisions = revisions or [{'revision': 'd' * 64}]
        config_read = (lambda p: revisions.pop(0) if len(revisions) > 1 else revisions[0])
        with patch.dict(sys.modules, self.modules), patch.object(HELPER, 'project_path', return_value=self.project), \
                patch.object(HELPER, 'config_read', side_effect=config_read), \
                patch.object(HELPER, 'session_record', return_value={'runtime_handle': {'apptainer': {'slurm_jobid': '12345'}}}), \
                patch.object(HELPER, 'module_from_bundle', return_value=self.screen), \
                patch.object(Path, 'home', return_value=self.root), patch('builtins.input', return_value=answer), \
                patch.object(subprocess, 'run', side_effect=run), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return HELPER.native_launch(self.profile, self.data)

    def receipt(self): return json.loads((self.control / ('operation-' + REQUEST + '.json')).read_text())

    def test_decline_records_no_dispatch_and_native_cleanup(self):
        run = unittest.mock.Mock(side_effect=AssertionError('must not dispatch'))
        self.assertEqual(self.invoke('n', run=run), 3)
        self.assertEqual(self.receipt()['phase'], 'not-dispatched'); run.assert_not_called()
        self.assertEqual(self.cleanup_file.read_text(), 'cleanup\n')
        self.assertFalse(any('--yes' in argv for argv in self.calls))

    def test_accept_records_exact_saved_sid_and_job(self):
        run = unittest.mock.Mock(return_value=types.SimpleNamespace(returncode=0))
        self.assertEqual(self.invoke('y', run=run), 0)
        self.assertEqual(self.receipt()['result'], {'session_id': SID, 'job_id': '12345'})
        self.assertEqual(self.receipt()['phase'], 'completed'); self.assertEqual(run.call_count, 1)
        self.assertIn('--agent', self.calls[0]); self.assertFalse(self.cleanup_file.exists())

    def test_unqualified_login_screen_refuses_before_native_cli_or_scheduler(self):
        self.screen.verify_screen_policy.side_effect = self.screen.Refused('unreviewed binary')
        run = unittest.mock.Mock(side_effect=AssertionError('must not dispatch'))
        with self.assertRaisesRegex(ValueError, 'No job was submitted.*site qualification'):
            self.invoke('y', run=run)
        self.assertEqual(self.receipt()['phase'], 'not-dispatched')
        self.assertEqual(self.receipt()['exit_code'], 2)
        self.assertEqual(self.calls, []); run.assert_not_called()
        self.assertFalse(self.cleanup_file.exists(), 'Native composition must not have run')

    def test_unreadable_login_screen_refuses_without_echoing_exception_contents(self):
        self.screen.verify_screen_policy.side_effect = OSError('private diagnostic contents')
        with self.assertRaisesRegex(ValueError, 'SSH login machine') as caught:
            self.invoke('y')
        self.assertNotIn('private diagnostic contents', str(caught.exception))
        self.assertEqual(self.receipt()['phase'], 'not-dispatched')
        self.assertEqual(self.calls, [])

    def test_login_screen_policy_is_checked_before_native_consent(self):
        original = self.modules['botainer.cli.main'].main
        def checked_cli(argv):
            self.screen.verify_screen_policy.assert_called_once_with()
            return original(argv)
        self.modules['botainer.cli.main'].main = checked_cli
        self.assertEqual(self.invoke('n'), 3)
        self.assertEqual(self.receipt()['phase'], 'not-dispatched')
        self.assertEqual(self.cleanup_file.read_text(), 'cleanup\n')

    def test_login_screen_success_does_not_bypass_compute_policy_check(self):
        self.assertEqual(self.invoke('y'), 0)
        self.screen.verify_screen_policy.side_effect = self.screen.Refused('different compute policy')
        record = {'project_root': str(self.project), 'runtime_handle': {'apptainer': {'slurm_jobid': '12345'}},
                  'screen_session_id': 'botainer-12345'}
        with patch.object(HELPER, 'module_from_bundle', return_value=self.screen), \
                patch.object(HELPER, 'session_record', return_value=record), \
                patch.object(Path, 'home', return_value=self.root), \
                patch.dict(os.environ, {'SLURM_JOB_ID': '12345'}), \
                patch.object(HELPER, 'bounded', side_effect=AssertionError('must not attach')) as attach:
            with self.assertRaisesRegex(self.screen.Refused, 'different compute policy'):
                HELPER.compute_attach(self.profile, {**self.data, 'session_id': SID, 'job_id': '12345'})
        self.assertEqual(self.screen.verify_screen_policy.call_count, 2)
        attach.assert_not_called()

    def test_installed_wheel_native_dispatch_preserves_consent_and_receipt(self):
        state = self.root / 'state'
        installed = state / 'plugins/hpc-launcher/host_helper/submit.py'
        installed.parent.mkdir(parents=True); self.helper.rename(installed); self.helper = installed
        self.profile.update(version=2, state_root=str(state), state_plugin_sha256={
            'plugins/hpc-launcher/host_helper/submit.py': hashlib.sha256(installed.read_bytes()).hexdigest()})
        for name in ('botainer.plugins', 'botainer.plugins.lifecycle'):
            self.modules[name] = types.ModuleType(name)
        self.modules['botainer.plugins.lifecycle'].list_installed = lambda: [types.SimpleNamespace(
            name='hpc-launcher', plugin_dir=installed.parent.parent)]
        self.modules['botainer.plugins'].lifecycle = self.modules['botainer.plugins.lifecycle']
        run = unittest.mock.Mock(return_value=types.SimpleNamespace(returncode=0))
        self.assertEqual(self.invoke('y', run=run), 0)
        self.assertEqual(self.receipt()['result'], {'session_id': SID, 'job_id': '12345'})
        self.assertEqual(run.call_count, 1)
        self.assertEqual(len(self.calls), 2)

    def test_unselected_wheel_helper_refusal_is_known_not_dispatched(self):
        self.profile['version'] = 2
        with patch.object(HELPER, 'native_hpc_helper', side_effect=ValueError('plugin changed')):
            with self.assertRaises(ValueError): self.invoke('y')
        self.assertEqual(self.receipt()['phase'], 'not-dispatched')
        self.assertEqual(self.calls, [])

    def test_changed_config_refuses_through_native_cleanup(self):
        run = unittest.mock.Mock(side_effect=AssertionError('must not dispatch'))
        self.assertEqual(self.invoke('y', run=run, revisions=[{'revision': 'd' * 64}, {'revision': 'f' * 64}]), 2)
        run.assert_not_called(); self.assertEqual(self.receipt()['phase'], 'not-dispatched')
        self.assertEqual(self.cleanup_file.read_text(), 'cleanup\n')

    def test_lost_scheduler_reply_stays_ambiguous(self):
        def lost(*args, **kwargs): raise OSError('connection lost after possible acceptance')
        with self.assertRaises(OSError): self.invoke('y', run=lost)
        self.assertEqual(self.receipt()['phase'], 'dispatching')
        with self.assertRaises(ValueError): self.invoke('y')

    def test_project_lock_loser_publishes_no_receipt(self):
        with HELPER.lock(self.control / ('project-' + UID + '.lock')):
            with self.assertRaises(BlockingIOError): self.invoke('y')
        self.assertFalse((self.control / ('operation-' + REQUEST + '.json')).exists())
        self.assertEqual(self.calls, [])

    def test_shared_request_lock_refuses_another_project_or_action(self):
        with HELPER.lock(self.control / ('operation-' + REQUEST + '.lock')):
            with self.assertRaises(BlockingIOError): self.invoke('y')
            with self.assertRaises(BlockingIOError):
                HELPER.native_init({**self.profile, 'project_roots': {'work': str(self.root)}},
                    {'request_id': REQUEST, 'root': 'work', 'directory': 'other', 'mode': 'create',
                     'profile_fingerprint': 'e' * 64})
        self.assertFalse((self.root / 'other').exists())
        self.assertFalse((self.control / ('operation-' + REQUEST + '.json')).exists())

    def test_preflight_refusal_is_published_only_by_the_project_lock_owner(self):
        original = HELPER.atomic_json
        phases = []
        def write(path, value):
            phases.append(value['phase'])
            with self.assertRaises(BlockingIOError):
                with HELPER.lock(self.control / ('project-' + UID + '.lock')): pass
            original(path, value)
        with patch.object(HELPER, 'atomic_json', side_effect=write):
            with self.assertRaises(ValueError): self.invoke('y', revisions=[{'revision': 'f' * 64}])
        self.assertEqual(phases, ['entered', 'not-dispatched'])
        self.assertEqual(self.receipt()['exit_code'], 2); self.assertEqual(self.calls, [])


class PreDispatchReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve(); self.control = self.root / 'control'; self.control.mkdir(mode=0o700)
        self.profile = {**profile(), 'control_root': str(self.control)}
        self.data = {'request_id': REQUEST, 'operation': 'start', 'project_uuid': UID,
                     'project_path': '/home/test/projects/one', 'config_revision': 'a' * 64, 'profile_fingerprint': 'b' * 64}
        self.path = self.control / ('operation-' + REQUEST + '.json')
        self.lock = self.control / ('project-' + UID + '.lock')
        self.lock.touch(mode=0o600)

    def write(self, phase): HELPER.atomic_json(self.path, {**self.data, 'phase': phase})

    def test_free_existing_lock_attests_nondispatch_without_changing_receipt(self):
        for phase in ('entered', 'before-dispatch'):
            with self.subTest(phase=phase):
                self.write(phase); original = self.path.read_bytes()
                result = HELPER.reconciled_receipt(self.profile, self.data)
                self.assertEqual(result['phase'], phase)
                self.assertEqual(result['reconciliation'], {'kind': 'pre-dispatch-command-ended',
                                 'observedPhase': phase, 'projectLock': 'exclusive-existing'})
                self.assertEqual(self.path.read_bytes(), original)

    def test_busy_or_missing_lock_does_not_attest(self):
        self.write('entered')
        with HELPER.lock(self.lock):
            self.assertNotIn('reconciliation', HELPER.reconciled_receipt(self.profile, self.data))
        self.lock.unlink()
        self.assertNotIn('reconciliation', HELPER.reconciled_receipt(self.profile, self.data))
        self.assertFalse(self.lock.exists())

    def test_possible_dispatch_and_changed_scope_are_never_cleared(self):
        for phase in ('dispatching', 'completed'):
            self.write(phase)
            self.assertNotIn('reconciliation', HELPER.reconciled_receipt(self.profile, self.data))
        self.write('entered')
        for change in ({'project_uuid': REQUEST}, {'config_revision': 'c' * 64}, {'profile_fingerprint': 'c' * 64}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                HELPER.reconciled_receipt(self.profile, {**self.data, **change})

    def test_receipt_advanced_before_lock_acquisition_stays_unknown(self):
        self.write('entered')
        before = self.path.read_bytes(); after = json.dumps({**self.data, 'phase': 'dispatching'}).encode()
        with patch.object(HELPER, 'regular', side_effect=[before, after]):
            result = HELPER.reconciled_receipt(self.profile, self.data)
        self.assertEqual(result['phase'], 'dispatching'); self.assertNotIn('reconciliation', result)

    def test_main_lock_failure_never_publishes_or_replaces_a_receipt(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                if existing: self.write('dispatching')
                original = self.path.read_bytes() if existing else None
                with HELPER.lock(self.lock), \
                        patch.object(sys, 'argv', ['helper', json.dumps(self.profile), 'launch', json.dumps(self.data)]), \
                        patch.object(HELPER, 'trusted_environment', return_value={}), patch.object(HELPER, 'verify'), \
                        patch.object(HELPER, 'native_launch', side_effect=BlockingIOError('another command owns the project')):
                    with self.assertRaises(BlockingIOError): HELPER.main()
                if existing: self.assertEqual(self.path.read_bytes(), original)
                else: self.assertFalse(self.path.exists())


class RemoteBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve(); (self.repo / 'tools').mkdir(); (self.repo / '.local').mkdir(mode=0o700)
        for name in ('remote_botainer_helper.py', 'workspace_botainer_helper.py', 'cluster_attach_supervisor.py'):
            shutil.copyfile(REPO / 'tools' / name, self.repo / 'tools' / name)
        self.profile_path = self.repo / 'remote.json'; write_private_json(self.profile_path, profile())
        self.exchanges = []
        def exchange(argv, payload, **kwargs):
            request = json.loads(payload); self.exchanges.append((argv, request))
            value = {'projects': [{'uuid': UID, 'path': '/home/test/projects/one', 'name': 'One', 'verified': True}], 'sessions': []}
            return {'transport_status': 'complete', 'returncode': 0, 'stdout': json.dumps(value).encode(), 'stderr': b''}
        self.backend = RemoteBackend(self.repo, self.profile_path, exchange=exchange)

    def test_constructor_does_not_contact_remote_and_payload_not_argv(self):
        self.assertEqual(self.exchanges, [])
        self.backend.refresh()
        argv, envelope = self.exchanges[0]
        self.assertEqual(envelope['action'], 'inventory')
        self.assertNotIn('/home/test/.botainer', ' '.join(argv))
        self.assertIn('EscapeChar=none', argv)
        snapshot = self.backend.snapshot()
        self.assertEqual(snapshot['projects'][0]['registeredUuid'], UID)
        self.assertTrue(snapshot['projects'][0]['capabilities']['nativeCliLaunch'])

    def test_restart_reads_persisted_state_without_contacting_remote(self):
        self.backend.refresh(); calls = len(self.exchanges)
        restarted = RemoteBackend(self.repo, self.profile_path, exchange=self.backend.exchange)
        self.assertEqual(len(self.exchanges), calls)
        self.assertEqual(restarted._data['projects'][0]['uuid'], UID)
        self.assertEqual(restarted._connection_error(), 'remote-checking-connection')

    @unittest.skipUnless(shutil.which('node'), 'The cross-language consumer check uses the existing Node.js tool')
    def test_actual_remote_views_are_accepted_by_frontend_setup_launch_and_log_consumers(self):
        self.backend.refresh(); snapshot = self.backend.snapshot(); project = snapshot['projects'][0]
        self.backend._transcript(REQUEST).write_bytes(b'native console output\n')
        entry = {'request_id': REQUEST, 'project_id': project['id'], 'operation': 'init', 'state': 'waiting',
                 'created_at': '2026-09-22T12:00:00Z', 'project_path': project['path']}
        setup = self.backend._launch_view(entry)
        finished_setup = {**entry, 'state': 'completed', 'result_project': project['id']}
        # Persist the old journal schema: the corrected response must work after
        # restart without rewriting a successful remote setup or its receipt.
        self.backend._launches = [finished_setup]; self.backend._save()
        restarted = RemoteBackend(self.repo, self.profile_path, exchange=self.backend.exchange)
        setup_log = restarted._launch_view(restarted._launches[0])
        target = {'contextNamespace': self.backend.namespace, 'runtimeId': SID, 'projectId': project['id'],
                  'state': 'running', 'capabilities': {'attachTerminal': True}}
        start = self.backend._launch_view({**entry, 'operation': 'start'})
        launch_log = self.backend._launch_view({**entry, 'operation': 'start', 'state': 'completed',
                                                'result_target': {'contextNamespace': target['contextNamespace'], 'runtimeId': SID}})
        value = {'snapshot': snapshot, 'project': project, 'setup': setup, 'setupLog': setup_log,
                 'start': start, 'launchLog': launch_log, 'target': target}
        script = '''import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { isProjectSetup, isLaunchLog, includeProjectSetupResponse, includeLaunchResponse,
  sessionControls, nativeSetupResult, nativeLaunchResult } from './frontend/app.js';
const v = JSON.parse(readFileSync(0, 'utf8'));
assert.equal(isProjectSetup(v.setup), true);
assert.equal(isLaunchLog(v.setup), false);
assert.equal(sessionControls(v.snapshot, v.setup).attach, true);
assert.ok(includeProjectSetupResponse(v.snapshot, {project: v.project, session: v.setup}).sessions.includes(v.setup));
assert.equal(isLaunchLog(v.setupLog), true);
assert.deepEqual(nativeSetupResult(v.snapshot, v.setupLog), v.project);
assert.equal(nativeLaunchResult(v.snapshot, v.setupLog), null);
assert.equal(isProjectSetup(v.start), false);
assert.equal(sessionControls(v.snapshot, v.start).attach, true);
assert.ok(includeLaunchResponse(v.snapshot, v.project, v.start).sessions.includes(v.start));
const running = {...v.snapshot, sessions: [v.target, v.launchLog]};
assert.equal(isLaunchLog(v.launchLog), true);
assert.equal(nativeLaunchResult(running, v.launchLog), v.target);
'''
        result = subprocess.run([shutil.which('node'), '--input-type=module', '-e', script],
                                input=json.dumps(value), text=True, capture_output=True, cwd=REPO, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_oversized_state_does_not_replace_the_last_readable_state(self):
        self.backend.refresh(); previous = self.backend.state_path.read_bytes()
        self.backend._data = {'projects': [{'name': 'x' * (2 * 1024 * 1024)}], 'sessions': []}
        with self.assertRaises(BackendUnavailable): self.backend._save()
        self.assertEqual(self.backend.state_path.read_bytes(), previous)

    def test_journal_failure_releases_owner_lock_without_starting_console(self):
        self.backend.refresh()
        prepared = {'path': self.backend.profile.data['control_root'] + '/helper-' + self.backend.payload_digest + '.py',
                    'sha256': self.backend.payload_digest}
        entry = {'request_id': REQUEST, 'project_id': self.backend._project_id(UID), 'state': 'waiting'}
        descriptor = os.open(self.repo / 'owner-test', os.O_CREAT | os.O_WRONLY, 0o600)
        with patch.object(self.backend, '_call', return_value=prepared), \
                patch.object(self.backend, '_save', side_effect=OSError('disk full')), \
                patch('botainer_dashboard.remote_backend.private_lock', return_value=descriptor), \
                patch.object(self.backend, 'console_factory') as factory:
            with self.assertRaises(OSError): self.backend._start_console(entry, 'launch', {})
            factory.assert_not_called()
        with self.assertRaises(OSError): os.fstat(descriptor)

    def test_connection_loss_retains_unknown_state(self):
        self.backend.refresh()
        self.backend._data['sessions'] = [{'project_uuid': UID, 'session_id': SID, 'state': 'running', 'screen': 'botainer-12345', 'job_id': '12345'}]
        self.backend.exchange = lambda *args, **kwargs: {'transport_status': 'complete', 'returncode': 255,
                                                         'stdout': b'', 'stderr': b'test@cluster: Permission denied (keyboard-interactive).'}
        self.backend.refresh()
        snapshot = self.backend.snapshot()
        self.assertEqual(snapshot['sessions'][0]['state'], 'unknown')
        self.assertFalse(snapshot['sessions'][0]['capabilities']['stopSession'])
        self.assertEqual(snapshot['connectionStatus'], 'unavailable')
        self.assertEqual(snapshot['clusterSettings']['connectionDiagnostic']['code'], 'ssh-authentication-required')

    def test_typed_helper_failure_is_advisory_and_success_clears_it(self):
        self.backend.refresh()
        self.backend._data['sessions'] = [{'project_uuid': UID, 'session_id': SID, 'state': 'running',
                                          'screen': 'botainer-12345', 'job_id': '12345'}]
        working_exchange = self.backend.exchange
        error = {'protocol': 'botainer-dashboard.remote-error', 'version': 1, 'code': 'remote-launcher-changed'}
        self.backend.exchange = lambda *args, **kwargs: {'transport_status': 'complete', 'returncode': 2,
            'stdout': json.dumps(error).encode(), 'stderr': b'Selected launcher: /private/synthetic-secret\n'}
        self.backend.refresh()
        snapshot = self.backend.snapshot()
        self.assertEqual(snapshot['clusterSettings']['connectionDiagnostic']['code'], 'remote-launcher-changed')
        self.assertEqual(snapshot['sessions'][0]['state'], 'unknown')
        self.assertFalse(snapshot['sessions'][0]['capabilities']['stopSession'])
        self.assertFalse(snapshot['sessions'][0]['capabilities']['attachTerminal'])
        self.assertNotIn('/private/synthetic-secret', json.dumps(snapshot))
        self.assertEqual(len(self.backend._launches), 0)
        self.backend.exchange = working_exchange
        self.backend.refresh()
        snapshot = self.backend.snapshot()
        self.assertEqual(snapshot['connectionStatus'], 'available')
        self.assertIsNone(snapshot['clusterSettings']['connectionDiagnostic'])
        self.assertEqual(len(self.backend._launches), 0)

    def test_legacy_helper_stderr_remains_generic(self):
        self.backend.exchange = lambda *args, **kwargs: {'transport_status': 'complete', 'returncode': 2,
            'stdout': b'', 'stderr': b'Dashboard remote operation refused: import-path-other-account-writable\n'}
        self.backend.refresh()
        self.assertEqual(self.backend.snapshot()['clusterSettings']['connectionDiagnostic']['code'], 'remote-check-failed')

    def test_stop_scope_identifies_whole_job_without_granting_controls(self):
        self.backend.refresh()
        for state in ('running', 'queued'):
            with self.subTest(state=state):
                self.backend._data['sessions'] = [{'project_uuid': UID, 'session_id': SID, 'state': state,
                                                  'screen': 'botainer-' + SID, 'job_id': '12345'}]
                row = self.backend.snapshot()['sessions'][0]
                self.assertEqual(row['stopScope'], 'allocation')
                self.assertEqual(row['jobId'], '12345')
                self.assertTrue(row['capabilities']['stopSession'])
        self.backend._data['projects'][0]['verified'] = False
        row = self.backend.snapshot()['sessions'][0]
        self.assertEqual(row['stopScope'], 'allocation')
        self.assertEqual(row['jobId'], '12345')
        self.assertFalse(row['capabilities']['stopSession'])

    def test_wrong_context_and_agent_refuse_before_dispatch(self):
        self.backend.refresh(); count = len(self.exchanges)
        with self.assertRaises(BackendUnavailable): self.backend.start_session('no-project', REQUEST, agent='shell')
        with self.assertRaises(BackendUnavailable): self.backend.attach('wrong-context', SID, 80, 24)
        self.assertEqual(len(self.exchanges), count)

    def test_stalled_observation_ages_to_unknown_and_disables_actions(self):
        self.backend.refresh(); self.backend._polling = True
        self.backend._data['sessions'] = [{'project_uuid': UID, 'session_id': SID, 'state': 'running', 'screen': 'botainer-12345', 'job_id': '12345'}]
        self.backend._observed_at = time.monotonic() - 21
        snapshot = self.backend.snapshot()
        self.assertEqual(snapshot['error'], 'remote-observation-stale')
        self.assertEqual(snapshot['sessions'][0]['state'], 'unknown')
        self.assertFalse(snapshot['sessions'][0]['capabilities']['attachTerminal'])
        with self.assertRaises(BackendUnavailable): self.backend.start_session(self.backend._project_id(UID), REQUEST)

    def test_unverified_project_cannot_attach_or_stop(self):
        self.backend.refresh()
        self.backend._data['projects'][0]['verified'] = False
        self.backend._data['sessions'] = [{'project_uuid': UID, 'session_id': SID, 'state': 'running', 'screen': 'botainer-12345', 'job_id': '12345'}]
        count = len(self.exchanges)
        self.assertFalse(self.backend.snapshot()['sessions'][0]['capabilities']['attachTerminal'])
        self.assertFalse(self.backend.snapshot()['sessions'][0]['capabilities']['stopSession'])
        with self.assertRaises(BackendUnavailable): self.backend.attach(self.backend.namespace, SID, 80, 24)
        with self.assertRaises(BackendUnavailable): self.backend.stop_session(self.backend.namespace, SID, REQUEST)
        self.assertEqual(len(self.exchanges), count)

    def test_allocation_metadata_does_not_enable_controls_or_survive_as_fresh_during_connection_loss(self):
        self.backend.refresh()
        self.backend._data['projects'][0].update(verified=False, path_identity_verified=True,
                                               control_restriction='outside-project-roots')
        self.backend._data['sessions'] = [{'project_uuid': UID, 'session_id': SID, 'state': 'unknown',
            'runtime': 'apptainer', 'job_id': '12345', 'scheduler_observation': 'active', 'scheduler_state': 'RUNNING',
            'started_at': '2026-01-01T01:02:03Z', 'ended_at': None}]
        snap = self.backend.snapshot(); row = snap['sessions'][0]
        self.assertTrue(snap['projects'][0]['pathIdentityVerified'])
        self.assertEqual(snap['projects'][0]['controlRestriction'], 'outside-project-roots')
        self.assertEqual(row['schedulerObservation'], 'active'); self.assertEqual(row['state'], 'unknown')
        self.assertFalse(row['capabilities']['attachTerminal']); self.assertFalse(row['capabilities']['stopSession'])
        self.backend._error = 'remote-unavailable'
        self.assertEqual(self.backend.snapshot()['sessions'][0]['schedulerObservation'], 'unavailable')

    def test_active_or_unresolved_session_locks_config(self):
        self.backend.refresh()
        self.backend._data['sessions'] = [{'project_uuid': UID, 'session_id': SID, 'state': 'unknown', 'job_id': '12345'}]
        self.assertFalse(self.backend.snapshot()['projects'][0]['capabilities']['configWrite'])
        project_id = self.backend._project_id(UID)
        self.backend._launches.append({'project_id': project_id, 'state': 'unknown'})
        count = len(self.exchanges)
        with self.assertRaises(BackendUnavailable): self.backend.save_config(project_id, 'agent: codex', 'a' * 64)
        self.assertEqual(len(self.exchanges), count)

    def test_abandoned_predispatch_attestation_unblocks_new_intent_and_retains_timeout_fact(self):
        self.backend.refresh()
        entry = {'request_id': REQUEST, 'project_id': self.backend._project_id(UID), 'operation': 'start',
                 'project_uuid': UID, 'project_path': '/home/test/projects/one', 'config_revision': 'a' * 64,
                 'created_at': '2026-09-22T12:00:00Z', 'state': 'unknown'}
        self.backend._launches = [entry]
        self.backend._consoles[REQUEST] = types.SimpleNamespace(done=True, timed_out=True, cleanup_confirmed=True)
        receipt = {**entry, 'profile_fingerprint': self.backend.profile.fingerprint, 'phase': 'entered',
                   'reconciliation': {'kind': 'pre-dispatch-command-ended', 'observedPhase': 'entered',
                                      'projectLock': 'exclusive-existing'}}
        with patch.object(self.backend, '_call', return_value=receipt): self.backend._reconcile(entry)
        self.assertEqual(entry['state'], 'failed'); self.assertIn('no job was submitted', entry['reason'])
        self.assertNotIn('result_target', entry)
        self.assertTrue(self.backend.snapshot()['projects'][0]['capabilities']['startSession'])
        self.backend._save()
        restarted = RemoteBackend(self.repo, self.profile_path, exchange=self.backend.exchange)
        view = restarted._launch_view(restarted._launches[0])
        self.assertTrue(view['consoleEnded']); self.assertEqual(view['consoleEndReason'], 'timeout')
        self.assertEqual(view['launchState'], 'failed')


if __name__ == '__main__': unittest.main()
