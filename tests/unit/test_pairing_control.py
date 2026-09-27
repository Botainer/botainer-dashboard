"""Private owner pairing mailbox; synthetic secrets, no listener or backend."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from botainer_dashboard.access import AccessDenied
from botainer_dashboard import pairing_control
from botainer_dashboard import pairing_control as control_module
from botainer_dashboard.pairing import MAX_BROWSERS, PairingStore, read_private_json, write_private_json
from botainer_dashboard.pairing_control import PairingControl, PairingControlError, request_pairing_code


class PairingControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.run = self.root / ("a" * 32)
        self.run.mkdir(mode=0o700)
        self.token = "A" * 43
        self.origin = "http://127.0.0.1:50522"
        self.store = PairingStore(origin=self.origin, token=self.token,
                                  state_path=self.root / "auth" / "browsers.json")
        self.addCleanup(self.store.close)
        self.control = PairingControl(self.run, self.store, token=self.token,
            metadata={"url": self.origin, "pid": os.getpid(), "mode": "synthetic"})
        self.addCleanup(self.control.close)
        self.access = self.run / "access.json"

    def descriptor(self):
        return read_private_json(self.access)

    def request(self, **changes):
        now = time.time()
        value = {"version": 1, "runId": self.run.name, "url": self.origin,
                 "requestId": "b" * 32, "action": "pairing-code", "issuedAt": now,
                 "expiresAt": now + 5, **changes}
        write_private_json(self.run / "request.json", value)
        return value

    def ask(self, **kwargs):
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(request_pairing_code, self.access, 1.0, **kwargs)
            until = time.monotonic() + 2
            while not result.done() and time.monotonic() < until:
                self.control.poll()
                time.sleep(0.005)
            answer = result.result(timeout=1)
            # An in-flight owner poll can republish a receipt after the client
            # removes it. Settle the next owner turn before checking cleanup.
            self.control.poll()
            return answer

    def wait_request(self):
        until = time.monotonic() + 1
        while not (self.run / "request.json").exists():
            if time.monotonic() > until:
                self.fail("owner request was not written")
            time.sleep(0.005)
        return read_private_json(self.run / "request.json")

    def test_descriptor_and_usable_token_retained_without_resetting_expiry(self):
        original = self.descriptor()
        self.assertEqual(original["version"], 2)
        self.assertEqual(original["runId"], self.run.name)
        self.assertEqual(original["status"], "ready")
        self.assertEqual(self.access.stat().st_mode & 0o777, 0o600)
        with patch.object(self.store, "issue_token", wraps=self.store.issue_token) as issue:
            result = self.ask()
        issue.assert_not_called()
        self.assertEqual(result, {"url": self.origin, "token": self.token,
                                  "expiresAt": original["expiresAt"]})
        self.assertFalse((self.run / "request.json").exists())
        self.assertFalse((self.run / "response.json").exists())

    def test_selected_service_binding_preserves_existing_browser_and_renews_spent_code(self):
        expected = {key: self.descriptor()[key] for key in ("runId", "url", "pid", "mode")}
        original = dict(expected)
        grant = self.store.pair(self.token)
        self.control.poll()
        with patch.object(self.store, "issue_token", wraps=self.store.issue_token) as issue:
            result = self.ask(expected_service=expected)
        issue.assert_called_once()
        self.assertNotEqual(result["token"], self.token)
        self.assertEqual(result["url"], expected["url"])
        self.assertTrue(self.store.token_available(result["token"]))
        self.assertTrue(self.store.authenticated(grant.cookie, grant.bearer))
        self.assertEqual(expected, original, "selecting a service must not rewrite the caller's identity")
        self.assertFalse((self.run / "request.json").exists())
        self.assertFalse((self.run / "response.json").exists())

    def test_selected_service_mismatch_refuses_before_request_or_token_issuance(self):
        expected = {key: self.descriptor()[key] for key in ("runId", "url", "pid", "mode")}
        grant = self.store.pair(self.token)
        self.control.poll()
        mismatches = ({"runId": "b" * 32}, {"url": "http://127.0.0.1:50523"},
                      {"pid": os.getpid() + 1}, {"mode": "another synthetic service"})
        for changes in mismatches:
            with self.subTest(changes=changes), \
                    patch.object(control_module, "write_private_json") as write, \
                    patch.object(self.store, "issue_token", wraps=self.store.issue_token) as issue:
                with self.assertRaises(PairingControlError):
                    request_pairing_code(self.access, timeout=0.05,
                                         expected_service={**expected, **changes})
                write.assert_not_called()
                issue.assert_not_called()
                self.assertFalse((self.run / "request.json").exists())
                self.assertFalse((self.run / "response.json").exists())
                self.assertTrue(self.store.authenticated(grant.cookie, grant.bearer))

    def test_selected_service_mapping_requires_exact_fields_and_value_types(self):
        expected = {key: self.descriptor()[key] for key in ("runId", "url", "pid", "mode")}
        malformed = (False, 1, "service", [], list(expected.items()), {},
                     {**expected, "unexpected": "field"},
                     {key: value for key, value in expected.items() if key != "mode"},
                     {**expected, "pid": float(expected["pid"])},
                     {**expected, "pid": True}, {**expected, "runId": []},
                     {**expected, "url": []}, {**expected, "mode": []})
        for value in malformed:
            with self.subTest(value=value), \
                    patch.object(control_module, "write_private_json") as write:
                with self.assertRaises(PairingControlError):
                    request_pairing_code(self.access, timeout=0.05, expected_service=value)
                write.assert_not_called()
                self.assertFalse((self.run / "request.json").exists())
                self.assertFalse((self.run / "response.json").exists())

    def test_selected_service_identity_change_while_opening_locks_refuses_request(self):
        descriptor = self.descriptor()
        expected = {key: descriptor[key] for key in ("runId", "url", "pid", "mode")}
        with patch.object(control_module, "_descriptor", side_effect=[
                (self.run, descriptor), (self.run, {**descriptor, "pid": descriptor["pid"] + 1})]), \
                patch.object(control_module, "write_private_json") as write:
            with self.assertRaises(PairingControlError):
                request_pairing_code(self.access, timeout=0.05, expected_service=expected)
            write.assert_not_called()
        self.assertFalse((self.run / "request.json").exists())
        self.assertFalse((self.run / "response.json").exists())

    def test_descriptor_reopens_after_atomic_replacement_unlinks_open_file(self):
        replacement = self.run / "next-access.json"
        expected = {key: value for key, value in self.descriptor().items()
                    if key not in {"token", "expiresAt"}}
        expected["status"] = "unavailable"
        write_private_json(replacement, expected)
        original = self.access.stat()
        original_identity = (original.st_dev, original.st_ino)
        fstat = os.fstat
        unlinked = []

        def replace_before_validation(fd):
            info = fstat(fd)
            if (info.st_dev, info.st_ino) == original_identity:
                os.replace(replacement, self.access)
                info = fstat(fd)
                unlinked.append(info.st_nlink)
            return info

        with patch("botainer_dashboard.pairing.os.fstat", side_effect=replace_before_validation):
            run, value = pairing_control._descriptor(self.access)
        self.assertEqual(unlinked, [0], "the regression must exercise a genuinely unlinked open inode")
        self.assertEqual(run, self.run)
        self.assertEqual(value, expected)
        self.assertNotIn("token", value)

    def test_read_does_not_retry_unchanged_invalid_json(self):
        path = self.run / "invalid.json"
        path.write_text('{"duplicate":1,"duplicate":2}')
        path.chmod(0o600)
        with patch.object(pairing_control, "read_private_json", wraps=read_private_json) as read:
            with self.assertRaisesRegex(ValueError, "duplicate"):
                pairing_control._read(path)
        self.assertEqual(read.call_count, 1)

    def test_descriptor_uses_fd_validation_despite_stale_pre_read_path_stat(self):
        replacement = self.run / "next-access.json"
        expected = {key: value for key, value in self.descriptor().items()
                    if key not in {"token", "expiresAt"}}
        expected["status"] = "unavailable"
        write_private_json(replacement, expected)
        old_fd = os.open(self.access, os.O_RDONLY)
        self.addCleanup(os.close, old_fd)
        lstat = Path.lstat
        unlinked = []

        def replaced_path_stat(path):
            if path == self.access and not unlinked:
                os.replace(replacement, self.access)
                info = os.fstat(old_fd)
                unlinked.append(info.st_nlink)
                return info
            return lstat(path)

        with patch.object(Path, "lstat", replaced_path_stat):
            _, value = pairing_control._descriptor(self.access)
        self.assertEqual(unlinked, [0])
        self.assertEqual(value, expected)

    def test_read_refuses_unsafe_replacement_instead_of_accepting_old_contents(self):
        fstat = os.fstat
        for kind in ("permissions", "hardlink", "symlink"):
            with self.subTest(kind=kind):
                path = self.run / (kind + ".json")
                replacement = self.run / (kind + "-next.json")
                target = self.run / (kind + "-target.json")
                write_private_json(path, {"old": True})
                write_private_json(target, {"new": True})
                if kind == "symlink":
                    replacement.symlink_to(target)
                else:
                    write_private_json(replacement, {"new": True})
                    if kind == "permissions":
                        replacement.chmod(0o644)
                    else:
                        os.link(replacement, self.run / "second-link.json")
                original = path.stat()
                original_identity = (original.st_dev, original.st_ino)
                opened = []

                def replace_before_validation(fd):
                    info = fstat(fd)
                    if (info.st_dev, info.st_ino) == original_identity:
                        os.replace(replacement, path)
                        info = fstat(fd)
                        opened.append(fd)
                    return info

                with patch("botainer_dashboard.pairing.os.fstat", side_effect=replace_before_validation):
                    with self.assertRaises(ValueError):
                        pairing_control._read(path)
                self.assertEqual(len(opened), 1)
                with self.assertRaises(OSError):
                    fstat(opened[0])

    def test_read_bounds_reopens_when_every_open_inode_is_replaced(self):
        path = self.run / "replaced.json"
        write_private_json(path, {"revision": 0})
        replacements = []
        for number in range(1, 5):
            replacement = self.run / f"replacement-{number}.json"
            write_private_json(replacement, {"revision": number})
            replacements.append(replacement)
        fstat = os.fstat
        unlinked = []

        def replace_before_validation(fd):
            info, current = fstat(fd), path.stat()
            if (info.st_dev, info.st_ino) == (current.st_dev, current.st_ino):
                os.replace(replacements[len(unlinked)], path)
                info = fstat(fd)
                unlinked.append(info.st_nlink)
            return info

        with patch("botainer_dashboard.pairing.os.fstat", side_effect=replace_before_validation):
            with self.assertRaises(ValueError):
                pairing_control._read(path)
        self.assertEqual(unlinked, [0, 0, 0])
        self.assertTrue(replacements[-1].exists())

    def test_poll_tolerates_client_cleanup_before_existing_response_is_read(self):
        self.request()
        self.control.poll()
        response = self.run / "response.json"
        read = pairing_control._read
        removed = []

        def cleanup_before_response_read(path):
            if path == response:
                (self.run / "request.json").unlink()
                response.unlink()
                removed.append(True)
            return read(path)

        with patch.object(pairing_control, "_read", side_effect=cleanup_before_response_read), \
                patch.object(self.store, "issue_token", wraps=self.store.issue_token) as issue:
            self.control.poll()
        self.assertEqual(removed, [True])
        issue.assert_not_called()
        self.assertFalse((self.run / "request.json").exists())
        self.assertFalse(response.exists())
        self.control.poll()
        self.assertFalse(response.exists())
        self.assertEqual(self.ask()["token"], self.token)

    def test_poll_tolerates_client_cleanup_during_response_write_validation(self):
        self.request()
        self.control.poll()
        response = self.run / "response.json"
        # Force a receipt refresh, then acknowledge while the atomic writer is
        # validating the file it is about to replace.
        write_private_json(response, {"previous": "receipt"})
        open_file = os.open
        response_opens = []

        def cleanup_before_write_validation(path, flags, *args, **kwargs):
            if Path(path) == response and flags & os.O_ACCMODE == os.O_RDONLY:
                response_opens.append(True)
                if len(response_opens) == 2:
                    (self.run / "request.json").unlink()
                    response.unlink()
            return open_file(path, flags, *args, **kwargs)

        with patch("botainer_dashboard.pairing.os.open", side_effect=cleanup_before_write_validation), \
                patch.object(self.store, "issue_token", wraps=self.store.issue_token) as issue:
            self.control.poll()
        self.assertEqual(len(response_opens), 2)
        issue.assert_not_called()
        self.assertFalse((self.run / "request.json").exists())
        self.assertFalse(response.exists())
        self.assertEqual(self.ask()["token"], self.token)

    def test_consumed_token_is_scrubbed_and_new_code_preserves_existing_browser(self):
        grant = self.store.pair(self.token)
        self.control.poll()
        self.assertEqual(self.descriptor()["status"], "needs-code")
        self.assertNotIn("token", self.descriptor())
        result = self.ask()
        self.assertNotEqual(result["token"], self.token)
        self.assertTrue(self.store.token_available(result["token"]))
        self.assertTrue(self.store.authenticated(grant.cookie, grant.bearer))
        with self.assertRaises(AccessDenied):
            self.store.pair(self.token)

    def test_expired_token_is_scrubbed_without_automatic_renewal(self):
        self.store.clock = lambda: time.time() - 601
        self.store.issue_token(self.token)
        self.store.clock = time.time
        with patch.object(self.store, "issue_token", wraps=self.store.issue_token) as issue:
            self.control.poll()
            self.assertEqual(self.descriptor()["status"], "needs-code")
            self.assertNotIn("token", self.descriptor())
            issue.assert_not_called()
            result = self.ask()
            issue.assert_called_once()
        self.assertNotEqual(result["token"], self.token)

    def test_latest_nonce_is_idempotent_and_cannot_mutate_its_request(self):
        self.store.pair(self.token)
        request = self.request()
        with patch.object(self.store, "issue_token", wraps=self.store.issue_token) as issue:
            self.control.poll()
            reply = read_private_json(self.run / "response.json")
            self.control.poll()
            self.assertEqual(read_private_json(self.run / "response.json"), reply)
            self.store.pair(reply["token"])
            self.control.poll()
            failed = read_private_json(self.run / "response.json")
            self.assertEqual(failed["error"], "pairing-code-no-longer-valid")
            self.assertNotIn("token", failed)
            self.control.poll()
            self.assertEqual(read_private_json(self.run / "response.json"), failed)
            request["expiresAt"] -= 0.1
            write_private_json(self.run / "request.json", request)
            self.control.poll()
            self.assertEqual(read_private_json(self.run / "response.json"), failed)
            issue.assert_called_once()

    def test_cached_ok_expired_token_becomes_error_without_minting_twice(self):
        self.request()
        self.control.poll()
        with patch.object(self.store, "clock", return_value=time.time() + 601), \
                patch.object(self.store, "issue_token", wraps=self.store.issue_token) as issue:
            self.control.poll()
            reply = read_private_json(self.run / "response.json")
            self.assertEqual(reply["error"], "pairing-code-no-longer-valid")
            self.assertNotIn("token", reply)
            issue.assert_not_called()

    def test_spent_cached_reply_tells_cli_to_request_again_then_new_request_succeeds(self):
        ready = threading.Event()
        read = control_module._read
        def hold_client_reply(path):
            if path.name == "response.json" and threading.current_thread() is not threading.main_thread():
                if not ready.wait(1):
                    raise AssertionError("test did not release the client reply")
            return read(path)
        with patch.object(control_module, "_read", side_effect=hold_client_reply), \
                ThreadPoolExecutor(max_workers=1) as executor:
            try:
                result = executor.submit(request_pairing_code, self.access, 1.0)
                self.wait_request()
                with patch.object(self.store, "issue_token", wraps=self.store.issue_token) as issue:
                    self.control.poll()
                    # A browser uses the code after publication, before the CLI
                    # reads its receipt. The next poll must not replay it as OK.
                    self.store.pair(self.token)
                    self.control.poll()
                    ready.set()
                    with self.assertRaisesRegex(PairingControlError, "Request a pairing code again"):
                        result.result(timeout=1)
                    issue.assert_not_called()
            finally:
                ready.set()
        fresh = self.ask()
        self.assertNotEqual(fresh["token"], self.token)
        self.assertTrue(self.store.token_available(fresh["token"]))

    def test_capacity_refusal_does_not_return_a_code(self):
        for _ in range(MAX_BROWSERS):
            self.store.issue_token(self.token)
            self.store.pair(self.token)
        with self.assertRaisesRegex(PairingControlError, "cannot issue"):
            self.ask()
        self.assertEqual(self.descriptor()["status"], "unavailable")
        self.assertNotIn("token", self.descriptor())

    def test_failed_store_cannot_be_reenabled_by_owner_request(self):
        self.store._failed = True
        with self.assertRaisesRegex(PairingControlError, "cannot issue"):
            self.ask()
        self.assertNotIn("token", self.descriptor())
        self.assertFalse(self.store.can_issue_token())

    def test_stopped_service_scrubs_secrets_and_releases_lock(self):
        self.request()
        self.control.poll()
        self.control.close()
        self.assertEqual(self.descriptor()["status"], "stopped")
        self.assertNotIn("token", self.descriptor())
        self.assertFalse((self.run / "request.json").exists())
        self.assertFalse((self.run / "response.json").exists())
        with self.assertRaisesRegex(PairingControlError, "stopped"):
            request_pairing_code(self.access)
        self.control.close()

    def test_stale_descriptor_and_live_pid_are_not_active_owner_proof(self):
        old = self.descriptor()
        self.control.close()
        write_private_json(self.access, old)
        with self.assertRaisesRegex(PairingControlError, "no longer active"):
            request_pairing_code(self.access)

    def test_second_service_cannot_take_existing_owner(self):
        before = self.access.read_bytes()
        with self.assertRaises(BlockingIOError):
            PairingControl(self.run, self.store, token=self.token,
                metadata={"url": self.origin, "pid": os.getpid(), "mode": "synthetic"})
        self.assertEqual(self.access.read_bytes(), before)

    def test_timeout_never_falls_back_to_access_file_token(self):
        with self.assertRaisesRegex(PairingControlError, "did not answer"):
            request_pairing_code(self.access, timeout=0.05)
        self.assertFalse((self.run / "request.json").exists())
        self.assertEqual(self.descriptor()["token"], self.token)

    def test_response_after_client_cleanup_is_removed_on_next_poll(self):
        request = self.request()
        self.control.poll()
        reply = read_private_json(self.run / "response.json")
        (self.run / "request.json").unlink()
        self.control.poll()
        self.assertFalse((self.run / "response.json").exists())
        # Retain idempotence during the original short request window.
        write_private_json(self.run / "request.json", request)
        self.control.poll()
        self.assertEqual(read_private_json(self.run / "response.json"), reply)

    def test_inflight_poll_republishes_after_client_cleanup_then_next_poll_scrubs(self):
        grant = self.store.pair(self.token)
        self.control.poll()
        owner_thread = threading.current_thread()
        allow_client_reply = threading.Event()
        client_finished = threading.Event()
        original_read = control_module._read
        request_reads = 0

        def client():
            try:
                return request_pairing_code(self.access, timeout=2)
            finally:
                client_finished.set()

        def read_with_barrier(path):
            nonlocal request_reads
            if path.name == "response.json" and threading.current_thread() is not owner_thread:
                self.assertTrue(allow_client_reply.wait(2), "owner did not read the request again")
            value = original_read(path)
            if path.name == "request.json" and threading.current_thread() is owner_thread:
                request_reads += 1
                if request_reads == 2:
                    # The owner now holds a valid request snapshot. The client
                    # consumes its first reply and unlinks both mailbox files.
                    allow_client_reply.set()
                    self.assertTrue(client_finished.wait(2), "client did not finish cleanup")
                    self.assertFalse((self.run / "request.json").exists())
                    self.assertFalse((self.run / "response.json").exists())
            return value

        with ThreadPoolExecutor(max_workers=1) as executor, \
                patch.object(control_module, "_read", side_effect=read_with_barrier), \
                patch.object(self.store, "issue_token", wraps=self.store.issue_token) as issue:
            result = executor.submit(client)
            try:
                self.wait_request()
                self.control.poll()
                self.control.poll()
                answer = result.result(timeout=1)
                self.assertEqual(request_reads, 2)
                self.assertFalse((self.run / "request.json").exists())
                self.assertTrue((self.run / "response.json").exists())
                self.assertTrue(self.store.token_available(answer["token"]))
                self.assertTrue(self.store.authenticated(grant.cookie, grant.bearer))
                self.control.poll()
                self.assertFalse((self.run / "request.json").exists())
                self.assertFalse((self.run / "response.json").exists())
                issue.assert_called_once()
            finally:
                allow_client_reply.set()

    def test_abandoned_processed_request_expires_and_scrubs_reply(self):
        request = self.request()
        self.control.poll()
        with patch("botainer_dashboard.pairing_control.time.time", return_value=request["expiresAt"] + 1):
            self.control.poll()
        self.assertFalse((self.run / "request.json").exists())
        self.assertFalse((self.run / "response.json").exists())
        self.assertIsNone(self.control._last_request)
        self.assertIsNone(self.control._last_response)

    def test_late_poll_after_abandoned_request_does_not_mint_code(self):
        self.store.pair(self.token)
        request = self.request(issuedAt=time.time() - 2, expiresAt=time.time() - 1)
        with patch.object(self.store, "issue_token", wraps=self.store.issue_token) as issue:
            self.control.poll()
            issue.assert_not_called()
        self.assertFalse((self.run / "request.json").exists())
        self.assertFalse((self.run / "response.json").exists())

    def test_expiry_cleanup_does_not_remove_different_nonce(self):
        request = self.request()
        self.control.poll()
        other = {"runId": self.run.name, "requestId": "c" * 32, "token": "B" * 43}
        write_private_json(self.run / "response.json", other)
        with patch("botainer_dashboard.pairing_control.time.time", return_value=request["expiresAt"] + 1):
            self.control.poll()
        self.assertEqual(read_private_json(self.run / "response.json"), other)

    def test_second_simultaneous_client_is_refused(self):
        with ThreadPoolExecutor(max_workers=1) as executor:
            first = executor.submit(request_pairing_code, self.access, 0.2)
            self.wait_request()
            with self.assertRaisesRegex(PairingControlError, "Another owner"):
                request_pairing_code(self.access, timeout=0.1)
            with self.assertRaisesRegex(PairingControlError, "did not answer"):
                first.result(timeout=1)

    def test_wrong_response_nonce_is_not_a_result(self):
        write_private_json(self.run / "response.json", {"requestId": "c" * 32, "token": "B" * 43})
        with self.assertRaisesRegex(PairingControlError, "did not answer"):
            request_pairing_code(self.access, timeout=0.05)

    def test_matching_nonce_with_wrong_identity_refuses(self):
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(request_pairing_code, self.access, 1.0)
            request = self.wait_request()
            write_private_json(self.run / "response.json", {
                "version": 1, "runId": "c" * 32, "url": self.origin,
                "requestId": request["requestId"], "status": "ok", "token": "B" * 43,
                "expiresAt": time.time() + 600})
            with self.assertRaisesRegex(PairingControlError, "identity"):
                result.result(timeout=1)

    def test_response_huge_timestamp_and_nonstr_status_are_fixed_errors(self):
        for changes in ({"expiresAt": 10 ** 400}, {"status": []}, {"status": {}}):
            with self.subTest(changes=changes), ThreadPoolExecutor(max_workers=1) as executor:
                result = executor.submit(request_pairing_code, self.access, 1.0)
                request = self.wait_request()
                write_private_json(self.run / "response.json", {
                    "version": 1, "runId": self.run.name, "url": self.origin,
                    "requestId": request["requestId"], "status": "ok", "token": "B" * 43,
                    "expiresAt": time.time() + 600, **changes})
                with self.assertRaisesRegex(PairingControlError, "invalid or expired"):
                    result.result(timeout=1)

    def test_invalid_request_and_late_request_do_not_issue(self):
        self.store.pair(self.token)
        cases = ({"runId": "c" * 32}, {"url": "http://127.0.0.1:55555"},
                 {"action": "run-command"}, {"token": "untrusted"}, {"version": True},
                 {"expiresAt": time.time() - 1}, {"issuedAt": time.time() + 50},
                 {"expiresAt": time.time() + 100}, {"requestId": "../outside"},
                 {"expiresAt": 10 ** 400})
        with patch.object(self.store, "issue_token", wraps=self.store.issue_token) as issue:
            for changes in cases:
                with self.subTest(changes=changes):
                    self.request(**changes)
                    self.control.poll()
                    self.assertFalse((self.run / "response.json").exists())
            issue.assert_not_called()

    def test_corrupt_oversized_and_special_request_files_do_not_issue(self):
        self.store.pair(self.token)
        path = self.run / "request.json"
        with patch.object(self.store, "issue_token", wraps=self.store.issue_token) as issue:
            for content in ('{"bad":', '{"version":1,"version":1}', "x" * 5000):
                path.write_text(content)
                path.chmod(0o600)
                self.control.poll()
            path.unlink()
            os.mkfifo(path, 0o600)
            self.control.poll()
            issue.assert_not_called()
        path.unlink()

    def test_descriptor_permissions_hardlinks_symlinks_and_wrong_schema_refuse(self):
        self.access.chmod(0o644)
        with self.assertRaises(ValueError):
            request_pairing_code(self.access)
        self.access.chmod(0o600)
        other = self.root / "other.json"
        os.link(self.access, other)
        with self.assertRaises(ValueError):
            request_pairing_code(self.access)
        other.unlink()
        original = self.access.read_bytes()
        self.access.unlink()
        other.write_bytes(original)
        other.chmod(0o600)
        self.access.symlink_to(other)
        with self.assertRaises((ValueError, OSError)):
            request_pairing_code(self.access)
        self.access.unlink()
        write_private_json(self.access, {"version": 1, "token": self.token})
        with self.assertRaisesRegex(PairingControlError, "descriptor"):
            request_pairing_code(self.access)
        write_private_json(self.access, json.loads(original))

    def test_descriptor_huge_timestamp_and_nonstr_status_are_fixed_errors(self):
        original = self.descriptor()
        for changes in ({"expiresAt": 10 ** 400}, {"status": []}, {"status": {}}):
            with self.subTest(changes=changes):
                write_private_json(self.access, {**original, **changes})
                with self.assertRaises(PairingControlError):
                    request_pairing_code(self.access)
        write_private_json(self.access, original)

    def test_private_directory_and_ancestry_are_required(self):
        self.run.chmod(0o755)
        with self.assertRaisesRegex(PairingControlError, "owner-only"):
            request_pairing_code(self.access)
        self.run.chmod(0o700)
        link = self.root / "link"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(PairingControlError, "symlinks"):
            request_pairing_code(link / self.run.name / "access.json")

    def test_owner_lock_replacement_is_not_accepted_as_original_service(self):
        lock = self.run / "owner.lock"
        lock.unlink()
        lock.write_text("")
        lock.chmod(0o600)
        with self.assertRaisesRegex(PairingControlError, "no longer active"):
            request_pairing_code(self.access)

    def test_cli_timeout_and_access_file_arguments_are_bounded(self):
        for timeout in (True, 0, -1, 11, float("nan"), float("inf"), 10 ** 400):
            with self.subTest(timeout=timeout), self.assertRaises(PairingControlError):
                request_pairing_code(self.access, timeout=timeout)
        with self.assertRaisesRegex(PairingControlError, "exact access.json"):
            request_pairing_code(self.run / "other.json")


if __name__ == "__main__":
    unittest.main()
