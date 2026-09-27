import test from 'node:test';
import assert from 'node:assert/strict';
import { sessionControls, sessionGuidance, sessionGuidanceButton, sessionStopPresentation, sessionStopTarget } from '../../frontend/app.js';
import { TerminalViewRegistry } from '../../frontend/terminal-registry.js';

const project = { id: 'project-one', name: 'Project one', workspaceId: 'local' };
const session = { contextNamespace: 'ordinary:one', runtimeId: 'native-session-one', projectId: project.id,
  workspaceId: 'local', state: 'running', containerId: 'c'.repeat(64), containerName: '/botainer-example',
  stopMode: 'orphan-container', stopScope: 'container', controlRestriction: 'original-owner-ended',
  capabilities: { stopSession: true, attachTerminal: false } };
const snapshot = { capabilities: {}, projects: [project], sessions: [session],
  workspaces: [{ id: 'local', kind: 'local', status: 'available', capabilities: { stopSession: true, attachTerminal: true } }] };

test('a selected orphan exposes Stop without creating any terminal connection', () => {
  let transports = 0;
  const registry = new TerminalViewRegistry({
    createTerminal: () => ({ write() {}, setVisible() {}, setInputEnabled() {}, resize() {}, focus() {}, dispose() {} }),
    createTransport: () => { transports++; throw new Error('Selecting an orphan must not connect.'); },
  });
  try {
    const view = registry.show(session);
    assert.equal(view.state, 'detached');
    const controls = sessionControls(snapshot, session);
    assert.deepEqual(controls, { attach: false, stop: true });
    const guidance = sessionGuidance(session, { viewState: view.state, canAttach: controls.attach, canStop: controls.stop });
    const stop = sessionStopPresentation(snapshot, session, project);
    assert.deepEqual(sessionGuidanceButton(guidance, { stopLabel: stop.label, viewState: view.state }),
      { control: 'stop-session', label: 'Stop leftover container', danger: true });
    assert.equal(transports, 0);
  } finally { registry.disposeAll(); }
});

test('orphan Stop confirmation identifies the exact container and explains interruption and cleanup limits', () => {
  const stop = sessionStopPresentation(snapshot, session, project);
  assert.equal(stop.label, 'Stop leftover container');
  for (const identity of [session.runtimeId, project.name, session.containerId, session.containerName]) {
    assert.ok(stop.description.includes(identity));
  }
  assert.match(stop.description, /ends all work inside this exact container/);
  assert.match(stop.description, /may interrupt an agent that is still working/);
  assert.match(stop.description, /original Botainer launcher has ended/);
  assert.match(stop.description, /Credential-helper cleanup may remain unconfirmed/);
  assert.match(stop.result, /requested.*refreshed inventory/);
  assert.match(stop.result, /cleanup may remain unconfirmed/);
});

test('orphan metadata alone cannot grant Stop or recover terminal attachment', () => {
  for (const capabilities of [undefined, {}, { stopSession: false }, { stopSession: 'true' }]) {
    const denied = { ...session, capabilities };
    assert.deepEqual(sessionControls(snapshot, denied), { attach: false, stop: false });
    assert.equal(sessionGuidance(denied, { viewState: 'detached', canStop: false }).action, null);
  }
  assert.deepEqual(sessionControls(snapshot, { ...session, capabilities: { attachTerminal: true, stopSession: true } }),
    { attach: false, stop: true });
  assert.deepEqual(sessionControls(snapshot, { ...session, stopMode: undefined }), { attach: false, stop: false });
  assert.deepEqual(sessionControls(snapshot, { ...session, stopScope: 'allocation' }), { attach: false, stop: false });
});

test('stale, busy, ended and ambiguous orphan states never offer a control', () => {
  for (const options of [{ connected: false }, { busy: true }]) {
    assert.deepEqual(sessionControls(snapshot, session, options), { attach: false, stop: false });
  }
  const offline = { ...snapshot, workspaces: [{ ...snapshot.workspaces[0], status: 'unavailable' }] };
  assert.deepEqual(sessionControls(offline, session), { attach: false, stop: false });
  for (const state of ['stopped', 'failed', 'unknown']) {
    assert.deepEqual(sessionControls(snapshot, { ...session, state }), { attach: false, stop: false });
  }
  const ambiguous = { ...session, controlRestriction: 'orphan-stop-unconfirmed' };
  assert.deepEqual(sessionControls(snapshot, ambiguous), { attach: false, stop: false });
  const stale = sessionGuidance(session, { viewState: 'detached', canStop: true, serviceConnected: false });
  assert.equal(sessionGuidanceButton(stale), null);
  assert.match(stale.title, /may be stale/);
});

