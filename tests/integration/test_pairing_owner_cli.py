"""Real loopback pairing-mailbox tests with a non-executing recording backend.

Only a temporary private run directory and this test's HTTP listener are used.
There are no Botainer, container, agent, SSH, or terminal subprocesses.
"""

import contextlib
import http.client
from http.cookies import SimpleCookie
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import secrets
import socket
import sys
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

import uvicorn

from botainer_dashboard.access import LoopbackAccess
from botainer_dashboard.pairing import PairingStore
from botainer_dashboard.pairing_control import PairingControl, PairingControlError, request_pairing_code
from botainer_dashboard.service import COOKIE_NAME, create_app
from botainer_dashboard.service_control import ServiceControl


class RecordingBackend:
    def __init__(self):
        self.calls = []
        self.snapshots = 0

    def snapshot(self):
        self.snapshots += 1
        return {"mode": "pairing-test-fixture", "projects": [], "sessions": []}

    def start_session(self, *args, **kwargs):
        self.calls.append("start")
        raise AssertionError("pairing must not start a session")

    def stop_session(self, *args, **kwargs):
        self.calls.append("stop")
        raise AssertionError("pairing must not stop a session")

    def attach(self, *args, **kwargs):
        self.calls.append("attach")
        raise AssertionError("pairing must not attach a terminal")


