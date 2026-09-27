import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { SETUP_LIMIT, connectionRows, connectionAgentSummary, connectionDisplayLabel, createConnectionEditor, createHostAgentUpdateEditor, createInstallationUpdateEditor, mountConnectionsPanel,
  preparationRequest, reviewedProfile, reviewedHookCandidates, sshSnippet } from '../../frontend/connections.js';
import { workspaceConnectionPresentation } from '../../frontend/app.js';

const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const pause = () => new Promise(resolve => setImmediate(resolve));
const profile = (id = 'cluster') => ({ version: 1, id, label: 'Research cluster', ssh_alias: 'research',
  remote_python: '/opt/environment/bin/python', launcher: { path: '/opt/environment/bin/botainer', sha256: 'a'.repeat(64) },
  source_root: '/opt/source', state_root: '/home/example/.botainer', project_roots: { projects: '/work/projects' },
  control_root: '/home/example/.dashboard' });
const outcome = (id = 'cluster') => ({ kind: 'remote', profile: profile(id), checks: [], instructions: [], qualification: 'untested' });
const localHooks = () => ({
  '/opt/source/plugins/credentials/hooks/start.py': 'a'.repeat(64),
  '/home/example/.botainer/plugins/credentials/hooks/stop.py': 'b'.repeat(64),
});
const localOutcome = () => ({ ...outcome('local'), kind: 'local', hook_candidates: localHooks(),
  profile: { ...profile('local'), approved_hooks: {},
    source_hashes: { 'plugins/credentials/hooks/start.py': 'a'.repeat(64) },
    support_hashes: { '/home/example/.botainer/plugins/credentials/hooks/stop.py': 'b'.repeat(64) } } });
const entry = (overrides = {}) => ({ id: 'cluster', kind: 'remote', label: 'Research cluster', profileDigest: 'a'.repeat(64),
  profilePath: '/private/settings/remote.json', enabled: true, ...overrides });
const snapshot = (overrides = {}) => ({ enabled: true, revision: 3, entries: [], active: [], pendingRestart: false,
  notice: 'Saved changes apply after restart.', ...overrides });
const agentUpdate = (overrides = {}) => ({ id: 'host-native', agent: 'codex', label: 'Native Codex', revision: 3,
  current: { path: '/opt/agents/old/codex', sha256: 'a'.repeat(64) },
  candidate: { path: '/opt/agents/current/codex', sha256: 'b'.repeat(64) }, changed: true,
  notice: 'Existing sessions keep their terminal owner.', ...overrides });
const hostEntry = (overrides = {}) => entry({ id: 'host-native', kind: 'host', label: 'Native agents',
  agents: [{ id: 'claude', label: 'Claude', path: '/opt/agents/claude', sha256: 'c'.repeat(64) },
    { id: 'codex', label: 'Codex', ...agentUpdate().current, available: false, unavailableReason: 'Reviewed executable is missing.' }],
  ...overrides });

const installationSettings = (overrides = {}) => ({ id: 'cluster', kind: 'remote', revision: 3, profileDigest: 'a'.repeat(64),
  settings: { python: '/opt/environment/bin/python', source_root: '/opt/source', launcher: '/opt/environment/bin/botainer' }, ...overrides });
const installationUpdate = (overrides = {}) => ({ id: 'cluster', kind: 'remote', revision: 3, profileDigest: 'a'.repeat(64),
  profile: profile(), candidateDigest: 'b'.repeat(64), changed: true,
  changes: {}, checks: [{ name: 'Installation files', status: 'passed', message: 'Selected files match the reviewed execution policy.' }],
  instructions: ['Review the selected software before saving.'], notice: 'Saved changes require a restart.',
  summary: ['2 selected source file fingerprints differ.', 'Existing selected plugin bytes remain unchanged.'],
  requiresRestart: true, requiresPairing: true, preservesRemoteHistory: false, ...overrides });

test('Botainer update review binds software paths, saved profile and candidate digest without importing a profile', async () => {
  const calls = [], editor = createInstallationUpdateEditor(async (path, options) => {
    calls.push([path, options]); return !options ? installationSettings() : path.endsWith('/prepare')
      ? installationUpdate() : snapshot({ revision: 4, entries: [entry({ profileDigest: 'b'.repeat(64) })] });
  });
  await assert.rejects(editor.load('cluster'), /Refresh saved machines/);
  editor.accept(snapshot({ entries: [entry()] })); await editor.load('cluster');
  await assert.rejects(editor.inspect({}, false), /Acknowledge/);
  await assert.rejects(editor.inspect({ ssh_alias: 'another-account' }, true), /Only installed Botainer/);
  await assert.rejects(editor.inspect({ project_roots: ['/another-project'] }, true), /Only installed Botainer/);
  await editor.inspect({ source_root: ' /opt/new-source ' }, true);
  await assert.rejects(editor.save(false), /confirm that you trust/);
  await editor.save(true);
  assert.deepEqual(calls[1], ['/api/connections/cluster/installation-update/prepare', { method: 'POST', mutation: false,
    timeoutMs: 45000, body: { revision: 3, profileDigest: 'a'.repeat(64), changes: { source_root: '/opt/new-source' }, acknowledged: true } }]);
  assert.deepEqual(calls[2], ['/api/connections/cluster/installation-update', { method: 'POST', timeoutMs: 45000, body: {
    revision: 3, profileDigest: 'a'.repeat(64), changes: { source_root: '/opt/new-source' }, candidateDigest: 'b'.repeat(64),
    acknowledged: true, confirmed: true } }]);
  assert.equal(Object.hasOwn(calls[2][1].body, 'profile'), false);
  await assert.rejects(editor.save(true), /Inspect Botainer/);
});

test('Botainer update load and inspect reject stale or mismatched identities and cancelled results', async () => {
  for (const changed of [{ id: 'another' }, { kind: 'local' }, { revision: 4 }, { profileDigest: 'c'.repeat(64) }]) {
    const editor = createInstallationUpdateEditor(async () => installationSettings(changed));
    editor.accept(snapshot({ entries: [entry()] })); await assert.rejects(editor.load('cluster'), /no longer current/);
  }
  for (const phase of ['load', 'inspect']) for (const invalidate of [editor => editor.invalidate(), editor => editor.accept(snapshot({ revision: 4, entries: [entry()] }))]) {
    const pending = deferred();
    const editor = createInstallationUpdateEditor(async (_path, options) => !options && phase === 'inspect' ? installationSettings() : pending.promise);
    editor.accept(snapshot({ entries: [entry()] }));
    if (phase === 'inspect') await editor.load('cluster');
    const check = phase === 'load' ? editor.load('cluster') : editor.inspect({}, true);
    invalidate(editor); pending.resolve(phase === 'load' ? installationSettings() : installationUpdate());
    assert.equal(await check, null); await assert.rejects(editor.save(true), /Inspect Botainer/);
  }
});

test('failed, unchanged or mismatched Botainer inspections cannot create a saveable candidate', async () => {
  for (const outcome of [installationUpdate({ profile: null, qualification: 'failed' }), installationUpdate({ changed: false }),
    installationUpdate({ id: 'another' }), installationUpdate({ revision: 4 }), installationUpdate({ profileDigest: 'd'.repeat(64) }),
    installationUpdate({ kind: 'local' }), installationUpdate({ profile: profile('another') }), installationUpdate({ candidateDigest: 'unknown' })]) {
    const editor = createInstallationUpdateEditor(async (_path, options) => options ? outcome : installationSettings());
    editor.accept(snapshot({ entries: [entry()] })); await editor.load('cluster');
    if (!outcome.profile || outcome.changed === false) await editor.inspect({}, true);
    else await assert.rejects(editor.inspect({}, true), /no longer current/);
    await assert.rejects(editor.save(true), /Inspect Botainer/);
  }
});

test('failed Botainer update write consumes review and never retries; local updates cannot add a launcher or host profile', async () => {
  let writes = 0;
  const editor = createInstallationUpdateEditor(async (path, options) => {
    if (!options) return installationSettings({ id: 'local', kind: 'local' });
    if (path.endsWith('/prepare')) return installationUpdate({ id: 'local', kind: 'local', profile: profile('local') });
    writes++; throw new Error('Inspected installation changed.');
  });
  editor.accept(snapshot({ entries: [entry({ id: 'local', kind: 'local' }), hostEntry()] }));
  await assert.rejects(editor.load('host-native'), /Refresh saved machines/);
  await editor.load('local'); await assert.rejects(editor.inspect({ launcher: '/opt/unrelated' }, true), /Only installed Botainer/);
  await editor.inspect({}, true); await assert.rejects(editor.save(true), /installation changed/);
  await assert.rejects(editor.save(true), /Inspect Botainer/); assert.equal(writes, 1);
});

test('host update review binds explicit trust to the inspected path, hash and settings revision', async () => {
  const calls = [], editor = createHostAgentUpdateEditor(async (path, options) => {
    calls.push([path, options]); return path.endsWith('/prepare') ? agentUpdate() : snapshot({ revision: 4 });
  });
  await assert.rejects(editor.inspect('host-native', 'codex'), /Refresh saved machines/);
  editor.accept(snapshot());
  for (const trust of [false, true]) await assert.rejects(editor.save(trust), /Inspect the installed update/);
  await editor.inspect('host-native', 'codex', ' /opt/agents/codex-link ');
  for (const trust of [false, undefined, 'true']) await assert.rejects(editor.save(trust), /confirm that you trust/);
  assert.deepEqual(calls[0], ['/api/connections/host-native/agent-update/prepare', { method: 'POST', mutation: false,
    timeoutMs: 45000, body: { revision: 3, agent: 'codex', path: '/opt/agents/codex-link' } }]);
  await editor.save(true);
  assert.deepEqual(calls[1], ['/api/connections/host-native/agent-update', { method: 'POST', body: {
    revision: 3, agent: 'codex', path: '/opt/agents/current/codex', sha256: 'b'.repeat(64), confirmed: true } }]);
  await assert.rejects(editor.save(true), /Inspect the installed update/);
  assert.equal(calls.length, 2);
});

test('host update inspection rejects late results after edits or a settings revision change', async () => {
  for (const invalidate of [editor => editor.invalidate(), editor => editor.accept(snapshot({ revision: 4 }))]) {
    const pending = deferred(), calls = [], editor = createHostAgentUpdateEditor(async (...args) => {
      calls.push(args); return pending.promise;
    });
    editor.accept(snapshot()); const inspection = editor.inspect('host-native', 'codex');
    invalidate(editor); pending.resolve(agentUpdate()); assert.equal(await inspection, null);
    await assert.rejects(editor.save(true), /Inspect the installed update/); assert.equal(calls.length, 1);
  }
});

test('host update results must identify the requested agent, connection and revision; unchanged files cannot be saved', async () => {
  for (const result of [agentUpdate({ id: 'another-host' }), agentUpdate({ agent: 'claude' }),
    agentUpdate({ revision: 4 }), agentUpdate({ candidate: { path: 'codex', sha256: 'b'.repeat(64) } }),
    agentUpdate({ candidate: { path: '/opt/codex', sha256: 'unknown' } })]) {
    const editor = createHostAgentUpdateEditor(async () => result); editor.accept(snapshot());
    await assert.rejects(editor.inspect('host-native', 'codex'), /review is no longer current/);
    await assert.rejects(editor.save(true), /Inspect the installed update/);
  }
  const editor = createHostAgentUpdateEditor(async () => agentUpdate({ changed: false })); editor.accept(snapshot());
  assert.equal((await editor.inspect('host-native', 'codex')).changed, false);
  await assert.rejects(editor.save(true), /Inspect the installed update/);
});

test('host update write failure consumes the review and never retries an ambiguous update', async () => {
  const calls = [], editor = createHostAgentUpdateEditor(async (path, options) => {
    calls.push([path, options]);
    if (path.endsWith('/prepare')) return agentUpdate();
    throw new Error('Connections changed; refresh and review.');
  });
  editor.accept(snapshot()); await editor.inspect('host-native', 'codex');
  await assert.rejects(editor.save(true), /Connections changed/);
  await assert.rejects(editor.save(true), /Inspect the installed update/);
  assert.equal(calls.length, 2);
});

test('editing while inspection is pending invalidates its result and cannot leave a saveable candidate', async () => {
  const pending = deferred(), calls = [];
  const editor = createConnectionEditor(async (...args) => { calls.push(args); return pending.promise; });
  editor.accept(snapshot());
  const first = editor.inspect({ kind: 'remote', label: 'Original selection' });
  editor.invalidate(); pending.resolve(outcome());
  assert.equal(await first, null);
  await assert.rejects(editor.save(true), /Review the exact prepared profile/);
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], '/api/connections/prepare');
  assert.equal(calls[0][1].mutation, false);
  assert.equal(calls[0][1].timeoutMs, 45000);
});

