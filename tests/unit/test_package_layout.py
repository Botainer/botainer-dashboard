"""Package/state separation, including read-only checks and ordinary venv links."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from botainer_dashboard import cli, layout
from botainer_dashboard.launcher import authentication_profile


class PackageLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.package = self.root / "env/lib/botainer_dashboard"
        self.resources = self.package / "_resources"
        self.resources.mkdir(parents=True)
        self.module = self.package / "layout.py"
        self.module.write_text("# inert package fixture\n")
        self.marker = self.resources / "PACKAGE-MANIFEST.json"
        self.marker.write_text(json.dumps({"schema_version": 1, "distribution": "botainer-dashboard",
            "version": "0.1.0a1", "files": [{"source": "src/botainer_dashboard/layout.py",
                "path": "layout.py", "sha256": hashlib.sha256(self.module.read_bytes()).hexdigest()}]}))
        changed = patch.object(layout, "__file__", str(self.module))
        changed.start()
        self.addCleanup(changed.stop)

    def test_packaged_resources_never_depend_on_working_directory(self):
        with patch.object(Path, "cwd", side_effect=AssertionError("no cwd discovery")):
            self.assertEqual(layout.resource_root(), self.resources)
            layout.verify_package(self.resources)

    def test_absent_marker_and_modified_unlisted_missing_sources_fail(self):
        original = self.module.read_bytes()
        self.module.write_text("# replaced\n")
        with self.assertRaisesRegex(ValueError, "changed or missing"):
            layout.verify_package(self.resources)
        self.module.write_bytes(original)
        extra = self.package / "unreviewed.py"
        extra.write_text("# not in inventory\n")
        with self.assertRaisesRegex(ValueError, "unlisted"):
            layout.verify_package(self.resources)
        extra.unlink()
        self.module.unlink()
        with self.assertRaisesRegex(ValueError, "changed or missing"):
            layout.verify_package(self.resources)
        self.marker.unlink()
        with self.assertRaisesRegex(ValueError, "inventory is missing"):
            layout.resource_root()

    def test_manifest_cannot_escape_package_or_repeat_a_path(self):
        value = json.loads(self.marker.read_text())
        for name in ("../outside", "/etc/passwd", "./layout.py", "layout.py/../layout.py", "x\\y"):
            value["files"][0]["path"] = name
            self.marker.write_text(json.dumps(value))
            with self.subTest(name=name), self.assertRaises(ValueError):
                layout.verify_package(self.resources)
        value["files"][0]["path"] = "layout.py"
        value["files"].append(value["files"][0].copy())
        self.marker.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            layout.verify_package(self.resources)

    def test_state_defaults_and_override_do_not_create_anything(self):
        home = self.root / "home"
        with patch.object(Path, "home", return_value=home), patch.object(layout.sys, "platform", "darwin"):
            self.assertEqual(layout.data_root(self.resources), home / "Library/Application Support/Botainer Dashboard")
        with patch.object(Path, "home", return_value=home), patch.object(layout.sys, "platform", "linux"), \
                patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root / "xdg")}):
            self.assertEqual(layout.data_root(self.resources), self.root / "xdg/botainer-dashboard")
        self.assertEqual(layout.data_root(self.resources, home / "explicit"), home / "explicit")
        self.assertFalse(home.exists())
        self.assertFalse((self.root / "xdg").exists())

    def test_state_refuses_package_environment_and_symlinks(self):
        with patch.object(layout.sys, "prefix", str(self.root / "env")):
            for path in (self.package / "state", self.root / "env/state",
                         self.root / "other/../env/state"):
                with self.assertRaisesRegex(ValueError, "outside"):
                    layout.data_root(self.resources, path)
        target = self.root / "target"
        target.mkdir()
        link = self.root / "link"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            layout.data_root(self.resources, link / "state")

    def test_normal_symlinked_venv_interpreter_is_accepted(self):
        executable = self.root / "env/bin/python"
        executable.parent.mkdir()
        base = self.root / "base-python"
        base.write_text("# inert never executed\n")
        base.chmod(0o700)
        executable.symlink_to(base)
        with patch.object(layout.sys, "prefix", str(self.root / "env")), \
                patch.object(layout.sys, "executable", str(executable)), \
                patch.object(layout.sys, "version_info", (3, 13, 15)):
            self.assertTrue(layout.runtime_valid(self.resources))
        with patch.object(layout.sys, "prefix", str(self.root / "unrelated")):
            self.assertFalse(layout.runtime_valid(self.resources))

    def test_existing_source_container_permissions_remain_compatible(self):
        source = self.root / "source"
        local = source / ".local"
        local.mkdir(parents=True)
        local.chmod(0o755)
        self.assertEqual(layout.prepare_data_root(source), local)
        self.assertEqual(local.stat().st_mode & 0o777, 0o755)
        # Only the default source container has this compatibility rule.
        with self.assertRaisesRegex(ValueError, "owner-only"):
            layout.prepare_data_root(source, local)
        with self.assertRaisesRegex(ValueError, "owner-only"):
            layout.prepare_data_root(self.resources, local)
        with patch.object(layout, "default_data_root", return_value=local), \
                self.assertRaisesRegex(ValueError, "owner-only"):
            layout.prepare_data_root(self.resources)
        with patch.object(layout.os, "getuid", return_value=os.getuid() + 1), \
                self.assertRaisesRegex(ValueError, "belong to the current user"):
            layout.prepare_data_root(source)
        local.chmod(0o775)
        with self.assertRaisesRegex(ValueError, "not be writable"):
            layout.prepare_data_root(source)

    def test_fresh_installed_data_root_is_private_and_separate(self):
        selected = self.root / "fresh-data"
        self.assertEqual(layout.prepare_data_root(self.resources, selected), selected)
        self.assertEqual(selected.stat().st_mode & 0o777, 0o700)
        legacy = layout.prepare_data_root(self.root / "fresh-source")
        self.assertEqual(legacy.stat().st_mode & 0o777, 0o700)

    def test_explicit_source_state_preserves_pairing_identity(self):
        source = self.root / "source"
        legacy = authentication_profile(source, None, "example")
        selected = authentication_profile(source, None, "example", data_dir=source / ".local")
        self.assertEqual(legacy, selected)
        separate = authentication_profile(source, None, "example", data_dir=self.root / "other-state")
        self.assertEqual(legacy.name, separate.name)
        self.assertNotEqual(legacy, separate)

    def test_installed_plan_uses_external_state_and_refuses_development_trials(self):
        target = self.root / "state"
        args = cli.parser().parse_args(["check", "--data-dir", str(target)])
        plan = cli.plan_for(args, root=self.resources)
        self.assertEqual(plan.connections, target / "connections/selection.json")
        self.assertIn("--data-dir", plan.argv())
        self.assertFalse(target.exists())
        for flag in ("--local-only", "--native-cli", "--cluster-only"):
            with self.subTest(flag=flag), self.assertRaisesRegex(ValueError, "trial modes"):
                cli.plan_for(cli.parser().parse_args([flag]), root=self.resources)
