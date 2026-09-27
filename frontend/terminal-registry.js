/**
 * Owns terminal views, not runtime processes. No DOM or xterm dependency.
 *
 * A target is { contextNamespace, runtimeId }. The service supplies the canonical
 * host/account/state namespace; labels, executable versions and paths are not IDs.
 *
 * createTerminal(target, { onInput }) returns an adapter with write(data),
 * setVisible(boolean), setInputEnabled(boolean), resize(cols, rows), focus(),
 * and dispose(). It owns one stable DOM host. Disabling input must suppress user
 * keystrokes without preventing an attached terminal's protocol responses.
 * Optional getSelection() and paste(text) support explicit clipboard gestures;
 * paste must use the renderer's native bracketed-paste handling and return false
 * if it cannot accept input. Clipboard reads/writes belong to the UI gesture.
 *
 * createTransport(target, { onOpen, onData, onClose, onError }) returns an adapter
 * with open(), sendInput(data), resize(cols, rows), and close(). Factories are
 * synchronous; transport events begin only after open(). close() detaches the
 * client and MUST NOT stop a runtime. Transport adapters own byte/frame limits,
 * authentication, controller leases and output backpressure.
 * write may return a Promise fulfilled after renderer drain. onData returns that
 * completion to the transport for flow control. A real adapter must additionally
 * drain old parser work before allowing protocol replies on a new connection.
 *
 * sendInput/resize return false when unavailable; input is never queued/replayed.
 * focus requests during connection wait for that visible attachment's readiness.
 * Hiding/detaching cancels them; cancelPendingFocus also protects editor navigation.
 * Observer callbacks must not throw or reenter the registry. No terminal content
 * is persisted. Bounded history and rendering are the terminal adapter's job.
 */

const TERMINAL_METHODS = ['write', 'setVisible', 'setInputEnabled', 'resize', 'focus', 'dispose'];
const TRANSPORT_METHODS = ['open', 'sendInput', 'resize', 'close'];

function checkedTarget(target) {
  if (!target || typeof target !== 'object') throw new TypeError('A terminal target is required.');
  const { contextNamespace, runtimeId } = target;
  for (const [name, value] of Object.entries({ contextNamespace, runtimeId })) {
    if (typeof value !== 'string' || !value.trim()) throw new TypeError(`${name} must be a nonempty string.`);
  }
  return Object.freeze({ contextNamespace, runtimeId });
}

function targetKey(target) {
  const checked = checkedTarget(target);
  return JSON.stringify([checked.contextNamespace, checked.runtimeId]);
}

function assertMethods(adapter, methods, name) {
  for (const method of methods) {
    if (typeof adapter?.[method] !== 'function') throw new TypeError(`${name}.${method} must be a function.`);
  }
}

function checkedData(data) {
  if (typeof data !== 'string' && !(data instanceof Uint8Array)) {
    throw new TypeError('Terminal data must be a string or Uint8Array.');
  }
  return data;
}

export class TerminalViewCapacityError extends Error {
  constructor() {
    super('Terminal view limit reached. Hide and detach an unused view before opening another.');
    this.name = 'TerminalViewCapacityError';
    this.code = 'TERMINAL_VIEW_CAPACITY';
  }
}

export class TerminalViewRegistry {
  #views = new Map();
  #createTerminal;
  #createTransport;
  #maxViews;
  #onHistoryRelease;
  #onStateChange;
  #clock = 0;

  constructor({ createTerminal, createTransport, maxViews = 8, onHistoryRelease = () => {}, onStateChange = () => {} } = {}) {
    if (typeof createTerminal !== 'function' || typeof createTransport !== 'function') {
      throw new TypeError('Terminal and transport factories are required.');
    }
    if (!Number.isSafeInteger(maxViews) || maxViews < 1) throw new TypeError('maxViews must be a positive integer.');
    if (typeof onHistoryRelease !== 'function' || typeof onStateChange !== 'function') {
      throw new TypeError('Registry observers must be functions.');
    }
    this.#createTerminal = createTerminal;
    this.#createTransport = createTransport;
    this.#maxViews = maxViews;
    this.#onHistoryRelease = onHistoryRelease;
    this.#onStateChange = onStateChange;
  }

