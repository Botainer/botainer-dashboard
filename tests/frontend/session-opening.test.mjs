import test from 'node:test';
import assert from 'node:assert/strict';
import { projectCurrentSessions, openSessionView } from '../../frontend/app.js';
import { TerminalViewRegistry, TerminalViewCapacityError } from '../../frontend/terminal-registry.js';

const key = target => JSON.stringify([target.contextNamespace, target.runtimeId]);
const session = overrides => ({ contextNamespace: 'example:local:state', runtimeId: 'same-id',
  projectId: 'local-project', workspaceId: 'local', state: 'running', ...overrides });
const inventory = sessions => ({
  workspaces: [
    { id: 'local', status: 'available', capabilities: { attachTerminal: true } },
    { id: 'remote', status: 'available', capabilities: { attachTerminal: true } },
  ],
  projects: [{ id: 'local-project', workspaceId: 'local' }, { id: 'remote-project', workspaceId: 'remote' }],
  sessions,
});

// Real view ownership with inert adapters. This exercises the user-activation
// helper, not the browser DOM, WebSocket service or a running Botainer agent.
function harness(t, { maxViews = 8, immediate = true } = {}) {
  const renderers = new Map(), transports = [], selected = [];
  const registry = new TerminalViewRegistry({ maxViews,
    createTerminal(target, { onInput }) {
      const renderer = { focusCount: 0, output: [], onInput,
        write(data) { this.output.push(data); }, setVisible() {}, setInputEnabled() {}, resize() {},
        focus() { this.focusCount++; }, dispose() {} };
      renderers.set(key(target), renderer); return renderer;
    },
    createTransport(target, callbacks) {
      const transport = { target, callbacks, opens: 0, closes: 0, input: [],
        open() { this.opens++; if (immediate) callbacks.onOpen(); },
        sendInput(data) { this.input.push(data); return true; }, resize() { return true; },
        close() { this.closes++; } };
      transports.push(transport); return transport;
    },
  });
  t.after(() => registry.disposeAll());
  const selectSession = current => { selected.push(current); return registry.show(current); };
  return { registry, selected, renderers, transports, selectSession,
    open(snapshot, requested, options = {}) {
      return openSessionView(snapshot, requested, { registry, selectSession, ...options });
    } };
}

test('project opening candidates contain current work only in the registered project workspace', () => {
  const running = session(), queued = session({ runtimeId: 'queued', state: 'queued' });
  const unknown = session({ runtimeId: 'unverified', state: 'unknown' });
  const launch = session({ runtimeId: 'launch', kind: 'launch', launchState: 'waiting' });
  const observations = inventory([running, queued, unknown, launch,
    session({ runtimeId: 'stopped', state: 'stopped' }),
    session({ runtimeId: 'failed', state: 'failed' }),
    session({ runtimeId: 'finished-log', kind: 'launch', launchState: 'completed', consoleEnded: true }),
    session({ runtimeId: 'wrong-workspace', workspaceId: 'remote' }),
    session({ runtimeId: 'other-project', projectId: 'remote-project', workspaceId: 'remote' }),
  ]);
  assert.deepEqual(new Set(projectCurrentSessions(observations, 'local-project')), new Set([running, queued, unknown, launch]));
  assert.deepEqual(projectCurrentSessions(observations, 'missing-project'), []);
  assert.deepEqual(projectCurrentSessions({ ...observations, projects: [] }, 'local-project'), []);
});

test('project opening candidates support an ordinary single-workspace inventory', () => {
  const current = session({ workspaceId: undefined });
  const snapshot = { capabilities: { attachTerminal: true }, projects: [{ id: current.projectId }], sessions: [current] };
  assert.deepEqual(projectCurrentSessions(snapshot, current.projectId), [current]);
});

test('one explicit open reveals and connects current verified work; repeated clicks reuse it', t => {
  const h = harness(t), current = session(), snapshot = inventory([current]);
  const first = h.open(snapshot, current);
  assert.equal(first, h.registry.get(current));
  assert.equal(first.visible, true);
  assert.equal(first.state, 'connected');
  assert.equal(h.transports.length, 1);
  assert.equal(h.transports[0].opens, 1);
  assert.equal(h.open(snapshot, current), first);
  assert.equal(h.transports.length, 1);
  assert.equal(h.renderers.get(key(current)).focusCount, 2);
  assert.equal(h.transports[0].closes, 0);
});

