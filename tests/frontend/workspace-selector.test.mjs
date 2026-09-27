import test from 'node:test';
import assert from 'node:assert/strict';
import { workspaceSummaries, navigateWorkbench, projectListView, projectListSignals, observedAgents, sessionGuidance,
  pendingConnections, workspaceConnectionLabel, projectSetupOptions, projectStatusSummary } from '../../frontend/app.js';

const snapshot = {
  capabilities: {},
  workspaces: [{ id: 'local', label: 'This computer · Botainer', status: 'available' },
    { id: 'remote', label: 'Example cluster', status: 'available' },
    { id: 'host', label: 'This computer · Host agents', status: 'checking' }],
  projects: [{ id: 'local-one', name: 'Local one', workspaceId: 'local' },
    { id: 'remote-one', name: 'Remote one', workspaceId: 'remote' },
    { id: 'remote-two', name: 'Remote two', workspaceId: 'remote' }], sessions: [],
};

test('host selector names show actual configured agents without changing the profile identity', () => {
  const host = { id: 'host', label: 'Work profile', executionKind: 'host', availableAgents: [{ id: 'claude' }] };
  assert.equal(workspaceConnectionLabel(host), 'Work profile · Claude · no container');
  assert.equal(workspaceConnectionLabel({ ...host, availableAgents: [{ id: 'codex' }] }), 'Work profile · Codex · no container');
  assert.equal(workspaceConnectionLabel({ id: 'local', label: 'Containers', availableAgents: [{ id: 'claude' }] }), 'Containers');
  assert.equal(host.label, 'Work profile');
});

test('saved profiles appear only as pending setup and never grant a workspace or launch capability', () => {
  const codex = { id: 'host-codex', kind: 'host', label: 'Host · Codex', enabled: true, profileDigest: 'new' };
  const active = { id: 'host', kind: 'host', label: 'Host · Claude', enabled: true, profileDigest: 'existing' };
  const saved = { enabled: true, entries: [active, codex], active: [active] };
  const before = JSON.stringify(snapshot);
  assert.deepEqual(pendingConnections(snapshot, saved).map(row => row.id), ['host-codex']);
  assert.deepEqual(projectSetupOptions(snapshot, { roots: [], installations: [] }, 'host-codex').modes, []);
  assert.equal(JSON.stringify(snapshot), before);
  assert.deepEqual(pendingConnections(snapshot, { ...saved, entries: [active, { ...codex, enabled: false }] }), []);
  assert.deepEqual(pendingConnections(snapshot, { ...saved, active: [active, codex] }), []);
  // Independently polled settings may briefly lag a newly loaded state.
  assert.deepEqual(pendingConnections({ ...snapshot, workspaces: [...snapshot.workspaces, codex] }, saved), []);
  for (const absent of [null, {}, { enabled: false, entries: [codex], active: [] }]) {
    assert.deepEqual(pendingConnections(snapshot, absent), []);
  }
});

test('compact summaries keep unavailable observations unknown and control restrictions separate from runtime state', () => {
  const project = snapshot.projects[0];
  const sessions = [{ kind: 'session', state: 'running', agent: 'codex' },
    { kind: 'session', state: 'queued', agent: 'claude' },
    { kind: 'session', state: 'unknown', agent: 'Agent not recorded' }];
  assert.deepEqual(projectListSignals(snapshot, project, sessions), { text: '1 running · 1 queued · 1 unverified', warning: '' });
  assert.deepEqual(projectListSignals(snapshot, { ...project, controlRestriction: 'outside-project-roots' }, sessions),
    { text: '1 running · 1 queued · 1 unverified', warning: 'Controls unavailable' });
  assert.deepEqual(observedAgents(sessions), ['claude', 'codex']);
  for (const status of ['unavailable', 'checking', 'unknown']) {
    const offline = { ...snapshot, workspaces: snapshot.workspaces.map(item => ({ ...item, status })) };
    assert.equal(projectListSignals(offline, project, []).text, 'Activity unverified');
    assert.equal(projectListSignals(offline, project, sessions).text, 'Activity unverified');
  }
  assert.deepEqual(projectListSignals(snapshot, project, sessions, { connected: false }),
    { text: 'Activity unverified', warning: 'Service offline' });
  assert.equal(projectListSignals(snapshot, { ...project, stale: true }, []).text, 'Activity unverified');
  assert.equal(projectListSignals({ ...snapshot, stale: true }, project, []).text, 'Activity unverified');
  assert.equal(projectListSignals(snapshot, project, [{ ...sessions[0], stale: true }]).text, 'Activity unverified');
  assert.equal(projectListSignals(snapshot, project, []).text, 'No current sessions');
});

test('bell filtering keeps exact session namespaces and combines with search and connection filters', () => {
  const sessions = [{ projectId: 'local-one', contextNamespace: 'local:one', runtimeId: 'same-id', state: 'running' },
    { projectId: 'remote-one', contextNamespace: 'remote:one', runtimeId: 'same-id', state: 'running' }];
  const catalog = { ...snapshot, sessions };
  const bellTargets = new Set([JSON.stringify(['remote:one', 'same-id'])]);
  const filtered = projectListView(catalog, { bellTargets });
  assert.deepEqual(filtered.projects.map(project => project.id), ['remote-one']);
  assert.equal(filtered.countLabel, '1 of 3');
  assert.equal(filtered.filtered, true);
  assert.equal(projectListView(catalog, { bellTargets, workspaceId: 'local' }).projects.length, 0);
  assert.equal(projectListView(catalog, { bellTargets, query: 'two' }).projects.length, 0);
  assert.equal(projectListView(catalog, { bellTargets: new Set() }).projects.length, 0);
  assert.equal(projectListView(catalog).projects.length, 3);
  assert.equal(sessions.length, 2); // Filtering neither opens nor rewrites session records.
});

