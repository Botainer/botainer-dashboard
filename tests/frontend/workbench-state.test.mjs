import test from 'node:test';
import assert from 'node:assert/strict';
import {
  readWorkbenchPreferences, writeWorkbenchPreferences, reconcileWorkbenchPreferences,
} from '../../frontend/workbench-state.js';

const KEY = 'botainer-dashboard:workbench:v1';
const A = { contextNamespace: 'local:state-a', runtimeId: 'run-one', projectId: 'project-a', workspaceId: 'local' };
const B = { contextNamespace: 'remote:state-b', runtimeId: 'run-one', projectId: 'project-b', workspaceId: 'remote' };
const defaults = () => ({ version: 1, filterId: null, projectId: null, projectWorkspaceId: null,
  selectedTarget: null, openViews: [], query: '', sort: 'recent', showEnded: false,
  projectExpansion: [], sidebarShown: true, focusMode: false });
const preferences = () => ({ ...defaults(), filterId: 'remote', projectId: A.projectId,
  projectWorkspaceId: A.workspaceId, selectedTarget: { ...A }, openViews: [{ ...A }, { ...B }],
  query: 'example', sort: 'active', showEnded: true, projectExpansion: [['project-a', false], ['project-b', true]],
  sidebarShown: false, focusMode: true });
const inventory = () => ({ workspaces: [{ id: 'local', status: 'available' }, { id: 'remote', status: 'available' }],
  projects: [{ id: A.projectId, workspaceId: A.workspaceId }, { id: B.projectId, workspaceId: B.workspaceId }],
  sessions: [{ ...A, state: 'running' }, { ...B, state: 'running' }] });

function memory(raw = null) {
  const values = new Map(raw === null ? [] : [[KEY, raw]]);
  const calls = [];
  return { values, calls,
    getItem(key) { calls.push(['get', key]); return values.get(key) ?? null; },
    setItem(key, value) { calls.push(['set', key]); values.set(key, value); } };
}

function freeze(value) {
  if (value && typeof value === 'object') {
    for (const item of Object.values(value)) freeze(item);
    Object.freeze(value);
  }
  return value;
}

test('missing, corrupt and unsupported storage return independent defaults', () => {
  for (const raw of [null, '', '{', 'null', '[]', '42', '{}', '{"version":2}', '{"version":"1"}']) {
    assert.deepEqual(readWorkbenchPreferences(memory(raw)), defaults());
  }
  const first = readWorkbenchPreferences(memory());
  first.openViews.push(A);
  assert.deepEqual(readWorkbenchPreferences(memory()), defaults());
});

test('only the dedicated key is accessed and canonical preferences round-trip', () => {
  const storage = memory(), state = preferences();
  assert.equal(writeWorkbenchPreferences(storage, state), true);
  assert.deepEqual(readWorkbenchPreferences(storage), state);
  assert.deepEqual(storage.calls, [['set', KEY], ['get', KEY]]);
  assert.equal(storage.values.size, 1);
});

test('denied storage and a throwing default localStorage getter are harmless', () => {
  const denied = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } };
  assert.deepEqual(readWorkbenchPreferences(denied), defaults());
  assert.equal(writeWorkbenchPreferences(denied, preferences()), false);
  assert.equal(writeWorkbenchPreferences(null, preferences()), false);
  const prior = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, get() { throw new Error('denied'); } });
  try {
    assert.deepEqual(readWorkbenchPreferences(), defaults());
    assert.equal(writeWorkbenchPreferences(undefined, preferences()), false);
  } finally {
    if (prior) Object.defineProperty(globalThis, 'localStorage', prior);
    else delete globalThis.localStorage;
  }
});

test('write defaults missing version to v1 and removes all unknown fields at every level', () => {
  const storage = memory();
  const state = { ...preferences(), version: undefined, path: '/private/example', label: 'secret label',
    token: 'secret-token', bearer: 'secret-bearer', output: 'secret-output', input: 'secret-input',
    selectedTarget: { ...A, credentials: 'secret-credentials' },
    openViews: [{ ...A, output: 'secret-output', path: '/private/example', attached: true }],
    projectExpansion: [['project-a', true, 'secret-output']] };
  assert.equal(writeWorkbenchPreferences(storage, state), true);
  const text = storage.values.get(KEY);
  assert.doesNotMatch(text, /secret|private|attached|credentials|bearer|token|output|input/);
  const result = readWorkbenchPreferences(storage);
  assert.deepEqual(result.openViews, [A]);
  assert.deepEqual(result.selectedTarget, A);
  assert.deepEqual(result.projectExpansion, []);
});

test('hostile JSON keys remain data and cannot populate the result prototype', () => {
  const raw = '{"version":1,"__proto__":{"polluted":true},"constructor":{"prototype":{"polluted":true}},"sort":"name"}';
  const result = readWorkbenchPreferences(memory(raw));
  assert.equal(result.sort, 'name');
  assert.equal(Object.getPrototypeOf(result), Object.prototype);
  assert.equal(Object.hasOwn(result, '__proto__'), false);
  assert.equal(Object.hasOwn(result, 'constructor'), false);
  assert.equal({}.polluted, undefined);
});

