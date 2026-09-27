import test from 'node:test';
import assert from 'node:assert/strict';
import { createWebSocketTransport, createXtermAdapter, FRAME_BYTES, QUEUE_BYTES, PASTE_BYTES, BELL_INTERVAL_MS } from '../../frontend/xterm-adapter.js';

const target = { contextNamespace: 'host:account:state', runtimeId: 'run-1' };
const bearer = 'A'.repeat(43);
const tick = () => new Promise(resolve => setImmediate(resolve));
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
function transportHarness(options = {}) {
  const sockets = [], data = [], events = [], errors = [], closes = [];
  class Socket {
    constructor(url, protocols) { this.url = url; this.protocols = protocols; this.readyState = 0; this.bufferedAmount = 0; this.sent = []; sockets.push(this); }
    send(value) { this.sent.push(value); }
    close() { this.readyState = 3; this.onclose?.(); }
    handshake() { this.readyState = 1; this.onopen(); }
    frame(data) { this.onmessage({ data }); }
  }
  const terminal = { flush: () => options.flush ?? Promise.resolve(), beginConnection: () => events.push('begin'), endConnection: () => events.push('end') };
  const transport = createWebSocketTransport({ target, terminal, WebSocket: Socket, bearer,
    location: { protocol: 'http:', host: '127.0.0.1:4567' }, timeoutMs: options.timeoutMs ?? 1000,
    callbacks: { onOpen: () => events.push('open'), onData: bytes => { data.push(bytes); return options.render?.(bytes); }, onClose: reason => { events.push('close'); closes.push(reason); }, onError: reason => { events.push('error'); errors.push(reason); } } });
  const connect = async () => { transport.open(); await tick(); sockets[0].handshake(); sockets[0].frame(JSON.stringify({ type: 'ready' })); };
  return { transport, sockets, data, events, errors, closes, connect };
}

test('EOF, explicit closure and rejected upgrades remain distinct from a lost ready connection', async () => {
  const eof = transportHarness(); await eof.connect(); eof.sockets[0].frame('{"type":"eof"}');
  assert.deepEqual(eof.closes, ['session_eof']);
  assert.equal(eof.transport.sendInput('after EOF'), false);
  const closed = transportHarness(); await closed.connect(); closed.transport.close();
  assert.deepEqual(closed.closes, ['connection_closed']);
  for (const code of [1000, 1008, 1011]) {
    const refused = transportHarness(); await refused.connect(); refused.sockets[0].onclose({ code });
    assert.deepEqual(refused.closes, ['connection_closed']);
  }
  for (const code of [1006, 1012]) {
    const lost = transportHarness(); await lost.connect(); lost.sockets[0].onclose({ code });
    assert.deepEqual(lost.closes, ['connection_lost']);
  }
  const beforeReady = transportHarness(); beforeReady.transport.open(); await tick(); beforeReady.sockets[0].onerror();
  assert.deepEqual(beforeReady.errors, ['transport_error']);
  const ready = transportHarness(); await ready.connect(); ready.sockets[0].onerror();
  assert.deepEqual(ready.errors, ['connection_lost']);
});

test('binds an explicit namespace after renderer drain, with no URL token and no handshake input', async () => {
  const drain = deferred(); const h = transportHarness({ flush: drain.promise });
  h.transport.open(); await tick(); assert.equal(h.sockets.length, 0);
  assert.equal(h.transport.sendInput('lost'), false);
  drain.resolve(); await tick(); const socket = h.sockets[0];
  assert.equal(socket.url, 'ws://127.0.0.1:4567/api/sessions/run-1/terminal');
  assert.deepEqual(socket.protocols, ['botainer-dashboard.v1', `credential.${bearer}`]);
  socket.handshake();
  assert.deepEqual(JSON.parse(socket.sent[0]), { type: 'bind', contextNamespace: target.contextNamespace, cols: 80, rows: 24 });
  assert.equal(h.transport.sendInput('also lost'), false);
  assert.deepEqual(h.events, []);
  socket.frame('{"type":"ready"}'); assert.deepEqual(h.events, ['begin', 'open']);
  assert.equal(h.transport.sendInput('once'), true); assert.equal(new TextDecoder().decode(socket.sent[1]), 'once');
  h.transport.close();
});

