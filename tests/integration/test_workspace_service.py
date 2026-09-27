"""Actual authenticated HTTP workspace boundaries; temporary files, no Botainer.

Only config semantics use a deterministic validator. Directory containment,
exact text/revision handling, middleware and HTTP parsing are production code.
"""

import http.client
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from uuid import uuid4

import uvicorn

from botainer_dashboard.access import LoopbackAccess
from botainer_dashboard.local_backend import DashboardConfigEditor
from botainer_dashboard.service import COOKIE_NAME, create_app
from botainer_dashboard.workspace import Workspace, WorkspaceInstallation, WorkspaceRoot


class WorkspaceFixture:
    def __init__(self, root):
        self.calls = []
        self.allowed = True
        projects = root / "projects"
        projects.mkdir()
        self.workspace = Workspace(private_root=root / "private", roots=[WorkspaceRoot("local", "Temporary projects", projects)],
            installations=[WorkspaceInstallation("fixture", "HTTP fixture", self.validator, "version: config-v1\n")],
            can_write=lambda _project: self.allowed)
        self.config_path = root / "dashboard.json"
        self.config_path.write_text('{"version": 1}\n')
        self.editor = DashboardConfigEditor(self.config_path)

    @staticmethod
    def validator(text, previous):
        return {"valid": not text.startswith("invalid"), "errors": ["Fixture refusal"] if text.startswith("invalid") else [], "warnings": []}

    def snapshot(self):
        return {"mode": "http-workspace-fixture", "projects": self.workspace.projects(), "sessions": []}

    def workspace_metadata(self):
        self.calls.append("metadata")
        return self.workspace.metadata()

    def create_project(self, data):
        self.calls.append("create")
        return {"project": self.workspace.create_project(mode=data["mode"], root_id=data["rootId"], path=data["path"],
            name=data["name"], installation_id=data["installationId"], request_id=data["requestId"])}

    def open_host_project(self, project, workspace, request):
        self.calls.append(("open-host", project, workspace, request))
        return {"project": {"id": "host-project", "workspaceId": workspace, "executionKind": "host"}}

    def read_terminal_history(self, namespace, runtime):
        self.calls.append(("history", namespace, runtime))
        return {"text": "Literal terminal output <script>\n", "kind": "terminal-snapshot"}

    def list_files(self, project, path):
        self.calls.append("files")
        return self.workspace.list_files(project, path)

    def read_file(self, project, path):
        self.calls.append("file")
        return self.workspace.read_file(project, path)

    def read_config(self, project):
        self.calls.append("config")
        return self.workspace.read_config(project)

    def validate_config(self, project, text, revision):
        self.calls.append("validate")
        return self.workspace.validate_config(project, text, revision)

    def save_config(self, project, text, revision):
        self.calls.append("save")
        return self.workspace.save_config(project, text, revision)

    def dashboard_config(self):
        self.calls.append("dashboard-read")
        return self.editor.read()

    def validate_dashboard_config(self, text, revision):
        self.calls.append("dashboard-validate")
        return self.editor.validate(text, revision)

    def save_dashboard_config(self, text, revision):
        self.calls.append("dashboard-save")
        return self.editor.save(text, revision)


