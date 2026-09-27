import test from 'node:test';
import assert from 'node:assert/strict';
import {
  navigateWorkbench, isSettingsPage, configurationTarget, configDocument, configCanSave,
  displayedWorkspace, capability, launchResponsePresentation, presentLaunchResult,
} from '../../frontend/app.js';
import { TerminalViewRegistry } from '../../frontend/terminal-registry.js';

const local = Object.freeze({ id: 'local', label: 'This computer', status: 'available',
  capabilities: { attachTerminal: true, configWrite: true } });
const cluster = Object.freeze({ id: 'cluster', label: 'Remote cluster', status: 'available',
  capabilities: { attachTerminal: true, configWrite: false } });
const A = Object.freeze({ projectId: 'project-a', workspaceId: 'local',
  contextNamespace: 'local:account:state-a', runtimeId: 'same-run-id', state: 'running' });
const B = Object.freeze({ projectId: 'project-b', workspaceId: 'cluster',
  contextNamespace: 'cluster:account:state-a', runtimeId: 'same-run-id', state: 'running' });
const snapshot = Object.freeze({
  workspaces: Object.freeze([local, cluster]),
  projects: Object.freeze([
    Object.freeze({ id: A.projectId, workspaceId: A.workspaceId }),
    Object.freeze({ id: B.projectId, workspaceId: B.workspaceId }),
  ]),
  sessions: Object.freeze([A, B]),
});
const exactTarget = session => ({ contextNamespace: session.contextNamespace, runtimeId: session.runtimeId });
const initial = () => ({ filterId: 'local', projectId: A.projectId, target: exactTarget(A),
  panel: null, settingsMachineId: null });

// These are adapter doubles. The tests exercise the production navigation
// reducer and registry; they do not claim DOM, xterm, or live runtime coverage.
function terminals(t) {
  const renderers = new Map(), transports = new Map();
  const key = target => JSON.stringify([target.contextNamespace, target.runtimeId]);
  const registry = new TerminalViewRegistry({
    createTerminal(target, { onInput }) {
      const terminal = {
        visible: false, enabled: false, output: [], disposed: 0,
        write(data) { this.output.push(data); },
        setVisible(value) { this.visible = value; },
        setInputEnabled(value) { this.enabled = value; },
        resize() {}, focus() {}, dispose() { this.disposed++; }, onInput,
      };
      renderers.set(key(target), terminal); return terminal;
    },
    createTransport(target, callbacks) {
      const transport = {
        opens: 0, closes: 0, input: [], callbacks,
        open() { this.opens++; callbacks.onOpen(); },
        sendInput(data) { this.input.push(data); return true; },
        resize() { return true; }, close() { this.closes++; },
      };
      transports.set(key(target), transport); return transport;
    },
  });
  t.after(() => registry.disposeAll());
  return { registry, renderer: target => renderers.get(key(target)),
    transport: target => transports.get(key(target)), transports };
}

test('machine filtering preserves selected work and its live exact terminal identity', t => {
  const h = terminals(t), before = initial(), inventory = JSON.stringify(snapshot);
  const view = h.registry.show(A); h.registry.attach(A);
  h.transport(A).callbacks.onData('local work already displayed');

  const filtered = navigateWorkbench(before, { type: 'filter', id: 'cluster' }, snapshot);
  assert.equal(filtered.filterId, 'cluster');
  assert.equal(filtered.projectId, A.projectId);
  assert.deepEqual(filtered.target, exactTarget(A));
  assert.equal(filtered.panel, null);
  assert.equal(before.filterId, 'local');
  assert.equal(JSON.stringify(snapshot), inventory);
  assert.equal(h.registry.get(filtered.target), view);
  assert.equal(view.visible, true);
  assert.equal(view.state, 'connected');
  assert.equal(h.transport(A).closes, 0);
  assert.equal(h.transports.size, 1);
  h.renderer(A).onInput('input remains local');
  assert.deepEqual(h.transport(A).input, ['input remains local']);
  assert.equal(h.registry.get(B), null);

  const all = navigateWorkbench(filtered, { type: 'filter', id: '' }, snapshot);
  assert.equal(all.filterId, null);
  assert.deepEqual(all.target, exactTarget(A));
});

test('settings machine inspection and Back to work preserve the independent filter and work selection', t => {
  const h = terminals(t); h.registry.show(A); h.registry.attach(A);
  const work = { ...initial(), filterId: 'cluster' };
  const overview = navigateWorkbench(work, { type: 'settings' }, snapshot);
  const machine = navigateWorkbench(overview, { type: 'machine', id: 'local' }, snapshot);
  assert.equal(machine.panel, 'machine');
  assert.equal(machine.settingsMachineId, 'local');
  assert.equal(machine.filterId, 'cluster');
  assert.deepEqual(machine.target, exactTarget(A));

  const returned = navigateWorkbench(machine, { type: 'back' }, snapshot);
  assert.equal(returned.panel, null);
  assert.equal(returned.projectId, work.projectId);
  assert.equal(returned.filterId, work.filterId);
  assert.deepEqual(returned.target, work.target);
  assert.equal(h.registry.get(A).state, 'connected');
  assert.equal(h.transport(A).closes, 0);
  assert.equal(h.renderer(A).disposed, 0);
  assert.equal(h.transports.size, 1);
  const reopened = navigateWorkbench(returned, { type: 'settings' }, snapshot);
  assert.equal(reopened.settingsMachineId, null);
  assert.deepEqual(reopened.target, work.target);
});

