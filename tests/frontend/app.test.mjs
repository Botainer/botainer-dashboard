import test from 'node:test';
import assert from 'node:assert/strict';
import { api as rawApi, capability, sortProjects, validateSnapshot, stateClass, configCanSave, configDocument,
  configurationTarget, sessionControls, sessionStopPresentation, sessionGuidance, singleFlight, clusterSettingsDetails,
  workspaceFor, workspaceCapability, projectSetupOptions, schedulerReason, workspaceAvailability,
  presentationUnchanged, sessionDisplayLabel, sessionStateLabel, hasNoRecordedJob, nativeLaunchResult, presentLaunchResult, includeLaunchResponse,
  isLaunchLog, projectActivity, projectWorkflow, launchResponsePresentation, isHistoricalSession,
  projectSessionGroups, relativeTimestamp, sessionTimestamp, projectLaunchTimestamp, observedAgents, observedBellKey,
  BELL_NOTICE, updateNavigationList, displayedWorkspace, sshRecoveryCommand, projectPrimaryAction,
  runtimeStartedAt, sessionRecordedEnd, configurationWritable, configurationStatus, sessionLaunchRequest, includeProjectSetupResponse,
  nativeSetupResult, isProjectSetup, preparedTrial, projectSetupDescription, projectUnavailableMessage, projectStartUnavailableMessage,
  terminalRecoveryIntent, advanceTerminalRecovery, terminalRecoveryTarget, beginTerminalRecovery,
  dashboardCommand, observationStatusLabel, lastReportedSession, workspaceSummaries, projectStatusSummary, projectInventoryNotice,
  workspaceConnectionPresentation, projectListSignals } from '../../frontend/app.js';
import { TerminalViewRegistry } from '../../frontend/terminal-registry.js';
const bearer = 'A'.repeat(43);
const api = (path, options = {}) => rawApi(path, { bearer, ...options });

test('project configuration cannot silently fall back to global settings', () => {
  assert.equal(configurationTarget('project'), null);
  assert.throws(() => configurationTarget(), /explicit configuration scope/);
  assert.throws(() => configurationTarget('installations'), /explicit configuration scope/);
  assert.throws(() => configurationTarget('project', {}), /project identity/);
});

test('configuration requests retain their original project and drafts cannot collide with dashboard settings', () => {
  const project = { id: 'dashboard/second' };
  const selected = configurationTarget('project', project);
  const dashboard = configurationTarget('dashboard', project);
  const drafts = new Map([[selected.key, configDocument({ text: 'project draft', revision: 'project-v1' })],
    [dashboard.key, configDocument({ text: 'dashboard draft', revision: 'dashboard-v1' })]]);
  project.id = 'another-project';
  assert.ok(Object.isFrozen(selected));
  assert.equal(selected.projectId, 'dashboard/second');
  assert.equal(selected.endpoint, '/api/projects/dashboard%2Fsecond/config');
  assert.equal(dashboard.endpoint, '/api/dashboard/config');
  assert.equal(dashboard.projectId, null);
  assert.equal(drafts.get(selected.key).text, 'project draft');
  assert.equal(drafts.get(dashboard.key).text, 'dashboard draft');
  assert.equal(drafts.has(configurationTarget('project', project).key), false);
  assert.equal(configurationTarget('dashboard').key, dashboard.key);
});

const snapshot = {
  capabilities: { startSession: true },
  projects: [{ id: 'old', name: 'A old project' }, { id: 'new', name: 'B recent project' }, { id: 'active', name: 'C active project' }],
  sessions: [{ contextNamespace: 'local:a', runtimeId: '1', projectId: 'old', state: 'stopped', createdAt: '2026-01-01', lastActiveAt: '2026-01-01' },
    { contextNamespace: 'local:a', runtimeId: '2', projectId: 'new', state: 'stopped', createdAt: '2026-09-01', lastActiveAt: '2026-09-01', label: 'review parser' },
    { contextNamespace: 'local:a', runtimeId: '3', projectId: 'active', state: 'running', createdAt: '2026-05-01', lastActiveAt: '2026-05-01', agent: 'codex' }],
};
const combined = {
  capabilities: { dashboardConfigRead: true, configWrite: true, startSession: true },
  workspaces: [
    { id: 'local', label: 'This computer', kind: 'local', status: 'available', capabilities: { startSession: true, configWrite: true, projectCreate: true, projectRegister: true, attachTerminal: true } },
    { id: 'cluster', label: 'Example cluster', kind: 'cluster', status: 'available', capabilities: { startSession: true, configWrite: false, attachTerminal: false }, clusterSettings: { timeMinutes: 5 } },
  ],
  projects: [{ id: 'local-project', name: 'Local project', workspaceId: 'local' }, { id: 'cluster-project', name: 'Remote project', workspaceId: 'cluster' }],
  sessions: [
    { contextNamespace: 'local:one', runtimeId: 'same-id', projectId: 'local-project', workspaceId: 'local', state: 'running' },
    { contextNamespace: 'cluster:one', runtimeId: 'same-id', projectId: 'cluster-project', workspaceId: 'cluster', state: 'running', capabilities: { attachTerminal: true } },
  ],
};
test('project and session capabilities never inherit another machine global privilege', () => {
  assert.equal(capability(combined, 'configWrite', combined.projects[0]), true);
  assert.equal(capability(combined, 'configWrite', combined.projects[1]), false);
  assert.equal(capability(combined, 'projectCreate', combined.projects[1]), false);
  assert.equal(capability(combined, 'attachTerminal', combined.sessions[1]), true);
  assert.equal(workspaceFor(combined, combined.sessions[1]).id, 'cluster');
  assert.equal(capability(combined, 'configWrite', { workspaceId: 'unregistered', capabilities: { configWrite: true } }), false);
  const offline = { ...combined, workspaces: combined.workspaces.map(workspace => ({ ...workspace, status: 'unavailable' })) };
  assert.equal(capability(offline, 'attachTerminal', combined.sessions[1]), false);
  assert.equal(workspaceCapability(offline, 'local', 'projectCreate'), false);
  assert.equal(capability(combined, 'dashboardConfigRead'), true);
});
test('combined inventory refuses ambiguous or cross-machine project and session identities', () => {
  assert.equal(validateSnapshot(combined), combined);
  assert.throws(() => validateSnapshot({ ...combined, workspaces: [...combined.workspaces, combined.workspaces[0]] }), /ambiguous machine/);
  assert.throws(() => validateSnapshot({ ...combined, projects: [{ ...combined.projects[0], workspaceId: 'unknown' }] }), /ambiguous project/);
  assert.throws(() => validateSnapshot({ ...combined, sessions: [{ ...combined.sessions[0], workspaceId: 'cluster' }] }), /ambiguous session/);
});
test('switching terminal views across machines retains live connections and keeps input and hidden protocol replies on their own namespace', () => {
  const terminals = new Map(), transports = new Map();
  const registry = new TerminalViewRegistry({
    createTerminal(target, { onInput }) {
      const terminal = { write() {}, setVisible() {}, setInputEnabled() {}, resize() {}, focus() {}, dispose() {}, onInput };
      terminals.set(target.contextNamespace, terminal); return terminal;
    },
    createTransport(target, callbacks) {
      const transport = { input: [], closes: 0, open() { callbacks.onOpen(); }, sendInput(data) { this.input.push(data); }, resize() {}, close() { this.closes++; } };
      transports.set(target.contextNamespace, transport); return transport;
    },
  });
  for (const session of combined.sessions) {
    registry.show(session); registry.attach(session);
  }
  const local = combined.sessions[0];
  registry.show(local);
  terminals.get('local:one').onInput('local only');
  terminals.get('cluster:one').onInput('hidden protocol reply');
  assert.equal(registry.size, 2);
  assert.equal(registry.get(combined.sessions[1]).state, 'connected');
  assert.equal(transports.get('local:one').closes, 0);
  assert.equal(transports.get('cluster:one').closes, 0);
  assert.deepEqual(transports.get('local:one').input, ['local only']);
  assert.deepEqual(transports.get('cluster:one').input, ['hidden protocol reply']);
  registry.disposeAll();
});
test('project setup pairs machine-local roots and installations even when native IDs coincide', () => {
  const metadata = { roots: [{ id: 'projects', workspaceId: 'local', path: '/local' }, { id: 'projects', workspaceId: 'cluster', path: '/remote' }],
    installations: [{ id: 'default', workspaceId: 'local' }, { id: 'default', workspaceId: 'cluster' }] };
  const local = projectSetupOptions(combined, metadata, 'local');
  assert.deepEqual(local.roots.map(root => root.path), ['/local']);
  assert.deepEqual(local.installations.map(item => item.workspaceId), ['local']);
  assert.deepEqual(local.modes.map(item => item.value), ['create', 'register']);
  assert.deepEqual(projectSetupOptions(combined, metadata, 'cluster').modes, []);
  assert.deepEqual(projectSetupOptions(combined, metadata, 'unregistered').roots, []);
});
test('literal scheduler placeholders do not become misleading queue explanations', () => {
  for (const value of ['None', '(null)', 'N/A', '', undefined]) assert.equal(schedulerReason(value), '');
  const waiting = sessionGuidance({ state: 'queued', queueReason: 'None' }, { viewState: 'detached' });
  assert.doesNotMatch(waiting.message, /Scheduler reason: None/);
  assert.equal(schedulerReason('Resources'), 'Resources');
});
test('unavailable machine guidance gives scoped recovery steps without assuming authentication failure or ended sessions', () => {
  const remote = workspaceAvailability({ kind: 'cluster', status: 'unavailable', clusterSettings: { sshAlias: 'example-cluster' } });
  assert.match(remote, /If SSH access has expired/);
  assert.match(remote, /normal terminal, run ssh example-cluster/);
  assert.match(remote, /then Refresh/);
  assert.match(remote, /may still be running/);
  assert.doesNotMatch(remote, /new session|force attach/i);
  assert.match(workspaceAvailability({ kind: 'cluster', status: 'unavailable', clusterSettings: { sshAlias: '-o unsafe' } }), /configured SSH alias/);
  const local = workspaceAvailability({ kind: 'local', status: 'unavailable' });
  assert.match(local, /cause is not yet identified/);
  assert.match(local, /Check again/);
  assert.doesNotMatch(local, /Docker|ssh /i);
  assert.equal(workspaceAvailability({ status: 'available', notice: 'Ready on this machine.' }), 'Ready on this machine.');
});
test('initial checking disables combined actions and gives no premature runtime or SSH repair advice', () => {
  const checking = { ...combined, workspaces: combined.workspaces.map(workspace => ({ ...workspace, status: 'checking' })) };
  assert.equal(capability(checking, 'startSession', combined.projects[0]), false);
  assert.equal(capability(checking, 'attachTerminal', combined.sessions[1]), false);
  assert.equal(workspaceCapability(checking, 'local', 'projectCreate'), false);
  for (const workspace of checking.workspaces) {
    const copy = workspaceAvailability(workspace);
    assert.match(copy, /Checking this machine/);
    assert.match(copy, /until its status is verified/);
    assert.doesNotMatch(copy, /Docker|ssh|expired/i);
    assert.match(copy, /does not mean its sessions have ended/);
  }
});

