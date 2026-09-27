import test from 'node:test';
import assert from 'node:assert/strict';
import {
  HOST_BADGE, HOST_ACCESS, isHostExecution, hostTerminalWarning, configuredHostAgents, sessionAgentOptions,
  sessionLaunchRequest, hostLaunchDescription, hostAgentRestrictions, projectSetupOptions, projectSetupDescription,
  filePreviewNote, sessionStopPresentation,
  retainedSessionObservation, projectForRetainedSession,
  hostOpeningOptions, hostProjectOpeningDescription, includeHostProjectResponse, projectListView, navigateWorkbench, hostOpeningErrorMessage,
  hostLaunchPresentation, projectUnavailableMessage, projectStartUnavailableMessage, projectExecutionLabel,
  projectWorkflow, workspaceAvailability, sortProjects, capability, launchResponsePresentation, projectPrimaryAction,
} from '../../frontend/app.js';

const agents = [{ id: 'codex', label: 'Codex', executable: '/opt/agents/codex' }];
const host = { id: 'host', kind: 'local', mode: 'host-agents', executionKind: 'host',
  label: 'This computer · Host agents', status: 'available', availableAgents: agents, defaultAgent: 'codex',
  capabilities: { projectOpen: true, projectCreate: true, projectRegister: false, startSession: true,
    agentOverride: true, configRead: false, configWrite: false, nativeCliLaunch: false, nativeCliProjectSetup: false } };
const container = { id: 'containers', kind: 'local', label: 'This computer · Botainer', status: 'available',
  capabilities: { startSession: true, agentOverride: true, configRead: true } };
const project = { id: 'host-project', workspaceId: host.id, name: 'Shared name', path: '/work/project',
  executionKind: 'host', availableAgents: agents, defaultAgent: 'codex' };
const containerProject = { id: 'container-project', workspaceId: container.id, name: 'Shared name', path: '/work/project' };
const snapshot = { capabilities: {}, workspaces: [host, container], projects: [project, containerProject], sessions: [] };

test('host execution identification survives stale or missing workspace inventory and does not use labels', () => {
  assert.equal(HOST_BADGE, '⚠ Host · no container');
  assert.equal(isHostExecution(snapshot, project), true);
  assert.equal(isHostExecution(snapshot, { workspaceId: host.id }), true);
  const retained = { executionKind: 'host', workspaceId: host.id, runtimeId: 'retained' };
  assert.equal(isHostExecution({ ...snapshot, workspaces: [] }, retained), true);
  assert.equal(isHostExecution(snapshot, { ...containerProject, name: HOST_BADGE }), false);
  assert.equal(isHostExecution(snapshot, { ...containerProject, agent: 'codex' }), false);
  assert.equal(isHostExecution({ ...snapshot, workspaces: [{ ...host, status: 'unavailable' }] }, project), true);
});

test('an open host terminal keeps its warning while inventory is missing, offline or inconsistent', () => {
  const retained = { executionKind: 'host', contextNamespace: 'host:profile-a', runtimeId: 'owned-terminal',
    workspaceId: host.id, projectId: project.id, state: 'running' };
  const empty = { ...snapshot, workspaces: [], projects: [], sessions: [] };
  assert.equal(hostTerminalWarning(empty, null, retained), true);
  assert.equal(hostTerminalWarning(empty, undefined, retained), true);
  assert.equal(hostTerminalWarning(empty, retained), true);
  const unavailable = { ...snapshot, workspaces: [{ ...host, status: 'unavailable' }, container] };
  assert.equal(hostTerminalWarning(unavailable, retained), true);
  const changedObservation = { ...retained, executionKind: 'container', state: 'unknown' };
  assert.equal(hostTerminalWarning(empty, changedObservation, retained), true);
  assert.equal(hostTerminalWarning(empty, { ...changedObservation, executionKind: undefined }, retained), true);
  assert.equal(hostTerminalWarning(empty, null, null), false);
});