test('overlapping inspection responses retain only the newest selected installation', async () => {
  const first = deferred(), second = deferred(), calls = [];
  const editor = createConnectionEditor(async (path, options) => {
    calls.push([path, options]);
    if (path.endsWith('/prepare')) return calls.length === 1 ? first.promise : second.promise;
    return snapshot({ revision: 4 });
  });
  editor.accept(snapshot());
  const a = editor.inspect({ id: 'old' }), b = editor.inspect({ id: 'new' });
  second.resolve(outcome('new')); assert.equal((await b).profile.id, 'new');
  first.resolve(outcome('old')); assert.equal(await a, null);
  await editor.save(true);
  assert.equal(calls[2][1].body.profile.id, 'new');
  assert.equal(calls[2][1].body.revision, 3);
});

test('failed replacement import clears the previous candidate instead of saving stale reviewed content', async () => {
  const editor = createConnectionEditor(async () => assert.fail('No write is authorized'));
  editor.accept(snapshot()); editor.import('remote', JSON.stringify(profile('old')));
  assert.throws(() => editor.import('remote', '{ broken'), /valid JSON/);
  await assert.rejects(editor.save(true), /Review the exact prepared profile/);
});

test('saving needs an exact candidate, a known revision and boolean explicit trust', async () => {
  const calls = [], editor = createConnectionEditor(async (path, options) => {
    calls.push([path, options]); return snapshot({ revision: 4 });
  });
  editor.import('remote', JSON.stringify(profile()));
  await assert.rejects(editor.save(true), /Review the exact prepared profile/);
  editor.accept(snapshot());
  for (const trust of [false, undefined, null, 0, 'true', {}]) {
    await assert.rejects(editor.save(trust), /Review the exact prepared profile/);
  }
  assert.equal(calls.length, 0);
  await editor.save(true);
  assert.deepEqual(calls[0][1].body, { kind: 'remote', profile: profile(), revision: 3, trusted: true });
  await assert.rejects(editor.save(true), /Review the exact prepared profile/);
  assert.equal(calls.length, 1);
});

test('revision conflicts propagate once and never fetch or retry automatically', async () => {
  const calls = [], conflict = new Error('Connections changed in another window; reload and review before saving again.');
  const editor = createConnectionEditor(async (path, options) => {
    calls.push([path, options]);
    if (calls.length === 1) throw conflict;
    return snapshot({ revision: 8 });
  });
  editor.accept(snapshot()); editor.import('remote', JSON.stringify(profile()));
  await assert.rejects(editor.save(true), error => error === conflict);
  await pause(); assert.equal(calls.length, 1);
  editor.accept(snapshot({ revision: 7 }));
  editor.import('remote', JSON.stringify(profile()));
  await editor.save(true); // An explicit separate action after refresh/review.
  assert.equal(calls.length, 2);
  assert.equal(calls[1][1].body.revision, 7);
});

test('local hook opt-in uses only exact canonical probed files bound to the prepared profile', () => {
  const value = localOutcome();
  const injected = { ...value.hook_candidates, '/elsewhere/hook.py': 'a'.repeat(64),
    '/opt/source/../elsewhere/hooks/run.py': 'a'.repeat(64),
    '/opt/source//plugins/credentials/hooks/start.py': 'a'.repeat(64),
    '/opt/source/plugins/credentials/hooks/start.py/': 'a'.repeat(64),
    '/opt/source-bad/plugins/credentials/hooks/start.py': 'a'.repeat(64),
    '/opt/source/plugins/credentials/hooks/bad\n.py': 'a'.repeat(64) };
  assert.deepEqual(reviewedHookCandidates('local', value.profile, injected), localHooks());
  assert.deepEqual(reviewedHookCandidates('local', value.profile, { ...localHooks(),
    '/opt/source/plugins/credentials/hooks/start.py': 'c'.repeat(64) }), {
    '/home/example/.botainer/plugins/credentials/hooks/stop.py': 'b'.repeat(64) });
  assert.deepEqual(reviewedHookCandidates('local', { ...value.profile,
    support_hashes: { '/opt/source/plugins/credentials/hooks/start.py': 'c'.repeat(64) } }, localHooks()), {},
    'conflicting source/support pins cannot offer an approval the backend would reject');
  for (const kind of ['host', 'remote', undefined]) assert.deepEqual(reviewedHookCandidates(kind, value.profile, injected), {});
  for (const invalid of [null, [], 'not hooks', Object.fromEntries(Array.from({ length: 2049 }, (_, i) => [String(i), 'a'.repeat(64)]))]) {
    assert.deepEqual(reviewedHookCandidates('local', value.profile, invalid), {});
  }
});

test('hook approvals are explicit, cloned and reversible without dropping pre-existing approvals', async () => {
  const prepared = localOutcome(), existing = { '/opt/source/plugins/existing/hooks/start.py': 'c'.repeat(64) }, writes = [];
  prepared.profile.approved_hooks = { ...existing };
  const editor = createConnectionEditor(async (path, options) => {
    if (path.endsWith('/prepare')) return prepared;
    writes.push(options.body); return snapshot({ revision: 4 });
  });
  editor.accept(snapshot()); await editor.inspect({ kind: 'local' });
  const token = editor.generation;
  const display = editor.hookCandidates; display['/arbitrary/hook'] = 'd'.repeat(64);
  const approved = editor.approveHooks(true, token);
  assert.deepEqual(approved.approved_hooks, { ...existing, ...localHooks() });
  assert.deepEqual(prepared.profile.approved_hooks, existing, 'inspection response must not be mutated');
  approved.approved_hooks['/also-arbitrary/hook'] = 'e'.repeat(64);
  assert.deepEqual(editor.approveHooks(false, token).approved_hooks, existing);
  await editor.save(true);
  assert.deepEqual(writes[0].profile.approved_hooks, existing);
  assert.equal(writes.length, 1);
});

test('inspection, import, edits and changed settings revisions invalidate hook approvals and stale handlers', async () => {
  const editor = createConnectionEditor(async () => localOutcome());
  editor.accept(snapshot()); await editor.inspect({ kind: 'local' });
  const stale = editor.generation; editor.approveHooks(true, stale);
  editor.accept(snapshot({ revision: 4 }));
  assert.deepEqual(editor.hookCandidates, {});
  assert.throws(() => editor.approveHooks(true, stale), /Inspect this local/);
  await assert.rejects(editor.save(true), /Review the exact/);
  await editor.inspect({ kind: 'local' });
  assert.throws(() => editor.approveHooks(true, stale), /Inspect this local/);
  editor.approveHooks(true, editor.generation); editor.invalidate();
  assert.deepEqual(editor.hookCandidates, {});
  editor.import('local', JSON.stringify(localOutcome().profile));
  assert.throws(() => editor.approveHooks(true, editor.generation), /Inspect this local/);
  assert.deepEqual(editor.hookCandidates, {}, 'raw imported display cannot create hook approval candidates');
});

test('changed revision while a local inspection is pending rejects its result and hooks', async () => {
  const pending = deferred(), editor = createConnectionEditor(async () => pending.promise);
  editor.accept(snapshot()); const inspecting = editor.inspect({ kind: 'local' });
  editor.accept(snapshot({ revision: 4 })); pending.resolve(localOutcome());
  assert.equal(await inspecting, null); assert.deepEqual(editor.hookCandidates, {});
  await assert.rejects(editor.save(true), /Review the exact/);
});

test('failed preparation clears earlier candidate and does not treat an untested check as activation', async () => {
  const editor = createConnectionEditor(async () => ({ kind: 'remote', profile: null, qualification: 'failed' }));
  editor.accept(snapshot()); editor.import('remote', JSON.stringify(profile()));
  const result = await editor.inspect({});
  assert.equal(result.qualification, 'failed');
  await assert.rejects(editor.save(true), /Review the exact prepared profile/);
});

test('preparation requests filter credentials and hidden fields for the selected kind', () => {
  const values = { kind: 'remote', id: ' cluster ', label: ' Research cluster ', project_roots: ' /work/a\n\n /work/b ',
    ssh_alias: ' research ', python: ' /opt/python ', source_root: ' /opt/source ', state_root: '',
    launcher: '', control_root: ' /home/example/.dashboard ', docker_path: '/ignored/docker',
    docker_host: 'unix:///ignored.sock', tmux_path: '/ignored/tmux', home: '/ignored/home',
    password: 'never transmit', privateKey: 'never transmit', ssh_options: '-oProxyCommand=bad', read_only_acknowledged: true };
  assert.deepEqual(preparationRequest(values), { kind: 'remote', id: 'cluster', label: 'Research cluster',
    project_roots: ['/work/a', '/work/b'], read_only_acknowledged: true, python: '/opt/python',
    source_root: '/opt/source', control_root: '/home/example/.dashboard', ssh_alias: 'research' });
  const local = preparationRequest({ ...values, kind: 'local', read_only_acknowledged: 'true' });
  assert.equal(local.id, 'local'); assert.equal(local.read_only_acknowledged, false);
  assert.equal(local.docker_path, '/ignored/docker');
  assert.equal(Object.hasOwn(local, 'ssh_alias'), false);
  assert.equal(Object.hasOwn(local, 'password'), false);
});

test('profile parsing bounds UTF-8 bytes and accepts objects only', () => {
  assert.deepEqual(reviewedProfile(JSON.stringify(profile())), profile());
  for (const raw of [undefined, 3, '[]', 'null', 'true', '"text"', '{broken',
    JSON.stringify({ note: 'é'.repeat(SETUP_LIMIT / 2) })]) {
    assert.throws(() => reviewedProfile(raw));
  }
  assert.ok(JSON.stringify({ note: 'é'.repeat(SETUP_LIMIT / 2) }).length < SETUP_LIMIT);
});

test('SSH snippet rejects newline, directive and shell interpolation fields', () => {
  const valid = { alias: 'research', host: 'login.example.invalid', user: 'example', identity: '~/.ssh/id_ed25519' };
  for (const [field, values] of Object.entries({
    alias: ['research\nHost *', '-bad', '$(touch file)', 'name with space'],
    host: ['example\nProxyCommand sh', '$(host)', 'host;whoami', 'host/path'],
    user: ['user\nForwardAgent yes', 'user@elsewhere', '`id`'],
    port: ['0', '65536', '-1', '22\nHost *', '22;id'],
    identity: ['key', '/key\nProxyCommand bad', '~/a b', '~/$(id)', '/tmp/%h'],
  })) for (const value of values) assert.throws(() => sshSnippet({ ...valid, [field]: value }), undefined, `${field}: ${value}`);
  const text = sshSnippet(valid);
  assert.match(text, /^Host research\n/);
  assert.match(text, /IdentityFile ~\/\.ssh\/id_ed25519\n    IdentitiesOnly yes/);
  assert.match(text, /ForwardAgent no/); assert.match(text, /ForwardX11 no/);
  assert.match(text, /ClearAllForwardings yes/);
  assert.match(text, /ControlPath ~\/\.ssh\/dashboard-%C/);
  assert.match(text, /ControlPersist 15m/);
  assert.match(text, /ServerAliveInterval 30\n    ServerAliveCountMax 3/);
  assert.doesNotMatch(text, /StrictHostKeyChecking no|UserKnownHostsFile|ProxyCommand/);
});

test('saved removal and disable stay distinct from the still-loaded active machine', () => {
  const active = entry(), removed = connectionRows(snapshot({ active: [active] }))[0];
  assert.equal(removed.saved, false); assert.equal(removed.active, true);
  assert.match(removed.status, /Removal saved.*still loaded until restart/);
  const disabled = connectionRows(snapshot({ entries: [{ ...active, enabled: false }], active: [active] }))[0];
  assert.match(disabled.status, /Disable saved.*still loaded/);
  const replacement = connectionRows(snapshot({ entries: [{ ...active, profileDigest: 'b'.repeat(64) }], active: [active] }))[0];
  assert.equal(replacement.active, true); assert.match(replacement.status, /loads after restart/);
  assert.equal(connectionRows(snapshot({ entries: [active], active: [active] }))[0].status, 'Loaded in this dashboard');
});

test('host connection summaries use configured agent IDs and never infer identity from display names', () => {
  assert.equal(connectionAgentSummary({ executionKind: 'host', availableAgents: [{ id: 'claude' }] }), 'Claude · no container');
  assert.equal(connectionAgentSummary({ kind: 'host', availableAgents: [{ id: 'codex' }] }), 'Codex · no container');
  assert.equal(connectionAgentSummary({ kind: 'host', availableAgents: [{ id: 'codex' }, { id: 'claude' },
    { id: 'codex' }, null, { id: 'shell', label: 'Claude' }] }), 'Claude + Codex · no container');
  for (const availableAgents of [undefined, null, 'claude', [], [{ id: 'shell', label: 'Codex' }]]) {
    assert.equal(connectionAgentSummary({ kind: 'host', label: 'Claude and Codex', availableAgents }), 'Host · no container');
  }
  assert.equal(connectionAgentSummary({ kind: 'local', label: 'Host · Codex', availableAgents: [{ id: 'codex' }] }), '');
  assert.equal(connectionAgentSummary(null), '');
});