test('structured SSH diagnostics explain recovery without turning unavailable sessions into ended work', () => {
  const workspace = { kind: 'cluster', status: 'unavailable', clusterSettings: { sshAlias: 'example-cluster',
    connectionDiagnostic: { code: 'ssh-authentication-required', message: 'SSH authentication is required.',
      recovery: 'Complete login in your own terminal. Keep it open if connection sharing does not persist.' } } };
  const text = workspaceAvailability(workspace);
  assert.match(text, /SSH authentication is required/);
  assert.match(text, /own terminal/);
  assert.match(text, /Keep it open/);
  assert.match(text, /Session state is unverified/);
  assert.match(text, /may still be running/);
  assert.match(text, /No launch will be retried/);
  assert.equal(sshRecoveryCommand(workspace), 'ssh example-cluster');
  assert.equal(sshRecoveryCommand({ ...workspace, status: 'available' }), null);
  assert.equal(sshRecoveryCommand({ ...workspace, kind: 'local' }), null);
  for (const alias of ['-oProxyCommand=bad', 'site;command', 'site\ncommand', 'a'.repeat(129)]) {
    assert.equal(sshRecoveryCommand({ ...workspace, clusterSettings: { sshAlias: alias } }), null);
  }
  assert.doesNotMatch(workspaceAvailability({ ...workspace, status: 'checking' }), /authentication is required/);
  assert.equal(workspaceAvailability({ ...workspace, status: 'available' }), 'Connected workspace');
});

test('workspace diagnostics lead with the verified repair and clear when the machine recovers', () => {
  for (const [code, message, recovery] of [
    ['botainer-installation-changed', 'The selected Botainer installation changed.', 'Review the Botainer update before reconnecting.'],
    ['dashboard-restart-required', 'The dashboard needs to restart.', 'Restart the dashboard service, then refresh.'],
  ]) {
    for (const kind of ['local', 'cluster']) {
      const workspace = { kind, status: 'unavailable', observationStatus: 'failed',
        connectionDiagnostic: { code, message, recovery } };
      const copy = workspaceAvailability(workspace);
      assert.ok(copy.startsWith(`${message} ${recovery}`));
      assert.match(copy, /Status check failed/);
      assert.match(copy, /may still be running/);
      assert.match(copy, /Project inventory may be incomplete/);
      assert.match(copy, /missing projects are not confirmed deleted/);
      assert.match(copy, /controls stay disabled until verified/);
      assert.doesNotMatch(copy, /Docker|SSH access has expired/);
      if (code === 'botainer-installation-changed') {
        assert.match(workspaceConnectionPresentation(workspace).recovery, /maintainer/);
        assert.match(copy, /This alpha cannot do that review in the GUI/);
        assert.match(copy, /Restarting alone will not fix it/);
      }
      assert.equal(workspaceAvailability({ ...workspace, status: 'available', observationStatus: 'current' }), 'Connected workspace');
      assert.equal(workspaceAvailability({ ...workspace, status: 'available', observationStatus: 'refreshing', notice: 'Ready.' }), 'Ready.');
      assert.doesNotMatch(workspaceAvailability({ ...workspace, status: 'checking', observationStatus: 'checking' }), /installation changed|needs to restart/);
    }
  }
});

test('generic workspace errors retain specific SSH recovery and slow checks do not invent update advice', () => {
  const generic = { code: 'workspace-check-failed', message: 'The workspace check failed.', recovery: 'Review the selected connection.' };
  const ssh = { code: 'ssh-authentication-required', message: 'SSH authentication is required.', recovery: 'Complete login in your own terminal.' };
  const workspace = { kind: 'cluster', status: 'unavailable', observationStatus: 'failed',
    connectionDiagnostic: generic, clusterSettings: { connectionDiagnostic: ssh } };
  assert.ok(workspaceAvailability(workspace).startsWith(`${ssh.message} ${ssh.recovery}`));
  assert.doesNotMatch(workspaceAvailability(workspace), /The workspace check failed/);
  const changed = { code: 'botainer-installation-changed', message: 'The Botainer installation changed.', recovery: 'Review its update.' };
  assert.ok(workspaceAvailability({ ...workspace, connectionDiagnostic: changed }).startsWith(changed.message));
  for (const observationStatus of ['delayed', 'stale']) {
    const slow = { ...workspace, observationStatus };
    assert.ok(workspaceAvailability(slow).startsWith(observationStatusLabel(slow)));
    assert.doesNotMatch(workspaceAvailability(slow), /check failed|authentication is required|update|restart|Docker/);
    // A concrete diagnosis explicitly retained after failure remains useful during a retry.
    assert.ok(workspaceAvailability({ ...slow, connectionDiagnostic: changed }).startsWith(changed.message));
  }
});

test('malformed workspace diagnostics are bounded and cannot create recognized repair labels', () => {
  const diagnostic = { code: 'botainer-installation-changed', message: 'DIAGNOSTIC MESSAGE', recovery: 'DIAGNOSTIC RECOVERY' };
  for (const connectionDiagnostic of [null, [], 'bad', { ...diagnostic, code: '__proto__' },
    { ...diagnostic, code: 'botainer-installation-changed-extra' }, { ...diagnostic, code: 'x'.repeat(129) },
    { ...diagnostic, message: 'm'.repeat(1001) }, { ...diagnostic, recovery: 'r'.repeat(1501) },
    { ...diagnostic, message: {} }, { ...diagnostic, recovery: [] }, { ...diagnostic, message: ' ' },
    { ...diagnostic, recovery: '' }]) {
    const workspace = { id: 'local', kind: 'local', status: 'unavailable', connectionDiagnostic };
    assert.doesNotMatch(workspaceAvailability(workspace), /DIAGNOSTIC/);
    assert.equal(workspaceSummaries({ ...combined, workspaces: [workspace] })[0].status, 'unavailable');
  }
  const valid = { kind: 'local', status: 'unavailable', connectionDiagnostic: {
    ...diagnostic, message: 'm'.repeat(1000), recovery: 'r'.repeat(1500) } };
  const copy = workspaceAvailability(valid);
  assert.ok(copy.startsWith(`${valid.connectionDiagnostic.message} ${valid.connectionDiagnostic.recovery}`));
  assert.ok(copy.length < 3200);
});

test('incomplete project inventory notice follows the displayed scope and disappears after recovery', () => {
  const failed = { ...combined, workspaces: combined.workspaces.map(workspace => workspace.id === 'cluster'
    ? { ...workspace, status: 'unavailable' } : workspace) };
  const before = JSON.stringify(failed);
  assert.match(projectInventoryNotice(failed), /may be incomplete/);
  assert.match(projectInventoryNotice(failed, 'cluster'), /missing projects are not confirmed deleted/);
  assert.equal(projectInventoryNotice(failed, 'local'), '');
  assert.match(projectInventoryNotice({ ...failed, projects: [] }), /may be incomplete/);
  assert.match(projectInventoryNotice(combined, null, { connected: false }), /may be incomplete/);
  assert.equal(projectInventoryNotice(combined), '');
  assert.equal(JSON.stringify(failed), before);
});

test('known SSH causes reach connection summaries, project warnings and visible recovery presentation', () => {
  for (const [code, summary] of [
    ['ssh-authentication-required', 'SSH sign-in needed'], ['ssh-host-key-unverified', 'Verify SSH host key'],
    ['ssh-host-key-changed', 'SSH host key changed'], ['ssh-name-resolution-failed', 'Host name not found'],
    ['ssh-connection-refused', 'SSH connection refused'], ['ssh-network-unreachable', 'SSH host unreachable'],
    ['ssh-connection-lost', 'SSH connection lost'], ['ssh-timeout', 'Cluster check timed out'],
    ['ssh-local-client-unavailable', 'SSH client unavailable'],
  ]) {
    const diagnostic = { code, message: 'A specific observed connection problem.', recovery: 'Follow this specific recovery step.' };
    const workspace = { ...combined.workspaces[1], status: 'unavailable', observationStatus: 'failed',
      connectionDiagnostic: { code: 'workspace-check-failed', message: 'Generic check failure.', recovery: 'Generic advice.' },
      clusterSettings: { sshAlias: 'example-cluster', connectionDiagnostic: diagnostic } };
    const current = { ...combined, workspaces: [workspace] };
    assert.equal(workspaceSummaries(current)[0].status, summary);
    assert.equal(projectListSignals(current, combined.projects[1], combined.sessions).warning, summary);
    assert.equal(projectStatusSummary(combined.projects[1], combined.sessions, { workspace }), `Activity unverified · ${summary}`);
    const presentation = workspaceConnectionPresentation(workspace);
    assert.equal(presentation.summary, summary); assert.ok(presentation.message.startsWith(diagnostic.message));
    assert.match(presentation.message, /Existing sessions may still be running/);
    assert.match(presentation.message, /controls stay disabled until verified/);
    assert.equal(presentation.recovery, diagnostic.recovery); assert.equal(presentation.command, 'ssh example-cluster');
    const recovered = workspaceConnectionPresentation({ ...workspace, status: 'available' });
    assert.equal(recovered.summary, 'Connected'); assert.equal(recovered.recovery, ''); assert.equal(recovered.command, null);
    const delayed = workspaceConnectionPresentation({ ...workspace, observationStatus: 'delayed' });
    assert.equal(delayed.summary, 'Status check delayed'); assert.equal(delayed.command, null);
    assert.doesNotMatch(delayed.message, /specific observed|failed|sign-in/);
    const disconnected = workspaceConnectionPresentation(workspace, { connected: false });
    assert.equal(disconnected.summary, 'Dashboard service offline'); assert.equal(disconnected.command, null);
  }
});

test('unknown connection failures give an honest cause boundary and accessible recovery actions', () => {
  for (const code of ['workspace-check-failed', 'remote-check-failed', 'ssh-request-failed']) {
    const diagnostic = { code, message: 'Could not verify.', recovery: 'Inspect a private receipt.' };
    const workspace = { kind: code === 'workspace-check-failed' ? 'local' : 'cluster', status: 'unavailable', observationStatus: 'failed',
      connectionDiagnostic: code === 'workspace-check-failed' ? diagnostic : undefined,
      clusterSettings: { sshAlias: 'example-cluster', connectionDiagnostic: diagnostic } };
    const presentation = workspaceConnectionPresentation(workspace);
    assert.match(presentation.message, /could not read current Botainer or session status/);
    assert.match(presentation.message, /cause is not yet identified/);
    assert.match(presentation.recovery, /Connection help/);
    assert.doesNotMatch(JSON.stringify(presentation), /private receipt|Docker|stopped/);
    const availability = workspaceAvailability(workspace);
    assert.match(availability, /cause is not yet identified/);
    assert.match(availability, /Connection help/);
    assert.match(availability, /may still be running/);
    assert.doesNotMatch(availability, /private receipt|private diagnostic|Docker/);
  }
  assert.match(workspaceAvailability({ kind: 'local', status: 'unavailable', observationStatus: 'failed' }), /cause is not yet identified/);
  const host = { kind: 'local', executionKind: 'host', status: 'unavailable', observationStatus: 'failed',
    connectionDiagnostic: { code: 'workspace-check-failed', message: 'Connection failed.', recovery: 'Read private diagnostic evidence.' } };
  for (const copy of [workspaceAvailability(host), JSON.stringify(workspaceConnectionPresentation(host))]) {
    assert.match(copy, /current status for this connection/);
    assert.match(copy, /configured agent executables/);
    assert.doesNotMatch(copy, /Botainer|installation|private diagnostic|Docker/);
  }
});