test('retained host warnings cannot migrate to another terminal, workspace or project', () => {
  const retained = { executionKind: 'host', contextNamespace: 'host:profile-a', runtimeId: 'same-runtime',
    workspaceId: host.id, projectId: project.id };
  const empty = { ...snapshot, workspaces: [], projects: [], sessions: [] };
  const sameIdentity = { ...retained, executionKind: 'container' };
  for (const other of [
    { ...sameIdentity, contextNamespace: 'host:profile-b' },
    { ...sameIdentity, runtimeId: 'different-runtime' },
    { ...sameIdentity, workspaceId: container.id },
    { ...sameIdentity, projectId: containerProject.id },
  ]) assert.equal(hostTerminalWarning(empty, other, retained), false);
  assert.equal(hostTerminalWarning(snapshot, { ...containerProject, runtimeId: 'same-runtime',
    contextNamespace: 'container:profile', executionKind: 'container', label: HOST_BADGE, agent: 'claude' }, retained), false);
  assert.equal(hostTerminalWarning(empty, null, { ...retained, executionKind: 'container' }), false);
});

test('incomplete identities cannot transfer a retained warning to a current observation', () => {
  const retained = { executionKind: 'host', contextNamespace: 'host:profile-a', runtimeId: 'owned-terminal',
    workspaceId: host.id, projectId: project.id };
  const empty = { ...snapshot, workspaces: [], projects: [], sessions: [] };
  for (const field of ['contextNamespace', 'runtimeId', 'workspaceId', 'projectId']) {
    for (const value of [undefined, null, '', 1]) {
      const incomplete = { ...retained, [field]: value };
      assert.equal(hostTerminalWarning(empty, { ...incomplete, executionKind: 'container' }, incomplete), false);
    }
  }
  assert.equal(hostTerminalWarning(empty, {}, { executionKind: 'host' }), false);
  assert.equal(hostTerminalWarning(empty, { executionKind: 'host' }), true);
  assert.equal(hostTerminalWarning(empty, null, { executionKind: 'host' }), true);
});

test('host agent choices are limited to configured installed executables in the selected scope', () => {
  assert.deepEqual(sessionAgentOptions(snapshot, project), [{ value: 'codex', label: 'Codex' }]);
  assert.deepEqual(sessionAgentOptions(snapshot, containerProject).map(option => option.label), ['Project default', 'Claude', 'Codex']);
  assert.deepEqual(configuredHostAgents(snapshot, { workspaceId: host.id }), agents);
  assert.deepEqual(configuredHostAgents(snapshot, { ...project, availableAgents: [] }), []);
  const unsupported = { ...project, availableAgents: [null, { id: 'shell', executable: '/bin/sh' },
    { id: 'claude', executable: 'claude' }, ...agents, { id: 'codex', executable: '/other/codex' }] };
  assert.deepEqual(configuredHostAgents(snapshot, unsupported), agents);
  assert.deepEqual(sessionAgentOptions(snapshot, { ...project, defaultAgent: 'claude' }), [{ value: 'codex', label: 'Codex' }]);
});

test('host choices list each agent once with the profile default first, without changing source metadata', () => {
  const claude = { id: 'claude', executable: '/opt/agents/claude' };
  const configured = { ...project, availableAgents: [...agents, claude, ...agents], defaultAgent: 'claude' };
  const before = JSON.stringify(configured);
  assert.deepEqual(sessionAgentOptions(snapshot, configured), [
    { value: 'claude', label: 'Claude (default)' },
    { value: 'codex', label: 'Codex' },
  ]);
  assert.equal(JSON.stringify(configured), before);
  assert.deepEqual(sessionAgentOptions(snapshot, { ...configured, availableAgents: [] }), []);
  assert.deepEqual(sessionAgentOptions(snapshot, { ...configured, availableAgents: [claude] }), [
    { value: 'claude', label: 'Claude' },
  ]);
});

