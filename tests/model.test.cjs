'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const {createDemo} = require('../prototype/model.js');

function project(demo, name, config, hostId = 'local', kind = 'empty') {
  const result = demo.createProject({name, path: '~/Projects/' + name, hostId, kind, config});
  assert.equal(result.ok, true, JSON.stringify(result.error));
  return result.project;
}
function running(demo, projectId, options) {
  const started = demo.startSession(projectId, options);
  assert.equal(started.ok, true, JSON.stringify(started.error));
  assert.equal(demo.advanceSession(started.session.id).ok, true);
  assert.equal(demo.connectTerminal(started.session.id).ok, true);
  return started.session;
}

test('initial data distinguishes reachable runs, a queued allocation, idle project, and unknown host', () => {
  const demo = createDemo();
  assert.equal(demo.state.projects.length, 4);
  assert.equal(demo.sessionView('s-local').status, 'running');
  assert.equal(demo.sessionView('s-cluster').status, 'queued');
  assert.equal(demo.getLiveSession('p-notes'), null);
  assert.equal(demo.sessionView('s-workstation').status, 'unknown');
  assert.equal(demo.getSession('s-workstation').status, 'running');
  assert.equal(demo.getSession('missing'), null);
});

test('draft edits, saved defaults, and immutable launch configuration are independent', () => {
  const demo = createDemo();
  const p = demo.getProject('p-dashboard');
  assert.equal(demo.updateDraft(p.id, {network: 'none', cpus: 8}).ok, true);
  assert.equal(p.savedConfig.network, 'internet');
  assert.equal(demo.getSession('s-local').launchConfig.network, 'internet');
  assert.equal(demo.hasDraftChanges(p.id), true);
  assert.equal(demo.saveConfig(p.id).ok, true);
  assert.equal(p.savedConfig.network, 'none');
  assert.equal(demo.sessionView('s-local').configChanged, true);
  assert.equal(demo.getSession('s-local').launchConfig.network, 'internet');
  assert.throws(() => { demo.getSession('s-local').launchConfig.network = 'none'; }, TypeError);
  demo.stopSession('s-local');
  const next = demo.startSession(p.id).session;
  assert.equal(next.launchConfig.network, 'none');
  assert.equal(next.launchConfig.cpus, 8);
  assert.equal(next.launchConfigRevision, p.configRevision);
});

test('discarding a draft restores saved defaults without mutating saved revision', () => {
  const demo = createDemo();
  const p = demo.getProject('p-notes');
  demo.updateDraft(p.id, {cpus: 12});
  demo.resetDraft(p.id);
  assert.equal(p.draftConfig.cpus, 2);
  assert.equal(p.configRevision, 1);
  assert.equal(demo.hasDraftChanges(p.id), false);
});

test('invalid settings and stale save revisions preserve both saved configuration and the draft', () => {
  const demo = createDemo();
  const p = demo.getProject('p-notes');
  demo.updateDraft(p.id, {cpus: -1});
  assert.equal(demo.saveConfig(p.id).error.code, 'INVALID_CONFIG');
  assert.equal(p.savedConfig.cpus, 2);
  assert.equal(p.draftConfig.cpus, -1);
  demo.updateDraft(p.id, {cpus: 6});
  assert.equal(demo.saveConfig(p.id, 1).ok, true);
  demo.updateDraft(p.id, {cpus: 8});
  assert.equal(demo.saveConfig(p.id, 1).error.code, 'CONFIG_CONFLICT');
  assert.equal(p.savedConfig.cpus, 6);
  assert.equal(p.draftConfig.cpus, 8);
  assert.equal(demo.updateDraft(p.id, {unrecognized: true}).error.code, 'INVALID_CONFIG');
});

test('credential changes require ending the live run while ordinary defaults may save', () => {
  const demo = createDemo();
  demo.updateDraft('p-dashboard', {authMode: 'shared'});
  assert.equal(demo.saveConfig('p-dashboard').error.code, 'CONFIG_REQUIRES_STOP');
  assert.equal(demo.getProject('p-dashboard').draftConfig.authMode, 'shared');
  demo.stopSession('s-local');
  assert.equal(demo.saveConfig('p-dashboard').ok, true);
});