@unittest.skipUnless(os.name == "posix", "owner mailbox uses POSIX file locks")
class PairingOwnerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.expected_control_failure = False
        self.temp = TemporaryDirectory(prefix="dashboard-pairing-owner-")
        self.addCleanup(self.temp.cleanup)
        self.cwd = Path(self.temp.name).resolve()
        (self.cwd / ".local").mkdir(mode=0o700)
        run_root = self.cwd / ".local/run"
        run_root.mkdir(mode=0o700)
        self.run_dir = run_root / secrets.token_hex(16)
        self.run_dir.mkdir(mode=0o700)
        (self.cwd / "index.html").write_text("<!doctype html><title>Pairing fixture</title>")
        self.backend = RecordingBackend()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(self.sock.close)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.access = LoopbackAccess.create(self.sock.getsockname()[1])
        self.store = PairingStore(origin=self.access.origin, token=self.access.token,
                                  state_path=self.run_dir / "browser-grants.json")
        self.addCleanup(self.store.close)
        self.control = PairingControl(self.run_dir, self.store, token=self.access.token,
            metadata={"url": self.access.origin, "pid": os.getpid(), "mode": "pairing-test-fixture"})
        self.addCleanup(self.control.close)
        self.server = None
        self.shutdown_requests = 0
        self.lifecycle = ServiceControl(self.run_dir,
            metadata={"url": self.access.origin, "pid": os.getpid(), "mode": "pairing-test-fixture"},
            is_ready=lambda: self.server is not None and self.server.started,
            request_shutdown=self.request_shutdown)
        self.addCleanup(self.lifecycle.close)
        self.app = create_app(access=self.access, backend=self.backend,
            static_root=self.cwd, vendor_root=self.cwd, pairing_store=self.store,
            pairing_control=self.control, service_control=self.lifecycle)
        config = uvicorn.Config(self.app, host="127.0.0.1", port=self.access.port,
            access_log=False, log_level="critical", proxy_headers=False, ws="none",
            timeout_graceful_shutdown=2)
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run,
            kwargs={"sockets": [self.sock]}, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)
        self.wait_until(lambda: self.server.started)

    def request_shutdown(self):
        self.shutdown_requests += 1
        self.app.state.service_stopping = True
        self.server.should_exit = True

    def shutdown(self):
        self.server.should_exit = True
        self.thread.join(4)
        self.assertFalse(self.thread.is_alive(), "fixture HTTP service did not stop")

    def tearDown(self):
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(self.app.state.pairing_control_failed, self.expected_control_failure)
        self.assertFalse(self.app.state.service_control_failed)
        self.assertEqual(self.shutdown_requests, 0, "pairing must not request service shutdown")

    def wait_until(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.fail("bounded pairing fixture condition did not complete")

    def request(self, method, path, body=None, *, grant=None, origin=True, headers=None):
        selected = {"Origin": self.access.origin} if origin else {}
        if grant is not None:
            selected.update({"Cookie": grant[0], "Authorization": "Bearer " + grant[1]})
        selected.update(headers or {})
        connection = http.client.HTTPConnection("127.0.0.1", self.access.port, timeout=3)
        try:
            connection.request(method, path, body, selected)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def request_code(self):
        # A real owner-side file request; only the running service's lifespan
        # poll can issue its response. The test never calls control.poll itself.
        result = request_pairing_code(self.control.access_file, timeout=3)
        self.assertEqual(set(result), {"url", "token", "expiresAt"})
        self.assertEqual(result["url"], self.access.origin)
        self.assertGreater(result["expiresAt"], time.time())
        self.assertNotIn("token", result["url"])
        self.assertFalse((self.run_dir / "request.json").exists())
        self.assertFalse((self.run_dir / "response.json").exists())
        return result["token"]

    def login(self, token):
        status, headers, body = self.request("POST", "/auth", urlencode({"token": token}),
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 200)
        self.assertTrue(token.encode() not in body, "pairing code leaked into auth response")
        data = json.loads(body)
        cookie = SimpleCookie()
        cookie.load(headers["set-cookie"])
        name = f"{COOKIE_NAME}_{self.access.port}"
        self.assertTrue(cookie[name]["httponly"])
        self.assertEqual(cookie[name]["samesite"].lower(), "strict")
        self.assertEqual(headers["cache-control"], "no-store")
        return f"{name}={cookie[name].value}", data["bearer"]

    def replay(self, token):
        status, _, body = self.request("POST", "/auth", urlencode({"token": token}),
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 403)
        self.assertTrue(token.encode() not in body, "rejected code leaked into response")

    def test_owner_requests_fresh_single_use_code_without_revoking_browser(self):
        first_code = self.request_code()
        self.assertTrue(first_code == self.access.token, "startup code should remain usable until consumed")
        first_grant = self.login(first_code)
        self.wait_until(lambda: json.loads(self.control.access_file.read_text())["status"] == "needs-code")
        descriptor = json.loads(self.control.access_file.read_text())
        self.assertNotIn("token", descriptor)
        second_code = self.request_code()
        self.assertTrue(second_code != first_code, "consumed pairing code was reused")
        # Issuing another code leaves the first browser's independent pair valid.
        self.assertEqual(self.request("GET", "/api/state", grant=first_grant)[0], 200)
        self.replay(first_code)
        second_grant = self.login(second_code)
        self.replay(second_code)
        self.assertEqual(self.request("GET", "/api/state", grant=first_grant)[0], 200)
        self.assertEqual(self.request("GET", "/api/state", grant=second_grant)[0], 200)

        # Each individual credential and a mixture from different browsers fail
        # before the recording backend; issuing a code creates no new API bypass.
        for headers in ({"Cookie": first_grant[0]},
                        {"Authorization": "Bearer " + first_grant[1]},
                        {"Cookie": second_grant[0], "Authorization": "Bearer " + first_grant[1]}):
            self.assertEqual(self.request("GET", "/api/state", headers=headers)[0], 401)
        self.assertEqual(self.backend.snapshots, 3)

    def test_short_pair_command_discovers_service_and_renews_code_without_runtime_actions(self):
        source = Path(__file__).resolve().parents[2] / "src/botainer_dashboard/cli.py"
        name = "dashboard_pairing_integration_command"
        spec = importlib.util.spec_from_file_location(name, source)
        command = importlib.util.module_from_spec(spec)
        sys.modules[name] = command
        self.addCleanup(sys.modules.pop, name, None)
        spec.loader.exec_module(command)

        class OwnerTerminal(io.StringIO):
            def isatty(self):
                return True

        def pair(*selector):
            # Exercise the actual CLI and both private control mailboxes. Keep
            # its synthetic code in memory; never send it to test logs.
            output, errors = OwnerTerminal(), io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                result = command.main(["pair", *selector], root=self.cwd)
            self.assertEqual(result, 0, "short pairing command failed")
            self.assertFalse(errors.getvalue(), "short pairing command wrote an error")
            lines = output.getvalue().splitlines()
            self.assertTrue("Pair this browser at: " + self.access.origin in lines,
                            "pairing command returned the wrong browser address")
            prefix = "One-time pairing code: "
            codes = [line[len(prefix):] for line in lines if line.startswith(prefix)]
            self.assertEqual(len(codes), 1)
            self.assertFalse((self.run_dir / "request.json").exists())
            self.assertFalse((self.run_dir / "response.json").exists())
            return codes[0]

        first_code = pair()
        first_grant = self.login(first_code)
        second_code = pair("--port", str(self.access.port))
        self.assertTrue(second_code != first_code, "consumed code must be replaced")
        self.assertTrue(pair("--instance", self.run_dir.name) == second_code,
                        "an unused valid code should remain usable")
        self.assertEqual(self.backend.snapshots, 0, "pairing must not inspect runtime state")
        self.assertEqual(self.request("GET", "/api/state", grant=first_grant)[0], 200)
        second_grant = self.login(second_code)
        self.replay(first_code)
        self.replay(second_code)
        self.assertEqual(self.request("GET", "/api/state", grant=first_grant)[0], 200)
        self.assertEqual(self.request("GET", "/api/state", grant=second_grant)[0], 200)
        self.assertEqual(self.backend.snapshots, 3)
        self.assertEqual(self.backend.calls, [])
        self.assertFalse(self.server.should_exit)
        self.assertTrue(self.thread.is_alive())

    def test_owner_code_keeps_exact_host_and_origin_boundary(self):
        code = self.request_code()
        body = urlencode({"token": code})
        content_type = {"Content-Type": "application/x-www-form-urlencoded"}
        attempts = (
            ({"Host": "attacker.invalid"}, True),
            ({"Host": f"localhost:{self.access.port}"}, True),
            ({"Origin": "http://attacker.invalid"}, True),
            ({"Origin": "null"}, True),
            ({"X-Forwarded-Host": self.access.authority}, True),
            ({}, False),
        )
        for headers, origin in attempts:
            with self.subTest(header_names=tuple(headers), origin_present=origin):
                self.assertEqual(self.request("POST", "/auth", body, origin=origin,
                    headers={**content_type, **headers})[0], 403)
        # Boundary refusals must not consume the still-valid owner's code.
        grant = self.login(code)
        for headers in ({"Host": "attacker.invalid"}, {"Origin": "http://attacker.invalid"}):
            self.assertEqual(self.request("GET", "/api/state", grant=grant, headers=headers)[0], 403)
        for path, payload in (
            ("/api/projects/fixture/sessions", {"requestId": "fixture-request"}),
            ("/api/sessions/fixture/stop", {"requestId": "fixture-request", "contextNamespace": "fixture"}),
        ):
            self.assertEqual(self.request("POST", path, json.dumps(payload), grant=grant,
                origin=False, headers={"Content-Type": "application/json"})[0], 403)
        self.assertEqual(self.backend.snapshots, 0)

    def assert_poll_failure_preserves_browser(self, error):
        grant = self.login(self.request_code())
        self.expected_control_failure = True

        def ownership_released():
            # Verify the kernel lock from a separate open description, rather
            # than trusting the control object's closed flag or saved PID.
            fd = os.open(self.run_dir / "owner.lock", os.O_RDWR | os.O_NOFOLLOW)
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return False
                fcntl.flock(fd, fcntl.LOCK_UN)
                return True
            finally:
                os.close(fd)

        with patch.object(self.control, "poll", side_effect=error) as poll:
            self.wait_until(lambda: self.app.state.pairing_control_failed and ownership_released())
            poll.assert_called_once()
        self.assertEqual(json.loads(self.control.access_file.read_text())["status"], "stopped")
        with self.assertRaises(PairingControlError):
            request_pairing_code(self.control.access_file, timeout=0.5)

        # Failure of the owner mailbox does not close HTTP, revoke an existing
        # browser grant, or weaken its independent-cookie/bearer requirement.
        self.assertTrue(self.thread.is_alive())
        self.assertEqual(self.request("GET", "/api/state", grant=grant)[0], 200)
        self.assertEqual(self.request("GET", "/api/state", headers={"Cookie": grant[0]})[0], 401)
        self.assertEqual(self.backend.snapshots, 1)
        self.assertEqual(self.backend.calls, [])

    def test_mailbox_oserror_releases_ownership_without_ending_browser(self):
        self.assert_poll_failure_preserves_browser(OSError("synthetic mailbox storage failure"))

    def test_mailbox_valueerror_releases_ownership_without_ending_browser(self):
        self.assert_poll_failure_preserves_browser(ValueError("synthetic mailbox validation failure"))


if __name__ == "__main__":
    unittest.main()
