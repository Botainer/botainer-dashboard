import test from 'node:test';
import assert from 'node:assert/strict';
import { TerminalViewCapacityError, TerminalViewRegistry } from '../../frontend/terminal-registry.js';

test('renderer drain completion reaches transport; an old async failure cannot disconnect a replacement', async () => {
  const h = harness();
  h.registry.show(A); h.registry.attach(A); h.transports[0].callbacks.onOpen();
  let reject;
  h.terminals[0].write = () => new Promise((resolve, refuse) => { reject = refuse; });
  const drained = h.transports[0].callbacks.onData('pending old bytes');
  assert.equal(typeof drained.then, 'function');
  h.registry.detach(A); h.registry.attach(A); h.transports[1].callbacks.onOpen();
  reject(new Error('old parser failed'));
  assert.equal(await drained, false);
  assert.equal(h.registry.get(A).state, 'connected');
  h.registry.disposeAll();
});

test('a current asynchronous renderer failure disconnects without an unhandled rejection', async () => {
  const h = harness();
  h.registry.show(A); h.registry.attach(A); h.transports[0].callbacks.onOpen();
  h.terminals[0].write = () => Promise.reject(new Error('parse failure'));
  assert.equal(await h.transports[0].callbacks.onData('bad output'), false);
  assert.equal(h.registry.get(A).state, 'disconnected');
  assert.equal(h.registry.get(A).reason, 'output_failed');
  h.registry.disposeAll();
});

const A = Object.freeze({ contextNamespace: 'local:account:state-a', runtimeId: 'run-1' });
const B = Object.freeze({ contextNamespace: 'local:account:state-a', runtimeId: 'run-2' });
const C = Object.freeze({ contextNamespace: 'cluster:account:state-a', runtimeId: 'run-1' });

// These are deterministic adapter doubles, not a PTY or terminal emulator.
function harness(options = {}) {
  const terminals = [];
  const transports = [];
  const released = [];
  const states = [];
  const registry = new TerminalViewRegistry({
    maxViews: options.maxViews ?? 8,
    onHistoryRelease: event => released.push(event),
    onStateChange: event => states.push(event),
    createTerminal(target, { onInput }) {
      const terminal = {
        target, host: {}, output: [], inputEnabled: false, visible: false,
        resizeCalls: [], focusCalls: 0, focusStates: [], disposeCalls: 0,
        selection: '', pastes: [], rejectPaste: false,
        failWrite: false, failDisableInput: false, failDispose: false,
        write(data) {
          if (this.failWrite) throw new Error('Renderer failed.');
          this.output.push(data);
        },
        setVisible(visible) { this.visible = visible; },
        setInputEnabled(enabled) {
          if (!enabled && this.failDisableInput) throw new Error('Unable to disable terminal input.');
          this.inputEnabled = enabled;
        },
        resize(cols, rows) { this.resizeCalls.push([cols, rows]); },
        focus() { this.focusCalls++; this.focusStates.push({ visible: this.visible, inputEnabled: this.inputEnabled }); },
        dispose() {
          this.disposeCalls++;
          if (this.failDispose) throw new Error('Terminal disposal failed.');
        },
        emitInput: onInput,
        getSelection() { return this.selection; },
        paste(text) {
          if (this.rejectPaste) return false;
          this.pastes.push(text);
          return onInput(text);
        },
      };
      terminals.push(terminal);
      return terminal;
    },
    createTransport(target, callbacks) {
      const transport = {
        target, callbacks, input: [], resizeCalls: [], openCalls: 0,
        closeCalls: 0, stopCalls: 0, rejectInput: false, failResize: false,
        open() {
          this.openCalls++;
          if (options.failOpen) throw new Error('Unable to open attachment.');
          options.onTransportOpen?.(this);
        },
        sendInput(data) {
          if (this.rejectInput) return false;
          this.input.push(data);
        },
        resize(cols, rows) {
          if (this.failResize) throw new Error('Connection closed while resizing.');
          this.resizeCalls.push([cols, rows]);
        },
        close() {
          this.closeCalls++;
          // Exercise synchronous callbacks during deliberate detach/disposal.
          callbacks.onClose();
        },
        stopRuntime() { this.stopCalls++; },
      };
      transports.push(transport);
      return transport;
    },
  });
  return { registry, terminals, transports, released, states };
}

