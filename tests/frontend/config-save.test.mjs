import test from 'node:test';
import assert from 'node:assert/strict';
import { configDocument, configurationTarget, configurationWritable, configurationStatus, configurationFormat,
  configCanSave, configCanRequestSave, submitConfigurationDraft } from '../../frontend/app.js';

const project = { id: 'project-one', workspaceId: 'local', name: 'Project one', capabilities: { configRead: true, configWrite: true } };
const target = configurationTarget('project', project);
const snapshot = { capabilities: {}, workspaces: [{ id: 'local', status: 'available', capabilities: {} }], projects: [project], sessions: [] };
const editable = () => { const draft = configDocument({ text: 'image: previous\n', revision: 'revision-1', writable: true }); draft.text = 'image: edited\n'; return draft; };
const blocked = () => { throw new Error('This request must not be sent.'); };

test('Validate and save is available for a changed writable draft before separate validation', () => {
  const draft = editable();
  assert.equal(configCanSave(draft), false);
  assert.equal(configCanRequestSave(snapshot, target, draft), true);
  assert.match(configurationStatus(snapshot, target, draft), /Validate and save checks this text before writing/);
  draft.text = draft.savedText;
  assert.equal(configCanRequestSave(snapshot, target, draft), false);
  assert.match(configurationStatus(snapshot, target, draft), /no unsaved changes/);
  draft.text = 'image: newer\n'; draft.pending = true; draft.phase = 'validating';
  assert.equal(configCanRequestSave(snapshot, target, draft), false);
  assert.match(configurationStatus(snapshot, target, draft), /Validating.*Nothing has been saved/);
  draft.phase = 'saving';
  assert.match(configurationStatus(snapshot, target, draft), /Saving the validated draft/);
});

test('one save action validates and saves precisely the same text and revision before accepting a new revision', async () => {
  const draft = editable(), phases = [], requests = [];
  const result = await submitConfigurationDraft(draft, { writable: () => true,
    check: async submitted => { requests.push(['check', submitted]); return { valid: true, revision: 'revision-1', warnings: ['Review this warning.'] }; },
    save: async submitted => { requests.push(['save', submitted]); return { saved: true, text: submitted.text, revision: 'revision-2', format: 'yaml', restartRequired: true }; },
    onChange: () => phases.push(draft.phase) });
  assert.equal(result.status, 'saved');
  assert.deepEqual(requests.map(([kind]) => kind), ['check', 'save']);
  assert.equal(requests[0][1], requests[1][1]);
  assert.deepEqual(requests[1][1], { text: 'image: edited\n', revision: 'revision-1' });
  assert.equal(Object.isFrozen(requests[1][1]), true);
  assert.deepEqual(phases, ['validating', 'saving', null]);
  assert.equal(draft.savedText, 'image: edited\n');
  assert.equal(draft.revision, 'revision-2');
  assert.equal(draft.pending, false);
  assert.equal(draft.validation, null);
  assert.match(draft.report, /Restart is required for launch modes that use this registration file/);
  assert.match(draft.report, /Separately selected runtime profiles are unchanged/);
  assert.match(draft.report, /Review this warning/);
});

test('invalid drafts and an explicitly changed saved revision never reach the write endpoint', async () => {
  const invalid = editable();
  assert.equal((await submitConfigurationDraft(invalid, { writable: () => true, save: blocked,
    check: async () => ({ valid: false, revision: 'revision-1', errors: ['Unknown setting: invent_me.'] }) })).status, 'invalid');
  assert.match(invalid.report, /Unknown setting/);
  assert.match(configurationStatus(snapshot, target, invalid), /validation failed/);
  assert.equal(invalid.savedText, 'image: previous\n');
  const stale = editable();
  assert.equal((await submitConfigurationDraft(stale, { writable: () => true, save: blocked,
    check: async () => ({ valid: true, revision: 'revision-external-change' }) })).status, 'revision-changed');
  assert.equal(stale.revision, 'revision-1');
  assert.equal(stale.text, 'image: edited\n');
  assert.equal(configCanRequestSave(snapshot, target, stale), false);
  assert.match(configurationStatus(snapshot, target, stale), /saved configuration revision changed/);
});