test('output credit follows renderer completion and preserves bytes split inside UTF-8', async () => {
  const renders = [deferred(), deferred()]; let index = 0;
  const h = transportHarness({ render: () => renders[index++].promise }); await h.connect();
  const socket = h.sockets[0]; socket.frame(new Uint8Array([0xf0, 0x9f]).buffer); socket.frame(new Uint8Array([0x8c, 0xb1]).buffer);
  assert.deepEqual(h.data.map(bytes => [...bytes]), [[0xf0, 0x9f], [0x8c, 0xb1]]);
  assert.equal(socket.sent.length, 1);
  renders[0].resolve(true); await tick(); assert.deepEqual(JSON.parse(socket.sent[1]), { type: 'ack', bytes: 2 });
  renders[1].resolve(true); await tick(); assert.deepEqual(JSON.parse(socket.sent[2]), { type: 'ack', bytes: 2 });
  h.transport.close();
});

test('disconnect fences late renderer ACK and never replays refused input', async () => {
  const rendered = deferred(); const h = transportHarness({ render: () => rendered.promise }); await h.connect();
  const socket = h.sockets[0]; socket.frame(new Uint8Array([1]).buffer);
  h.transport.close(); assert.equal(h.transport.sendInput('do not replay'), false);
  rendered.resolve(true); await tick(); assert.equal(socket.sent.length, 1);
  h.transport.open(); await tick(); assert.equal(h.sockets.length, 1);
});

test('renderer failure ends attachment without granting output credit', async () => {
  const rendered = deferred(); const h = transportHarness({ render: () => rendered.promise }); await h.connect();
  h.sockets[0].frame(new Uint8Array([1]).buffer); rendered.reject(new Error('failed renderer')); await tick();
  assert.equal(h.sockets[0].sent.length, 1); assert.deepEqual(h.events, ['begin', 'open', 'end', 'error']);
});

test('bounds pending output and frame size instead of silently dropping terminal bytes', async () => {
  for (const oversizedFrame of [false, true]) {
    const rendered = deferred(); const h = transportHarness({ render: () => rendered.promise }); await h.connect();
    if (oversizedFrame) h.sockets[0].frame(new Uint8Array(FRAME_BYTES + 1).buffer);
    else for (let n = 0; n < QUEUE_BYTES / FRAME_BYTES + 1; n++) h.sockets[0].frame(new Uint8Array(FRAME_BYTES).buffer);
    assert.equal(h.events.at(-1), 'error'); assert.equal(h.sockets[0].sent.length, 1);
    rendered.resolve(); await tick();
  }
});

test('input is fragmented without recoding binary bytes and refused under backpressure', async () => {
  const h = transportHarness(); await h.connect(); const socket = h.sockets[0];
  const input = Uint8Array.from({ length: FRAME_BYTES + 3 }, (_, i) => i % 256);
  assert.equal(h.transport.sendInput(input), true);
  assert.deepEqual(new Uint8Array([...socket.sent[1], ...socket.sent[2]]), input);
  assert.equal(socket.sent[1].byteLength, FRAME_BYTES);
  socket.bufferedAmount = QUEUE_BYTES;
  assert.equal(h.transport.sendInput('x'), false); assert.equal(socket.sent.length, 3);
  assert.equal(h.transport.resize(80, 24), false);
  h.transport.close();
});

test('invalid control, pre-ready output, excessive dimensions and expired handshake fail closed', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  for (const frame of ['{"type":"launch","shell":"bad"}', '{broken', new Uint8Array([1]).buffer]) {
    const h = transportHarness(); h.transport.open(); await tick(); h.sockets[0].handshake(); h.sockets[0].frame(frame);
    assert.equal(h.events.at(-1), 'error'); assert.equal(h.data.length, 0);
  }
  const h = transportHarness({ timeoutMs: 5 }); h.transport.open(); await tick();
  t.mock.timers.tick(5);
  assert.equal(h.events.at(-1), 'error');
  const h2 = transportHarness(); await h2.connect(); assert.equal(h2.transport.resize(501, 24), false); assert.equal(h2.transport.resize(80, 0), false); h2.transport.close();
});

