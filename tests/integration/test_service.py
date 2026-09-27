"""Opt-in real loopback HTTP/WS tests with a trusted deterministic PTY fixture.

Uses only approved runtime libraries and unittest. Never dispatches Botainer or
starts a container/agent. The fixture owns its PTY child across browser detach;
this does not establish the production Botainer durable-owner gate.
"""

import http.client
from http.cookies import SimpleCookie
import json
import logging
import os
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

import uvicorn
from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from botainer_dashboard.access import LoopbackAccess
from botainer_dashboard.pty_bridge import PtyAttachment
from botainer_dashboard.service import (
    BackendUnavailable, COOKIE_NAME, OUTPUT_WINDOW, WS_MAX_QUEUE, WS_MAX_SIZE, WS_PROTOCOL, create_app,
)

_CHILD = """import fcntl,os,struct,termios
os.write(1,b'READY')
while True:
    data=os.read(0,16384)
    if data == b'size':
        rows,cols,*_=struct.unpack('HHHH',fcntl.ioctl(0,termios.TIOCGWINSZ,b'\\0'*8))
        os.write(1,('%d,%d' % (cols,rows)).encode())
    elif data == b'flood':
        remaining=b'x'*262144
        while remaining:
            remaining=remaining[os.write(1,remaining):]
    else:
        os.write(1,data)
"""


class _AttachmentView:
    def __init__(self, backend):
        self.backend = backend

    def read(self, *args, **kwargs):
        # Explicit fixture Stop closes its local PTY; present the resulting
        # owner end as EOF, as a remote attachment does after allocation exit.
        if self.backend.owner.closed:
            return b""
        try:
            return self.backend.owner.read(*args, **kwargs)
        except (ValueError, OSError):
            if self.backend.owner.closed:
                return b""
            raise

    def write(self, data):
        self.backend.input.extend(data)
        return self.backend.owner.write(data)

    def resize(self, cols, rows):
        self.backend.owner.resize(cols, rows)

    def close(self):
        self.backend.detaches += 1
        if self.backend.fail_close:
            raise OSError("private fixture owner socket disappeared during cleanup")


class FixtureBackend:
    def __init__(self, cwd):
        self.cwd = cwd
        self.owner = None
        self.attaches = 0
        self.detaches = 0
        self.actions = []
        self.input = bytearray()
        self.fail_close = False

    def snapshot(self):
        return {"mode": "deterministic-test-fixture", "projects": [{"id": "project1", "name": "Fixture"}],
                "sessions": [{"contextNamespace": "fixture", "runtimeId": "session1", "projectId": "project1"}]}

    def start_session(self, project_id, request_id):
        if project_id != "project1":
            raise BackendUnavailable("unknown-project")
        self.actions.append(("start", project_id, request_id))
        return {"status": "fixture-only"}

    def stop_session(self, namespace, runtime_id, request_id):
        if (namespace, runtime_id) != ("fixture", "session1"):
            raise BackendUnavailable("unknown-session")
        self.actions.append(("stop", namespace, runtime_id, request_id))
        if self.owner:
            self.owner.close()
        return {"status": "stopped"}

    def attach(self, namespace, runtime_id, cols, rows):
        if (namespace, runtime_id) != ("fixture", "session1"):
            raise BackendUnavailable("unknown-session")
        if self.owner is None:
            self.owner = PtyAttachment([sys.executable, "-I", "-S", "-c", _CHILD], cwd=self.cwd,
                                       env={"TERM": "xterm-256color"}, cols=cols, rows=rows)
        self.attaches += 1
        self.owner.resize(cols, rows)
        return _AttachmentView(self)

    def close(self):
        if self.owner:
            self.owner.close()