test('an edit or revision change during validation invalidates its response and preserves the newer draft', async () => {
  for (const change of [draft => { draft.text = 'image: changed-during-check\n'; }, draft => { draft.revision = 'different-revision'; }]) {
    const draft = editable();
    const result = await submitConfigurationDraft(draft, { writable: () => true, save: blocked,
      check: async () => { change(draft); return { valid: true, revision: 'revision-1' }; } });
    assert.equal(result.status, 'draft-changed');
    assert.equal(draft.validation, null);
    assert.equal(draft.savedText, 'image: previous\n');
    assert.match(draft.report, /Nothing was saved/);
  }
});

test('permissions are rechecked after validation and directly before write dispatch', async () => {
  for (const revokeAt of ['check', 'saving']) {
    const draft = editable(); let writable = true;
    const result = await submitConfigurationDraft(draft, { writable: () => writable, save: blocked,
      check: async () => { if (revokeAt === 'check') writable = false; return { valid: true, revision: 'revision-1' }; },
      onChange: () => { if (revokeAt === 'saving' && draft.phase === 'saving') writable = false; } });
    assert.ok(['read-only', 'changed-before-save'].includes(result.status));
    assert.match(draft.report, /Nothing was saved/);
    assert.equal(draft.savedText, 'image: previous\n');
  }
});

test('concurrent actions share the pending guard and do not duplicate validation or write', async () => {
  const draft = editable(); let resolveCheck, checks = 0, writes = 0;
  const check = () => { checks++; return new Promise(resolve => { resolveCheck = resolve; }); };
  const save = async submitted => { writes++; return { saved: true, text: submitted.text, revision: 'revision-2' }; };
  const first = submitConfigurationDraft(draft, { writable: () => true, check, save });
  assert.equal((await submitConfigurationDraft(draft, { writable: () => true, check, save })).status, 'busy');
  resolveCheck({ valid: true, revision: 'revision-1' });
  assert.equal((await first).status, 'saved');
  assert.equal(checks, 1); assert.equal(writes, 1);
});

test('a failed or unacknowledged write keeps the draft and requires reload instead of resubmitting', async () => {
  for (const failure of ['network', 'refused']) {
    const draft = editable();
    const result = await submitConfigurationDraft(draft, { writable: () => true,
      check: async () => ({ valid: true, revision: 'revision-1' }),
      save: async () => { if (failure === 'network') throw new Error('The operation outcome is unknown.'); return { saved: false, errors: ['Revision conflict; file changed.'] }; } });
    assert.equal(result.status, 'error');
    assert.equal(draft.text, 'image: edited\n');
    assert.equal(draft.savedText, 'image: previous\n');
    assert.equal(draft.revision, 'revision-1');
    assert.equal(draft.requiresReload, true);
    assert.equal(configCanRequestSave(snapshot, target, draft), false);
    assert.match(configurationStatus(snapshot, target, draft), /Save was not confirmed.*draft is retained/);
    assert.equal((await submitConfigurationDraft(draft, { writable: () => true, check: blocked, save: blocked })).status, 'reload-required');
  }
});

test('a save receipt for different text cannot replace the submitted draft or claim success', async () => {
  const draft = editable();
  const result = await submitConfigurationDraft(draft, { writable: () => true,
    check: async () => ({ valid: true, revision: 'revision-1' }),
    save: async () => ({ saved: true, text: 'image: unexpected-content\n', revision: 'revision-2' }) });
  assert.equal(result.status, 'error');
  assert.equal(draft.text, 'image: edited\n');
  assert.equal(draft.savedText, 'image: previous\n');
  assert.equal(draft.revision, 'revision-1');
  assert.equal(draft.requiresReload, true);
  assert.match(draft.report, /different configuration text/);
});

test('a validation-stage revision conflict retains the draft and requires reload with a clear explanation', async () => {
  const draft = editable();
  const result = await submitConfigurationDraft(draft, { writable: () => true, save: blocked,
    check: async () => { throw new Error('config-revision-conflict'); } });
  assert.equal(result.status, 'error');
  assert.equal(draft.text, 'image: edited\n');
  assert.equal(draft.savedText, 'image: previous\n');
  assert.equal(draft.revision, 'revision-1');
  assert.equal(draft.requiresReload, true);
  assert.match(draft.report, /file changed on disk.*draft is retained/);
  assert.doesNotMatch(draft.report, /config-revision-conflict/);
  assert.equal(configCanRequestSave(snapshot, target, draft), false);
  assert.equal((await submitConfigurationDraft(draft, { writable: () => true, check: blocked, save: blocked })).status, 'reload-required');
});