test('standalone cluster diagnostics use the same machine guidance without inventing a selected combined scope', () => {
  const single = { connectionStatus: 'unavailable', clusterSettings: { site: 'Example site', sshAlias: 'site-login' }, capabilities: {} };
  const workspace = displayedWorkspace(single, null);
  assert.equal(workspace.status, 'unavailable');
  assert.equal(workspace.label, 'Example site');
  assert.equal(sshRecoveryCommand(workspace), 'ssh site-login');
  assert.equal(displayedWorkspace(combined, 'local'), combined.workspaces[0]);
  assert.equal(displayedWorkspace(combined, null), null);
  assert.equal(displayedWorkspace({ capabilities: {} }, null), null);
});
test('late action results cannot replace a changed scope or subsequent user navigation and editing', () => {
  const expected = Object.freeze({ revision: 4, workspaceId: 'local', projectId: 'project-one',
    targetKey: '["local:one","session-one"]', inspector: null, focus: 'start-button' });
  assert.equal(presentationUnchanged(expected, { ...expected }), true);
  for (const [key, value] of [['workspaceId', 'cluster'], ['projectId', 'project-two'],
    ['targetKey', '["cluster:one","session-one"]'], ['inspector', 'config'],
    ['revision', 5]]) {
    assert.equal(presentationUnchanged(expected, { ...expected, [key]: value }), false, key);
  }
  // Moving away and returning is still interaction: do not steal a user's view
  // merely because its identifiers once again match the launch-time selection.
  assert.equal(presentationUnchanged(expected, { ...expected, revision: 9 }), false);
  assert.equal(presentationUnchanged(expected, { ...expected, focus: 'config-editor', revision: 5 }), false);
  assert.equal(presentationUnchanged(null, expected), false);
});
test('ordinary dialog focus restoration or poll DOM replacement does not cancel default result navigation', () => {
  const expected = { revision: 4, workspaceId: null, projectId: 'original', targetKey: null,
    inspector: null, focus: { id: 'confirm-action' } };
  for (const focus of [{ id: 'add-project' }, { id: 'body' }, null]) {
    assert.equal(presentationUnchanged(expected, { ...expected, focus }), true);
  }
  assert.equal(presentationUnchanged(expected, { ...expected, focus: { id: 'editor' }, revision: 5 }), false);
});
test('pins precede recent activity and agent/session search works without mutating inventory', () => {
  assert.deepEqual(sortProjects(snapshot).map(project => project.id), ['new', 'active', 'old']);
  assert.deepEqual(sortProjects(snapshot, { pins: ['old'] }).map(project => project.id), ['old', 'new', 'active']);
  assert.deepEqual(sortProjects(snapshot, { sort: 'active' }).map(project => project.id), ['active', 'new', 'old']);
  assert.deepEqual(sortProjects(snapshot, { query: 'CODEx' }).map(project => project.id), ['active']);
  assert.deepEqual(sortProjects(snapshot, { query: 'parser' }).map(project => project.id), ['new']);
  assert.deepEqual(snapshot.projects.map(project => project.id), ['old', 'new', 'active']);
});
test('capabilities require explicit backend support and entity refusal wins', () => {
  assert.equal(capability(snapshot, 'startSession'), true);
  assert.equal(capability(snapshot, 'stopSession'), false);
  assert.equal(capability(snapshot, 'startSession', { capabilities: { startSession: false } }), false);
  assert.equal(capability(snapshot, 'stopSession', { capabilities: { stopSession: true } }), true);
  assert.equal(capability(snapshot, 'startSession', { capabilities: { startSession: 'true' } }), false);
  assert.equal(capability({ capabilities: { startSession: 'true' } }, 'startSession'), false);
  assert.equal(stateClass('running extra attacker-class'), 'unknown');
});
test('session controls require an observed compatible state as well as capability', () => {
  const inventory = { capabilities: { attachTerminal: true, stopSession: true } };
  assert.deepEqual(sessionControls(inventory, { state: 'running' }), { attach: true, stop: true });
  for (const state of ['queued', 'starting']) {
    assert.deepEqual(sessionControls(inventory, { state }), { attach: false, stop: true });
  }
  for (const state of ['unknown', 'stopped', 'failed', 'new-upstream-state', undefined]) {
    assert.deepEqual(sessionControls(inventory, { state }), { attach: false, stop: false });
  }
  assert.deepEqual(sessionControls(inventory, null), { attach: false, stop: false });
  assert.deepEqual(sessionControls(inventory, { state: 'running', capabilities: { attachTerminal: false, stopSession: false } }), { attach: false, stop: false });
  for (const options of [{ connected: false }, { busy: true }]) {
    assert.deepEqual(sessionControls(inventory, { state: 'running' }, options), { attach: false, stop: false });
  }
});
test('allocation stop warns that the exact Slurm job and all its workloads are cancelled', () => {
  for (const state of ['running', 'queued', 'starting']) {
    const session = { contextNamespace: 'remote:test', runtimeId: 'native-session', state,
      stopScope: 'allocation', jobId: '12345', capabilities: { stopSession: true } };
    const stop = sessionStopPresentation(snapshot, session, { name: 'Project one' });
    assert.equal(stop.label, 'Cancel Slurm job');
    assert.equal(stop.title, 'Cancel Slurm job 12345?');
    assert.match(stop.description, /Slurm job 12345.*Project one/);
    assert.match(stop.description, /entire allocation.*all work within it/);
    assert.match(stop.description, /other sessions or Open OnDemand \(OOD\) work/);
    assert.match(stop.description, /does not know how many other workloads/);
    assert.match(stop.description, /Other allocations on the same compute node are not cancelled/);
    assert.match(stop.result, /requested.*scheduler outcome/);
    assert.equal(sessionControls(snapshot, session).stop, true);
    assert.equal(sessionControls(snapshot, { ...session, capabilities: { stopSession: false } }).stop, false);
  }
  assert.equal(sessionStopPresentation(snapshot, { runtimeId: 'request-id', stopScope: 'allocation' }).title,
    'Cancel this Slurm job?');
});
test('stop scope metadata does not relabel local containers or native host terminals', () => {
  const local = { contextNamespace: 'local:test', runtimeId: 'container', state: 'running' };
  assert.equal(sessionStopPresentation(snapshot, local).label, 'Stop session');
  assert.equal(sessionStopPresentation(snapshot, { ...local, state: 'starting' }).label, 'Cancel session');
  assert.equal(sessionStopPresentation(snapshot, { ...local, jobId: '12345' }).label, 'Stop session');
  const host = sessionStopPresentation(snapshot, { ...local, executionKind: 'host' });
  assert.equal(host.label, 'Close host terminal');
  assert.match(host.description, /Detached background work.*may continue/);
});
test('queued and starting terminal guidance waits for the existing launch instead of connecting or resubmitting', () => {
  const options = { viewState: 'detached', canAttach: true, canStart: true, remote: true };
  const queued = sessionGuidance({ state: 'queued', queueReason: 'Priority' }, options);
  assert.equal(queued.action, null);
  assert.match(queued.title, /allocation/);
  assert.match(queued.message, /Priority/);
  assert.match(queued.note, /No terminal/);
  const starting = sessionGuidance({ state: 'starting' }, options);
  assert.equal(starting.action, null);
  assert.match(starting.message, /do not submit another launch/);
  assert.match(starting.note, /not yet confirmed/);
});
test('unknown and stale inventory preserve uncertainty and never suggest that the process ended', () => {
  const options = { viewState: 'detached', canAttach: true, canStart: true, remote: true };
  for (const state of ['unknown', 'new-upstream-state']) {
    const guidance = sessionGuidance({ state, lastKnownState: 'running', lastObservedAt: '2026-09-20T12:00:00Z', unavailableReason: 'SSH is unavailable.' }, options);
    assert.equal(guidance.action, null);
    assert.match(guidance.title, /unverified/);
    assert.match(guidance.message, /SSH is unavailable/);
    assert.match(guidance.message, /Last known state: running/);
    assert.match(guidance.message, /2026-09-20T12:00:00Z/);
    assert.doesNotMatch(guidance.note, /Session ended/);
  }
  const missing = sessionGuidance(null, options);
  assert.equal(missing.action, null);
  assert.match(missing.title, /unverified/);
  const stale = sessionGuidance({ state: 'running' }, { ...options, serviceConnected: false, viewState: 'connected' });
  assert.equal(stale.action, null);
  assert.match(stale.title, /stale/);
  assert.match(stale.note, /Terminal connected; inventory is unavailable/);
});
test('delayed, failed and expired observations have stable quiet connected-terminal presentation', () => {
  const base = { ...combined.sessions[0], state: 'unknown', stale: true, lastKnownState: 'running',
    lastKnownAt: '2026-09-20T12:00:00Z', capabilities: { attachTerminal: false, stopSession: false } };
  for (const [status, title, detail] of [
    ['delayed', 'Status check delayed', /still pending/],
    ['failed', 'Status check failed', /check failed/],
    ['stale', 'Status out of date', /too old/],
  ]) {
    const session = { ...base, observationStatus: status };
    assert.equal(observationStatusLabel(session), title);
    assert.equal(sessionStateLabel(session), `${title} · last running`);
    const connected = sessionGuidance(session, { viewState: 'connected', canAttach: true, canStart: true, canStop: true });
    assert.equal(connected.title, title);
    assert.match(connected.message, detail);
    assert.match(connected.message, /Last reported: running at 2026-09-20T12:00:00Z/);
    assert.match(connected.message, /Checks continue automatically/);
    assert.match(connected.note, /Terminal connected/);
    assert.equal(connected.quiet, true); // Render as the existing note, not a tall terminal banner.
    assert.equal(connected.action, null);
    const detached = sessionGuidance(session, { viewState: 'detached', canAttach: true, canStart: true });
    assert.equal(detached.quiet, false);
    assert.match(detached.note, /controls unavailable/);
    assert.equal(detached.action, null);
    const current = { ...combined, workspaces: [{ ...combined.workspaces[0], status: 'unavailable', observationStatus: status }],
      sessions: [session], projects: [{ ...combined.projects[0], stale: true, observationStatus: status }] };
    assert.deepEqual(sessionControls(current, session), { attach: false, stop: false });
    assert.equal(projectStatusSummary(current.projects[0], current.sessions), `Activity unverified · ${title}`);
  }
  const recovered = sessionGuidance({ ...base, state: 'running', stale: false, observationStatus: 'current' },
    { viewState: 'connected', canAttach: true });
  assert.doesNotMatch(recovered.note, /check delayed|check failed|out of date/);
  assert.notEqual(recovered.quiet, true);
});
test('last reported states expose receipt time and age without inventing a verified agent state', () => {
  const session = { lastKnownState: 'running', lastKnownAt: '2026-09-20T12:00:00Z' };
  const value = lastReportedSession(session, Date.parse('2026-09-20T12:02:00Z'));
  assert.match(value, /Last reported: running at 2026-09-20T12:00:00Z/);
  assert.match(value, /2m ago/);
  assert.doesNotMatch(value, /verified|ready|working/);
  assert.equal(lastReportedSession({ lastKnownState: 'unknown', lastKnownAt: session.lastKnownAt }), '');
  assert.equal(lastReportedSession({ lastKnownState: 'running', lastKnownAt: '<script>bad</script>' }), 'Last reported: running.');
  for (const observationStatus of ['refreshing', 'current', 'checking', '__proto__', '<img src=x>', null]) {
    assert.equal(observationStatusLabel({ observationStatus }), '');
  }
});
test('machine observation labels distinguish slow checks from failure and retain SSH recovery details', () => {
  const workspace = { ...combined.workspaces[1], status: 'unavailable', observationStatus: 'delayed',
    lastObservationAt: '2026-09-20T12:00:00Z', clusterSettings: { sshAlias: 'example-cluster' } };
  const delayed = workspaceAvailability(workspace);
  assert.match(delayed, /^Status check delayed/);
  assert.match(delayed, /Last successful status received at 2026-09-20T12:00:00Z/);
  assert.match(delayed, /Checks continue automatically/);
  assert.doesNotMatch(delayed, /run ssh|Docker|expired|has stopped/);
  assert.equal(sshRecoveryCommand(workspace), null);
  assert.equal(workspaceSummaries({ ...combined, workspaces: [workspace] })[0].status, 'Status check delayed');
  const failed = { ...workspace, observationStatus: 'failed', clusterSettings: { ...workspace.clusterSettings,
    connectionDiagnostic: { message: 'SSH authentication is required.', recovery: 'Complete login in your own terminal.' } } };
  assert.match(workspaceAvailability(failed), /^SSH authentication is required/);
  assert.match(workspaceAvailability(failed), /Status check failed/);
  assert.equal(sshRecoveryCommand(failed), 'ssh example-cluster');
  // Ordinary fast in-flight refreshes remain visually connected.
  for (const observationStatus of ['refreshing', 'current']) {
    const healthy = { ...workspace, status: 'available', observationStatus, notice: 'Selected policy' };
    assert.equal(workspaceAvailability(healthy), 'Selected policy');
    assert.equal(workspaceSummaries({ ...combined, workspaces: [healthy] })[0].status, 'connected');
  }
});
test('remote view closure needs no manual detach and does not claim confirmed cleanup or replay lost input', () => {
  const detached = sessionGuidance({ state: 'running' }, { viewState: 'detached', canAttach: true, remote: true });
  assert.equal(detached.action, 'connect');
  assert.match(detached.message, /view cleanup may still be completing/);
  assert.match(detached.message, /session was not stopped/);
  assert.match(detached.message, /original owner/);
  assert.match(detached.message, /unconfirmed previous attachment will be refused/);
  const disconnected = sessionGuidance({ state: 'running', unavailableReason: 'Previous attachment cleanup is unconfirmed.' }, { viewState: 'disconnected', remote: true });
  assert.equal(disconnected.action, null);
  assert.match(disconnected.message, /cleanup is unconfirmed/);
  assert.match(disconnected.note, /will not be replayed/);
  for (const remote of [false, true]) {
    const lost = sessionGuidance({ state: 'running' }, { viewState: 'disconnected', canAttach: true, remote });
    assert.match(lost.message, /No manual detach is needed/);
  }
  const ended = sessionGuidance({ state: 'stopped' }, { viewState: 'detached', canStart: true });
  assert.equal(ended.action, 'start');
});

