"""Synthetic retention/privacy checks. No SSH, native commands or real profile."""
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
import uuid

from botainer_dashboard import ssh_transport
from botainer_dashboard.pairing import write_private_json
from botainer_dashboard.remote_backend import RemoteBackend
from botainer_dashboard.remote_observations import RemoteObservations, MAX_BYTES, MAX_ENTRIES
from botainer_dashboard.ssh_diagnostics import SshRequestUnavailable


REPO = Path(__file__).resolve().parents[2]
PROFILE = 'a' * 64
HELPER = 'b' * 64
SECRET = 'SYNTHETIC-PRIVATE-FILE-OR-CONFIG-TEXT'


class RetentionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve(); self.root.chmod(0o700)
        self.now = 2000000000
        self.store = RemoteObservations(self.root, PROFILE, clock=lambda: self.now)
        self.backend = RemoteBackend.__new__(RemoteBackend)
        self.backend.root = self.root
        self.backend.profile = types.SimpleNamespace(fingerprint=PROFILE, data={'remote_python': '/synthetic/python'})
        self.backend.payload = '# synthetic source, never executed'
        self.backend.payload_digest = HELPER
        self.backend._verify = lambda: None
        self.backend._ssh = lambda args: ['/synthetic/ssh-never-executed']
        self.backend._observations = self.store
        self.backend.driver = ssh_transport
        self.response = {'text': SECRET, 'revision': 'c' * 64, 'saved': True}
        self.result = {'transport_status': 'complete', 'returncode': 0}
        def exchange(argv, raw, **kwargs):
            self.assertEqual(argv, ['/synthetic/ssh-never-executed'])
            self.assertIsInstance(json.loads(raw), dict)
            return {**self.result, 'stdout': json.dumps(self.response).encode(), 'stderr': b''}
        self.backend.exchange = Mock(side_effect=exchange)

    def journal(self):
        return json.loads(self.store.path.read_text())

    def contents(self):
        return b'\n'.join(path.read_bytes() for path in self.root.rglob('*') if path.is_file() and not path.is_symlink())

    def legacy(self, action='inventory', *, result=None, response=None, extra=False):
        directory = self.root / ('observation-' + uuid.uuid4().hex)
        directory.mkdir(mode=0o700)
        write_private_json(directory / 'intent.json', {'action': action, 'profile': PROFILE, 'helper_sha256': HELPER, 'retry': False})
        write_private_json(directory / 'result.json', result or {'transport_status': 'complete', 'returncode': 0})
        for name, content in [('stdout.bin', json.dumps(response or {'text': SECRET}).encode()), ('stderr.bin', b'')]:
            with os.fdopen(os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
                stream.write(content)
        if extra: (directory / 'do-not-delete.txt').write_text(SECRET)
        return directory

    def record(self):
        self.store.record('inventory', {'transport_status': 'complete', 'returncode': 0}, outcome='success')

    def test_repeated_reads_keep_fixed_metadata_footprint_and_return_full_content(self):
        for index in range(200):
            action = ['inventory', 'files', 'file', 'config', 'validate-config'][index % 5]
            self.assertEqual(self.backend._call(action), self.response)
        self.assertEqual(len(self.journal()['entries']), MAX_ENTRIES)
        self.assertLessEqual(self.store.path.stat().st_size, MAX_BYTES)
        self.assertEqual({path.name for path in self.root.iterdir()}, {'routine-observations.json', 'routine-observations.lock'})
        self.assertNotIn(SECRET.encode(), self.contents())
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)

    def test_failures_store_only_typed_categories_not_stdout_stderr_or_exception(self):
        self.backend.exchange = Mock(return_value={'transport_status': 'complete', 'returncode': 255,
            'stdout': SECRET.encode(), 'stderr': ('synthetic-private-host: Permission denied (publickey).\n' + SECRET).encode()})
        with self.assertRaises(SshRequestUnavailable) as raised:
            self.backend._call('file')
        self.assertEqual(raised.exception.diagnostic_code, 'ssh-authentication-required')
        self.assertEqual(self.journal()['entries'][-1]['diagnostic'], 'ssh-authentication-required')
        self.backend.exchange = Mock(side_effect=OSError(SECRET))
        with self.assertRaises(OSError): self.backend._call('config')
        self.assertEqual(self.journal()['entries'][-1]['outcome'], 'exception')
        self.assertNotIn(SECRET.encode(), self.contents())
        self.assertNotIn(b'synthetic-private-host', self.contents())

    def test_helper_verification_failure_retains_only_fixed_diagnostic_category(self):
        envelope = {'protocol': 'botainer-dashboard.remote-error', 'version': 1, 'code': 'remote-installation-unsafe'}
        self.backend.exchange = Mock(return_value={'transport_status': 'complete', 'returncode': 2,
            'stdout': json.dumps(envelope).encode(), 'stderr': SECRET.encode()})
        with self.assertRaises(SshRequestUnavailable) as raised: self.backend._call('inventory')
        self.assertEqual(raised.exception.diagnostic_code, 'remote-installation-unsafe')
        self.assertEqual(self.journal()['entries'][-1]['diagnostic'], 'remote-installation-unsafe')
        self.assertNotIn(SECRET.encode(), self.contents())

    def test_malformed_read_response_is_not_saved(self):
        self.backend.exchange = Mock(return_value={'transport_status': 'complete', 'returncode': 0,
            'stdout': SECRET.encode(), 'stderr': SECRET.encode()})
        with self.assertRaises(ValueError): self.backend._call('config')
        self.assertEqual(self.journal()['entries'][-1]['outcome'], 'invalid-response')
        self.assertNotIn(SECRET.encode(), self.contents())

    def test_config_mutation_omits_success_text_and_keeps_exact_outcome_identity(self):
        request = str(uuid.uuid4())
        self.assertEqual(self.backend._call('save-config', request_id=request, config_revision='d' * 64), self.response)
        directory = next(self.root.glob('operation-*'))
        self.assertEqual(json.loads((directory / 'intent.json').read_text())['request_id'], request)
        stored = json.loads((directory / 'stdout.bin').read_text())
        self.assertEqual(stored, {'revision': 'c' * 64, 'saved': True})
        self.assertNotIn(SECRET.encode(), self.contents())

    def test_mutation_unknown_action_and_reconciliation_evidence_survive_routine_retention(self):
        for action in ('stop', 'prepare', 'future-action'):
            self.backend._call(action, request_id=str(uuid.uuid4()))
        originals = {path: path.read_bytes() for directory in self.root.glob('operation-*') for path in directory.iterdir()}
        for _ in range(100): self.backend._call('inventory')
        self.assertEqual(len(list(self.root.glob('operation-*'))), 3)
        self.assertEqual(originals, {path: path.read_bytes() for path in originals})
        self.assertIn(SECRET.encode(), self.contents())  # Durable evidence is deliberately separate, not redacted wholesale.

    def test_mutation_intent_disk_failure_never_dispatches(self):
        with patch('botainer_dashboard.remote_backend.write_private_json', side_effect=OSError('synthetic disk full')):
            with self.assertRaises(OSError): self.backend._call('stop', request_id=str(uuid.uuid4()))
        self.backend.exchange.assert_not_called()

    def test_unresolved_mutation_receipt_survives_restart_and_age_retention(self):
        self.result = {'transport_status': 'timeout', 'returncode': -9, 'remote_outcome': 'unknown'}
        with self.assertRaises(SshRequestUnavailable):
            self.backend._call('stop', request_id=str(uuid.uuid4()))
        directory = next(self.root.glob('operation-*'))
        original = {path: path.read_bytes() for path in directory.iterdir()}
        self.now += 30 * 24 * 60 * 60
        self.store = RemoteObservations(self.root, PROFILE, clock=lambda: self.now)
        for _ in range(100): self.record()
        self.assertEqual(original, {path: path.read_bytes() for path in original})

    def receipt_response(self, request, phase='dispatching'):
        scope = {'request_id': request, 'operation': 'start', 'project_uuid': str(uuid.UUID(int=1)),
                 'project_path': '/synthetic/project', 'config_revision': 'd' * 64}
        return scope, {**scope, 'profile_fingerprint': PROFILE, 'phase': phase}

    def test_unknown_receipt_polling_across_restart_has_fixed_first_latest_evidence(self):
        request = str(uuid.uuid4())
        self.backend._call('stop', request_id=request)
        original = {path: path.read_bytes() for directory in self.root.glob('operation-*') for path in directory.iterdir()}
        scope, self.response = self.receipt_response(request)
        self.backend._call('receipt', **scope)
        directory = self.root / ('reconciliation-' + request)
        first = (directory / 'first.json').read_bytes()
        for index in range(100):
            if index == 50:
                self.store = RemoteObservations(self.root, PROFILE, clock=lambda: self.now)
                self.backend._observations = self.store
            self.response = {**self.response, 'observation_number': index}
            self.backend._call('receipt', **scope)
        self.assertEqual((directory / 'first.json').read_bytes(), first)
        self.assertEqual(json.loads((directory / 'latest.json').read_bytes())['observation_number'], 99)
        self.assertEqual({path.name for path in directory.iterdir()}, {'first.json', 'latest.json'})
        self.assertEqual(len(list(self.root.glob('operation-*'))), 1)
        self.assertEqual(len(list(self.root.glob('reconciliation-*'))), 1)
        self.assertEqual(original, {path: path.read_bytes() for path in original})
        self.assertLessEqual(len(self.journal()['entries']), MAX_ENTRIES)

    def test_failed_malformed_and_missing_receipt_observations_cannot_replace_good_evidence(self):
        request = str(uuid.uuid4()); scope, self.response = self.receipt_response(request)
        self.backend._call('receipt', **scope)
        directory = self.root / ('reconciliation-' + request)
        original = {path: path.read_bytes() for path in directory.iterdir()}
        self.result = {'transport_status': 'timeout', 'returncode': -9}
        with self.assertRaises(SshRequestUnavailable): self.backend._call('receipt', **scope)
        self.result = {'transport_status': 'complete', 'returncode': 0}
        for response in ({'phase': []}, {**self.response, 'project_path': '/different/project'},
                         {**self.response, 'request_id': str(uuid.uuid4())}):
            self.response = response
            with self.assertRaises(ValueError): self.backend._call('receipt', **scope)
        self.response = {'phase': 'unobserved'}
        self.backend._call('receipt', **scope)
        self.assertEqual(original, {path: path.read_bytes() for path in original})
        self.assertEqual(len(list(self.root.glob('operation-*'))), 0)

    def test_receipt_disk_failure_preserves_good_evidence_and_is_not_hidden_by_routine_journal(self):
        request = str(uuid.uuid4()); scope, self.response = self.receipt_response(request)
        self.backend._call('receipt', **scope)
        directory = self.root / ('reconciliation-' + request)
        original = {path: path.read_bytes() for path in directory.iterdir()}
        self.response = {**self.response, 'phase': 'completed', 'exit_code': 0}
        with patch.object(self.store, '_receipt_write', side_effect=OSError('synthetic disk full')):
            self.assertEqual(self.backend._call('receipt', **scope), self.response)
        self.assertEqual(original, {path: path.read_bytes() for path in original})
        self.assertEqual(self.store.status()['status'], 'unavailable')
        self.backend._call('inventory')
        self.assertEqual(self.store.status()['status'], 'unavailable')
        self.backend._call('receipt', **scope)
        self.assertEqual(self.store.status()['status'], 'ok')

    def test_existing_malformed_or_linked_receipt_snapshot_is_preserved_for_review(self):
        request = str(uuid.uuid4()); scope, self.response = self.receipt_response(request)
        self.backend._call('receipt', **scope)
        directory = self.root / ('reconciliation-' + request)
        latest = (directory / 'latest.json').read_bytes()
        (directory / 'first.json').write_text(SECRET)
        self.backend._call('receipt', **scope)
        self.assertEqual((directory / 'first.json').read_text(), SECRET)
        self.assertEqual((directory / 'latest.json').read_bytes(), latest)
        self.assertEqual(self.store.status()['status'], 'unavailable')
        (directory / 'first.json').unlink()
        (directory / 'first.json').symlink_to(directory / 'latest.json')
        self.backend._call('receipt', **scope)
        self.assertTrue((directory / 'first.json').is_symlink())
        self.assertEqual((directory / 'latest.json').read_bytes(), latest)

    def test_read_diagnostic_disk_failure_does_not_change_read_outcome_or_replace_good_journal(self):
        self.backend._call('file'); previous = self.store.path.read_bytes()
        legacy = self.legacy('config')
        with patch.object(self.store, '_write_journal', side_effect=OSError('synthetic disk full')):
            self.assertEqual(self.backend._call('config'), self.response)
        self.assertEqual(self.store.status()['status'], 'unavailable')
        self.assertEqual(self.store.path.read_bytes(), previous)
        self.assertTrue(legacy.exists())
        self.backend._call('config')
        self.assertEqual(self.store.status()['status'], 'ok')

    def test_restart_count_byte_and_age_limits(self):
        self.store = RemoteObservations(self.root, PROFILE, clock=lambda: self.now, max_entries=8, max_bytes=1024, max_age=10)
        for _ in range(20): self.record()
        self.assertLess(len(self.journal()['entries']), 8)
        self.assertLessEqual(self.store.path.stat().st_size, 1024)
        self.now += 11
        self.store = RemoteObservations(self.root, PROFILE, clock=lambda: self.now, max_entries=8, max_bytes=1024, max_age=10)
        self.record()
        self.assertEqual(len(self.journal()['entries']), 1)

    def test_interrupted_journal_replacement_keeps_previous_and_removes_temporary(self):
        self.backend._call('config'); previous = self.store.path.read_bytes()
        with patch('botainer_dashboard.pairing.os.replace', side_effect=OSError('synthetic interrupted replacement')):
            self.assertEqual(self.backend._call('file'), self.response)
        self.assertEqual(self.store.path.read_bytes(), previous)
        self.assertEqual(self.store.status()['status'], 'unavailable')
        self.assertEqual({path.name for path in self.root.iterdir()}, {'routine-observations.json', 'routine-observations.lock'})

    def test_single_crash_staging_slot_is_recovered_and_unsafe_slot_is_preserved(self):
        pending = self.root / 'routine-observations.pending'
        for _ in range(5):
            with os.fdopen(os.open(pending, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'wb') as stream:
                stream.write(b'{"partial":')
            self.backend._call('config')
            self.assertFalse(pending.exists())
        self.assertEqual({path.name for path in self.root.iterdir()}, {'routine-observations.json', 'routine-observations.lock'})
        target = self.root / 'outside.txt'; target.write_text(SECRET)
        pending.symlink_to(target)
        self.assertEqual(self.backend._call('config'), self.response)
        self.assertTrue(pending.is_symlink())
        self.assertEqual(target.read_text(), SECRET)
        self.assertEqual(self.store.status()['status'], 'unavailable')

    def test_malformed_journal_is_retained_and_reported_without_breaking_read(self):
        self.store.path.write_text(SECRET); self.store.path.chmod(0o600)
        self.assertEqual(self.backend._call('file'), self.response)
        self.assertEqual(self.store.path.read_text(), SECRET)
        self.assertEqual(self.store.status()['status'], 'unavailable')

    def test_journal_symlink_cannot_overwrite_target(self):
        target = self.root / 'outside.txt'; target.write_text(SECRET)
        self.store.path.symlink_to(target)
        self.assertEqual(self.backend._call('file'), self.response)
        self.assertEqual(target.read_text(), SECRET)
        self.assertTrue(self.store.path.is_symlink())
        self.assertEqual(self.store.status()['status'], 'unavailable')

    def test_legacy_migration_only_removes_complete_successful_known_reads(self):
        removable = [self.legacy(action) for action in ('inventory', 'files', 'file', 'config', 'validate-config')]
        retained = [self.legacy(action) for action in ('stop', 'save-config', 'receipt', 'prepare', 'future-action')]
        retained.append(self.legacy(result={'transport_status': 'timeout', 'returncode': -9}))
        retained.append(self.legacy(result={'transport_status': 'complete', 'returncode': 2}))
        retained.append(self.legacy(extra=True))
        malformed = self.legacy(); (malformed / 'stdout.bin').write_text('not json'); retained.append(malformed)
        interrupted = self.legacy(); (interrupted / 'result.json').unlink(); retained.append(interrupted)
        foreign = self.legacy(); intent = json.loads((foreign / 'intent.json').read_text()); intent['profile'] = 'f' * 64
        write_private_json(foreign / 'intent.json', intent); retained.append(foreign)
        originals = {path: path.read_bytes() for directory in retained for path in directory.iterdir()}
        for _ in range(4): self.record()
        self.assertTrue(all(not directory.exists() for directory in removable))
        self.assertEqual(originals, {path: path.read_bytes() for path in originals})
        self.assertEqual(self.store.status()['status'], 'review-retained-legacy')
        self.assertEqual(self.store.status()['legacy']['retained'], len(retained))

    def test_legacy_linked_files_and_extra_nested_content_are_not_removed(self):
        linked = self.legacy(); target = self.root / 'outside'; target.write_text(SECRET); target.chmod(0o600)
        (linked / 'stdout.bin').unlink(); (linked / 'stdout.bin').symlink_to(target)
        hardlinked = self.legacy(); os.link(hardlinked / 'stdout.bin', self.root / 'second-link')
        nested = self.legacy(); (nested / 'extra').mkdir(); (nested / 'extra/private.txt').write_text(SECRET)
        linkdir = self.root / ('observation-' + uuid.uuid4().hex); linkdir.symlink_to(nested)
        self.record()
        for directory in (linked, hardlinked, nested, linkdir): self.assertTrue(directory.exists())
        self.assertEqual(target.read_text(), SECRET)
        self.assertEqual((nested / 'extra/private.txt').read_text(), SECRET)

    def test_legacy_scan_has_bounded_work_and_makes_progress(self):
        legacy = [self.legacy() for _ in range(12)]
        self.store = RemoteObservations(self.root, PROFILE, clock=lambda: self.now, scan_budget=1)
        previous = len(legacy)
        for _ in range(30):
            self.record()
            remaining = sum(path.exists() for path in legacy)
            self.assertLessEqual(previous - remaining, 1)
            previous = remaining
        self.assertEqual(previous, 0)
        self.assertTrue(self.store.status()['legacy']['complete'])

    def test_failed_cleanup_retains_remaining_files_and_reports_review(self):
        legacy = self.legacy()
        original = os.unlink
        def refuse_legacy(path, *args, **kwargs):
            if kwargs.get('dir_fd') is not None and str(path) in {'intent.json', 'result.json', 'stdout.bin', 'stderr.bin'}:
                raise PermissionError('synthetic refusal')
            return original(path, *args, **kwargs)
        with patch('botainer_dashboard.remote_observations.os.unlink', side_effect=refuse_legacy):
            self.record()
        self.assertTrue(legacy.exists())
        self.assertEqual(self.store.status()['status'], 'review-retained-legacy')
        self.assertEqual(len(list(legacy.iterdir())), 4)


if __name__ == '__main__':
    unittest.main()