test('connection names prefer loaded workspace identity without rewriting saved labels', () => {
  const saved = entry({ label: 'Saved name' }), loaded = { id: saved.id, label: 'Loaded name' };
  assert.equal(connectionDisplayLabel(saved, loaded), 'Loaded name');
  assert.equal(connectionDisplayLabel(saved), 'Saved name');
  assert.equal(connectionDisplayLabel({ id: 'unnamed' }), 'unnamed');
  assert.equal(connectionDisplayLabel(null), '');
  assert.equal(saved.label, 'Saved name'); assert.equal(loaded.label, 'Loaded name');
});

test('remove and enable send settings revisions and never a lifecycle endpoint', async () => {
  const calls = [], editor = createConnectionEditor(async (path, options) => {
    calls.push([path, options]); return snapshot({ revision: 3 + calls.length });
  });
  editor.accept(snapshot());
  await editor.change('remote/id', 'remove');
  await editor.change('cluster', 'enabled', false);
  assert.deepEqual(calls.map(([path, options]) => [path, options.body]), [
    ['/api/connections/remote%2Fid/remove', { revision: 3, confirmed: true }],
    ['/api/connections/cluster/enabled', { revision: 4, enabled: false }],
  ]);
  await assert.rejects(editor.change('cluster', 'stop'), /Refresh saved machines first/);
  assert.equal(calls.length, 2);
});

// Minimal event/element adapter. This exercises the production mount handlers,
// not a browser engine, CSS, accessibility tree or visual layout.
class Element {
  constructor(tagName, text = '') {
    this.tagName = tagName.toUpperCase(); this.children = []; this.parentNode = null;
    this._text = text; this.listeners = new Map(); this.attributes = new Map();
    this.value = ''; this.checked = false; this.disabled = false; this.hidden = false;
  }
  set textContent(text) { this._text = String(text); this.replaceChildren(); }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  append(...children) {
    for (const child of children) {
      if (child.parentNode) child.parentNode.children.splice(child.parentNode.children.indexOf(child), 1);
      child.parentNode = this; this.children.push(child);
    }
  }
  replaceChildren(...children) {
    // A browser loses focus when the focused node (or its ancestor) is removed.
    // Keeping a detached loading heading active would conceal focus regressions.
    for (const child of this.children) {
      for (let node = document.activeElement; node; node = node.parentNode) {
        if (node === child) { document.activeElement = null; break; }
      }
      child.parentNode = null;
    }
    this.children = []; this.append(...children);
  }
  insertBefore(child, before) { child.parentNode = this; this.children.splice(this.children.indexOf(before), 0, child); }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  focus() { document.activeElement = this; }
  addEventListener(name, callback) { const listeners = this.listeners.get(name) || []; listeners.push(callback); this.listeners.set(name, listeners); }
}
const document = { createElement: tag => new Element(tag), createTextNode: text => new Element('#text', text) };
const descendants = element => [element, ...element.children.flatMap(descendants)];
const visible = element => !element.hidden && (!element.parentNode || visible(element.parentNode));
const classElement = (container, className) => {
  const found = descendants(container).find(element => (element.className || '').split(/\s+/).includes(className));
  assert.ok(found, `Missing element class: ${className}`); return found;
};
const visibleButtons = container => descendants(container).filter(element => element.tagName === 'BUTTON' && visible(element));
const visibleButton = (container, text) => {
  const found = visibleButtons(container).find(element => element.textContent === text);
  assert.ok(found, `Missing visible button: ${text}`); return found;
};
const button = (container, text) => {
  const found = descendants(container).find(element => element.tagName === 'BUTTON' && element.textContent === text);
  assert.ok(found, `Missing button: ${text}`); return found;
};
const input = (container, label) => {
  const found = descendants(container).find(element => element.tagName === 'LABEL' && element._text === label);
  assert.ok(found, `Missing field: ${label}`); return found.children[0];
};
const check = (container, labelStart) => {
  const found = descendants(container).find(element => element.tagName === 'LABEL' && element.textContent.startsWith(labelStart));
  assert.ok(found, `Missing checkbox: ${labelStart}`); return found.children[0];
};
async function dispatch(element, eventName) {
  if (element.disabled && eventName === 'click') return;
  for (const listener of element.listeners.get(eventName) || []) await listener({ preventDefault() {} });
  await pause();
}

async function inspectLocalWizard(container) {
  await dispatch(button(container, 'Add machine'), 'click');
  input(container, 'Display name').value = 'Example local';
  input(container, 'Allowed parent project folders (absolute paths on that machine, one per line)').value = '/work/projects';
  input(container, 'Private dashboard control folder on that machine').value = '/private/control';
  const ack = check(container, 'Run a bounded read-only inspection'); ack.checked = true;
  await dispatch(ack, 'change');
  await dispatch(descendants(container).find(element => element.tagName === 'FORM'), 'submit');
}

test('wizard hook approval shows exact host execution warning and saves only after separate final trust', async () => {
  for (const optIn of [false, true]) {
    const container = new Element('main'), calls = [];
    await mountConnectionsPanel(container, { document, api: async (path, options) => {
      calls.push([path, options]);
      return path.endsWith('/prepare') ? localOutcome() : snapshot({ revision: options ? 4 : 3 });
    } });
    await inspectLocalWizard(container);
    const hooks = check(container, 'I reviewed these exact installed hooks');
    assert.equal(hooks.checked, false); assert.equal(calls.length, 2);
    assert.match(container.textContent, /startup and cleanup hooks execute on this computer as your OS account, outside the container/);
    const hookDetails = descendants(container).find(node => node.tagName === 'DETAILS' && node.textContent.includes('Installed Botainer hooks'));
    assert.equal(hookDetails.open, true);
    for (const [path, hash] of Object.entries(localHooks())) {
      assert.ok(hookDetails.textContent.includes(path)); assert.ok(hookDetails.textContent.includes(`SHA-256: ${hash}`));
    }
    const save = button(container, 'Save for restart'), trust = check(container, 'I reviewed this installation');
    trust.checked = true; await dispatch(trust, 'change');
    hooks.checked = true; await dispatch(hooks, 'change');
    assert.equal(trust.checked, false); assert.equal(save.disabled, true, 'new host hook approval needs final profile review');
    assert.equal(calls.length, 2, 'hook selection never launches or writes');
    if (!optIn) { hooks.checked = false; await dispatch(hooks, 'change'); }
    trust.checked = true; await dispatch(trust, 'change'); await dispatch(save, 'click');
    assert.equal(calls.length, 3);
    assert.equal(calls[2][0], '/api/connections');
    assert.deepEqual(calls[2][1].body.profile.approved_hooks, optIn ? localHooks() : {});
    assert.equal(calls[2][1].body.trusted, true);
    assert.ok(calls.every(([path]) => path.startsWith('/api/connections')));
  }
});

test('hook review is inert text and a detached approval cannot modify a new inspection', async () => {
  const container = new Element('main'), calls = [], hostile = '/opt/source/plugins/<img src=x>/hooks/run.py';
  const prepared = localOutcome();
  prepared.hook_candidates[hostile] = 'c'.repeat(64);
  prepared.profile.source_hashes['plugins/<img src=x>/hooks/run.py'] = 'c'.repeat(64);
  await mountConnectionsPanel(container, { document, api: async (path, options) => {
    calls.push([path, options]); return path.endsWith('/prepare') ? prepared : snapshot({ revision: options ? 4 : 3 });
  } });
  await inspectLocalWizard(container);
  const oldHooks = check(container, 'I reviewed these exact installed hooks');
  const oldTrust = check(container, 'I reviewed this installation'), oldSave = button(container, 'Save for restart');
  assert.ok(container.textContent.includes(hostile));
  assert.equal(descendants(container).some(node => node.tagName === 'IMG'), false);
  const name = input(container, 'Display name'); name.value = 'Changed local'; await dispatch(name, 'input');
  await dispatch(descendants(container).find(element => element.tagName === 'FORM'), 'submit');
  const hooks = check(container, 'I reviewed these exact installed hooks');
  assert.notEqual(hooks, oldHooks); assert.equal(hooks.checked, false);
  oldHooks.checked = true; await dispatch(oldHooks, 'change');
  oldTrust.checked = true; await dispatch(oldTrust, 'change');
  await dispatch(oldSave, 'click'); assert.equal(calls.length, 3);
  const trust = check(container, 'I reviewed this installation');
  trust.checked = true; await dispatch(trust, 'change'); await dispatch(button(container, 'Save for restart'), 'click');
  assert.deepEqual(calls[3][1].body.profile.approved_hooks, {});
});

test('refreshing a changed saved revision clears hook confirmation and requires a new inspection', async () => {
  const container = new Element('main'), calls = []; let revision = 3;
  await mountConnectionsPanel(container, { document, api: async (path, options) => {
    calls.push([path, options]); return path.endsWith('/prepare') ? localOutcome() : snapshot({ revision });
  } });
  await inspectLocalWizard(container);
  const hooks = check(container, 'I reviewed these exact installed hooks'); hooks.checked = true; await dispatch(hooks, 'change');
  const trust = check(container, 'I reviewed this installation'); trust.checked = true; await dispatch(trust, 'change');
  const save = button(container, 'Save for restart'); revision = 4;
  await dispatch(button(container, 'Refresh saved machines'), 'click');
  assert.equal(hooks.checked, false); assert.equal(hooks.disabled, true);
  assert.equal(trust.checked, false); assert.equal(save.disabled, true);
  await dispatch(save, 'click'); assert.equal(calls.length, 3);
});

test('host update form inspects installed files and saves only after explicit review, with no agent launch', async () => {
  const container = new Element('main'), calls = [], host = hostEntry();
  await mountConnectionsPanel(container, { document, api: async (path, options) => {
    calls.push([path, options]);
    if (path.endsWith('/prepare')) return agentUpdate();
    return snapshot({ revision: options ? 4 : 3, entries: [host], pendingRestart: Boolean(options) });
  } });
  assert.match(container.textContent, /Claude \+ Codex · no container/);
  assert.match(container.textContent, /Codex: update review needed/);
  await dispatch(visibleButton(container, 'Review agent update…'), 'click');
  assert.match(classElement(classElement(container, 'connection-workspace'), 'host-warning').textContent, /HOST ACCESS/);
  assert.match(container.textContent, /preserves this connection’s projects, history and browser pairing/);
  assert.match(container.textContent, /does not install software or run the agent/);
  assert.equal(input(container, 'Agent to update').value, 'codex');
  const form = descendants(container).find(element => element.tagName === 'FORM');
  await dispatch(form, 'submit');
  assert.equal(calls.length, 2);
  assert.deepEqual(calls[1][1].body, { revision: 3, agent: 'codex', path: '' });
  assert.match(container.textContent, /Current executable\/opt\/agents\/old\/codex/);
  assert.match(container.textContent, /Installed executable\/opt\/agents\/current\/codex/);
  const save = visibleButton(container, 'Save update for restart');
  assert.equal(save.disabled, true); await dispatch(save, 'click'); assert.equal(calls.length, 2);
  const trust = check(container, 'I reviewed this executable'); trust.checked = true; await dispatch(trust, 'change');
  await dispatch(save, 'click');
  assert.equal(calls.length, 3);
  assert.deepEqual(calls[2], ['/api/connections/host-native/agent-update', { method: 'POST', body: {
    revision: 3, agent: 'codex', path: '/opt/agents/current/codex', sha256: 'b'.repeat(64), confirmed: true } }]);
  assert.equal(visible(classElement(container, 'machine-overview')), true);
  assert.equal(classElement(container, 'connection-workspace').children.length, 0);
});

test('host update review uses text nodes for paths, notices and agent labels', async () => {
  const container = new Element('main'), hostile = '<img src=x onerror=alert(1)>', host = hostEntry();
  host.agents[1].label = hostile;
  await mountConnectionsPanel(container, { document, api: async path => path.endsWith('/prepare')
    ? agentUpdate({ current: { path: '/old/' + hostile, sha256: 'a'.repeat(64) },
      candidate: { path: '/installed/' + hostile, sha256: 'b'.repeat(64) }, notice: hostile })
    : snapshot({ entries: [host] }) });
  await dispatch(visibleButton(container, 'Review agent update…'), 'click');
  await dispatch(descendants(container).find(element => element.tagName === 'FORM'), 'submit');
  assert.match(container.textContent, /<img src=x onerror=alert\(1\)>/);
  assert.equal(descendants(container).some(element => ['IMG', 'SCRIPT'].includes(element.tagName)), false);
  assert.equal(descendants(container).some(element => element.getAttribute('onerror')), false);
});

