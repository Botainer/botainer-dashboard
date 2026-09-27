import test from 'node:test';
import assert from 'node:assert/strict';
import { isPastSchedulerRecord, hasActiveAllocationWithoutTerminal, isHistoricalSession,
  projectSessionGroups, projectStatusSummary, sessionGuidance, sessionStateLabel, sessionRecordedEnd, observedAgents, sessionControls } from '../../frontend/app.js';

const record = { contextNamespace: 'remote:profile', runtimeId: 'record-one', projectId: 'project-one', workspaceId: 'cluster',
  runtime: 'apptainer', agent: 'codex', state: 'unknown', jobId: '12345', schedulerObservation: 'not-active',
  schedulerState: null, createdAt: '2026-01-01T12:00:00Z', recordedEndedAt: null,
  capabilities: { attachTerminal: false, stopSession: false } };

test('a failed observation never reports zero active work in the project footer', () => {
  const project = { id: 'project-one' };
  for (const [p, rows, options] of [
    [{ ...project, stale: true }, [], {}],
    [project, [], { serviceConnected: false }],
    [project, [{ ...record, stale: true }], {}],
    [{ ...project, unavailableReason: 'SSH authentication required' }, [], {}],
  ]) {
    const summary = projectStatusSummary(p, rows, options);
    assert.match(summary, /Activity unverified/);
    assert.doesNotMatch(summary, /0 active/);
  }
});

test('project footer separates known activity, unresolved work, history and control restrictions', () => {
  const project = { id: 'project-one', controlRestriction: 'outside-project-roots', unavailableReason: 'Folder not enabled' };
  const unknown = { ...record, schedulerObservation: 'unavailable' };
  assert.equal(projectStatusSummary(project, [{ ...record, state: 'running' }, unknown, record]),
    '1 active session · 1 unverified · Controls unavailable');
  assert.equal(projectStatusSummary(project, [unknown]), '1 unverified · Controls unavailable');
  assert.equal(projectStatusSummary({ id: 'project-one' }, [record]), '0 active sessions · Service connected');
});

test('a fresh inactive scheduler record goes to history without inventing an agent outcome or end timestamp', () => {
  const before = JSON.stringify(record);
  assert.equal(isPastSchedulerRecord(record), true);
  assert.equal(isHistoricalSession(record), true);
  const groups = projectSessionGroups([record], { showHistory: true });
  assert.deepEqual(groups.current, []);
  assert.deepEqual(groups.history, [record]);
  assert.equal(groups.historyCount, 1);
  assert.equal(sessionStateLabel(record), 'past · outcome unverified');
  assert.equal(sessionRecordedEnd(record), '');
  assert.equal(record.state, 'unknown');
  assert.equal(JSON.stringify(record), before);
  assert.deepEqual(observedAgents([record]), []);
  const guidance = sessionGuidance(record, { viewState: 'detached', canStart: true });
  assert.equal(guidance.title, 'Past record · outcome unverified');
  assert.match(guidance.message, /successful scheduler check/);
  assert.match(guidance.message, /outcome and exact end time remain unverified/);
  assert.equal(guidance.action, null);
});

test('missing handles, failed scheduler queries and stale SSH observations never classify unknown records as past', () => {
  const uncertain = [
    { ...record, jobId: null, unavailableReason: 'No recorded scheduler job' },
    { ...record, schedulerObservation: 'unavailable' },
    { ...record, schedulerObservation: undefined, schedulerState: 'COMPLETED' },
    { ...record, schedulerObservation: 'active', schedulerState: 'RUNNING' },
    { ...record, stale: true },
    { ...record, runtime: 'docker' },
    { ...record, kind: 'launch' },
  ];
  for (const session of uncertain) {
    assert.equal(isPastSchedulerRecord(session), false);
    assert.equal(isHistoricalSession(session), false);
    assert.deepEqual(projectSessionGroups([session]).current, [session]);
  }
  const stale = { ...record, stale: true, schedulerObservation: 'active', schedulerState: 'RUNNING' };
  assert.equal(hasActiveAllocationWithoutTerminal(stale), false);
  assert.equal(sessionStateLabel(stale), 'unverified');
  assert.equal(sessionGuidance(stale, { viewState: 'detached' }).title, 'Session status is unverified');
});

test('an active allocation remains current while its original persistent agent terminal is unverified', () => {
  const session = { ...record, schedulerObservation: 'active', schedulerState: 'RUNNING',
    unavailableReason: 'No Screen owner was found for this session in the recorded allocation.' };
  assert.equal(hasActiveAllocationWithoutTerminal(session), true);
  assert.equal(isHistoricalSession(session), false);
  assert.deepEqual(projectSessionGroups([session]).current, [session]);
  assert.equal(sessionStateLabel(session), 'allocation active · unverified');
  const guidance = sessionGuidance(session, { viewState: 'detached', canStart: true, canAttach: false });
  assert.equal(guidance.title, 'Allocation running · persistent terminal unavailable');
  assert.ok(guidance.message.includes(session.unavailableReason));
  assert.match(guidance.message, /active allocation alone does not establish a reconnectable agent session/);
  assert.equal(guidance.action, null);
  assert.deepEqual(sessionControls({ capabilities: {} }, session), { attach: false, stop: false });
  const waiting = sessionGuidance({ ...session, schedulerState: 'PENDING' }, { viewState: 'detached' });
  assert.equal(waiting.title, 'Active allocation · persistent terminal unavailable');
  assert.doesNotMatch(waiting.title, /Allocation running/);
  assert.equal(sessionGuidance(session, { viewState: 'detached', serviceConnected: false }).title, 'Session status may be stale');
});

test('selected past records remain reachable without promoting unknown records from a failed connection', () => {
  const older = Array.from({ length: 7 }, (_, index) => ({ ...record, runtimeId: `past-${index}`,
    createdAt: `2026-01-0${index + 1}T12:00:00Z` }));
  const unknown = { ...record, runtimeId: 'ssh-unavailable', schedulerObservation: 'unavailable', stale: true };
  const groups = projectSessionGroups([...older, unknown], { selectedTarget: older[0] });
  assert.deepEqual(groups.current, [unknown]);
  assert.equal(groups.historyCount, 7);
  assert.deepEqual(groups.history, [older[0]]);
  assert.equal(groups.selectedHistory, older[0]);
});
