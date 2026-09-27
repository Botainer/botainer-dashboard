"""Offline tests of exact cluster fixture target guards; no SSH/Slurm calls."""
import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

PATH = Path(__file__).resolve().parents[2] / "tools/cluster_runtime_probe.py"
SPEC = importlib.util.spec_from_file_location("cluster_runtime_probe", PATH)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class ClusterRuntimeProbeTests(unittest.TestCase):
    def preflight_fixture(self, directory):
        root = Path(directory).resolve() / "botainer-dashboard-test-fixture"
        project_uuid = "12345678-1234-1234-1234-123456789abc"
        sid = "0123456789abcdef"
        evidence = root / "preflights" / sid
        evidence.mkdir(parents=True)
        session = root / "state/state" / project_uuid / "sessions" / sid
        session.mkdir(parents=True)
        files = {"spec.json": b"{}", "plan.json": b"{}", "argv.json": b"[]",
                 "unsubmitted.sbatch": b"#SBATCH --job-name=botainer-12345678\nset -euo pipefail\n"}
        for name, data in files.items():
            (evidence / name).write_bytes(data)
        preflight = {"session_id": sid, "project_uuid": project_uuid,
                     "session_directory": str(session),
                     "scheduler": {"partition": "day", "time_minutes": 5, "cpus": 1, "memory_gb": 1, "gpus": 0},
                     "artifacts_sha256": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
        helper = types.SimpleNamespace(
            verify=Mock(), read_json=lambda path: json.loads(path.read_text()),
            sha256=lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest(),
            new_json=Mock(side_effect=AssertionError("no receipt or dispatch claim may be written")),
            new_file=Mock(side_effect=AssertionError("no runtime artifact may be written")))
        return root, evidence, preflight, helper, {"project_uuid": project_uuid}

    def test_parse_scheduler_identity_rejects_missing_job(self):
        with self.assertRaises(probe.Refused):
            probe.parse_job("slurm_load_jobs error: Invalid job id specified")
        fields = probe.parse_job("JobId=123 UserId=fixture(1234) JobName=trial WorkDir=/private/trial SubmitTime=2026-09-21T00:00:00 JobState=RUNNING")
        self.assertEqual(probe.job_identity(fields)["UserId"], "fixture(1234)")
        self.assertNotIn("JobState", probe.job_identity(fields))

    def test_existing_dispatch_claim_refuses_before_verification_or_submission(self):
        root = types.SimpleNamespace()
        class Root:
            def __truediv__(self, name):
                self.name = name
                return self
            def exists(self):
                return self.name == "submission-intent.json"
        helper = types.SimpleNamespace(verify=lambda *args: self.fail("must not verify/redispatch"))
        with self.assertRaisesRegex(probe.Refused, "dispatch already claimed"):
            probe.submit(Root(), helper, {})

    def test_missing_or_extra_preflight_artifacts_refuse_before_dispatch(self):
        for alteration in ("missing-script", "extra-file", "empty-set"):
            with self.subTest(alteration=alteration), tempfile.TemporaryDirectory() as directory:
                root, evidence, preflight, helper, descriptor = self.preflight_fixture(directory)
                if alteration == "missing-script":
                    del preflight["artifacts_sha256"]["unsubmitted.sbatch"]
                elif alteration == "extra-file":
                    preflight["artifacts_sha256"]["unexpected.sh"] = "anything"
                else:
                    preflight["artifacts_sha256"] = {}
                (evidence / "receipt.json").write_text(json.dumps(preflight))
                with patch.object(probe, "tool_paths") as tools, patch.object(probe, "run") as run:
                    with self.assertRaisesRegex(probe.Refused, "artifact set changed"):
                        probe.submit(root, helper, descriptor)
                tools.assert_not_called()
                run.assert_not_called()
                helper.new_json.assert_not_called()
                self.assertFalse((root / "submission-intent.json").exists())

    def test_preflight_project_session_and_evidence_targets_are_pinned(self):
        for alteration in ("wrong-project", "outside-session", "symlink-session", "wrong-evidence-directory"):
            with self.subTest(alteration=alteration), tempfile.TemporaryDirectory() as directory:
                root, evidence, preflight, helper, descriptor = self.preflight_fixture(directory)
                if alteration == "wrong-project":
                    preflight["project_uuid"] = "different-project"
                elif alteration == "outside-session":
                    preflight["session_directory"] = str(root / "another-session")
                elif alteration == "symlink-session":
                    session = Path(preflight["session_directory"])
                    session.rmdir()
                    outside = root / "outside-session"
                    outside.mkdir()
                    session.symlink_to(outside, target_is_directory=True)
                else:
                    renamed = evidence.with_name("fedcba9876543210")
                    evidence.rename(renamed)
                    evidence = renamed
                (evidence / "receipt.json").write_text(json.dumps(preflight))
                with patch.object(probe, "tool_paths") as tools, patch.object(probe, "run") as run:
                    with self.assertRaisesRegex(probe.Refused, "preflight target mismatch"):
                        probe.submit(root, helper, descriptor)
                tools.assert_not_called()
                run.assert_not_called()
                helper.new_json.assert_not_called()
                self.assertFalse((root / "submission-intent.json").exists())

    def test_valid_preflight_reaches_tool_selection_without_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root, evidence, preflight, helper, descriptor = self.preflight_fixture(directory)
            (evidence / "receipt.json").write_text(json.dumps(preflight))
            with patch.object(probe, "tool_paths", side_effect=RuntimeError("offline tool boundary")) as tools:
                with self.assertRaisesRegex(RuntimeError, "offline tool boundary"):
                    probe.submit(root, helper, descriptor)
            tools.assert_called_once_with()
            helper.new_json.assert_not_called()
            self.assertFalse((root / "submission-intent.json").exists())

    def test_identity_change_is_refused_before_action(self):
        root = Path("/private/botainer-dashboard-test-example")
        runtime = {"tools": {"scontrol": "/usr/bin/scontrol"},
                   "tool_sha256": {"scontrol": "digest"}, "job_id": "123", "job_name": "trial"}
        helper = types.SimpleNamespace(sha256=lambda _: "digest")
        cases = [
            "JobId=999 UserId=test(100) JobName=trial WorkDir=/private/botainer-dashboard-test-example/project",
            "JobId=123 UserId=test(999) JobName=trial WorkDir=/private/botainer-dashboard-test-example/project",
            "JobId=123 UserId=test(100) JobName=other WorkDir=/private/botainer-dashboard-test-example/project",
            "JobId=123 UserId=test(100) JobName=trial WorkDir=/other/project",
        ]
        for response in cases:
            with self.subTest(response=response), patch.object(probe.os, "getuid", return_value=100), \
                    patch.object(probe, "env_for", return_value={}), patch.object(probe, "run", return_value=response):
                with self.assertRaisesRegex(probe.Refused, "scheduler target mismatch"):
                    probe.observed_job(root, helper, {}, runtime)


if __name__ == "__main__":
    unittest.main()