test('editing either host agent selection or executable path discards reviewed trust and requires fresh inspection', async () => {
  for (const edit of ['agent', 'path']) {
    const container = new Element('main'), calls = [];
    await mountConnectionsPanel(container, { document, api: async (path, options) => {
      calls.push([path, options]); return path.endsWith('/prepare') ? agentUpdate({ agent: options.body.agent })
        : snapshot({ entries: [hostEntry()] });
    } });
    await dispatch(visibleButton(container, 'Review agent update…'), 'click');
    const form = descendants(container).find(element => element.tagName === 'FORM');
    await dispatch(form, 'submit');
    const trust = check(container, 'I reviewed this executable'); trust.checked = true; await dispatch(trust, 'change');
    const field = input(container, edit === 'agent' ? 'Agent to update' : 'Installed executable (optional absolute path)');
    field.value = edit === 'agent' ? 'claude' : '/opt/another/codex';
    await dispatch(field, edit === 'agent' ? 'change' : 'input');
    assert.equal(visibleButtons(container).some(element => element.textContent === 'Save update for restart'), false);
    assert.equal(calls.length, 2);
    await dispatch(form, 'submit');
    assert.equal(check(container, 'I reviewed this executable').checked, false);
    assert.equal(visibleButton(container, 'Save update for restart').disabled, true);
    assert.equal(calls.length, 3); assert.ok(calls.slice(1).every(([path]) => path.endsWith('/prepare')));
  }
});

test('leaving a host update inspection rejects late responses and late errors without changing the list', async () => {
  for (const fail of [false, true]) {
    const container = new Element('main'), pending = deferred(), calls = [];
    await mountConnectionsPanel(container, { document, api: async (path, options) => {
      calls.push([path, options]); return path.endsWith('/prepare') ? pending.promise : snapshot({ entries: [hostEntry()] });
    } });
    await dispatch(visibleButton(container, 'Review agent update…'), 'click');
    await dispatch(descendants(container).find(element => element.tagName === 'FORM'), 'submit');
    await dispatch(visibleButton(container, '← Back to machines'), 'click');
    const before = classElement(container, 'machines-page').textContent;
    if (fail) pending.reject(new Error('Abandoned update inspection'));
    else pending.resolve(agentUpdate());
    await pause(); await pause();
    assert.equal(classElement(container, 'machines-page').textContent, before);
    assert.equal(classElement(container, 'connection-workspace').children.length, 0);
    assert.equal(calls.length, 2);
  }
});

test('unchanged installed host executable needs no trust checkbox or write', async () => {
  const container = new Element('main'), calls = [];
  await mountConnectionsPanel(container, { document, api: async (path, options) => {
    calls.push([path, options]); return path.endsWith('/prepare') ? agentUpdate({ changed: false })
      : snapshot({ entries: [hostEntry()] });
  } });
  await dispatch(visibleButton(container, 'Review agent update…'), 'click');
  await dispatch(descendants(container).find(element => element.tagName === 'FORM'), 'submit');
  assert.match(container.textContent, /Already using this executable/);
  assert.match(container.textContent, /No update needs saving/);
  assert.equal(descendants(classElement(container, 'connection-workspace')).some(element => element.type === 'checkbox'), false);
  assert.equal(calls.length, 2);
});

test('a rejected host update save clears confirmation and offers fresh inspection without automatic retry', async () => {
  const container = new Element('main'), calls = [];
  await mountConnectionsPanel(container, { document, api: async (path, options) => {
    calls.push([path, options]);
    if (path.endsWith('/prepare')) return agentUpdate();
    if (path.endsWith('/agent-update')) throw new Error('Connections changed in another window; refresh and review.');
    return snapshot({ entries: [hostEntry()] });
  } });
  await dispatch(visibleButton(container, 'Review agent update…'), 'click');
  await dispatch(descendants(container).find(element => element.tagName === 'FORM'), 'submit');
  const trust = check(container, 'I reviewed this executable'); trust.checked = true; await dispatch(trust, 'change');
  await dispatch(visibleButton(container, 'Save update for restart'), 'click');
  assert.match(container.textContent, /Connections changed in another window/);
  assert.match(container.textContent, /Refresh saved machines and inspect again/);
  assert.equal(visibleButtons(container).some(element => element.textContent === 'Save update for restart'), false);
  assert.equal(calls.length, 3); await pause(); assert.equal(calls.length, 3);
  await dispatch(visibleButton(container, '← Back to machines'), 'click');
  await dispatch(visibleButton(container, 'Refresh saved machines'), 'click');
  assert.equal(calls.length, 4); assert.equal(calls[3][0], '/api/connections');
});

test('editing a host update path during save preserves the draft and uses the new revision for fresh review', async () => {
  const container = new Element('main'), pending = deferred(), calls = [];
  await mountConnectionsPanel(container, { document, api: async (path, options) => {
    calls.push([path, options]);
    if (path.endsWith('/prepare')) return agentUpdate({ revision: options.body.revision });
    if (path.endsWith('/agent-update')) return pending.promise;
    return snapshot({ entries: [hostEntry()] });
  } });
  await dispatch(visibleButton(container, 'Review agent update…'), 'click');
  const form = descendants(container).find(element => element.tagName === 'FORM');
  await dispatch(form, 'submit');
  const trust = check(container, 'I reviewed this executable'); trust.checked = true; await dispatch(trust, 'change');
  await dispatch(visibleButton(container, 'Save update for restart'), 'click');
  const path = input(container, 'Installed executable (optional absolute path)');
  path.value = '/opt/agents/another/codex'; await dispatch(path, 'input');
  pending.resolve(snapshot({ revision: 4, entries: [hostEntry()], pendingRestart: true })); await pause(); await pause();
  assert.equal(input(container, 'Installed executable (optional absolute path)'), path);
  assert.equal(path.value, '/opt/agents/another/codex');
  assert.match(container.textContent, /newer draft has not been saved/);
  assert.equal(visibleButtons(container).some(element => element.textContent === 'Save update for restart'), false);
  await dispatch(form, 'submit');
  assert.equal(calls.at(-1)[1].body.revision, 4);
  assert.equal(calls.at(-1)[1].body.path, '/opt/agents/another/codex');
  assert.equal(check(container, 'I reviewed this executable').checked, false);
  assert.equal(visibleButton(container, 'Save update for restart').disabled, true);
});

test('mounted removal asks for confirmation and preserves a visibly loaded active machine', async () => {
  const container = new Element('main'), calls = [], updates = [], active = entry();
  await mountConnectionsPanel(container, { document, onConnectionsChange: result => updates.push(result), api: async (path, options) => {
    calls.push([path, options]);
    if (!options) return snapshot({ entries: [active], active: [active] });
    assert.equal(path, '/api/connections/cluster/remove');
    assert.deepEqual(options.body, { revision: 3, confirmed: true });
    return snapshot({ revision: 4, entries: [], active: [active], pendingRestart: true });
  } });
  assert.equal(updates.length, 1); assert.equal(updates[0].revision, 3);
  await dispatch(button(container, 'Remove…'), 'click');
  assert.equal(calls.length, 1); assert.match(container.textContent, /does not stop sessions, cancel jobs/);
  await dispatch(button(container, 'Cancel'), 'click'); assert.equal(calls.length, 1);
  await dispatch(button(container, 'Remove…'), 'click');
  await dispatch(button(container, 'Remove for next restart'), 'click');
  assert.equal(calls.length, 2);
  assert.equal(updates.length, 2); assert.equal(updates[1].revision, 4); assert.equal(updates[1].pendingRestart, true);
  assert.match(container.textContent, /Removal saved · still loaded until restart/);
  assert.equal(descendants(container).some(element => element.tagName === 'BUTTON' && element.textContent === 'Remove…'), false);
});

test('loaded host cards match sidebar names and identify agents while saved-only profiles explain their absence', async () => {
  const container = new Element('main'), inspected = [], active = entry({ id: 'host-current', kind: 'host', label: 'Loaded host profile' });
  const renamed = { ...active, label: 'Saved replacement name', profileDigest: 'b'.repeat(64) };
  const pending = entry({ id: 'host-next', kind: 'host', label: 'Codex profile pending' });
  await mountConnectionsPanel(container, { document,
    api: async () => snapshot({ entries: [renamed, pending], active: [active], pendingRestart: true }),
    activeMachines: () => [{ id: active.id, label: active.label, executionKind: 'host',
      availableAgents: [{ id: 'claude', label: 'An arbitrary alias' }] }],
    onInspectMachine: id => inspected.push(id),
  });
  const cards = descendants(container).filter(element => (element.className || '').split(/\s+/).includes('connection-card'));
  assert.equal(cards.length, 2);
  assert.equal(cards[0].children[0].children[0].textContent, 'Loaded host profile');
  assert.match(cards[0].textContent, /⚠ Claude · no container/);
  assert.match(cards[0].textContent, /Saved name: Saved replacement name/);
  assert.match(cards[0].textContent, /name above matches the loaded connection in the sidebar/);
  assert.doesNotMatch(cards[0].textContent, /Saved only|An arbitrary alias/);
  assert.match(cards[1].textContent, /Codex profile pending/);
  assert.match(cards[1].textContent, /⚠ Host · no container/);
  assert.doesNotMatch(cards[1].textContent, /⚠ Codex · no container/);
  assert.match(cards[1].textContent, /Saved only · not available for projects or sessions yet\. Restart the dashboard service to load this profile; reloading the browser is not enough\./);
  assert.equal(visibleButtons(cards[1]).some(item => item.textContent.startsWith('Details')), false);
  await dispatch(visibleButton(cards[0], 'Details & agent help'), 'click');
  assert.deepEqual(inspected, [active.id]);
});

test('loaded metadata refresh names agents without fetching, notifying, changing a draft or stealing focus', async () => {
  const container = new Element('main'), calls = [], updates = [];
  const active = entry({ id: 'host-current', kind: 'host', label: 'Work host' });
  let machines = [{ id: active.id, label: active.label, executionKind: 'host', availableAgents: [] }];
  const panel = await mountConnectionsPanel(container, { document,
    api: async (...args) => { calls.push(args); return snapshot({ entries: [active], active: [active] }); },
    activeMachines: () => machines, onConnectionsChange: result => updates.push(result),
  });
  assert.match(classElement(container, 'connection-card').textContent, /⚠ Host · no container/);
  await dispatch(visibleButton(container, 'Import prepared profile'), 'click');
  const raw = input(container, 'Prepared profile JSON'); raw.value = '{ "unfinished":'; raw.focus();
  machines = [{ ...machines[0], availableAgents: [{ id: 'claude' }] }];
  panel.refreshActiveMachines();
  assert.match(classElement(container, 'connection-card').textContent, /⚠ Claude · no container/);
  assert.equal(input(container, 'Prepared profile JSON'), raw);
  assert.equal(raw.value, '{ "unfinished":'); assert.equal(document.activeElement, raw);
  assert.equal(visible(classElement(container, 'connection-workspace')), true);
  assert.equal(visible(classElement(container, 'machine-overview')), false);
  assert.equal(calls.length, 1); assert.equal(updates.length, 1);
  const card = classElement(container, 'connection-card');
  panel.refreshActiveMachines();
  assert.equal(classElement(container, 'connection-card'), card);
  assert.equal(calls.length, 1); assert.equal(updates.length, 1);
});

test('loaded metadata refresh defers rebuilding a focused connection action until focus leaves', async () => {
  const container = new Element('main'), active = entry({ id: 'host-current', kind: 'host', label: 'Work host' });
  let machines = [{ id: active.id, label: active.label, executionKind: 'host', availableAgents: [] }];
  const panel = await mountConnectionsPanel(container, { document,
    api: async () => snapshot({ entries: [active], active: [active] }), activeMachines: () => machines,
    onInspectMachine: () => {},
  });
  const details = visibleButton(container, 'Details & agent help'); details.focus();
  machines = [{ ...machines[0], availableAgents: [{ id: 'codex' }] }];
  panel.refreshActiveMachines();
  assert.equal(visibleButton(container, 'Details & agent help'), details);
  assert.equal(document.activeElement, details);
  assert.match(classElement(container, 'connection-card').textContent, /⚠ Host · no container/);
  visibleButton(container, 'Add machine').focus(); panel.refreshActiveMachines();
  assert.match(classElement(container, 'connection-card').textContent, /⚠ Codex · no container/);
  assert.equal(document.activeElement, visibleButton(container, 'Add machine'));
});

