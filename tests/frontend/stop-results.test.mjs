import test from 'node:test';
import assert from 'node:assert/strict';
import { applySessionStopResult } from '../../frontend/app.js';

const session = { contextNamespace: 'local:example', runtimeId: 'native-session',
  projectId: 'project', state: 'running' };

function apply(result, target = session) {
  const detached = [];
  const notice = { textContent: '', set innerHTML(_value) {
    assert.fail('Native stop diagnostics must remain text.');
  } };
  const outcome = applySessionStopResult({}, target, result, {
    registry: { detach: value => detached.push(value) },
    notice: message => { notice.textContent = message; },
  });
  return { detached, message: notice.textContent, ...outcome };
}

test('unconfirmed local Stop retains the viewer and native diagnostic', () => {
  const result = apply({ session, terminationConfirmed: false,
    diagnostic: 'Native stop failed: <img src=x onerror=alert(1)>', helperCleanupConfirmed: false });
  assert.equal(result.confirmed, false);
  assert.deepEqual(result.detached, []);
  assert.match(result.message, /Stop is not confirmed.*terminal view remains open/s);
  assert.match(result.message, /<img src=x onerror=alert\(1\)>/);
  assert.doesNotMatch(result.message, /runtime has stopped|request completed/);
});

test('explicit false verdict overrides an ended sidebar observation', () => {
  const result = apply({ session: { ...session, state: 'stopped' }, terminationConfirmed: false });
  assert.equal(result.confirmed, false);
  assert.deepEqual(result.detached, []);
});

test('proven local termination detaches only its own view and keeps cleanup separate', () => {
  const result = apply({ session, terminationConfirmed: true, helperCleanupConfirmed: false });
  assert.equal(result.confirmed, true);
  assert.deepEqual(result.detached, [session]);
  assert.match(result.message, /runtime has stopped/);
  assert.match(result.message, /helper cleanup is still unconfirmed/);
});

test('wrong or missing target never closes a viewer or exposes another target diagnostic', () => {
  for (const observed of [undefined, { ...session, runtimeId: 'different' },
    { ...session, contextNamespace: 'remote:another' }]) {
    const result = apply({ session: observed, terminationConfirmed: true, diagnostic: 'other-target-output' });
    assert.equal(result.confirmed, false);
    assert.deepEqual(result.detached, []);
    assert.match(result.message, /could not be matched/);
    assert.doesNotMatch(result.message, /other-target-output/);
  }
});

test('scheduler acknowledgement is not termination even if an old record says stopped', () => {
  const target = { ...session, stopScope: 'allocation', runtime: 'apptainer', jobId: '1234' };
  for (const state of ['running', 'stopped', 'unknown']) {
    const result = apply({ session: { ...target, state } }, target);
    assert.equal(result.confirmed, false);
    assert.deepEqual(result.detached, []);
    assert.match(result.message, /cancellation was acknowledged.*termination is not yet confirmed/);
  }
});

test('explicit scheduler termination can close the original allocation view', () => {
  const target = { ...session, stopScope: 'allocation', runtime: 'apptainer', jobId: '1234' };
  const result = apply({ session: target, terminationConfirmed: true }, target);
  assert.equal(result.confirmed, true);
  assert.deepEqual(result.detached, [target]);
  assert.match(result.message, /scheduler allocation has ended/);
});

test('legacy verified host terminal end keeps the background-work warning', () => {
  const target = { ...session, executionKind: 'host' };
  const result = apply({ session: { ...target, state: 'stopped' } }, target);
  assert.equal(result.confirmed, true);
  assert.deepEqual(result.detached, [target]);
  assert.match(result.message, /host terminal process has stopped/);
  assert.match(result.message, /background work and shared-daemon tasks may still be running/);
});

test('legacy stale or unknown observation does not close the terminal', () => {
  for (const observed of [{ ...session, state: 'stopped', stale: true }, { ...session, state: 'unknown' }]) {
    const result = apply({ session: observed });
    assert.equal(result.confirmed, false);
    assert.deepEqual(result.detached, []);
  }
});

test('diagnostic bounds and control characters cannot escape the plain-text notice', () => {
  const result = apply({ session, terminationConfirmed: false,
    diagnostic: '\x00\u202e' + 'x'.repeat(10000), diagnosticTruncated: true });
  assert.equal(result.confirmed, false);
  assert.ok(result.message.length < 4500);
  assert.doesNotMatch(result.message, /[\x00\u202e]/);
  assert.match(result.message, /diagnostic was shortened/);
});

test('malformed success flags and missing response do not imply termination', () => {
  for (const reply of [undefined, null, {}, { session, terminationConfirmed: 'true' }]) {
    const result = apply(reply);
    assert.equal(result.confirmed, false);
    assert.deepEqual(result.detached, []);
  }
});
