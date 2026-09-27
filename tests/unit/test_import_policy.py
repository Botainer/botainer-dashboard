"""Selected-package import safeguards with disposable source and no installers."""
from pathlib import Path
import os
import importlib.util
import py_compile
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from botainer_dashboard.import_policy import (ImportPolicyError, cache_arguments,
                                              remove_empty_cache_before_exec, source_imports,
                                              source_modules, trusted_path)


class ImportPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="dashboard-import-policy-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.package = self.root / "botainer"
        self.package.mkdir(mode=0o700)
        (self.package / "__init__.py").write_text("")

    def test_cache_scope_is_private_empty_restored_and_removed_even_on_failure(self):
        previous = sys.pycache_prefix, sys.dont_write_bytecode
        with self.assertRaisesRegex(RuntimeError, "synthetic"):
            with source_imports():
                folder = Path(sys.pycache_prefix)
                self.assertTrue(folder.is_dir())
                self.assertEqual(list(folder.iterdir()), [])
                self.assertEqual(folder.stat().st_mode & 0o077, 0)
                self.assertTrue(sys.dont_write_bytecode)
                raise RuntimeError("synthetic")
        self.assertEqual((sys.pycache_prefix, sys.dont_write_bytecode), previous)
        self.assertFalse(folder.exists())

    def test_normal_installed_cache_is_not_deleted_or_part_of_module_set(self):
        cache = self.package / "__pycache__"
        cache.mkdir(); (cache / "__init__.cpython-311.pyc").write_bytes(b"inert")
        self.assertEqual(source_modules(self.root), {"botainer/__init__.py"})
        self.assertEqual((cache / "__init__.cpython-311.pyc").read_bytes(), b"inert")

    def test_other_account_owned_path_is_refused(self):
        original = Path.stat
        def observe(path, *args, **kwargs):
            value = original(path, *args, **kwargs)
            if path == self.package:
                return SimpleNamespace(st_uid=os.getuid() + 1, st_mode=value.st_mode)
            return value
        with patch.object(Path, "stat", observe):
            with self.assertRaisesRegex(ImportPolicyError, "owner-untrusted"):
                trusted_path(self.package)

    def test_shared_parent_without_sticky_protection_is_refused(self):
        self.root.chmod(0o777)
        with self.assertRaisesRegex(ImportPolicyError, "other-account-writable"):
            source_modules(self.root)

    def test_selected_interpreter_link_cannot_hide_shared_installation_permissions(self):
        shared = self.root / "shared-runtime"
        shared.mkdir(mode=0o775)
        shared.chmod(0o775)
        binary = shared / "python"
        binary.write_bytes(b"inert interpreter fixture; never executed")
        binary.chmod(0o755)
        private = self.root / "private-environment"
        private.mkdir(mode=0o700)
        selected = private / "python"
        selected.symlink_to(binary)
        with self.assertRaisesRegex(ImportPolicyError, "other-account-writable"):
            trusted_path(selected.resolve(strict=True))
        # A copied fixture has its own path and leaves the shared install alone.
        # Base interpreter libraries remain trusted separately, as documented.
        selected.unlink()
        selected.write_bytes(binary.read_bytes())
        selected.chmod(0o755)
        trusted_path(selected.resolve(strict=True))
        self.assertEqual(stat.S_IMODE(shared.stat().st_mode), 0o775)
        self.assertEqual(selected.read_bytes(), binary.read_bytes())
        private.chmod(0o775)
        with self.assertRaisesRegex(ImportPolicyError, "other-account-writable"):
            trusted_path(selected.resolve(strict=True))

    def check_applications_tool(self, *, allow=True, platform="darwin", changes=None,
                                path="/Applications/Docker.app/Contents/Resources/bin/docker",
                                admin_missing=False):
        # Synthetic metadata only: never inspect, chmod or execute a host tool.
        target = Path(path)
        observations = {item: {"st_uid": 0, "st_gid": 0,
                              "st_mode": (stat.S_IFREG if item == target else stat.S_IFDIR) | 0o755}
                        for item in (target, *target.parents)}
        observations[Path("/Applications")].update(st_gid=80, st_mode=stat.S_IFDIR | 0o775)
        for name, values in (changes or {}).items():
            observations[Path(name)].update(values)
        with patch.object(sys, "platform", platform), \
                patch.object(Path, "resolve", lambda value, strict=False: value), \
                patch.object(Path, "stat", lambda value: SimpleNamespace(**observations[value])), \
                patch("grp.getgrnam", return_value=SimpleNamespace(gr_gid=80),
                      side_effect=KeyError("admin") if admin_missing else None):
            trusted_path(target, allow_macos_docker_app=allow)

    def test_selected_macos_docker_allows_only_admin_applications_parent(self):
        self.check_applications_tool()

    def test_macos_docker_parent_allowance_requires_explicit_opt_in(self):
        with self.assertRaisesRegex(ImportPolicyError, "other-account-writable"):
            self.check_applications_tool(allow=False)

    def test_macos_docker_parent_allowance_is_not_portable_to_other_platforms(self):
        with self.assertRaisesRegex(ImportPolicyError, "other-account-writable"):
            self.check_applications_tool(platform="linux")

    def test_macos_docker_parent_refuses_world_write_wrong_owner_or_group(self):
        cases = [{"st_mode": stat.S_IFDIR | 0o777}, {"st_gid": 81},
                 {"st_uid": os.getuid() or 1}]
        for metadata in cases:
            with self.subTest(metadata=metadata), self.assertRaises(ImportPolicyError):
                self.check_applications_tool(changes={"/Applications": metadata})

    def test_macos_docker_parent_refuses_missing_admin_group(self):
        with self.assertRaisesRegex(ImportPolicyError, "other-account-writable"):
            self.check_applications_tool(admin_missing=True)

    def test_macos_docker_parent_allowance_keeps_inner_paths_and_file_strict(self):
        base = Path("/Applications/Docker.app/Contents/Resources/bin/docker")
        for path in (base, *list(base.parents)[:4]):
            mode = stat.S_IFREG if path == base else stat.S_IFDIR
            with self.subTest(path=str(path)), self.assertRaisesRegex(
                    ImportPolicyError, "other-account-writable"):
                self.check_applications_tool(changes={str(path): {"st_mode": mode | 0o775}})

    def test_macos_docker_parent_allowance_never_applies_to_python_or_hooks(self):
        paths = ["/Applications/Python.app/Contents/bin/python3",
                 "/Applications/Docker.app/Contents/Resources/bin/hook.py",
                 "/Applications/Docker.app/Contents/Resources/bin/docker.py",
                 "/Applications/Docker.app/Contents/Resources/botainer/__init__.py"]
        for path in paths:
            with self.subTest(path=path), self.assertRaisesRegex(
                    ImportPolicyError, "other-account-writable"):
                self.check_applications_tool(path=path)

    def test_symlinked_package_subdirectory_is_refused(self):
        extra = self.root / "outside"; extra.mkdir()
        (self.package / "redirected").symlink_to(extra, target_is_directory=True)
        with self.assertRaisesRegex(ImportPolicyError, "directory-symlink"):
            source_modules(self.root)

    def test_child_flags_ignore_bad_cache_that_is_read_with_B_alone(self):
        source = self.package / "__init__.py"
        source.write_text("IS_REVIEWED_SOURCE = True\n")
        alternate = self.root / "alternate.py"
        alternate.write_text("IS_REVIEWED_SOURCE = False\n")
        cache = Path(importlib.util.cache_from_source(str(source)))
        py_compile.compile(str(alternate), cfile=str(cache), doraise=True,
                           invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
        before = cache.read_bytes()
        payload = ("import sys;sys.path.insert(0,sys.argv[1]);import botainer;"
                   "print(botainer.IS_REVIEWED_SOURCE)")
        base = [sys.executable, "-I", "-B"]
        unsafe = subprocess.run([*base, "-c", payload, str(self.root)], capture_output=True,
                                text=True, timeout=10, check=True)
        self.assertEqual(unsafe.stdout.strip(), "False")
        with source_imports():
            safe = subprocess.run([*base, *cache_arguments(), "-c", payload, str(self.root)],
                                  capture_output=True, text=True, timeout=10, check=True)
            self.assertEqual(safe.stdout.strip(), "True")
        self.assertEqual(cache.read_bytes(), before)

    def test_child_flags_require_private_empty_scope(self):
        with patch.object(sys, "pycache_prefix", None):
            with self.assertRaisesRegex(ImportPolicyError, "scope-required"):
                cache_arguments()
        with source_imports():
            (Path(sys.pycache_prefix) / "unexpected").write_text("inert")
            with self.assertRaisesRegex(ImportPolicyError, "private-empty"):
                cache_arguments()

    def test_exec_cleanup_only_removes_empty_prefix_and_survives_failed_exec(self):
        previous = sys.pycache_prefix, sys.dont_write_bytecode
        with self.assertRaisesRegex(OSError, "synthetic exec failure"):
            with source_imports():
                folder = Path(sys.pycache_prefix)
                remove_empty_cache_before_exec()
                self.assertFalse(folder.exists())
                self.assertEqual(sys.pycache_prefix, str(folder))
                self.assertTrue(sys.dont_write_bytecode)
                raise OSError("synthetic exec failure")
        self.assertEqual((sys.pycache_prefix, sys.dont_write_bytecode), previous)

    def test_exec_cleanup_refuses_nonempty_directory_without_deleting_contents(self):
        with source_imports():
            marker = Path(sys.pycache_prefix) / "unexpected"
            marker.write_text("keep")
            with self.assertRaisesRegex(ImportPolicyError, "private-empty"):
                remove_empty_cache_before_exec()
            self.assertEqual(marker.read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