test('loaded machine cards show DNS recovery directly and refresh health without hiding recovery behind details', async () => {
  const container = new Element('main'), active = entry(), inspected = [];
  let checks = 0;
  let machines = [{ id: active.id, label: active.label, kind: 'cluster', status: 'unavailable', observationStatus: 'failed',
    clusterSettings: { sshAlias: 'research', connectionDiagnostic: { code: 'ssh-name-resolution-failed',
      message: 'SSH could not resolve the configured host.', recovery: 'Check the SSH alias, network and any required VPN. Then refresh.' } } }];
  const panel = await mountConnectionsPanel(container, { document,
    api: async () => snapshot({ entries: [active], active: [active] }), activeMachines: () => machines,
    presentMachine: workspaceConnectionPresentation, onInspectMachine: id => inspected.push(id),
    onRefreshMachines: async () => { checks++; },
  });
  let health = classElement(container, 'connection-health');
  assert.equal(visible(health), true);
  assert.match(classElement(container, 'connection-card').textContent, /Loaded in this dashboard/);
  assert.match(health.textContent, /Host name not found/);
  assert.match(health.textContent, /Next: Check the SSH alias, network and any required VPN/);
  assert.match(health.textContent, /ssh research/);
  await dispatch(visibleButton(container, 'Connection help'), 'click'); assert.deepEqual(inspected, [active.id]);
  const checkAgain = visibleButton(container, 'Check again'); checkAgain.focus();
  await dispatch(checkAgain, 'click'); assert.equal(checks, 1);
  machines = [{ ...machines[0], status: 'available', observationStatus: 'current' }];
  panel.refreshActiveMachines();
  health = classElement(container, 'connection-health');
  assert.match(health.textContent, /Connected/);
  assert.doesNotMatch(health.textContent, /Host name not found|VPN|ssh research|Next:/);
  assert.equal(document.activeElement, checkAgain);
  visibleButton(container, 'Add machine').focus(); panel.refreshActiveMachines();
  assert.equal(visibleButtons(container).some(button => button.textContent === 'Check again'), false);
  machines = [{ ...machines[0], status: 'unavailable', observationStatus: 'delayed' }]; panel.refreshActiveMachines();
  assert.match(classElement(container, 'connection-health').textContent, /Status check delayed/);
  assert.doesNotMatch(classElement(container, 'connection-health').textContent, /Host name not found|VPN|ssh research/);
});

test('machine health changes with unchanged status refresh cause and recovery using only text nodes', async () => {
  const container = new Element('main'), active = entry(), hostile = '<img src=x onerror=alert(1)>';
  let machines = [{ id: active.id, kind: 'cluster', status: 'unavailable', observationStatus: 'failed',
    clusterSettings: { connectionDiagnostic: { code: 'ssh-name-resolution-failed', message: 'DNS failed.', recovery: 'Check network.' } } }];
  const panel = await mountConnectionsPanel(container, { document,
    api: async () => snapshot({ entries: [active], active: [active] }), activeMachines: () => machines,
    presentMachine: workspaceConnectionPresentation,
  });
  machines = [{ ...machines[0], clusterSettings: { connectionDiagnostic: {
    code: 'ssh-authentication-required', message: hostile, recovery: 'Complete normal SSH sign-in.' } } }];
  panel.refreshActiveMachines();
  const health = classElement(container, 'connection-health');
  assert.match(health.textContent, /SSH sign-in needed/); assert.ok(health.textContent.includes(hostile));
  assert.match(health.textContent, /Complete normal SSH sign-in/); assert.doesNotMatch(health.textContent, /DNS failed/);
  assert.equal(descendants(health).some(element => ['IMG', 'SCRIPT'].includes(element.tagName)), false);
});

test('failed connection fetches do not announce new saved state to the sidebar', async () => {
  const container = new Element('main'), updates = [];
  await mountConnectionsPanel(container, { document, api: async () => { throw new Error('Connection settings unavailable'); },
    onConnectionsChange: result => updates.push(result),
  });
  assert.equal(updates.length, 0);
  assert.match(container.textContent, /Connection settings unavailable/);
});

test('mounted wizard suppresses a successful result after fields changed during inspection', async () => {
  const container = new Element('main'), pending = deferred(), calls = [];
  await mountConnectionsPanel(container, { document, api: async (path, options) => {
    calls.push([path, options]); return path.endsWith('/prepare') ? pending.promise : snapshot();
  } });
  await dispatch(button(container, 'Add machine'), 'click');
  const name = input(container, 'Display name'); name.value = 'Original local';
  input(container, 'Allowed parent project folders (absolute paths on that machine, one per line)').value = '/work/projects';
  input(container, 'Private dashboard control folder on that machine').value = '/private/control';
  const acknowledgement = check(container, 'Run a bounded read-only inspection');
  acknowledgement.checked = true; await dispatch(acknowledgement, 'change');
  const form = descendants(container).find(element => element.tagName === 'FORM');
  await dispatch(form, 'submit');
  assert.equal(calls.length, 2); assert.equal(calls[1][1].body.request.label, 'Original local');
  name.value = 'Changed local'; await dispatch(name, 'input');
  pending.resolve({ ...outcome('local'), kind: 'local' }); await pause(); await pause();
  assert.doesNotMatch(container.textContent, /Review before saving/);
  assert.equal(descendants(container).some(element => element.tagName === 'BUTTON' && element.textContent === 'Save for restart'), false);
});

test('mounted import renders exact remote selections and requires renewed review after editing', async () => {
  const container = new Element('main'), calls = [];
  await mountConnectionsPanel(container, { document, api: async (...args) => { calls.push(args); return snapshot(); } });
  await dispatch(button(container, 'Import prepared profile'), 'click');
  const raw = input(container, 'Prepared profile JSON'); raw.value = JSON.stringify(profile());
  await dispatch(button(container, 'Review profile'), 'click');
  const save = button(container, 'Save for restart'); assert.equal(save.disabled, true);
  const reviewDetails = descendants(container).find(element => element.tagName === 'DL');
  assert.match(reviewDetails.textContent, /Interpreter\/opt\/environment\/bin\/python/);
  assert.match(reviewDetails.textContent, /Launcher\/opt\/environment\/bin\/botainer/);
  assert.doesNotMatch(reviewDetails.textContent, /\[object Object\]/);
  const trust = check(container, 'I reviewed this installation'); trust.checked = true; await dispatch(trust, 'change');
  assert.equal(save.disabled, false);
  raw.value = JSON.stringify(profile('changed')); await dispatch(raw, 'input');
  assert.doesNotMatch(container.textContent, /Review before saving/);
  await dispatch(save, 'click'); // Even a stale detached handler cannot save.
  assert.equal(calls.length, 1);
  assert.match(container.textContent, /Review the exact prepared profile/);
});

test('mounted host-profile review presents host access warning as literal text', async () => {
  const container = new Element('main');
  await mountConnectionsPanel(container, { document, api: async () => snapshot() });
  await dispatch(button(container, 'Import prepared profile'), 'click');
  input(container, 'Profile type').value = 'host';
  input(container, 'Prepared profile JSON').value = JSON.stringify({ id: 'host', label: '<script>not executable</script>' });
  await dispatch(button(container, 'Review profile'), 'click');
  assert.match(container.textContent, /HOST ACCESS: agents run as your OS account, outside containers/);
  assert.match(container.textContent, /<script>not executable<\/script>/);
  assert.equal(descendants(container).some(element => element.tagName === 'SCRIPT'), false);
});

const guide = id => ({ title: `Guide ${id}`, text: `Instructions for ${id}.` });
const guideApi = async path => path.startsWith('/api/connections/help/')
  ? guide(path.split('/').at(-1)) : snapshot({ entries: [entry()] });

test('mounted Help renders the shipped support table by default and keeps raw Markdown out of view', async () => {
  const markdown = readFileSync(new URL('../../docs/setup-support-matrix.md', import.meta.url), 'utf8');
  const container = new Element('main');
  const controller = await mountConnectionsPanel(container, { document, api: async path =>
    path.endsWith('/help/support') ? { title: 'Tested and untested setup routes', text: markdown } : snapshot(),
  });
  await controller.openHelp('support');
  const help = classElement(container, 'help-page');
  const rendered = classElement(help, 'help-document'), raw = classElement(help, 'help-markdown-source');
  assert.equal(visible(rendered), true);
  assert.equal(visible(raw), false);
  assert.equal(descendants(rendered).filter(node => node.tagName === 'TABLE').length, 1);
  assert.deepEqual(descendants(rendered).filter(node => node.tagName === 'TH').map(node => node.textContent), ['Route', 'Current scope']);
  assert.ok(descendants(rendered).some(node => node.tagName === 'TD' && node.textContent.includes('SSH/Slurm')));
  assert.equal(descendants(rendered).some(node => /^H[1-6]$/.test(node.tagName) && node.textContent === 'Setup and platform support'), false);
  assert.equal(raw.textContent, markdown);
  assert.equal(visibleButton(help, 'Markdown source').getAttribute('aria-pressed'), 'false');
});

test('the shipped installation status link and Help topic open the authenticated alpha limits guide', async () => {
  const installation = readFileSync(new URL('../../docs/installation.md', import.meta.url), 'utf8');
  const status = readFileSync(new URL('../../docs/status.md', import.meta.url), 'utf8');
  const requests = [], container = new Element('main');
  const controller = await mountConnectionsPanel(container, { document, api: async path => {
    requests.push(path);
    if (path === '/api/connections/help/installation') return { title: 'Installation', text: installation };
    if (path === '/api/connections/help/status') return { title: 'Alpha status and limits', text: status };
    assert.equal(path, '/api/connections');
    return snapshot();
  } });
  await controller.openHelp('installation');
  const help = classElement(container, 'help-page'), topics = classElement(help, 'help-topics');
  await dispatch(visibleButton(classElement(help, 'help-document'), 'status'), 'click');
  await pause();
  assert.equal(requests.filter(path => path === '/api/connections/help/status').length, 1);
  assert.equal(visibleButton(topics, 'Alpha status & limits').getAttribute('aria-current'), 'page');
  assert.equal(classElement(help, 'help-markdown-source').textContent, status);
  assert.equal(visible(classElement(help, 'help-markdown-source')), false);
  assert.ok(descendants(help).some(node => node.tagName === 'H3' && node.textContent === 'Alpha status and limits'));
  await controller.openHelp('installation');
  await dispatch(visibleButton(topics, 'Alpha status & limits'), 'click');
  await pause();
  assert.equal(requests.filter(path => path === '/api/connections/help/status').length, 2);
  assert.equal(classElement(help, 'help-markdown-source').textContent, status);
});

test('mounted Markdown source toggles the same inert text without fetching or changing the selected guide', async () => {
  const markdown = '# Guide\n\n| Meaning | Value |\n| --- | --- |\n| Preview | **Readable** |\n\n<script>never execute</script>\n\n![Remote image](https://images.example.invalid/tracker)';
  const container = new Element('main'), calls = [];
  const controller = await mountConnectionsPanel(container, { document, api: async path => {
    calls.push(path);
    return path.endsWith('/help/ssh') ? { title: 'SSH guide', text: markdown } : snapshot();
  } });
  await controller.openHelp('ssh');
  const help = classElement(container, 'help-page'), topics = classElement(help, 'help-topics');
  const rendered = classElement(help, 'help-document'), raw = classElement(help, 'help-markdown-source');
  const requests = [...calls], toggle = visibleButton(help, 'Markdown source');
  await dispatch(toggle, 'click');
  assert.equal(visible(rendered), false); assert.equal(visible(raw), true);
  assert.equal(toggle.textContent, 'Rendered guide'); assert.equal(toggle.getAttribute('aria-pressed'), 'true');
  assert.equal(raw.textContent, markdown);
  assert.deepEqual(calls, requests);
  assert.equal(descendants(help).some(node => ['SCRIPT', 'IMG', 'IFRAME'].includes(node.tagName)), false);
  await dispatch(toggle, 'click');
  assert.equal(visible(rendered), true); assert.equal(visible(raw), false);
  assert.equal(classElement(help, 'help-document'), rendered);
  assert.equal(classElement(help, 'help-markdown-source'), raw);
  assert.equal(toggle.textContent, 'Markdown source'); assert.equal(toggle.getAttribute('aria-pressed'), 'false');
  assert.equal(visibleButton(topics, 'SSH setup').getAttribute('aria-current'), 'page');
  assert.deepEqual(calls, requests);
});