function connect(h, target = A) {
  const view = h.registry.attach(target);
  const transport = h.transports.at(-1);
  transport.callbacks.onOpen();
  return { view, transport };
}

test('selected output can be copied after EOF without enabling paste or reconnecting', () => {
  const h = harness();
  h.registry.show(A); connect(h);
  h.terminals[0].selection = 'CLI launch failed';
  h.transports[0].callbacks.onClose('session_eof');
  assert.equal(h.registry.getSelection(A), 'CLI launch failed');
  assert.equal(h.registry.preparePaste(A), null);
  assert.equal(h.registry.get(A).state, 'disconnected');
  assert.equal(h.transports.length, 1);
  h.registry.show(B);
  assert.equal(h.registry.getSelection(A), '');
  h.registry.disposeAll();
});

test('prepared paste is explicit, single-use and bound to the exact current terminal', () => {
  const h = harness();
  h.registry.show(A);
  assert.equal(h.registry.preparePaste(A), null);
  h.registry.attach(A);
  assert.equal(h.registry.preparePaste(A), null);
  h.transports[0].callbacks.onOpen();
  const paste = h.registry.preparePaste(A);
  assert.equal(paste('chosen clipboard text'), true);
  assert.equal(paste('never replay'), false);
  assert.deepEqual(h.terminals[0].pastes, ['chosen clipboard text']);
  assert.deepEqual(h.transports[0].input, ['chosen clipboard text']);
  h.registry.disposeAll();
});

test('late clipboard read cannot paste after a switch, reconnect, EOF, disposal or view replacement', () => {
  const mutations = [
    h => { h.registry.show(B); },
    h => { h.registry.show(B); h.registry.show(A); },
    h => { h.registry.detach(A); h.registry.attach(A); h.transports[1].callbacks.onOpen(); },
    h => { h.transports[0].callbacks.onClose('session_eof'); },
    h => { h.registry.dispose(A); },
    h => { h.registry.dispose(A); h.registry.show(A); connect(h); },
  ];
  for (const mutate of mutations) {
    const h = harness();
    h.registry.show(A); connect(h);
    const paste = h.registry.preparePaste(A);
    mutate(h);
    assert.equal(paste('late clipboard text'), false);
    assert.deepEqual(h.terminals.flatMap(terminal => terminal.pastes), []);
    assert.deepEqual(h.transports.flatMap(transport => transport.input), []);
    h.registry.disposeAll();
  }
});

test('rejected paste reports failure without retrying or falling back to direct input', () => {
  const h = harness(); h.registry.show(A); connect(h);
  h.terminals[0].rejectPaste = true;
  const paste = h.registry.preparePaste(A);
  assert.equal(paste('too large'), false);
  assert.equal(paste('retry'), false);
  assert.deepEqual(h.transports[0].input, []);
  h.terminals[0].rejectPaste = false; h.transports[0].rejectInput = true;
  assert.equal(h.registry.preparePaste(A)('transport full'), false);
  assert.equal(h.registry.get(A).reason, 'input_failed');
  assert.deepEqual(h.transports[0].input, []);
  h.registry.disposeAll();
});

test('target identity is explicit, immutable and collision-free', () => {
  const h = harness();
  const target = { ...A };
  const a = h.registry.ensure(target);
  target.runtimeId = 'changed-after-registration';
  assert.deepEqual(a.target, A);
  assert.ok(Object.isFrozen(a.target));
  assert.equal(h.registry.ensure({ ...A }), a);
  assert.notEqual(h.registry.ensure(C), a);
  assert.notEqual(
    h.registry.ensure({ contextNamespace: 'x:y', runtimeId: 'z' }),
    h.registry.ensure({ contextNamespace: 'x', runtimeId: 'y:z' }),
  );
  assert.equal(h.terminals.length, 4);
  for (const target of [null, {}, { ...A, runtimeId: '' }, { ...A, contextNamespace: '  ' }]) {
    assert.throws(() => h.registry.ensure(target), TypeError);
  }
});