test('Machines and Help navigation preserve the selected project and exact connected terminal', t => {
  const h = terminals(t); h.registry.show(A); h.registry.attach(A);
  const work = { ...initial(), filterId: 'cluster' };
  let state = navigateWorkbench(work, { type: 'machine', id: 'cluster' }, snapshot);
  for (const [action, panel] of [['connections', 'connections'], ['help', 'help'], ['connections', 'connections']]) {
    state = navigateWorkbench(state, { type: action }, snapshot);
    assert.equal(state.panel, panel);
    assert.equal(isSettingsPage(state.panel), true);
    assert.equal(state.settingsMachineId, null);
    assert.equal(state.filterId, work.filterId);
    assert.equal(state.projectId, work.projectId);
    assert.deepEqual(state.target, work.target);
    assert.equal(h.registry.get(state.target).state, 'connected');
    assert.equal(h.transport(A).closes, 0);
    assert.equal(h.renderer(A).disposed, 0);
    assert.equal(h.transports.size, 1);
  }
  state = navigateWorkbench(state, { type: 'help' }, snapshot);
  state = navigateWorkbench(state, { type: 'back' }, snapshot);
  assert.equal(state.panel, null);
  assert.equal(state.filterId, work.filterId);
  assert.equal(state.projectId, work.projectId);
  assert.deepEqual(state.target, work.target);
  h.renderer(A).onInput('continued local work');
  assert.deepEqual(h.transport(A).input, ['continued local work']);
  assert.equal(h.registry.get(B), null);
});

test('choosing another project hides but retains an open terminal; its tab restores work without changing the filter', t => {
  const h = terminals(t);
  let state = { ...initial(), filterId: 'cluster', panel: 'config' };
  const original = h.registry.show(A); h.registry.attach(A);
  h.transport(A).callbacks.onData('retained scrollback');

  // This is the selectProject/registry boundary: selecting a project does not
  // attach its sessions, and hiding an old view is different from disposing it.
  h.registry.hide(state.target);
  state = navigateWorkbench(state, { type: 'project', id: B.projectId }, snapshot);
  assert.equal(state.projectId, B.projectId);
  assert.equal(state.target, null);
  assert.equal(state.panel, null);
  assert.equal(state.filterId, 'cluster');
  assert.equal(original.visible, false);
  assert.equal(original.state, 'connected');
  assert.equal(h.renderer(A).enabled, false);
  assert.equal(h.registry.get(B), null);
  h.transport(A).callbacks.onData('background output');
  assert.deepEqual(h.renderer(A).output, ['retained scrollback', 'background output']);

  const restored = h.registry.show(A);
  state = navigateWorkbench(state, { type: 'session', session: A }, snapshot);
  assert.equal(state.projectId, A.projectId);
  assert.deepEqual(state.target, exactTarget(A));
  assert.equal(state.filterId, 'cluster');
  assert.equal(restored, original);
  assert.equal(h.renderer(A).enabled, true);
  assert.equal(h.transport(A).opens, 1);
  assert.equal(h.transport(A).closes, 0);
  assert.equal(h.renderer(A).disposed, 0);
});

test('same runtime ID on another machine opens a separate view and cannot retarget existing input', t => {
  const h = terminals(t); h.registry.show(A); h.registry.attach(A);
  const state = navigateWorkbench({ ...initial(), filterId: 'local' }, { type: 'session', session: B }, snapshot);
  h.registry.show(state.target); h.registry.attach(state.target);
  assert.equal(state.filterId, 'local');
  assert.equal(state.projectId, B.projectId);
  assert.deepEqual(state.target, exactTarget(B));
  assert.equal(h.registry.size, 2);
  assert.notEqual(h.registry.get(A), h.registry.get(B));
  h.renderer(A).onInput('hidden local protocol reply');
  h.renderer(B).onInput('remote user input');
  assert.deepEqual(h.transport(A).input, ['hidden local protocol reply']);
  assert.deepEqual(h.transport(B).input, ['remote user input']);
  assert.equal(h.transport(A).closes, 0);
});