test('a mounted cross-guide link loads its topic and focuses the requested section after the response', async () => {
  const container = new Element('main'), requests = [], pages = [], ssh = deferred();
  const controller = await mountConnectionsPanel(container, { document,
    onPageChange: page => pages.push(page), api: async path => {
      requests.push(path);
      if (path.endsWith('/help/getting-started')) return { title: 'Getting started',
        text: '# Getting started\n\n[Check the connection](ssh-setup.md#first-login-then-dashboard-test)' };
      if (path.endsWith('/help/ssh')) return ssh.promise;
      return snapshot();
    },
  });
  await controller.openHelp('getting-started');
  const help = classElement(container, 'help-page'), topics = classElement(help, 'help-topics');
  await dispatch(visibleButton(help, 'Check the connection'), 'click');
  assert.equal(visible(help), true);
  assert.equal(visible(classElement(container, 'machines-page')), false);
  assert.match(help.textContent, /Loading guide/);
  assert.equal(visibleButton(topics, 'SSH setup').getAttribute('aria-current'), 'page');
  assert.equal(requests.filter(path => path === '/api/connections/help/ssh').length, 1);
  assert.equal(pages.at(-1), 'help');
  ssh.resolve({ title: 'SSH setup and troubleshooting',
    text: '# Set up SSH\n\nIntroduction.\n\n## First login, then dashboard test\n\nUse your existing connection.' });
  await pause(); await pause();
  const destination = descendants(help).find(node => /^H[1-6]$/.test(node.tagName) && node.textContent === 'First login, then dashboard test');
  assert.ok(destination);
  assert.equal(document.activeElement, destination);
  assert.equal(destination.tabIndex, -1);
  assert.equal(visibleButton(topics, 'SSH setup').getAttribute('aria-current'), 'page');
  assert.equal(visibleButton(topics, 'Getting started').getAttribute('aria-current'), 'false');
  assert.equal(classElement(help, 'help-article').getAttribute('aria-busy'), 'false');
});

test('replacing the focused loading indicator moves focus to the loaded guide title', async () => {
  const container = new Element('main'), pending = deferred();
  const controller = await mountConnectionsPanel(container, { document, api: async path =>
    path.endsWith('/help/ssh') ? pending.promise : snapshot(),
  });
  const loading = controller.openHelp('ssh');
  await pause();
  assert.equal(document.activeElement.textContent, 'Loading guide…');
  pending.resolve({ title: 'SSH setup title', text: '# SSH guide\n\nInstructions.' });
  await loading;
  const header = classElement(container, 'help-article-header');
  const title = header.children.find(node => node.tagName === 'H3');
  assert.equal(title.textContent, 'SSH setup title');
  assert.equal(document.activeElement, title);
});

test('a guide response does not reclaim focus after the reader moves to a different control', async () => {
  const container = new Element('main'), pending = deferred();
  const controller = await mountConnectionsPanel(container, { document, api: async path =>
    path.endsWith('/help/ssh') ? pending.promise : snapshot(),
  });
  const loading = controller.openHelp('ssh', true, 'checks');
  await pause();
  const elsewhere = visibleButton(container, '← Back to machines');
  elsewhere.focus();
  pending.resolve({ title: 'SSH setup title', text: '# SSH guide\n\n## Checks\n\nInstructions.' });
  await loading;
  assert.equal(document.activeElement, elsewhere);
  assert.match(classElement(container, 'help-document').textContent, /Checks/);
});

test('an anchored guide response cannot focus a Help pane hidden while the request was pending', async () => {
  const container = new Element('main'), pending = deferred();
  let paneVisible = true;
  const controller = await mountConnectionsPanel(container, { document, isVisible: () => paneVisible,
    api: async path => path.endsWith('/help/ssh') ? pending.promise : snapshot(),
  });
  const loading = controller.openHelp('ssh', true, 'checks');
  await pause();
  assert.equal(document.activeElement.textContent, 'Loading guide…');
  paneVisible = false;
  pending.resolve({ title: 'SSH setup title', text: '# SSH guide\n\n## Checks\n\nInstructions.' });
  await loading;
  assert.equal(document.activeElement, null);
  assert.match(classElement(container, 'help-document').textContent, /Checks/);
});

test('machine add and import each replace the overview, and Back clears the form', async () => {
  const container = new Element('main');
  await mountConnectionsPanel(container, { document, api: guideApi });
  const machines = classElement(container, 'machines-page'), help = classElement(container, 'help-page');
  const overview = classElement(machines, 'machine-overview');
  assert.equal(visible(machines), true); assert.equal(visible(help), false);
  assert.equal(visible(overview), true);
  for (const label of ['Add machine', 'Import prepared profile']) {
    await dispatch(visibleButton(container, label), 'click');
    const workspace = classElement(machines, 'connection-workspace');
    assert.equal(visible(overview), false); assert.equal(visible(workspace), true);
    assert.ok(workspace.children.length > 0);
    assert.equal(visibleButtons(container).some(element => ['Add machine', 'Import prepared profile',
      'Refresh saved machines', 'Disable…', 'Remove…'].includes(element.textContent)), false);
    await dispatch(visibleButton(container, '← Back to machines'), 'click');
    assert.equal(visible(overview), true);
    assert.equal(workspace.children.length, 0);
    assert.equal(descendants(container).some(element => element.tagName === 'FORM'), false);
  }
});

test('Help has persistent topic navigation and hides all machine actions', async () => {
  const container = new Element('main'), pages = [];
  const controller = await mountConnectionsPanel(container, {
    document, api: guideApi, initialPage: 'help', onPageChange: page => pages.push(page),
  });
  if (controller.ready) await controller.ready;
  await pause();
  const machines = classElement(container, 'machines-page'), help = classElement(container, 'help-page');
  assert.equal(visible(machines), false); assert.equal(visible(help), true);
  const topics = classElement(help, 'help-topics');
  for (const label of ['Getting started', 'SSH setup', 'Setup agent guide', 'Support & tested routes']) {
    assert.ok(visibleButton(topics, label));
  }
  await controller.openHelp('ssh'); await pause();
  assert.equal(classElement(help, 'help-topics'), topics);
  assert.equal(visibleButton(topics, 'SSH setup').getAttribute('aria-current'), 'page');
  assert.equal(visibleButtons(topics).filter(element => element.getAttribute('aria-current') === 'page').length, 1);
  assert.match(help.textContent, /Instructions for ssh/);
  assert.equal(visibleButtons(container).some(element => ['Add machine', 'Import prepared profile',
    'Refresh saved machines', 'Disable…', 'Remove…'].includes(element.textContent)), false);
  await dispatch(visibleButton(help, '← Back to machines'), 'click');
  assert.equal(visible(machines), true); assert.equal(visible(help), false);
  assert.equal(pages.at(-1), 'machines');
});

test('a reviewed import survives Help and saves the exact retained candidate before returning to the list', async () => {
  const container = new Element('main'), calls = [];
  const controller = await mountConnectionsPanel(container, { document, api: async (path, options) => {
    calls.push([path, options]);
    if (path.startsWith('/api/connections/help/')) return guide('ssh');
    return snapshot(options ? { revision: 4, entries: [entry()], pendingRestart: true } : {});
  } });
  await dispatch(visibleButton(container, 'Import prepared profile'), 'click');
  const raw = input(container, 'Prepared profile JSON'); raw.value = JSON.stringify(profile('retained'));
  await dispatch(visibleButton(container, 'Review profile'), 'click');
  const trust = check(container, 'I reviewed this installation'); trust.checked = true;
  await dispatch(trust, 'change');
  const save = visibleButton(container, 'Save for restart');
  await controller.openHelp('ssh'); await pause();
  assert.equal(visible(save), false);
  await dispatch(visibleButton(container, '← Back to setup'), 'click');
  assert.equal(input(container, 'Prepared profile JSON'), raw);
  assert.equal(raw.value, JSON.stringify(profile('retained')));
  assert.equal(check(container, 'I reviewed this installation'), trust);
  assert.equal(trust.checked, true); assert.equal(save.disabled, false); assert.equal(visible(save), true);
  assert.equal(visible(classElement(container, 'machine-overview')), false);
  await dispatch(save, 'click');
  const writes = calls.filter(([, options]) => options?.method === 'POST');
  assert.equal(writes.length, 1);
  assert.deepEqual(writes[0], ['/api/connections', { method: 'POST',
    body: { kind: 'remote', profile: profile('retained'), revision: 3, trusted: true } }]);
  assert.equal(classElement(container, 'connection-workspace').children.length, 0);
  assert.equal(visible(classElement(container, 'machine-overview')), true);
});

test('Help and page switching retain wizard values and acknowledgement without checking a machine', async () => {
  const container = new Element('main'), calls = [];
  const controller = await mountConnectionsPanel(container, { document, api: async (...args) => {
    calls.push(args); return guideApi(...args);
  } });
  await dispatch(visibleButton(container, 'Add machine'), 'click');
  const name = input(container, 'Display name'); name.value = 'Unfinished setup'; await dispatch(name, 'input');
  const roots = input(container, 'Allowed parent project folders (absolute paths on that machine, one per line)');
  roots.value = '/work/first\n/work/second'; await dispatch(roots, 'input');
  const acknowledgement = check(container, 'Run a bounded read-only inspection');
  acknowledgement.checked = true; await dispatch(acknowledgement, 'change');
  await controller.openHelp('ssh'); await pause();
  assert.equal(visible(name), false);
  assert.equal(visibleButtons(container).some(element => element.textContent === 'Check connection'), false);
  await controller.showPage('machines');
  assert.equal(input(container, 'Display name'), name); assert.equal(name.value, 'Unfinished setup');
  assert.equal(roots.value, '/work/first\n/work/second'); assert.equal(acknowledgement.checked, true);
  assert.equal(visible(name), true); assert.equal(visible(classElement(container, 'machine-overview')), false);
  assert.equal(calls.some(([path]) => path.endsWith('/prepare')), false);
  await controller.showPage('help'); await pause();
  assert.ok(visibleButton(container, '← Back to setup'));
  await dispatch(visibleButton(container, '← Back to setup'), 'click');
  assert.equal(name.value, 'Unfinished setup');
});

test('only the most recently selected help article can replace content or selection', async () => {
  const container = new Element('main'), old = deferred(), recent = deferred();
  const controller = await mountConnectionsPanel(container, { document, api: async path => {
    if (path.endsWith('/help/ssh')) return old.promise;
    if (path.endsWith('/help/agent')) return recent.promise;
    return snapshot();
  } });
  const first = controller.openHelp('ssh'); await pause();
  const second = controller.openHelp('agent'); await pause();
  recent.resolve(guide('agent')); await second; await pause();
  const help = classElement(container, 'help-page'), topics = classElement(help, 'help-topics');
  assert.match(help.textContent, /Instructions for agent/);
  old.resolve(guide('ssh')); await first; await pause();
  assert.match(help.textContent, /Instructions for agent/); assert.doesNotMatch(help.textContent, /Instructions for ssh/);
  assert.equal(visibleButton(topics, 'Setup agent guide').getAttribute('aria-current'), 'page');
  assert.equal(visibleButtons(topics).filter(element => element.getAttribute('aria-current') === 'page').length, 1);
});

test('a stale help failure cannot replace the current article with an error', async () => {
  const container = new Element('main'), old = deferred();
  const controller = await mountConnectionsPanel(container, { document, api: async path => {
    if (path.endsWith('/help/ssh')) return old.promise;
    if (path.endsWith('/help/support')) return guide('support');
    return snapshot();
  } });
  const first = controller.openHelp('ssh'); await pause();
  await controller.openHelp('support'); await pause();
  old.reject(new Error('Superseded SSH guide error')); await first; await pause();
  const help = classElement(container, 'help-page');
  assert.match(help.textContent, /Instructions for support/);
  assert.doesNotMatch(container.textContent, /Superseded SSH guide error/);
});

test('Back to machines during inspection clears the draft and rejects its late review result', async () => {
  const container = new Element('main'), pending = deferred(), calls = [];
  await mountConnectionsPanel(container, { document, api: async (path, options) => {
    calls.push([path, options]); return path.endsWith('/prepare') ? pending.promise : snapshot();
  } });
  await dispatch(visibleButton(container, 'Add machine'), 'click');
  input(container, 'Display name').value = 'Abandoned setup';
  input(container, 'Allowed parent project folders (absolute paths on that machine, one per line)').value = '/work/projects';
  input(container, 'Private dashboard control folder on that machine').value = '/private/control';
  const acknowledgement = check(container, 'Run a bounded read-only inspection');
  acknowledgement.checked = true; await dispatch(acknowledgement, 'change');
  await dispatch(descendants(container).find(element => element.tagName === 'FORM'), 'submit');
  assert.equal(calls.length, 2);
  await dispatch(visibleButton(container, '← Back to machines'), 'click');
  assert.equal(visible(classElement(container, 'machine-overview')), true);
  assert.equal(classElement(container, 'connection-workspace').children.length, 0);
  pending.resolve({ ...outcome('local'), kind: 'local' }); await pause(); await pause();
  assert.doesNotMatch(container.textContent, /Review before saving/);
  assert.equal(descendants(container).some(element => element.tagName === 'BUTTON' && element.textContent === 'Save for restart'), false);
  await dispatch(visibleButton(container, 'Add machine'), 'click');
  assert.equal(input(container, 'Display name').value, '');
  assert.equal(check(container, 'Run a bounded read-only inspection').checked, false);
  assert.equal(calls.filter(([, options]) => options?.method === 'POST').length, 1);
});