test('a project can have only one live run, including startup and queueing', () => {
  const demo = createDemo();
  const started = demo.startSession('p-notes', {title: 'A new task'});
  assert.equal(started.ok, true);
  assert.equal(started.session.status, 'starting');
  assert.equal(started.session.title, 'A new task');
  assert.equal(demo.startSession('p-notes').error.code, 'PROJECT_BUSY');
  assert.equal(demo.startSession('p-cluster').error.code, 'PROJECT_BUSY');
  demo.stopSession(started.session.id);
  assert.equal(demo.startSession('p-notes').ok, true);
});

test('retrying an acknowledged start request returns its original identity and never starts another agent', () => {
  const demo = createDemo();
  const first = demo.startSession('p-notes', {requestId: 'request-one'});
  const count = demo.state.sessions.length;
  demo.stopSession(first.session.id);
  const retried = demo.startSession('p-notes', {requestId: 'request-one'});
  assert.equal(retried.ok, true);
  assert.equal(retried.reused, true);
  assert.equal(retried.session.id, first.session.id);
  assert.equal(demo.state.sessions.length, count);
  assert.equal(demo.startSession('p-dashboard', {requestId: 'request-one'}).error.code, 'REQUEST_CONFLICT');
});

test('terminal attachment requires readiness; detaching does not end or replace the run', () => {
  const demo = createDemo();
  const started = demo.startSession('p-notes');
  assert.equal(demo.connectTerminal(started.session.id).error.code, 'TERMINAL_UNAVAILABLE');
  demo.advanceSession(started.session.id);
  demo.connectTerminal(started.session.id);
  demo.sendInput(started.session.id, 'Plan next steps');
  const before = demo.getSession(started.session.id);
  const transcript = JSON.stringify(before.transcript);
  const count = demo.state.sessions.length;
  demo.disconnectTerminal(before.id);
  assert.equal(before.status, 'running');
  assert.equal(before.endedAt, null);
  assert.equal(demo.sendInput(before.id, 'hello').error.code, 'TERMINAL_DISCONNECTED');
  assert.equal(demo.connectTerminal(before.id).ok, true);
  assert.equal(demo.getSession(before.id), before);
  assert.equal(JSON.stringify(before.transcript), transcript);
  assert.equal(demo.state.sessions.length, count);
});

test('losing a host gives unknown observed state, preserves run identity, and requires explicit terminal reconnect', () => {
  const demo = createDemo();
  const session = demo.getSession('s-local');
  const previousSeen = session.observedAt;
  demo.setHostReachable('local', false);
  assert.equal(session.status, 'running');
  assert.equal(demo.sessionView(session.id).status, 'unknown');
  assert.equal(session.observedAt, previousSeen);
  assert.equal(session.terminalConnected, false);
  assert.equal(demo.stopSession(session.id).error.code, 'HOST_UNREACHABLE');
  assert.equal(demo.startSession('p-notes').error.code, 'HOST_UNREACHABLE');
  demo.setHostReachable('local', true);
  assert.equal(demo.sessionView(session.id).status, 'running');
  assert.equal(session.terminalConnected, false);
  assert.equal(demo.connectTerminal(session.id).ok, true);
});

test('cluster allocation advances explicitly; cancelled allocation never starts', () => {
  const demo = createDemo();
  const queued = demo.getSession('s-cluster');
  assert.equal(queued.startedAt, null);
  assert.equal(demo.connectTerminal(queued.id).error.code, 'TERMINAL_UNAVAILABLE');
  demo.advanceSession(queued.id);
  assert.equal(queued.status, 'running');
  assert.equal(queued.queueReason, null);
  assert.equal(demo.connectTerminal(queued.id).ok, true);
  demo.stopSession(queued.id);
  const next = demo.startSession('p-cluster').session;
  demo.stopSession(next.id);
  assert.equal(next.status, 'cancelled');
  assert.equal(next.startedAt, null);
  assert.equal(demo.advanceSession(next.id).error.code, 'INVALID_TRANSITION');
});