test('selection excludes accessors, inherited fields and toJSON without invoking them', () => {
  const state = { version: 1, openViews: [A] };
  Object.defineProperty(state, 'query', { get() { throw new Error('must not read accessor'); } });
  Object.defineProperty(state, 'unknown', { get() { throw new Error('must not read unknown'); } });
  state.toJSON = () => { throw new Error('must not invoke toJSON'); };
  const storage = memory();
  assert.equal(writeWorkbenchPreferences(storage, state), true);
  assert.equal(readWorkbenchPreferences(storage).query, '');
  const inherited = Object.create({ version: 1, query: 'inherited' });
  assert.equal(writeWorkbenchPreferences(storage, inherited), true);
  assert.deepEqual(readWorkbenchPreferences(storage), defaults());
});

test('raw JSON is bounded in bytes before parsing and malformed field types default', () => {
  assert.deepEqual(readWorkbenchPreferences(memory(' '.repeat(256 * 1024 + 1))), defaults());
  const multibyte = JSON.stringify({ version: 1, ignored: '界'.repeat(100000) });
  assert.ok(multibyte.length < 256 * 1024);
  assert.deepEqual(readWorkbenchPreferences(memory(multibyte)), defaults());
  const storage = memory(JSON.stringify({ version: 1, sort: 'constructor', query: {}, showEnded: 1,
    sidebarShown: 0, focusMode: 'true', projectExpansion: {}, openViews: {}, selectedTarget: [] }));
  assert.deepEqual(readWorkbenchPreferences(storage), defaults());
});

test('IDs match the bounded service alphabet and never coerce strings or paths', () => {
  for (const invalid of ['', 'x'.repeat(161), 'id\n', '/tmp/project', '../project', 'a b', 'é', 42, {}, true]) {
    const result = readWorkbenchPreferences(memory(JSON.stringify({ ...preferences(), filterId: invalid,
      projectId: invalid, openViews: [{ ...A, runtimeId: invalid }] })));
    assert.equal(result.filterId, null);
    assert.equal(result.projectId, null);
    assert.equal(result.projectWorkspaceId, null);
    assert.deepEqual(result.openViews, []);
  }
  const storage = memory();
  writeWorkbenchPreferences(storage, { ...defaults(), projectId: 'a'.repeat(160) });
  assert.equal(readWorkbenchPreferences(storage).projectId.length, 160);
});

test('query, views and expansion pairs have independent bounded output', () => {
  const storage = memory();
  writeWorkbenchPreferences(storage, { ...preferences(), query: 'q'.repeat(201),
    openViews: Array.from({ length: 100 }, (_, i) => ({ ...A, runtimeId: `run-${i}` })),
    projectExpansion: Array.from({ length: 1100 }, (_, i) => [`project-${i}`, i % 2 === 0]) });
  const result = readWorkbenchPreferences(storage);
  assert.equal(result.query.length, 200);
  assert.equal(result.openViews.length, 8);
  assert.equal(result.projectExpansion.length, 1000);
  assert.deepEqual(result.openViews.at(-1), { ...A, runtimeId: 'run-7' });
  const cleaned = memory();
  writeWorkbenchPreferences(cleaned, { ...defaults(), query: 'a\u0000b\nc\u007fd' });
  assert.equal(readWorkbenchPreferences(cleaned).query, 'abcd');
});

test('duplicate views deduplicate exact tuples but discard conflicting ownership', () => {
  const storage = memory();
  writeWorkbenchPreferences(storage, { ...defaults(), openViews: [A, { ...A }, B],
    projectExpansion: [['project-a', false], ['project-a', true]] });
  assert.deepEqual(readWorkbenchPreferences(storage).openViews, [A, B]);
  assert.deepEqual(readWorkbenchPreferences(storage).projectExpansion, [['project-a', false]]);
  writeWorkbenchPreferences(storage, { ...defaults(), openViews: [A, { ...A, projectId: 'replacement' }, A, B] });
  assert.deepEqual(readWorkbenchPreferences(storage).openViews, [B]);
});

test('fresh exact inventory restores copied navigation while keeping machine filter independent', () => {
  const state = freeze(preferences()), snapshot = freeze(inventory());
  const before = JSON.stringify([state, snapshot]);
  const result = reconcileWorkbenchPreferences(state, snapshot);
  assert.deepEqual(result, state);
  assert.equal(JSON.stringify([state, snapshot]), before);
  assert.notEqual(result, state);
  assert.notEqual(result.openViews, state.openViews);
  assert.notEqual(result.openViews[0], state.openViews[0]);
  assert.notEqual(result.selectedTarget, state.selectedTarget);
});

