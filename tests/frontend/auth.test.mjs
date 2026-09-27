import test from 'node:test';
import assert from 'node:assert/strict';
import { getBearer, setBearer, clearBearer, authHeaders } from '../../frontend/auth.js';

test('credential remains in supplied origin storage and can be cleared', () => {
  const data = new Map();
  const storage = { getItem: key => data.get(key), setItem: (key, value) => data.set(key, value), removeItem: key => data.delete(key) };
  const token = 'A'.repeat(43);
  assert.equal(getBearer(storage), null);
  setBearer(token, storage);
  assert.equal(getBearer(storage), token);
  assert.deepEqual(authHeaders(getBearer(storage)), { Authorization: `Bearer ${token}` });
  clearBearer(storage);
  assert.equal(getBearer(storage), null);
  assert.throws(() => authHeaders(null), /Dashboard locked/);
});

test('blocked storage or malformed credentials never enable cookie-only access', () => {
  const storage = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } };
  assert.equal(getBearer(storage), null);
  assert.throws(() => setBearer('A'.repeat(43), storage), /blocked/);
  assert.throws(() => setBearer('short', storage), /Invalid dashboard credential/);
  assert.throws(() => authHeaders('A'.repeat(43) + '\n'), /Dashboard locked/);
});

test('default storage persists across tabs instead of requiring a per-tab token', () => {
  const prior = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  const data = new Map();
  const storage = { getItem: key => data.get(key), setItem: (key, value) => data.set(key, value), removeItem: key => data.delete(key) };
  Object.defineProperty(globalThis, 'localStorage', { value: storage, configurable: true });
  try {
    setBearer('B'.repeat(43));
    assert.equal(getBearer(), 'B'.repeat(43));
    assert.equal(data.get('botainer-dashboard:bearer:v2'), 'B'.repeat(43));
    clearBearer();
    assert.equal(getBearer(), null);
  } finally {
    if (prior) Object.defineProperty(globalThis, 'localStorage', prior);
    else delete globalThis.localStorage;
  }
});