test('stopping ends the run once, retains transcript, and prohibits further input', () => {
  const demo = createDemo();
  const session = running(demo, 'p-notes');
  demo.sendInput(session.id, 'run the tests');
  demo.stopSession(session.id);
  const endedAt = session.endedAt;
  const entries = session.transcript.length;
  assert.equal(session.status, 'stopped');
  assert.equal(session.terminalConnected, false);
  assert.equal(demo.stopSession(session.id).alreadyEnded, true);
  assert.equal(session.endedAt, endedAt);
  assert.equal(session.transcript.length, entries);
  assert.equal(demo.connectTerminal(session.id).error.code, 'TERMINAL_UNAVAILABLE');
});

test('shared credentials block competing projects, including queued or disconnected holders', () => {
  const demo = createDemo();
  const first = project(demo, 'shared-one', {authMode: 'shared'});
  const second = project(demo, 'shared-two', {authMode: 'shared'});
  const session = demo.startSession(first.id).session;
  assert.equal(demo.startSession(second.id).error.code, 'AUTH_CONFLICT');
  demo.advanceSession(session.id);
  demo.disconnectTerminal(session.id);
  assert.equal(demo.startSession(second.id).error.code, 'AUTH_CONFLICT');
  demo.stopSession(session.id);
  assert.equal(demo.startSession(second.id).ok, true);
});

test('different logins, agent families, or hosts do not implicitly share credentials', () => {
  const demo = createDemo();
  const first = project(demo, 'shared-one', {authMode: 'shared'});
  demo.startSession(first.id);
  const isolated = project(demo, 'isolated-two', {authMode: 'isolated'});
  const codex = project(demo, 'codex-two', {agent: 'codex', authMode: 'shared'});
  const profile = project(demo, 'profile-two', {authMode: 'shared', authProfile: 'separate-login'});
  assert.equal(demo.startSession(isolated.id).ok, true);
  assert.equal(demo.startSession(codex.id).ok, true);
  assert.equal(demo.startSession(profile.id).ok, true);
});

test('explicit credential-store identity catches cross-host shared-refresh conflicts', () => {
  const demo = createDemo();
  const a = project(demo, 'shared-local', {authMode: 'shared', credentialStore: 'same-login'});
  const b = project(demo, 'shared-cluster', {authMode: 'shared', credentialStore: 'same-login'}, 'cluster');
  demo.startSession(a.id);
  demo.setHostReachable('local', false);
  assert.equal(demo.startSession(b.id).error.code, 'AUTH_CONFLICT');
});

test('drafts never change a launch or its auth checks until saved', () => {
  const demo = createDemo();
  demo.updateDraft('p-notes', {network: 'none', cpus: 24});
  const started = demo.startSession('p-notes');
  assert.equal(started.session.launchConfig.network, 'internet');
  assert.equal(started.session.launchConfig.cpus, 2);
});

test('project initialization is simulated, host-scoped, and rejects duplicate folders', () => {
  const demo = createDemo();
  const existing = project(demo, 'existing-project', undefined, 'local', 'existing');
  const empty = project(demo, 'empty-project');
  assert.equal(existing.kind, 'existing');
  assert.equal(empty.kind, 'empty');
  assert.equal(demo.getLiveSession(empty.id), null);
  assert.equal(demo.createProject({name: 'duplicate', path: empty.path + '/', hostId: 'local', kind: 'existing'}).error.code, 'PROJECT_EXISTS');
  assert.equal(demo.createProject({name: 'offline', path: '~/Projects/offline', hostId: 'workstation', kind: 'empty'}).error.code, 'HOST_UNREACHABLE');
});

test('host runtime mismatch and unverified Codex broker are rejected without saving', () => {
  const demo = createDemo();
  demo.updateDraft('p-notes', {authMode: 'broker'});
  assert.equal(demo.saveConfig('p-notes').error.code, 'INVALID_CONFIG');
  demo.resetDraft('p-notes');
  demo.updateDraft('p-notes', {runtime: 'apptainer'});
  assert.equal(demo.saveConfig('p-notes').error.code, 'INVALID_CONFIG');
  assert.equal(demo.getProject('p-notes').savedConfig.runtime, 'docker');
});

