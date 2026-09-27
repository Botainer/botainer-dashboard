"""Actual ASGI/auth boundaries and durable selection, without sockets or SSH."""
import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from botainer_dashboard.connections import ConnectionRegistry
from botainer_dashboard.access import LoopbackAccess

try:
    from botainer_dashboard.connection_routes import ConnectionManager
    from botainer_dashboard.service import COOKIE_NAME, create_app
except ModuleNotFoundError as error:
    if error.name not in {"fastapi", "starlette"}:
        raise
    create_app = None


def remote():
    names = ("pyproject.toml", "botainer/plugins/lifecycle.py", "botainer/cli/main.py",
             "botainer/cli/hpc.py", "botainer/cli/plugin.py", "botainer/plugins/manifest.py",
             "plugins/hpc-launcher/host_helper/submit.py", "plugins/hpc-launcher/host_helper/_common.py")
    return {"version": 1, "id": "site", "label": "Example cluster", "ssh_alias": "example",
            "remote_python": "/opt/example/bin/python", "remote_python_sha256": "a" * 64,
            "source_root": "/opt/example/source", "state_root": "/home/example/.botainer",
            "launcher": {"path": "/home/example/bin/botainer", "sha256": "b" * 64},
            "source_sha256": {name: "c" * 64 for name in names}, "state_plugin_sha256": {},
            "control_root": "/home/example/.dashboard", "project_roots": {"work": "/home/example/projects"}}