@unittest.skipUnless(os.name == "posix", "POSIX PTY required")
class ServiceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="dashboard-service-")
        self.addCleanup(self.temp.cleanup)
        self.cwd = Path(self.temp.name).resolve()
        (self.cwd / "index.html").write_text("<!doctype html><title>Trusted fixture frontend</title>")
        (self.cwd / "app.js").write_text("// trusted fixture asset")
        self.backend = FixtureBackend(self.cwd)
        self.addCleanup(self.backend.close)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.access = LoopbackAccess.create(self.sock.getsockname()[1])
        self.app = create_app(access=self.access, backend=self.backend, static_root=self.cwd, vendor_root=self.cwd)
        config = uvicorn.Config(self.app, host="127.0.0.1", port=self.access.port, access_log=False,
                                log_level="critical", proxy_headers=False, ws="websockets",
                                ws_max_size=WS_MAX_SIZE, ws_max_queue=WS_MAX_QUEUE,
                                ws_per_message_deflate=False, timeout_graceful_shutdown=2)
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [self.sock]}, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)
        self.wait_until(lambda: self.server.started)
        self.credential = self.login()

    def shutdown(self):
        self.server.should_exit = True
        self.thread.join(4)
        self.sock.close()
        self.assertFalse(self.thread.is_alive(), "loopback server did not stop")

    def wait_until(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.fail("bounded fixture condition did not complete")

    def request(self, method, path, body=None, *, cookie=True, origin=True, headers=None):
        selected = {}
        if cookie and getattr(self, "credential", None):
            selected["Cookie"] = self.credential
            selected["Authorization"] = "Bearer " + self.bearer
        if origin:
            selected["Origin"] = self.access.origin
        selected.update(headers or {})
        connection = http.client.HTTPConnection("127.0.0.1", self.access.port, timeout=3)
        try:
            connection.request(method, path, body, selected)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def login(self):
        status, headers, body = self.request("POST", "/auth", f"token={self.access.token}", cookie=False,
                                             headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 200, body)
        self.bearer = json.loads(body)["bearer"]
        self.assertNotIn(self.access.token.encode(), body)
        cookie = SimpleCookie()
        cookie.load(headers["set-cookie"])
        name = f"{COOKIE_NAME}_{self.access.port}"
        self.assertTrue(cookie[name]["httponly"])
        self.assertEqual(cookie[name]["samesite"].lower(), "strict")
        return f"{name}={cookie[name].value}"

    def websocket(self, *, origin=None, cookie=None, bearer=None, protocols=None, path="/api/sessions/session1/terminal", headers=None):
        selected = [("Cookie", self.credential if cookie is None else cookie)]
        selected.extend(headers or [])
        return connect(f"ws://{self.access.authority}{path}",
                       origin=self.access.origin if origin is None else (None if origin is False else origin),
                       additional_headers=selected, subprotocols=protocols if protocols is not None else [WS_PROTOCOL, "credential." + (bearer if bearer is not None else self.bearer)],
                       open_timeout=3, close_timeout=1, proxy=None,
                       compression=None, max_size=WS_MAX_SIZE, max_queue=4)

    def bind(self, ws, namespace="fixture"):
        ws.send(json.dumps({"type": "bind", "contextNamespace": namespace, "cols": 80, "rows": 24}))
        self.assertEqual(json.loads(ws.recv(timeout=3)), {"type": "ready"})

    def output(self, ws, expected):
        result = bytearray()
        while len(result) < len(expected):
            chunk = ws.recv(timeout=3)
            self.assertIsInstance(chunk, bytes)
            result.extend(chunk)
            ws.send(json.dumps({"type": "ack", "bytes": len(chunk)}))
        self.assertEqual(bytes(result), expected)

    def test_cookie_bootstrap_no_url_secret_and_static_authentication(self):
        status, headers, body = self.request("GET", "/", cookie=False, origin=False)
        self.assertEqual(status, 200)
        self.assertIn(b'form id="login-form" method="post" action="/auth"', body)
        self.assertIn(f'python3 tools/dashboard.py pair --port {self.access.port}'.encode(), body)
        self.assertNotIn(b'__PAIRING_PORT__', body)
        self.assertEqual(self.request("GET", "/unlock", cookie=False, origin=False)[2], body)
        self.assertNotIn(self.access.token.encode(), body)
        self.assertIn("frame-ancestors 'none'", headers["content-security-policy"])
        self.assertEqual(self.request("GET", "/api/state", cookie=False)[0], 401)
        self.assertEqual(self.request("GET", "/app.js", cookie=False)[0], 200)
        self.assertEqual(self.request("GET", "/app.js")[0], 200)
        self.assertEqual(self.request("GET", "/api/state", origin=False)[0], 200)
        self.assertEqual(self.request("GET", "/?token=" + self.access.token)[0], 403)

    def test_stolen_cross_port_cookie_without_bearer_cannot_access_api_or_ws(self):
        for authorization in ("", "Bearer wrong", "Basic " + self.bearer):
            status, _, body = self.request("GET", "/api/state", headers={"Authorization": authorization})
            self.assertEqual(status, 401)
            self.assertNotIn(self.bearer.encode(), body)
        with self.assertRaises(InvalidStatus):
            with self.websocket(protocols=[WS_PROTOCOL]):
                pass
        self.assertEqual(self.backend.attaches, 0)
        # A separate login creates a different cookie/bearer pair; mixing a
        # stolen cookie with another browser's valid bearer must still fail.
        prior_cookie, prior_bearer = self.credential, self.bearer
        self.app.state.pairing_store.issue_token(self.access.token)
        self.login()
        self.assertEqual(self.request("GET", "/api/state", headers={"Cookie": prior_cookie})[0], 401)
        self.bearer = prior_bearer
        self.assertEqual(self.request("GET", "/api/state")[0], 200)

    def test_two_loopback_services_can_share_browser_cookies_without_overwriting_auth(self):
        other = ServiceIntegrationTests("test_cookie_bootstrap_no_url_secret_and_static_authentication")
        self.addCleanup(other.doCleanups)
        other.setUp()
        combined = self.credential + "; " + other.credential
        self.assertEqual(self.request("GET", "/api/state", headers={"Cookie": combined})[0], 200)
        self.assertEqual(other.request("GET", "/api/state", headers={"Cookie": combined})[0], 200)
        self.assertEqual(self.request("GET", "/api/state", headers={"Cookie": combined,
                         "Authorization": "Bearer " + other.bearer})[0], 401)

    def test_wrong_host_and_cross_origin_refused_before_backend(self):
        self.assertEqual(self.request("GET", "/api/state", headers={"Host": "attacker.invalid"})[0], 403)
        self.assertEqual(self.request("GET", "/api/state", headers={"Origin": "http://attacker.invalid"})[0], 403)
        self.assertEqual(self.request("GET", "/api/state", headers={"X-Forwarded-Host": self.access.authority})[0], 403)
        for origin in (False, True):
            status, _, _ = self.request("POST", "/api/projects/project1/sessions", '{"requestId":"r1"}',
                                         origin=origin, headers={"Content-Type": "application/json",
                                                                 **({"Origin": "null"} if origin else {})})
            self.assertEqual(status, 403)
        self.assertEqual(self.backend.actions, [])

    def test_duplicate_security_headers_and_cookie_are_refused(self):
        for duplicated, values in (("Origin", [self.access.origin] * 2),
                                   ("Cookie", [self.credential] * 2),
                                   ("Host", [self.access.authority] * 2)):
            with self.subTest(duplicated=duplicated):
                connection = http.client.HTTPConnection("127.0.0.1", self.access.port, timeout=3)
                connection.putrequest("GET", "/api/state", skip_host=True)
                if duplicated != "Host":
                    connection.putheader("Host", self.access.authority)
                if duplicated != "Cookie":
                    connection.putheader("Cookie", self.credential)
                connection.putheader("Authorization", "Bearer " + self.bearer)
                for value in values:
                    connection.putheader(duplicated, value)
                connection.endheaders()
                response = connection.getresponse()
                self.assertIn(response.status, (400, 403))
                response.read()
                connection.close()
        self.assertEqual(self.request("GET", "/api/state", headers={"Cookie": self.credential + "; " + self.credential})[0], 403)

    def test_bootstrap_refuses_ambiguous_or_unbounded_forms(self):
        for body, content_type in (("token=" + self.access.token + "&token=" + self.access.token, "application/x-www-form-urlencoded"),
                                   ("token=" + "x" * 1000, "application/x-www-form-urlencoded"),
                                   ('{"token":"' + self.access.token + '"}', "application/json"),
                                   ("token=wrong", "application/x-www-form-urlencoded")):
            with self.subTest(content_type=content_type, length=len(body)):
                self.assertEqual(self.request("POST", "/auth", body, cookie=False, headers={"Content-Type": content_type})[0], 403)
        self.assertEqual(self.request("POST", "/auth", "token=" + self.access.token, origin=False,
                                      headers={"Content-Type": "application/x-www-form-urlencoded"})[0], 403)

    def test_actions_require_exact_fields_and_explicit_context(self):
        headers = {"Content-Type": "application/json"}
        for body in ('{"requestId":"r1","argv":["/bin/sh"]}',
                     '{"requestId":"r1","requestId":"r2"}', '{"requestId":"../shell"}', '"bad"'):
            self.assertEqual(self.request("POST", "/api/projects/project1/sessions", body, headers=headers)[0], 400)
        self.assertEqual(self.backend.actions, [])
        self.assertEqual(self.request("POST", "/api/projects/project1/sessions", '{"requestId":"r1"}', headers=headers)[0], 200)
        self.assertEqual(self.backend.actions, [("start", "project1", "r1")])
        self.assertEqual(self.request("POST", "/api/sessions/session1/stop", '{"requestId":"r2"}', headers=headers)[0], 400)
        self.assertEqual(self.request("POST", "/api/sessions/session1/stop", '{"requestId":"r2","contextNamespace":"wrong"}', headers=headers)[0], 409)
        self.assertEqual(len(self.backend.actions), 1)

    def test_orphan_stop_requires_explicit_mode_supported_backend_and_authentication(self):
        headers = {"Content-Type": "application/json"}
        path = "/api/sessions/session1/stop"
        value = {"contextNamespace": "fixture", "requestId": "orphan-request",
                 "expectedStopMode": "orphan-container"}
        # An old backend cannot silently turn this into its normal Stop.
        self.assertEqual(self.request("POST", path, json.dumps(value), headers=headers)[0], 409)
        self.assertEqual(self.backend.actions, [])
        for mode in [True, None, {}, [], "force", "allocation"]:
            self.assertEqual(self.request("POST", path, json.dumps({**value, "expectedStopMode": mode}), headers=headers)[0], 400)
        calls = []
        self.backend.stop_orphan_session = lambda *args: calls.append(args) or {"terminationConfirmed": True}
        body = json.dumps(value)
        self.assertEqual(self.request("POST", path, body, headers=headers, cookie=False)[0], 401)
        self.assertEqual(self.request("POST", path, body, headers=headers, origin=False)[0], 403)
        self.assertEqual(calls, [])
        self.assertEqual(self.request("POST", path, body, headers=headers)[0], 200)
        self.assertEqual(calls, [("fixture", "session1", "orphan-request")])
        self.assertEqual(self.backend.actions, [])
        for mode in [None, "normal"]:
            normal = {key: value[key] for key in ("contextNamespace", "requestId")}
            if mode: normal["expectedStopMode"] = mode
            self.assertEqual(self.request("POST", path, json.dumps(normal), headers=headers)[0], 200)
        self.assertEqual(len(calls), 1)
        self.assertEqual([action[0] for action in self.backend.actions], ["stop", "stop"])

    def test_websocket_rejects_auth_origin_duplicates_and_query_before_attach(self):
        attempts = ({"origin": "http://attacker.invalid"}, {"origin": False}, {"cookie": "wrong=value"},
                    {"bearer": "x" * 43}, {"protocols": [WS_PROTOCOL]},
                    {"protocols": [WS_PROTOCOL, "credential." + self.bearer, "extra"]},
                    {"headers": [("Origin", self.access.origin)]},
                    {"path": "/api/sessions/session1/terminal?token=" + self.access.token})
        for kwargs in attempts:
            with self.subTest(keys=list(kwargs)), self.assertRaises(InvalidStatus) as error:
                with self.websocket(**kwargs):
                    pass
            self.assertEqual(error.exception.response.status_code, 403)
        self.assertEqual(self.backend.attaches, 0)

    def test_agent_override_is_bounded_and_requires_advertised_support(self):
        headers = {"Content-Type": "application/json"}
        for agent in ("", "default", "codex --help", ["codex"], None):
            body = json.dumps({"requestId": "r1", "agent": agent})
            self.assertEqual(self.request("POST", "/api/projects/project1/sessions", body, headers=headers)[0], 400)
        body = json.dumps({"requestId": "r1", "agent": "codex"})
        self.assertEqual(self.request("POST", "/api/projects/project1/sessions", body, headers=headers)[0], 409)
        self.assertEqual(self.backend.actions, [])
        snap = self.backend.snapshot()
        snap["projects"][0]["capabilities"] = {"agentOverride": True}
        calls = []
        def start(project_id, request_id, agent=None):
            calls.append((project_id, request_id, agent))
            return {"status": "fixture-only"}
        with patch.object(self.backend, "snapshot", return_value=snap), patch.object(self.backend, "start_session", side_effect=start):
            self.assertEqual(self.request("POST", "/api/projects/project1/sessions", body, headers=headers)[0], 200)
        self.assertEqual(calls, [("project1", "r1", "codex")])

    def test_real_pty_bytes_resize_detach_and_reconnect_without_input_replay(self):
        with self.websocket() as ws:
            self.bind(ws)
            self.output(ws, b"READY")
            pid = self.backend.owner.pid
            data = b"\x00\x03\x1b[A\r\n\xff\xf0\x9f\x98\x80"
            ws.send(data)
            self.output(ws, data)
            ws.send(json.dumps({"type": "resize", "cols": 132, "rows": 43}))
            ws.send(b"size")
            self.output(ws, b"132,43")
        self.wait_until(lambda: self.backend.detaches == 1)
        self.assertFalse(self.backend.owner.closed)
        with self.websocket() as ws:
            self.bind(ws)
            self.assertEqual(self.backend.owner.pid, pid)
            with self.assertRaises(TimeoutError):
                ws.recv(timeout=0.1)
            ws.send(b"reconnected")
            self.output(ws, b"reconnected")
        self.assertEqual(bytes(self.backend.input), data + b"size" + b"reconnected")

    def test_backend_identity_refusal_never_readies_or_forwards_input(self):
        with patch.object(self.backend, "attach", side_effect=BackendUnavailable("runtime-identity-changed")) as attach:
            with self.websocket() as ws:
                ws.send(json.dumps({"type": "bind", "contextNamespace": "fixture", "cols": 80, "rows": 24}))
                # The first server event must be refusal, never a ready frame.
                with self.assertRaises(ConnectionClosed) as error:
                    ws.recv(timeout=3)
                self.assertEqual(error.exception.rcvd.code, 1008)
                with self.assertRaises(ConnectionClosed):
                    ws.send(b"must-not-reach-an-attachment")
            attach.assert_called_once_with("fixture", "session1", 80, 24)
        self.assertIsNone(self.backend.owner)
        self.assertEqual(self.backend.attaches, 0)
        self.assertEqual(self.backend.input, b"")

    def test_slow_attach_keeps_http_responsive_and_disconnect_cleans_late_client(self):
        entered, release = threading.Event(), threading.Event()
        original = self.backend.attach
        def delayed(*args):
            entered.set()
            if not release.wait(3):
                raise BackendUnavailable("fixture-attach-timeout")
            return original(*args)
        with patch.object(self.backend, "attach", side_effect=delayed):
            try:
                with self.websocket() as ws:
                    ws.send(json.dumps({"type": "bind", "contextNamespace": "fixture", "cols": 80, "rows": 24}))
                    self.assertTrue(entered.wait(1))
                    started = time.monotonic()
                    self.assertEqual(self.request("GET", "/api/state")[0], 200)
                    self.assertLess(time.monotonic() - started, 0.5)
                # The old attempt is still creating its client: its lease must
                # remain held until the worker confirms that client's cleanup.
                with self.websocket() as other:
                    other.send(json.dumps({"type": "bind", "contextNamespace": "fixture", "cols": 80, "rows": 24}))
                    with self.assertRaises(ConnectionClosed):
                        other.recv(timeout=3)
                self.assertEqual(self.backend.attaches, 0)
            finally:
                release.set()
                self.wait_until(lambda: self.backend.detaches == 1)
            self.assertEqual(self.backend.input, b"")
            self.assertEqual(self.request("GET", "/api/state")[0], 200)
            with self.websocket() as ws:
                self.bind(ws)
                self.output(ws, b"READY")
                ws.send(b"after-cleanup")
                self.output(ws, b"after-cleanup")

    def test_cancelled_service_task_cleans_late_attachment(self):
        entered, release = threading.Event(), threading.Event()
        original = self.backend.attach
        def delayed(*args):
            entered.set()
            if not release.wait(3):
                raise BackendUnavailable("fixture-attach-timeout")
            return original(*args)
        with patch.object(self.backend, "attach", side_effect=delayed):
            try:
                with self.websocket() as ws:
                    ws.send(json.dumps({"type": "bind", "contextNamespace": "fixture", "cols": 80, "rows": 24}))
                    self.assertTrue(entered.wait(1))
                    tasks = list(self.server.server_state.tasks)
                    self.assertEqual(len(tasks), 1)
                    tasks[0].get_loop().call_soon_threadsafe(tasks[0].cancel)
                    with self.assertRaises(ConnectionClosed):
                        ws.recv(timeout=3)
            finally:
                release.set()
                self.wait_until(lambda: self.backend.detaches == 1)
        self.assertEqual(self.backend.attaches, 1)
        self.assertEqual(self.backend.input, b"")

    def test_attach_timeout_cleans_client_created_after_timeout(self):
        entered, release = threading.Event(), threading.Event()
        original = self.backend.attach
        def delayed(*args):
            entered.set()
            if not release.wait(3):
                raise BackendUnavailable("fixture-attach-timeout")
            return original(*args)
        with patch.object(self.backend, "attach", side_effect=delayed), patch("botainer_dashboard.service.ATTACH_TIMEOUT", 0.1):
            try:
                with self.websocket() as ws:
                    ws.send(json.dumps({"type": "bind", "contextNamespace": "fixture", "cols": 80, "rows": 24}))
                    self.assertTrue(entered.wait(1))
                    with self.assertRaises(ConnectionClosed) as error:
                        ws.recv(timeout=3)
                    self.assertEqual(error.exception.rcvd.code, 1008)
                    self.assertEqual(self.backend.attaches, 0)
            finally:
                release.set()
                self.wait_until(lambda: self.backend.detaches == 1)
        self.assertEqual(self.backend.input, b"")

    def test_one_writer_per_session_and_unknown_target_does_not_attach(self):
        with self.websocket() as first:
            self.bind(first)
            self.output(first, b"READY")
            with self.websocket() as second:
                second.send(json.dumps({"type": "bind", "contextNamespace": "fixture", "cols": 80, "rows": 24}))
                with self.assertRaises(ConnectionClosed) as error:
                    second.recv(timeout=3)
                self.assertEqual(error.exception.rcvd.code, 1008)
            first.send(b"still-owning")
            self.output(first, b"still-owning")
            with self.websocket() as other:
                other.send(json.dumps({"type": "bind", "contextNamespace": "untrusted", "cols": 80, "rows": 24}))
                with self.assertRaises(ConnectionClosed):
                    other.recv(timeout=3)
        self.assertEqual(self.backend.attaches, 1)

    def test_uncertain_attachment_cleanup_keeps_target_fenced(self):
        self.backend.fail_close = True
        with self.websocket() as ws:
            self.bind(ws)
            self.output(ws, b"READY")
        self.wait_until(lambda: self.backend.detaches == 1)
        with self.websocket() as ws:
            ws.send(json.dumps({"type": "bind", "contextNamespace": "fixture", "cols": 80, "rows": 24}))
            with self.assertRaises(ConnectionClosed):
                ws.recv(timeout=3)
        self.assertEqual(self.backend.attaches, 1)
        self.assertFalse(self.backend.owner.closed)

    def assert_second_writer_fenced(self):
        with self.websocket() as other:
            other.send(json.dumps({"type": "bind", "contextNamespace": "fixture", "cols": 80, "rows": 24}))
            with self.assertRaises(ConnectionClosed) as error:
                other.recv(timeout=3)
            self.assertEqual(error.exception.rcvd.code, 1008)
        self.assertEqual(self.backend.attaches, 1)

    def stop_attached_fixture(self, ws):
        status, _, body = self.request("POST", "/api/sessions/session1/stop",
            json.dumps({"contextNamespace": "fixture", "requestId": "explicit-stop"}),
            headers={"Content-Type": "application/json"})
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(ws.recv(timeout=3)), {"type": "eof"})
        with self.assertRaises(ConnectionClosed) as error:
            ws.recv(timeout=3)
        self.assertEqual(error.exception.rcvd.code, 1011)
        self.assertEqual(error.exception.rcvd.reason, "attachment-cleanup-unconfirmed")

    def test_owner_stop_with_uncertain_detach_closes_safely_without_asgi_exception(self):
        self.backend.fail_close = True
        with patch.object(logging.getLogger("uvicorn.error"), "error") as unhandled:
            with self.websocket() as ws:
                self.bind(ws)
                self.output(ws, b"READY")
                self.stop_attached_fixture(ws)
            self.wait_until(lambda: not self.server.server_state.tasks)
            self.assertEqual(self.backend.detaches, 1)
            self.assertTrue(self.backend.owner.closed)
            self.assert_second_writer_fenced()
            self.wait_until(lambda: not self.server.server_state.tasks)
            unhandled.assert_not_called()

    def test_detach_timeout_keeps_fence_after_late_worker_returns_without_asgi_exception(self):
        entered, release, returned = threading.Event(), threading.Event(), threading.Event()
        def slow_close():
            self.backend.detaches += 1
            entered.set()
            try:
                release.wait(3)
            finally:
                returned.set()
        with patch.object(logging.getLogger("uvicorn.error"), "error") as unhandled:
            with self.websocket() as ws:
                self.bind(ws)
                self.output(ws, b"READY")
                with patch.object(_AttachmentView, "close", side_effect=slow_close), \
                        patch("botainer_dashboard.service.IO_TIMEOUT", .1):
                    try:
                        self.stop_attached_fixture(ws)
                        self.assertTrue(entered.is_set())
                        self.assertFalse(returned.is_set())
                    finally:
                        release.set()
                        if entered.is_set():
                            self.assertTrue(returned.wait(1))
            self.wait_until(lambda: not self.server.server_state.tasks)
            self.assert_second_writer_fenced()
            self.wait_until(lambda: not self.server.server_state.tasks)
            unhandled.assert_not_called()

    def test_output_window_waits_for_renderer_drain(self):
        with self.websocket() as ws:
            self.bind(ws)
            self.output(ws, b"READY")
            ws.send(b"flood")
            seen = 0
            while seen < OUTPUT_WINDOW:
                chunk = ws.recv(timeout=3)
                self.assertIsInstance(chunk, bytes)
                self.assertLessEqual(len(chunk), WS_MAX_SIZE)
                seen += len(chunk)
            self.assertEqual(seen, OUTPUT_WINDOW)
            with self.assertRaises(TimeoutError):
                ws.recv(timeout=0.15)
            ws.send(json.dumps({"type": "ack", "bytes": 1024}))
            self.assertEqual(len(ws.recv(timeout=3)), 1024)
            with self.assertRaises(TimeoutError):
                ws.recv(timeout=0.15)

    def test_expiry_blocks_input_while_output_waits_for_ack(self):
        self.app.state.pairing_store.issue_token(self.access.token)
        with patch.object(self.app.state.pairing_store, "lifetime", 1):
            self.credential = self.login()
        with self.websocket() as ws:
            self.bind(ws)
            self.output(ws, b"READY")
            ws.send(b"flood")
            seen = 0
            while seen < OUTPUT_WINDOW:
                seen += len(ws.recv(timeout=3))
            time.sleep(1.1)
            ws.send(b"post-expiry")
            with self.assertRaises(ConnectionClosed):
                ws.recv(timeout=3)
        self.assertEqual(bytes(self.backend.input), b"flood")

    def test_bad_control_and_oversized_input_close_without_dispatch(self):
        for frame in (json.dumps({"type": "resize", "cols": 0, "rows": 24}),
                      json.dumps({"type": "ack", "bytes": OUTPUT_WINDOW + 1}),
                      b"x" * (WS_MAX_SIZE + 1)):
            with self.subTest(kind=type(frame).__name__):
                baseline = self.backend.detaches
                with self.websocket() as ws:
                    self.bind(ws)
                    if self.backend.attaches == 1:
                        self.output(ws, b"READY")
                    ws.send(frame)
                    with self.assertRaises(ConnectionClosed) as error:
                        ws.recv(timeout=3)
                    self.assertIn(error.exception.rcvd.code, (1008, 1009))
                self.wait_until(lambda: self.backend.detaches > baseline)
        self.assertEqual(self.backend.input, b"")

    def test_static_traversal_and_noncode_files_are_not_served(self):
        (self.cwd / "secret.txt").write_text("private")
        for path in ("/secret.txt", "/../secret.txt", "/vendor/../../secret.txt"):
            self.assertEqual(self.request("GET", path)[0], 404)
        external = self.cwd.parent / (self.cwd.name + "-outside.js")
        external.write_text("private")
        self.addCleanup(external.unlink)
        (self.cwd / "outside.js").symlink_to(external)
        self.assertEqual(self.request("GET", "/outside.js")[0], 404)

    def test_logout_revokes_cookie_and_current_transport(self):
        with self.websocket() as ws:
            self.bind(ws)
            self.output(ws, b"READY")
            status, _, body = self.request("POST", "/logout")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body), {"locked": True})
            with self.assertRaises(ConnectionClosed):
                ws.recv(timeout=3)
        self.wait_until(lambda: self.backend.detaches == 1)
        self.assertFalse(self.backend.owner.closed)
        self.assertEqual(self.request("GET", "/api/state")[0], 401)

    def test_pairing_token_cannot_be_replayed_or_used_as_api_credentials(self):
        self.assertEqual(self.request("POST", "/auth", "token=" + self.access.token, cookie=False,
            headers={"Content-Type": "application/x-www-form-urlencoded"})[0], 403)
        self.assertEqual(self.request("GET", "/api/state",
            headers={"Authorization": "Bearer " + self.access.token})[0], 401)

    def test_logout_does_not_claim_success_when_persistent_revocation_fails(self):
        with patch.object(self.app.state.pairing_store, "revoke", side_effect=OSError("fixture failure")):
            status, _, body = self.request("POST", "/logout")
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body), {"error": "authentication-storage-unavailable"})


if __name__ == "__main__":
    unittest.main()