test('host launch refuses unconfigured, cross-project and unavailable agent choices before a request', () => {
  assert.deepEqual(sessionLaunchRequest(snapshot, project, 'codex', 'request'), { requestId: 'request', agent: 'codex' });
  assert.throws(() => sessionLaunchRequest(snapshot, project, 'claude', 'request'), /not configured/);
  assert.throws(() => sessionLaunchRequest(snapshot, { ...project, workspaceId: container.id }, 'codex', 'request'), /no longer available/);
  const unavailable = { ...snapshot, workspaces: [{ ...host, status: 'unavailable' }, container] };
  assert.throws(() => sessionLaunchRequest(unavailable, project, 'codex', 'request'), /no longer available/);
  const missing = { ...snapshot, projects: [{ ...project, availableAgents: [] }, containerProject] };
  assert.throws(() => sessionLaunchRequest(missing, project, 'default', 'request'), /not configured/);
  assert.equal(projectPrimaryAction(missing, project), null);
  assert.equal(capability(snapshot, 'configRead', project), false);
  assert.equal(capability(snapshot, 'nativeCliLaunch', project), false);
});

test('host default launch resolves a concrete agent while the container default remains native', () => {
  const before = JSON.stringify(snapshot);
  assert.deepEqual(sessionLaunchRequest(snapshot, project, 'default', 'host-default'),
    { requestId: 'host-default', agent: 'codex' });
  assert.deepEqual(sessionLaunchRequest(snapshot, containerProject, 'default', 'container-default'),
    { requestId: 'container-default' });
  assert.equal(JSON.stringify(snapshot), before);
});

test('a confirmed host choice is refused if that agent is removed before launch', () => {
  const confirmedChoice = sessionAgentOptions(snapshot, project)[0].value;
  assert.match(hostLaunchPresentation(snapshot, project, confirmedChoice).title, /Codex/);
  const claude = { id: 'claude', executable: '/opt/agents/claude' };
  const changed = { ...snapshot, projects: [
    { ...project, availableAgents: [claude], defaultAgent: 'claude' }, containerProject,
  ] };
  assert.throws(() => sessionLaunchRequest(changed, project, confirmedChoice, 'confirmed-request'), /not configured/);
});

test('host launch confirmation names the installed executable and describes the real access boundary', () => {
  for (const choice of ['default', 'codex']) {
    const description = hostLaunchDescription(snapshot, project, choice);
    assert.match(description, /Run Codex directly/);
    assert.ok(description.includes(project.path));
    assert.ok(description.includes(agents[0].executable));
    assert.ok(description.includes(HOST_ACCESS));
    assert.match(description, /not an OS access boundary/);
    assert.match(description, /dashboard does not answer/);
    assert.doesNotMatch(description, /Botainer|preflight|bypass|sandboxed/);
  }
  assert.throws(() => hostLaunchDescription(snapshot, project, 'claude'), /not configured/);
  assert.throws(() => hostLaunchDescription(snapshot, containerProject, 'codex'), /not configured/);
});

test('host launch confirmation names the resolved agent in its title, warning and final action', () => {
  const claude = { id: 'claude', label: 'Claude', executable: '/opt/agents/claude' };
  const configured = { ...project, availableAgents: [...agents, claude], defaultAgent: 'claude' };
  const defaultView = hostLaunchPresentation(snapshot, configured, 'default');
  assert.equal(defaultView.title, 'Start a new Claude session on host?');
  assert.equal(defaultView.confirm, '⚠ Start new Claude session on host');
  assert.match(defaultView.description, /same (?:project )?folder/);
  assert.match(defaultView.description, /separate|independent/);
  assert.match(defaultView.description, /does not reconnect/);
  assert.ok(defaultView.description.includes(configured.path));
  assert.match(defaultView.description, /same files|shared? files/);
  assert.match(defaultView.description, /edits can conflict/);
  assert.ok(defaultView.warning.includes(configured.path));
  assert.ok(defaultView.warning.includes(claude.executable));
  assert.ok(defaultView.warning.includes(HOST_ACCESS));
  assert.equal(hostLaunchPresentation(snapshot, configured, 'codex').confirm, '⚠ Start new Codex session on host');
  assert.equal(sessionAgentOptions(snapshot, configured)[0].label, 'Claude (default)');
});