test('a save finishing while Help is open clears the saved draft without changing the article or focus', async () => {
  const container = new Element('main'), pending = deferred(), calls = [], pages = [];
  const controller = await mountConnectionsPanel(container, { document,
    onPageChange: page => pages.push(page), api: async (path, options) => {
      calls.push([path, options]);
      if (path.startsWith('/api/connections/help/')) return guide('ssh');
      return options?.method === 'POST' ? pending.promise : snapshot();
    } });
  await dispatch(button(container, 'Import prepared profile'), 'click');
  input(container, 'Prepared profile JSON').value = JSON.stringify(profile('saving'));
  await dispatch(visibleButton(container, 'Review profile'), 'click');
  const trust = check(container, 'I reviewed this installation'); trust.checked = true;
  await dispatch(trust, 'change');
  const saving = dispatch(visibleButton(container, 'Save for restart'), 'click'); await pause();
  assert.equal(calls.filter(([, options]) => options?.method === 'POST').length, 1);
  await controller.openHelp('ssh'); await pause();
  const help = classElement(container, 'help-page');
  const focused = document.activeElement, pageChanges = [...pages];
  assert.ok(visibleButton(help, '← Back to setup'));
  pending.resolve(snapshot({ revision: 4, entries: [entry({ id: 'saving' })], pendingRestart: true }));
  await saving; await pause();
  assert.equal(visible(help), true); assert.equal(visible(classElement(container, 'machines-page')), false);
  assert.match(help.textContent, /Instructions for ssh/);
  assert.equal(visibleButton(classElement(help, 'help-topics'), 'SSH setup').getAttribute('aria-current'), 'page');
  assert.equal(document.activeElement, focused);
  assert.deepEqual(pages, pageChanges);
  assert.ok(visibleButton(help, '← Back to machines'));
  assert.equal(classElement(container, 'connection-workspace').children.length, 0);
  await dispatch(visibleButton(help, '← Back to machines'), 'click');
  assert.equal(visible(classElement(container, 'machine-overview')), true);
  assert.match(classElement(container, 'machine-overview').textContent, /saving/);
  assert.equal(calls.filter(([, options]) => options?.method === 'POST').length, 1);
});

test('a canceled inspection rejection does not add an error to the machine list', async () => {
  const container = new Element('main'), pending = deferred(), calls = [];
  await mountConnectionsPanel(container, { document, api: async (path, options) => {
    calls.push([path, options]); return path.endsWith('/prepare') ? pending.promise : snapshot();
  } });
  await dispatch(visibleButton(container, 'Add machine'), 'click');
  input(container, 'Display name').value = 'Canceled check';
  input(container, 'Allowed parent project folders (absolute paths on that machine, one per line)').value = '/work/projects';
  input(container, 'Private dashboard control folder on that machine').value = '/private/control';
  const acknowledgement = check(container, 'Run a bounded read-only inspection');
  acknowledgement.checked = true; await dispatch(acknowledgement, 'change');
  await dispatch(descendants(container).find(element => element.tagName === 'FORM'), 'submit');
  assert.equal(calls.length, 2);
  await dispatch(visibleButton(container, '← Back to machines'), 'click');
  const before = classElement(container, 'machines-page').textContent;
  pending.reject(new Error('Canceled inspection failed later')); await pause(); await pause();
  assert.equal(classElement(container, 'machines-page').textContent, before);
  assert.equal(visible(classElement(container, 'machine-overview')), true);
  assert.equal(classElement(container, 'connection-workspace').children.length, 0);
  assert.equal(descendants(container).some(element => element.getAttribute('role') === 'alert'), false);
  assert.equal(calls.length, 2);
});

test('a save finishing after leaving settings updates the retained pane without navigating or taking focus', async () => {
  const container = new Element('main'), pending = deferred(), pages = [];
  let paneVisible = true;
  const controller = await mountConnectionsPanel(container, { document, isVisible: () => paneVisible,
    onPageChange: page => pages.push(page), api: async (path, options) => {
      return options?.method === 'POST' ? pending.promise : snapshot();
    } });
  await dispatch(button(container, 'Import prepared profile'), 'click');
  input(container, 'Prepared profile JSON').value = JSON.stringify(profile('background-save'));
  await dispatch(visibleButton(container, 'Review profile'), 'click');
  const trust = check(container, 'I reviewed this installation'); trust.checked = true;
  await dispatch(trust, 'change');
  const saving = dispatch(visibleButton(container, 'Save for restart'), 'click'); await pause();
  // Model the app retaining this pane while the user returns to unrelated work.
  paneVisible = false;
  const work = new Element('button', 'Selected work'); work.focus();
  const pageChanges = [...pages];
  pending.resolve(snapshot({ revision: 4, entries: [entry({ id: 'background-save' })], pendingRestart: true }));
  await saving; await pause();
  assert.equal(document.activeElement, work);
  assert.deepEqual(pages, pageChanges);
  assert.equal(classElement(container, 'connection-workspace').children.length, 0);
  assert.match(classElement(container, 'machine-overview').textContent, /background-save/);
  paneVisible = true; controller.showPage('machines'); controller.focusPage();
  assert.equal(visible(classElement(container, 'machine-overview')), true);
  assert.equal(document.activeElement.tagName, 'H2'); assert.equal(document.activeElement.textContent, 'Machines');
  assert.equal(descendants(container).some(element => element.tagName === 'BUTTON' && element.textContent === 'Save for restart'), false);
});

test('editing an import during save retains the newer draft and requires fresh review with the updated revision', async () => {
  const container = new Element('main'), pending = deferred(), writes = [];
  await mountConnectionsPanel(container, { document, api: async (path, options) => {
    if (!options?.method) return snapshot();
    writes.push([path, options]);
    return writes.length === 1 ? pending.promise : snapshot({ revision: 5, entries: [entry({ id: 'newer' })] });
  } });
  await dispatch(button(container, 'Import prepared profile'), 'click');
  const raw = input(container, 'Prepared profile JSON'); raw.value = JSON.stringify(profile('original'));
  await dispatch(visibleButton(container, 'Review profile'), 'click');
  const originalTrust = check(container, 'I reviewed this installation'); originalTrust.checked = true;
  await dispatch(originalTrust, 'change');
  const saving = dispatch(visibleButton(container, 'Save for restart'), 'click'); await pause();
  assert.equal(writes.length, 1);
  assert.equal(writes[0][1].body.profile.id, 'original'); assert.equal(writes[0][1].body.revision, 3);
  raw.value = JSON.stringify(profile('newer')); await dispatch(raw, 'input');
  pending.resolve(snapshot({ revision: 4, entries: [entry({ id: 'original' })], pendingRestart: true }));
  await saving; await pause();
  assert.equal(input(container, 'Prepared profile JSON'), raw);
  assert.equal(raw.value, JSON.stringify(profile('newer')));
  assert.equal(visible(classElement(container, 'connection-workspace')), true);
  assert.equal(visible(classElement(container, 'machine-overview')), false);
  assert.match(container.textContent, /newer draft has not been saved/);
  assert.doesNotMatch(container.textContent, /Review before saving/);
  assert.equal(descendants(container).some(element => element.tagName === 'BUTTON' && element.textContent === 'Save for restart'), false);
  assert.equal(writes.length, 1);
  await dispatch(visibleButton(container, 'Review profile'), 'click');
  const renewedSave = visibleButton(container, 'Save for restart');
  const renewedTrust = check(container, 'I reviewed this installation');
  assert.notEqual(renewedTrust, originalTrust); assert.equal(renewedTrust.checked, false); assert.equal(renewedSave.disabled, true);
  await dispatch(renewedSave, 'click'); assert.equal(writes.length, 1);
  renewedTrust.checked = true; await dispatch(renewedTrust, 'change'); await dispatch(renewedSave, 'click');
  assert.equal(writes.length, 2);
  assert.deepEqual(writes[1], ['/api/connections', { method: 'POST',
    body: { kind: 'remote', profile: profile('newer'), revision: 4, trusted: true } }]);
  assert.equal(visible(classElement(container, 'machine-overview')), true);
  assert.equal(classElement(container, 'connection-workspace').children.length, 0);
});

test('switching between local and SSH setup preserves the remote ID and SSH draft', async () => {
  const container = new Element('main'), calls = [];
  await mountConnectionsPanel(container, { document, api: async (...args) => { calls.push(args); return snapshot(); } });
  await dispatch(visibleButton(container, 'Add machine'), 'click');
  const kind = input(container, 'Where is Botainer?');
  const id = input(container, 'Connection ID (stable, no spaces)'), alias = input(container, 'Existing SSH alias');
  assert.equal(kind.value, 'local'); assert.equal(id.value, 'local'); assert.equal(id.readOnly, true);
  kind.value = 'remote'; await dispatch(kind, 'change');
  assert.equal(id.readOnly, false); assert.equal(id.value, ''); assert.equal(alias.required, true);
  id.value = 'research-east'; await dispatch(id, 'input');
  alias.value = 'existing-research'; await dispatch(alias, 'input');
  kind.value = 'local'; await dispatch(kind, 'change');
  assert.equal(id.value, 'local'); assert.equal(id.readOnly, true); assert.equal(alias.required, false);
  assert.equal(visible(alias), false);
  kind.value = 'remote'; await dispatch(kind, 'change');
  assert.equal(id.value, 'research-east'); assert.equal(id.readOnly, false);
  assert.equal(alias.value, 'existing-research'); assert.equal(alias.required, true); assert.equal(visible(alias), true);
  id.value = 'research-west'; await dispatch(id, 'input');
  kind.value = 'local'; await dispatch(kind, 'change');
  kind.value = 'remote'; await dispatch(kind, 'change');
  assert.equal(id.value, 'research-west');
  assert.equal(calls.length, 1);
});

test('startup-flag connections remain inspectable without enabling saved machine mutations', async () => {
  const container = new Element('main'), inspected = [];
  await mountConnectionsPanel(container, { document,
    api: async () => ({ ...snapshot(), enabled: false, notice: 'Saved machine setup needs --connections.' }),
    activeMachines: () => [{ id: 'existing', label: 'Existing cluster', kind: 'remote' }],
    onInspectMachine: id => inspected.push(id),
  });
  assert.match(container.textContent, /Existing cluster/);
  assert.match(container.textContent, /Loaded · managed by startup flags/);
  assert.equal(visibleButton(container, 'Add machine').disabled, true);
  assert.equal(visibleButtons(container).some(b => ['Disable…', 'Remove…'].includes(b.textContent)), false);
  await dispatch(visibleButton(container, 'Details & Botainer help'), 'click');
  assert.deepEqual(inspected, ['existing']);
});

test('a detached old host update confirmation cannot approve a freshly inspected executable', async () => {
  const container = new Element('main'), calls = [];
  await mountConnectionsPanel(container, { document, api: async (path, options) => {
    calls.push([path, options]);
    return path.endsWith('/prepare') ? agentUpdate({ candidate: { path: options.body.path || '/opt/agents/current/codex', sha256: 'b'.repeat(64) } })
      : snapshot({ entries: [hostEntry()] });
  } });
  await dispatch(visibleButton(container, 'Review agent update…'), 'click');
  const form = descendants(container).find(element => element.tagName === 'FORM');
  await dispatch(form, 'submit');
  const oldSave = visibleButton(container, 'Save update for restart');
  const trust = check(container, 'I reviewed this executable'); trust.checked = true; await dispatch(trust, 'change');
  const path = input(container, 'Installed executable (optional absolute path)');
  path.value = '/opt/agents/another/codex'; await dispatch(path, 'input');
  await dispatch(form, 'submit');
  assert.equal(check(container, 'I reviewed this executable').checked, false);
  await dispatch(oldSave, 'click');
  assert.equal(calls.length, 3);
  assert.equal(calls.some(([path]) => path.endsWith('/agent-update')), false);
});

