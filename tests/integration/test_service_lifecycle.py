"""Private lifecycle mailbox against a real, disposable loopback service.

The only child is the existing deterministic PTY fixture from test_service.
No Botainer, container, agent, SSH, or installed dashboard is controlled. A
fixture owner survives two ASGI server lifetimes within this test process;
that deliberately does not establish real Botainer or OS-process durability.
"""

import http.client
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import secrets
import socket
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

import uvicorn
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from botainer_dashboard.access import LoopbackAccess
from botainer_dashboard.pairing import PairingStore, read_private_json
from botainer_dashboard.pairing_control import PairingControl
from botainer_dashboard.service import COOKIE_NAME, WS_MAX_QUEUE, WS_MAX_SIZE, WS_PROTOCOL, create_app
from botainer_dashboard.service_control import (
    ServiceControl, ServiceControlError, request_service_action, wait_for_stopped,
)

from test_service import FixtureBackend


class _LoopbackService:
    """Run the application; hold its lifecycle lock through server cleanup."""

    def __init__(self, case, root, backend, *, ready=True):
        self.case, self.backend = case, backend
        self.ready = ready
        self.allow_shutdown = True
        self.shutdown_requested = threading.Event()
        self.server_closed = threading.Event()
        self.release_owner = threading.Event()
        self.release_owner.set()
        self.errors = []
        self.run_dir = root / secrets.token_hex(16)
        self.run_dir.mkdir(mode=0o700)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        case.addCleanup(self.sock.close)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.access = LoopbackAccess.create(self.sock.getsockname()[1])
        self.store = PairingStore(origin=self.access.origin, token=self.access.token,
                                  state_path=self.run_dir / "browser-grants.json")
        case.addCleanup(self.store.close)
        metadata = {"url": self.access.origin, "pid": os.getpid(), "mode": "lifecycle-test-fixture"}
        self.pairing = PairingControl(self.run_dir, self.store, token=self.access.token,
                                     metadata=metadata)
        case.addCleanup(self.pairing.close)
        self.server = None
        self.control = ServiceControl(self.run_dir, metadata=metadata,
            is_ready=lambda: self.ready and self.server is not None and self.server.started,
            request_shutdown=self.request_shutdown)
        case.addCleanup(self.control.close)
        self.app = create_app(access=self.access, backend=backend, static_root=root,
            vendor_root=root, pairing_store=self.store, pairing_control=self.pairing,
            service_control=self.control)
        config = uvicorn.Config(self.app, host="127.0.0.1", port=self.access.port,
            access_log=False, log_level="critical", proxy_headers=False, ws="websockets",
            ws_max_size=WS_MAX_SIZE, ws_max_queue=WS_MAX_QUEUE,
            ws_per_message_deflate=False, timeout_graceful_shutdown=2)
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.run, daemon=True)
        case.addCleanup(self.shutdown)
        self.thread.start()
        case.wait_until(lambda: self.server.started)

    def request_shutdown(self):
        self.app.state.service_stopping = True
        self.shutdown_requested.set()
        if self.allow_shutdown:
            self.server.should_exit = True

    def run(self):
        try:
            self.server.run(sockets=[self.sock])
        except BaseException as exc:
            self.errors.append(exc)
        finally:
            self.sock.close()
            self.server_closed.set()
            # A test can pause here to distinguish listener closure from the
            # launcher's final release of the immutable service-owner lock.
            if not self.release_owner.wait(5):
                self.errors.append(RuntimeError("fixture owner-release gate timed out"))
            self.pairing.close()
            self.control.close()
            self.store.close()

    def shutdown(self):
        self.release_owner.set()
        self.server.should_exit = True
        self.thread.join(5)
        self.case.assertFalse(self.thread.is_alive(), "fixture service did not stop")
        self.case.assertEqual(self.errors, [])

    def action(self, action, timeout=2):
        return request_service_action(self.control.descriptor_path, action, timeout=timeout)

    def request(self, method, path, body=None, *, grant=None, headers=None):
        selected = {"Origin": self.access.origin}
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

    def login(self):
        status, headers, body = self.request("POST", "/auth",
            urlencode({"token": self.access.token}),
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.case.assertEqual(status, 200, body)
        cookie = SimpleCookie()
        cookie.load(headers["set-cookie"])
        name = f"{COOKIE_NAME}_{self.access.port}"
        return f"{name}={cookie[name].value}", json.loads(body)["bearer"]

    def websocket(self, grant):
        return connect(f"ws://{self.access.authority}/api/sessions/session1/terminal",
            origin=self.access.origin, additional_headers=[("Cookie", grant[0])],
            subprotocols=[WS_PROTOCOL, "credential." + grant[1]],
            open_timeout=3, close_timeout=1, proxy=None, compression=None,
            max_size=WS_MAX_SIZE, max_queue=4)


@unittest.skipUnless(os.name == "posix", "owner mailbox and PTY require POSIX")
class ServiceLifecycleIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="dashboard-lifecycle-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "index.html").write_text("<!doctype html><title>Lifecycle fixture</title>")
        self.backend = FixtureBackend(self.root)
        self.addCleanup(self.backend.close)

    def wait_until(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.fail("bounded lifecycle fixture condition did not complete")

    def service(self, **kwargs):
        return _LoopbackService(self, self.root, self.backend, **kwargs)

    def bind(self, ws):
        ws.send(json.dumps({"type": "bind", "contextNamespace": "fixture", "cols": 80, "rows": 24}))
        self.assertEqual(json.loads(ws.recv(timeout=3)), {"type": "ready"})

    def output(self, ws, expected):
        result = bytearray()
        while len(result) < len(expected):
            chunk = ws.recv(timeout=3)
            self.assertIsInstance(chunk, bytes)
            result.extend(chunk)
            ws.send(json.dumps({"type": "ack", "bytes": len(chunk)}))
        self.assertEqual(bytes(result), expected)

    def test_status_reports_starting_until_readiness_callback_then_running(self):
        service = self.service(ready=False)
        starting = service.action("status")
        self.assertEqual(starting["status"], "starting")
        self.assertTrue(starting["responsive"])
        self.assertEqual(starting["runId"], service.run_dir.name)
        self.assertEqual(starting["url"], service.access.origin)
        service.ready = True
        running = service.action("status")
        self.assertEqual(running["status"], "running")
        self.assertTrue(running["responsive"])
        self.assertEqual(self.backend.actions, [])
        self.assertIsNone(self.backend.owner)

    def test_stop_ack_does_not_claim_owner_released_before_final_cleanup(self):
        service = self.service()
        service.release_owner.clear()
        result = service.action("stop")
        self.assertEqual(result["status"], "stopping")
        self.assertTrue(result["responsive"])
        self.assertTrue(service.shutdown_requested.is_set())
        self.assertTrue(service.server_closed.wait(3))
        self.assertFalse(wait_for_stopped(service.control.descriptor_path, timeout=0.15))
        self.assertEqual(self.backend.actions, [])
        service.release_owner.set()
        self.assertTrue(wait_for_stopped(service.control.descriptor_path, timeout=3))
        service.thread.join(3)
        self.assertFalse(service.thread.is_alive())
        self.assertEqual(service.action("status")["status"], "stopped")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.3)
            self.assertNotEqual(probe.connect_ex(("127.0.0.1", service.access.port)), 0)

    def test_stop_blocks_new_launch_while_view_state_remains_readable(self):
        service = self.service()
        grant = service.login()
        service.allow_shutdown = False
        result = service.action("stop")
        self.assertEqual(result["status"], "stopping")
        status, _, body = service.request("POST", "/api/projects/project1/sessions",
            json.dumps({"requestId": "lifecycle-fixture-start"}), grant=grant,
            headers={"Content-Type": "application/json"})
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body), {"error": "dashboard-stopping"})
        self.assertEqual(service.request("GET", "/api/state", grant=grant)[0], 200)
        self.assertFalse(wait_for_stopped(service.control.descriptor_path, timeout=0.15))
        self.assertEqual(self.backend.actions, [])
        self.assertIsNone(self.backend.owner)

    def test_pairing_control_failure_does_not_disable_service_status_or_stop(self):
        service = self.service()
        grant = service.login()
        with patch.object(service.pairing, "poll", side_effect=OSError("synthetic pairing failure")):
            self.wait_until(lambda: service.app.state.pairing_control_failed)
        self.assertFalse(service.app.state.service_control_failed)
        self.assertEqual(service.action("status")["status"], "running")
        self.assertEqual(service.request("GET", "/api/state", grant=grant)[0], 200)
        self.assertEqual(service.action("stop")["status"], "stopping")
        self.assertTrue(wait_for_stopped(service.control.descriptor_path, timeout=3))
        self.assertEqual(self.backend.actions, [])

    def test_service_control_failure_keeps_owner_active_and_browser_usable(self):
        service = self.service()
        grant = service.login()
        failure_entered, allow_publication, failure_finished = (threading.Event() for _ in range(3))
        original_failed = service.control.failed

        def gated_failure():
            # The event-loop failure flag precedes atomic descriptor I/O. Make
            # that window deterministic; the flag is not a publication barrier.
            failure_entered.set()
            if not allow_publication.wait(3):
                raise AssertionError("fixture failure-publication gate timed out")
            try:
                original_failed()
            finally:
                failure_finished.set()

        with patch.object(service.control, "poll", side_effect=OSError("synthetic lifecycle failure")), \
                patch.object(service.control, "failed", side_effect=gated_failure):
            try:
                self.assertTrue(failure_entered.wait(3))
                self.assertTrue(service.app.state.service_control_failed)
                self.assertIn(read_private_json(service.control.descriptor_path)["status"], {"starting", "running"})
            finally:
                allow_publication.set()
            self.assertTrue(failure_finished.wait(3))
        self.assertFalse(service.app.state.pairing_control_failed)
        self.assertEqual(read_private_json(service.control.descriptor_path)["status"], "control-unavailable")
        self.assertFalse(wait_for_stopped(service.control.descriptor_path, timeout=0.15))
        with self.assertRaisesRegex(ServiceControlError, "still owned.*unavailable"):
            service.action("status", timeout=0.15)
        self.assertEqual(service.request("GET", "/api/state", grant=grant)[0], 200)
        self.assertTrue(service.thread.is_alive())
        self.assertEqual(self.backend.actions, [])

    def test_stop_closes_viewer_and_new_service_reconnects_same_fixture_owner(self):
        first = self.service()
        with first.websocket(first.login()) as ws:
            self.bind(ws)
            self.output(ws, b"READY")
            ws.send(b"first-input")
            self.output(ws, b"first-input")
            owner = self.backend.owner
            pid = owner.pid
            self.assertEqual(first.action("stop")["status"], "stopping")
            with self.assertRaises(ConnectionClosed):
                ws.recv(timeout=3)
        self.assertTrue(wait_for_stopped(first.control.descriptor_path, timeout=3))
        self.assertGreaterEqual(self.backend.detaches, 1)
        self.assertFalse(owner.closed)
        self.assertIsNone(owner._process.poll())
        self.assertEqual(self.backend.actions, [])

        second = self.service()
        self.assertNotEqual(first.run_dir, second.run_dir)
        with second.websocket(second.login()) as ws:
            self.bind(ws)
            self.assertIs(self.backend.owner, owner)
            self.assertEqual(self.backend.owner.pid, pid)
            ws.send(b"second-input")
            # An accidental replacement emits READY; replayed first input also
            # fails this exact comparison and the input history below.
            self.output(ws, b"second-input")
        self.assertEqual(self.backend.input, b"first-inputsecond-input")
        self.assertEqual(self.backend.attaches, 2)
        self.assertEqual(self.backend.actions, [])


if __name__ == "__main__":
    unittest.main()