test('persistent workspace summaries expose every registered workspace with its own count and connection state', () => {
  assert.deepEqual(workspaceSummaries(snapshot), [
    { id: 'local', label: 'This computer · Botainer', status: 'connected', projectCount: 1 },
    { id: 'remote', label: 'Example cluster', status: 'connected', projectCount: 2 },
    { id: 'host', label: 'This computer · Host agents', status: 'checking', projectCount: 0 },
  ]);
  const offline = { ...snapshot, workspaces: snapshot.workspaces.map(item => item.id === 'remote' ? { ...item, status: 'unavailable' } : item) };
  assert.deepEqual(workspaceSummaries(offline)[1], { id: 'remote', label: 'Example cluster', status: 'unavailable', projectCount: 2 });
  assert.deepEqual(workspaceSummaries({ capabilities: {}, projects: [], sessions: [] }), []);
});

test('project and sidebar warnings identify concrete workspace repairs without changing session activity', () => {
  const project = snapshot.projects[0];
  const sessions = [{ state: 'running', agent: 'claude' }];
  for (const [code, warning] of [['botainer-installation-changed', 'Review Botainer update'],
    ['dashboard-restart-required', 'Dashboard restart needed']]) {
    const workspace = { ...snapshot.workspaces[0], status: 'unavailable', observationStatus: 'failed',
      connectionDiagnostic: { code, message: 'A verified connection problem.', recovery: 'Review the connection.' } };
    const failed = { ...snapshot, workspaces: [workspace, ...snapshot.workspaces.slice(1)] };
    assert.deepEqual(projectListSignals(failed, project, sessions), { text: 'Activity unverified', warning });
    assert.equal(workspaceSummaries(failed)[0].status, warning);
    assert.equal(projectStatusSummary(project, sessions, { workspace }), `Activity unverified · ${warning}`);
    assert.deepEqual(projectListSignals(failed, project, sessions, { connected: false }),
      { text: 'Activity unverified', warning: 'Service offline' });
    const healthy = { ...workspace, status: 'available', observationStatus: 'current' };
    const recovered = { ...failed, workspaces: [healthy, ...snapshot.workspaces.slice(1)] };
    assert.equal(workspaceSummaries(recovered)[0].status, 'connected');
    assert.deepEqual(projectListSignals(recovered, project, sessions), { text: '1 running', warning: '' });
    assert.equal(projectStatusSummary(project, sessions, { workspace: healthy }), '1 active session · Service connected');
    assert.equal(sessions[0].state, 'running');
  }
  const slow = { ...snapshot.workspaces[0], status: 'unavailable', observationStatus: 'delayed',
    connectionDiagnostic: { code: 'workspace-check-failed', message: 'A workspace check failed.', recovery: 'Review the connection.' } };
  const delayed = { ...snapshot, workspaces: [slow] };
  assert.equal(workspaceSummaries(delayed)[0].status, 'Status check delayed');
  assert.equal(projectListSignals(delayed, project, sessions).warning, 'Connection unavailable');
});

test('workspace selection exposes remote projects without changing selected terminal or opening a new dashboard', () => {
  const target = { contextNamespace: 'local:exact', runtimeId: 'active-local' };
  const state = { projectId: 'local-one', target, filterId: null, panel: null, settingsMachineId: null };
  const filtered = navigateWorkbench(state, { type: 'filter', id: 'remote' }, snapshot);
  assert.equal(filtered.target, target);
  assert.equal(filtered.projectId, state.projectId);
  assert.equal(filtered.panel, state.panel);
  assert.deepEqual(projectListView(snapshot, { workspaceId: filtered.filterId }).projects.map(project => project.id), ['remote-one', 'remote-two']);
  assert.equal(projectListView(snapshot, { workspaceId: filtered.filterId }).countLabel, '2 of 3');
  const all = navigateWorkbench(filtered, { type: 'filter', id: null }, snapshot);
  assert.equal(all.target, target);
  assert.equal(projectListView(snapshot, { workspaceId: all.filterId }).projects.length, 3);
});

test('observed-running sessions with unavailable controls stay running and explain the actual restriction', () => {
  const session = { state: 'running', unavailableReason: 'This folder is outside the profile control roots.' };
  const guidance = sessionGuidance(session, { viewState: 'detached', canAttach: false, remote: true });
  assert.equal(guidance.title, 'Observed running · control unavailable');
  assert.ok(guidance.message.includes(session.unavailableReason));
  assert.match(guidance.message, /does not mean the agent has stopped/);
  assert.match(guidance.note, /Input is disabled and will not be replayed/);
  assert.equal(guidance.action, null);
  assert.equal(session.state, 'running');
  const connected = sessionGuidance(session, { viewState: 'connected', canAttach: false });
  assert.equal(connected.title, '');
  assert.match(connected.note, /Click to type/);
  assert.notEqual(sessionGuidance({ state: 'stopped' }, { viewState: 'detached' }).title, guidance.title);
});