test('terminal output is deterministic synthetic data and shell-like input stays inert', () => {
  const a = createDemo();
  const b = createDemo();
  const input = '$(touch /tmp/should-not-exist) <script>alert(1)</script>';
  const first = a.sendInput('s-local', input);
  const second = b.sendInput('s-local', input);
  assert.equal(first.ok, true);
  assert.equal(first.reply.text, second.reply.text);
  assert.match(first.reply.text, /does not execute shell commands/);
  assert.equal(a.getSession('s-local').transcript.at(-2).text, input);
  assert.deepEqual(a.snapshot(), b.snapshot());
});

test('snapshot mutations and resetting a demo cannot alter another demo', () => {
  const a = createDemo();
  const saved = a.snapshot();
  saved.projects[0].name = 'altered';
  assert.equal(a.getProject('p-dashboard').name, 'dashboard');
  a.stopSession('s-local');
  const b = createDemo();
  assert.equal(b.getSession('s-local').status, 'running');
});

test('transcript retention and prompt length are bounded', () => {
  const demo = createDemo();
  assert.equal(demo.sendInput('s-local', 'x'.repeat(4001)).error.code, 'INPUT_TOO_LONG');
  for (let i = 0; i < 100; i += 1) demo.sendInput('s-local', 'hello ' + i);
  assert.equal(demo.getSession('s-local').transcript.length, 120);
  assert.equal(demo.getSession('s-local').transcript.at(-2).text, 'hello 99');
});

test('pins outrank recency, name, and active sorts without changing substantive activity', () => {
  const demo = createDemo();
  const before = demo.projectActivity('p-notes');
  demo.setProjectPinned('p-dashboard', false);
  demo.setProjectPinned('p-notes', true);
  for (const sort of ['recent', 'name', 'active']) {
    assert.equal(demo.listProjects({sort})[0].id, 'p-notes');
  }
  assert.equal(demo.projectActivity('p-notes'), before);
  demo.setProjectPinned('p-notes', false);
  assert.equal(demo.listProjects({sort: 'recent'})[0].id, 'p-cluster');
});

test('recency follows session work, not opening, connection checks, or configuration editing', () => {
  const demo = createDemo();
  demo.setProjectPinned('p-dashboard', false);
  const localActivity = demo.projectActivity('p-dashboard');
  demo.getProject('p-dashboard');
  demo.sessionView('s-local');
  demo.disconnectTerminal('s-local');
  demo.connectTerminal('s-local');
  demo.updateDraft('p-dashboard', {cpus: 3});
  demo.saveConfig('p-dashboard');
  assert.equal(demo.projectActivity('p-dashboard'), localActivity);
  assert.equal(demo.listProjects()[0].id, 'p-cluster');
  demo.sendInput('s-local', 'Plan next steps');
  assert.ok(demo.projectActivity('p-dashboard') > localActivity);
  assert.equal(demo.listProjects()[0].id, 'p-dashboard');
  const offlineActivity = demo.projectActivity('p-workstation');
  demo.setHostReachable('workstation', true);
  assert.equal(demo.projectActivity('p-workstation'), offlineActivity);
});

test('project search covers names, paths, hosts, installation names, and task titles without changing state', () => {
  const demo = createDemo();
  const before = demo.snapshot();
  assert.deepEqual(demo.listProjects({query: '  DEVELOPMENT  '}).map(p => p.id), ['p-notes']);
  assert.deepEqual(demo.listProjects({query: 'parameter sweep'}).map(p => p.id), ['p-cluster']);
  assert.deepEqual(demo.listProjects({query: 'Codex'}).map(p => p.id), ['p-notes']);
  assert.equal(demo.listProjects({hostId: 'local'}).length, 2);
  assert.deepEqual(demo.listProjects({installationId: 'install-local-dev'}).map(p => p.id), ['p-notes']);
  assert.equal(demo.listProjects({query: 'missing-project'}).length, 0);
  assert.deepEqual(demo.snapshot(), before);
});

