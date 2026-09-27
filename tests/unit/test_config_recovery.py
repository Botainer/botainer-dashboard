"""Recovery storage is private, bounded and independent of project content."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from botainer_dashboard.config_recovery import ConfigRecoveryError, MAX_RECORD, SLOTS, preserve_config


class ConfigRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_recovers_exact_base_and_draft_without_names_in_path(self):
        value = preserve_config(self.root, '/synthetic/project', b'base\n', 'draft café\n'.encode())
        path = Path(value['path']); record = json.loads(path.read_text())
        self.assertEqual(record['id'], value['id'])
        self.assertEqual(record['base'], 'base\n'); self.assertEqual(record['draft'], 'draft café\n')
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        self.assertNotIn('project', path.name)

    def test_fixed_count_and_byte_bounds_keep_newest_attempts(self):
        for index in range(SLOTS + 4):
            # Ordering must survive a clock correction backwards.
            with patch('botainer_dashboard.config_recovery.time.time_ns', return_value=1000-index):
                preserve_config(self.root, str(index), b'base', str(index).encode())
        files = list((self.root / 'config-recovery').glob('slot-*.json'))
        self.assertEqual(len(files), SLOTS)
        self.assertEqual({json.loads(p.read_text())['draft'] for p in files}, {str(i) for i in range(4, SLOTS + 4)})
        self.assertLessEqual(sum(p.stat().st_size for p in files), MAX_RECORD * SLOTS)
        with self.assertRaises(ConfigRecoveryError): preserve_config(self.root, 'target', b'x' * 65537, b'draft')

    def test_symlink_directory_and_slot_are_not_followed(self):
        outside = self.root / 'outside'; outside.mkdir(mode=0o700)
        folder = self.root / 'config-recovery'; folder.symlink_to(outside)
        with self.assertRaises(ConfigRecoveryError): preserve_config(self.root, 'target', b'base', b'draft')
        folder.unlink(); folder.mkdir(mode=0o700)
        target = outside / 'keep'; target.write_text('unchanged')
        (folder / 'slot-00.json').symlink_to(target)
        with self.assertRaises(ConfigRecoveryError): preserve_config(self.root, 'target', b'base', b'draft')
        self.assertEqual(target.read_text(), 'unchanged')

    def test_hardlinked_or_nonprivate_records_block_overwrite(self):
        value = preserve_config(self.root, 'target', b'base', b'draft'); path = Path(value['path'])
        os.link(path, self.root / 'alias')
        with self.assertRaises(ConfigRecoveryError): preserve_config(self.root, 'target', b'base', b'other')
        (self.root / 'alias').unlink(); path.chmod(0o644)
        with self.assertRaises(ConfigRecoveryError): preserve_config(self.root, 'target', b'base', b'other')

    def test_storage_error_is_fixed_and_does_not_damage_previous_copy(self):
        value = preserve_config(self.root, 'target', b'base', b'draft'); before = Path(value['path']).read_bytes()
        with patch('botainer_dashboard.config_recovery.os.replace', side_effect=OSError('sensitive path')):
            with self.assertRaisesRegex(ConfigRecoveryError, '^config-recovery-unavailable$'):
                preserve_config(self.root, 'target', b'base', b'other')
        self.assertEqual(Path(value['path']).read_bytes(), before)
        self.assertFalse((self.root / 'config-recovery/pending.json').exists())