const launch = {
  contextNamespace: 'launch:request-scope', runtimeId: 'request-one', kind: 'launch',
  projectId: 'local-project', workspaceId: 'local', state: 'running', launchState: 'running',
  capabilities: { attachTerminal: true, stopSession: false },
};
const nativeSnapshot = { ...combined, mode: 'native-cli-workspace', projects: combined.projects.map(project =>
  project.id === launch.projectId ? { ...project, capabilities: { nativeCliLaunch: true } } : project) };
const completeLaunch = { ...launch, state: 'stopped', launchState: 'completed',
  resultTarget: { contextNamespace: 'local:one', runtimeId: 'same-id' }, capabilities: { attachTerminal: false, stopSession: false } };

const ordinarySnapshot = { ...nativeSnapshot, mode: 'ordinary-workspace', workspaces: nativeSnapshot.workspaces.map(item => item.id === 'local'
  ? { ...item, mode: 'ordinary-local', capabilities: { ...item.capabilities, agentOverride: true, nativeCliProjectSetup: true, projectOpen: true } } : item) };

test('per-run agent selection omits default and preserves project configuration and exact machine permissions', () => {
  const project = ordinarySnapshot.projects[0], before = JSON.stringify(ordinarySnapshot);
  assert.deepEqual(sessionLaunchRequest(ordinarySnapshot, project, 'default', 'request-default'), { requestId: 'request-default' });
  for (const agent of ['claude', 'codex']) assert.deepEqual(sessionLaunchRequest(ordinarySnapshot, project, agent, 'request-agent'),
    { requestId: 'request-agent', agent });
  assert.equal(JSON.stringify(ordinarySnapshot), before);
  assert.throws(() => sessionLaunchRequest(ordinarySnapshot, ordinarySnapshot.projects[1], 'codex', 'request'), /not available/);
  assert.throws(() => sessionLaunchRequest(ordinarySnapshot, { ...project, workspaceId: 'cluster' }, 'codex', 'request'), /no longer available/);
  for (const agent of ['', null, 'shell', 'claude --dangerously-skip-permissions']) {
    assert.throws(() => sessionLaunchRequest(ordinarySnapshot, project, agent, 'request'), /Choose Project default/);
  }
  const unavailable = { ...ordinarySnapshot, workspaces: ordinarySnapshot.workspaces.map(item => ({ ...item, status: 'unavailable' })) };
  assert.throws(() => sessionLaunchRequest(unavailable, project, 'default', 'request'), /no longer available/);
});

test('native folder setup is advertised per machine and retains legacy register choices', () => {
  const metadata = { roots: [{ id: 'projects', workspaceId: 'local' }], installations: [{ id: 'botainer', workspaceId: 'local' }] };
  assert.deepEqual(projectSetupOptions(ordinarySnapshot, metadata, 'local').modes.map(item => item.value), ['open', 'create']);
  assert.deepEqual(projectSetupOptions(ordinarySnapshot, metadata, 'cluster').modes, []);
  assert.match(projectSetupDescription(ordinarySnapshot, 'local'), /native init prompts/);
  assert.match(projectSetupDescription(ordinarySnapshot, 'local'), /does not start an agent/);
  assert.doesNotMatch(projectSetupDescription(ordinarySnapshot, 'local'), /trial defaults/);
  assert.match(projectSetupDescription(nativeSnapshot, 'local'), /trial defaults/);
});

test('setup POST opens only its exact native console in its selected workspace before a delayed poll', () => {
  const project = { id: 'setup-provisional', name: 'New folder', path: '/allowed/new-folder', workspaceId: 'local' };
  const session = { ...launch, runtimeId: 'setup-request', operation: 'init', projectId: project.id };
  const observed = includeProjectSetupResponse(ordinarySnapshot, { project, session }, 'local');
  assert.equal(observed.projects.at(-1), project);
  assert.equal(observed.sessions.at(-1), session);
  assert.equal(ordinarySnapshot.projects.length, 2);
  assert.equal(sessionDisplayLabel(session), 'Project setup · Botainer CLI');
  assert.throws(() => includeProjectSetupResponse(ordinarySnapshot, { project, session }, 'cluster'), /unsupported project setup/);
  assert.throws(() => includeProjectSetupResponse(ordinarySnapshot, { project, session: { ...session, workspaceId: 'cluster' } }, 'local'), /unsupported launch console/);
  assert.throws(() => includeProjectSetupResponse(ordinarySnapshot, { project, session: { ...session, operation: 'start' } }, 'local'), /unsupported project setup/);
  assert.throws(() => includeProjectSetupResponse(observed, { project: { ...project, path: '/different' }, session }, 'local'), /ambiguous project setup/);
  assert.equal(includeProjectSetupResponse(ordinarySnapshot, { project }, 'local').projects.at(-1), project);
});

test('native init completion identifies a same-machine project without launching or attaching a runtime', () => {
  const setup = { ...completeLaunch, operation: 'init', resultProjectId: 'local-project' };
  assert.equal(isProjectSetup(setup), true);
  assert.equal(nativeSetupResult(ordinarySnapshot, setup), ordinarySnapshot.projects[0]);
  assert.equal(nativeLaunchResult(ordinarySnapshot, setup), null);
  assert.equal(nativeSetupResult(ordinarySnapshot, { ...setup, resultProjectId: 'cluster-project' }), null);
  assert.equal(nativeSetupResult(ordinarySnapshot, { ...setup, launchState: 'unknown' }), null);
  assert.equal(nativeSetupResult(ordinarySnapshot, completeLaunch), null);
  assert.equal(sessionDisplayLabel(setup), 'Setup log · completed');
  const finished = sessionGuidance(setup, { viewState: 'detached', canAttach: true, canStart: true });
  assert.match(finished.message, /does not contain a running agent/);
  assert.equal(finished.action, 'log');
  const pending = { ...launch, operation: 'init' };
  assert.match(sessionGuidance(pending, { viewState: 'connected' }).message, /initialization prompts/);
  assert.match(projectWorkflow({ ...ordinarySnapshot, sessions: [pending] }, ordinarySnapshot.projects[0]), /Project setup is in progress/);
  const unknown = sessionGuidance({ ...pending, launchState: 'unknown' }, { viewState: 'disconnected', canStart: true });
  assert.match(unknown.title, /unverified/);
  assert.match(unknown.message, /Files may have been written/);
  assert.notEqual(unknown.action, 'start');
});

test('ordinary native project instructions do not inherit cached-shell or one-active trial claims', () => {
  const project = ordinarySnapshot.projects[0];
  assert.equal(preparedTrial(ordinarySnapshot, project), false);
  const empty = projectWorkflow({ ...ordinarySnapshot, sessions: [] }, project, { canStart: true });
  assert.match(empty, /Project default uses the saved agent/);
  assert.match(empty, /actual CLI/);
  assert.doesNotMatch(empty, /Alpine|no AI-agent credentials|local trial/);
  assert.doesNotMatch(projectWorkflow(ordinarySnapshot, project), /one active session|local trial/);
  const mixed = { ...ordinarySnapshot, mode: 'combined-workspace' };
  assert.equal(preparedTrial(mixed, project), false);
  assert.equal(preparedTrial(nativeSnapshot, nativeSnapshot.projects[0]), true);
});

test('a requested queued session waits for its exact running owner and never follows another session or workspace', () => {
  const session = combined.sessions[0], intent = terminalRecoveryIntent(session, { queued: true });
  assert.equal(terminalRecoveryTarget(combined, intent, session, { now: 0 }), session);
  assert.equal(terminalRecoveryTarget(combined, intent, combined.sessions[1], { now: 0 }), null);
  for (const options of [{ hidden: true }, { busy: true }, { connected: false }, { inspector: 'config' }]) {
    assert.equal(terminalRecoveryTarget(combined, intent, session, { now: 0, ...options }), null);
  }
  for (const patch of [{ state: 'queued' }, { state: 'starting' }, { state: 'unknown' }, { state: 'stopped' },
    { projectId: 'different' }, { workspaceId: 'cluster' }, { contextNamespace: 'another-machine' },
    { capabilities: { attachTerminal: false }, unavailableReason: 'previous attachment cleanup is unverified' }]) {
    const changed = { ...combined, sessions: [{ ...session, ...patch }] };
    assert.equal(terminalRecoveryTarget(changed, intent, session, { now: 0 }), null);
  }
  assert.equal(terminalRecoveryIntent(launch), null);
  assert.equal(terminalRecoveryTarget(combined, null, session), null);
  const connecting = beginTerminalRecovery(intent);
  assert.equal(connecting.attempts, 0);
  assert.equal(terminalRecoveryTarget(combined, connecting, session), null);
});