@unittest.skipUnless(os.name == "posix", "Directory descriptors required")
class WorkspaceServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="dashboard-workspace-http-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.backend = WorkspaceFixture(self.root)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.access = LoopbackAccess.create(self.sock.getsockname()[1])
        self.app = create_app(access=self.access, backend=self.backend, static_root=self.root, vendor_root=self.root)
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.access.port,
            access_log=False, log_level="critical", proxy_headers=False, ws="none", timeout_graceful_shutdown=2))
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [self.sock]}, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)
        deadline = time.monotonic() + 3
        while not self.server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.server.started)
        self.cookie = self.bearer = ""
        status, headers, body = self.request("POST", "/auth", f"token={self.access.token}",
            headers={"Content-Type": "application/x-www-form-urlencoded"}, authenticate=False)
        self.assertEqual(status, 200, body)
        self.bearer = json.loads(body)["bearer"]
        cookie = SimpleCookie()
        cookie.load(headers["set-cookie"])
        name = f"{COOKIE_NAME}_{self.access.port}"
        self.cookie = f"{name}={cookie[name].value}"

    def shutdown(self):
        self.server.should_exit = True
        self.thread.join(4)
        self.sock.close()
        self.assertFalse(self.thread.is_alive())

    def request(self, method, path, body=None, *, headers=None, authenticate=True, origin=True):
        selected = {"Content-Type": "application/json"}
        if authenticate:
            selected.update(Cookie=self.cookie, Authorization="Bearer " + self.bearer)
        if origin:
            selected["Origin"] = self.access.origin
        selected.update(headers or {})
        if isinstance(body, dict):
            body = json.dumps(body, ensure_ascii=True).encode("utf-8")
        connection = http.client.HTTPConnection("127.0.0.1", self.access.port, timeout=3)
        try:
            connection.request(method, path, body, selected)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def post(self, path, body, expected=200):
        status, _headers, raw = self.request("POST", path, body)
        self.assertEqual(status, expected, raw)
        return json.loads(raw)

    def get(self, path, expected=200):
        status, _headers, raw = self.request("GET", path)
        self.assertEqual(status, expected, raw)
        return json.loads(raw)

    def create(self):
        return self.post("/api/projects", {"mode": "create", "rootId": "local", "path": "sample", "name": "Sample",
                          "installationId": "fixture", "requestId": str(uuid4())})["project"]

    def test_every_workspace_route_requires_cookie_and_independent_bearer(self):
        routes = [("GET", "/api/workspace"), ("POST", "/api/projects"),
                  ("POST", "/api/sessions/sample/history"),
                  ("POST", "/api/projects/sample/open-host"),
                  ("POST", "/api/projects/sample/files"), ("POST", "/api/projects/sample/file"),
                  ("GET", "/api/projects/sample/config"), ("POST", "/api/projects/sample/config/validate"),
                  ("POST", "/api/projects/sample/config/save"), ("GET", "/api/dashboard/config"),
                  ("POST", "/api/dashboard/config/validate"), ("POST", "/api/dashboard/config/save")]
        for method, path in routes:
            with self.subTest(method=method, path=path):
                self.assertEqual(self.request(method, path, {}, authenticate=False)[0], 401)
                self.assertEqual(self.request(method, path, {}, headers={"Authorization": ""})[0], 401)
        self.assertEqual(self.backend.calls, [])

    def test_terminal_history_requires_exact_text_target_and_origin(self):
        route = "/api/sessions/session-1/history"
        body = {"contextNamespace": "local:namespace"}
        for headers, origin in (({"Origin": "http://127.0.0.1:1"}, True), ({}, False)):
            self.assertEqual(self.request("POST", route, body, headers=headers, origin=origin)[0], 403)
        for value in ({}, body | {"path": "/other"}, body | {"input": "yes"},
                      {"contextNamespace": []}, {"contextNamespace": "../../other"},
                      {"contextNamespace": "\ud800"},
                      '{"contextNamespace":"one","contextNamespace":"two"}'):
            with self.subTest(value=value):
                self.post(route, value, 400)
        self.post("/api/sessions/bad%20id/history", body, 400)
        self.assertEqual(self.backend.calls, [])
        result = self.post(route, body)
        self.assertEqual(result, {"text": "Literal terminal output <script>\n", "kind": "terminal-snapshot"})
        self.assertEqual(self.backend.calls, [("history", "local:namespace", "session-1")])

    def test_mutations_reject_wrong_or_missing_origin_before_dispatch(self):
        body = {"mode": "create", "rootId": "local", "path": "sample", "name": "Sample",
                "installationId": "fixture", "requestId": str(uuid4())}
        for headers, origin in (({"Origin": "http://127.0.0.1:1"}, True), ({}, False),
                                ({"Host": "localhost:" + str(self.access.port)}, True)):
            self.assertEqual(self.request("POST", "/api/projects", body, headers=headers, origin=origin)[0], 403)
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(self.backend.workspace.projects(), [])

    def test_open_host_resolves_only_identities_and_checks_origin_before_dispatch(self):
        route = "/api/projects/source-project/open-host"
        body = {"workspaceId": "host-workspace", "requestId": str(uuid4())}
        for headers, origin in (({"Origin": "http://127.0.0.1:1"}, True), ({}, False)):
            self.assertEqual(self.request("POST", route, body, headers=headers, origin=origin)[0], 403)
        for value in (body | {"path": "/unexpected"}, body | {"agent": "claude"},
                      body | {"workspaceId": []}, {"workspaceId": "host-workspace"},
                      body | {"workspaceId": "../../elsewhere"},
                      '{"workspaceId":"one","workspaceId":"two","requestId":"x"}'):
            self.post(route, value, 400)
        self.assertEqual(self.backend.calls, [])
        result = self.post(route, body)
        self.assertEqual(result["project"]["workspaceId"], "host-workspace")
        self.assertEqual(self.backend.calls, [("open-host", "source-project", "host-workspace", body["requestId"])])

    def test_unknown_missing_duplicate_and_nonstrings_fields_never_dispatch(self):
        body = {"mode": "create", "rootId": "local", "path": "sample", "name": "Sample",
                "installationId": "fixture", "requestId": str(uuid4())}
        cases = [body | {"shell": "whoami"}, {key: value for key, value in body.items() if key != "mode"},
                 body | {"name": []}, body | {"requestId": "bad request id"},
                 '{"path":"first","path":"second"}']
        for value in cases:
            with self.subTest(value=value):
                self.post("/api/projects", value, 400)
        self.post("/api/projects/sample/files", {"path": "", "host": "/"}, 400)
        self.post("/api/dashboard/config/save", {"text": "{}", "revision": "x"}, 400)
        self.assertEqual(self.backend.calls, [])

    def test_body_limits_wrong_content_type_invalid_utf8_and_deep_json_are_bounded(self):
        self.post("/api/projects", b" " * 4097, 400)
        self.post("/api/dashboard/config/validate", b" " * (128 * 1024 + 1), 400)
        self.post("/api/projects", b'{"name":"\xff"}', 400)
        self.post("/api/projects", ('{"nested":' + '[' * 1500 + '0' + ']' * 1500 + '}').encode(), 400)
        self.assertEqual(self.request("POST", "/api/projects", "{}", headers={"Content-Type": "text/plain"})[0], 400)
        self.assertEqual(self.backend.calls, [])

    def test_unpaired_unicode_surrogate_is_rejected_before_any_mutation(self):
        body = {"mode": "create", "rootId": "local", "path": "sample", "name": "\ud800",
                "installationId": "fixture", "requestId": str(uuid4())}
        self.post("/api/projects", body, 400)
        self.post("/api/dashboard/config/validate", {"text": "\ud800", "revision": "x"}, 400)
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(self.backend.workspace.projects(), [])

    def test_real_folder_and_text_access_stays_inside_registered_project(self):
        project = self.create()
        directory = Path(project["path"])
        (directory / "README.md").write_text("<script>literal data</script>\nUnicode λ\n")
        (directory / ".env").write_text("PRIVATE=value")
        (directory / "outside").symlink_to(self.root / "dashboard.json")
        prefix = "/api/projects/" + project["id"]
        listing = self.post(prefix + "/files", {"path": ""})
        self.assertEqual([item["name"] for item in listing["entries"]], ["README.md"])
        content = self.post(prefix + "/file", {"path": "README.md"})
        self.assertEqual(content["text"], (directory / "README.md").read_text())
        for path in ("../dashboard.json", ".env", ".botainer/config.yaml", "outside"):
            with self.subTest(path=path):
                result = self.post(prefix + "/file", {"path": path}, 409)
                self.assertIn("error", result)

    def test_config_validation_and_save_preserve_text_and_reject_stale_revision(self):
        project = self.create()
        prefix = "/api/projects/" + project["id"] + "/config"
        before = self.get(prefix)
        text = "# Exact comments retained.\nversion: config-v1\n\n"
        self.assertTrue(self.post(prefix + "/validate", {"text": text, "revision": before["revision"]})["valid"])
        saved = self.post(prefix + "/save", {"text": text, "revision": before["revision"], "requestId": str(uuid4())})
        self.assertTrue(saved["saved"])
        self.assertEqual(self.get(prefix)["text"], text)
        conflict = self.post(prefix + "/save", {"text": "stale", "revision": before["revision"], "requestId": str(uuid4())}, 409)
        self.assertEqual(conflict["error"], "config-revision-conflict")
        self.assertEqual(self.get(prefix)["text"], text)

    def test_rejected_or_active_config_does_not_overwrite_saved_text(self):
        project = self.create()
        prefix = "/api/projects/" + project["id"] + "/config"
        before = self.get(prefix)
        body = {"text": "invalid candidate", "revision": before["revision"], "requestId": str(uuid4())}
        self.assertFalse(self.post(prefix + "/save", body)["saved"])
        self.backend.allowed = False
        self.post(prefix + "/save", body | {"text": "valid candidate"}, 409)
        self.assertEqual(self.get(prefix)["text"], before["text"])

    def test_dashboard_config_uses_validated_exact_text_and_restart_marker(self):
        before = self.get("/api/dashboard/config")
        text = '{\n "version": 1,\n "machines": {"local": {}}\n}\n'
        valid = self.post("/api/dashboard/config/validate", {"text": text, "revision": before["revision"]})
        self.assertTrue(valid["valid"])
        result = self.post("/api/dashboard/config/save", {"text": text, "revision": before["revision"], "requestId": str(uuid4())})
        self.assertTrue(result["saved"])
        self.assertTrue(result["restartRequired"])
        self.assertEqual(self.backend.config_path.read_text(), text)
        self.post("/api/dashboard/config/save", {"text": '{"version":1}', "revision": before["revision"], "requestId": str(uuid4())}, 409)


if __name__ == "__main__":
    unittest.main()
