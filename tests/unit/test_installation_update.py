from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from botainer_dashboard.installation_update import (
    InstallationUpdateError, build_update_request, prepare_installation_update,
)


A, B = "a" * 64, "b" * 64
SOURCE_FILES = ("pyproject.toml", "botainer/__init__.py", "botainer/plugins/lifecycle.py",
                "botainer/plugins/manifest.py", "botainer/cli/main.py", "botainer/cli/plugin.py",
                "botainer/cli/hpc.py", "plugins/hpc-launcher/host_helper/submit.py",
                "plugins/hpc-launcher/host_helper/_common.py")


def remote():
    return {"version": 1, "id": "research", "label": "Research server", "ssh_alias": "research",
            "source_root": "/opt/botainer-source", "remote_python": "/opt/python/bin/python3",
            "remote_python_sha256": A, "state_root": "/home/person/.botainer",
            "control_root": "/home/person/.dashboard-control",
            "launcher": {"path": "/opt/bin/botainer", "sha256": A},
            "project_roots": {"existing-projects": "/work/projects"},
            "source_sha256": {p: A for p in SOURCE_FILES},
            "state_plugin_sha256": {"plugins/selected/hooks/start.py": A}}


def local():
    return {"version": 1, "id": "local", "label": "This computer", "source_root": "/opt/botainer-source",
            "python": "/opt/python/bin/python3", "home": "/home/person",
            "state_root": "/home/person/.botainer",
            "project_roots": [{"id": "existing-projects", "label": "Work", "path": "/work/projects"}],
            "docker": {"executable": "/opt/bin/docker", "host": "unix:///var/run/docker.sock", "daemon_id": "daemon-one"},
            "environment": {"PATH": "/opt/python/bin:/opt/bin:/usr/bin:/bin", "LANG": "C"},
            "terminal_owner": {"path": "/opt/bin/tmux", "sha256": A, "control_root": "/tmp/test-control"},
            "source_hashes": {p: A for p in SOURCE_FILES},
            "support_hashes": {"/opt/python/bin/python3": A, "/opt/bin/docker": A,
                               "/home/person/.botainer/plugins/selected/hooks/start.py": A},
            "approved_hooks": {"/home/person/.botainer/plugins/selected/hooks/start.py": A},
            "directory_identities": {"/home/person": [1, 1], "/home/person/.botainer": [1, 2],
                                     "/opt/botainer-source": [1, 3], "/work/projects": [1, 4]}}


def observed(kind, *, digest=B):
    value = {"ok": True, "source_root": "/opt/botainer-source", "state_root": "/home/person/.botainer",
             "home": "/home/person", "python_sha256": digest, "python_target": "/opt/python/bin/python3",
             "source_hashes": {p: digest for p in SOURCE_FILES},
             "state_hashes": {"plugins/selected/hooks/start.py": A,
                              "plugins/unreviewed/hooks/start.py": B},
             "hook_candidates": {}, "project_roots": ["/work/projects"], "control_exists": True,
             "control_root": "/home/person/.dashboard-control" if kind == "remote" else "/tmp/test-control"}
    if kind == "remote":
        value["launcher"] = {"path": "/opt/bin/botainer", "sha256": digest}
    else:
        value.update(docker_path="/opt/bin/docker", docker_sha256=A,
                     tmux_path="/opt/bin/tmux", tmux_sha256=A,
                     directory_identities=copy.deepcopy(local()["directory_identities"]))
    return value


def runner(observation, calls=None):
    def run(argv, **kwargs):
        if calls is not None:
            calls.append((argv, kwargs))
        value = "daemon-one" if argv[0] == "/opt/bin/docker" else observation
        return {"returncode": 0, "stdout": json.dumps(value).encode(), "stderr": b""}
    return run