@unittest.skipIf(create_app is None, "requires the existing approved dashboard runtime")
class ConnectionRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.registry = ConnectionRegistry(self.root / "connections.json")
        self.registry.initialize()
        self.prepare = Mock(return_value={"profile": None, "checks": [{"status": "blocked"}]})
        self.manager = ConnectionManager(self.registry, prepare=self.prepare)
        self.backend = Mock()
        self.access = LoopbackAccess.create(57432)
        self.app = create_app(access=self.access, backend=self.backend, static_root=self.root,
                              vendor_root=self.root, docs_root=self.root, connection_manager=self.manager)
        self.grant = self.app.state.pairing_store.pair(self.access.token)
        self.auth = {"cookie": f"{COOKIE_NAME}_{self.access.port}={self.grant.cookie}",
                     "authorization": "Bearer " + self.grant.bearer}

    async def request(self, path, *, method="GET", data=None, raw=None, headers=None, auth=True):
        body = raw if raw is not None else json.dumps(data).encode() if data is not None else b""
        combined = {"host": self.access.authority, "origin": self.access.origin,
                    "content-type": "application/json"}
        if auth:
            combined.update(self.auth)
        combined.update(headers or {})
        encoded = [(key.encode(), value.encode()) for key, value in combined.items() if value is not None]
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
                 "http_version": "1.1", "method": method, "scheme": "http", "path": path,
                 "raw_path": path.encode(), "query_string": b"", "root_path": "",
                 "headers": encoded, "server": ("127.0.0.1", self.access.port), "client": ("127.0.0.1", 1234)}
        sent, delivered = [], False

        async def receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            await asyncio.Event().wait()

        async def send(message):
            sent.append(message)

        await asyncio.wait_for(self.app(scope, receive, send), timeout=5)
        start = next(message for message in sent if message["type"] == "http.response.start")
        response = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
        return start["status"], json.loads(response)

    async def test_every_setup_route_requires_cookie_and_independent_bearer(self):
        paths = [("GET", "/api/connections", None),
                 ("GET", "/api/connections/help/ssh", None),
                 ("GET", "/api/connections/help/status", None),
                 ("GET", "/api/connections/help/alpha-testing", None),
                 ("POST", "/api/connections/prepare", {"request": {}}),
                 ("POST", "/api/connections", {"kind": "remote", "profile": remote(), "revision": 0, "trusted": True}),
                 ("POST", "/api/connections/site/enabled", {"enabled": False, "revision": 0}),
                 ("POST", "/api/connections/site/remove", {"confirmed": True, "revision": 0}),
                 ("POST", "/api/connections/site/agent-update/prepare", {"agent": "codex", "path": "/opt/example/codex", "revision": 0}),
                 ("POST", "/api/connections/site/agent-update", {"agent": "codex", "path": "/opt/example/codex",
                    "sha256": "a" * 64, "revision": 0, "confirmed": True}),
                 ("GET", "/api/connections/site/installation-update", None),
                 ("POST", "/api/connections/site/installation-update/prepare", {'revision': 0,
                    'profileDigest': 'a' * 64, 'changes': {}, 'acknowledged': True}),
                 ("POST", "/api/connections/site/installation-update", {'revision': 0,
                    'profileDigest': 'a' * 64, 'changes': {}, 'acknowledged': True,
                    'candidateDigest': 'b' * 64, 'confirmed': True})]
        for method, path, data in paths:
            for credentials in ({}, {"cookie": self.auth["cookie"]}, {"authorization": self.auth["authorization"]}):
                with self.subTest(path=path, credentials=list(credentials)):
                    status, _ = await self.request(path, method=method, data=data, auth=False, headers=credentials)
                    self.assertEqual(status, 401)
        self.prepare.assert_not_called()
        self.assertEqual(self.registry.read()["revision"], 0)
        self.backend.assert_has_calls([])

    async def test_cross_origin_or_wrong_host_cannot_prepare_or_save(self):
        for headers in ({"origin": "http://127.0.0.1:1234"}, {"origin": None}, {"host": "localhost:57432"}):
            status, _ = await self.request("/api/connections/prepare", method="POST", data={"request": {}}, headers=headers)
            self.assertEqual(status, 403)
            for suffix, data in (("agent-update/prepare", {"agent": "codex", "path": "/opt/example/codex", "revision": 0}),
                                 ("agent-update", {"agent": "codex", "path": "/opt/example/codex", "sha256": "a" * 64,
                                                   "revision": 0, "confirmed": True}),
                                 ('installation-update/prepare', {'revision': 0, 'profileDigest': 'a' * 64,
                                      'changes': {}, 'acknowledged': True}),
                                 ('installation-update', {'revision': 0, 'profileDigest': 'a' * 64,
                                      'changes': {}, 'acknowledged': True, 'candidateDigest': 'b' * 64,
                                      'confirmed': True})):
                status, _ = await self.request("/api/connections/site/" + suffix, method="POST", data=data, headers=headers)
                self.assertEqual(status, 403)
        self.prepare.assert_not_called()
        self.assertEqual(self.registry.read()["revision"], 0)

    async def test_botainer_update_reinspects_exact_candidate_and_preserves_active_selection(self):
        saved = self.registry.add('remote', remote(), 0, trusted=True)
        self.manager.active = saved['entries']
        old = saved['entries'][0]
        old_path = Path(old['profilePath']); original = old_path.read_bytes()
        profile = {**remote(), 'source_root': '/opt/example/source-updated'}
        raw = (json.dumps(profile, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode()
        digest = hashlib.sha256(raw).hexdigest()
        observed = {'kind': 'remote', 'profile': profile, 'candidate_sha256': digest,
                    'checks': [], 'summary': [], 'instructions': [], 'requires_restart': True,
                    'requires_pairing': True}
        body = {'revision': saved['revision'], 'profileDigest': old['profileDigest'],
                'changes': {'source_root': profile['source_root']}, 'acknowledged': True}
        with patch('botainer_dashboard.installation_update.prepare_installation_update', return_value=observed) as inspect, \
                patch('subprocess.Popen', side_effect=AssertionError('No runtime action in selection persistence')):
            status, settings = await self.request('/api/connections/site/installation-update')
            self.assertEqual(status, 200)
            self.assertEqual(settings['settings']['source_root'], remote()['source_root'])
            inspect.assert_not_called()
            status, review = await self.request('/api/connections/site/installation-update/prepare', method='POST', data=body)
            self.assertEqual(status, 200, review)
            self.assertEqual(review['candidateDigest'], digest)
            self.assertEqual(self.registry.read(), saved)
            status, updated = await self.request('/api/connections/site/installation-update', method='POST',
                data={**body, 'candidateDigest': digest, 'confirmed': True})
            self.assertEqual(status, 200, updated)
            self.assertEqual(inspect.call_count, 2)
        self.assertTrue(updated['pendingRestart'])
        self.assertEqual(updated['active'], saved['entries'])
        self.assertEqual(updated['revision'], saved['revision'] + 1)
        self.assertEqual(old_path.read_bytes(), original)
        self.assertEqual(self.backend.mock_calls, [])

    async def test_botainer_update_rejects_unreviewed_injected_and_raced_requests(self):
        saved = self.registry.add('remote', remote(), 0, trusted=True)
        old = saved['entries'][0]; original = self.registry.path.read_bytes()
        body = {'revision': 1, 'profileDigest': old['profileDigest'], 'changes': {},
                'acknowledged': True, 'candidateDigest': 'a' * 64, 'confirmed': True}
        with patch('botainer_dashboard.installation_update.prepare_installation_update') as inspect:
            for bad in ({**body, 'confirmed': False}, {**body, 'acknowledged': False},
                        {**body, 'revision': True}, {**body, 'profile': remote()},
                        {**body, 'candidateDigest': 3}):
                status, _ = await self.request('/api/connections/site/installation-update', method='POST', data=bad)
                self.assertEqual(status, 400)
            inspect.assert_not_called()
            observed = {'profile': {**remote(), 'source_root': '/opt/example/new'}, 'candidate_sha256': 'b' * 64}
            inspect.return_value = observed
            status, value = await self.request('/api/connections/site/installation-update', method='POST', data=body)
            self.assertEqual(status, 409)
            self.assertEqual(value['code'], 'connections-installation-changed')
        self.assertEqual(self.registry.path.read_bytes(), original)

    async def test_botainer_update_concurrent_selection_is_not_overwritten(self):
        saved = self.registry.add('remote', remote(), 0, trusted=True)
        body = {'revision': 1, 'profileDigest': saved['entries'][0]['profileDigest'],
                'changes': {}, 'acknowledged': True}
        def inspection(*args):
            self.registry.enable('site', False, 1)
            return {'profile': remote(), 'candidate_sha256': 'a' * 64}
        with patch('botainer_dashboard.installation_update.prepare_installation_update', side_effect=inspection):
            status, value = await self.request('/api/connections/site/installation-update/prepare', method='POST', data=body)
        self.assertEqual(status, 409)
        self.assertEqual(value['code'], 'connections-revision-conflict')
        self.assertFalse(self.registry.read()['entries'][0]['enabled'])

    async def test_add_is_pending_remove_retains_profile_and_never_calls_runtime(self):
        with patch("subprocess.Popen", side_effect=AssertionError("selection must not execute")):
            status, value = await self.request("/api/connections", method="POST",
                data={"kind": "remote", "profile": remote(), "revision": 0, "trusted": True})
            self.assertEqual(status, 200)
            self.assertTrue(value["pendingRestart"])
            self.assertEqual(value["active"], [])
            profile_path = Path(value["entries"][0]["profilePath"])
            original = profile_path.read_bytes()
            self.manager.active = value["entries"]
            status, value = await self.request("/api/connections/site/remove", method="POST",
                data={"revision": 1, "confirmed": True})
            self.assertEqual(status, 200)
            self.assertEqual(value["entries"], [])
            self.assertEqual(value["active"][0]["id"], "site")
            self.assertTrue(value["pendingRestart"])
            self.assertEqual(profile_path.read_bytes(), original)
        self.assertEqual(self.backend.mock_calls, [])
        self.prepare.assert_not_called()

    async def test_compare_and_swap_rejects_stale_remove_without_partial_change(self):
        self.registry.add("remote", remote(), 0, trusted=True)
        before = self.registry.path.read_bytes()
        status, value = await self.request("/api/connections/site/remove", method="POST",
            data={"revision": 0, "confirmed": True})
        self.assertEqual(status, 409)
        self.assertEqual(value["code"], "connections-revision-conflict")
        self.assertEqual(self.registry.path.read_bytes(), before)

    async def test_unreviewed_or_malformed_add_cannot_write(self):
        valid = {"kind": "remote", "profile": remote(), "revision": 0, "trusted": True}
        for data in ({**valid, "trusted": False}, {**valid, "revision": True},
                     {**valid, "shell": "touch unexpected"}, {**valid, "profile": "not-a-profile"}):
            status, _ = await self.request("/api/connections", method="POST", data=data)
            self.assertIn(status, (400, 409))
        status, _ = await self.request("/api/connections", method="POST", raw=b'{"revision":0,"revision":1}')
        self.assertEqual(status, 400)
        self.assertEqual(self.registry.read()["entries"], [])

    async def test_help_allowlist_returns_plain_text_and_refuses_symlink_and_oversize(self):
        for ident, name, title in (("ssh", "ssh-setup.md", "SSH setup and troubleshooting"),
                                   ("status", "status.md", "Alpha status and limits"),
                                   ("alpha-testing", "alpha-testing.md", "Try the limited alpha")):
            with self.subTest(guide=ident):
                guide = self.root / name
                guide.write_text("# Guide\n<script>untrusted markup stays data</script>\n")
                status, value = await self.request("/api/connections/help/" + ident)
                self.assertEqual(status, 200)
                self.assertEqual(value, {"title": title, "text": guide.read_text()})
                guide.unlink()
                guide.symlink_to(self.registry.path)
                self.assertEqual((await self.request("/api/connections/help/" + ident))[0], 404)
                guide.unlink()
                guide.write_bytes(b"x" * (256 * 1024 + 1))
                self.assertEqual((await self.request("/api/connections/help/" + ident))[0], 404)
        for name in ("unknown", "ssh-setup.md", "status.md", "connections.json"):
            self.assertEqual((await self.request("/api/connections/help/" + name))[0], 404)

    async def test_preparation_is_explicit_and_failed_io_does_not_expose_private_error(self):
        self.prepare.side_effect = OSError("private key path and host name must stay private")
        status, value = await self.request("/api/connections/prepare", method="POST", data={"request": {"kind": "remote"}})
        self.assertEqual(status, 503)
        self.assertNotIn("private key path", json.dumps(value))
        self.assertEqual(self.registry.read()["entries"], [])

    async def test_disabled_setup_routes_are_inert_and_still_allow_authenticated_help(self):
        self.app = create_app(access=self.access, backend=self.backend, static_root=self.root,
            vendor_root=self.root, docs_root=self.root)
        grant = self.app.state.pairing_store.pair(self.access.token)
        self.auth = {"cookie": f"{COOKIE_NAME}_{self.access.port}={grant.cookie}", "authorization": "Bearer " + grant.bearer}
        self.assertFalse((await self.request("/api/connections"))[1]["enabled"])
        self.assertEqual((await self.request("/api/connections/prepare", method="POST", data={"request": {}}))[0], 409)
        self.prepare.assert_not_called()

    async def test_disable_changes_next_selection_without_mutating_active_snapshot(self):
        saved = self.registry.add("remote", remote(), 0, trusted=True)
        self.manager.active = [dict(saved["entries"][0])]
        status, value = await self.request("/api/connections/site/enabled", method="POST",
            data={"enabled": False, "revision": 1})
        self.assertEqual(status, 200)
        self.assertTrue(value["pendingRestart"])
        self.assertTrue(value["active"][0]["enabled"])
        self.assertEqual(self.registry.selected_profiles()["remote_profiles"], ())
        self.assertEqual(self.backend.mock_calls, [])

    def host_profile(self):
        # Short private control path meets the Unix socket bound independently
        # of platform temporary-directory prefixes.
        short = tempfile.TemporaryDirectory(prefix="bd-api-", dir="/private/tmp" if Path("/private/tmp").is_dir() else "/tmp")
        self.addCleanup(short.cleanup)
        home = Path(short.name).resolve()
        control, projects = home / "c", home / "projects"
        control.mkdir(mode=0o700)
        projects.mkdir(mode=0o700)
        pins = {}
        for name in ("tmux", "codex", "codex-updated"):
            path = home / name
            path.write_text("Inert fixture " + name + "; never executed\n")
            path.chmod(0o700)
            pins[name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        profile = {"version": 1, "id": "host", "label": "Host fixture", "home": str(home),
                   "tmux": pins["tmux"], "control_root": str(control), "project_roots": {"work": str(projects)},
                   "agents": {"codex": pins["codex"]}, "default_agent": "codex", "environment": {"PATH": "/usr/bin:/bin"}}
        return profile, pins

    async def test_reviewed_host_update_applies_next_restart_without_runtime_or_profile_mutation(self):
        profile, pins = self.host_profile()
        saved = self.registry.add("host", profile, 0, trusted=True)
        original = Path(saved["entries"][0]["profilePath"])
        original_bytes = original.read_bytes()
        self.manager.active = [dict(saved["entries"][0])]
        candidate = {"agent": "codex", "path": pins["codex-updated"]["path"], "revision": 1}
        with patch("subprocess.Popen", side_effect=AssertionError("review must not execute")):
            status, review = await self.request("/api/connections/host/agent-update/prepare", method="POST", data=candidate)
            self.assertEqual(status, 200)
            self.assertEqual(review["current"], pins["codex"])
            self.assertEqual(review["candidate"], pins["codex-updated"])
            self.assertTrue(review["changed"])
            self.assertEqual(self.registry.read(), saved)
            status, updated = await self.request("/api/connections/host/agent-update", method="POST",
                data={**candidate, "sha256": review["candidate"]["sha256"], "confirmed": True})
            self.assertEqual(status, 200)
        self.assertEqual(updated["revision"], 2)
        self.assertTrue(updated["pendingRestart"])
        self.assertEqual(updated["active"][0]["profileDigest"], saved["entries"][0]["profileDigest"])
        self.assertNotEqual(updated["entries"][0]["profileDigest"], saved["entries"][0]["profileDigest"])
        self.assertEqual(original.read_bytes(), original_bytes)
        self.assertEqual(self.backend.mock_calls, [])
        self.prepare.assert_not_called()

    async def test_agent_update_requires_exact_fields_confirmation_and_current_revision(self):
        profile, pins = self.host_profile()
        self.registry.add("host", profile, 0, trusted=True)
        before = self.registry.path.read_bytes()
        valid = {"agent": "codex", "path": pins["codex-updated"]["path"],
                 "sha256": pins["codex-updated"]["sha256"], "revision": 1, "confirmed": True}
        for data in ({**valid, "confirmed": False}, {**valid, "confirmed": 1}, {**valid, "revision": True},
                     {**valid, "revision": 0}, {**valid, "sha256": "0" * 64},
                     {**valid, "agent": "claude"}, {**valid, "environment": {"PATH": "/tmp"}}):
            with self.subTest(data=data):
                status, _ = await self.request("/api/connections/host/agent-update", method="POST", data=data)
                self.assertIn(status, (400, 409))
                self.assertEqual(self.registry.path.read_bytes(), before)
        for data in ({"agent": "codex", "path": pins["codex-updated"]["path"], "revision": True},
                     {"agent": "codex", "path": pins["codex-updated"]["path"], "revision": 1, "shell": "ignored"}):
            status, _ = await self.request("/api/connections/host/agent-update/prepare", method="POST", data=data)
            self.assertEqual(status, 400)
        self.assertEqual(self.backend.mock_calls, [])


if __name__ == "__main__":
    unittest.main()