test('network recovery uses bounded delays without treating EOF, timeout, policy refusal or detach as network loss', () => {
  const session = combined.sessions[0];
  const event = (state, reason) => ({ target: session, state, reason });
  let intent = terminalRecoveryIntent(session);
  assert.equal(advanceTerminalRecovery(intent, event('disconnected', 'connection_lost'), 0), null);
  intent = advanceTerminalRecovery(intent, event('connected'), 0);
  for (const reason of ['session_eof', 'connection_closed', 'transport_error', 'connection_timeout', 'output_failed']) {
    assert.equal(advanceTerminalRecovery(intent, event('disconnected', reason), 1), null);
  }
  assert.equal(advanceTerminalRecovery(intent, event('detached'), 1), null);
  assert.equal(advanceTerminalRecovery(intent, event('disposed'), 1), null);
  let now = 0;
  for (const [index, delay] of [2000, 5000, 10000].entries()) {
    intent = advanceTerminalRecovery(intent, event('disconnected', 'connection_lost'), now + 1);
    assert.equal(intent.nextAt, now + 1 + delay);
    assert.equal(terminalRecoveryTarget(combined, intent, session, { now: intent.nextAt - 1 }), null);
    assert.equal(terminalRecoveryTarget(combined, intent, session, { now: intent.nextAt }), session);
    now = intent.nextAt;
    intent = beginTerminalRecovery(intent);
    assert.equal(intent.attempts, index + 1);
    intent = advanceTerminalRecovery(intent, event('connected'), now);
  }
  assert.equal(advanceTerminalRecovery(intent, event('disconnected', 'connection_lost'), now + 1), null);
  const stableLoss = advanceTerminalRecovery(intent, event('disconnected', 'connection_lost'), now + 30001);
  assert.equal(stableLoss.attempts, 0);
  assert.equal(terminalRecoveryTarget(combined, stableLoss, session, { now: stableLoss.expiresAt + 1 }), null);
});

test('project primary action connects only its sole verified live target or starts explicitly when no current work exists', () => {
  const project = nativeSnapshot.projects[0], session = nativeSnapshot.sessions[0];
  assert.deepEqual(projectPrimaryAction(nativeSnapshot, project), { kind: 'connect', label: 'Connect session',
    target: { contextNamespace: session.contextNamespace, runtimeId: session.runtimeId } });
  const empty = { ...nativeSnapshot, sessions: [completeLaunch] };
  assert.deepEqual(projectPrimaryAction(empty, project), { kind: 'start', label: 'Start session' });
  assert.equal(projectPrimaryAction(empty, { id: 'unregistered', capabilities: { startSession: true } }), null);
  assert.deepEqual(projectPrimaryAction({ ...nativeSnapshot, sessions: [launch] }, project), {
    kind: 'connect', label: 'Resume launch console', target: { contextNamespace: launch.contextNamespace, runtimeId: launch.runtimeId } });
  for (const state of ['queued', 'starting', 'unknown']) {
    assert.equal(projectPrimaryAction({ ...nativeSnapshot, sessions: [{ ...session, state }] }, project), null);
  }
  assert.equal(projectPrimaryAction({ ...nativeSnapshot, sessions: [session, { ...session, runtimeId: 'second-run' }] }, project), null);
  assert.equal(projectPrimaryAction({ ...nativeSnapshot, sessions: [session, { ...launch, launchState: 'unknown', state: 'unknown' }] }, project), null);
  assert.equal(projectPrimaryAction({ ...nativeSnapshot, workspaces: nativeSnapshot.workspaces.map(workspace => ({ ...workspace, status: 'unavailable' })) }, project), null);
  for (const options of [{ connected: false }, { busy: true }]) assert.equal(projectPrimaryAction(nativeSnapshot, project, options), null);
});

test('native launch POST observation opens its exact console even when inventory has not caught up', () => {
  const observed = includeLaunchResponse(nativeSnapshot, nativeSnapshot.projects[0], launch);
  assert.equal(observed.sessions.at(-1), launch);
  assert.equal(nativeSnapshot.sessions.length, 2);
  assert.equal(sessionControls(observed, launch).attach, true);
  const updated = includeLaunchResponse(observed, observed.projects[0], completeLaunch);
  assert.equal(updated.sessions.length, 3);
  assert.equal(updated.sessions.at(-1), completeLaunch);
  for (const changed of [{ projectId: 'cluster-project' }, { workspaceId: 'cluster' }, { kind: 'container' },
    { runtimeId: '' }, { contextNamespace: '' }]) {
    assert.throws(() => includeLaunchResponse(nativeSnapshot, nativeSnapshot.projects[0], { ...launch, ...changed }), /launch console|session identity/);
  }
  assert.throws(() => includeLaunchResponse(combined, combined.projects[0], launch), /unsupported launch/);
  assert.throws(() => includeLaunchResponse(nativeSnapshot, nativeSnapshot.projects[0], {
    ...launch, ...completeLaunch.resultTarget,
  }), /ambiguous launch/);
});

test('launch completion resolves only a verified exact target in its own project and machine', () => {
  assert.equal(nativeLaunchResult(nativeSnapshot, completeLaunch), nativeSnapshot.sessions[0]);
  for (const changed of [{ launchState: 'running' }, { launchState: 'declined' }, { launchState: 'failed' },
    { launchState: 'unknown' }, { kind: 'session' }, { resultTarget: null },
    { resultTarget: { runtimeId: 'same-id' } }, { resultTarget: { contextNamespace: 'cluster:one', runtimeId: 'same-id' } },
    { resultTarget: { contextNamespace: 'local:one', runtimeId: 'not-reported' } },
    { workspaceId: 'cluster' }, { projectId: 'cluster-project' }]) {
    assert.equal(nativeLaunchResult(nativeSnapshot, { ...completeLaunch, ...changed }), null);
  }
  assert.equal(nativeLaunchResult({ ...nativeSnapshot, sessions: [{ ...nativeSnapshot.sessions[0], kind: 'launch' }] }, completeLaunch), null);
});

test('launch completion follows the visible launch console without taking over another terminal or editor', () => {
  assert.equal(presentLaunchResult(completeLaunch, launch), true);
  assert.equal(presentLaunchResult(completeLaunch, { contextNamespace: launch.contextNamespace, runtimeId: launch.runtimeId }), true);
  assert.equal(presentLaunchResult(completeLaunch, nativeSnapshot.sessions[0]), false);
  assert.equal(presentLaunchResult(completeLaunch, { ...launch, contextNamespace: 'launch:other-scope' }), false);
  assert.equal(presentLaunchResult(completeLaunch, launch, 'config'), false);
  assert.equal(presentLaunchResult(completeLaunch, null), false);
});

test('native New session leaves an unchanged initial config inspector so completion can open its session', () => {
  const expected = { revision: 3, workspaceId: 'local', projectId: launch.projectId, targetKey: null, inspector: 'config' };
  const initial = launchResponsePresentation(expected, { ...expected }, launch);
  assert.deepEqual(initial, { inspector: null });
  assert.equal(presentLaunchResult(completeLaunch, launch, initial.inspector), true);
  assert.equal(expected.inspector, 'config');
  // A user who navigated or edited while POST was pending keeps that view. A
  // later explicit return to the config also prevents automatic completion.
  assert.equal(launchResponsePresentation(expected, { ...expected, revision: 4 }, launch), null);
  assert.equal(launchResponsePresentation(expected, { ...expected, projectId: 'other-project' }, launch), null);
  assert.equal(presentLaunchResult(completeLaunch, launch, 'config'), false);
  assert.deepEqual(launchResponsePresentation(expected, { ...expected }, nativeSnapshot.sessions[0]), { inspector: 'config' });
});

test('launch guidance preserves native prompts and distinguishes completion, decline, failure and unknown outcome', () => {
  assert.equal(sessionDisplayLabel(launch), 'Launch · Botainer CLI');
  assert.equal(sessionDisplayLabel(completeLaunch), 'Launch log · completed');
  const live = sessionGuidance(launch, { viewState: 'connected' });
  assert.equal(live.title, 'Botainer CLI · launch in progress');
  assert.match(live.message, /Preflight details, warnings and any confirmation prompts/);
  assert.match(live.note, /type answers directly here/);
  const completed = sessionGuidance(completeLaunch, { viewState: 'disconnected' });
  assert.match(completed.title, /launch completed/);
  assert.match(completed.message, /preflight and launch output again/);
  assert.equal(completed.action, null);
  for (const [launchState, title] of [['declined', 'Launch declined'], ['failed', 'Botainer launch failed'], ['unknown', 'Launch outcome is unverified']]) {
    const current = { ...launch, launchState, state: launchState === 'unknown' ? 'unknown' : 'stopped' };
    const guidance = sessionGuidance(current, { viewState: 'disconnected', canStart: true, canAttach: true });
    assert.equal(guidance.title, title);
    assert.equal(guidance.action, 'log');
    assert.match(guidance.note, /retry|retried/);
    assert.deepEqual(sessionControls(nativeSnapshot, current), { attach: true, stop: false });
  }
  // A generic workspace stop capability must never turn a launch console into
  // a container stop target, even if the launch omits its own capability map.
  assert.equal(sessionControls({ capabilities: { stopSession: true, attachTerminal: true } }, { ...launch, capabilities: undefined }).stop, false);
});

test('finished launch transcript controls read only the exact advertised log and cannot stop or create work', () => {
  for (const launchState of ['completed', 'declined', 'failed', 'unknown']) {
    const log = { ...launch, launchState, state: launchState === 'unknown' ? 'unknown' : 'stopped' };
    assert.equal(isLaunchLog(log), true);
    assert.deepEqual(sessionControls(nativeSnapshot, log), { attach: true, stop: false });
    assert.deepEqual(sessionControls(nativeSnapshot, { ...log, capabilities: { attachTerminal: false } }), { attach: false, stop: false });
    const guidance = sessionGuidance(log, { viewState: 'detached', canAttach: true, canStart: true });
    assert.equal(guidance.action, 'log');
    assert.notEqual(guidance.action, 'start');
  }
  for (const session of [launch, { ...launch, launchState: 'waiting' }, nativeSnapshot.sessions[0], null]) {
    assert.equal(isLaunchLog(session), false);
  }
});

test('explicit CLI timeout shows a read-only log and preserves the reported dispatch outcome', () => {
  for (const operation of ['init', 'start']) {
    const timedOut = { ...launch, operation, consoleEnded: true, consoleEndReason: 'timeout',
      launchState: 'unknown', state: 'unknown', launchReason: 'Dispatch outcome is not verified.' };
    assert.equal(isLaunchLog(timedOut), true);
    assert.match(sessionDisplayLabel(timedOut), /log · timed out/);
    const guidance = sessionGuidance(timedOut, { viewState: 'disconnected', reason: 'session_eof', canAttach: true, canStart: true });
    assert.match(guidance.title, /console timed out/);
    assert.match(guidance.message, /pending prompt cannot be resumed/);
    assert.match(guidance.message, /Dispatch outcome is not verified/);
    assert.doesNotMatch(guidance.message, /No session was started|did not start/);
    assert.equal(guidance.action, 'log');
    assert.match(guidance.note, /No automatic retry or relaunch/);
    assert.deepEqual(sessionControls(nativeSnapshot, timedOut), { attach: true, stop: false });
    assert.equal(terminalRecoveryIntent(timedOut), null);
  }
});

