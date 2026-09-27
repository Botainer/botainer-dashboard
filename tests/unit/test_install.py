"""Offline installer plans and simulated writes; never install or run software."""
from __future__ import annotations

import base64
import copy
import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("dashboard_install_test", ROOT / "tools/install.py")
install = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(install)


def archive_bytes(contents, record=None):
    entries = dict(contents)
    if record:
        stream = io.StringIO()
        writer = csv.writer(stream, lineterminator="\n")
        for name, data in sorted(entries.items()):
            sha = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            writer.writerow((name, "sha256=" + sha, str(len(data))))
        writer.writerow((record, "", ""))
        entries[record] = stream.getvalue().encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.wheelhouse = self.root / "cache"
        self.wheelhouse.mkdir()
        self.destination = self.root / "botainer-dashboard-0.1.0a1"
        self.target = {"implementation": "CPython", "python": "3.13.15", "platform": "macOS arm64",
                       "executable": "synthetic-python", "base_prefix": "synthetic-base"}
        self.bootstrap = {"name": "pip", "version": "26.2.1", "filename": "pip-26.2.1-py3-none-any.whl",
                          "sha256": "a" * 64, "bytes": 10, "source": "synthetic-stdlib/pip.whl",
                          "ensurepip_sha256": "b" * 64, "note": "Synthetic fixture; never executed"}
        self.bootstrap_patch = patch.object(install, "bootstrap_input", return_value=self.bootstrap)
        self.bootstrap_patch.start()
        self.addCleanup(self.bootstrap_patch.stop)
        self.metadata = json.loads((ROOT / install.METADATA_PATH).read_bytes())
        self.direct = self.metadata["direct_requirements"]
        pins = []
        for item in self.metadata["packages"]:
            filename = item["wheel"]["filename"]
            prefix = item["name"].replace("-", "_") + "-" + item["version"] + ".dist-info/"
            data = archive_bytes({prefix + "METADATA": f"Metadata-Version: 2.4\nName: {item['name']}\nVersion: {item['version']}\n\n".encode()})
            (self.wheelhouse / filename).write_bytes(data)
            item["wheel"]["digests"]["sha256"] = install.digest(data)
            item["wheel"]["size"] = len(data)
            pins.append(f"{item['name']}=={item['version']} --hash=sha256:{install.digest(data)}\n")
        inputs = {install.METADATA_PATH: json.dumps(self.metadata).encode(), install.LOCK_PATH: "".join(pins).encode(),
                  "requirements/manifest.json": json.dumps({"python": {"direct_requirements": self.direct}}).encode()}
        (self.root / "requirements").mkdir()
        for name, data in inputs.items():
            (self.root / name).write_bytes(data)
        (self.root / "requirements/SHA256SUMS").write_text("".join(f"{install.digest(data)}  {name}\n" for name, data in inputs.items()))
        self.app_files = {"botainer_dashboard/" + name: b"# Inert synthetic file\n" for name in
                          ("__init__.py", "cli.py", "layout.py", "launcher.py", "_resources/frontend/index.html",
                           "_resources/frontend/app.js", "_resources/docs/getting-started.md",
                           "_resources/tools/run_dashboard.py", "_resources/tools/host_agent_helper.py")}
        self.prefix = "botainer_dashboard-0.1.0a1.dist-info/"
        self.wheel = self.root / "botainer_dashboard-0.1.0a1-py3-none-any.whl"
        self.write_app()

    def write_app(self, *, transform_manifest=None, extra=None, requires_python="<3.14,>=3.13.15"):
        files = dict(self.app_files)
        manifest = {"schema_version": 1, "distribution": "botainer-dashboard", "version": "0.1.0a1",
                    "files": [{"source": name.removeprefix("botainer_dashboard/"),
                               "path": name.removeprefix("botainer_dashboard/"), "sha256": install.digest(data)}
                              for name, data in files.items()]}
        if transform_manifest:
            transform_manifest(manifest)
        files["botainer_dashboard/_resources/PACKAGE-MANIFEST.json"] = json.dumps(manifest).encode()
        files[self.prefix + "METADATA"] = ("Metadata-Version: 2.4\nName: botainer-dashboard\nVersion: 0.1.0a1\n"
                + f"Requires-Python: {requires_python}\n"
                + "".join("Requires-Dist: " + pin + "\n" for pin in self.direct) + "\nSynthetic metadata\n").encode()
        files[self.prefix + "WHEEL"] = b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        files[self.prefix + "entry_points.txt"] = b"[console_scripts]\nbotainer-dashboard = botainer_dashboard.cli:main\n"
        if extra:
            files.update(extra)
        self.wheel.write_bytes(archive_bytes(files, self.prefix + "RECORD"))

    def plan(self, **kwargs):
        with patch.object(install, "target_check", return_value=self.target):
            return install.installation_plan(self.wheel, self.wheelhouse, self.destination, root=self.root, **kwargs)

    def test_plan_reads_exact_selection_without_mutation_or_process(self):
        (self.wheelhouse / "unrelated.whl").write_text("ignored, never inspected or installed")
        before = {path.relative_to(self.root).as_posix(): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        with patch.object(install.subprocess, "run") as run:
            plan, buffered = self.plan()
        run.assert_not_called()
        self.assertEqual(plan["status"], "plan-only")
        self.assertEqual(len(plan["artifacts"]), 15)
        self.assertEqual(len(buffered), 15)
        self.assertEqual(plan["downloads"], [])
        self.assertFalse(self.destination.exists())
        self.assertEqual(before, {path.relative_to(self.root).as_posix(): path.read_bytes() for path in self.root.rglob("*") if path.is_file()})

    def test_current_interpreter_target_is_strict(self):
        for implementation, version, platform, machine, gil in (
            ("cpython", (3, 12), "darwin", "arm64", 0), ("cpython", (3, 13), "linux", "arm64", 0),
            ("cpython", (3, 13), "darwin", "x86_64", 0), ("pypy", (3, 13), "darwin", "arm64", 0),
            ("cpython", (3, 13, 14), "darwin", "arm64", 0),
            ("cpython", (3, 13), "darwin", "arm64", 1)):
            with self.subTest(platform=platform, machine=machine, version=version, gil=gil):
                with patch.object(install.sys, "implementation", types.SimpleNamespace(name=implementation)), \
                        patch.object(install.sys, "version_info", version), patch.object(install.sys, "platform", platform), \
                        patch.object(install.os, "uname", return_value=types.SimpleNamespace(machine=machine)), \
                        patch.object(install.sysconfig, "get_config_var", return_value=gil):
                    with self.assertRaisesRegex(install.Refused, "CPython 3.13"):
                        install.target_check()

    def test_bootstrap_inspection_reads_existing_wheel_and_never_imports_pip(self):
        self.bootstrap_patch.stop()
        directory = self.root / "stdlib/ensurepip"
        (directory / "_bundled").mkdir(parents=True)
        (directory / "__init__.py").write_text("_PIP_VERSION = '26.2.1'\nraise AssertionError('must never import this module')\n")
        payload = archive_bytes({"pip-26.2.1.dist-info/METADATA": b"Metadata-Version: 2.4\nName: pip\nVersion: 26.2.1\n"})
        (directory / "_bundled/pip-26.2.1-py3-none-any.whl").write_bytes(payload)
        with patch.object(install.sysconfig, "get_path", return_value=str(self.root / "stdlib")), \
                patch.object(install.sysconfig, "get_config_var", return_value=""), patch.object(install.subprocess, "run") as run:
            result = install.bootstrap_input()
        self.assertEqual(result["version"], "26.2.1")
        self.assertEqual(result["sha256"], install.digest(payload))
        run.assert_not_called()
        with patch.object(install.sysconfig, "get_config_var", return_value="/synthetic/system/wheels"), \
                self.assertRaisesRegex(install.Refused, "external system wheel"):
            install.bootstrap_input()

    def test_existing_destination_and_unversioned_name_refused(self):
        self.destination.mkdir()
        sentinel = self.destination / "keep.txt"
        sentinel.write_text("existing environment")
        with self.assertRaisesRegex(install.Refused, "already exists"):
            self.plan()
        self.assertEqual(sentinel.read_text(), "existing environment")
        with self.assertRaisesRegex(install.Refused, "versioned"):
            install.new_destination(self.root / "unversioned")

    def test_group_writable_destination_parent_refused(self):
        self.root.chmod(0o770)
        try:
            with self.assertRaisesRegex(install.Refused, "writable by other"):
                install.new_destination(self.destination)
        finally:
            self.root.chmod(0o700)

    def test_dotdot_cannot_place_install_inside_running_environment(self):
        active = self.root / "active"
        other = self.root / "other"
        active.mkdir(mode=0o700)
        other.mkdir(mode=0o700)
        with patch.object(install.sys, "prefix", str(active)):
            for path in (active / "botainer-dashboard-0.1.0a1", other / "../active/botainer-dashboard-0.1.0a1"):
                with self.subTest(path=path), self.assertRaisesRegex(install.Refused, "inside the running"):
                    install.new_destination(path)
        self.assertEqual(install.new_destination(other / "../botainer-dashboard-0.1.0a1"), self.destination)
        self.assertFalse(self.destination.exists())

    def test_symlink_inputs_and_destination_parent_refused(self):
        self.wheel.unlink()
        self.wheel.symlink_to(self.wheelhouse / self.metadata["packages"][0]["wheel"]["filename"])
        with self.assertRaisesRegex(install.Refused, "Symbolic links"):
            self.plan()
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(install.Refused, "canonical"):
            install.new_destination(alias / "other-0.1.0a1")

    def test_missing_and_changed_cached_dependency_fail_before_writes(self):
        path = self.wheelhouse / self.metadata["packages"][0]["wheel"]["filename"]
        original = path.read_bytes()
        path.unlink()
        with self.assertRaises(OSError):
            self.plan()
        self.assertFalse(self.destination.exists())
        path.write_bytes(original + b"modified")
        with self.assertRaisesRegex(install.Refused, "Cached wheel differs"):
            self.plan()
        self.assertFalse(self.destination.exists())

    def test_stale_dashboard_hash_and_changed_dependency_inputs_refused(self):
        with self.assertRaisesRegex(install.Refused, "explicitly selected SHA"):
            self.plan(expected_sha256="0" * 64)
        (self.root / install.LOCK_PATH).write_text("unreviewed change")
        with self.assertRaisesRegex(install.Refused, "dependency input changed"):
            self.plan()
        self.assertFalse(self.destination.exists())

    def test_dashboard_metadata_and_resource_inventory_refused(self):
        self.write_app(requires_python=">=3.9")
        with self.assertRaisesRegex(install.Refused, "metadata differs"):
            self.plan()
        self.write_app(transform_manifest=lambda value: value["files"].pop())
        with self.assertRaisesRegex(install.Refused, "complete application resources"):
            self.plan()
        self.write_app(extra={"botainer_dashboard/private/secret.txt": b"synthetic private"})
        with self.assertRaisesRegex(install.Refused, "Unsafe wheel"):
            self.plan()

    def test_main_apply_requires_explicit_wheel_hash_before_planning(self):
        args = ["--wheel", str(self.wheel), "--wheelhouse", str(self.wheelhouse), "--destination", str(self.destination), "--apply"]
        with patch.object(install, "installation_plan") as plan, patch.object(install, "apply_plan") as apply, \
                patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit) as error:
            install.main(args)
        self.assertEqual(error.exception.code, 2)
        plan.assert_not_called()
        apply.assert_not_called()
        self.assertFalse(self.destination.exists())

    def test_simulated_apply_writes_only_new_environment_and_uses_offline_flags(self):
        plan, buffered = self.plan()
        calls = []
        def no_execution(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0)
        with patch.object(install.subprocess, "run", side_effect=no_execution):
            command = install.apply_plan(plan, buffered)
        self.assertEqual(command, self.destination / "bin/botainer-dashboard")
        self.assertEqual(len(calls), 2)
        bootstrap, pip = [value[0] for value in calls]
        self.assertEqual(bootstrap[1:5], ["-I", "-B", "-m", "venv"])
        for flag in ("--no-index", "--no-deps", "--require-hashes", "--only-binary=:all:", "--no-cache-dir", "--no-compile"):
            self.assertIn(flag, pip)
        self.assertNotIn("--upgrade", pip)
        self.assertEqual(calls[1][1]["cwd"], self.destination)
        self.assertEqual(calls[1][1]["env"]["PIP_CONFIG_FILE"], install.os.devnull)
        receipt = json.loads((self.destination / ".dashboard-install/receipt.json").read_text())
        self.assertEqual(receipt["status"], "installed-not-started")
        lock = (self.destination / ".dashboard-install/requirements.txt").read_text()
        self.assertEqual(len(lock.splitlines()), 15)
        self.assertEqual(lock.count("--hash=sha256:"), 15)
        self.assertEqual(set(path.name for path in (self.destination / ".dashboard-install/wheels").iterdir()), set(buffered))
        self.assertFalse((self.destination / "bin").exists(), "mocked test must not actually create/install an environment")

    def test_apply_rechecks_destination_and_buffer_before_mutation(self):
        plan, buffered = self.plan()
        changed = dict(buffered)
        changed[self.wheel.name] = b"changed"
        with patch.object(install.subprocess, "run") as run, self.assertRaisesRegex(install.Refused, "Buffered wheel"):
            install.apply_plan(plan, changed)
        run.assert_not_called()
        self.assertFalse(self.destination.exists())
        self.destination.mkdir()
        with patch.object(install.subprocess, "run") as run, self.assertRaisesRegex(install.Refused, "already exists"):
            install.apply_plan(plan, buffered)
        run.assert_not_called()

    def test_changed_bootstrap_refused_before_any_apply_write(self):
        plan, buffered = self.plan()
        with patch.object(install, "bootstrap_input", return_value=self.bootstrap | {"sha256": "c" * 64}), \
                patch.object(install.subprocess, "run") as run, self.assertRaisesRegex(install.Refused, "bundled pip changed"):
            install.apply_plan(plan, buffered)
        run.assert_not_called()
        self.assertFalse(self.destination.exists())

    def test_simulated_failed_install_retains_labeled_partial_directory(self):
        plan, buffered = self.plan()
        with patch.object(install.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "synthetic")), \
                self.assertRaisesRegex(install.Refused, "Only the new directory"):
            install.apply_plan(plan, buffered)
        receipt = json.loads((self.destination / ".dashboard-install/receipt.json").read_text())
        self.assertEqual(receipt["status"], "failed-partial-environment-retained")
        self.assertTrue(self.wheel.exists())


if __name__ == "__main__":
    unittest.main()
