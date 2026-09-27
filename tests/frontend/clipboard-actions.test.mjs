import test from 'node:test';
import assert from 'node:assert/strict';
import { copyTerminalSelection, pasteTerminalClipboard, sessionGuidance } from '../../frontend/app.js';

const target = { contextNamespace: 'local:example', runtimeId: 'session-one' };

test('copy uses selected output without requiring a live terminal or reading the clipboard', async () => {
  const copied = [];
  const registry = { getSelection: input => { assert.equal(input, target); return 'Recorded launch error'; } };
  await copyTerminalSelection(registry, target, { writeText: async text => copied.push(text),
    readText: () => { throw new Error('Copy must not read clipboard'); } });
  assert.deepEqual(copied, ['Recorded launch error']);
  await assert.rejects(copyTerminalSelection({ getSelection: () => '' }, target, {
    writeText: () => { throw new Error('No selection must not clear clipboard'); },
  }), /Select terminal text first/);
});

test('paste captures a target-bound permission before reading clipboard and never retries refused data', async () => {
  let resolve, current = true;
  const read = new Promise(done => { resolve = done; });
  const sent = [], events = [];
  const registry = { preparePaste: input => {
    assert.equal(input, target); events.push('capture');
    return text => { events.push('commit'); if (!current) return false; sent.push(text); return true; };
  }, focus: input => { assert.equal(input, target); events.push('focus'); } };
  const pending = pasteTerminalClipboard(registry, target, { readText: () => { events.push('read'); return read; } });
  current = false; resolve('never send this');
  await assert.rejects(pending, /Paste was not sent/);
  assert.deepEqual(sent, []);
  assert.deepEqual(events, ['capture', 'read', 'commit']);
  current = true;
  await pasteTerminalClipboard(registry, target, { readText: async () => 'one paste' });
  assert.deepEqual(sent, ['one paste']);
  assert.equal(events.at(-1), 'focus');
});

test('readonly or disconnected terminals cannot request clipboard contents', async () => {
  await assert.rejects(pasteTerminalClipboard({ preparePaste: () => null }, target,
    { readText: () => { throw new Error('Must not read clipboard'); } }), /connected, writable terminal/);
});

test('a finished launch retains its own failure explanation when workspace observation later fails', () => {
  const session = { kind: 'launch', launchState: 'failed', state: 'unknown',
    unavailableReason: 'The container runtime is currently unavailable',
    launchReason: 'A startup hook needs review; no container was dispatched.' };
  let guidance = sessionGuidance(session, { viewState: 'disconnected' });
  assert.equal(guidance.title, 'Botainer launch failed');
  assert.equal(guidance.message, session.launchReason);
  delete session.launchReason;
  guidance = sessionGuidance(session, { viewState: 'disconnected' });
  assert.doesNotMatch(guidance.message, /container runtime is currently unavailable/);
  assert.match(guidance.message, /CLI output/);
});
