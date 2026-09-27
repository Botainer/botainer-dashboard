"""Acceptance-runner cleanup and terminal display parsing; no network calls."""
from pathlib import Path
import json
import re
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import cluster_acceptance as acceptance
from cluster_terminal_check import TERMINAL_ESCAPES, detach


class ClusterAcceptanceTests(unittest.TestCase):
    def test_editable_profile_change_cannot_redirect_dispatch_or_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory).resolve()
            parent.chmod(0o700)
            original = parent / "operator-profile.json"
            profile = {"ssh_alias": "test-cluster", "remote_python": "/opt/botainer/bin/python3",
                       "trial_root": "/home/test/botainer-dashboard-test-original",
                       "source_root": "/opt/botainer/source", "image": "/images/test.sif"}
            raw = (json.dumps(profile, indent=2) + "\n").encode()
            original.write_bytes(raw)
            original.chmod(0o600)
            calls = []
            replies = iter([{}, {}, {"job_id": "123"}, {"JobState": "RUNNING"}, {}, {"JobState": "CANCELLED"}])

            def step(snapshot_path, action, evidence):
                calls.append((snapshot_path, action))
                self.assertNotEqual(snapshot_path, original)
                self.assertEqual(snapshot_path.read_bytes(), raw)
                selected, _raw, _parent = acceptance.load_profile(snapshot_path)
                self.assertEqual(selected, profile)
                if action == "prepare":
                    original.write_text(json.dumps({**profile, "trial_root": "/home/test/botainer-dashboard-test-changed"}))
                return next(replies)

            with patch.object(acceptance, "step", side_effect=step), \
                    patch.object(acceptance, "exercise", return_value={"same_process": True}) as exercise:
                result, receipt = acceptance.run_trial(original)
            self.assertTrue(result["passed"])
            self.assertTrue(result["termination_confirmed"])
            self.assertEqual([action for _path, action in calls],
                             ["prepare", "preflight", "submit", "status", "stop", "status"])
            self.assertEqual({path for path, _action in calls}, {receipt / "profile.json"})
            self.assertEqual(exercise.call_args.args[0], profile)
            self.assertEqual(stat.S_IMODE((receipt / "profile.json").stat().st_mode), 0o600)
            self.assertNotEqual(original.read_bytes(), raw)

    def test_detach_requires_screen_acknowledgement_and_transport_eof(self):
        class Client:
            def __init__(self):
                self.sent = b""
                self.responses = iter([b"[detached from 123.botainer-456]\r\n", b"channel closed\r\n", b""])

            def write(self, data):
                self.sent += data
                return len(data)

            def read(self, *_args, **_kwargs):
                return next(self.responses)

        client = Client()
        transcript = bytearray()
        detach(client, transcript)
        self.assertEqual(client.sent, b"\x01d")
        self.assertIn(b"channel closed", transcript)

    def test_disconnect_without_detach_ack_is_not_success(self):
        from unittest.mock import Mock
        client = Mock()
        client.write.side_effect = len
        client.read.return_value = b""
        with self.assertRaisesRegex(RuntimeError, "ended before expected"):
            detach(client, bytearray())

    def test_screen_resize_title_sequences_do_not_hide_reported_dimensions(self):
        output = b"SIZE\x1b]2;screen\x07\x1b]2;[screen 0: apptainer]\x07 37 120\r\n"
        self.assertIsNotNone(re.search(rb"SIZE\s+37\s+120", TERMINAL_ESCAPES.sub(b"", output)))

    def test_terminal_failure_still_requests_exact_cleanup_without_resubmitting(self):
        with tempfile.TemporaryDirectory() as directory:
            replies = [{}, {}, {"job_id": "123"}, {"JobState": "RUNNING"}, {}, {"JobState": "CANCELLED"}]
            with patch.object(acceptance, "load_profile", return_value=({}, b"{}", Path(directory))), \
                    patch.object(acceptance, "step", side_effect=replies) as step, \
                    patch.object(acceptance, "exercise", side_effect=RuntimeError("input failed")):
                result, receipt = acceptance.run_trial(Path(directory) / "profile.json")
            self.assertFalse(result["passed"])
            self.assertTrue(result["termination_confirmed"])
            self.assertEqual([call.args[1] for call in step.call_args_list],
                             ["prepare", "preflight", "submit", "status", "stop", "status"])
            self.assertTrue((receipt / "result.json").is_file())

    def test_preparation_failure_never_attempts_submission_or_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(acceptance, "load_profile", return_value=({}, b"{}", Path(directory))), \
                    patch.object(acceptance, "step", side_effect=RuntimeError("prepare refused")) as step:
                result, _receipt = acceptance.run_trial(Path(directory) / "profile.json")
            self.assertFalse(result["passed"])
            self.assertEqual(step.call_count, 1)
            self.assertEqual(step.call_args.args[1], "prepare")
