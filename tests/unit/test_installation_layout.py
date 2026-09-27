"""Synthetic distribution metadata only; no package install or native import."""
import hashlib
import os
from pathlib import Path
import tempfile
import unittest

from botainer_dashboard.installation_layout import (
    InstallationLayoutError, discover_layout, metadata_paths, parse_layout,
    validate_source_pins, verify_layout,
)


class InstallationLayoutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.package = self.root / 'botainer'; self.package.mkdir()
        (self.package / '__init__.py').write_text('raise RuntimeError("must not import")\n')
        self.info = self.root / 'botainer-0.1.0a5.dist-info'; self.info.mkdir()
        self.metadata = self.info / 'METADATA'
        self.metadata.write_text('Metadata-Version: 2.4\nName: botainer\nVersion: 0.1.0a5\n\n')
        self.entry = self.info / 'entry_points.txt'
        self.entry.write_text('[console_scripts]\nbotainer = botainer.cli.main:main\n')
        self.layout = {'kind': 'wheel', 'metadata_path': self.metadata.relative_to(self.root).as_posix()}

    def hashes(self):
        return {p.relative_to(self.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (self.metadata, self.entry, self.package / '__init__.py')}

    def test_metadata_recognition_does_not_import_or_follow_record_paths(self):
        (self.info / 'RECORD').write_text('../../outside,sha256=anything,99\n')
        self.assertEqual(discover_layout(self.root), self.layout)
        self.assertEqual(verify_layout(self.root, self.layout, self.hashes()), self.layout)
        self.assertEqual(metadata_paths(self.layout)[1], 'botainer-0.1.0a5.dist-info/entry_points.txt')

    def test_source_layout_remains_v1_and_mixed_installation_refuses(self):
        (self.root / 'pyproject.toml').write_text('[project]\nname = "botainer"\n')
        with self.assertRaisesRegex(InstallationLayoutError, 'Mixed'): discover_layout(self.root)
        self.metadata.unlink(); self.entry.unlink(); self.info.rmdir()
        self.assertIsNone(discover_layout(self.root))

    def test_duplicate_and_case_alias_metadata_refuse(self):
        for name in ('botainer-0.1.0a4.dist-info', 'BOTAINER-extra.dist-info'):
            other = self.root / name; other.mkdir()
            with self.subTest(name=name), self.assertRaises(InstallationLayoutError):
                verify_layout(self.root, self.layout, self.hashes())
            other.rmdir()

    def test_wrong_duplicate_or_multiline_identity_refuses(self):
        for text in ('Name: other\nVersion: 0.1.0a5\n',
                     'Name: botainer\nName: botainer\nVersion: 0.1.0a5\n',
                     'Name: botainer\nVersion: 0.1.0a4\n',
                     'Name: botainer\nVersion: 0.1.0a5\n injected\n'):
            self.metadata.write_text(text)
            with self.subTest(text=text), self.assertRaises(InstallationLayoutError): discover_layout(self.root)

    def test_non_native_and_duplicate_entry_points_refuse(self):
        for text in ('[console_scripts]\nbotainer = attacker:main\n',
                     '[console_scripts]\nbotainer = botainer.cli.main:main\nbotainer = attacker:main\n',
                     '[DEFAULT]\nbotainer = botainer.cli.main:main\n[console_scripts]\n',
                     '[console_scripts]\nBotainer = botainer.cli.main:main\n',
                     '[console_scripts]\nother = botainer.cli.main:main\n'):
            self.entry.write_text(text)
            with self.subTest(text=text), self.assertRaises(InstallationLayoutError): discover_layout(self.root)

    def test_symlinks_hardlinks_and_oversize_metadata_refuse(self):
        original = self.metadata.read_bytes()
        self.metadata.unlink(); self.metadata.symlink_to(self.entry)
        with self.assertRaises(InstallationLayoutError): discover_layout(self.root)
        self.metadata.unlink(); os.link(self.entry, self.metadata)
        with self.assertRaises(InstallationLayoutError): discover_layout(self.root)
        self.metadata.unlink(); self.metadata.write_bytes(original + b'x' * 131073)
        with self.assertRaises(InstallationLayoutError): discover_layout(self.root)

    def test_missing_and_changed_metadata_pins_refuse(self):
        hashes = self.hashes(); hashes.pop(self.entry.relative_to(self.root).as_posix())
        with self.assertRaises(InstallationLayoutError): verify_layout(self.root, self.layout, hashes)
        hashes = self.hashes(); self.entry.write_text(self.entry.read_text() + '\n# changed\n')
        with self.assertRaises(InstallationLayoutError): verify_layout(self.root, self.layout, hashes)

    def test_unrelated_site_packages_are_not_inside_pin_scope(self):
        hashes = self.hashes()
        for name in ('other/__init__.py', 'botainer/../other.py', 'botainer//bad.py', '/etc/passwd'):
            with self.subTest(name=name), self.assertRaises(InstallationLayoutError):
                validate_source_pins(self.layout, {**hashes, name: 'a' * 64})

    def test_descriptor_cannot_choose_an_arbitrary_metadata_location(self):
        for value in ({'kind': 'source', 'metadata_path': self.layout['metadata_path']},
                      {**self.layout, 'extra': True},
                      {'kind': 'wheel', 'metadata_path': '../other/METADATA'},
                      {'kind': 'wheel', 'metadata_path': 'other-0.1.dist-info/METADATA'}):
            with self.subTest(value=value), self.assertRaises(InstallationLayoutError): parse_layout(value)

    def test_editable_plugin_override_refuses_even_when_metadata_valid(self):
        (self.root / 'pyproject.toml').write_text('[project]\nname = "botainer"\n')
        (self.root / 'plugins').mkdir()
        with self.assertRaisesRegex(InstallationLayoutError, 'editable plugin override'):
            verify_layout(self.root, self.layout, self.hashes())


if __name__ == '__main__': unittest.main()