test('switching views preserves terminal objects, hosts, buffers and attachments', () => {
  const h = harness();
  const a = h.registry.show(A);
  const host = a.terminal.host;
  const { transport: first } = connect(h);
  first.callbacks.onData('retained output');
  const b = h.registry.show(B);
  connect(h, B);
  assert.equal(a.visible, false);
  assert.equal(a.terminal.inputEnabled, false);
  assert.equal(a.state, 'connected');
  assert.equal(first.closeCalls, 0);
  // Hidden attached views still receive their own output.
  first.callbacks.onData('background output');
  assert.equal(h.registry.show(A), a);
  assert.equal(a.terminal.host, host);
  assert.deepEqual(a.terminal.output, ['retained output', 'background output']);
  assert.equal(a.terminal.inputEnabled, true);
  assert.equal(b.visible, false);
  assert.equal(b.terminal.inputEnabled, false);
  assert.equal(h.terminals.length, 2);
  assert.equal(h.transports.length, 2);
  assert.equal(h.registry.focus(A), true);
  assert.equal(h.registry.focus(B), false);
  assert.equal(a.terminal.focusCalls, 1);
});

test('attach is idempotent while connecting and connected', () => {
  const h = harness();
  const view = h.registry.show(A);
  assert.equal(h.registry.attach(A), view);
  assert.equal(view.state, 'connecting');
  assert.equal(view.terminal.inputEnabled, false);
  assert.equal(h.registry.attach(A), view);
  const transport = h.transports[0];
  transport.callbacks.onData('too early');
  assert.deepEqual(view.terminal.output, []);
  transport.callbacks.onOpen();
  assert.equal(h.registry.attach(A), view);
  assert.equal(view.state, 'connected');
  assert.equal(view.terminal.inputEnabled, true);
  assert.equal(h.transports.length, 1);
  assert.equal(transport.openCalls, 1);
});

test('an explicit focus request during attach waits for writable readiness exactly once', () => {
  const h = harness();
  const view = h.registry.show(A);
  assert.equal(h.registry.focus(A), false);
  h.registry.attach(A);
  assert.equal(h.registry.focus(A), true);
  assert.equal(h.registry.focus(A), true);
  assert.equal(view.terminal.focusCalls, 0);
  assert.equal(view.terminal.emitInput('before ready'), false);
  const transport = h.transports[0];
  transport.callbacks.onOpen();
  assert.deepEqual(view.terminal.focusStates, [{ visible: true, inputEnabled: true }]);
  assert.equal(view.terminal.emitInput('echo hello\r'), true);
  assert.deepEqual(transport.input, ['echo hello\r']);
  transport.callbacks.onOpen();
  assert.equal(view.terminal.focusCalls, 1);
  assert.equal(h.registry.focus(A), true);
  assert.equal(view.terminal.focusCalls, 2);
});

test('switching views cancels pending focus even if the old view is shown again before ready', () => {
  const h = harness();
  const a = h.registry.show(A);
  h.registry.attach(A); h.registry.focus(A);
  const b = h.registry.show(B);
  connect(h, B); h.registry.focus(B);
  h.transports[0].callbacks.onOpen();
  assert.equal(a.terminal.focusCalls, 0);
  assert.equal(b.terminal.focusCalls, 1);

  h.registry.detach(A); h.registry.show(A); h.registry.attach(A); h.registry.focus(A);
  h.registry.hide(A); h.registry.show(A);
  h.transports.at(-1).callbacks.onOpen();
  assert.equal(a.terminal.focusCalls, 0);
  assert.equal(a.terminal.inputEnabled, true);
});

test('detachment and stale callbacks cannot carry a focus request into a new attachment', () => {
  const h = harness();
  const view = h.registry.show(A);
  h.registry.attach(A); h.registry.focus(A);
  const old = h.transports[0];
  h.registry.detach(A); h.registry.attach(A);
  old.callbacks.onOpen();
  assert.equal(view.terminal.focusCalls, 0);
  h.transports[1].callbacks.onOpen();
  assert.equal(view.terminal.focusCalls, 0);

  h.registry.detach(A); h.registry.attach(A); h.registry.focus(A);
  old.callbacks.onOpen();
  assert.equal(view.terminal.focusCalls, 0);
  h.transports[2].callbacks.onOpen();
  assert.deepEqual(view.terminal.focusStates, [{ visible: true, inputEnabled: true }]);
});

test('editor navigation can cancel pending terminal focus without abandoning the attachment', () => {
  const h = harness();
  const view = h.registry.show(A);
  assert.equal(h.registry.cancelPendingFocus(B), false);
  h.registry.attach(A); h.registry.focus(A);
  assert.equal(h.registry.cancelPendingFocus(A), true);
  assert.equal(h.registry.cancelPendingFocus(A), false);
  h.transports[0].callbacks.onOpen();
  assert.equal(view.state, 'connected');
  assert.equal(view.terminal.focusCalls, 0);
  assert.equal(view.terminal.inputEnabled, true);
});