test('Check config never saves and unchanged or read-only saves send no requests', async () => {
  const draft = editable();
  assert.equal((await submitConfigurationDraft(draft, { writable: () => true,
    check: async () => ({ valid: true, revision: 'revision-1' }) })).status, 'checked');
  assert.equal(draft.savedText, 'image: previous\n');
  assert.equal(configCanSave(draft), true);
  assert.match(configurationStatus(snapshot, target, draft), /validation passed/);
  draft.text = draft.savedText;
  assert.equal((await submitConfigurationDraft(draft, { writable: () => true, check: blocked, save: blocked })).status, 'unchanged');
  assert.equal((await submitConfigurationDraft(editable(), { writable: () => false, check: blocked, save: blocked })).status, 'read-only');
});

test('a newer retained edit is not replaced by a successful response for the earlier submitted draft', async () => {
  const draft = editable();
  const result = await submitConfigurationDraft(draft, { writable: () => true,
    check: async () => ({ valid: true, revision: 'revision-1' }),
    save: async submitted => {
      draft.text = 'image: an-even-newer-edit\n';
      return { saved: true, text: submitted.text, revision: 'revision-2' };
    } });
  assert.equal(result.status, 'saved');
  assert.equal(draft.text, 'image: an-even-newer-edit\n');
  assert.equal(draft.savedText, 'image: edited\n');
  assert.equal(draft.revision, 'revision-2');
  assert.equal(draft.validation, null);
  assert.equal(configCanRequestSave(snapshot, target, draft), true);
  assert.match(configurationStatus(snapshot, target, draft), /Unsaved draft/);
});

test('read-only explanations distinguish unavailable connections, control roots and active sessions', () => {
  const draft = editable();
  assert.match(configurationStatus(snapshot, target, draft, { connected: false }), /dashboard service is unavailable/);
  const outside = { ...snapshot, projects: [{ ...project, capabilities: { configWrite: false }, unavailableReason: 'Project is outside the configured control roots' }] };
  assert.match(configurationStatus(outside, target, draft), /outside the configured control roots/);
  const active = { ...snapshot, projects: [{ ...project, capabilities: { configWrite: false } }], sessions: [{ projectId: project.id, state: 'running' }] };
  assert.match(configurationStatus(active, target, draft), /active or unverified session/);
  const reason = { ...draft, writable: false, readOnlyReason: 'Explicitly configured read-only in this profile.' };
  assert.match(configurationStatus(snapshot, target, reason), /Explicitly configured read-only/);
  assert.equal(configurationWritable(snapshot, target, reason), false);
  draft.validation = { valid: true, text: draft.text, revision: 'an-older-revision' };
  assert.match(configurationStatus(snapshot, target, draft), /earlier validation no longer matches/);
});

test('configuration format labels use the service descriptor and conservative legacy defaults', () => {
  const dashboard = configurationTarget('dashboard');
  for (const format of ['yaml', 'json']) {
    const draft = configDocument({ text: 'example text', revision: 'version', format });
    assert.equal(configurationFormat(draft, dashboard), format);
    assert.equal(configurationFormat(draft, target), format);
  }
  const unknown = configDocument({ text: '{}', revision: 'version', format: 'executable' });
  assert.equal(configurationFormat(unknown, dashboard), 'json');
  assert.equal(configurationFormat(unknown, target), 'yaml');
});

test('saved recovery location includes its rotating record identity and storage failure preserves draft', async () => {
  const draft = editable();
  await submitConfigurationDraft(draft, { writable: () => true,
    check: async () => ({ valid: true }),
    save: async submitted => ({ saved: true, text: submitted.text, revision: 'next',
      recovery: { path: '/private-control/config-recovery/slot-00.json', id: 'abc123' } }) });
  assert.match(draft.report, /rotating history.*slot-00.json.*ID abc123/);
  const failed = editable();
  await submitConfigurationDraft(failed, { writable: () => true,
    check: async () => ({ valid: true }),
    save: async () => { throw new Error('config-recovery-unavailable'); } });
  assert.equal(failed.text, 'image: edited\n');
  assert.equal(failed.savedText, 'image: previous\n');
  assert.equal(failed.requiresReload, true);
});