  get size() { return this.#views.size; }

  get(target) { return this.#views.get(targetKey(target))?.view ?? null; }

  ensure(target) {
    const key = targetKey(target);
    const existing = this.#views.get(key);
    if (existing) return existing.view;

    // Never evict a connected, connecting, visible, or unexpectedly lost view.
    const candidate = this.#views.size >= this.#maxViews
      ? [...this.#views.values()].filter(entry => !entry.visible && entry.state === 'detached')
        .sort((a, b) => a.lastUsed - b.lastUsed)[0]
      : null;
    if (this.#views.size >= this.#maxViews && !candidate) throw new TerminalViewCapacityError();

    const entry = {
      key, target: checkedTarget(target), terminal: null, transport: null,
      generation: 0, state: 'detached', visible: false, reason: null,
      lastUsed: ++this.#clock, dimensions: null, sentDimensions: null, focusGeneration: null, visibilityGeneration: 0,
    };
    const terminal = this.#createTerminal(entry.target, { onInput: data => this.#sendInput(entry, data) });
    try {
      assertMethods(terminal, TERMINAL_METHODS, 'terminal');
      terminal.setInputEnabled(false);
      terminal.setVisible(false);
    } catch (error) {
      terminal?.dispose?.();
      throw error;
    }
    entry.terminal = terminal;
    entry.view = Object.freeze({
      key, target: entry.target, terminal,
      get state() { return entry.state; },
      get visible() { return entry.visible; },
      get reason() { return entry.reason; },
    });
    if (candidate) {
      try { this.#release(candidate, 'capacity'); }
      catch (error) {
        // The replacement has not been registered; do not strand its DOM host
        // if releasing the old renderer reports a cleanup failure.
        try { terminal.dispose(); }
        catch (cleanupError) { throw new AggregateError([error, cleanupError], 'Terminal view replacement failed.'); }
        throw error;
      }
    }
    this.#views.set(key, entry);
    this.#notify(entry);
    return entry.view;
  }

  show(target) {
    const view = this.ensure(target);
    const entry = this.#views.get(view.key);
    for (const other of this.#views.values()) {
      if (other !== entry && other.visible) this.#setVisible(other, false);
    }
    entry.lastUsed = ++this.#clock;
    if (!entry.visible) this.#setVisible(entry, true);
    return view;
  }

  hide(target) {
    const entry = this.#views.get(targetKey(target));
    if (!entry) return false;
    if (entry.visible) this.#setVisible(entry, false);
    return true;
  }

  focus(target) {
    const entry = this.#views.get(targetKey(target));
    if (!entry?.visible) return false;
    // A connecting renderer is inert. Remember this explicit request until its
    // own attachment enables input, rather than focusing an unfocusable host.
    if (entry.state === 'connecting') {
      entry.focusGeneration = entry.generation;
      return true;
    }
    if (entry.state !== 'connected') return false;
    entry.terminal.focus();
    return true;
  }

  cancelPendingFocus(target) {
    const entry = this.#views.get(targetKey(target));
    if (!entry || entry.focusGeneration === null) return false;
    entry.focusGeneration = null;
    return true;
  }

  attach(target) {
    const view = this.ensure(target);
    const entry = this.#views.get(view.key);
    if (entry.state === 'connecting' || entry.state === 'connected') return view;
    const generation = ++entry.generation;
    entry.state = 'connecting';
    entry.reason = null;
    entry.sentDimensions = null;
    entry.focusGeneration = null;
    entry.terminal.setInputEnabled(false);
    const current = () => entry.generation === generation && entry.state !== 'disposed';
    let transport;
    try {
      transport = this.#createTransport(entry.target, {
        onOpen: () => {
          if (!current() || entry.state !== 'connecting' || !entry.transport) return;
          entry.state = 'connected';
          entry.terminal.setInputEnabled(entry.visible);
          this.#sendResize(entry);
          const focusRequested = entry.focusGeneration === generation;
          entry.focusGeneration = null;
          if (focusRequested && current() && entry.visible && entry.state === 'connected') {
            entry.terminal.focus();
          }
          this.#notify(entry);
        },
        onData: data => {
          if (!current() || entry.state !== 'connected') return false;
          try {
            const completion = entry.terminal.write(checkedData(data));
            if (completion && typeof completion.then === 'function') {
              // Fulfil with false on failure so consumers cannot create an
              // unhandled rejection. Never disconnect a newer attachment.
              return Promise.resolve(completion).then(() => current(), () => {
                if (current()) this.#disconnect(entry, 'disconnected', 'output_failed');
                return false;
              });
            }
            return true;
          } catch {
            this.#disconnect(entry, 'disconnected', 'output_failed');
            return false;
          }
        },
        onClose: reason => {
          if (current()) this.#disconnect(entry, 'disconnected',
            reason === undefined || reason === 'connection_lost' ? 'connection_lost'
              : reason === 'session_eof' ? 'session_eof' : 'connection_closed');
        },
        onError: reason => {
          if (current()) this.#disconnect(entry, 'disconnected',
            ['connection_timeout', 'connection_lost'].includes(reason) ? reason : 'transport_error');
        },
      });
      assertMethods(transport, TRANSPORT_METHODS, 'transport');
      entry.transport = transport;
      transport.open();
    } catch (error) {
      if (!entry.transport) entry.transport = typeof transport?.close === 'function' ? transport : null;
      this.#disconnect(entry, 'disconnected', 'attach_failed');
      throw error;
    }
    this.#notify(entry);
    return view;
  }

  detach(target) {
    const entry = this.#views.get(targetKey(target));
    if (!entry) return false;
    if (entry.state !== 'detached') this.#disconnect(entry, 'detached', null);
    return true;
  }

  sendInput(target, data) {
    const entry = this.#views.get(targetKey(target));
    return entry ? this.#sendInput(entry, data) : false;
  }

  getSelection(target) {
    const entry = this.#views.get(targetKey(target));
    if (!entry?.visible) return '';
    return entry.terminal.getSelection?.() ?? '';
  }

  preparePaste(target) {
    const key = targetKey(target), entry = this.#views.get(key);
    if (!entry?.visible || entry.state !== 'connected' || typeof entry.terminal.paste !== 'function') return null;
    const { generation, visibilityGeneration } = entry;
    let used = false;
    // Clipboard permission/read can finish after a tab switch or reconnect.
    // Capture the exact view and connection before that asynchronous operation;
    // refuse old input, including when the user switches away and back.
    return text => {
      if (used) return false;
      used = true;
      if (this.#views.get(key) !== entry || !entry.visible || entry.state !== 'connected' ||
          entry.generation !== generation || entry.visibilityGeneration !== visibilityGeneration) return false;
      const pasted = entry.terminal.paste(text);
      return pasted !== false && entry.state === 'connected' && entry.generation === generation;
    };
  }

  resize(target, cols, rows) {
    if (!Number.isSafeInteger(cols) || cols < 1 || !Number.isSafeInteger(rows) || rows < 1) {
      throw new TypeError('Terminal dimensions must be positive integers.');
    }
    const entry = this.#views.get(targetKey(target));
    if (!entry?.visible) return false;
    if (entry.dimensions?.cols !== cols || entry.dimensions?.rows !== rows) {
      entry.terminal.resize(cols, rows);
      entry.dimensions = { cols, rows };
    }
    return this.#sendResize(entry);
  }

  dispose(target) {
    const entry = this.#views.get(targetKey(target));
    if (!entry) return false;
    this.#release(entry, 'disposed');
    return true;
  }

  disposeAll() {
    const errors = [];
    for (const entry of [...this.#views.values()]) {
      try { this.#release(entry, 'disposed'); }
      catch (error) { errors.push(error); }
    }
    if (errors.length) throw new AggregateError(errors, 'Terminal view cleanup failed.');
  }

  #setVisible(entry, visible) {
    entry.visible = visible;
    if (!visible) {
      entry.focusGeneration = null;
      ++entry.visibilityGeneration;
    }
    entry.terminal.setVisible(visible);
    entry.terminal.setInputEnabled(visible && entry.state === 'connected');
    this.#notify(entry);
  }

  #sendInput(entry, data) {
    if (entry.state !== 'connected' || !entry.transport) return false;
    checkedData(data);
    try {
      if (entry.transport.sendInput(data) === false) throw new Error('Transport rejected input.');
      return true;
    } catch {
      this.#disconnect(entry, 'disconnected', 'input_failed');
      return false;
    }
  }

  #sendResize(entry) {
    if (!entry.visible || entry.state !== 'connected' || !entry.transport || !entry.dimensions) return false;
    const { cols, rows } = entry.dimensions;
    if (entry.sentDimensions?.cols === cols && entry.sentDimensions?.rows === rows) return true;
    try {
      if (entry.transport.resize(cols, rows) === false) throw new Error('Transport rejected resize.');
      entry.sentDimensions = { cols, rows };
      return true;
    } catch {
      this.#disconnect(entry, 'disconnected', 'resize_failed');
      return false;
    }
  }

  #disconnect(entry, state, reason) {
    // Fence callbacks before close(), which may synchronously emit more events.
    ++entry.generation;
    const transport = entry.transport;
    entry.transport = null;
    entry.state = state;
    entry.reason = reason;
    entry.sentDimensions = null;
    entry.focusGeneration = null;
    try { entry.terminal.setInputEnabled(false); }
    finally {
      // Renderer failures must not leave a transport attached and writable.
      try { transport?.close(); }
      catch { /* The connection is already fenced; never revive it to retry input. */ }
      this.#notify(entry);
    }
  }

  #release(entry, reason) {
    const errors = [];
    entry.visible = false;
    try { this.#disconnect(entry, 'disposed', reason); }
    catch (error) { errors.push(error); }
    this.#views.delete(entry.key);
    try { entry.terminal.dispose(); }
    catch (error) { errors.push(error); }
    this.#onHistoryRelease(Object.freeze({ key: entry.key, target: entry.target, reason }));
    if (errors.length) throw new AggregateError(errors, 'Terminal view cleanup failed.');
  }

  #notify(entry) {
    this.#onStateChange(Object.freeze({ key: entry.key, target: entry.target, state: entry.state, visible: entry.visible, reason: entry.reason }));
  }
}
