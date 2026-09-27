import unittest

from botainer_dashboard.access import AccessDenied, LoopbackAccess


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.policy = LoopbackAccess.create(47123)

    def request(self, **changes):
        values = dict(host=self.policy.authority, origin=self.policy.origin,
                      token=self.policy.token, require_origin=True)
        self.policy.authorize(**dict(values, **changes))

    def test_correct_same_origin_action_and_originless_read(self):
        self.request()
        self.request(origin=None, require_origin=False)

    def test_secret_is_required_even_for_read(self):
        for value in (None, "", "wrong", "x" * len(self.policy.token), "é" * len(self.policy.token)):
            with self.subTest(value=value):
                with self.assertRaisesRegex(AccessDenied, "invalid-credential"):
                    self.request(token=value, origin=None, require_origin=False)

    def test_wrong_host_rebinding_and_alternate_names_rejected(self):
        for host in ("attacker.example:47123", "localhost:47123", "127.0.0.1:47124",
                     "127.0.0.1:47123.attacker.example", "127.0.0.1:47123@attacker.example",
                     "127.0.0.1", "[::1]:47123", None):
            with self.subTest(host=host):
                with self.assertRaisesRegex(AccessDenied, "invalid-host"):
                    self.request(host=host)

    def test_wrong_null_and_missing_origin_rejected_for_write_or_websocket(self):
        for origin in (None, "null", "", "https://attacker.example",
                       "http://127.0.0.1:47124", "http://localhost:47123",
                       self.policy.origin + "/", self.policy.origin + ".attacker.example"):
            with self.subTest(origin=origin):
                with self.assertRaisesRegex(AccessDenied, "invalid-origin"):
                    self.request(origin=origin)

    def test_cross_origin_read_rejected_even_with_credential(self):
        with self.assertRaisesRegex(AccessDenied, "invalid-origin"):
            self.request(origin="https://attacker.example", require_origin=False)

    def test_rotation_and_error_output_do_not_reuse_or_expose_secret(self):
        other = LoopbackAccess.create(self.policy.port)
        self.assertNotEqual(other.token, self.policy.token)
        self.assertNotIn(self.policy.token, repr(self.policy))
        with self.assertRaises(AccessDenied) as result:
            other.authorize(host=other.authority, origin=other.origin,
                            token=self.policy.token, require_origin=True)
        self.assertNotIn(self.policy.token, str(result.exception))

    def test_invalid_service_configuration_refused(self):
        for port in (True, 0, -1, 65536, "1234"):
            with self.assertRaises(ValueError):
                LoopbackAccess.create(port)
        with self.assertRaises(ValueError):
            LoopbackAccess(47123, "weak")


if __name__ == "__main__":
    unittest.main()
