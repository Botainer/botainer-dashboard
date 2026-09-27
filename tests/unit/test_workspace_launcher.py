"""Workspace selection must preserve target and browser-auth identities."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from botainer_dashboard.backend_errors import BackendUnavailable
from botainer_dashboard.connections import ConnectionRegistry
from botainer_dashboard.launcher import select_backend, select_connections_backend, select_installed_backend
from botainer_dashboard.ordinary_local import OrdinaryLocalBackend
from botainer_dashboard.project_boundaries import require_separate_project
import test_ordinary_local as fixtures


class WorkspaceLauncherTests(unittest.TestCase):
    def setUp(self):
        self.root = Path('/synthetic/dashboard')
        self.config = SimpleNamespace()

    def test_proof_cannot_be_combined_with_live_workspaces(self):
        for changes in ({'workspace': True}, {'cluster_paths': [Path('site.json')]}):
            with self.assertRaises(ValueError):
                select_backend(self.root, self.config, proof=True, **changes)

    def test_duplicate_site_ids_refuse_before_any_controller_construction(self):
        profile = SimpleNamespace(id='local', fingerprint='a'*64)
        with patch('botainer_dashboard.cluster_profiles.load_cluster_profile', return_value=profile), \
             patch('botainer_dashboard.local_backend.LocalBackend') as local, \
             patch('botainer_dashboard.cluster_backend.ClusterBackend') as remote:
            for workspace, paths in ((True, [Path('one')]), (False, [Path('one'), Path('two')])):
                with self.assertRaises(ValueError):
                    select_backend(self.root, self.config, workspace=workspace, cluster_paths=paths)
            local.assert_not_called()
            remote.assert_not_called()

    def test_combined_profile_order_does_not_change_pairing_but_target_change_does(self):
        profiles = {name: SimpleNamespace(id=name, fingerprint=char*64)
                    for name, char in (('one','a'),('two','b'),('three','c'))}
        local = Mock()
        children = {name: Mock(profile=value) for name, value in profiles.items()}
        with patch('botainer_dashboard.cluster_profiles.load_cluster_profile', side_effect=lambda p: profiles[str(p)]), \
             patch('botainer_dashboard.local_backend.LocalBackend', return_value=local), \
             patch('botainer_dashboard.cluster_backend.ClusterBackend', side_effect=lambda root,p: children[str(p)]), \
             patch('botainer_dashboard.combined_backend.CombinedBackend') as combined:
            _, first = select_backend(self.root, self.config, workspace=True, cluster_paths=[Path('one'),Path('two')])
            combined.assert_called_with(local, clusters={'one':children['one'],'two':children['two']})
            _, reordered = select_backend(self.root, self.config, workspace=True, cluster_paths=[Path('two'),Path('one')])
            _, changed = select_backend(self.root, self.config, workspace=True, cluster_paths=[Path('one'),Path('three')])
            self.assertEqual(first, reordered)
            self.assertNotEqual(first, changed)
            self.assertTrue(first.startswith('combined-workspace:'))
            local.snapshot.assert_not_called()
            for child in children.values(): child.snapshot.assert_not_called()

    def test_single_workspace_keeps_existing_pairing_mode(self):
        profile = SimpleNamespace(id='site', fingerprint='a'*64)
        local = Mock()
        child = Mock(profile=profile)
        with patch('botainer_dashboard.cluster_profiles.load_cluster_profile', return_value=profile), \
             patch('botainer_dashboard.local_backend.LocalBackend', return_value=local), \
             patch('botainer_dashboard.cluster_backend.ClusterBackend', return_value=child):
            self.assertEqual(select_backend(self.root, self.config, workspace=True), (local,'local-workspace'))
            self.assertEqual(select_backend(self.root, self.config, cluster_paths=[Path('site.json')]),
                             (child, 'cluster-workspace:' + profile.fingerprint))

    def test_native_cli_selection_is_explicit_and_has_separate_pairing_identity(self):
        local = Mock()
        with patch('botainer_dashboard.local_backend.LocalBackend', return_value=local) as make:
            self.assertEqual(select_backend(self.root, self.config, workspace=True, native_cli=True),
                             (local, 'native-cli-workspace'))
            make.assert_called_once_with(self.root, self.config, native_cli=True)
        for changes in ({}, {'proof': True}, {'cluster_paths': [Path('site.json')]}):
            with self.assertRaises(ValueError):
                select_backend(self.root, self.config, native_cli=True, **changes)

    def test_installed_profiles_never_silently_activate_a_trial(self):
        for changes in ({'workspace': True}, {'proof': True}, {'native_cli': True},
                        {'cluster_paths': [Path('trial.json')]}):
            with self.assertRaisesRegex(ValueError, 'cannot be mixed'):
                select_backend(self.root, self.config, local_profile=Path('real.json'), **changes)

    def test_installed_profile_order_preserves_pairing_and_target_change_does_not(self):
        local_profile = SimpleNamespace(id='my-mac', label='My Mac', fingerprint='a'*64)
        profiles = {'one': SimpleNamespace(id='one', label='One', fingerprint='b'*64),
                    'two': SimpleNamespace(id='two', label='Two', fingerprint='c'*64)}
        local = Mock(profile=local_profile)
        remotes = {name: Mock(profile=profile) for name, profile in profiles.items()}
        modules = {
            'botainer_dashboard.ordinary_local': SimpleNamespace(load_local_profile=Mock(return_value=local_profile), OrdinaryLocalBackend=Mock(return_value=local)),
            'botainer_dashboard.remote_profile': SimpleNamespace(load_remote_profile=lambda path: profiles[str(path)]),
            'botainer_dashboard.remote_backend': SimpleNamespace(RemoteBackend=Mock(side_effect=lambda root, path: remotes[str(path)])),
        }
        with patch.dict(sys.modules, modules), patch('botainer_dashboard.combined_backend.CombinedBackend') as combined:
            _, first = select_backend(self.root, self.config, local_profile=Path('mac'), remote_paths=[Path('one'), Path('two')])
            combined.assert_called_with(local, clusters=remotes, local_id='my-mac', local_label='My Mac')
            _, second = select_backend(self.root, self.config, local_profile=Path('mac'), remote_paths=[Path('two'), Path('one')])
            self.assertEqual(first, second)
            local_profile.fingerprint = 'd'*64
            _, changed = select_backend(self.root, self.config, local_profile=Path('mac'), remote_paths=[Path('one'), Path('two')])
            self.assertNotEqual(first, changed)
            local.snapshot.assert_not_called()
            for remote in remotes.values(): remote.snapshot.assert_not_called()

    def test_installed_profile_replacement_during_construction_refuses_pairing(self):
        selected = SimpleNamespace(id='local', label='Mac', fingerprint='a'*64)
        changed = SimpleNamespace(id='local', label='Mac', fingerprint='b'*64)
        modules = {
            'botainer_dashboard.ordinary_local': SimpleNamespace(load_local_profile=lambda path: selected,
                OrdinaryLocalBackend=lambda root, path: Mock(profile=changed)),
            'botainer_dashboard.remote_profile': SimpleNamespace(load_remote_profile=Mock()),
            'botainer_dashboard.remote_backend': SimpleNamespace(RemoteBackend=Mock()),
        }
        with patch.dict(sys.modules, modules), self.assertRaisesRegex(ValueError, 'changed while starting'):
            select_backend(self.root, self.config, local_profile=Path('mac'))

    def test_installed_location_collision_refuses_before_controller_construction(self):
        profile = SimpleNamespace(id='local', label='Local', fingerprint='a'*64)
        make_local, make_remote = Mock(), Mock()
        modules = {
            'botainer_dashboard.ordinary_local': SimpleNamespace(load_local_profile=lambda path: profile, OrdinaryLocalBackend=make_local),
            'botainer_dashboard.remote_profile': SimpleNamespace(load_remote_profile=lambda path: profile),
            'botainer_dashboard.remote_backend': SimpleNamespace(RemoteBackend=make_remote),
        }
        with patch.dict(sys.modules, modules), self.assertRaisesRegex(ValueError, 'unique'):
            select_backend(self.root, self.config, local_profile=Path('mac'), remote_paths=[Path('remote')])
        make_local.assert_not_called()
        make_remote.assert_not_called()

    def test_host_profile_has_distinct_stable_auth_scope_and_no_implicit_botainer(self):
        profiles = {key: SimpleNamespace(id=key, label=key, fingerprint=char * 64, identity_fingerprint=char * 64)
                    for key, char in (('host-one', 'a'), ('host-two', 'b'))}
        children = {key: Mock(profile=value) for key, value in profiles.items()}
        make_local, make_remote = Mock(), Mock()
        modules = {
            'botainer_dashboard.ordinary_local': SimpleNamespace(load_local_profile=Mock(), OrdinaryLocalBackend=make_local),
            'botainer_dashboard.remote_profile': SimpleNamespace(load_remote_profile=Mock()),
            'botainer_dashboard.remote_backend': SimpleNamespace(RemoteBackend=make_remote),
            'botainer_dashboard.host_backend': SimpleNamespace(load_host_profile=lambda p: profiles[str(p)],
                HostAgentBackend=lambda root,p: children[str(p)]),
        }
        with patch.dict(sys.modules, modules), patch('botainer_dashboard.combined_backend.CombinedBackend') as combined:
            _, first = select_backend(self.root, self.config, host_paths=[Path('host-one'), Path('host-two')])
            combined.assert_called_with(None, clusters=children, local_id='local', local_label='This computer')
            _, reordered = select_backend(self.root, self.config, host_paths=[Path('host-two'), Path('host-one')])
            self.assertEqual(first, reordered)
            profiles['host-one'].fingerprint = 'c' * 64
            _, executable_revision = select_backend(self.root, self.config, host_paths=[Path('host-one'), Path('host-two')])
            self.assertEqual(first, executable_revision)
            profiles['host-one'].identity_fingerprint = 'c' * 64
            _, changed = select_backend(self.root, self.config, host_paths=[Path('host-one'), Path('host-two')])
            self.assertNotEqual(first, changed)
        make_local.assert_not_called(); make_remote.assert_not_called()

    def test_host_profile_collision_or_changed_target_refuses(self):
        profile = SimpleNamespace(id='host', label='Host', fingerprint='a' * 64, identity_fingerprint='a' * 64)
        changed = SimpleNamespace(id='host', label='Host', fingerprint='b' * 64, identity_fingerprint='a' * 64)
        make = Mock(return_value=Mock(profile=changed))
        module = SimpleNamespace(load_host_profile=lambda path: profile, HostAgentBackend=make)
        with patch.dict(sys.modules, {'botainer_dashboard.host_backend': module}):
            with self.assertRaisesRegex(ValueError, 'unique'):
                select_backend(self.root, self.config, host_paths=[Path('one'), Path('two')])
            make.assert_not_called()
            with self.assertRaisesRegex(ValueError, 'changed while starting'):
                select_backend(self.root, self.config, host_paths=[Path('one')])


class InstalledProtectionSelectionTests(unittest.TestCase):
    """Prove selected host-local secrets reach the real local target guard."""

    def setUp(self):
        self.f = fixtures.OrdinaryFixture()
        self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.config = SimpleNamespace()

    def local_backend(self, root, path, **kwargs):
        self.local = OrdinaryLocalBackend(root, path, runner=self.f.runner, **kwargs)
        return self.local

    def test_remote_profiles_and_revised_host_controls_and_tools_are_not_project_data(self):
        f = self.f
        # Deliberately outside application, native state, source and Python bin:
        # those existing protections must not accidentally make this test pass.
        protected = {name: f.root / name / leaf for name, leaf in (
            ('remote-profile', 'site.json'), ('host-original', 'original.json'),
            ('host-current', 'revision.json'), ('host-cli', 'claude'),
            ('host-code', 'codex'), ('host-terminal', 'tmux'))}
        for path in protected.values():
            path.parent.mkdir(); path.write_text('inert selected fixture\n')
        control = f.root / 'host-control'; control.mkdir(mode=0o700)
        remote = SimpleNamespace(id='cluster', label='Cluster', fingerprint='b' * 64,
            data={'control_root': '/remote-only/control'})
        host = SimpleNamespace(id='host', label='Host', fingerprint='c' * 64,
            identity_fingerprint='d' * 64, path=protected['host-current'],
            original_path=protected['host-original'], data={
                'control_root': str(control), 'tmux': {'path': str(protected['host-terminal'])},
                'agents': {'claude': {'path': str(protected['host-cli'])},
                           'codex': {'path': str(protected['host-code'])}}})
        with patch('botainer_dashboard.ordinary_local.OrdinaryLocalBackend', side_effect=self.local_backend), \
                patch('botainer_dashboard.remote_profile.load_remote_profile', return_value=remote), \
                patch('botainer_dashboard.remote_backend.RemoteBackend', return_value=Mock(profile=remote)), \
                patch('botainer_dashboard.host_backend.load_host_profile', return_value=host), \
                patch('botainer_dashboard.host_backend.HostAgentBackend', return_value=Mock(profile=host)), \
                patch('botainer_dashboard.combined_backend.CombinedBackend'):
            select_installed_backend(f.application, f.profile_path, [protected['remote-profile']],
                host_paths=[protected['host-original']])
        for path in (*protected.values(), control):
            for target in (path, path.parent):
                with self.subTest(path=path, target=target), self.assertRaisesRegex(BackendUnavailable, 'overlaps-protected'):
                    require_separate_project(target, self.local.protected_paths)
        self.assertEqual(require_separate_project(f.project, self.local.protected_paths), f.project)
        # A remote pathname is not a local control directory; only its private
        # local selection file belongs in the local containment boundary.
        remote_control = Path(remote.data['control_root'])
        self.assertEqual(require_separate_project(remote_control, self.local.protected_paths), remote_control)
        f.runner.assert_not_called()

    def test_saved_registry_file_is_protected_even_when_outside_dashboard_data(self):
        f = self.f
        selected_folder = f.root / 'machine-settings'; selected_folder.mkdir(mode=0o700)
        registry = ConnectionRegistry(selected_folder / 'selection.json')
        registry.initialize()
        registry.add('local', f.profile_data, 0, trusted=True)
        # A manager seam keeps this pure assembly test independent of optional
        # HTTP libraries; auth/route behavior is exercised in its own suite.
        routes = SimpleNamespace(ConnectionManager=Mock())
        with patch.dict(sys.modules, {'botainer_dashboard.connection_routes': routes}), \
                patch('botainer_dashboard.ordinary_local.OrdinaryLocalBackend', side_effect=self.local_backend), \
                patch('botainer_dashboard.combined_backend.CombinedBackend'):
            select_connections_backend(f.application, self.config, registry.path)
        with self.assertRaisesRegex(BackendUnavailable, 'overlaps-protected'):
            require_separate_project(registry.path, self.local.protected_paths)
        with self.assertRaisesRegex(BackendUnavailable, 'overlaps-protected'):
            require_separate_project(selected_folder, self.local.protected_paths)
        self.assertEqual(require_separate_project(f.project, self.local.protected_paths), f.project)
        f.runner.assert_not_called()

    def test_explicit_config_file_is_protected_through_direct_and_saved_selection(self):
        f = self.f
        folder = f.root / 'custom-config'; folder.mkdir()
        config_path = folder / 'settings.json'; config_path.write_text('{"version":1}\n')
        config = SimpleNamespace(source_path=config_path)
        registry = ConnectionRegistry(f.root / 'connections.json')
        registry.initialize(); registry.add('local', f.profile_data, 0, trusted=True)
        routes = SimpleNamespace(ConnectionManager=Mock())
        for saved in (False, True):
            with self.subTest(saved=saved), \
                    patch.dict(sys.modules, {'botainer_dashboard.connection_routes': routes}), \
                    patch('botainer_dashboard.ordinary_local.OrdinaryLocalBackend', side_effect=self.local_backend), \
                    patch('botainer_dashboard.combined_backend.CombinedBackend'):
                if saved:
                    select_connections_backend(f.application, config, registry.path)
                else:
                    select_backend(f.application, config, local_profile=f.profile_path)
                for target in (config_path, folder):
                    with self.assertRaisesRegex(BackendUnavailable, 'overlaps-protected'):
                        require_separate_project(target, self.local.protected_paths)
                self.assertEqual(require_separate_project(f.project, self.local.protected_paths), f.project)
        f.runner.assert_not_called()


if __name__ == '__main__': unittest.main()