test('settings merges stale-agent status only from the exact loaded executable revision', async () => {
  for (const same of [true, false]) {
    const container = new Element('main'), host = hostEntry();
    host.agents = host.agents.map(({ available, unavailableReason, ...agent }) => agent);
    const active = { ...host, profileDigest: same ? host.profileDigest : 'f'.repeat(64) };
    let available = true;
    const panel = await mountConnectionsPanel(container, { document,
      api: async () => snapshot({ entries: [host], active: [active] }),
      activeMachines: () => [{ id: host.id, executionKind: 'host', availableAgents: host.agents.map(agent => ({
        id: agent.id, executable: agent.path, available: agent.id !== 'codex' || available,
        unavailableReason: agent.id === 'codex' && !available ? 'Installed executable changed' : undefined,
      })) }],
    });
    assert.doesNotMatch(container.textContent, /Codex: update review needed/);
    available = false; panel.refreshActiveMachines();
    assert.equal(container.textContent.includes('Codex: update review needed'), same);
    await dispatch(visibleButton(container, 'Review agent update…'), 'click');
    assert.equal(input(container, 'Agent to update').value, same ? 'codex' : 'claude');
  }
});

test('Botainer update form explains the boundary and needs read-only acknowledgement plus renewed trust before saving', async () => {
  const container = new Element('main'), calls = [];
  await mountConnectionsPanel(container, { document, api: async (path, options) => {
    calls.push([path, options]);
    if (path === '/api/connections') return snapshot({ entries: [entry()] });
    if (!options) return installationSettings();
    if (path.endsWith('/prepare')) return installationUpdate({ profile: { ...profile(), source_root: '/opt/new-source' } });
    return snapshot({ revision: 4, entries: [entry({ profileDigest: 'b'.repeat(64) })], pendingRestart: true });
  } });
  await dispatch(visibleButton(container, 'Review Botainer update…'), 'click');
  assert.match(container.textContent, /does not mean the saved Botainer installation still matches/);
  assert.match(container.textContent, /does not install software, change SSH settings or start sessions/);
  assert.match(container.textContent, /new browser pairing/);
  assert.match(container.textContent, /remain archived/);
  assert.match(container.textContent, /not transferred automatically/);
  assert.match(container.textContent, /pending launch or consent prompts/);
  const form = descendants(container).find(element => element.tagName === 'FORM');
  await dispatch(form, 'submit'); assert.equal(calls.length, 2);
  const path = input(container, 'Botainer import folder'); path.value = '/opt/new-source'; await dispatch(path, 'input');
  const acknowledgement = check(container, 'I allow a read-only check'); acknowledgement.checked = true; await dispatch(acknowledgement, 'change');
  await dispatch(form, 'submit'); assert.equal(calls.length, 3);
  assert.deepEqual(calls[2][1].body.changes, { source_root: '/opt/new-source' });
  assert.match(container.textContent, /Botainer import folder · saved\/opt\/source/);
  assert.match(container.textContent, /Botainer import folder · inspected\/opt\/new-source/);
  const save = visibleButton(container, 'Save update for restart'); assert.equal(save.disabled, true);
  await dispatch(save, 'click'); assert.equal(calls.length, 3);
  const trust = check(container, 'I reviewed these software paths'); trust.checked = true; await dispatch(trust, 'change');
  await dispatch(save, 'click'); assert.equal(calls.length, 4);
  assert.equal(calls[3][1].body.candidateDigest, 'b'.repeat(64));
  assert.equal(Object.hasOwn(calls[3][1].body, 'profile'), false);
  assert.equal(visible(classElement(container, 'machine-overview')), true);
  assert.ok(calls.every(([path]) => path.startsWith('/api/connections')));
});

test('Botainer software review is available only for saved container connections and restricts local fields', async () => {
  const container = new Element('main'), local = entry({ id: 'local', kind: 'local' });
  await mountConnectionsPanel(container, { document, api: async path => path.endsWith('/installation-update')
    ? installationSettings({ id: 'local', kind: 'local' }) : snapshot({ entries: [local, hostEntry()], active: [entry()] }) });
  assert.equal(visibleButtons(container).filter(element => element.textContent === 'Review Botainer update…').length, 1);
  await dispatch(visibleButton(container, 'Review Botainer update…'), 'click');
  assert.equal(input(container, 'Python interpreter').value, '/opt/environment/bin/python');
  assert.equal(descendants(container).some(element => element.tagName === 'LABEL' && element.textContent === 'Botainer launcher'), false);
  assert.doesNotMatch(classElement(container, 'connection-workspace').textContent, /new browser pairing/);
});

test('Botainer failed checks and untrusted display values stay inert and never expose a Save action', async () => {
  for (const failed of [true, false]) {
    const container = new Element('main'), hostile = '<img src=x onerror=alert(1)>';
    await mountConnectionsPanel(container, { document, api: async (path, options) => {
      if (path === '/api/connections') return snapshot({ entries: [entry()] });
      if (!options) return installationSettings();
      return installationUpdate({ profile: failed ? null : { ...profile(), source_root: '/opt/' + hostile },
        checks: [{ name: hostile, message: hostile, status: 'failed' }], notice: hostile, instructions: [hostile] });
    } });
    await dispatch(visibleButton(container, 'Review Botainer update…'), 'click');
    const acknowledgement = check(container, 'I allow a read-only check'); acknowledgement.checked = true; await dispatch(acknowledgement, 'change');
    await dispatch(descendants(container).find(element => element.tagName === 'FORM'), 'submit');
    assert.match(container.textContent, /<img src=x onerror=alert\(1\)>/);
    assert.equal(descendants(container).some(element => ['IMG', 'SCRIPT'].includes(element.tagName)), false);
    assert.equal(visibleButtons(container).some(element => element.textContent === 'Save update for restart'), !failed);
  }
});

test('editing Botainer paths or withdrawing inspection acknowledgement invalidates old confirmations', async () => {
  for (const edit of ['path', 'acknowledgement']) {
    const container = new Element('main'), calls = [];
    await mountConnectionsPanel(container, { document, api: async (path, options) => {
      calls.push([path, options]); return path === '/api/connections' ? snapshot({ entries: [entry()] })
        : !options ? installationSettings() : installationUpdate();
    } });
    await dispatch(visibleButton(container, 'Review Botainer update…'), 'click');
    const acknowledgement = check(container, 'I allow a read-only check'); acknowledgement.checked = true; await dispatch(acknowledgement, 'change');
    const form = descendants(container).find(element => element.tagName === 'FORM'); await dispatch(form, 'submit');
    const oldTrust = check(container, 'I reviewed these software paths'), oldSave = visibleButton(container, 'Save update for restart');
    if (edit === 'path') { const path = input(container, 'Python interpreter'); path.value = '/opt/another/python'; await dispatch(path, 'input'); }
    else { acknowledgement.checked = false; await dispatch(acknowledgement, 'change'); }
    assert.equal(acknowledgement.checked, false);
    assert.equal(visibleButton(container, 'Inspect Botainer update').disabled, true);
    await dispatch(form, 'submit'); assert.equal(calls.length, 3);
    oldTrust.checked = true; await dispatch(oldTrust, 'change'); await dispatch(oldSave, 'click'); assert.equal(calls.length, 3);
    assert.equal(visibleButtons(container).some(element => element.textContent === 'Save update for restart'), false);
    acknowledgement.checked = true; await dispatch(acknowledgement, 'change'); await dispatch(form, 'submit');
    assert.equal(check(container, 'I reviewed these software paths').checked, false);
    assert.equal(visibleButton(container, 'Save update for restart').disabled, true);
    await dispatch(oldSave, 'click'); assert.equal(calls.length, 4);
  }
});

test('abandoning Botainer load or inspection suppresses late responses and errors', async () => {
  for (const phase of ['load', 'inspect']) for (const fail of [false, true]) {
    const container = new Element('main'), pending = deferred();
    await mountConnectionsPanel(container, { document, api: async (path, options) => {
      if (path === '/api/connections') return snapshot({ entries: [entry()] });
      if (!options && phase === 'inspect') return installationSettings();
      return pending.promise;
    } });
    await dispatch(visibleButton(container, 'Review Botainer update…'), 'click');
    if (phase === 'inspect') {
      const acknowledgement = check(container, 'I allow a read-only check'); acknowledgement.checked = true; await dispatch(acknowledgement, 'change');
      await dispatch(descendants(container).find(element => element.tagName === 'FORM'), 'submit');
    }
    await dispatch(visibleButton(container, '← Back to machines'), 'click');
    const before = classElement(container, 'machines-page').textContent;
    if (fail) pending.reject(new Error('Abandoned inspection error'));
    else pending.resolve(phase === 'load' ? installationSettings() : installationUpdate());
    await pause(); await pause();
    assert.equal(classElement(container, 'machines-page').textContent, before);
    assert.equal(classElement(container, 'connection-workspace').children.length, 0);
  }
});

test('a rejected Botainer update write clears trust and is not retried automatically', async () => {
  const container = new Element('main'), calls = [];
  await mountConnectionsPanel(container, { document, api: async (path, options) => {
    calls.push([path, options]);
    if (path === '/api/connections') return snapshot({ entries: [entry()] });
    if (!options) return installationSettings();
    if (path.endsWith('/prepare')) return installationUpdate();
    throw new Error('A launch prompt is pending. Finish it before saving.');
  } });
  await dispatch(visibleButton(container, 'Review Botainer update…'), 'click');
  const acknowledgement = check(container, 'I allow a read-only check'); acknowledgement.checked = true; await dispatch(acknowledgement, 'change');
  await dispatch(descendants(container).find(element => element.tagName === 'FORM'), 'submit');
  const trust = check(container, 'I reviewed these software paths'); trust.checked = true; await dispatch(trust, 'change');
  const save = visibleButton(container, 'Save update for restart'); await dispatch(save, 'click');
  assert.match(container.textContent, /A launch prompt is pending/);
  assert.match(container.textContent, /Refresh saved machines and inspect again/);
  assert.equal(visibleButtons(container).some(element => element.textContent === 'Save update for restart'), false);
  await dispatch(save, 'click'); await pause(); assert.equal(calls.length, 4);
});

test('Botainer paths edited during an inspection reject that response before showing a saveable review', async () => {
  const container = new Element('main'), pending = deferred();
  await mountConnectionsPanel(container, { document, api: async (path, options) => path === '/api/connections'
    ? snapshot({ entries: [entry()] }) : !options ? installationSettings() : pending.promise });
  await dispatch(visibleButton(container, 'Review Botainer update…'), 'click');
  const acknowledgement = check(container, 'I allow a read-only check'); acknowledgement.checked = true; await dispatch(acknowledgement, 'change');
  await dispatch(descendants(container).find(element => element.tagName === 'FORM'), 'submit');
  const path = input(container, 'Botainer launcher'); path.value = '/opt/another/botainer'; await dispatch(path, 'input');
  assert.equal(acknowledgement.checked, false);
  assert.equal(visibleButton(container, 'Inspect Botainer update').disabled, true);
  pending.resolve(installationUpdate()); await pause(); await pause();
  assert.equal(path.value, '/opt/another/botainer');
  assert.equal(visibleButton(container, 'Inspect Botainer update').disabled, true);
  assert.equal(visibleButtons(container).some(element => element.textContent === 'Save update for restart'), false);
  assert.equal(descendants(classElement(container, 'connection-workspace')).some(element =>
    element.tagName === 'LABEL' && element.textContent.includes('I reviewed these software paths')), false);
});

test('a newer Botainer draft survives completion of an already confirmed save and cannot reuse its trust', async () => {
  const container = new Element('main'), pending = deferred(), calls = [];
  await mountConnectionsPanel(container, { document, api: async (path, options) => {
    calls.push([path, options]);
    if (path === '/api/connections') return snapshot({ entries: [entry()] });
    if (!options) return installationSettings();
    if (path.endsWith('/prepare')) return installationUpdate();
    return pending.promise;
  } });
  await dispatch(visibleButton(container, 'Review Botainer update…'), 'click');
  const acknowledgement = check(container, 'I allow a read-only check'); acknowledgement.checked = true; await dispatch(acknowledgement, 'change');
  await dispatch(descendants(container).find(element => element.tagName === 'FORM'), 'submit');
  const trust = check(container, 'I reviewed these software paths'); trust.checked = true; await dispatch(trust, 'change');
  const save = visibleButton(container, 'Save update for restart'); await dispatch(save, 'click');
  const path = input(container, 'Botainer import folder'); path.value = '/opt/new-draft'; await dispatch(path, 'input');
  pending.resolve(snapshot({ revision: 4, entries: [entry({ profileDigest: 'b'.repeat(64) })], pendingRestart: true }));
  await pause(); await pause();
  assert.equal(input(container, 'Botainer import folder'), path); assert.equal(path.value, '/opt/new-draft');
  assert.match(container.textContent, /newer draft was not saved/);
  assert.equal(visibleButtons(container).some(element => element.textContent === 'Save update for restart'), false);
  await dispatch(save, 'click'); assert.equal(calls.length, 4);
});
