"""Failure hints do not leak stderr or establish remote dispatch/lifecycle.

These tests use recorded-format synthetic messages only. No SSH is invoked.
"""
import json
import unittest

from botainer_dashboard.ssh_diagnostics import (
    SshRequestUnavailable, classify_remote_helper_failure, classify_ssh_failure, connection_diagnostic,
)


class SshDiagnosticTests(unittest.TestCase):
    def test_common_ssh_failures_get_distinct_fixed_recovery_hints(self):
        cases = (
            (b'user@example.invalid: Permission denied (publickey,keyboard-interactive).\r\n', 'ssh-authentication-required'),
            # Keyboard-interactive-only refusal with a synthetic identity.
            (b'user@example.invalid: Permission denied (keyboard-interactive).\r\n', 'ssh-authentication-required'),
            (b'@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\n@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @\nHost key verification failed.\n', 'ssh-host-key-changed'),
            (b'No ED25519 host key is known for example.invalid and you have requested strict checking.\nHost key verification failed.\n', 'ssh-host-key-unverified'),
            (b'ssh: Could not resolve hostname example.invalid: nodename nor servname provided, or not known\n', 'ssh-name-resolution-failed'),
            (b'ssh: connect to host example.invalid port 22: Connection refused\n', 'ssh-connection-refused'),
            (b'ssh: connect to host example.invalid port 22: No route to host\n', 'ssh-network-unreachable'),
            (b'ssh: connect to host example.invalid port 22: Network is unreachable\n', 'ssh-network-unreachable'),
            (b'ssh: connect to host example.invalid port 22: Operation timed out\n', 'ssh-timeout'),
            (b'Connection closed by example.invalid port 22\n', 'ssh-connection-lost'),
            (b'mux_client_request_session: read from master failed: Broken pipe\n', 'ssh-connection-lost'),
        )
        for stderr, expected in cases:
            with self.subTest(expected=expected, stderr=stderr):
                code = classify_ssh_failure({'transport_status': 'complete', 'returncode': 255}, stderr)
                self.assertEqual(code, expected)
                public = connection_diagnostic(code)
                self.assertEqual(set(public), {'code', 'message', 'recovery'})
                self.assertNotIn('example.invalid', json.dumps(public))
                self.assertTrue(all(isinstance(value, str) and len(value) <= 512 for value in public.values()))

    def test_remote_helper_errors_are_not_mislabeled_as_ssh_authentication(self):
        stderr = b'user@example.invalid: Permission denied (publickey).\n'
        for returncode in (0, 1, 2, 126, 127, None):
            with self.subTest(returncode=returncode):
                self.assertEqual(classify_ssh_failure({'transport_status': 'complete', 'returncode': returncode}, stderr),
                                 'remote-check-failed')
        self.assertEqual(classify_ssh_failure({'transport_status': 'complete', 'returncode': 255},
                                             b'project permission denied: /private/project\n'), 'ssh-request-failed')

    def test_timeout_and_start_failure_do_not_infer_dispatch_from_stderr(self):
        for status, expected in (('timeout', 'ssh-timeout'), ('start-error', 'ssh-local-client-unavailable'),
                                 ('stdout-limit', 'remote-check-failed'), ('interrupted', 'remote-check-failed')):
            code = classify_ssh_failure({'transport_status': status, 'returncode': 255, 'remote_outcome': 'unknown'},
                                        b'user@example.invalid: Permission denied (publickey).\n')
            self.assertEqual(code, expected)
            public = connection_diagnostic(code)
            self.assertNotIn('remote_outcome', public)
            self.assertNotIn('retry', public)
        self.assertIn('do not resubmit', connection_diagnostic('ssh-timeout')['recovery'])

    def test_untrusted_control_sequences_private_paths_and_unknown_codes_never_escape(self):
        secret = '/private/project/token-secret'
        stderr = ('\x1b]52;c;PRIVATE\x07\n' + secret + '\nuser@example.invalid: Permission denied (publickey).\n').encode()
        code = classify_ssh_failure({'transport_status': 'complete', 'returncode': 255}, stderr)
        self.assertEqual(code, 'ssh-authentication-required')
        public = json.dumps(connection_diagnostic(code))
        for forbidden in (secret, 'PRIVATE', 'example.invalid', '\\u001b', '\\u0007'):
            self.assertNotIn(forbidden, public)
        self.assertEqual(connection_diagnostic(secret)['code'], 'remote-check-failed')

    def test_parsing_is_bounded_and_never_decodes_arbitrary_objects(self):
        after_bound = b'x' * 65536 + b'\nHost key verification failed.\n'
        result = {'transport_status': 'complete', 'returncode': 255}
        self.assertEqual(classify_ssh_failure(result, after_bound), 'ssh-request-failed')
        self.assertEqual(classify_ssh_failure(result, object()), 'ssh-request-failed')
        self.assertEqual(classify_ssh_failure(result, b'\xff\xfe'), 'ssh-request-failed')

    def test_helper_envelope_distinguishes_installation_failures_with_fixed_text(self):
        for code in ('remote-source-changed', 'remote-launcher-changed', 'remote-interpreter-changed',
                     'remote-plugins-changed', 'remote-installation-unsafe', 'remote-import-layout-unsupported',
                     'remote-check-failed'):
            with self.subTest(code=code):
                raw = json.dumps({'protocol': 'botainer-dashboard.remote-error', 'version': 1, 'code': code}).encode()
                actual = classify_remote_helper_failure({'transport_status': 'complete', 'returncode': 2}, raw)
                self.assertEqual(actual, code)
                public = connection_diagnostic(actual)
                self.assertEqual(public['code'], code)
                self.assertTrue(all(isinstance(value, str) and len(value) <= 512 for value in public.values()))
                self.assertNotIn('retry', public)

    def test_installation_recovery_points_to_review_without_promising_plugin_approval(self):
        for code in ('remote-source-changed', 'remote-launcher-changed', 'remote-interpreter-changed'):
            hint = connection_diagnostic(code)
            self.assertIn('SSH reached the remote helper', hint['message'])
            self.assertIn('Settings → Machines → Review Botainer update', hint['recovery'])
        plugins = connection_diagnostic('remote-plugins-changed')
        self.assertIn('through machine setup', plugins['recovery'])
        self.assertIn('does not approve changes', plugins['recovery'])
        unsafe = connection_diagnostic('remote-installation-unsafe')
        self.assertIn('Do not disable verification', unsafe['recovery'])
        self.assertNotIn('chmod', unsafe['recovery'])

    def test_helper_envelope_cannot_classify_success_partial_transport_or_terminal_text(self):
        value = {'protocol': 'botainer-dashboard.remote-error', 'version': 1, 'code': 'remote-source-changed'}
        raw = json.dumps(value).encode()
        for status, code in (('complete', 0), ('complete', 255), ('complete', 1), ('complete', True),
                             ('complete', 2.0), ('timeout', 2), ('stdout-limit', 2), ('start-error', 2)):
            self.assertIsNone(classify_remote_helper_failure({'transport_status': status, 'returncode': code}, raw))
        result = {'transport_status': 'complete', 'returncode': 2}
        malformed = [b'', raw + raw, b'login banner\n' + raw, raw + b'\nterminal output', b'\xff', object(),
                     json.dumps({**value, 'path': '/private/example'}).encode(),
                     json.dumps({**value, 'code': '/private/example'}).encode(),
                     json.dumps({**value, 'code': ['remote-source-changed']}).encode(),
                     json.dumps({**value, 'version': True}).encode(),
                     json.dumps({**value, 'version': 1.0}).encode(),
                     json.dumps({**value, 'protocol': 'different-protocol'}).encode(),
                     b'{"protocol":"botainer-dashboard.remote-error","version":1,"version":1,"code":"remote-source-changed"}',
                     b' ' * 513 + raw, b'[' * 250 + b']' * 250]
        for body in malformed:
            with self.subTest(body=repr(body)[:80]):
                self.assertIsNone(classify_remote_helper_failure(result, body))

    def test_hints_do_not_change_public_errors_or_share_mutable_state(self):
        error = SshRequestUnavailable('cluster-status-unavailable-inspect-private-receipt', 'ssh-authentication-required')
        self.assertEqual(error.code, 'cluster-status-unavailable-inspect-private-receipt')
        hint = connection_diagnostic(error.diagnostic_code)
        hint['message'] = 'untrusted'
        self.assertNotEqual(connection_diagnostic(error.diagnostic_code)['message'], 'untrusted')
        self.assertIn('system terminal', connection_diagnostic(error.diagnostic_code)['recovery'])
        self.assertIn('does not accept', connection_diagnostic('ssh-host-key-unverified')['recovery'])


if __name__ == '__main__':
    unittest.main()