test('pending CLI EOF waits for recorded outcome and never offers another prompt or launch', () => {
  for (const operation of ['init', 'start']) {
    const pending = { ...launch, operation, launchState: 'waiting' };
    const ended = sessionGuidance(pending, { viewState: 'disconnected', reason: 'session_eof', canAttach: true, canStart: true });
    assert.equal(ended.title, 'Botainer CLI console ended');
    assert.equal(ended.action, null);
    assert.match(ended.message, /Refresh to check its recorded outcome/);
    assert.doesNotMatch(ended.message, /Reconnect to the same launch console/);
    const failed = { ...pending, state: 'stopped', launchState: 'failed',
      launchReason: 'Botainer preflight ended before scheduler dispatch; no job was submitted.' };
    assert.match(sessionGuidance(failed, { viewState: 'disconnected', canAttach: true }).message, /no job was submitted/);
    assert.equal(projectActivity([{ ...pending, consoleEnded: true }]).launches, 0);
  }
});

test('project activity separates pending CLI launches and historical logs from running sessions', () => {
  const values = [launch, completeLaunch, { ...completeLaunch, launchState: 'unknown', state: 'unknown' },
    { ...launch, launchState: 'declined', state: 'stopped' }, nativeSnapshot.sessions[0],
    { ...nativeSnapshot.sessions[0], state: 'queued' }, { ...nativeSnapshot.sessions[0], state: 'stopped' }];
  assert.deepEqual(projectActivity(values), { sessions: 2, launches: 1 });
});

test('project navigation bounds ended history while unknown work and selected older history remain reachable', () => {
  const ended = Array.from({ length: 8 }, (_, i) => ({ ...nativeSnapshot.sessions[0], runtimeId: `ended-${i}`,
    state: 'stopped', createdAt: `2026-09-${String(i + 1).padStart(2, '0')}T12:00:00Z` }));
  const unknown = { ...launch, launchState: 'unknown', state: 'unknown' };
  const current = [launch, unknown, nativeSnapshot.sessions[0], { ...nativeSnapshot.sessions[0], runtimeId: 'queue', state: 'queued' }];
  const sessions = [...ended, ...current];
  const before = JSON.stringify(sessions);
  const closed = projectSessionGroups(sessions);
  assert.equal(closed.historyCount, 8);
  assert.deepEqual(closed.history, []);
  assert.equal(closed.current.length, 4);
  assert.ok(closed.current.includes(unknown));
  assert.equal(isHistoricalSession(unknown), false);
  const recent = projectSessionGroups(sessions, { showHistory: true });
  assert.deepEqual(recent.history.map(session => session.runtimeId), ['ended-7', 'ended-6', 'ended-5', 'ended-4', 'ended-3']);
  assert.equal(recent.hiddenHistoryCount, 3);
  const selected = projectSessionGroups(sessions, { showHistory: true, selectedTarget: ended[0] });
  assert.equal(selected.history.length, 6);
  assert.equal(selected.history.at(-1), ended[0]);
  assert.deepEqual(projectSessionGroups(sessions, { selectedTarget: ended[0] }).history, [ended[0]]);
  assert.equal(projectSessionGroups(sessions, { showHistory: true, showAllHistory: true }).history.length, 8);
  assert.equal(JSON.stringify(sessions), before);
});

test('searching a project or historical session never implicitly expands its ended history', () => {
  const matches = sortProjects(snapshot, { query: 'parser' });
  assert.deepEqual(matches.map(project => project.id), ['new']);
  const sessions = snapshot.sessions.filter(session => session.projectId === matches[0].id);
  assert.deepEqual(projectSessionGroups(sessions).history, []);
  assert.equal(projectSessionGroups(sessions, { showHistory: true }).history[0].label, 'review parser');
});

test('native launch search matches displayed history outcomes and exact session IDs without expanding history', () => {
  const declined = { ...launch, label: 'Botainer launch', agent: 'Native CLI',
    runtimeId: '33333333-3333-4333-8333-333333333333', launchState: 'declined', state: 'stopped' };
  const inventory = { ...nativeSnapshot, sessions: [...nativeSnapshot.sessions, declined] };
  for (const query of ['declined', 'Launch log · declined', 'Botainer launch', declined.runtimeId, '333333333333']) {
    assert.deepEqual(sortProjects(inventory, { query }).map(project => project.id), [declined.projectId], query);
  }
  assert.deepEqual(sortProjects(inventory, { query: 'not-a-real-outcome' }), []);
  const scoped = inventory.sessions.filter(session => session.projectId === declined.projectId);
  assert.deepEqual(projectSessionGroups(scoped).history, []);
  assert.ok(projectSessionGroups(scoped, { showHistory: true }).history.includes(declined));
});

test('recent-launch order and displayed dates never confuse observations or terminal activity with launches', () => {
  const now = Date.parse('2026-09-21T12:00:00Z');
  const session = { ...nativeSnapshot.sessions[0], createdAt: '2026-09-21T10:00:00Z', lastActiveAt: '2026-09-21T11:59:59Z', lastObservedAt: '2026-09-21T12:00:00Z' };
  assert.equal(sessionTimestamp(session, now), 'Started 2h ago');
  assert.equal(sessionTimestamp({ ...session, schedulerState: 'RUNNING' }, now), 'Requested 2h ago');
  assert.equal(sessionTimestamp({ ...session, kind: 'launch' }, now), 'Requested 2h ago');
  assert.equal(sessionTimestamp({ ...session, createdAt: undefined }, now), '');
  assert.equal(projectLaunchTimestamp([session], now), 'Last launch 2h ago');
  assert.equal(projectLaunchTimestamp([{ ...session, createdAt: 'not-a-date' }], now), '');
  assert.equal(relativeTimestamp('2026-09-21T12:05:00Z', now), 'at 2026-09-21 12:05 UTC');
  const changedObservations = { ...snapshot,
    projects: snapshot.projects.map(project => ({ ...project, lastActiveAt: '2027-01-01' })),
    sessions: snapshot.sessions.map(session => ({ ...session, lastActiveAt: session.projectId === 'old' ? '2027-01-01' : '2020-01-01', lastObservedAt: '2028-01-01' })) };
  assert.deepEqual(sortProjects(changedObservations).map(project => project.id), ['new', 'active', 'old']);
  assert.deepEqual(sortProjects({ ...changedObservations, projects: changedObservations.projects.map(project =>
    project.id === 'old' ? { ...project, lastLaunchAt: '2026-09-20' } : project) }).map(project => project.id), ['old', 'new', 'active']);
});

test('real launch sorting excludes declined prompts and cluster requests without known start times', () => {
  const now = Date.parse('2026-09-21T12:00:00Z');
  const inventory = { ...snapshot, sessions: [...snapshot.sessions,
    { ...launch, projectId: 'old', state: 'stopped', launchState: 'declined', createdAt: '2026-09-21T11:00:00Z' },
    { ...snapshot.sessions[0], runtimeId: 'cluster-request', state: 'running', schedulerState: 'RUNNING', createdAt: '2026-09-21T11:30:00Z' }] };
  assert.deepEqual(sortProjects(inventory).map(project => project.id), ['new', 'active', 'old']);
  assert.equal(runtimeStartedAt(inventory.sessions.at(-1)), '');
  assert.equal(sessionTimestamp(inventory.sessions.at(-1), now), 'Requested 30m ago');
  assert.equal(projectLaunchTimestamp(inventory.sessions.slice(-2), now), '');
  assert.equal(projectLaunchTimestamp([], now, '2026-09-21T10:00:00Z'), 'Last launch 2h ago');
  const started = { ...inventory.sessions.at(-1), startedAt: '2026-09-21T11:45:00Z' };
  assert.equal(sessionTimestamp(started, now), 'Started 15m ago');
  assert.equal(projectLaunchTimestamp([started], now), 'Last launch 15m ago');
});

test('end metadata is labeled as a record rather than proof of exact process exit or success', () => {
  const now = Date.parse('2026-09-21T12:00:00Z');
  assert.equal(sessionRecordedEnd({ recordedEndedAt: '2026-09-21T11:00:00Z' }, now), 'End recorded 1h ago');
  for (const value of [undefined, null, '', 'not-a-date', []]) assert.equal(sessionRecordedEnd({ recordedEndedAt: value }, now), '');
  assert.equal(sessionRecordedEnd({ state: 'stopped', lastObservedAt: '2026-09-21T11:00:00Z' }, now), '');
});

test('project agent summaries use only reported labels from current sessions and never suggest selectable agents', () => {
  const values = [{ ...nativeSnapshot.sessions[0], agent: 'Codex' }, { ...nativeSnapshot.sessions[0], agent: 'Codex', state: 'unknown' },
    { ...nativeSnapshot.sessions[0], agent: 'Claude', state: 'queued' }, { ...nativeSnapshot.sessions[0], agent: 'Retired agent', state: 'stopped' },
    { ...launch, agent: 'Native CLI' }, { ...nativeSnapshot.sessions[0], agent: { invalid: true } }];
  assert.deepEqual(observedAgents(values), ['Claude', 'Codex']);
});

test('advisory bells bind to a live connected exact target and never arise from finished launch replay', () => {
  const session = nativeSnapshot.sessions[0];
  const target = { contextNamespace: session.contextNamespace, runtimeId: session.runtimeId };
  const view = { target, state: 'connected', visible: false };
  const event = { kind: 'bell' };
  assert.equal(observedBellKey(session, view, target, event), JSON.stringify([target.contextNamespace, target.runtimeId]));
  for (const state of ['detached', 'connecting', 'disconnected', 'disposed']) {
    assert.equal(observedBellKey(session, { ...view, state }, target, event), null);
  }
  for (const state of ['unknown', 'stopped', 'failed', 'queued']) {
    assert.equal(observedBellKey({ ...session, state }, view, target, event), null);
  }
  assert.equal(observedBellKey(null, view, target, event), null);
  assert.equal(observedBellKey(session, null, target, event), null);
  assert.equal(observedBellKey(session, view, target, { kind: 'needs_input' }), null);
  assert.equal(observedBellKey({ ...session, contextNamespace: 'other-machine' }, view, target, event), null);
  assert.equal(observedBellKey(session, { ...view, target: { ...target, runtimeId: 'other-run' } }, target, event), null);
  const launchView = { target: launch, state: 'connected' };
  assert.ok(observedBellKey(launch, launchView, launch, event));
  assert.equal(observedBellKey({ ...launch, launchState: 'completed' }, launchView, launch, event), null);
  assert.match(BELL_NOTICE, /not a confirmed request for input/);
  assert.match(BELL_NOTICE, /Unconnected sessions are not monitored/);
});

