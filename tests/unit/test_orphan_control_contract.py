"""Feed actual backend DTOs into production frontend action consumers.

Only inert fixture owners/inventory and the already-installed Node.js run.
The service routing contract is exercised separately in service integration tests.
"""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

import test_ordinary_local as fixture_module


ROOT = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")
CONSUME = r"""
import {readFileSync} from 'node:fs';
import {sessionControls, sessionGuidance, sessionGuidanceButton,
        sessionStopPresentation, sessionStopTarget} from './frontend/app.js';
const {snapshot, sid} = JSON.parse(readFileSync(0, 'utf8'));
const session = snapshot.sessions.find(s => s.runtimeId === sid && s.kind !== 'launch');
if (!session) throw new Error('Backend did not expose the selected runtime');
const controls = sessionControls(snapshot, session);
const guidance = sessionGuidance(session, {viewState: 'detached',
  canAttach: controls.attach, canStop: controls.stop});
const presentation = sessionStopPresentation(snapshot, session,
  snapshot.projects.find(p => p.id === session.projectId));
let target = null;
let refusal = null;
try { target = sessionStopTarget(snapshot, session); }
catch (error) { refusal = error.message; }
process.stdout.write(JSON.stringify({controls, guidance,
  button: sessionGuidanceButton(guidance, {stopLabel: presentation.label}),
  presentation, target, refusal}));
"""


@unittest.skipUnless(NODE, "Cross-module control checks require the existing Node.js tool")
class OrphanFrontendContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.BackendTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.backend = self.fixture.backend

    def consume(self):
        self.backend._last = 0
        snapshot = self.backend.snapshot()
        result = subprocess.run([NODE, "--input-type=module", "-e", CONSUME],
            input=json.dumps({"snapshot": snapshot, "sid": fixture_module.SID}),
            text=True, capture_output=True, cwd=ROOT, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return snapshot, json.loads(result.stdout)

    def launch(self, *, ended):
        self.fixture.durable_owner()
        project = self.backend.snapshot()["projects"][0]
        self.backend.start_session(project["id"], fixture_module.REQUEST)
        self.fixture.owner_receipt({"phase": "before-dispatch" if ended else "runtime-returned",
            "session_id": fixture_module.SID,
            **({} if ended else {"binding": self.fixture.binding()})})
        self.backend.owner.observe.return_value = {"state": "ended" if ended else "running"}
        self.fixture.raw["sessions"] = [self.fixture.running()]

    def test_ended_owner_dto_reaches_explicit_container_stop_confirmation(self):
        self.launch(ended=True)
        _snapshot, view = self.consume()
        self.assertEqual(view["controls"], {"attach": False, "stop": True})
        self.assertEqual(view["button"], {
            "control": "stop-session", "label": "Stop leftover container", "danger": True})
        self.assertEqual(view["target"], {"contextNamespace": self.backend.namespace,
            "runtimeId": fixture_module.SID, "expectedStopMode": "orphan-container"})
        self.assertIsNone(view["refusal"])
        self.assertIn(fixture_module.CID, view["presentation"]["description"])
        self.backend.owner.attach.assert_not_called()
        self.assertTrue(all(request["action"] == "inventory" for request in self.fixture.requests))

    def test_live_original_owner_dto_does_not_escalate_to_orphan_action(self):
        self.launch(ended=False)
        _snapshot, view = self.consume()
        self.assertEqual(view["controls"], {"attach": True, "stop": True})
        self.assertEqual(view["button"]["control"], "connect")
        self.assertEqual(view["presentation"]["label"], "Stop session")
        self.assertEqual(view["target"]["contextNamespace"], self.backend.namespace)
        self.assertEqual(view["target"]["runtimeId"], fixture_module.SID)
        self.assertEqual(view["target"].get("expectedStopMode", "normal"), "normal")

    def test_external_runtime_dto_never_becomes_a_verified_orphan_owner(self):
        self.fixture.raw["sessions"] = [self.fixture.running()]
        snapshot, view = self.consume()
        session = next(s for s in snapshot["sessions"] if s["runtimeId"] == fixture_module.SID)
        self.assertEqual(session["controlRestriction"], "external-terminal-owner-unverified")
        self.assertEqual(view["controls"], {"attach": False, "stop": False})
        self.assertNotEqual(session.get("stopMode"), "orphan-container")
        self.assertEqual(view["guidance"]["title"], "Running elsewhere · use the original terminal")
        self.assertIsNone(view["target"])
        self.assertIsNone(view["button"])
        self.assertIsNotNone(view["refusal"])
        self.assertTrue(all(request["action"] == "inventory" for request in self.fixture.requests))


if __name__ == "__main__":
    unittest.main()
