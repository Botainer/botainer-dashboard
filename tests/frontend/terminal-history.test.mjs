import test from 'node:test';
import assert from 'node:assert/strict';
import { api, canReadTerminalHistory, loadTerminalHistory, terminalHistoryText, navigateWorkbench,
  TERMINAL_HISTORY_BYTES, TERMINAL_HISTORY_CAPTION } from '../../frontend/app.js';

const session = { contextNamespace: 'local:exact-owner', runtimeId: 'same/run', projectId: 'project-one',
  workspaceId: 'local', state: 'running', capabilities: { readTerminalHistory: true } };
const snapshot = { workspaces: [{ id: 'local', status: 'available', capabilities: {} }],
  projects: [{ id: session.projectId, workspaceId: 'local' }], sessions: [session], capabilities: {} };

test('scrollback requires explicit session capability and its connected workspace, independent of attachment state', () => {
  assert.equal(canReadTerminalHistory(snapshot, session), true);
  assert.equal(canReadTerminalHistory(snapshot, { ...session, state: 'stopped' }), true);
  assert.equal(canReadTerminalHistory(snapshot, session, { connected: false }), false);
  assert.equal(canReadTerminalHistory(snapshot, { ...session, capabilities: {} }), false);
  assert.equal(canReadTerminalHistory(snapshot, { ...session, workspaceId: 'different-machine' }), false);
  assert.equal(canReadTerminalHistory({ ...snapshot, workspaces: [{ ...snapshot.workspaces[0], status: 'unavailable' }] }, session), false);
  assert.equal(canReadTerminalHistory({ ...snapshot, capabilities: { readTerminalHistory: true } }, null), false);
});

test('history uses authenticated readonly POST with frozen exact target and never starts or attaches anything', async () => {
  const calls = [], bearer = 'A'.repeat(43);
  const response = { kind: 'terminal-snapshot', text: 'Native preflight warning\n', truncated: false };
  const result = await loadTerminalHistory(session, { request: (path, options) => {
    assert.equal(options.mutation, false);
    assert.ok(Object.isFrozen(options.body));
    return api(path, { ...options, bearer, fetch: async (url, request) => {
      calls.push({ url, request });
      return { ok: true, status: 200, json: async () => response };
    } });
  } });
  assert.deepEqual(result, { text: response.text, truncated: false });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/sessions/same%2Frun/history');
  assert.equal(calls[0].request.method, 'POST');
  assert.equal(calls[0].request.credentials, 'same-origin');
  assert.equal(calls[0].request.headers.Authorization, `Bearer ${bearer}`);
  assert.deepEqual(JSON.parse(calls[0].request.body), { contextNamespace: session.contextNamespace });
});

test('an old history response cannot populate a different selected terminal or closed panel', async () => {
  let finish, current = true;
  const pending = loadTerminalHistory(session, { isCurrent: () => current,
    request: () => new Promise(resolve => { finish = resolve; }) });
  current = false;
  finish({ kind: 'terminal-snapshot', text: 'Output from the previous session' });
  assert.equal(await pending, null);
});

test('history validates kind, plain text, optional truncation and UTF-8 size before rendering', async () => {
  for (const value of [null, {}, { kind: 'html', text: '<script>bad()</script>' },
    { kind: 'terminal-snapshot', text: ['not text'] },
    { kind: 'terminal-snapshot', text: 'fine', truncated: 'true' },
    { kind: 'terminal-snapshot', text: '🌱'.repeat(TERMINAL_HISTORY_BYTES / 4 + 1) }]) {
    await assert.rejects(loadTerminalHistory(session, { request: async () => value }), /unsupported scrollback/);
  }
  await assert.rejects(loadTerminalHistory({ ...session, capabilities: {} }, {
    request: () => { throw new Error('Capability failure must not send a request'); },
  }), /not available/);
  assert.deepEqual(await loadTerminalHistory(session, { request: async () => ({ kind: 'terminal-snapshot', text: '', truncated: true }) }),
    { text: '', truncated: true });
});

test('history text is only a readonly form value, never HTML or terminal input', () => {
  const untrusted = '<img src=x onerror=alert(1)>\n\x1b]52;c;clipboard\x07\nShell-looking text: $(do-not-execute)';
  const element = { attributes: {}, setAttribute(name, value) { this.attributes[name] = value; },
    set innerHTML(_value) { throw new Error('Untrusted history must not become HTML'); } };
  const preview = terminalHistoryText({ createElement(tag) { assert.equal(tag, 'textarea'); return element; } }, untrusted);
  assert.equal(preview.value, untrusted);
  assert.equal(preview.readOnly, true);
  assert.equal(preview.spellcheck, false);
  assert.equal(preview.attributes['aria-label'], 'Terminal scrollback snapshot');
  assert.equal(TERMINAL_HISTORY_CAPTION, 'Screen and scrollback snapshot; refresh to update. Does not send input.');
});

test('closing the history inspector preserves the exact selected project and terminal target', () => {
  const target = { contextNamespace: session.contextNamespace, runtimeId: session.runtimeId };
  const current = { filterId: 'local', projectId: session.projectId, target, panel: 'history', settingsMachineId: null };
  const closed = navigateWorkbench(current, { type: 'back' }, snapshot);
  assert.equal(closed.panel, null);
  assert.equal(closed.target, target);
  assert.equal(closed.projectId, current.projectId);
  assert.equal(current.panel, 'history');
});