function navigationFixture() {
  const focused = [], document = { activeElement: null };
  const row = (key, text) => ({
    dataset: { navigationKey: key }, text,
    isEqualNode(other) { return other?.dataset.navigationKey === key && other.text === text; },
    focus(options) { focused.push({ key, options }); document.activeElement = this; },
  });
  const old = row('session:["local:one","same-id"]', 'Shell · running');
  const container = {
    childNodes: [old], scrollTop: 240, scrollLeft: 7, replacements: 0,
    contains(element) { return this.childNodes.includes(element); },
    replaceChildren(content) {
      if (this.contains(document.activeElement)) document.activeElement = null;
      this.childNodes = [...content.childNodes]; this.replacements++;
      this.scrollTop = 0; this.scrollLeft = 0;
    },
  };
  document.activeElement = old;
  return { container, old, row, document, focused };
}

test('unchanged navigation content retains the exact button, focus and scroll across inventory polls', () => {
  const { container, old, row, document, focused } = navigationFixture();
  const replacement = row(old.dataset.navigationKey, old.text);
  const changed = updateNavigationList(container, { childNodes: [replacement] }, document.activeElement,
    new Map([[old.dataset.navigationKey, replacement]]));
  assert.equal(changed, false);
  assert.equal(container.replacements, 0);
  assert.equal(container.childNodes[0], old);
  assert.equal(container.contains(old), true); // A pending pointer click still targets an attached button.
  assert.equal(document.activeElement, old);
  assert.deepEqual([container.scrollTop, container.scrollLeft], [240, 7]);
  assert.deepEqual(focused, []);
});

test('changed navigation content restores the same qualified keyboard target without scrolling', () => {
  const { container, old, row, document, focused } = navigationFixture();
  const replacement = row(old.dataset.navigationKey, 'Shell · unverified');
  assert.equal(updateNavigationList(container, { childNodes: [replacement] }, document.activeElement,
    new Map([[old.dataset.navigationKey, replacement]])), true);
  assert.equal(container.replacements, 1);
  assert.equal(container.childNodes[0].text, 'Shell · unverified');
  assert.equal(document.activeElement, replacement);
  assert.deepEqual([container.scrollTop, container.scrollLeft], [240, 7]);
  assert.deepEqual(focused, [{ key: old.dataset.navigationKey, options: { preventScroll: true } }]);
});

test('navigation changes never redirect missing-target focus or steal an editor focus and draft', () => {
  const { container, row, document, focused } = navigationFixture();
  const other = row('session:["cluster:one","same-id"]', 'Shell · running');
  updateNavigationList(container, { childNodes: [other] }, document.activeElement,
    new Map([[other.dataset.navigationKey, other]]));
  assert.equal(document.activeElement, null); // No fallback to a coinciding ID on another machine.
  assert.deepEqual(focused, []);
  const editor = { value: 'unsaved project draft', selectionStart: 5, selectionEnd: 12 };
  document.activeElement = editor;
  const changed = row(other.dataset.navigationKey, 'Shell · stopped');
  updateNavigationList(container, { childNodes: [changed] }, document.activeElement,
    new Map([[changed.dataset.navigationKey, changed]]));
  assert.equal(document.activeElement, editor);
  assert.deepEqual(editor, { value: 'unsaved project draft', selectionStart: 5, selectionEnd: 12 });
  assert.deepEqual(focused, []);
  assert.deepEqual([container.scrollTop, container.scrollLeft], [240, 7]);
});

test('connection recovery can restore focus to its stable navigation control without stealing editor focus', () => {
  const { container, row, document, focused } = navigationFixture();
  const check = row('connection-check:remote', 'Check again');
  container.childNodes = [check]; document.activeElement = check;
  const selector = row('workspace-select', 'Connection selector');
  const connected = row('status', 'Connected');
  updateNavigationList(container, { childNodes: [connected] }, document.activeElement, new Map(), selector);
  assert.equal(document.activeElement, selector);
  assert.deepEqual(focused, [{ key: 'workspace-select', options: { preventScroll: true } }]);
  const editor = { value: 'unfinished input' }; document.activeElement = editor;
  updateNavigationList(container, { childNodes: [row('status', 'Checking')] }, document.activeElement, new Map(), selector);
  assert.equal(document.activeElement, editor);
  assert.equal(focused.length, 1);
});

test('project instructions distinguish starting, preflight, connecting, uncertainty and the limited local trial', () => {
  const empty = { ...nativeSnapshot, sessions: [] }, project = nativeSnapshot.projects[0];
  const welcome = projectWorkflow(empty, project, { canStart: true });
  assert.match(welcome, /New session to open Botainer’s actual CLI/);
  assert.match(welcome, /preflight details and warnings/);
  assert.match(welcome, /answer any prompts in that terminal/);
  assert.match(welcome, /opens the session terminal/);
  assert.match(welcome, /Launch log tab/);
  assert.match(welcome, /cached Alpine shell with networking off and no AI-agent credentials/);
  const waiting = projectWorkflow({ ...empty, sessions: [launch] }, project);
  assert.match(waiting, /launch is already in progress/);
  assert.match(waiting, /Connect resumes that console/);
  const active = projectWorkflow(nativeSnapshot, project, { canStart: true });
  assert.match(active, /Connect session opens the running session’s terminal/);
  assert.doesNotMatch(active, /Select a running session.*then Connect/);
  assert.match(active, /one active session per project/);
  assert.match(active, /Connect to the current session, or Stop it before starting another/);
  assert.match(projectWorkflow(combined, combined.projects[0], { canStart: true }), /New session creates separate work/);
  const uncertain = projectWorkflow({ ...nativeSnapshot, sessions: [...nativeSnapshot.sessions, { ...launch, state: 'unknown', launchState: 'unknown' }] }, project, { canStart: true });
  assert.match(uncertain, /outcomes are unverified/);
  assert.doesNotMatch(uncertain, /Choose New session/);
  assert.doesNotMatch(projectWorkflow({ ...combined, sessions: [] }, combined.projects[0], { canStart: true }), /Botainer’s actual CLI|Alpine/);
  assert.match(projectWorkflow(empty, null), /New session starts work; Connect opens a session that is already running/);
});

test('project connect instructions require an explicit session choice when multiple current sessions exist', () => {
  const project = nativeSnapshot.projects[0];
  const session = nativeSnapshot.sessions.find(item => item.projectId === project.id);
  const multiple = { ...nativeSnapshot, sessions: [...nativeSnapshot.sessions, { ...session, runtimeId: 'another-current-session' }] };
  const instructions = projectWorkflow(multiple, project);
  assert.equal(projectPrimaryAction(multiple, project), null);
  assert.match(instructions, /Choose a session below or in the sidebar/);
  assert.doesNotMatch(instructions, /Connect session opens the running session’s terminal/);
});

test('an active-session launch restriction is separate from project access and never suggests starting while blocked', () => {
  const reason = 'A session is already running for this project outside the dashboard. Continue in its original terminal.';
  const project = { ...nativeSnapshot.projects[0], startUnavailableReason: reason,
    capabilities: { nativeCliLaunch: true, startSession: false, filesRead: true, configRead: true } };
  const stopped = { ...nativeSnapshot.sessions[0], state: 'stopped', controlRestriction: 'external-terminal-owner-unverified',
    unavailableReason: 'This session was started elsewhere. Its original terminal owner cannot be verified.' };
  const inventory = { ...nativeSnapshot, projects: [project], sessions: [stopped] };
  assert.equal(projectUnavailableMessage(project), '');
  assert.equal(projectStartUnavailableMessage(project), reason);
  assert.equal(capability(inventory, 'filesRead', project), true);
  assert.ok(projectWorkflow(inventory, project, { canStart: false }).startsWith(reason));
  const guidance = sessionGuidance(stopped, { viewState: 'detached', canStart: false, startUnavailableReason: reason });
  assert.equal(guidance.title, 'This session has stopped'); assert.equal(guidance.action, null);
  assert.match(guidance.message, /There is no running terminal here/);
  assert.match(guidance.message, /Continue in its original terminal/);
  assert.doesNotMatch(guidance.message, /Its original terminal owner cannot be verified/);
  assert.equal(guidance.note, reason);
  assert.doesNotMatch(guidance.note, /start a new one/);
});

test('specific project failures and pending CLI prompts precede a general active-session launch reason', () => {
  const project = { ...nativeSnapshot.projects[0], startUnavailableReason: 'A session is already running.',
    unavailableReason: 'The project identity changed.' };
  assert.equal(projectStartUnavailableMessage(project), 'The project identity changed.');
  assert.equal(projectUnavailableMessage(project), 'The project identity changed.');
  const pending = { ...nativeSnapshot, projects: [project], sessions: [launch] };
  assert.match(projectWorkflow(pending, project, { canStart: false }), /launch is already in progress/);
  assert.doesNotMatch(projectWorkflow(pending, project, { canStart: false }), /A session is already running/);
  const ended = sessionGuidance({ state: 'stopped' }, { viewState: 'detached', canStart: false });
  assert.equal(ended.action, null); assert.doesNotMatch(ended.note, /start a new one/);
});

test('missing scheduler handle has precise presentation without inferring ended state or enabling controls', () => {
  const record = { ...combined.sessions[1], runtime: 'apptainer', state: 'unknown', jobId: null,
    unavailableReason: 'No recorded scheduler job', capabilities: { attachTerminal: false, stopSession: false } };
  const before = JSON.stringify(record);
  assert.equal(hasNoRecordedJob(record), true);
  assert.equal(sessionStateLabel(record), 'No job recorded');
  assert.deepEqual(sessionControls(combined, record), { attach: false, stop: false });
  assert.deepEqual(projectActivity([record]), { sessions: 0, launches: 0 });
  assert.deepEqual(projectSessionGroups([record]).current, [record]);
  assert.equal(isHistoricalSession(record), false);
  const guidance = sessionGuidance(record, { viewState: 'detached', canStart: true });
  assert.equal(guidance.title, 'No scheduler job recorded');
  assert.match(guidance.message, /does not establish whether a job ever ran or has stopped/);
  assert.equal(guidance.action, null);
  assert.equal(JSON.stringify(record), before);
  for (const change of [{ unavailableReason: null }, { unavailableReason: 'remote-observation-stale' },
    { unavailableReason: 'Previous attachment cleanup is unconfirmed.' }, { jobId: '12345' },
    { runtime: 'docker' }, { kind: 'launch' }, { stale: true }]) {
    const other = { ...record, ...change };
    assert.equal(hasNoRecordedJob(other), false);
    assert.equal(sessionStateLabel(other), 'unverified');
  }
  assert.match(sessionGuidance(record, { viewState: 'detached', serviceConnected: false }).title, /may be stale/);
});

test('resolved native launch plus no-job record does not present an unresolved launch blocker', () => {
  const project = { ...combined.projects[1], capabilities: { nativeCliLaunch: true, startSession: true } };
  const record = { ...combined.sessions[1], runtime: 'apptainer', state: 'unknown', jobId: null,
    unavailableReason: 'No recorded scheduler job', capabilities: { attachTerminal: false, stopSession: false } };
  for (const launchState of ['declined', 'failed']) {
    const log = { ...completeLaunch, projectId: project.id, workspaceId: project.workspaceId, launchState,
      resultTarget: null, ...(launchState === 'failed' ? { consoleEnded: true, consoleEndReason: 'timeout' } : {}) };
    const observed = { ...combined, projects: [project], sessions: [record, log] };
    const instructions = projectWorkflow(observed, project, { canStart: true });
    assert.match(instructions, /Choose New session to open Botainer’s actual CLI/);
    assert.match(instructions, /retains 1 session record with no scheduler job ID/);
    assert.match(instructions, /does not establish whether a job ever ran/);
    assert.doesNotMatch(instructions, /outcomes are unverified|before starting more work/);
    assert.equal(projectPrimaryAction(observed, project), null);
    for (const unresolved of [{ ...log, state: 'unknown', launchState: 'unknown' },
      { ...record, runtimeId: 'unverified-job', jobId: '12345', unavailableReason: 'scheduler-unavailable' }]) {
      const uncertain = projectWorkflow({ ...observed, sessions: [...observed.sessions, unresolved] }, project, { canStart: true });
      assert.match(uncertain, /outcomes are unverified/);
      assert.doesNotMatch(uncertain, /Choose New session/);
    }
  }
});

