import test from 'node:test';
import assert from 'node:assert/strict';
import { TerminalViewRegistry, TerminalViewCapacityError } from '../../frontend/terminal-registry.js';
import { createWebSocketTransport } from '../../frontend/xterm-adapter.js';

const tick = () => new Promise(resolve => setImmediate(resolve));
const target = id => ({ contextNamespace: 'synthetic:local', runtimeId: id });

test('a full registry preserves the launch view when a runtime handoff needs another renderer', () => {
  const callbacks = new Map();
  const registry = new TerminalViewRegistry({
    createTerminal: () => ({ write() {}, setVisible() {}, setInputEnabled() {}, resize() {}, focus() {}, dispose() {} }),
    createTransport: (session, events) => {
      callbacks.set(session.runtimeId, events);
      return { open() {}, sendInput() {}, resize() {}, close() {} };
    },
  });
  for (let i = 0; i < 7; i++) {
    const session = target(`other-${i}`);
    registry.show(session); registry.attach(session); callbacks.get(session.runtimeId).onOpen();
  }
  const launch = target('launch'), runtime = target('runtime');
  registry.show(launch); registry.attach(launch); callbacks.get('launch').onOpen();
  callbacks.get('launch').onClose('session_eof');
  assert.equal(registry.size, 8);
  assert.throws(() => registry.show(runtime), TerminalViewCapacityError);
  assert.equal(registry.get(launch).visible, true);
  assert.equal(registry.get(runtime), null);
  // Explicitly making a completed view eligible frees capacity; a failed show
  // must not itself destroy history or a different connected terminal.
  registry.detach(launch); registry.hide(launch);
  registry.show(runtime);
  assert.equal(registry.get(runtime).visible, true);
  assert.equal(registry.get(target('other-0')).state, 'connected');
  registry.disposeAll();
});

test('EOF fences input while accepted output drains, and reconnect waits for that same renderer', async () => {
  const sockets = [], writes = [], pending = [], history = [];
  class Socket {
    constructor() { this.readyState = 0; this.bufferedAmount = 0; this.sent = []; sockets.push(this); }
    send(value) { this.sent.push(value); }
    close() { this.readyState = 3; this.onclose?.({ code: 1000 }); }
    ready() { this.readyState = 1; this.onopen(); this.onmessage({ data: '{"type":"ready"}' }); }
    frame(data) { this.onmessage({ data }); }
  }
  const renderer = {
    write(data) {
      writes.push(data);
      let resolve;
      const completion = new Promise(yes => { resolve = () => { history.push(...data); yes(); }; });
      pending.push({ completion, resolve });
      return completion;
    },
    flush: () => Promise.all(pending.map(item => item.completion)),
    beginConnection() {}, endConnection() {}, setVisible() {}, setInputEnabled() {}, resize() {}, focus() {}, dispose() {},
  };
  const registry = new TerminalViewRegistry({ createTerminal: () => renderer,
    createTransport: (session, callbacks) => createWebSocketTransport({ target: session, callbacks, terminal: renderer,
      WebSocket: Socket, bearer: 'A'.repeat(43), location: { protocol: 'http:', host: '127.0.0.1:4567' } }),
  });
  const session = target('runtime');
  registry.show(session); registry.attach(session); await tick(); sockets[0].ready();
  sockets[0].frame(new Uint8Array([0xf0, 0x9f]).buffer);
  sockets[0].frame(new Uint8Array([0x8c, 0xb1]).buffer);
  sockets[0].frame('{"type":"eof"}');
  assert.equal(registry.get(session).reason, 'session_eof');
  assert.equal(registry.sendInput(session, 'must not replay'), false);
  registry.attach(session); await tick();
  assert.equal(sockets.length, 1, 'new connection must await old parser work');
  pending.forEach(item => item.resolve()); await tick();
  assert.equal(writes.length, 2);
  assert.equal(new TextDecoder().decode(new Uint8Array(history)), '🌱');
  assert.equal(sockets[0].sent.length, 1, 'closed transport receives no late output credit');
  assert.equal(sockets.length, 2);
  sockets[1].ready();
  assert.equal(sockets[1].sent.filter(value => typeof value !== 'string').length, 0);
  registry.disposeAll();
});
