/** Browser terminal and wire adapters. No runtime is started by either adapter. */
import { getBearer, authHeaders } from './auth.js';
export const FRAME_BYTES = 16 * 1024;
export const QUEUE_BYTES = 64 * 1024;
// Reserve both bracketed-paste delimiters before passing text to xterm.
export const PASTE_BYTES = QUEUE_BYTES - 12;
// Allow the service's 45-second attachment deadline plus its bind/ready I/O.
export const CONNECT_TIMEOUT_MS = 55000;
export const BELL_INTERVAL_MS = 1000;
export const DEFAULT_TERMINAL_FONT_SIZE = 13;
export function terminalFontSize(value) {
  return Number.isInteger(value) && value >= 10 && value <= 24 ? value : DEFAULT_TERMINAL_FONT_SIZE;
}
const encoder = new TextEncoder();

export function targetKey(target) {
  return JSON.stringify([target.contextNamespace, target.runtimeId]);
}

export function createXtermAdapter({ target, container, onInput, onDimensions, onAttention, fontSize = DEFAULT_TERMINAL_FONT_SIZE,
  Terminal = globalThis.Terminal, FitAddon = globalThis.FitAddon?.FitAddon,
  SearchAddon = globalThis.SearchAddon?.SearchAddon,
  ResizeObserver = globalThis.ResizeObserver, document = globalThis.document } = {}) {
  if (!Terminal || !FitAddon || !SearchAddon) throw new Error('Local terminal assets are unavailable.');
  const host = document.createElement('div');
  host.className = 'terminal-host';
  host.hidden = true;
  host.setAttribute('role', 'group');
  host.setAttribute('aria-label', 'Session terminal');
  container.append(host);
  const term = new Terminal({
    cursorBlink: true, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
    fontSize: terminalFontSize(fontSize), lineHeight: 1.15, scrollback: 3000, disableStdin: true,
    allowProposedApi: false, allowTransparency: false, screenReaderMode: false,
    macOptionClickForcesSelection: true,
    logLevel: 'off', linkHandler: { activate() { /* Output cannot open a host URL. */ } },
    // No link addon, clipboard addon, window manipulation, or output-driven actions.
    theme: { background: '#11161c', foreground: '#dce5ed', cursor: '#a7d5ca',
      selectionBackground: '#334951', black: '#1b252d', red: '#ed8b88', green: '#9ac5a0',
      yellow: '#dcc18a', blue: '#94b9de', magenta: '#c8a5d4', cyan: '#8fcaca', white: '#dce5ed' },
  });
  const fit = new FitAddon();
  const search = new SearchAddon();
  term.loadAddon(fit);
  term.loadAddon(search);
  term.open(host);
  let visible = false, disposed = false, connected = false, inputEnabled = false, pendingBytes = 0;
  let lastBellAt = -Infinity;
  const pending = new Set();
  const updateInteraction = () => {
    // A disconnected view is read-only, not inert: its retained output must
    // still support text selection, copying and scrolling after a CLI failure.
    // Hidden attached views stay inert while xterm answers terminal queries.
    host.inert = !visible || (connected && !inputEnabled);
    term.options.disableStdin = !connected;
  };
  const subscriptions = [
    term.onData(data => { if (connected) onInput(data); }),
    term.onBinary(data => {
      if (connected) onInput(Uint8Array.from(data, character => character.charCodeAt(0) & 255));
    }),
    term.onBell(() => {
      // A terminal bell is an advisory hint, not proof of an input prompt.
      // Never forward output text or trigger input, URLs or OS notifications.
      if (!connected || disposed || typeof onAttention !== 'function') return;
      const now = globalThis.performance.now();
      if (now - lastBellAt < BELL_INTERVAL_MS) return;
      lastBellAt = now;
      try { onAttention(target, { kind: 'bell' }); }
      catch { /* An observer failure must not interrupt terminal parsing. */ }
    }),
  ];
  const measure = () => {
    if (!visible || disposed || !host.clientWidth || !host.clientHeight) return;
    const dimensions = fit.proposeDimensions();
    if (dimensions && dimensions.cols >= 2 && dimensions.rows >= 1) {
      onDimensions(target, Math.min(500, dimensions.cols), Math.min(300, dimensions.rows));
    }
  };
  const observer = new ResizeObserver(measure);
  observer.observe(host);
  document.fonts?.ready.then(() => { if (!disposed) measure(); });
  return {
    host,
    write(data) {
      if (disposed) throw new Error('Terminal view is closed.');
      const size = typeof data === 'string' ? encoder.encode(data).byteLength : data.byteLength;
      if (size > FRAME_BYTES || pendingBytes + size > QUEUE_BYTES) throw new Error('Terminal render queue limit exceeded.');
      pendingBytes += size;
      let resolve, reject;
      const completion = new Promise((accept, refuse) => { resolve = accept; reject = refuse; });
      const entry = { completion, reject };
      pending.add(entry);
      const finish = error => {
        if (!pending.delete(entry)) return;
        pendingBytes -= size;
        if (error) reject(error); else resolve();
      };
      try { term.write(data, () => finish()); }
      catch (error) { finish(error); }
      return completion;
    },
    flush() { return Promise.all([...pending].map(entry => entry.completion)); },
    beginConnection() {
      if (!disposed) {
        if (!connected) lastBellAt = -Infinity;
        connected = true;
        updateInteraction();
      }
    },
    endConnection() { connected = false; updateInteraction(); },
    setVisible(value) {
      visible = value; host.hidden = !value; updateInteraction();
      if (value) queueMicrotask(measure);
    },
    setInputEnabled(value) {
      // xterm's disableStdin suppresses protocol replies too. Inert fences actual
      // keyboard/paste/mouse interaction while a hidden attached view can answer
      // its own terminal queries. Transport state still fences every emitted byte.
      inputEnabled = value;
      updateInteraction();
    },
    setFontSize(value) {
      if (disposed) return;
      const size = terminalFontSize(value);
      if (term.options.fontSize === size) return;
      term.options.fontSize = size;
      // Keep the renderer and its history. The normal target-bound resize path
      // updates the PTY; hidden views are measured only when made visible.
      queueMicrotask(measure);
    },
    getSelection() { return disposed ? '' : term.getSelection(); },
    paste(text) {
      if (typeof text !== 'string') throw new TypeError('Paste requires plain text.');
      if (disposed || !visible || !connected || !inputEnabled || encoder.encode(text).byteLength > PASTE_BYTES) return false;
      // Use xterm's native normalization and bracketed-paste handling. Never
      // synthesize Enter or route pasted text directly around the terminal.
      term.paste(text);
      return true;
    },
    resize(cols, rows) { if (!disposed) term.resize(cols, rows); },
    focus() { if (visible && !disposed) term.focus(); },
    find(text, previous = false) {
      if (!text) { search.clearDecorations(); return false; }
      return previous ? search.findPrevious(text, { regex: false }) : search.findNext(text, { regex: false });
    },
    measure,
    dispose() {
      if (disposed) return;
      disposed = true;
      connected = false;
      observer.disconnect();
      subscriptions.forEach(subscription => subscription.dispose());
      for (const entry of pending) entry.reject(new Error('Terminal view closed before render completed.'));
      pending.clear();
      pendingBytes = 0;
      term.dispose();
      host.remove();
    },
  };
}

