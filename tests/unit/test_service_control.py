"""Synthetic lifecycle mailbox tests; no listener, signals, or runtime backend."""
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from botainer_dashboard import service_control as module
from botainer_dashboard.pairing import read_private_json, write_private_json
from botainer_dashboard.service_control import (
    ServiceControl, ServiceControlError, discover_services, inspect_service, request_service_action, wait_for_stopped,
)


class ServiceControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.run = self.root / ("a" * 32)
        self.run.mkdir(mode=0o700)
        self.ready = False
        self.shutdown = Mock()
        self.control = ServiceControl(self.run, metadata={"url": "http://127.0.0.1:50522", "pid": os.getpid(), "mode": "synthetic"},
                                      is_ready=lambda: self.ready, request_shutdown=self.shutdown)
        self.addCleanup(self.control.close)
        self.path = self.control.descriptor_path

    def descriptor(self):
        return read_private_json(self.path)

    def request(self, **changes):
        now = time.time()
        row = self.descriptor()
        value = {"version": 1, "runId": row["runId"], "url": row["url"], "ownerIdentity": row["ownerIdentity"],
                 "requestId": "b" * 32, "action": "status", "issuedAt": now, "expiresAt": now + 1, **changes}
        write_private_json(self.run / "service-request.json", value)
        return value

    def ask(self, action="status"):
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(request_service_action, self.path, action, 1.0)
            deadline = time.monotonic() + 2
            while not result.done() and time.monotonic() < deadline:
                self.control.poll()
                time.sleep(0.002)
            return result.result(timeout=1)

    def wait_request(self):
        path = self.run / "service-request.json"
        deadline = time.monotonic() + 1
        while not path.exists():
            if time.monotonic() >= deadline:
                self.fail("client did not publish request")
            time.sleep(0.002)
        return read_private_json(path)

    def test_starting_running_and_private_descriptor_without_pairing_secrets(self):
        self.assertEqual(self.descriptor()["status"], "starting")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("token", self.descriptor())
        self.assertEqual(self.ask()["status"], "starting")
        self.ready = True
        result = self.ask()
        self.assertEqual(result["status"], "running")
        self.assertTrue(result["responsive"])
        self.assertFalse((self.run / "service-request.json").exists())
        self.assertFalse((self.run / "service-response.json").exists())
        self.shutdown.assert_not_called()

    def test_stop_ack_precedes_callback_and_never_means_owner_exited(self):
        def shutdown():
            self.assertEqual(read_private_json(self.run / "service-response.json")["status"], "stopping")
            self.assertFalse(wait_for_stopped(self.path, 0.02))
        self.shutdown.side_effect = shutdown
        reply = self.ask("stop")
        self.assertEqual(reply["status"], "stopping")
        self.assertTrue(reply["responsive"])
        self.shutdown.assert_called_once_with()
        self.assertFalse(wait_for_stopped(self.path, 0.02))
        self.control.close()
        self.assertTrue(wait_for_stopped(self.path, 0.02))

    def test_stop_callback_runs_once_across_same_and_new_nonce(self):
        original = self.request(action="stop")
        self.control.poll()
        self.control.poll()
        self.request(action="stop", requestId="c" * 32)
        self.control.poll()
        self.shutdown.assert_called_once_with()
        write_private_json(self.run / "service-request.json", {**original, "action": "status"})
        self.control.poll()
        self.shutdown.assert_called_once_with()

    def test_mutating_last_nonce_is_not_a_new_action(self):
        request = self.request()
        self.control.poll()
        write_private_json(self.run / "service-request.json", {**request, "action": "stop"})
        self.control.poll()
        self.shutdown.assert_not_called()
        self.assertEqual(self.descriptor()["status"], "starting")

    def test_poll_failure_retains_owner_and_cannot_report_stopped(self):
        self.control.failed()
        self.assertEqual(self.descriptor()["status"], "control-unavailable")
        self.assertFalse(wait_for_stopped(self.path, 0.02))
        with self.assertRaisesRegex(ServiceControlError, "still owned"):
            request_service_action(self.path, "stop", 0.02)
        self.assertEqual(discover_services(self.root)[0]["status"], "control-unavailable")
        self.shutdown.assert_not_called()

    def test_failed_descriptor_write_still_keeps_lifetime_lock(self):
        with patch.object(module, "write_private_json", side_effect=OSError("synthetic error")):
            self.control.failed()
        self.assertFalse(wait_for_stopped(self.path, 0.02))
        self.assertEqual(discover_services(self.root, timeout=0.02)[0]["status"], "unresponsive")

    def test_callback_failure_fails_control_without_releasing_or_retrying(self):
        self.shutdown.side_effect = RuntimeError("synthetic callback failure")
        self.request(action="stop")
        with self.assertRaisesRegex(ServiceControlError, "callback failed"):
            self.control.poll()
        self.assertFalse(wait_for_stopped(self.path, 0.02))
        self.assertEqual(self.descriptor()["status"], "control-unavailable")
        self.control.poll()
        self.shutdown.assert_called_once_with()

    def test_stopped_status_uses_lock_release_even_if_descriptor_publish_failed(self):
        with patch.object(module, "write_private_json", side_effect=OSError("synthetic")):
            self.control.close()
        self.assertEqual(self.descriptor()["status"], "starting")
        reply = request_service_action(self.path, "status", 0.02)
        self.assertEqual(reply["status"], "stopped")
        self.assertFalse(reply["responsive"])
        self.assertTrue(wait_for_stopped(self.path, 0.02))

    def test_reply_remains_readable_when_shutdown_closes_owner_immediately(self):
        self.shutdown.side_effect = self.control.close
        reply = self.ask("stop")
        self.assertEqual(reply["status"], "stopping")
        self.assertTrue(wait_for_stopped(self.path, 0.02))

    def test_expired_invalid_and_unrecognized_actions_never_dispatch(self):
        now = time.time()
        for changes in ({"action": "kill"}, {"action": []}, {"pid": 123}, {"version": True},
                        {"ownerIdentity": [1, 2]}, {"ownerIdentity": [False, 1]},
                        {"requestId": "bad"}, {"runId": "d" * 32}, {"url": "http://localhost:50522"},
                        {"issuedAt": now - 2, "expiresAt": now - 1}, {"issuedAt": now + 10},
                        {"expiresAt": now + 100}, {"expiresAt": 10 ** 500}, {"expiresAt": True}):
            with self.subTest(changes=changes):
                self.request(**{"action": "stop", **changes})
                self.control.poll()
                self.shutdown.assert_not_called()

    def test_invalid_request_files_and_duplicate_json_never_dispatch(self):
        request_path = self.run / "service-request.json"
        for content in (b'{"version":1,"version":1}', b'[]', b'x' * 4097, b'\xff'):
            request_path.write_bytes(content)
            request_path.chmod(0o600)
            self.control.poll()
        request_path.unlink()
        request_path.symlink_to(self.path)
        self.control.poll()
        request_path.unlink()
        os.mkfifo(request_path, 0o600)
        self.control.poll()
        self.shutdown.assert_not_called()

    def test_deep_json_request_descriptor_and_reply_fail_with_fixed_errors(self):
        raw = b'{"nested":' + b'[' * 1200 + b'0' + b']' * 1200 + b'}'
        self.assertLess(len(raw), module._LIMIT)
        request_path = self.run / "service-request.json"
        request_path.write_bytes(raw)
        request_path.chmod(0o600)
        self.control.poll()
        self.shutdown.assert_not_called()
        request_path.unlink()
        original = self.path.read_bytes()
        self.path.write_bytes(raw)
        try:
            with self.assertRaisesRegex(ServiceControlError, "Invalid service"):
                request_service_action(self.path, "status", 0.02)
            self.assertEqual(inspect_service(self.path, 0.02)["status"], "unknown")
        finally:
            self.path.write_bytes(original)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(request_service_action, self.path, "status", 0.3)
            request = self.wait_request()
            response = self.run / "service-response.json"
            response.write_bytes(b'{"requestId":"' + request["requestId"].encode() + b'","nested":' + b'[' * 1200 + b'0' + b']' * 1200 + b'}')
            response.chmod(0o600)
            with self.assertRaisesRegex(ServiceControlError, "Invalid service control file|does not match"):
                future.result(timeout=1)
        self.assertFalse(wait_for_stopped(self.path, 0.02))

    def test_decoder_recursion_is_normalized_without_retrying_stable_file(self):
        with patch.object(module, "read_private_json", side_effect=RecursionError("synthetic decoder limit")) as reader:
            with self.assertRaisesRegex(ServiceControlError, "Invalid service control file"):
                module._read(self.path)
        reader.assert_called_once()

    def test_server_expiry_cleanup_yields_to_active_client(self):
        self.request()
        self.control.poll()
        self.control._last_request["expiresAt"] = time.time() - 1
        fd = os.open(self.run / "service-client.lock", os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(module, "_remove_matching") as cleanup:
                self.control.poll()
            cleanup.assert_not_called()
        finally:
            os.close(fd)

    def test_server_cleanup_holds_client_lock_through_nonce_check_and_removal(self):
        request = self.request()
        self.control.poll()
        original = module._remove_matching
        checked = []
        def remove(path, run_id, request_id):
            fd = os.open(self.run / "service-client.lock", os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(fd)
            checked.append(Path(path).name)
            return original(path, run_id, request_id)
        with patch.object(module, "_remove_matching", side_effect=remove):
            self.control._cleanup(request["requestId"])
        self.assertEqual(checked, ["service-request.json", "service-response.json"])
        self.assertFalse((self.run / "service-request.json").exists())

    def test_timeout_does_not_dispatch_late_request_or_signal_pid(self):
        with patch("os.kill", side_effect=AssertionError("must never signal")), self.assertRaisesRegex(ServiceControlError, "unconfirmed"):
            request_service_action(self.path, "stop", 0.02)
        self.control.poll()
        self.shutdown.assert_not_called()
        self.assertFalse((self.run / "service-request.json").exists())

    def test_owner_lock_replacement_is_unknown_not_stopped(self):
        lock = self.run / "service-owner.lock"
        lock.rename(self.run / "original-owner.lock")
        lock.write_text("")
        lock.chmod(0o600)
        with self.assertRaisesRegex(ServiceControlError, "ownership changed"):
            wait_for_stopped(self.path, 0.02)
        with self.assertRaisesRegex(ServiceControlError, "ownership changed"):
            request_service_action(self.path, "stop", 0.02)
        self.assertEqual(discover_services(self.root)[0]["status"], "unknown")
        self.shutdown.assert_not_called()

    def test_live_pid_and_stale_descriptor_do_not_prove_owner_liveness(self):
        self.control.close()
        value = self.descriptor()
        value.update(status="running", pid=os.getpid())
        write_private_json(self.path, value)
        self.assertEqual(discover_services(self.root)[0]["status"], "stopped")

    def test_second_owner_or_reused_run_directory_refused(self):
        kwargs = {"metadata": {"url": "http://127.0.0.1:50522", "pid": os.getpid(), "mode": "synthetic"},
                  "is_ready": lambda: True, "request_shutdown": Mock()}
        with self.assertRaises(BlockingIOError):
            ServiceControl(self.run, **kwargs)
        self.control.close()
        with self.assertRaisesRegex(ServiceControlError, "never be reused"):
            ServiceControl(self.run, **kwargs)

    def test_client_lock_refuses_concurrent_request(self):
        fd = os.open(self.run / "service-client.lock", os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ServiceControlError, "Another service-control"):
                request_service_action(self.path, "stop", 0.02)
        finally:
            os.close(fd)
        self.shutdown.assert_not_called()

    def test_wrong_response_identity_and_schema_refused(self):
        for changed in ({"runId": "f" * 32}, {"version": True}, {"status": []}, {"action": "stop"}):
            with self.subTest(changed=changed), ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(request_service_action, self.path, "status", 0.3)
                request = self.wait_request()
                reply = {key: request[key] for key in module._RESPONSE - {"status"}}
                reply.update({"status": "running", **changed})
                write_private_json(self.run / "service-response.json", reply)
                with self.assertRaisesRegex(ServiceControlError, "does not match"):
                    future.result(timeout=1)

    def test_wrong_nonce_never_counts_as_a_reply(self):
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(request_service_action, self.path, "status", 0.1)
            request = self.wait_request()
            reply = {key: request[key] for key in module._RESPONSE - {"status"}}
            reply.update(status="running", requestId="f" * 32)
            write_private_json(self.run / "service-response.json", reply)
            with self.assertRaisesRegex(ServiceControlError, "unconfirmed"):
                future.result(timeout=1)
            self.assertEqual(read_private_json(self.run / "service-response.json")["requestId"], "f" * 32)

    def test_client_lock_replacement_refuses_outstanding_request(self):
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(request_service_action, self.path, "status", 0.3)
            self.wait_request()
            path = self.run / "service-client.lock"
            path.rename(self.run / "previous-client.lock")
            path.write_text("")
            path.chmod(0o600)
            with self.assertRaisesRegex(ServiceControlError, "ownership changed"):
                future.result(timeout=1)

    def test_atomic_replacement_is_reopened_but_repeated_changes_are_bounded(self):
        original = module.read_private_json
        count = 0
        descriptor = self.descriptor()
        def once(path, **kwargs):
            nonlocal count
            count += 1
            if count == 1:
                write_private_json(path, descriptor)
            return original(path, **kwargs)
        with patch.object(module, "read_private_json", side_effect=once):
            self.assertEqual(module._read(self.path), descriptor)
        self.assertEqual(count, 2)
        def always(path, **kwargs):
            write_private_json(path, descriptor)
            return original(path, **kwargs)
        with patch.object(module, "read_private_json", side_effect=always) as reader:
            with self.assertRaises(ServiceControlError):
                module._read(self.path)
        self.assertEqual(reader.call_count, 3)

    def test_shutdown_refused_when_acknowledgement_cannot_be_published(self):
        self.request(action="stop")
        original = module.write_private_json
        def write(path, value):
            if Path(path).name == "service-response.json":
                raise FileNotFoundError("synthetic publication race")
            return original(path, value)
        with patch.object(module, "write_private_json", side_effect=write):
            with self.assertRaisesRegex(ServiceControlError, "publication is unconfirmed"):
                self.control.poll()
        self.shutdown.assert_not_called()
        self.assertEqual(self.descriptor()["status"], "control-unavailable")
        self.assertFalse(wait_for_stopped(self.path, 0.02))

    def test_replaced_owner_after_acknowledgement_never_calls_shutdown(self):
        self.request(action="stop")
        original = module.write_private_json
        def write(path, value):
            result = original(path, value)
            if Path(path).name == "service-response.json":
                lock = self.run / "service-owner.lock"
                lock.rename(self.run / "previous-owner.lock")
                lock.write_text("")
                lock.chmod(0o600)
            return result
        with patch.object(module, "write_private_json", side_effect=write):
            with self.assertRaisesRegex(ServiceControlError, "ownership changed"):
                self.control.poll()
        self.shutdown.assert_not_called()

    def test_descriptor_permissions_links_and_schema_refused(self):
        original = self.path.read_bytes()
        self.path.chmod(0o644)
        with self.assertRaises(ServiceControlError):
            request_service_action(self.path, "status", 0.02)
        self.path.chmod(0o600)
        alias = self.run / "alias.json"
        alias.hardlink_to(self.path)
        with self.assertRaises(ServiceControlError):
            wait_for_stopped(self.path, 0.02)
        alias.unlink()
        value = self.descriptor()
        for changed in ({"status": []}, {"version": True}, {"ownerIdentity": [False, 1]}, {"mode": "bad\nmode"}):
            write_private_json(self.path, {**value, **changed})
            with self.assertRaises(ServiceControlError):
                wait_for_stopped(self.path, 0.02)
        self.path.write_bytes(original)

    def test_discovery_reports_legacy_without_reading_access_files(self):
        legacy = self.root / ("c" * 32)
        legacy.mkdir(mode=0o700)
        (legacy / "access.json").write_text("not a service descriptor and never read")
        self.control.close()
        original = module.read_private_json
        def read(path, **kwargs):
            self.assertNotEqual(Path(path).name, "access.json")
            return original(path, **kwargs)
        with patch.object(module, "read_private_json", side_effect=read):
            rows = discover_services(self.root)
        self.assertEqual([row["status"] for row in rows], ["stopped", "unmanaged"])
        self.assertNotIn("pid", rows[1])

    def test_exact_inspection_bypasses_history_and_preserves_uncertainty(self):
        with patch.object(module.os, "scandir", side_effect=AssertionError("must not scan history")):
            self.assertEqual(inspect_service(self.path, 0.02)["status"], "unresponsive")
            self.control.failed()
            self.assertEqual(inspect_service(self.path)["status"], "control-unavailable")
            self.control.close()
            self.assertEqual(inspect_service(self.path)["status"], "stopped")
            legacy = self.root / ("d" * 32)
            legacy.mkdir(mode=0o700)
            self.assertEqual(inspect_service(legacy / "service.json")["status"], "unmanaged")
            self.assertEqual(inspect_service(self.root / ("e" * 32) / "service.json")["status"], "unknown")
            self.path.chmod(0o644)
            self.assertEqual(inspect_service(self.path)["status"], "unknown")
            self.path.chmod(0o600)

    def test_discovery_responding_and_stalled_owners_are_distinct(self):
        self.ready = True
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(discover_services, self.root, timeout=0.5)
            deadline = time.monotonic() + 1
            while not future.done() and time.monotonic() < deadline:
                self.control.poll()
                time.sleep(0.002)
            self.assertEqual(future.result(timeout=1)[0]["status"], "running")
        self.assertEqual(discover_services(self.root, timeout=0.02)[0]["status"], "unresponsive")

    def test_discovery_count_and_total_probe_budget_are_bounded(self):
        with patch.object(module, "_MAX_RUNS", 0), self.assertRaisesRegex(ServiceControlError, "Too many"):
            discover_services(self.root)
        began = time.monotonic()
        discover_services(self.root, timeout=0.025)
        self.assertLess(time.monotonic() - began, 0.5)
        self.assertEqual(discover_services(self.root / "missing"), [])

    def test_timeout_action_directory_and_descriptor_path_validation(self):
        for value in (0, -1, 11, True, float("inf"), 10 ** 500, "2"):
            with self.assertRaises(ServiceControlError):
                request_service_action(self.path, "status", value)
            with self.assertRaises(ServiceControlError):
                wait_for_stopped(self.path, value)
        with self.assertRaises(ServiceControlError):
            request_service_action(self.path, "restart", 0.02)
        with self.assertRaisesRegex(ServiceControlError, "exact service.json"):
            request_service_action(self.run / "access.json", "status", 0.02)
        self.run.chmod(0o755)
        with self.assertRaises(ServiceControlError):
            wait_for_stopped(self.path, 0.02)
        self.run.chmod(0o700)


if __name__ == "__main__":
    unittest.main()