test('a handshake timeout is actionable and late readiness cannot enable input or retry attachment', async (t) => {
  // Advance the deadline only after the bind. A real 20ms timer could expire
  // before setImmediate on a busy host, testing a different connection phase.
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const h = transportHarness({ timeoutMs: 20 }); h.transport.open(); await tick();
  const socket = h.sockets[0]; socket.handshake();
  assert.equal(h.transport.sendInput('echo hello'), false);
  t.mock.timers.tick(19);
  assert.deepEqual(h.errors, []);
  assert.equal(socket.sent.length, 1);
  t.mock.timers.tick(1);
  assert.deepEqual(h.errors, ['connection_timeout']);
  socket.frame('{"type":"ready"}');
  assert.equal(h.transport.sendInput('must not replay'), false);
  h.transport.open(); await tick();
  assert.equal(h.sockets.length, 1);
  assert.deepEqual(h.events, ['end', 'error']);
  assert.equal(socket.sent.length, 1);
});

function rendererHarness(options = {}) {
  const terms = [], observers = [], dimensions = [], inputs = [];
  const host = { hidden: false, inert: false, clientWidth: 640, clientHeight: 400, setAttribute() {}, remove() { this.removed = true; } };
  const container = { append(value) { assert.equal(value, host); } };
  class Terminal {
    constructor(options) { this.options = options; this.writes = []; this.pastes = []; this.selection = ''; terms.push(this); }
    loadAddon() {} open() {} resize(cols, rows) { this.dimensions = [cols, rows]; } focus() {} dispose() { this.disposed = true; }
    onData(callback) { this.data = callback; return { dispose() {} }; }
    onBinary(callback) { this.binary = callback; return { dispose() {} }; }
    onBell(callback) { this.bell = callback; return { dispose: () => { this.bellSubscriptionDisposed = true; } }; }
    write(data, callback) { this.writes.push({ data, callback }); }
    getSelection() { return this.selection; }
    paste(text) { this.pastes.push(text); }
  }
  class FitAddon { proposeDimensions() { return { cols: 90, rows: 30 }; } }
  class SearchAddon { clearDecorations() {} findNext() { return true; } }
  class ResizeObserver { constructor(callback) { this.callback = callback; observers.push(this); } observe() {} disconnect() { this.disconnected = true; } }
  const adapter = createXtermAdapter({ target, container, Terminal, FitAddon, SearchAddon, ResizeObserver,
    document: { createElement: () => host }, onInput: data => inputs.push(data), onDimensions: (...values) => dimensions.push(values),
    onAttention: options.onAttention, fontSize: options.fontSize });
  return { adapter, host, term: terms[0], dimensions, inputs, observers };
}

test('text size refits the same visible renderer without clearing output or changing input state', async () => {
  const h = rendererHarness({ fontSize: 17 });
  assert.equal(h.term.options.fontSize, 17);
  h.adapter.setVisible(true); h.adapter.beginConnection(); h.adapter.setInputEnabled(true);
  const output = h.adapter.write('retained output'); h.term.writes[0].callback(); await output; await tick();
  h.dimensions.length = 0;
  h.adapter.setFontSize(21); await tick();
  assert.equal(h.term.options.fontSize, 21);
  assert.deepEqual(h.dimensions, [[target, 90, 30]]);
  assert.equal(h.term.writes[0].data, 'retained output');
  assert.equal(h.term.disposed, undefined);
  assert.equal(h.term.options.disableStdin, false);
  assert.deepEqual(h.inputs, []);
  h.adapter.dispose();
});