test('stale or absent inventory clears identities without losing harmless display preferences', () => {
  for (const snapshot of [undefined, null, {}, { ...inventory(), stale: true }]) {
    const result = reconcileWorkbenchPreferences(preferences(), snapshot);
    assert.deepEqual(result.openViews, []);
    assert.equal(result.selectedTarget, null);
    assert.equal(result.projectId, null);
    assert.equal(result.filterId, null);
    assert.deepEqual(result.projectExpansion, []);
    assert.equal(result.query, 'example');
    assert.equal(result.sort, 'active');
    assert.equal(result.focusMode, true);
  }
});

test('deleted projects and changed project/workspace/namespace/runtime identities never redirect a view', () => {
  const variants = [
    snapshot => { snapshot.projects.shift(); },
    snapshot => { snapshot.projects[0].workspaceId = 'remote'; },
    snapshot => { snapshot.sessions[0].projectId = B.projectId; },
    snapshot => { snapshot.sessions[0].workspaceId = B.workspaceId; },
    snapshot => { snapshot.sessions[0].contextNamespace = 'replacement:namespace'; },
    snapshot => { snapshot.sessions[0].runtimeId = 'replacement-runtime'; },
    snapshot => { snapshot.sessions.shift(); },
  ];
  for (const change of variants) {
    const snapshot = inventory(); change(snapshot);
    const result = reconcileWorkbenchPreferences(preferences(), snapshot);
    assert.deepEqual(result.openViews, [B]);
    assert.equal(result.selectedTarget, null);
  }
});

test('stale entities or unavailable workspace cannot restore views; known filter may remain', () => {
  for (const scope of ['project', 'session', 'workspace', 'unavailable', 'checking']) {
    const snapshot = inventory();
    if (scope === 'project') snapshot.projects[1].stale = true;
    else if (scope === 'session') snapshot.sessions[1].stale = true;
    else if (scope === 'workspace') snapshot.workspaces[1].stale = true;
    else snapshot.workspaces[1].status = scope;
    const result = reconcileWorkbenchPreferences(preferences(), snapshot);
    assert.deepEqual(result.openViews, [A]);
    assert.equal(result.filterId, 'remote');
  }
});

test('removed or duplicate workspace, project or session identities fail closed', () => {
  for (const scope of ['workspace', 'project', 'session', 'workspace-removed']) {
    const snapshot = inventory();
    if (scope === 'workspace') snapshot.workspaces.push({ ...snapshot.workspaces[0] });
    else if (scope === 'project') snapshot.projects.push({ ...snapshot.projects[0] });
    else if (scope === 'session') snapshot.sessions.push({ ...snapshot.sessions[0] });
    else snapshot.workspaces.shift();
    const result = reconcileWorkbenchPreferences(preferences(), snapshot);
    assert.deepEqual(result.openViews, [B]);
    assert.equal(result.selectedTarget, null);
  }
});

test('single-backend absent workspace is canonical null and never matches a defined workspace', () => {
  const single = { ...A }; delete single.workspaceId;
  const state = { ...defaults(), projectId: A.projectId, selectedTarget: single, openViews: [single] };
  const snapshot = { projects: [{ id: A.projectId }], sessions: [single] };
  const restored = reconcileWorkbenchPreferences(state, snapshot);
  assert.deepEqual(restored.openViews, [{ ...single, workspaceId: null }]);
  assert.deepEqual(restored.selectedTarget, { ...single, workspaceId: null });
  assert.deepEqual(reconcileWorkbenchPreferences(state, inventory()).openViews, []);
  assert.deepEqual(reconcileWorkbenchPreferences(preferences(), snapshot).openViews, []);
  const changed = { projects: [{ id: A.projectId, workspaceId: 'local' }], sessions: [{ ...single, workspaceId: 'local' }] };
  assert.deepEqual(reconcileWorkbenchPreferences(state, changed).openViews, []);
});

test('selected target must belong to both surviving open views and selected project', () => {
  for (const changes of [{ openViews: [B] }, { selectedTarget: B },
    { projectId: B.projectId, projectWorkspaceId: B.workspaceId }, { projectWorkspaceId: null }]) {
    assert.equal(reconcileWorkbenchPreferences({ ...preferences(), ...changes }, inventory()).selectedTarget, null);
  }
});

test('reconciliation cannot invoke backend, transport, saved callbacks or unknown getters', () => {
  const bomb = () => { throw new Error('not a navigation operation'); };
  const state = preferences(), snapshot = inventory();
  state.attach = bomb; state.start = bomb; state.selectedTarget.onInput = bomb;
  snapshot.backend = { start: bomb, attach: bomb };
  Object.defineProperty(snapshot.sessions[0], 'output', { get: bomb });
  Object.defineProperty(snapshot.projects[0], 'path', { get: bomb });
  const result = reconcileWorkbenchPreferences(state, snapshot);
  assert.deepEqual(result, preferences());
});