test('disabled container controls explain the actual profile boundary without offering a nonexistent wizard', () => {
  const restricted = { ...containerProject, controlRestriction: 'outside-project-roots', unavailableReason: 'internal reason' };
  const message = projectUnavailableMessage(restricted);
  assert.match(message, /Container sessions are not enabled for this folder/);
  assert.match(message, /runtime profile.*project_roots/);
  assert.match(message, /Project details/);
  assert.doesNotMatch(message, /internal reason|wizard|combined-project-unavailable/);
  assert.equal(projectUnavailableMessage({ ...containerProject, unavailableReason: 'Existing runtime identity needs reconciliation' }), 'Existing runtime identity needs reconciliation');
  assert.doesNotMatch(projectUnavailableMessage({ unavailableReason: 'combined-project-unavailable' }), /combined-project-unavailable/);
  assert.equal(projectExecutionLabel(snapshot, project), HOST_BADGE);
  assert.equal(projectExecutionLabel(snapshot, containerProject), '');
  for (const mode of ['local-workspace', 'cluster-workspace', 'native-cli-workspace']) {
    const inventory = { ...snapshot, workspaces: [{ ...container, mode }] };
    assert.equal(projectExecutionLabel(inventory, containerProject), 'Shell trial');
  }
  for (const mode of ['ordinary-local', 'ordinary-remote', 'combined-workspace']) {
    assert.equal(projectExecutionLabel({ ...snapshot, workspaces: [{ ...container, mode }] }, containerProject), '');
  }
});

test('host profile startup restrictions are disclosed without implying changed sign-in or bypassed permissions', () => {
  const claude = { id: 'claude', label: 'Claude', executable: '/opt/agents/claude',
    externalTools: 'disabled', startupHooks: 'disabled', updates: 'disabled' };
  const restricted = { ...project, availableAgents: [claude], defaultAgent: 'claude' };
  assert.deepEqual(configuredHostAgents(snapshot, restricted), [claude]);
  const description = hostLaunchDescription(snapshot, restricted, 'default');
  const restrictions = hostAgentRestrictions(configuredHostAgents(snapshot, restricted)[0]);
  assert.match(restrictions, /disables external MCP tools, startup hooks and automatic updates/);
  assert.match(restrictions, /native permission prompts and existing sign-in remain in use/);
  assert.ok(description.includes(restrictions));
  assert.doesNotMatch(description, /usual global tools|bypassed permissions/);
  assert.equal(hostAgentRestrictions(agents[0]), '');
  assert.equal(hostAgentRestrictions({ externalTools: 'unknown', updates: 'enabled' }), '');
  assert.equal(hostAgentRestrictions({ updates: 'disabled' }), 'This profile disables automatic updates. The agent’s native permission prompts and existing sign-in remain in use.');
});

test('host project setup registers plain folders without offering Botainer initialization or config', () => {
  const metadata = { roots: [{ id: 'projects', workspaceId: host.id }, { id: 'projects', workspaceId: container.id }],
    installations: [{ id: 'host-profile', workspaceId: host.id }, { id: 'botainer', workspaceId: container.id }] };
  const options = projectSetupOptions(snapshot, metadata, host.id);
  assert.deepEqual(options.roots, [metadata.roots[0]]);
  assert.deepEqual(options.installations, [metadata.installations[0]]);
  assert.deepEqual(options.modes.map(mode => mode.value), ['open', 'create']);
  assert.match(projectSetupDescription(snapshot, host.id), /does not initialize Botainer, create a container, or start an agent/);
  const accidentalCapability = { ...snapshot, workspaces: [{ ...host, capabilities: { ...host.capabilities, projectRegister: true } }] };
  assert.deepEqual(projectSetupOptions(accidentalCapability, metadata, host.id).modes.map(mode => mode.value), ['open', 'create']);
});