class InstallationUpdateTests(unittest.TestCase):
    def setUp(self):
        self.ssh = patch("botainer_dashboard.connection_prepare.shutil.which", return_value="/usr/bin/ssh")
        self.ssh.start(); self.addCleanup(self.ssh.stop)
        def resolve(value):
            if value in {"/opt/bin/docker", "/opt/python/bin/python3"}:
                return value
            return str(Path(value).resolve(strict=True))
        resolver = patch("botainer_dashboard.installation_update._resolve_selector", side_effect=resolve)
        resolver.start(); self.addCleanup(resolver.stop)

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(InstallationUpdateError) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.code, "installation-update-" + code)

    def test_remote_update_inspects_and_preserves_existing_scope_and_keys(self):
        saved = remote(); before = copy.deepcopy(saved); calls = []
        result = prepare_installation_update("remote", saved, {}, runner=runner(observed("remote"), calls))
        candidate = result["profile"]
        self.assertEqual(saved, before)
        self.assertEqual(candidate["project_roots"], saved["project_roots"])
        self.assertEqual(candidate["state_plugin_sha256"], saved["state_plugin_sha256"])
        self.assertEqual(candidate["source_sha256"]["botainer/__init__.py"], B)
        self.assertEqual(len(calls), 1)
        self.assertIn("BatchMode=yes", calls[0][0])
        self.assertIn("StrictHostKeyChecking=yes", calls[0][0])
        self.assertTrue(result["requires_restart"])
        self.assertTrue(result["requires_pairing"])
        self.assertIn("archived", " ".join(result["summary"]))

    def test_local_update_preserves_environment_roots_tools_and_hook_approval(self):
        saved = local(); before = copy.deepcopy(saved); calls = []
        result = prepare_installation_update("local", saved, {}, runner=runner(observed("local"), calls))
        candidate = result["profile"]
        for field in ("project_roots", "environment", "terminal_owner", "docker", "approved_hooks"):
            self.assertEqual(candidate[field], saved[field])
        self.assertEqual(candidate["support_hashes"]["/opt/python/bin/python3"], B)
        self.assertNotIn("/home/person/.botainer/plugins/unreviewed/hooks/start.py", candidate["support_hashes"])
        self.assertEqual(saved, before)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0][0], saved["python"])
        self.assertEqual(calls[1][0][0], saved["docker"]["executable"])

    def test_update_hook_check_describes_retained_approval_instead_of_new_setup(self):
        data = observed("local")
        data["hook_candidates"] = {"/home/person/.botainer/plugins/selected/hooks/start.py": A,
                                   "/home/person/.botainer/plugins/unreviewed/hooks/start.py": B}
        result = prepare_installation_update("local", local(), {}, runner=runner(data))
        hook = next(check for check in result["checks"] if check["name"] == "Startup hooks")
        self.assertEqual(hook["status"], "passed")
        self.assertIn("Previous hook approvals are retained unchanged", hook["message"])
        self.assertIn("Newly discovered hooks are not approved", hook["message"])
        self.assertEqual(result["profile"]["approved_hooks"], local()["approved_hooks"])

    def test_source_summary_distinguishes_added_pins_from_changed_fingerprints(self):
        data = observed("remote", digest=A)
        data["source_hashes"]["botainer/additional.py"] = A
        result = prepare_installation_update("remote", remote(), {}, runner=runner(data))
        self.assertIn("1 added, 0 removed, 0 changed", " ".join(result["summary"]))
        self.assertIn("more complete inspection rather than edited files", " ".join(result["summary"]))

    def test_candidate_digest_is_exact_registry_encoding(self):
        result = prepare_installation_update("remote", remote(), {}, runner=runner(observed("remote")))
        raw = (json.dumps(result["profile"], sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()
        self.assertEqual(result["candidate_sha256"], hashlib.sha256(raw).hexdigest())
        changed = observed("remote", digest=A)
        repeated = prepare_installation_update("remote", remote(), {}, runner=runner(changed))
        self.assertNotEqual(result["candidate_sha256"], repeated["candidate_sha256"])

    def test_reinspection_detects_only_new_installed_plugin_files_without_approving_them(self):
        before = observed("remote"); after = copy.deepcopy(before)
        after["state_hashes"]["plugins/unreviewed/hooks/second.py"] = B
        first = prepare_installation_update("remote", remote(), {}, runner=runner(before))
        second = prepare_installation_update("remote", remote(), {}, runner=runner(after))
        # Unselected plugin code has no authority in either reviewed candidate.
        self.assertEqual(first["candidate_sha256"], second["candidate_sha256"])
        self.assertNotIn("plugins/unreviewed/hooks/second.py", second["profile"]["state_plugin_sha256"])

    def test_saved_documents_do_not_require_the_old_installation_to_exist(self):
        for kind, saved in (("local", local()), ("remote", remote())):
            saved["source_root"] = "/deleted/old-source"
            saved["python" if kind == "local" else "remote_python"] = "/deleted/python"
            request = build_update_request(kind, saved, {"source_root": "/opt/new-source", "python": "/opt/new/python"})
            self.assertEqual(request["source_root"], "/opt/new-source")
            self.assertEqual(request["python"], "/opt/new/python")

    def test_source_relocation_changes_only_source_directory_identity(self):
        data = observed("local"); data["source_root"] = "/opt/new-source"
        del data["directory_identities"]["/opt/botainer-source"]
        data["directory_identities"]["/opt/new-source"] = [1, 99]
        result = prepare_installation_update("local", local(), {"source_root": "/opt/new-source"}, runner=runner(data))
        self.assertNotIn("/opt/botainer-source", result["profile"]["directory_identities"])
        self.assertEqual(result["profile"]["directory_identities"]["/home/person/.botainer"], [1, 2])

    def test_absent_source_identity_is_not_added_by_inspection(self):
        saved = local(); del saved["directory_identities"][saved["source_root"]]
        result = prepare_installation_update("local", saved, {}, runner=runner(observed("local")))
        self.assertEqual(result["profile"]["directory_identities"], saved["directory_identities"])

    def test_absent_optional_directory_and_hook_fields_remain_absent(self):
        saved = local(); del saved["directory_identities"], saved["approved_hooks"]
        result = prepare_installation_update("local", saved, {}, runner=runner(observed("local")))
        self.assertNotIn("directory_identities", result["profile"])
        self.assertNotIn("approved_hooks", result["profile"])

    def test_source_move_keeps_an_identity_shared_with_fixed_scope(self):
        saved = local(); saved["source_root"] = saved["home"]
        del saved["directory_identities"]["/opt/botainer-source"]
        result = prepare_installation_update("local", saved, {"source_root": "/opt/botainer-source"},
                                             runner=runner(observed("local")))
        self.assertEqual(result["profile"]["directory_identities"][saved["home"]], [1, 1])
        self.assertEqual(result["profile"]["directory_identities"]["/opt/botainer-source"], [1, 3])
        data = observed("local"); data["directory_identities"][saved["home"]] = [1, 999]
        self.assert_code("scope-changed", prepare_installation_update, "local", saved,
                         {"source_root": "/opt/botainer-source"}, runner=runner(data))

    def test_rejects_unrelated_overrides_before_inspection(self):
        for key in ("ssh_alias", "state_root", "control_root", "project_roots", "environment", "docker_path", "trusted"):
            for kind, saved in (("local", local()), ("remote", remote())):
                self.assert_code("request-invalid", prepare_installation_update, kind, saved, {key: "/other"},
                                 runner=lambda *a, **k: self.fail("must not inspect"))
        self.assert_code("request-invalid", build_update_request, "local", local(), {"launcher": "/opt/other"})

    def test_rejects_unsafe_path_spellings_before_inspection(self):
        for value in ("", "~/python", "/", "/opt/../bin/python", "/opt/python\nsecret", "//server/python"):
            self.assert_code("request-invalid", prepare_installation_update, "remote", remote(), {"python": value},
                             runner=lambda *a, **k: self.fail("must not inspect"))

    def test_remote_scope_drift_rejected(self):
        for field, changed in (("state_root", "/home/another/.botainer"),
                               ("control_root", "/home/person/.new-control"),
                               ("project_roots", ["/work/other"])):
            data = observed("remote"); data[field] = changed
            self.assert_code("scope-changed", prepare_installation_update, "remote", remote(), {}, runner=runner(data))

    def test_local_directory_replacement_and_runtime_changes_rejected(self):
        for field in ("home", "state_root", "project_roots", "control_root"):
            data = observed("local")
            data[field] = ["/work/other"] if field == "project_roots" else "/other"
            self.assert_code("scope-changed", prepare_installation_update, "local", local(), {}, runner=runner(data))
        data = observed("local"); data["directory_identities"]["/home/person/.botainer"] = [1, 999]
        self.assert_code("scope-changed", prepare_installation_update, "local", local(), {}, runner=runner(data))

    def test_changed_or_missing_selected_plugin_requires_separate_review(self):
        for kind, saved in (("local", local()), ("remote", remote())):
            for changed in (True, False):
                data = observed(kind)
                if changed:
                    data["state_hashes"]["plugins/selected/hooks/start.py"] = B
                else:
                    del data["state_hashes"]["plugins/selected/hooks/start.py"]
                self.assert_code("hooks-review-required", prepare_installation_update, kind, saved, {}, runner=runner(data))

    def test_changed_previously_approved_source_hook_is_not_reapproved(self):
        saved = local(); path = "plugins/selected/hooks/start.py"
        saved["source_hashes"][path] = A
        saved["approved_hooks"]["/opt/botainer-source/" + path] = A
        data = observed("local"); data["source_hashes"][path] = B
        self.assert_code("hooks-review-required", prepare_installation_update, "local", saved, {}, runner=runner(data))

    def test_unknown_extra_local_support_pin_is_not_discarded(self):
        saved = local(); saved["support_hashes"]["/opt/unknown-helper"] = A
        self.assert_code("support-review-required", prepare_installation_update, "local", saved, {}, runner=runner(observed("local")))

    def test_unrecognized_saved_local_scope_is_not_dropped(self):
        saved = local(); saved["new_scope"] = {"path": "/other"}
        self.assert_code("profile-unsupported", prepare_installation_update, "local", saved, {},
                         runner=lambda *a, **k: self.fail("must not inspect"))

    def test_remote_review_explicitly_covers_bundled_plugin_code(self):
        data = observed("remote")
        data["source_hashes"]["plugins/bundled/hooks/start.py"] = B
        result = prepare_installation_update("remote", remote(), {}, runner=runner(data))
        self.assertIn("plugin code bundled", " ".join(result["summary"]))
        self.assertIn("plugins/bundled/hooks/start.py", result["profile"]["source_sha256"])
        self.assertEqual(result["profile"]["state_plugin_sha256"], remote()["state_plugin_sha256"])

    def test_changed_container_or_owner_tools_are_rejected_before_docker_execution(self):
        for field, value in (("docker_path", "/opt/other/docker"), ("docker_sha256", B),
                             ("tmux_path", "/opt/other/tmux"), ("tmux_sha256", B)):
            with self.subTest(field=field):
                data = observed("local"); data[field] = value; calls = []
                self.assert_code("tools-review-required", prepare_installation_update, "local", local(), {},
                                 runner=runner(data, calls))
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0][0][0], local()["python"])

    def test_invalid_saved_tool_pins_refuse_before_any_inspection(self):
        missing = local(); del missing["support_hashes"]["/opt/bin/docker"]
        bad_hash = local(); bad_hash["terminal_owner"]["sha256"] = "invalid"
        bad_path = local(); bad_path["terminal_owner"]["path"] = "relative/tmux"
        for saved in (missing, bad_hash, bad_path):
            self.assert_code("profile-unsupported", prepare_installation_update, "local", saved, {},
                             runner=lambda *a, **k: self.fail("must not inspect"))

    def test_failed_probe_retains_actionable_failure_without_candidate(self):
        failed = {"ok": False, "code": "source-not-discovered"}
        result = prepare_installation_update("remote", remote(), {}, runner=runner(failed))
        self.assertIsNone(result["profile"])
        self.assertNotIn("candidate_sha256", result)
        self.assertEqual(result["qualification"], "failed")

    def test_schema_failures_are_fixed_messages_without_private_content(self):
        for saved in ({}, {**remote(), "ssh_alias": "private;banner"}):
            with self.assertRaises(InstallationUpdateError) as raised:
                build_update_request("remote", saved, {})
            self.assertNotIn("private;banner", str(raised.exception))


class LocalSupportUpdateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="bd-update-", dir="/private/tmp" if Path("/private/tmp").is_dir() else "/tmp")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.tools = self.root / "tools"; self.tools.mkdir()
        self.bin = self.root / "bin"; self.bin.mkdir()
        self.docker = self.file("tools/docker-v1", b"synthetic Docker executable\n")
        self.python = self.file("tools/python-v1", b"synthetic Python executable\n")
        self.docker_selector = self.bin / "docker"; self.docker_selector.symlink_to(self.docker)
        self.python_selector = self.bin / "python"; self.python_selector.symlink_to(self.python)
        self.pth = self.file("startup.pth", b"import never_execute_this_test_module\n")
        self.extra = self.file("environment-input", b"raise RuntimeError('must never execute')\n")
        self.saved = local()
        self.saved["docker"]["executable"] = str(self.docker_selector)
        self.saved["python"] = str(self.python_selector)
        self.saved["environment"]["PATH"] = str(self.bin) + ":/usr/bin:/bin"
        self.saved["support_hashes"] = {
            str(self.docker): self.hash(self.docker), str(self.python): self.hash(self.python),
            str(self.pth): self.hash(self.pth), str(self.extra): self.hash(self.extra),
            "/home/person/.botainer/plugins/selected/hooks/start.py": A}
        self.data = observed("local")
        self.data.update(docker_path=str(self.docker), docker_sha256=self.hash(self.docker),
                         python_target=str(self.python), python_sha256=self.hash(self.python))
        self.calls = []

    def file(self, relative, contents):
        path = self.root / relative; path.write_bytes(contents); path.chmod(0o600)
        return path

    @staticmethod
    def hash(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def runner(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        value = "daemon-one" if argv[0] == str(self.docker) else self.data
        return {"returncode": 0, "stdout": json.dumps(value).encode(), "stderr": b""}

    def error(self, expected, changes=None):
        with self.assertRaises(InstallationUpdateError) as raised:
            prepare_installation_update("local", self.saved, changes or {}, runner=self.runner)
        self.assertEqual(raised.exception.code, "installation-update-" + expected)

    def test_real_selector_symlinks_and_extra_support_are_preserved_without_execution(self):
        candidate = prepare_installation_update("local", self.saved, {}, runner=self.runner)["profile"]
        self.assertEqual(candidate["docker"], self.saved["docker"])
        self.assertEqual(candidate["support_hashes"], self.saved["support_hashes"])
        self.assertEqual(candidate["python"], str(self.python_selector))
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0][0][0], str(self.python_selector))
        self.assertEqual(self.calls[1][0][0], str(self.docker))

    def test_additional_saved_directory_identities_are_preserved(self):
        extra_control = self.root / "control"; extra_control.mkdir(mode=0o700)
        extra_support = self.root / "support"; extra_support.mkdir(mode=0o700)
        for directory in (extra_control, extra_support):
            info = directory.stat()
            self.saved["directory_identities"][str(directory)] = [info.st_dev, info.st_ino]
        self.saved["terminal_owner"]["control_root"] = str(extra_control)
        self.data["control_root"] = str(extra_control)
        candidate = prepare_installation_update("local", self.saved, {}, runner=self.runner)["profile"]
        self.assertEqual(candidate["directory_identities"], self.saved["directory_identities"])

    def test_replaced_saved_directory_identity_is_refused(self):
        directory = self.root / "saved-support"; directory.mkdir()
        info = directory.stat()
        self.saved["directory_identities"][str(directory)] = [info.st_dev, info.st_ino]
        directory.rename(self.root / "old-support"); directory.mkdir()
        self.error("scope-changed")

    def test_missing_saved_directory_identity_is_refused(self):
        directory = self.root / "saved-support"; directory.mkdir()
        info = directory.stat()
        self.saved["directory_identities"][str(directory)] = [info.st_dev, info.st_ino]
        directory.rmdir()
        self.error("scope-changed")

    def test_saved_directory_replaced_by_symlink_is_refused(self):
        directory = self.root / "saved-support"; directory.mkdir()
        info = directory.stat()
        self.saved["directory_identities"][str(directory)] = [info.st_dev, info.st_ino]
        target = self.root / "moved-support"; directory.rename(target); directory.symlink_to(target)
        self.error("scope-changed")

    def test_changed_additional_pin_refuses_before_inspection(self):
        self.extra.write_bytes(b"changed bytes\n")
        self.error("support-review-required")
        self.assertEqual(self.calls, [])

    def test_missing_additional_pin_is_not_dropped(self):
        self.pth.unlink()
        self.error("support-review-required")
        self.assertEqual(self.calls, [])

    def test_writable_additional_input_refuses_before_inspection(self):
        self.extra.chmod(0o666)
        self.error("support-review-required")
        self.assertEqual(self.calls, [])

    def test_additional_input_symlink_refuses_before_inspection(self):
        self.extra.unlink(); self.extra.symlink_to(self.pth)
        self.error("support-review-required")
        self.assertEqual(self.calls, [])

    def test_retargeted_docker_selector_is_not_approved_as_an_update(self):
        replacement = self.file("tools/docker-v2", b"unreviewed Docker\n")
        self.docker_selector.unlink(); self.docker_selector.symlink_to(replacement)
        self.error("profile-unsupported")
        self.assertEqual(self.calls, [])

    def test_missing_python_exact_pin_can_be_replaced_while_extras_remain(self):
        self.saved["python"] = str(self.python)
        self.python.unlink()
        replacement = self.file("tools/python-v2", b"replacement Python\n")
        self.data.update(python_target=str(replacement), python_sha256=self.hash(replacement))
        candidate = prepare_installation_update("local", self.saved, {"python": str(replacement)}, runner=self.runner)["profile"]
        self.assertNotIn(str(self.python), candidate["support_hashes"])
        self.assertEqual(candidate["support_hashes"][str(replacement)], self.hash(replacement))
        self.assertEqual(candidate["support_hashes"][str(self.pth)], self.saved["support_hashes"][str(self.pth)])
        self.assertEqual(candidate["support_hashes"][str(self.extra)], self.saved["support_hashes"][str(self.extra)])

    def test_missing_python_symlink_target_with_extras_refuses_ambiguous_old_pin(self):
        self.python.unlink()
        self.error("python-identity-unavailable", {"python": "/opt/replacement/python"})
        self.assertEqual(self.calls, [])

    def test_missing_python_target_without_extras_has_an_unambiguous_old_pin(self):
        del self.saved["support_hashes"][str(self.pth)], self.saved["support_hashes"][str(self.extra)]
        self.python.unlink()
        replacement = self.file("tools/python-v2", b"replacement Python\n")
        self.data.update(python_target=str(replacement), python_sha256=self.hash(replacement))
        candidate = prepare_installation_update("local", self.saved, {"python": str(replacement)}, runner=self.runner)["profile"]
        self.assertNotIn(str(self.python), candidate["support_hashes"])
        self.assertIn(str(replacement), candidate["support_hashes"])


if __name__ == "__main__":
    unittest.main()