test('repeated clicks while connecting do not create another transport and focus only when ready', t => {
  const h = harness(t, { immediate: false }), current = session(), snapshot = inventory([current]);
  const view = h.open(snapshot, current);
  h.open(snapshot, current);
  assert.equal(view.state, 'connecting');
  assert.equal(h.transports.length, 1);
  assert.equal(h.renderers.get(key(current)).focusCount, 0);
  h.transports[0].callbacks.onOpen();
  assert.equal(view.state, 'connected');
  assert.equal(h.renderers.get(key(current)).focusCount, 1);
});

test('activation uses current metadata and permissions rather than the rendered row closure', t => {
  const h = harness(t), requested = session({ label: 'old label', capabilities: { attachTerminal: true } });
  const current = { ...requested, label: 'current label', capabilities: { attachTerminal: false } };
  const view = h.open(inventory([current]), requested);
  assert.equal(h.selected[0], current);
  assert.equal(view.visible, true);
  assert.equal(h.transports.length, 0);
});

test('unavailable, unverified and external-terminal work opens a view without attaching', async t => {
  const cases = [
    ['unknown', { state: 'unknown' }], ['queued', { state: 'queued' }], ['starting', { state: 'starting' }],
    ['ended', { state: 'stopped' }],
    ['external owner', { controlRestriction: 'external-terminal-owner-unverified' }],
    ['original owner ended', { controlRestriction: 'original-owner-ended' }],
    ['unconfirmed orphan', { controlRestriction: 'orphan-stop-unconfirmed' }],
    ['capability refused', { capabilities: { attachTerminal: false } }],
  ];
  for (const [name, overrides] of cases) await t.test(name, t => {
    const h = harness(t), current = session(overrides), view = h.open(inventory([current]), current);
    assert.equal(view.visible, true);
    assert.equal(h.selected[0], current);
    assert.equal(h.transports.length, 0);
  });
});

test('service, workspace and busy guards prevent a new connection without losing the requested view', async t => {
  for (const [name, options, unavailable] of [
    ['service disconnected', { connected: false }, false], ['busy', { busy: true }, false],
    ['workspace unavailable', {}, true],
  ]) await t.test(name, t => {
    const h = harness(t), current = session(), snapshot = inventory([current]);
    if (unavailable) snapshot.workspaces[0].status = 'unavailable';
    assert.equal(h.open(snapshot, current, options).visible, true);
    assert.equal(h.transports.length, 0);
  });
});

test('stale inventory, session or project records cannot authorize a fresh transport', async t => {
  for (const scope of ['snapshot', 'session', 'project']) await t.test(scope, t => {
    const h = harness(t), current = session(), snapshot = inventory([current]);
    if (scope === 'snapshot') snapshot.stale = true;
    else if (scope === 'session') current.stale = true;
    else snapshot.projects[0].stale = true;
    assert.equal(h.open(snapshot, current).visible, true);
    assert.equal(h.transports.length, 0);
  });
});

test('missing or changed exact identity preserves a retained view but cannot authorize attachment', async t => {
  const requested = session();
  const variants = [
    ['missing observation', []],
    ['namespace changed', [session({ contextNamespace: 'example:replacement:state' })]],
    ['runtime changed', [session({ runtimeId: 'replacement' })]],
    ['workspace changed', [session({ workspaceId: 'remote' })]],
    ['project changed', [session({ projectId: 'remote-project' })]],
  ];
  for (const [name, sessions] of variants) await t.test(name, t => {
    const h = harness(t), view = h.open(inventory(sessions), requested);
    assert.equal(view.visible, true);
    assert.equal(h.selected[0], requested);
    assert.equal(h.transports.length, 0);
  });
});

test('a session without matching current project membership cannot connect', async t => {
  for (const [name, projects] of [
    ['project removed', []], ['project moved to another workspace', [{ id: 'local-project', workspaceId: 'remote' }]],
  ]) await t.test(name, t => {
    const h = harness(t), current = session(), snapshot = { ...inventory([current]), projects };
    assert.equal(h.open(snapshot, current).visible, true);
    assert.equal(h.transports.length, 0);
  });
});