test('host workflow and unavailable status do not inherit Docker, scheduler or Botainer instructions', () => {
  const workflow = projectWorkflow(snapshot, project, { canStart: true });
  assert.ok(workflow.includes(HOST_BADGE));
  assert.ok(workflow.includes(HOST_ACCESS));
  assert.match(workflow, /Choose New session/);
  assert.doesNotMatch(workflow, /Botainer|allocation|saved defaults/);
  const running = { ...snapshot, sessions: [{ projectId: project.id, state: 'running' }] };
  assert.match(projectWorkflow(running, project), /Connect opens an existing agent terminal/);
  const unknown = { ...snapshot, sessions: [{ projectId: project.id, state: 'unknown' }] };
  assert.match(projectWorkflow(unknown, project), /outcome is unverified/);
  const failure = workspaceAvailability({ ...host, status: 'unavailable' });
  assert.match(failure, /configured agent executables/);
  assert.doesNotMatch(failure, /Docker|SSH|cluster/);
});

test('host and container projects with the same path keep independent pins and can be searched by execution mode', () => {
  assert.deepEqual(sortProjects(snapshot, { query: 'host' }).map(item => item.id), [project.id]);
  assert.deepEqual(sortProjects(snapshot, { query: 'no container' }).map(item => item.id), [project.id]);
  assert.equal(sortProjects(snapshot, { pins: [containerProject.id] })[0].id, containerProject.id);
  assert.equal(sortProjects(snapshot, { pins: [project.id] })[0].id, project.id);
});

test('a requested host terminal opens over files only if the user has not navigated elsewhere', () => {
  const view = { revision: 3, workspaceId: host.id, projectId: project.id, targetKey: null, inspector: 'files' };
  const session = { executionKind: 'host', projectId: project.id, workspaceId: host.id };
  assert.deepEqual(launchResponsePresentation(view, { ...view }, session), { inspector: null });
  assert.equal(launchResponsePresentation(view, { ...view, revision: 4, inspector: 'settings' }, session), null);
  assert.equal(launchResponsePresentation(view, { ...view, workspaceId: container.id }, session), null);
});

test('host text preview does not send users to a nonexistent Botainer configuration panel', () => {
  const note = filePreviewNote(snapshot, project);
  assert.match(note, /Read-only text preview/);
  assert.match(note, /plain folder/);
  assert.doesNotMatch(note, /Project config|Edit project defaults|Botainer/);
  assert.match(filePreviewNote(snapshot, containerProject), /Edit project defaults in Project config/);
});

test('host stop controls name the terminal and disclose detached or shared-daemon task limits', () => {
  const session = { executionKind: 'host', workspaceId: host.id, runtimeId: 'owned-terminal', state: 'running', label: 'Claude' };
  const stop = sessionStopPresentation(snapshot, session, project);
  assert.equal(stop.label, 'Close host terminal');
  assert.equal(stop.title, 'Close this host terminal?');
  assert.match(stop.description, /owned-terminal/);
  assert.match(stop.description, /stops its owned process/);
  assert.match(stop.description, /Detached background work and tasks in a shared agent daemon may continue/);
  assert.match(stop.description, /Closing only the terminal view leaves this process running/);
  assert.match(stop.result, /may still be running/);
  assert.equal(sessionStopPresentation({ ...snapshot, workspaces: [] }, session).label, 'Close host terminal');
  const containerSession = { ...session, executionKind: 'container', workspaceId: container.id };
  assert.equal(sessionStopPresentation(snapshot, containerSession).label, 'Stop session');
  assert.equal(sessionStopPresentation(snapshot, { ...containerSession, state: 'queued' }).label, 'Cancel session');
});