test('hidden text-size changes wait for visibility and invalid preferences use the default', async () => {
  for (const size of [undefined, null, '18', NaN, Infinity, 0, 9, 25, 12.5]) {
    const invalid = rendererHarness({ fontSize: size });
    assert.equal(invalid.term.options.fontSize, 13); invalid.adapter.dispose();
  }
  const h = rendererHarness();
  h.adapter.setFontSize(24); await tick();
  assert.equal(h.term.options.fontSize, 24); assert.deepEqual(h.dimensions, []);
  h.adapter.setVisible(true); await tick();
  assert.deepEqual(h.dimensions, [[target, 90, 30]]);
  h.adapter.dispose(); h.dimensions.length = 0;
  h.adapter.setFontSize(10); await tick();
  assert.equal(h.term.options.fontSize, 24); assert.deepEqual(h.dimensions, []);
});

test('hidden connected terminals retain protocol replies but inert blocks browser input', async () => {
  const h = rendererHarness(); h.adapter.setInputEnabled(false); h.term.data('stale'); assert.deepEqual(h.inputs, []);
  h.adapter.beginConnection(); h.adapter.setVisible(false); h.adapter.setInputEnabled(false);
  assert.equal(h.host.inert, true); assert.equal(h.term.options.disableStdin, false);
  h.term.data('terminal query reply'); assert.deepEqual(h.inputs, ['terminal query reply']);
  h.term.binary('\x80\xff'); assert.deepEqual([...h.inputs[1]], [128, 255]);
  h.adapter.endConnection(); h.term.data('old reply'); assert.equal(h.inputs.length, 2); assert.equal(h.term.options.disableStdin, true);
  h.adapter.dispose();
});

test('visible disconnected output remains selectable while input and late protocol replies are fenced', () => {
  const h = rendererHarness();
  h.adapter.setVisible(true);
  h.adapter.setInputEnabled(false);
  assert.equal(h.host.inert, false);
  assert.equal(h.term.options.disableStdin, true);
  h.term.selection = 'Native CLI failure text';
  assert.equal(h.adapter.getSelection(), 'Native CLI failure text');
  h.adapter.beginConnection(); h.adapter.setInputEnabled(true);
  h.adapter.setInputEnabled(false); h.adapter.endConnection();
  assert.equal(h.host.inert, false);
  assert.equal(h.term.options.disableStdin, true);
  h.term.data('must not reach ended launch');
  assert.deepEqual(h.inputs, []);
  assert.equal(h.adapter.paste('must not reach ended launch'), false);
  h.adapter.setVisible(false);
  assert.equal(h.host.inert, true);
  h.adapter.dispose();
  assert.equal(h.adapter.getSelection(), '');
});

test('explicit paste uses xterm native paste only for the visible writable attachment', () => {
  const h = rendererHarness();
  const text = 'first line\nsecond line\n';
  assert.equal(h.adapter.paste(text), false);
  h.adapter.setVisible(true); h.adapter.beginConnection();
  assert.equal(h.adapter.paste(text), false);
  h.adapter.setInputEnabled(true);
  assert.equal(h.adapter.paste(text), true);
  assert.deepEqual(h.term.pastes, [text]); // No direct onInput, rewrite or extra Enter.
  assert.deepEqual(h.inputs, []);
  assert.equal(h.term.options.macOptionClickForcesSelection, true);
  h.adapter.setVisible(false);
  assert.equal(h.adapter.paste(text), false);
  h.adapter.setVisible(true); h.adapter.endConnection();
  assert.equal(h.adapter.paste(text), false);
  h.adapter.dispose();
  assert.equal(h.adapter.paste(text), false);
  assert.deepEqual(h.term.pastes, [text]);
});

test('paste limit reserves bracket framing bytes and measures UTF-8 without queuing a retry', () => {
  const h = rendererHarness();
  h.adapter.setVisible(true); h.adapter.beginConnection(); h.adapter.setInputEnabled(true);
  assert.equal(h.adapter.paste('a'.repeat(PASTE_BYTES)), true);
  assert.equal(h.adapter.paste('a'.repeat(PASTE_BYTES + 1)), false);
  assert.equal(h.adapter.paste('🌱'.repeat(Math.floor(PASTE_BYTES / 4) + 1)), false);
  assert.equal(h.term.pastes.length, 1);
  assert.throws(() => h.adapter.paste(new Uint8Array([1])), /plain text/);
  h.adapter.dispose();
});