test('failed initial resize cancels deferred focus instead of focusing disabled input', () => {
  const h = harness();
  const view = h.registry.show(A);
  h.registry.resize(A, 80, 24);
  h.registry.attach(A); h.registry.focus(A);
  h.transports[0].failResize = true;
  h.transports[0].callbacks.onOpen();
  assert.equal(view.state, 'disconnected');
  assert.equal(view.terminal.focusCalls, 0);
  assert.equal(h.registry.cancelPendingFocus(A), false);
});

test('connection timeout is distinct while arbitrary transport error text stays private', () => {
  for (const [reason, expected] of [
    ['connection_timeout', 'connection_timeout'],
    ['connection_lost', 'connection_lost'],
    ['untrusted transport detail <script>', 'transport_error'],
    [undefined, 'transport_error'],
  ]) {
    const h = harness();
    const view = h.registry.show(A);
    h.registry.attach(A); h.registry.focus(A);
    h.transports[0].callbacks.onError(reason);
    assert.equal(view.state, 'disconnected');
    assert.equal(view.reason, expected);
    assert.equal(view.terminal.focusCalls, 0);
    assert.equal(h.registry.cancelPendingFocus(A), false);
  }
});

test('wire EOF and policy closure survive the registry without becoming reconnectable loss', () => {
  for (const [reason, expected] of [['session_eof', 'session_eof'], ['connection_closed', 'connection_closed'],
    ['untrusted close detail', 'connection_closed'], ['connection_lost', 'connection_lost']]) {
    const h = harness(), view = h.registry.show(A);
    h.registry.attach(A); h.transports[0].callbacks.onOpen();
    h.transports[0].callbacks.onClose(reason);
    assert.equal(view.reason, expected);
    assert.equal(view.state, 'disconnected');
    assert.equal(view.terminal.emitInput('after close'), false);
  }
});

test('input is not queued before attachment, during connection, or after loss', () => {
  const h = harness();
  const view = h.registry.show(A);
  assert.equal(view.terminal.emitInput('before'), false);
  h.registry.attach(A);
  assert.equal(h.registry.sendInput(A, 'connecting'), false);
  const first = h.transports[0];
  first.callbacks.onOpen();
  assert.equal(view.terminal.emitInput('live'), true);
  first.callbacks.onClose();
  assert.equal(view.state, 'disconnected');
  assert.equal(view.reason, 'connection_lost');
  assert.equal(view.terminal.inputEnabled, false);
  assert.equal(view.terminal.emitInput('after loss'), false);
  const { transport: second } = connect(h);
  assert.deepEqual(first.input, ['live']);
  assert.deepEqual(second.input, []);
  assert.equal(view.terminal.emitInput('after reconnect'), true);
  assert.deepEqual(second.input, ['after reconnect']);
});

test('transport generations reject late open, output, close and error events', () => {
  const h = harness();
  const view = h.registry.show(A);
  h.registry.attach(A);
  const first = h.transports[0];
  h.registry.detach(A);
  const { transport: second } = connect(h);
  for (const signal of ['onOpen', 'onClose', 'onError']) first.callbacks[signal]();
  first.callbacks.onData('stale bytes');
  second.callbacks.onData('current bytes');
  assert.equal(view.state, 'connected');
  assert.equal(view.reason, null);
  assert.equal(second.closeCalls, 0);
  assert.deepEqual(view.terminal.output, ['current bytes']);
  assert.equal(view.terminal.emitInput('current input'), true);
  assert.deepEqual(first.input, []);
  assert.deepEqual(second.input, ['current input']);
});

test('detach, hide and disposal never invoke runtime stop', () => {
  const h = harness();
  const view = h.registry.show(A);
  const { transport } = connect(h);
  h.registry.hide(A);
  assert.equal(view.state, 'connected');
  assert.equal(transport.closeCalls, 0);
  assert.equal(h.registry.detach(A), true);
  assert.equal(view.state, 'detached');
  assert.equal(transport.closeCalls, 1);
  h.registry.detach(A);
  assert.equal(transport.closeCalls, 1);
  const { transport: second } = connect(h);
  assert.equal(h.registry.dispose(A), true);
  assert.equal(view.state, 'disposed');
  assert.equal(view.visible, false);
  assert.equal(second.closeCalls, 1);
  assert.equal(view.terminal.disposeCalls, 1);
  assert.equal(transport.stopCalls + second.stopCalls, 0);
  assert.equal(h.registry.get(A), null);
  assert.equal(h.registry.dispose(A), false);
  assert.deepEqual(h.released.map(event => event.reason), ['disposed']);
});

