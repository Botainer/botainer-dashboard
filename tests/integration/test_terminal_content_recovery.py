"""Opt-in content recovery checks against a reviewed existing local tmux.

Only inert Python fixtures and private temporary files are used. This does not
exercise Botainer, containers, agent credentials, browser rendering or SSH.
"""
import os
import time
import unittest

import test_ordinary_owner as owner_fixture


CHILD = r'''
from pathlib import Path
import time
print("CONTENT_FIXTURE_READY", flush=True)
for stage, count in (("short", 200), ("long", 2500)):
    while not Path(stage).exists():
        time.sleep(.02)
    for number in range(count):
        print("CONTENT_%s_%04d" % (stage, number), flush=True)
    print("CONTENT_%s_DONE" % stage, flush=True)
while not Path("exit").exists():
    time.sleep(.02)
print("CONTENT_FIXTURE_FINAL", flush=True)
'''


@unittest.skipUnless(os.environ.get("BOTAINER_DASHBOARD_TEST_TMUX"),
                     "requires explicit reviewed existing tmux path")
class TerminalContentRecoveryTests(unittest.TestCase):
    def setUp(self):
        # Reuse the proven fixture ownership/cleanup helpers, without inheriting
        # its separate lifecycle test cases into this suite.
        self.fixture = owner_fixture.OrdinaryOwnerIntegrationTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)

    def wait_capture(self, marker):
        fixture = self.fixture
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            output = fixture.owner.capture(fixture.record, allow_running=True)
            if marker in output:
                return output
            time.sleep(.02)
        self.fail("retained inert fixture output did not reach marker")

    def test_disconnected_output_is_snapshot_history_not_replayed_attach_stream(self):
        fixture = self.fixture
        record = fixture.launch(CHILD)
        owner_identity = dict(record["owner"])
        first = fixture.attach()
        fixture.read_until(first, b"CONTENT_FIXTURE_READY")
        first.close()

        (fixture.root / "short").touch()
        captured = self.wait_capture(b"CONTENT_short_DONE")
        self.assertIn(b"CONTENT_short_0000", captured)
        self.assertIn(b"CONTENT_short_0199", captured)
        self.assertEqual(fixture.owner.observe(record)["state"], "running")

        fixture.owner = fixture.make_owner()
        fixture.record = fixture.owner.recover(fixture.operation_id)
        self.assertEqual(fixture.record["owner"], owner_identity)
        second = fixture.attach()
        attach_output = fixture.read_until(second, b"CONTENT_short_DONE")
        self.assertNotIn(b"CONTENT_short_0000", attach_output,
                         "reattach should redraw the screen, not replay all historical output")
        self.assertIn(b"CONTENT_short_0000", fixture.owner.capture(fixture.record, allow_running=True))
        second.close()

        (fixture.root / "long").touch()
        captured = self.wait_capture(b"CONTENT_long_DONE")
        self.assertNotIn(b"CONTENT_short_0000", captured)
        self.assertNotIn(b"CONTENT_long_0000", captured)
        self.assertIn(b"CONTENT_long_2499", captured)
        # Current capture reports byte-tail truncation only. tmux's history row
        # eviction is not represented by an omission marker or exact count.
        self.assertLess(len(captured), 262144)
        self.assertFalse(captured.startswith(b"[Earlier terminal output omitted.]"))

        (fixture.root / "exit").touch()
        captured = self.wait_capture(b"CONTENT_FIXTURE_FINAL")
        self.assertIn(b"CONTENT_long_2499", captured)

    def test_failed_viewer_creation_preserves_real_owner_and_unseen_output(self):
        fixture = self.fixture
        record = fixture.launch()
        identity = dict(record["owner"])
        original_factory = fixture.owner.attachment_factory

        def refused_viewer(*_args, **_kwargs):
            raise OSError("synthetic PTY allocation failure")

        fixture.owner.attachment_factory = refused_viewer
        with self.assertRaisesRegex(OSError, "synthetic PTY allocation failure"):
            fixture.attach()
        self.assertEqual(fixture.owner.observe(record)["state"], "running")
        self.assertEqual(fixture.owner.recover(fixture.operation_id)["owner"], identity)
        self.assertIn(b"CLI_FIXTURE_READY", self.wait_capture(b"CLI_FIXTURE_READY"))
        fixture.owner.attachment_factory = original_factory
        attached = fixture.attach()
        fixture.read_until(attached, b"CLI_FIXTURE_READY:" + str(identity["panePid"]).encode())
        fixture.write(attached, b"PID\r")
        fixture.read_until(attached, b"CLI_FIXTURE_PID:" + str(identity["panePid"]).encode())


if __name__ == "__main__":
    unittest.main()