test('switching from launch to created session retains CLI transcript without replaying typed consent', () => {
  const terminals = new Map(), transports = new Map();
  const registry = new TerminalViewRegistry({
    createTerminal(target, { onInput }) {
      const terminal = { output: [], write(data) { this.output.push(data); }, setVisible() {}, setInputEnabled() {},
        resize() {}, focus() {}, dispose() {}, onInput };
      terminals.set(target.contextNamespace, terminal); return terminal;
    },
    createTransport(target, callbacks) {
      const transport = { input: [], closes: 0, callbacks, open() { callbacks.onOpen(); },
        sendInput(data) { this.input.push(data); }, resize() {}, close() { this.closes++; } };
      transports.set(target.contextNamespace, transport); return transport;
    },
  });
  registry.show(launch); registry.attach(launch);
  const consoleTransport = transports.get(launch.contextNamespace);
  consoleTransport.callbacks.onData('Actual CLI warning. Continue? [y/N] ');
  terminals.get(launch.contextNamespace).onInput('y\r');
  consoleTransport.callbacks.onData('Session started.\r\n');
  consoleTransport.callbacks.onClose();
  const session = nativeLaunchResult(nativeSnapshot, completeLaunch);
  registry.show(session); registry.attach(session);
  assert.deepEqual(terminals.get(launch.contextNamespace).output, ['Actual CLI warning. Continue? [y/N] ', 'Session started.\r\n']);
  assert.deepEqual(consoleTransport.input, ['y\r']);
  assert.deepEqual(transports.get(session.contextNamespace).input, []);
  assert.equal(registry.size, 2);
  assert.equal(registry.get(launch).visible, false);
  assert.equal(registry.get(session).visible, true);
  registry.disposeAll();
});
test('read-only cluster settings retain explicit units and scope without copying unrelated properties', () => {
  const details = clusterSettingsDetails({ site: 'Example cluster', preset: 'terminal-trial', sshAlias: 'cluster-test',
    partition: 'short', timeMinutes: 5, cpus: 1, memoryMiB: 1024, profilePath: '/private-example/cluster.json',
    qualification: 'Fixture only; agent launch not qualified.', token: 'do-not-display', setupSteps: ['Use existing SSH.'] });
  assert.deepEqual(Object.fromEntries(details), { Site: 'Example cluster', Preset: 'terminal-trial', 'SSH alias': 'cluster-test',
    Partition: 'short', 'Time limit': '5 minutes', CPUs: '1', Memory: '1024 MiB', 'Private profile file': '/private-example/cluster.json',
    Qualification: 'Fixture only; agent launch not qualified.' });
  for (const value of [null, [], 'incorrect', {}, { site: {}, timeMinutes: '', cpus: Infinity }]) {
    assert.deepEqual(clusterSettingsDetails(value), []);
  }
});
test('single-flight refresh coalesces slow polls and allows another observation after completion', async () => {
  let resolve, calls = 0;
  const refresh = singleFlight(() => { calls++; return new Promise(done => { resolve = done; }); });
  const first = refresh(), second = refresh();
  assert.equal(first, second);
  await Promise.resolve();
  assert.equal(calls, 1);
  const third = refresh();
  assert.equal(first, third);
  resolve('observed');
  assert.equal(await third, 'observed');
  const next = refresh();
  assert.notEqual(next, first);
  await Promise.resolve();
  assert.equal(calls, 2);
  resolve('new observation');
  assert.equal(await next, 'new observation');
});
test('single-flight refresh clears a failed attempt without automatically retrying', async () => {
  let calls = 0;
  const refresh = singleFlight(() => { calls++; throw new Error('offline'); });
  const first = refresh(), second = refresh();
  assert.equal(first, second);
  await assert.rejects(first, /offline/);
  assert.equal(calls, 1);
  await assert.rejects(refresh(), /offline/);
  assert.equal(calls, 2);
});
test('configuration saves require a changed draft validated against the exact text and revision', () => {
  const draft = configDocument({ text: 'image: old\n', revision: 'abc123', writable: true });
  assert.equal(configCanSave(draft), false);
  draft.text = 'image: new\n';
  draft.validation = { text: draft.text, revision: draft.revision, valid: true };
  assert.equal(configCanSave(draft), true);
  draft.text += '# another edit\n';
  assert.equal(configCanSave(draft), false);
  draft.validation.text = draft.text;
  draft.revision = 'updated-on-disk';
  assert.equal(configCanSave(draft), false);
  draft.validation.revision = draft.revision;
  draft.validation.valid = 'true';
  assert.equal(configCanSave(draft), false);
  assert.throws(() => configDocument({ text: 'valid text', revision: '' }), /unsupported configuration/);
  assert.throws(() => configDocument({ text: 'x'.repeat(262145), revision: 'revision' }), /unsupported configuration/);
  assert.equal(configDocument({ text: '<script>untrusted text</script>', revision: 'one', writable: false }).writable, false);
});

test('open configuration permissions follow the current exact project instead of a stale captured capability', () => {
  const project = combined.projects[0];
  const target = configurationTarget('project', project);
  const draft = configDocument({ text: 'retained draft', revision: 'v1', writable: true });
  assert.equal(configurationWritable(combined, target, draft), true);
  const locked = { ...combined, projects: combined.projects.map(item => item.id === project.id
    ? { ...item, capabilities: { configWrite: false } } : item) };
  assert.equal(configurationWritable(locked, target, draft), false);
  assert.equal(configurationWritable({ ...combined, projects: [] }, target, draft), false);
  assert.equal(configurationWritable({ ...combined, workspaces: combined.workspaces.map(workspace => ({ ...workspace, status: 'unavailable' })) }, target, draft), false);
  assert.equal(configurationWritable(combined, target, draft, { connected: false }), false);
  assert.equal(configurationWritable(combined, target, { ...draft, writable: false }), false);
  assert.equal(configurationWritable(combined, { scope: 'unknown', projectId: project.id }, draft), false);
  assert.equal(draft.text, 'retained draft');
  assert.equal(draft.revision, 'v1');
  assert.equal(configurationWritable({ ...locked, capabilities: { dashboardConfigWrite: true } }, configurationTarget('dashboard'), draft), true);
});

test('configuration status honors project write permission when workspace permission is absent or false', () => {
  const project = { ...combined.projects[1], capabilities: { configWrite: true } };
  const target = configurationTarget('project', project);
  const draft = configDocument({ text: 'agent: codex\n', revision: 'v1', writable: true });
  for (const capabilities of [{}, { configWrite: false }]) {
    const snapshot = { ...combined, projects: [project], workspaces: combined.workspaces.map(workspace =>
      workspace.id === project.workspaceId ? { ...workspace, capabilities } : workspace) };
    assert.equal(configurationWritable(snapshot, target, draft), true);
    assert.equal(configurationStatus(snapshot, target, draft), 'Saved version loaded · no unsaved changes.');
    assert.match(configurationStatus(snapshot, target, { ...draft, text: 'agent: claude\n' }), /Unsaved draft/);
    const locked = { ...snapshot, projects: [{ ...project, capabilities: { configWrite: false } }] };
    assert.match(configurationStatus(locked, target, draft), /Read-only for this project/);
    assert.doesNotMatch(configurationStatus(locked, target, draft), /not implemented|does not support/);
    assert.match(configurationStatus(snapshot, target, draft, { connected: false }), /Read-only/);
    assert.equal(draft.text, 'agent: codex\n');
  }
});
test('read-only POST failures do not claim a potentially launched mutation', async () => {
  const failure = new TypeError('Network unavailable');
  await assert.rejects(api('/api/projects/project/files', { method: 'POST', mutation: false, body: { path: '' }, fetch: async () => { throw failure; } }), error => error === failure);
});
test('ambiguous and orphaned inventory is refused, same runtime ID across namespaces is distinct', () => {
  assert.equal(validateSnapshot(snapshot), snapshot);
  assert.throws(() => validateSnapshot({ ...snapshot, projects: [...snapshot.projects, snapshot.projects[0]] }), /ambiguous project/);
  assert.throws(() => validateSnapshot({ ...snapshot, sessions: [...snapshot.sessions, snapshot.sessions[0]] }), /ambiguous session/);
  assert.throws(() => validateSnapshot({ ...snapshot, sessions: [{ ...snapshot.sessions[0], projectId: 'missing' }] }), /ambiguous session/);
  const value = { ...snapshot, sessions: [...snapshot.sessions, { ...snapshot.sessions[0], contextNamespace: 'remote:b' }] };
  assert.equal(validateSnapshot(value), value);
});
test('API uses same-origin cookie and JSON, never URL credentials or action retries', async () => {
  let calls = 0;
  const result = await api('/api/projects/p/sessions', { method: 'POST', body: { requestId: 'once' }, fetch: async (path, options) => {
    calls++; assert.equal(path.includes('?'), false); assert.equal(options.credentials, 'same-origin'); assert.equal(options.cache, 'no-store'); assert.equal(options.redirect, 'error');
    assert.deepEqual(JSON.parse(options.body), { requestId: 'once' }); assert.equal(options.headers.Authorization, `Bearer ${bearer}`);
    return { ok: true, status: 200, json: async () => ({ accepted: true }) };
  } });
  assert.equal(result.accepted, true); assert.equal(calls, 1);
  await assert.rejects(api('/api/projects/p/sessions', { method: 'POST', body: {}, fetch: async () => { calls++; throw new TypeError('network failure'); } }), /outcome is unknown/);
  assert.equal(calls, 2);
  await assert.rejects(api('/api/state?token=bad'), /Invalid service endpoint/);
});
test('API refuses cookie-only access before making a request', async () => {
  let called = false;
  await assert.rejects(rawApi('/api/state', { bearer: null, fetch: async () => { called = true; } }), /Dashboard locked/);
  assert.equal(called, false);
});
test('authentication expiry requests a reload and preserves uncertainty on timed-out mutations', async () => {
  await assert.rejects(api('/api/state', { fetch: async () => ({ status: 401 }) }), /Browser access expired or was signed out/);
  await assert.rejects(api('/api/sessions/one/stop', { method: 'POST', fetch: async () => { const error = new Error('timeout'); error.name = 'AbortError'; throw error; } }), /will not be retried/);
});

test('dashboard maintenance commands follow the trusted installation kind', () => {
  assert.equal(dashboardCommand({ dashboardEntryPoint: 'installed' }), 'botainer-dashboard');
  for (const value of ['source', undefined, 'run arbitrary text']) {
    assert.equal(dashboardCommand({ dashboardEntryPoint: value }), 'python3 tools/dashboard.py');
  }
});