test('disposed terminal callbacks cannot write to a replacement view for the same run', () => {
  const h = harness();
  const oldView = h.registry.show(A);
  const { transport: oldTransport } = connect(h);
  h.registry.dispose(A);
  const newView = h.registry.show(A);
  const { transport: newTransport } = connect(h);
  assert.notEqual(newView, oldView);
  assert.equal(oldView.terminal.emitInput('from disposed terminal'), false);
  oldTransport.callbacks.onData('from disposed socket');
  oldTransport.callbacks.onClose();
  assert.deepEqual(newTransport.input, []);
  assert.deepEqual(newView.terminal.output, []);
  assert.equal(newView.state, 'connected');
});

test('capacity evicts only least-recently-shown hidden explicitly detached history', () => {
  const h = harness({ maxViews: 2 });
  const a = h.registry.show(A);
  const { transport } = connect(h);
  transport.callbacks.onData('history to release');
  h.registry.detach(A);
  const b = h.registry.show(B);
  h.registry.hide(B);
  // Refresh A's recency, then hide it so B is now the eligible oldest view.
  h.registry.show(A);
  h.registry.hide(A);
  h.registry.show(C);
  assert.equal(h.registry.size, 2);
  assert.equal(h.registry.get(B), null);
  assert.equal(h.registry.get(A), a);
  assert.equal(b.terminal.disposeCalls, 1);
  assert.deepEqual(a.terminal.output, ['history to release']);
  assert.deepEqual(h.released.map(event => [event.target, event.reason]), [[B, 'capacity']]);
});

test('capacity never silently detaches active, connecting, visible or lost views', () => {
  for (const state of ['visible', 'connecting', 'connected', 'disconnected']) {
    const h = harness({ maxViews: 1 });
    const view = h.registry.show(A);
    if (state !== 'visible') {
      h.registry.attach(A);
      if (state !== 'connecting') h.transports[0].callbacks.onOpen();
      if (state === 'disconnected') h.transports[0].callbacks.onClose();
      h.registry.hide(A);
    }
    const closesBefore = h.transports[0]?.closeCalls;
    assert.throws(() => h.registry.show(B), TerminalViewCapacityError, state);
    assert.equal(h.registry.get(A), view);
    assert.equal(h.terminals.length, 1);
    assert.equal(h.released.length, 0);
    assert.equal(h.transports[0]?.closeCalls, closesBefore);
  }
});

test('resize goes only to the visible connected owner and is repeated after reconnect', () => {
  const h = harness();
  const view = h.registry.show(A);
  assert.equal(h.registry.resize(A, 80, 24), false);
  const { transport: first } = connect(h);
  assert.deepEqual(first.resizeCalls, [[80, 24]]);
  assert.equal(h.registry.resize(A, 80, 24), true);
  assert.deepEqual(first.resizeCalls, [[80, 24]]);
  h.registry.resize(A, 100, 32);
  h.registry.hide(A);
  assert.equal(h.registry.resize(A, 40, 10), false);
  assert.deepEqual(first.resizeCalls, [[80, 24], [100, 32]]);
  assert.deepEqual(view.terminal.resizeCalls, [[80, 24], [100, 32]]);
  assert.throws(() => h.registry.resize(A, 0, 24), TypeError);
  assert.throws(() => h.registry.resize(A, 80, 1.5), TypeError);
  h.registry.show(A);
  h.registry.detach(A);
  const { transport: second } = connect(h);
  assert.deepEqual(second.resizeCalls, [[100, 32]]);
});

test('bytes pass to adapters unchanged, including split Unicode sequences', () => {
  const h = harness();
  const view = h.registry.show(A);
  const { transport } = connect(h);
  const first = new Uint8Array([0xf0, 0x9f]);
  const second = new Uint8Array([0x98, 0x80]);
  transport.callbacks.onData(first);
  transport.callbacks.onData(second);
  assert.equal(view.terminal.output[0], first);
  assert.equal(view.terminal.output[1], second);
  const input = new Uint8Array([0x1b, 0x5b, 0x41]);
  assert.equal(h.registry.sendInput(A, input), true);
  assert.equal(transport.input[0], input);
  assert.throws(() => h.registry.sendInput(A, { command: 'invalid data' }), TypeError);
});