test('a retained terminal restores its project after transient empty inventory using the complete original identity', () => {
  const retained = { executionKind: 'host', contextNamespace: 'host:profile-a', runtimeId: 'same-runtime',
    workspaceId: host.id, projectId: project.id, state: 'running' };
  const target = { contextNamespace: retained.contextNamespace, runtimeId: retained.runtimeId };
  const checking = { ...snapshot, projects: [], sessions: [] };
  assert.equal(projectForRetainedSession(checking, target, retained), null);
  assert.equal(retainedSessionObservation(checking, target, retained), null);
  const returned = { ...snapshot, sessions: [{ ...retained }] };
  assert.equal(projectForRetainedSession(returned, target, retained), project);
  assert.equal(retainedSessionObservation(returned, target, retained), returned.sessions[0]);
  assert.equal(projectForRetainedSession(returned, null, retained), null); // Explicit project navigation does not recover an old view.
  assert.equal(projectForRetainedSession(returned, target, null), null);
  assert.equal(projectForRetainedSession(returned, { ...target, contextNamespace: 'host:profile-b' }, retained), null);
  assert.equal(projectForRetainedSession({ ...returned, sessions: [{ ...retained, workspaceId: container.id }] }, target, retained), null);
  assert.equal(projectForRetainedSession({ ...returned, sessions: [{ ...retained, projectId: containerProject.id }] }, target, retained), null);
  assert.equal(projectForRetainedSession({ ...returned, projects: [{ ...project, workspaceId: container.id }] }, target, retained), null);
});

test('an explicitly offered local folder can open with a host agent even when Botainer launch is disabled', () => {
  const source = { ...containerProject, capabilities: { startSession: false, filesRead: false }, nativeHostWorkspaces: [{ id: host.id, label: host.label }] };
  const inventory = { ...snapshot, projects: [project, source] };
  assert.equal(capability(inventory, 'startSession', source), false);
  assert.deepEqual(hostOpeningOptions(inventory, source), [{ id: host.id, label: host.label }]);
  assert.deepEqual(hostOpeningOptions(snapshot, containerProject), []); // No inferred eligibility from paths or launch capability.
  assert.deepEqual(hostOpeningOptions(inventory, project), []);
  assert.deepEqual(hostOpeningOptions({ ...inventory, workspaces: [container, { ...host, status: 'unavailable' }] }, source), []);
  assert.deepEqual(hostOpeningOptions({ ...inventory, workspaces: [{ ...container, kind: 'cluster' }, host] }, source), []);
  assert.deepEqual(hostOpeningOptions(inventory, { ...source, workspaceId: host.id }), []);
  const second = { ...host, id: 'second-host', label: 'Second reviewed host profile' };
  const multipleSource = { ...source, nativeHostWorkspaces: [...source.nativeHostWorkspaces, { id: second.id }, { id: host.id }, { id: 'unknown' }] };
  assert.deepEqual(hostOpeningOptions({ ...inventory, workspaces: [...inventory.workspaces, second], projects: [multipleSource] }, multipleSource),
    [{ id: host.id, label: host.label }, { id: second.id, label: second.label }]);
});