test('same runtime IDs on two machines remain separate exact views and transports', t => {
  const h = harness(t), local = session(), remote = session({ contextNamespace: 'example:remote:state',
    workspaceId: 'remote', projectId: 'remote-project' });
  const snapshot = inventory([local, remote]);
  const a = h.open(snapshot, local), b = h.open(snapshot, remote);
  assert.notEqual(a, b);
  assert.equal(h.registry.size, 2);
  assert.equal(h.transports.length, 2);
  assert.equal(a.visible, false);
  assert.equal(b.visible, true);
  h.registry.sendInput(remote, 'remote only');
  assert.deepEqual(h.transports[0].input, []);
  assert.deepEqual(h.transports[1].input, ['remote only']);
  assert.equal(h.open(snapshot, local), a);
  assert.equal(h.transports.length, 2);
  assert.equal(h.transports.every(transport => transport.closes === 0), true);
});

test('selection failure or a wrong, hidden or unregistered view never attaches the previous selection', async t => {
  for (const outcome of ['null', 'wrong', 'hidden', 'unregistered']) await t.test(outcome, t => {
    const h = harness(t), previous = session({ runtimeId: 'previous' }), requested = session();
    const previousView = h.registry.show(previous);
    const selectSession = current => {
      if (outcome === 'null') return null;
      if (outcome === 'wrong') return previousView;
      if (outcome === 'unregistered') return { target: current, visible: true, state: 'detached' };
      const view = h.registry.show(current); h.registry.hide(current); return view;
    };
    h.open(inventory([previous, requested]), requested, { selectSession });
    assert.equal(h.transports.length, 0);
    assert.equal(previousView.state, 'detached');
  });
});

test('missing requested work does not select, create a view or attach anything', t => {
  const h = harness(t);
  assert.equal(h.open(inventory([]), null), null);
  assert.equal(h.registry.size, 0);
  assert.deepEqual(h.selected, []);
  assert.deepEqual(h.transports, []);
});

test('view capacity failure does not attach or discard another terminal', t => {
  const h = harness(t, { maxViews: 1 }), previous = session({ runtimeId: 'previous' }), requested = session();
  const snapshot = inventory([previous, requested]), previousView = h.open(snapshot, previous);
  h.transports[0].callbacks.onData('existing output');
  assert.throws(() => h.open(snapshot, requested), TerminalViewCapacityError);
  assert.equal(h.registry.get(requested), null);
  assert.equal(previousView.visible, true);
  assert.equal(previousView.state, 'connected');
  assert.equal(h.transports.length, 1);
  assert.equal(h.transports[0].closes, 0);
  assert.deepEqual(h.renderers.get(key(previous)).output, ['existing output']);
});

test('completed launch log loads once and keeps its renderer after EOF', t => {
  const h = harness(t), log = session({ kind: 'launch', launchState: 'completed', state: 'stopped', consoleEnded: true });
  const snapshot = inventory([log]), view = h.open(snapshot, log);
  assert.equal(h.transports.length, 1);
  h.transports[0].callbacks.onData('native CLI transcript');
  h.transports[0].callbacks.onClose('session_eof');
  assert.equal(h.open(snapshot, log), view);
  assert.equal(h.transports.length, 1);
  assert.deepEqual(h.renderers.get(key(log)).output, ['native CLI transcript']);
  assert.equal(h.renderers.get(key(log)).focusCount, 0);
});

test('a live launch opens its exact native prompt console without starting another runtime', t => {
  const h = harness(t), launch = session({ kind: 'launch', launchState: 'waiting', runtimeId: 'native-launch' });
  const snapshot = inventory([launch]), view = h.open(snapshot, launch);
  assert.equal(view.state, 'connected');
  assert.deepEqual(h.transports.map(transport => transport.target), [{ contextNamespace: launch.contextNamespace, runtimeId: launch.runtimeId }]);
  assert.equal(h.selected[0], launch);
});

test('retained connected output can be revealed after inventory loss without a replacement transport', t => {
  const h = harness(t), current = session(), view = h.open(inventory([current]), current);
  h.transports[0].callbacks.onData('retained output');
  h.registry.hide(current);
  assert.equal(h.open(inventory([]), current), view);
  assert.equal(view.visible, true);
  assert.equal(h.transports.length, 1);
  assert.deepEqual(h.renderers.get(key(current)).output, ['retained output']);
});
