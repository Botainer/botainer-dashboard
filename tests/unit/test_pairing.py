"""Authentication persistence with temporary private state; no listener required."""
import json
import os
import shlex
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

from botainer_dashboard.access import AccessDenied
from botainer_dashboard import launcher
from botainer_dashboard.launcher import authentication_profile, remembered_port
from botainer_dashboard.pairing import (
    BROWSER_LIFETIME, MAX_BROWSERS, PAIRING_LIFETIME, PairingStore, write_private_json,
    read_private_json,
)


class PairingTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / "auth" / "browsers.json"
        self.now = 1000000.0
        self.origin = "http://127.0.0.1:50522"
        self.token = "A" * 43

    def store(self, **changes):
        store = PairingStore(**dict({"origin": self.origin, "token": self.token,
                                     "state_path": self.state, "clock": lambda: self.now}, **changes))
        self.addCleanup(store.close)
        return store

    def test_runtime_read_size_override_does_not_broaden_authentication_default(self):
        large = self.root / 'journal.json'
        write_private_json(large, {'text': 'x' * 20000})
        with self.assertRaisesRegex(ValueError, 'too large'):
            read_private_json(large)
        self.assertEqual(len(read_private_json(large, max_bytes=32768)['text']), 20000)
        for limit in (True, 0, 2*1024*1024+1):
            with self.assertRaises(ValueError): read_private_json(large, max_bytes=limit)
        large.unlink()
        os.mkfifo(large,0o600)
        with self.assertRaisesRegex(ValueError, 'private regular file'):
            read_private_json(large)

    def test_token_is_single_use_and_neither_browser_secret_works_alone(self):
        store = self.store()
        with self.assertRaises(AccessDenied):
            store.pair("B" * 43)
        grant = store.pair(self.token)
        self.assertTrue(store.authenticated(grant.cookie, grant.bearer))
        for cookie, bearer in (("", grant.bearer), (grant.cookie, ""), (grant.cookie, grant.cookie),
                               (grant.bearer, grant.bearer), (grant.cookie, "B" * 43)):
            self.assertFalse(store.authenticated(cookie, bearer))
        with self.assertRaises(AccessDenied):
            store.pair(self.token)
        self.assertEqual(grant.lifetime, BROWSER_LIFETIME)

    def test_pairing_token_expires_without_expiring_existing_browser(self):
        store = self.store()
        grant = store.pair(self.token)
        store.issue_token("B" * 43)
        self.now += PAIRING_LIFETIME
        with self.assertRaises(AccessDenied):
            store.pair("B" * 43)
        self.assertTrue(store.authenticated(grant.cookie, grant.bearer))
        self.now = grant.expires
        self.assertFalse(store.authenticated(grant.cookie, grant.bearer))

    def test_restart_preserves_pair_and_expiry_without_persisting_secrets(self):
        first = self.store()
        grant = first.pair(self.token)
        raw = self.state.read_text()
        for secret in (self.token, grant.cookie, grant.bearer):
            self.assertNotIn(secret, raw)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.state.parent.stat().st_mode & 0o777, 0o700)
        first.close()
        self.now += 100
        second = self.store(token="B" * 43)
        self.assertTrue(second.authenticated(grant.cookie, grant.bearer))
        self.assertEqual(second.credential_for(grant.cookie).expires, grant.expires)
        other = second.pair("B" * 43)
        self.assertFalse(second.authenticated(grant.cookie, other.bearer))
        second.revoke(grant.cookie)
        second.close()
        third = self.store(token="C" * 43)
        self.assertFalse(third.authenticated(grant.cookie, grant.bearer))
        self.assertTrue(third.authenticated(other.cookie, other.bearer))

    def test_saved_state_cannot_be_reused_for_different_origin(self):
        first = self.store()
        first.pair(self.token)
        first.close()
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.store(origin="http://127.0.0.1:50523")

    def test_authentication_file_is_exclusive_and_lock_releases(self):
        first = self.store()
        with self.assertRaises(BlockingIOError):
            self.store()
        first.close()
        self.store()

    def test_rejects_symlinks_permissions_hard_links_and_malformed_state(self):
        first = self.store()
        first.pair(self.token)
        first.close()
        original = self.state.read_text()
        self.state.chmod(0o644)
        with self.assertRaises(ValueError):
            self.store()
        self.state.chmod(0o600)
        outside = self.root / "outside.json"
        os.link(self.state, outside)
        with self.assertRaises(ValueError):
            self.store()
        outside.unlink()
        self.state.unlink()
        outside.write_text(original)
        outside.chmod(0o600)
        self.state.symlink_to(outside)
        with self.assertRaises(OSError):
            self.store()
        self.state.unlink()
        self.state.write_text('{"version":1,"version":1}')
        self.state.chmod(0o600)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.store()

    def test_rejects_public_directory_and_symlink_ancestry(self):
        self.state.parent.mkdir(mode=0o755)
        with self.assertRaisesRegex(ValueError, "owner-only"):
            self.store()
        alternate = self.root / "link"
        alternate.symlink_to(self.state.parent, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            self.store(state_path=alternate / "nested" / "browsers.json")

    def test_failed_revocation_write_disables_all_current_authentication(self):
        store = self.store()
        grant = store.pair(self.token)
        with patch("botainer_dashboard.pairing.write_private_json", side_effect=OSError("fixture failure")):
            with self.assertRaises(OSError):
                store.revoke(grant.cookie)
        self.assertFalse(store.authenticated(grant.cookie, grant.bearer))
        self.assertFalse(store.can_issue_token())
        self.assertFalse(store.token_available(self.token))
        with self.assertRaisesRegex(ValueError, "storage is unavailable"):
            store.issue_token("B" * 43)
        with self.assertRaises(AccessDenied):
            store.pair("B" * 43)

    def test_capacity_is_bounded_and_expiry_reclaims_it(self):
        store = self.store(lifetime=10)
        for _ in range(MAX_BROWSERS):
            store.issue_token(self.token)
            store.pair(self.token)
        self.assertFalse(store.can_issue_token())
        store.issue_token(self.token)
        with self.assertRaisesRegex(AccessDenied, "capacity"):
            store.pair(self.token)
        self.now += 10
        self.assertTrue(store.can_issue_token())
        store.pair(self.token)
        self.assertEqual(len(json.loads(self.state.read_text())["browsers"]), 1)

    def test_owner_inspection_distinguishes_usable_spent_and_expired_codes(self):
        store = self.store()
        self.assertTrue(store.can_issue_token())
        self.assertTrue(store.token_available(self.token))
        self.assertEqual(store.token_expires_at, self.now + PAIRING_LIFETIME)
        self.assertFalse(store.token_available("B" * 43))
        self.assertFalse(self.state.exists())
        grant = store.pair(self.token)
        self.assertFalse(store.token_available(self.token))
        store.issue_token("B" * 43)
        self.assertTrue(store.token_available("B" * 43))
        self.now += PAIRING_LIFETIME
        self.assertFalse(store.token_available("B" * 43))
        self.assertTrue(store.authenticated(grant.cookie, grant.bearer))

    def test_launcher_profiles_and_saved_origins_are_separate(self):
        first = authentication_profile(self.root, self.root / "one.json", "proof")
        self.assertEqual(first, authentication_profile(self.root, self.root / "one.json", "proof"))
        self.assertNotEqual(first, authentication_profile(self.root, self.root / "two.json", "proof"))
        self.assertNotEqual(first, authentication_profile(self.root, self.root / "one.json", "workspace"))
        self.assertEqual(remembered_port(first), 0)
        write_private_json(first / "endpoint.json", {"version": 1, "port": 50522})
        self.assertEqual(remembered_port(first), 50522)
        write_private_json(first / "endpoint.json", {"version": 1, "port": True})
        with self.assertRaises(ValueError):
            remembered_port(first)

    def test_launcher_uses_resolved_environment_and_default_config_for_pairing(self):
        # Exercise main's actual config selection and private profile writes,
        # replacing only the listener/server boundary. No runtime dependencies
        # or actual sockets are needed for this offline wiring check.
        project = self.root / "project with spaces"
        assets = ("xterm/lib/xterm.js", "xterm/css/xterm.css",
                  "addon-fit/lib/addon-fit.js", "addon-search/lib/addon-search.js")
        for name in assets:
            asset = project / ".local/frontend/vendor" / name
            asset.parent.mkdir(parents=True, exist_ok=True)
            asset.write_text("/* inert fixture asset */")
        environment_config = self.root / "environment.json"
        default_config = self.root / "xdg/botainer-dashboard/config.json"
        default_config.parent.mkdir(parents=True)
        for config_file in (environment_config, default_config):
            config_file.write_text('{"version":1}')

        server_module = ModuleType("uvicorn")
        server_module.Config = Mock()
        server_module.Server = Mock()
        service_module = ModuleType("botainer_dashboard.service")
        service_module.create_app = Mock()
        service_module.WS_MAX_SIZE = 16384
        service_module.WS_MAX_QUEUE = 4
        listener = Mock()
        listener.getsockname.return_value = ("127.0.0.1", 50522)
        profiles = []
        cases = (({"BOTAINER_DASHBOARD_CONFIG": str(environment_config)}, environment_config),
                 ({"XDG_CONFIG_HOME": str(self.root / "xdg")}, default_config))
        for index, (environment, expected_config) in enumerate(cases):
            # First use the existing-runtime fallback. Then provide a complete
            # public bundle as well and verify it takes precedence.
            if index:
                for name in assets:
                    asset = project / "frontend/vendor" / name
                    asset.parent.mkdir(parents=True, exist_ok=True)
                    asset.write_text("/* inert distributed fixture asset */")
            (project / ".local").chmod(0o700)
            expected_vendor = project / ("frontend/vendor" if index else ".local/frontend/vendor")
            with self.subTest(source=expected_config.name):
                with patch.dict(os.environ, environment, clear=True), \
                     patch.dict(sys.modules, {"uvicorn": server_module,
                                              "botainer_dashboard.service": service_module}), \
                     patch.object(launcher, "resource_root", return_value=project), \
                     patch.object(launcher, "runtime_valid", return_value=True), \
                     patch.object(sys, "prefix", str(project / ".local/envs/botainer_dashboard")), \
                     patch.object(launcher.socket, "socket", return_value=listener), \
                     patch.object(launcher.os, "umask"), \
                     patch("builtins.print") as printed, \
                     patch.object(launcher, "authentication_profile", wraps=authentication_profile) as profile:
                    launcher.main([])
                    profile.assert_called_once_with(project, expected_config, "registrations", data_dir=project / ".local")
                    self.assertEqual(service_module.create_app.call_args.kwargs["vendor_root"], expected_vendor)
                    control = service_module.create_app.call_args.kwargs["pairing_control"]
                    lines = [call.args[0] for call in printed.call_args_list]
                    self.assertIn(f"Private pairing file: {control.access_file}", lines)
                    pairing_command = next(line.strip() for line in lines if " pair --instance " in line)
                    self.assertEqual(shlex.split(pairing_command), [sys.executable, "-I", "-B",
                        str(project / "tools/dashboard.py"), "pair", "--instance", control.run_dir.name])
                    startup_code = service_module.create_app.call_args.kwargs["access"].token
                    self.assertNotIn(startup_code, "\n".join(lines))
                    self.assertTrue(control._closed)
                    actual = authentication_profile(project, expected_config, "registrations")
                    self.assertEqual(remembered_port(actual), 50522)
                    profiles.append(actual)
        self.assertNotEqual(*profiles)
        self.assertEqual(server_module.Server.return_value.run.call_count, 2)
        self.assertEqual(listener.close.call_count, 2)


if __name__ == "__main__":
    unittest.main()