test('input rejection fences the connection and never retries the rejected input', () => {
  const h = harness();
  const view = h.registry.show(A);
  const { transport: first } = connect(h);
  first.rejectInput = true;
  assert.equal(view.terminal.emitInput('do not replay'), false);
  assert.equal(view.state, 'disconnected');
  assert.equal(view.reason, 'input_failed');
  assert.equal(view.terminal.inputEnabled, false);
  assert.equal(first.closeCalls, 1);
  const { transport: second } = connect(h);
  assert.deepEqual(second.input, []);
});

test('transport error, invalid output and resize failure disable input and close attachment', () => {
  for (const failure of ['transport_error', 'output_failed', 'resize_failed']) {
    const h = harness();
    const view = h.registry.show(A);
    const { transport } = connect(h);
    if (failure === 'transport_error') transport.callbacks.onError(new Error('gone'));
    if (failure === 'output_failed') transport.callbacks.onData({ invalid: 'not terminal bytes' });
    if (failure === 'resize_failed') {
      transport.failResize = true;
      assert.equal(h.registry.resize(A, 80, 24), false);
    }
    assert.equal(view.state, 'disconnected');
    assert.equal(view.reason, failure);
    assert.equal(view.terminal.inputEnabled, false);
    assert.equal(transport.closeCalls, 1);
    assert.equal(view.terminal.emitInput('disabled'), false);
  }
});

test('failed connection setup cleans its handle and permits an explicit retry', () => {
  const h = harness({ failOpen: true });
  const view = h.registry.show(A);
  assert.throws(() => h.registry.attach(A), /Unable to open/);
  assert.equal(view.state, 'disconnected');
  assert.equal(view.reason, 'attach_failed');
  assert.equal(view.terminal.inputEnabled, false);
  assert.equal(h.transports[0].closeCalls, 1);
  assert.throws(() => h.registry.attach(A), /Unable to open/);
  assert.equal(h.transports.length, 2);
  assert.equal(h.transports[1].closeCalls, 1);
  assert.equal(h.terminals.length, 1);
});

test('registry shutdown disposes every view without stopping runtimes', () => {
  const h = harness();
  for (const target of [A, B, C]) {
    h.registry.show(target);
    connect(h, target);
  }
  h.registry.disposeAll();
  assert.equal(h.registry.size, 0);
  assert.deepEqual(h.transports.map(transport => transport.closeCalls), [1, 1, 1]);
  assert.deepEqual(h.transports.map(transport => transport.stopCalls), [0, 0, 0]);
  assert.deepEqual(h.terminals.map(terminal => terminal.disposeCalls), [1, 1, 1]);
  assert.equal(h.released.length, 3);
});

test('synchronous open and output callbacks see the registered transport and dimensions', () => {
  const h = harness({
    onTransportOpen(transport) {
      transport.callbacks.onOpen();
      transport.callbacks.onData('initial screen');
    },
  });
  const view = h.registry.show(A);
  h.registry.resize(A, 95, 30);
  assert.equal(h.registry.attach(A), view);
  assert.equal(view.state, 'connected');
  assert.equal(view.terminal.inputEnabled, true);
  assert.deepEqual(view.terminal.output, ['initial screen']);
  assert.deepEqual(h.transports[0].resizeCalls, [[95, 30]]);
  assert.equal(view.terminal.emitInput('live'), true);
  assert.deepEqual(h.transports[0].input, ['live']);
});

test('connection loss during open cannot be reversed by later callbacks from that open', () => {
  const h = harness({
    onTransportOpen(transport) {
      transport.callbacks.onClose();
      transport.callbacks.onOpen();
      transport.callbacks.onData('obsolete screen');
    },
  });
  const view = h.registry.show(A);
  h.registry.attach(A);
  assert.equal(view.state, 'disconnected');
  assert.equal(view.terminal.inputEnabled, false);
  assert.deepEqual(view.terminal.output, []);
  assert.equal(h.transports[0].closeCalls, 1);
});