test('local installations have distinct namespaces; explicit installation selection controls launch provenance', () => {
  const demo = createDemo();
  const stable = demo.getInstallation('install-local-stable');
  const dev = demo.getInstallation('install-local-dev');
  assert.notEqual(stable.stateRoot, dev.stateRoot);
  assert.equal(demo.getProject('p-notes').installationId, dev.id);
  const created = demo.createProject({name: 'dev-task', path: '~/Projects/dev-task', kind: 'empty', installationId: dev.id});
  assert.equal(created.ok, true);
  assert.equal(created.project.hostId, 'local');
  const session = demo.startSession(created.project.id).session;
  assert.equal(session.installationId, dev.id);
  assert.equal(session.installationSnapshot.executable, dev.executable);
  assert.equal(session.installationSnapshot.stateRoot, dev.stateRoot);
  assert.equal(demo.createProject({name: 'mismatch', path: '~/Projects/mismatch', kind: 'empty', hostId: 'cluster', installationId: dev.id}).error.code, 'INSTALLATION_HOST_MISMATCH');
});

test('changing binaries does not duplicate a namespace; another state root does not duplicate checkout configuration', () => {
  const demo = createDemo();
  const stable = demo.getInstallation('install-local-stable');
  demo.state.installations.push(Object.assign({}, stable, {id: 'install-alias', name: 'Alias', executable: '~/Tools/other-botainer'}));
  const duplicate = demo.createProject({name: 'same', path: '~/Projects/dashboard', kind: 'existing', installationId: 'install-alias'});
  assert.equal(duplicate.error.code, 'PROJECT_EXISTS');
  assert.equal(duplicate.error.sameNamespace, true);
  const otherRoot = demo.createProject({name: 'same', path: '~/Projects/dashboard', kind: 'existing', installationId: 'install-local-dev'});
  assert.equal(otherRoot.error.code, 'PROJECT_EXISTS');
  assert.equal(otherRoot.error.sameNamespace, false);
  assert.match(otherRoot.error.message, /one project configuration file/);
});

test('raw project validation is nonmutating and explicitly accepts JSON rather than arbitrary YAML', () => {
  const demo = createDemo();
  const before = demo.snapshot();
  const config = JSON.parse(demo.serializeProjectConfig('p-notes'));
  config.cpus = 8;
  assert.equal(demo.validateProjectConfigText('p-notes', JSON.stringify(config)).ok, true);
  assert.equal(demo.validateProjectConfigText('p-notes', 'agent: codex\nnetwork: none').error.code, 'CONFIG_PARSE_ERROR');
  assert.deepEqual(demo.snapshot(), before);
});

test('invalid raw text persists and cannot accidentally save the previous parsed configuration', () => {
  const demo = createDemo();
  const p = demo.getProject('p-notes');
  const text = '{ "agent": ';
  assert.equal(demo.updateProjectConfigText(p.id, text).ok, false);
  assert.equal(demo.serializeProjectConfig(p.id), text);
  assert.equal(p.draftText, text);
  assert.equal(demo.hasDraftChanges(p.id), true);
  assert.equal(demo.saveConfig(p.id).error.code, 'CONFIG_PARSE_ERROR');
  assert.equal(p.savedConfig.agent, 'codex');
  assert.equal(p.configRevision, 1);
  demo.resetDraft(p.id);
  assert.equal(p.draftError, null);
  assert.equal(demo.hasDraftChanges(p.id), false);
});