test('renderer waits for xterm parser callback, bounds memory and ignores hidden dimensions', async () => {
  const h = rendererHarness(); h.adapter.measure(); assert.equal(h.dimensions.length, 0);
  h.adapter.setVisible(true); await tick(); assert.deepEqual(h.dimensions[0], [target, 90, 30]);
  const writes = []; for (let i = 0; i < 4; i++) writes.push(h.adapter.write(new Uint8Array(FRAME_BYTES)));
  assert.throws(() => h.adapter.write(new Uint8Array([1])), /queue limit/);
  let flushed = false; const flush = h.adapter.flush().then(() => { flushed = true; }); await tick(); assert.equal(flushed, false);
  h.term.writes.forEach(write => write.callback()); await Promise.all(writes); await flush; assert.equal(flushed, true);
  h.adapter.dispose(); assert.equal(h.observers[0].disconnected, true); assert.equal(h.host.removed, true);
});

test('view disposal settles pending parser completion without enabling output-driven host actions', async () => {
  const h = rendererHarness(); const completion = h.adapter.write(new Uint8Array([1]));
  const rejection = assert.rejects(completion, /closed/); h.adapter.dispose(); await rejection;
  assert.equal(h.term.options.logLevel, 'off'); assert.equal(h.term.options.linkHandler.activate(null, 'javascript:bad'), undefined);
});

test('terminal bell is advisory only while connected, including hidden views, and never forwards output', () => {
  const attention = [];
  const h = rendererHarness({ onAttention: (...args) => attention.push(args) });
  h.term.bell('untrusted payload before connection');
  assert.deepEqual(attention, []);
  h.adapter.beginConnection();
  h.adapter.setVisible(false);
  h.term.bell('untrusted terminal text');
  assert.deepEqual(attention, [[target, { kind: 'bell' }]]);
  assert.deepEqual(h.inputs, []);
  h.adapter.endConnection();
  h.term.bell('late parser event after disconnect');
  assert.equal(attention.length, 1);
  h.adapter.beginConnection();
  h.term.bell();
  assert.equal(attention.length, 2);
  h.adapter.dispose();
  assert.equal(h.term.bellSubscriptionDisposed, true);
  h.term.bell('queued parser callback after disposal');
  h.adapter.beginConnection();
  h.term.bell();
  assert.equal(attention.length, 2);
});

test('terminal bell flood is coalesced per connection without a deferred notification', t => {
  let now = 20;
  t.mock.method(globalThis.performance, 'now', () => now);
  const attention = [];
  const h = rendererHarness({ onAttention: (...args) => attention.push(args) });
  h.adapter.beginConnection();
  for (let index = 0; index < 5000; index++) h.term.bell();
  assert.equal(attention.length, 1);
  now += BELL_INTERVAL_MS - 1;
  h.term.bell();
  assert.equal(attention.length, 1);
  now += 1;
  h.term.bell();
  assert.equal(attention.length, 2);
  h.adapter.beginConnection(); // A repeated begin is not a fresh transport.
  h.term.bell();
  assert.equal(attention.length, 2);
  h.adapter.endConnection();
  h.adapter.beginConnection();
  h.term.bell();
  assert.equal(attention.length, 3);
  h.adapter.dispose();
});

test('throwing or missing bell observers do not fail terminal parsing or write completion', async () => {
  let observations = 0;
  const h = rendererHarness({ onAttention: () => { observations++; throw new Error('observer failed'); } });
  h.adapter.beginConnection();
  assert.doesNotThrow(() => h.term.bell());
  assert.equal(observations, 1);
  const completion = h.adapter.write(new Uint8Array([7]));
  h.term.writes[0].callback();
  await completion;
  h.adapter.dispose();
  const optional = rendererHarness();
  optional.adapter.beginConnection();
  assert.doesNotThrow(() => optional.term.bell());
  optional.adapter.dispose();
});