test('project draft identity and validation survive machine settings and remain separate from dashboard JSON', () => {
  const project = snapshot.projects[1];
  let state = navigateWorkbench(initial(), { type: 'project', id: project.id }, snapshot);
  const target = configurationTarget('project', project);
  const dashboard = configurationTarget('dashboard');
  const draft = configDocument({ text: 'image: saved\n', revision: 'project-v1', writable: true });
  draft.text = 'image: edited\n';
  draft.validation = { text: draft.text, revision: draft.revision, valid: true };
  const drafts = new Map([[target.key, draft], [dashboard.key,
    configDocument({ text: '{"machines": []}', revision: 'dashboard-v2', writable: true })]]);

  state = navigateWorkbench({ ...state, panel: 'config' }, { type: 'settings' }, snapshot);
  state = navigateWorkbench(state, { type: 'machine', id: 'local' }, snapshot);
  state = navigateWorkbench(state, { type: 'back' }, snapshot);
  const selected = snapshot.projects.find(item => item.id === state.projectId);
  assert.equal(configurationTarget('project', selected).key, target.key);
  assert.equal(drafts.get(target.key), draft);
  assert.equal(configCanSave(draft), true);
  assert.equal(target.endpoint, '/api/projects/project-b/config');
  assert.equal(dashboard.endpoint, '/api/dashboard/config');
  assert.equal(drafts.get(dashboard.key).text, '{"machines": []}');
  assert.equal(drafts.get(dashboard.key).revision, 'dashboard-v2');
  assert.notEqual(target.key, dashboard.key);
  state = navigateWorkbench(state, { type: 'session', session: A }, snapshot);
  assert.equal(state.projectId, A.projectId);
  assert.equal(drafts.get(target.key).text, 'image: edited\n');
  assert.equal(drafts.has(configurationTarget('project', snapshot.projects[0]).key), false);
});

const presentation = (state, revision) => ({ revision, workspaceId: state.filterId,
  projectId: state.projectId, targetKey: state.target ? JSON.stringify([
    state.target.contextNamespace, state.target.runtimeId]) : null, inspector: state.panel });

test('late launch responses cannot replace settings, a changed filter, or work after navigation away and back', () => {
  const launch = { ...A, kind: 'launch', contextNamespace: 'launch:local', runtimeId: 'launch-request' };
  const work = { ...initial(), target: exactTarget(launch) };
  const expected = presentation(work, 10);
  assert.deepEqual(launchResponsePresentation(expected, presentation(work, 10), launch), { inspector: null });
  const filtered = navigateWorkbench(work, { type: 'filter', id: 'cluster' }, snapshot);
  // The filter is part of the response guard even though it no longer retargets
  // work; an explicit navigation revision also fences a return to the same page.
  assert.deepEqual(filtered.target, work.target);
  assert.equal(launchResponsePresentation(expected, presentation(filtered, 10), launch), null);
  const settings = navigateWorkbench(work, { type: 'settings' }, snapshot);
  const machine = navigateWorkbench(settings, { type: 'machine', id: 'cluster' }, snapshot);
  const machines = navigateWorkbench(settings, { type: 'connections' }, snapshot);
  const help = navigateWorkbench(settings, { type: 'help' }, snapshot);
  for (const state of [settings, machine, machines, help, { ...settings, panel: 'dashboard-config' }]) {
    assert.equal(launchResponsePresentation(expected, presentation(state, 11), launch), null);
    assert.equal(presentLaunchResult(launch, state.target, state.panel), false);
  }
  const returned = navigateWorkbench(machine, { type: 'back' }, snapshot);
  assert.equal(launchResponsePresentation(expected, presentation(returned, 13), launch), null);
  assert.equal(presentLaunchResult(launch, returned.target, returned.panel), true);
});

test('a missing settings machine never falls back to the filtered machine or another machine capability', () => {
  const settings = navigateWorkbench(initial(), { type: 'settings' }, snapshot);
  const selected = navigateWorkbench(settings, { type: 'machine', id: 'cluster' }, snapshot);
  const removed = { ...snapshot, workspaces: [local] };
  assert.equal(navigateWorkbench(selected, { type: 'machine', id: 'missing' }, removed), selected);
  assert.equal(selected.panel, 'machine');
  assert.equal(selected.settingsMachineId, 'cluster');
  assert.equal(selected.filterId, 'local');
  assert.equal(displayedWorkspace(removed, selected.settingsMachineId), null);
  assert.equal(capability(removed, 'configWrite', { workspaceId: selected.settingsMachineId }), false);
  const invalidFilter = navigateWorkbench(selected, { type: 'filter', id: 'missing' }, removed);
  assert.equal(invalidFilter.filterId, null);
  assert.equal(invalidFilter.settingsMachineId, 'cluster');
  assert.deepEqual(invalidFilter.target, exactTarget(A));
});

test('only dashboard settings scopes replace the work page', () => {
  for (const panel of ['settings', 'machine', 'connections', 'help', 'dashboard-config']) assert.equal(isSettingsPage(panel), true, panel);
  for (const panel of [null, undefined, 'config', 'files', 'details', 'unknown']) assert.equal(isSettingsPage(panel), false, String(panel));
  assert.throws(() => navigateWorkbench(initial(), { type: 'launch' }, snapshot), /Unknown workbench/);
});