test('ended-launcher guidance distinguishes a running container from agent health or user permissions', () => {
  for (const viewState of ['detached', 'disconnected', 'connected']) {
    const guidance = sessionGuidance(session, { viewState, canStop: true });
    assert.equal(guidance.title, 'Container running · launcher ended');
    assert.match(guidance.message, /not that its agent is working/);
    assert.match(guidance.message, /Reconnecting cannot recover the ended launcher/);
    assert.match(guidance.message, /cleanup may remain unconfirmed/);
    assert.doesNotMatch(guidance.message, /permission|agent has stopped/i);
    assert.equal(guidance.action, 'stop');
  }
});

test('unverified external terminals remain observable with independently qualified Stop, never takeover', () => {
  const external = { ...session, stopMode: undefined, stopScope: undefined, controlRestriction: 'external-terminal-owner-unverified' };
  const controls = sessionControls(snapshot, external);
  assert.deepEqual(controls, { attach: false, stop: true });
  const guidance = sessionGuidance(external, { viewState: 'detached', canStop: controls.stop });
  assert.equal(guidance.title, 'Running elsewhere · use the original terminal');
  assert.match(guidance.message, /Continue in the terminal where you started it/);
  assert.match(guidance.message, /not supported yet/);
  assert.match(guidance.message, /closing that terminal does not enable attachment/);
  assert.doesNotMatch(guidance.message, /permission|agent has stopped/i);
  assert.deepEqual(sessionGuidanceButton(guidance, { stopLabel: sessionStopPresentation(snapshot, external).label }),
    { control: 'stop-session', label: 'Stop session', danger: true });
  assert.equal(sessionGuidance(external, { viewState: 'detached', canStop: false }).action, null);
  assert.equal(sessionControls(snapshot, { ...external, capabilities: { attachTerminal: true, stopSession: false } }).attach, false);
  assert.deepEqual(sessionControls(snapshot, { ...external, capabilities: undefined }), { attach: false, stop: false });
});

test('Stop confirmation rechecks target identity, mode, capability and service availability before dispatch', () => {
  const target = sessionStopTarget(snapshot, session);
  assert.deepEqual(target, { contextNamespace: session.contextNamespace, runtimeId: session.runtimeId, expectedStopMode: 'orphan-container' });
  assert.equal(Object.isFrozen(target), true);
  for (const mutation of [
    { contextNamespace: 'ordinary:other' }, { runtimeId: 'different-session' }, { projectId: 'different-project' },
    { workspaceId: 'other-machine' }, { containerId: 'd'.repeat(64) }, { stopMode: undefined }, { jobId: 'changed-job' },
    { stopScope: 'allocation' }, { state: 'stopped' }, { capabilities: { stopSession: false } },
    { controlRestriction: 'orphan-stop-unconfirmed' },
  ]) {
    const changed = { ...snapshot, sessions: [{ ...session, ...mutation }] };
    assert.throws(() => sessionStopTarget(changed, session), /Session control changed/);
  }
  assert.throws(() => sessionStopTarget({ ...snapshot, sessions: [] }, session), /Session control changed/);
  for (const options of [{ connected: false }, { busy: true }]) {
    assert.throws(() => sessionStopTarget(snapshot, session, options), /Session control changed/);
  }
  const normal = { ...session, stopMode: undefined, stopScope: undefined, controlRestriction: undefined };
  assert.throws(() => sessionStopTarget(snapshot, normal), /Session control changed/);
  assert.equal(sessionStopTarget({ ...snapshot, sessions: [normal] }, normal).expectedStopMode, 'normal');
  const allocation = { ...normal, stopScope: 'allocation', jobId: '12345' };
  assert.equal(sessionStopTarget({ ...snapshot, sessions: [allocation] }, allocation).expectedStopMode, 'normal');
  assert.throws(() => sessionStopTarget({ ...snapshot, sessions: [{ ...allocation, jobId: '67890' }] }, allocation), /Session control changed/);
});

test('new guidance dispatch keeps ordinary connect, launch logs and new-session actions distinct', () => {
  assert.deepEqual(sessionGuidanceButton({ action: 'connect' }, { viewState: 'disconnected' }),
    { control: 'connect', label: 'Reconnect terminal', danger: false });
  assert.deepEqual(sessionGuidanceButton({ action: 'log' }, { setup: true }),
    { control: 'connect', label: 'View setup log', danger: false });
  assert.deepEqual(sessionGuidanceButton({ action: 'start' }),
    { control: 'new-session', label: 'Start a new session', danger: false });
  assert.equal(sessionGuidanceButton({ action: null }), null);
});