export function createWebSocketTransport({ target, callbacks, terminal,
  WebSocket = globalThis.WebSocket, location = globalThis.location,
  timeoutMs = CONNECT_TIMEOUT_MS, bearer = getBearer() } = {}) {
  if (!target?.contextNamespace || !target?.runtimeId) throw new Error('A complete session target is required.');
  if (!['http:', 'https:'].includes(location.protocol)) throw new Error('Open the dashboard through its local service.');
  authHeaders(bearer); // Validate before creating any transport; no cookie-only path.
  const url = `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api/sessions/${encodeURIComponent(target.runtimeId)}/terminal`;
  let socket = null, closed = false, ready = false, opening = false, unrendered = 0, timer;
  const clearTimer = () => { if (timer) clearTimeout(timer); timer = null; };
  const finish = (error = false, reason = error ? 'transport_error' : 'connection_closed') => {
    if (closed) return;
    closed = true;
    ready = false;
    clearTimer();
    terminal.endConnection();
    try { socket?.close(); } catch { /* Already fenced. */ }
    if (error) callbacks.onError(reason); else callbacks.onClose(reason);
  };
  const sendControl = value => {
    const text = JSON.stringify(value);
    if (!socket || socket.readyState !== 1 || socket.bufferedAmount + encoder.encode(text).byteLength > QUEUE_BYTES) return false;
    try { socket.send(text); return true; }
    catch { return false; }
  };
  return {
    open() {
      if (opening || closed) return;
      opening = true;
      timer = setTimeout(() => finish(true, 'connection_timeout'), timeoutMs);
      // Old parser work may generate terminal replies. Keep it fenced and fully
      // drained before opening the new attachment; never route those replies to it.
      Promise.resolve().then(() => terminal.flush()).then(() => {
        if (closed) return;
        socket = new WebSocket(url, ['botainer-dashboard.v1', `credential.${bearer}`]);
        socket.binaryType = 'arraybuffer';
        socket.onopen = () => {
          if (closed) return;
          if (!sendControl({ type: 'bind', contextNamespace: target.contextNamespace, cols: 80, rows: 24 })) finish(true);
        };
        socket.onmessage = event => {
          if (closed) return;
          if (typeof event.data === 'string') {
            if (event.data.length > 2048) { finish(true); return; }
            let frame;
            try { frame = JSON.parse(event.data); } catch { finish(true); return; }
            if (frame?.type === 'ready' && !ready) {
              ready = true;
              clearTimer();
              terminal.beginConnection();
              callbacks.onOpen();
            } else if (frame?.type === 'eof' && ready) finish(false, 'session_eof');
            else finish(true);
            return;
          }
          if (!ready || !(event.data instanceof ArrayBuffer) || !event.data.byteLength ||
              event.data.byteLength > FRAME_BYTES || unrendered + event.data.byteLength > QUEUE_BYTES) {
            finish(true); return;
          }
          const bytes = event.data.byteLength;
          unrendered += bytes;
          let rendered;
          try { rendered = callbacks.onData(new Uint8Array(event.data)); }
          catch { finish(true); return; }
          Promise.resolve(rendered).then(result => {
            if (closed) return;
            if (result === false) { finish(true); return; }
            unrendered -= bytes;
            if (!sendControl({ type: 'ack', bytes })) finish(true);
          }, () => finish(true));
        };
        // A clean EOF or policy refusal must never create a reconnect loop.
        // Retry classification applies only after a ready, writable attachment.
        socket.onclose = event => finish(false, ready && [1006, 1012].includes(event?.code) ? 'connection_lost' : 'connection_closed');
        socket.onerror = () => finish(true, ready ? 'connection_lost' : 'transport_error');
      }).catch(() => finish(true));
    },
    sendInput(data) {
      if (!ready || closed || socket?.readyState !== 1) return false;
      const bytes = typeof data === 'string' ? encoder.encode(data) : data;
      if (!(bytes instanceof Uint8Array) || bytes.byteLength > QUEUE_BYTES ||
          socket.bufferedAmount + bytes.byteLength > QUEUE_BYTES) return false;
      try {
        for (let offset = 0; offset < bytes.byteLength; offset += FRAME_BYTES) {
          socket.send(bytes.slice(offset, offset + FRAME_BYTES));
        }
        return true;
      } catch { finish(true); return false; }
    },
    resize(cols, rows) {
      return ready && !closed && Number.isSafeInteger(cols) && Number.isSafeInteger(rows) &&
        cols >= 2 && cols <= 500 && rows >= 1 && rows <= 300 && sendControl({ type: 'resize', cols, rows });
    },
    close() { finish(); },
  };
}