test('renderer write exceptions fence input and stale output without stopping the runtime', () => {
  const h = harness();
  const view = h.registry.show(A);
  const { transport } = connect(h);
  view.terminal.failWrite = true;
  transport.callbacks.onData('valid bytes, broken renderer');
  assert.equal(view.reason, 'output_failed');
  assert.equal(view.state, 'disconnected');
  assert.equal(view.terminal.emitInput('must not send'), false);
  view.terminal.failWrite = false;
  transport.callbacks.onData('late output');
  assert.deepEqual(view.terminal.output, []);
  assert.equal(transport.closeCalls, 1);
  assert.equal(transport.stopCalls, 0);
});

test('a renderer input-disable failure cannot strand the transport during detach', () => {
  const h = harness();
  const view = h.registry.show(A);
  const { transport } = connect(h);
  view.terminal.failDisableInput = true;
  assert.throws(() => h.registry.detach(A), /Unable to disable/);
  assert.equal(transport.closeCalls, 1);
  assert.equal(view.state, 'detached');
  assert.equal(view.terminal.emitInput('still rejected by registry'), false);
  transport.callbacks.onOpen();
  transport.callbacks.onData('still fenced');
  assert.equal(view.state, 'detached');
  assert.deepEqual(view.terminal.output, []);
});

test('bulk cleanup continues past renderer failures and reports released history once', () => {
  const h = harness();
  const first = h.registry.show(A);
  connect(h, A);
  const second = h.registry.show(B);
  connect(h, B);
  first.terminal.failDisableInput = true;
  first.terminal.failDispose = true;
  assert.throws(() => h.registry.disposeAll(), AggregateError);
  assert.equal(h.registry.size, 0);
  assert.equal(first.state, 'disposed');
  assert.equal(second.state, 'disposed');
  assert.equal(first.terminal.emitInput('after failed renderer cleanup'), false);
  assert.deepEqual(h.transports.map(transport => transport.closeCalls), [1, 1]);
  assert.deepEqual(h.terminals.map(terminal => terminal.disposeCalls), [1, 1]);
  assert.deepEqual(h.released.map(event => event.target), [A, B]);
  assert.equal(h.states.at(-1).visible, false);
  h.registry.disposeAll();
  assert.equal(h.released.length, 2);
});

test('failed capacity cleanup also disposes the not-yet-registered replacement renderer', () => {
  const h = harness({ maxViews: 1 });
  const first = h.registry.ensure(A);
  first.terminal.failDispose = true;
  assert.throws(() => h.registry.ensure(B), AggregateError);
  assert.equal(h.registry.size, 0);
  assert.equal(h.registry.get(B), null);
  assert.deepEqual(h.terminals.map(terminal => terminal.disposeCalls), [1, 1]);
  assert.deepEqual(h.released.map(event => [event.target, event.reason]), [[A, 'capacity']]);
});

test('hidden terminal protocol replies stay on their attached target and stop after detach', () => {
  const h = harness();
  const first = h.registry.show(A);
  const { transport: firstTransport } = connect(h, A);
  h.registry.show(B);
  const { transport: secondTransport } = connect(h, B);
  assert.equal(first.terminal.inputEnabled, false);
  // A renderer may produce protocol replies while parsing output, independently
  // of user keystrokes. They must never be routed to the foreground view instead.
  first.terminal.write = () => first.terminal.emitInput('\u001b[1;1R');
  firstTransport.callbacks.onData('synthetic cursor query');
  assert.deepEqual(firstTransport.input, ['\u001b[1;1R']);
  assert.deepEqual(secondTransport.input, []);
  h.registry.detach(A);
  firstTransport.callbacks.onData('stale synthetic query');
  assert.deepEqual(firstTransport.input, ['\u001b[1;1R']);
  assert.deepEqual(secondTransport.input, []);
});

test('mutating caller target objects cannot retarget adapter callbacks or observations', () => {
  const h = harness();
  const callerTarget = { ...A };
  const view = h.registry.show(callerTarget);
  const { transport } = connect(h, callerTarget);
  callerTarget.contextNamespace = C.contextNamespace;
  callerTarget.runtimeId = 'another-run';
  assert.deepEqual(transport.target, A);
  assert.throws(() => { transport.target.runtimeId = 'changed'; }, TypeError);
  assert.equal(view.terminal.emitInput('original target'), true);
  assert.deepEqual(transport.input, ['original target']);
  assert.deepEqual(h.states.at(-1).target, A);
  assert.equal(h.registry.get(callerTarget), null);
});