test('raw project saves retain revision conflicts and live credential-change restrictions', () => {
  const demo = createDemo();
  const localConfig = JSON.parse(demo.serializeProjectConfig('p-dashboard'));
  localConfig.authMode = 'shared';
  const blockedText = JSON.stringify(localConfig, null, 4);
  assert.equal(demo.saveProjectConfigText('p-dashboard', blockedText).error.code, 'CONFIG_REQUIRES_STOP');
  assert.equal(demo.getProject('p-dashboard').draftText, blockedText);
  const notes = JSON.parse(demo.serializeProjectConfig('p-notes'));
  notes.memoryGb = 12;
  assert.equal(demo.saveProjectConfigText('p-notes', JSON.stringify(notes), 1).ok, true);
  notes.memoryGb = 20;
  assert.equal(demo.saveProjectConfigText('p-notes', JSON.stringify(notes), 1).error.code, 'CONFIG_CONFLICT');
  assert.equal(demo.getProject('p-notes').savedConfig.memoryGb, 12);
  assert.equal(demo.getProject('p-notes').draftConfig.memoryGb, 20);
});

test('instance policy validation and save revisions preserve invalid or conflicting drafts', () => {
  const demo = createDemo();
  const installation = demo.getInstallation('install-local-dev');
  const before = demo.snapshot();
  assert.equal(demo.validateInstallationConfigText(installation.id, '{bad').error.code, 'CONFIG_PARSE_ERROR');
  assert.deepEqual(demo.snapshot(), before);
  demo.updateInstallationConfigText(installation.id, '{bad');
  assert.equal(demo.saveInstallationConfig(installation.id).error.code, 'CONFIG_PARSE_ERROR');
  assert.equal(installation.draftText, '{bad');
  demo.resetInstallationDraft(installation.id);
  const policy = JSON.parse(demo.serializeInstallationConfig(installation.id));
  policy.default_auth_mode = 'shared';
  assert.equal(demo.saveInstallationConfigText(installation.id, JSON.stringify(policy), 1).ok, true);
  policy.network.default_mode = 'none';
  assert.equal(demo.saveInstallationConfigText(installation.id, JSON.stringify(policy), 1).error.code, 'CONFIG_CONFLICT');
  assert.equal(installation.savedConfig.network.default_mode, 'internet');
  assert.equal(installation.draftConfig.network.default_mode, 'none');
});

test('instance policy updates affect new project defaults and future launch checks, never existing snapshots', () => {
  const demo = createDemo();
  const installation = demo.getInstallation('install-local-stable');
  const session = demo.getSession('s-local');
  const snapshot = JSON.stringify(session.installationSnapshot);
  const policy = {version: 'policy-v1', default_auth_mode: 'shared', network: {default_mode: 'none'}};
  assert.equal(demo.saveInstallationConfigText(installation.id, JSON.stringify(policy)).ok, true);
  assert.equal(JSON.stringify(session.installationSnapshot), snapshot);
  assert.throws(() => { session.installationSnapshot.config.network.default_mode = 'none'; }, TypeError);
  assert.equal(demo.sessionView(session.id).installationConfigChanged, true);
  assert.equal(demo.getProject('p-dashboard').savedConfig.authMode, 'isolated');
  demo.stopSession(session.id);
  assert.equal(demo.startSession('p-dashboard').error.code, 'POLICY_REFUSED');
  const fresh = demo.createProject({name: 'policy-defaults', path: '~/Projects/policy-defaults', kind: 'empty', installationId: installation.id}).project;
  assert.equal(fresh.savedConfig.authMode, 'shared');
  assert.equal(fresh.savedConfig.network, 'none');
  const next = demo.startSession(fresh.id);
  assert.equal(next.ok, true);
  assert.equal(next.session.installationSnapshot.configRevision, installation.configRevision);
});

test('named shared credentials still conflict across installations with separate state roots', () => {
  const demo = createDemo();
  const config = {authMode: 'shared', credentialStore: 'copied-login'};
  const first = demo.createProject({name: 'stable-shared', path: '~/Projects/stable-shared', kind: 'empty', installationId: 'install-local-stable', config}).project;
  const second = demo.createProject({name: 'dev-shared', path: '~/Projects/dev-shared', kind: 'empty', installationId: 'install-local-dev', config}).project;
  assert.equal(demo.startSession(first.id).ok, true);
  assert.equal(demo.startSession(second.id).error.code, 'AUTH_CONFLICT');
});