test('opening an existing host folder remains separate from agent launch and rejects a wrong returned scope', () => {
  const copy = hostProjectOpeningDescription(containerProject);
  assert.ok(copy.includes(containerProject.path));
  assert.match(copy, /does not create a container, change Botainer configuration, or start an agent/);
  assert.match(copy, /Choose New session afterward/);
  const empty = { ...snapshot, projects: [containerProject] };
  const opened = includeHostProjectResponse(empty, { project }, host.id);
  assert.deepEqual(opened.projects.map(item => item.id), [containerProject.id, project.id]);
  assert.deepEqual(opened.sessions, []);
  assert.equal(includeHostProjectResponse(opened, { project }, host.id).projects.length, 2);
  assert.throws(() => includeHostProjectResponse(empty, { project }, container.id), /did not confirm/);
  assert.throws(() => includeHostProjectResponse(empty, { project: { ...project, executionKind: 'container' } }, host.id), /did not confirm/);
  assert.throws(() => includeHostProjectResponse(empty, { project, session: { runtimeId: 'unexpected-launch' } }, host.id), /did not confirm/);
  assert.throws(() => includeHostProjectResponse(empty, { project: { ...project, id: containerProject.id } }, host.id), /ambiguous/);
  assert.throws(() => includeHostProjectResponse(opened, { project: { ...project, path: '/other/folder' } }, host.id), /ambiguous/);
  assert.match(hostOpeningErrorMessage(new Error('combined-native-project-outside-roots')), /outside the allowed project folders/);
  assert.match(hostOpeningErrorMessage(new Error('combined-native-source-local-required')), /remote folder cannot/);
  const ambiguous = 'The operation outcome is unknown. This request will not be retried automatically.';
  assert.equal(hostOpeningErrorMessage(new Error(ambiguous)), ambiguous);
  for (const filterId of [null, container.id]) {
    const selected = navigateWorkbench({ filterId, projectId: containerProject.id, target: null, panel: 'files' },
      { type: 'project', id: project.id }, opened);
    assert.equal(selected.filterId, filterId); // Opening host work does not narrow All workspaces or replace another list filter.
    assert.equal(selected.projectId, project.id);
  }
});

test('filtered project counts disclose all reported projects and clearing filters preserves selected terminal identity', () => {
  assert.equal(projectListView(snapshot).countLabel, '2');
  assert.equal(projectListView(snapshot).filtered, false);
  assert.equal(projectListView(snapshot, { workspaceId: host.id }).countLabel, '1 of 2');
  assert.equal(projectListView(snapshot, { query: 'no container' }).countLabel, '1 of 2');
  assert.equal(projectListView(snapshot, { workspaceId: container.id, query: 'absent' }).countLabel, '0 of 2');
  assert.equal(projectListView(snapshot, { query: ' ' }).filtered, false);
  const target = { contextNamespace: 'host:profile', runtimeId: 'keep-running' };
  const state = { filterId: host.id, projectId: project.id, target, panel: 'files', settingsMachineId: null };
  const all = navigateWorkbench(state, { type: 'filter', id: null }, snapshot);
  assert.equal(all.filterId, null);
  assert.equal(all.projectId, project.id);
  assert.equal(all.target, target);
  assert.equal(all.panel, 'files');
  assert.deepEqual(projectListView(snapshot, { workspaceId: all.filterId, query: '', pins: [containerProject.id] }).projects.map(item => item.id),
    [containerProject.id, project.id]);
});

test('stale host executable is named in the agent choice and cannot submit a new launch', () => {
  const claude = { id: 'claude', executable: '/opt/agents/claude', available: true };
  const stale = { ...project, availableAgents: [{ ...agents[0], available: false }, claude] };
  const current = { ...snapshot, projects: [stale, containerProject] };
  assert.deepEqual(sessionAgentOptions(current, stale), [
    { value: 'codex', label: 'Codex (default) · update needed' },
    { value: 'claude', label: 'Claude' },
  ]);
  assert.throws(() => sessionLaunchRequest(current, stale, 'default', 'new-work'), /Review agent update/);
  assert.throws(() => hostLaunchPresentation(current, stale, 'codex'), /Review agent update/);
  assert.deepEqual(sessionLaunchRequest(current, stale, 'claude', 'new-work'), { requestId: 'new-work', agent: 'claude' });
  assert.equal(projectStartUnavailableMessage({ ...stale, startUnavailableReason: 'Review agent updates in Settings.' }), 'Review agent updates in Settings.');
  assert.equal(projectUnavailableMessage({ ...stale, startUnavailableReason: 'Review agent updates in Settings.' }), '');
});
