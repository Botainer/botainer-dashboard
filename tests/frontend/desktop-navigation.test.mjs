import test from 'node:test';
import assert from 'node:assert/strict';
import { workbenchChoices, restoreWorkbenchViews } from '../../frontend/app.js';
import { TerminalViewRegistry } from '../../frontend/terminal-registry.js';
const targetKey = target => JSON.stringify([target.contextNamespace, target.runtimeId]);

const local = { contextNamespace: 'local:one', runtimeId: 'run-one', projectId: 'alpha', workspaceId: 'local', state: 'running', agent: 'codex' };
const host = { contextNamespace: 'host:one', runtimeId: 'run-one', projectId: 'beta', workspaceId: 'host', state: 'running', agent: 'claude' };
const inventory = () => ({ workspaces: [
  { id: 'local', label: 'Local containers', status: 'available' },
  { id: 'host', label: 'Host agents', status: 'available', executionKind: 'host' },
  { id: 'remote', label: 'Example cluster', status: 'available' },
], projects: [
  { id: 'alpha', name: 'Alpha', workspaceId: 'local' },
  { id: 'beta', name: 'Beta', workspaceId: 'host' },
  { id: 'idle', name: 'Idle project', workspaceId: 'remote' },
], sessions: [{ ...local }, { ...host }] });

test('switcher finds inactive projects and searches across connections and recorded agents', () => {
  assert.equal(workbenchChoices(inventory(), { query: 'idle cluster' })[0].project.id, 'idle');
  const result = workbenchChoices(inventory(), { query: 'codex local' });
  assert.ok(result.some(item => item.kind === 'session' && item.session.runtimeId === local.runtimeId));
  assert.ok(result.every(item => !item.host));
});

test('host projects and sessions retain explicit host warnings in search results', () => {
  const results = workbenchChoices(inventory(), { query: 'Beta' });
  assert.equal(results.length, 2);
  for (const item of results) { assert.equal(item.host, true); assert.match(item.detail, /HOST.*NO CONTAINER/i); }
});

test('open-only list uses exact retained targets and includes no project or unopened session', () => {
  const choices = workbenchChoices(inventory(), { openViews: [local], openOnly: true, bells: new Map([[targetKey(local), true]]) });
  assert.equal(choices.length, 1);
  assert.equal(choices[0].session, local);
  assert.equal(choices[0].bell, true);
  assert.match(choices[0].detail, /Open view.*Bell observed/);
});

test('retained views never acquire replacement ownership or labels', () => {
  const snapshot = inventory();
  snapshot.sessions[0] = { ...local, projectId: 'beta', workspaceId: 'host', label: 'replacement label' };
  const [choice] = workbenchChoices(snapshot, { openViews: [local], openOnly: true });
  assert.equal(choice.session, local);
  assert.match(choice.detail, /No longer verified/);
  assert.doesNotMatch(choice.label, /replacement|Beta/);
});

test('ended sessions are searchable only when their view is already open', () => {
  const snapshot = inventory(), ended = { ...local, runtimeId: 'ended', state: 'stopped' };
  snapshot.sessions.push(ended);
  assert.ok(!workbenchChoices(snapshot).some(item => item.session?.runtimeId === 'ended'));
  assert.ok(workbenchChoices(snapshot, { openViews: [ended] }).some(item => item.session?.runtimeId === 'ended'));
});

function registryFixture() {
  const terminals = [], views = new Map(); let transports = 0;
  const registry = new TerminalViewRegistry({ createTerminal() {
    const terminal = { writes: [], enabled: [], write(value) { this.writes.push(value); },
      setVisible() {}, setInputEnabled(value) { this.enabled.push(value); }, resize() {}, focus() {}, dispose() {} };
    terminals.push(terminal); return terminal;
  }, createTransport() { transports++; throw new Error('Restore must not create a transport'); } });
  return { registry, views, terminals, transportCount: () => transports };
}
const preferences = () => ({ version: 1, projectId: 'alpha', projectWorkspaceId: 'local', selectedTarget: local, openViews: [local, host] });

test('restore creates detached renderers with fresh host metadata and no transport or output replay', () => {
  const fixture = registryFixture(), snapshot = inventory();
  const restored = restoreWorkbenchViews(preferences(), snapshot, fixture);
  assert.equal(restored.openViews.length, 2);
  assert.equal(fixture.transportCount(), 0);
  for (const session of snapshot.sessions) {
    assert.equal(fixture.views.get(targetKey(session)), session);
    assert.equal(fixture.registry.get(session).state, 'detached');
  }
  for (const terminal of fixture.terminals) {
    assert.deepEqual(terminal.writes, []);
    assert.ok(terminal.enabled.every(value => value === false));
  }
  fixture.registry.disposeAll();
});

test('restore drops unavailable and moved targets before creating renderers', () => {
  const fixture = registryFixture(), snapshot = inventory();
  snapshot.workspaces[0].status = 'unavailable';
  snapshot.sessions[1].projectId = 'idle'; snapshot.sessions[1].workspaceId = 'remote';
  const restored = restoreWorkbenchViews(preferences(), snapshot, fixture);
  assert.deepEqual(restored.openViews, []);
  assert.equal(restored.selectedTarget, null);
  assert.equal(fixture.registry.size, 0);
});

test('renderer failure drops the selection without invoking a fallback', () => {
  const restored = restoreWorkbenchViews(preferences(), inventory(), { views: new Map(), registry: {
    ensure() { throw new Error('unavailable renderer'); },
  } });
  assert.deepEqual(restored.openViews, []);
  assert.equal(restored.selectedTarget, null);
});
