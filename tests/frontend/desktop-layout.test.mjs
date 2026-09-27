import test from 'node:test';
import assert from 'node:assert/strict';
import { initDesktopLayout } from '../../frontend/desktop-layout.js';

const KEY = 'botainer-dashboard:desktop-layout:v1';

class Events {
  listeners = new Map();
  addEventListener(name, fn) {
    if (!this.listeners.has(name)) this.listeners.set(name, new Set());
    this.listeners.get(name).add(fn);
  }
  removeEventListener(name, fn) { this.listeners.get(name)?.delete(fn); }
  emit(name, fields = {}) {
    const event = { type: name, prevented: false, stopped: false,
      preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; }, ...fields };
    for (const fn of this.listeners.get(name) || []) fn(event);
    return event;
  }
  count() { return [...this.listeners.values()].reduce((n, listeners) => n + listeners.size, 0); }
}

class Element extends Events {
  hidden = false;
  disabled = false;
  attributes = new Map();
  classes = new Set();
  properties = new Map();
  classList = {
    contains: value => this.classes.has(value),
    add: (...values) => values.forEach(value => this.classes.add(value)),
    remove: (...values) => values.forEach(value => this.classes.delete(value)),
    toggle: (value, enabled) => {
      if (enabled ?? !this.classes.has(value)) this.classes.add(value);
      else this.classes.delete(value);
    },
  };
  style = { setProperty: (key, value) => this.properties.set(key, value),
    removeProperty: key => this.properties.delete(key) };
  setAttribute(key, value) { this.attributes.set(key, value); }
  getAttribute(key) { return this.attributes.get(key); }
  focus() { this.focused = true; }
  setPointerCapture(id) { this.captured = id; }
  releasePointerCapture(id) { if (this.captured === id) this.captured = null; }
}

function fixture({ width = 1440, stored, storage } = {}) {
  const document = new Events();
  const view = new Events();
  const elements = Object.fromEntries(['app', 'sidebar', 'inspector', 'dock-inspector', 'sidebar-resizer', 'inspector-resizer']
    .map(id => [id, new Element()]));
  elements.inspector.hidden = true;
  document.getElementById = id => elements[id];
  document.documentElement = { clientWidth: width };
  document.defaultView = view;
  const values = new Map(stored === undefined ? [] : [[KEY, stored]]);
  const writes = [];
  const memory = storage ?? {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => { values.set(key, value); writes.push([key, value]); },
    removeItem: key => values.delete(key),
  };
  let resized = 0;
  const layout = initDesktopLayout({ document, storage: memory, onResize: () => resized++ });
  return { document, view, elements, layout, values, writes, resized: () => resized,
    saved: () => JSON.parse(values.get(KEY)),
    width(value) { document.documentElement.clientWidth = value; view.emit('resize'); },
    open() { elements.inspector.hidden = false; return layout.refresh(); } };
}

const arrow = (element, key, extra = {}) => element.emit('keydown', { key, ...extra });
const down = (element, x, extra = {}) => element.emit('pointerdown', { button: 0, pointerId: 7, clientX: x, ...extra });

test('defaults keep the inspector as an overlay and expose keyboard-operable widths', () => {
  const f = fixture();
  assert.equal(f.layout.refresh().docked, false);
  assert.equal(f.elements['sidebar-resizer'].hidden, false);
  assert.equal(f.elements['inspector-resizer'].hidden, true);
  assert.equal(f.elements['sidebar-resizer'].getAttribute('aria-valuenow'), '248');
  assert.equal(f.elements['sidebar-resizer'].getAttribute('aria-valuetext'), '248 pixels');
  const before = f.resized();
  f.layout.refresh();
  assert.equal(f.resized(), before, 'unchanged state does not repeat terminal measurements');
  f.open();
  assert.equal(f.elements['inspector-resizer'].hidden, false);
  assert.equal(f.elements['dock-inspector'].getAttribute('aria-pressed'), 'false');
  assert.equal(f.elements.app.style !== undefined, true);
  assert.equal(f.writes.length, 0, 'opening a panel does not rewrite preferences');
});

test('stored widths are bounded and malformed or oversized preferences do not break layout', () => {
  const bounded = fixture({ stored: JSON.stringify({ version: 1, sidebarWidth: 9000, inspectorWidth: -5, docked: 'true' }) });
  assert.equal(bounded.layout.refresh().sidebarWidth, 440);
  assert.equal(bounded.open().inspectorWidth, 320);
  assert.equal(bounded.layout.refresh().docked, false);
  for (const stored of ['{', 'null', '[]', 'x'.repeat(513), JSON.stringify({ version: 2, sidebarWidth: 440 }),
    JSON.stringify({ version: 1, sidebarWidth: '440', inspectorWidth: null })]) {
    const f = fixture({ stored });
    assert.equal(f.layout.refresh().sidebarWidth, 248);
    assert.equal(f.open().inspectorWidth, 520);
    f.layout.dispose();
  }
});

test('keyboard arrows follow the separator direction and Home/End report actual bounds', () => {
  const f = fixture();
  f.open();
  const left = f.elements['sidebar-resizer'], right = f.elements['inspector-resizer'];
  assert.equal(arrow(left, 'ArrowRight').prevented, true);
  assert.equal(f.saved().sidebarWidth, 258);
  arrow(left, 'ArrowLeft', { shiftKey: true });
  assert.equal(f.saved().sidebarWidth, 218);
  arrow(right, 'ArrowLeft');
  assert.equal(f.saved().inspectorWidth, 530);
  arrow(right, 'ArrowRight', { shiftKey: true });
  assert.equal(f.saved().inspectorWidth, 490);
  arrow(left, 'Home'); arrow(right, 'End');
  assert.equal(f.saved().sidebarWidth, 200);
  assert.equal(f.saved().inspectorWidth, 800);
  assert.equal(right.getAttribute('aria-valuenow'), right.getAttribute('aria-valuemax'));
  const writes = f.writes.length;
  assert.equal(arrow(left, 'ArrowRight', { ctrlKey: true }).prevented, false);
  assert.equal(arrow(left, 'p', { metaKey: true, shiftKey: true }).prevented, false);
  assert.equal(f.writes.length, writes);
});

test('pointer drag is bounded, ignores other pointers and persists only when completed', () => {
  const f = fixture();
  const left = f.elements['sidebar-resizer'];
  assert.equal(down(left, 248).prevented, true);
  assert.equal(left.focused, true);
  assert.equal(left.captured, 7);
  f.document.emit('pointermove', { pointerId: 8, clientX: 9999 });
  assert.equal(f.layout.refresh().sidebarWidth, 248);
  f.document.emit('pointermove', { pointerId: 7, clientX: 9999 });
  assert.equal(f.layout.refresh().sidebarWidth, 440);
  assert.equal(f.writes.length, 0);
  f.document.emit('pointerup', { pointerId: 7 });
  assert.equal(f.saved().sidebarWidth, 440);
  assert.equal(left.captured, null);
  assert.equal(f.elements.app.classList.contains('pane-resizing'), false);
  assert.equal(down(left, 400, { button: 2 }).prevented, false);
});

test('cancel, escape and window blur restore the width without saving a partial drag', () => {
  for (const end of ['pointercancel', 'Escape', 'blur']) {
    const f = fixture();
    f.open();
    const right = f.elements['inspector-resizer'];
    down(right, 700);
    f.document.emit('pointermove', { pointerId: 7, clientX: 500 });
    assert.equal(f.layout.refresh().inspectorWidth, 720);
    if (end === 'Escape') assert.equal(arrow(right, 'Escape').stopped, true);
    else if (end === 'blur') f.view.emit('blur');
    else f.document.emit(end, { pointerId: 7 });
    assert.equal(f.layout.refresh().inspectorWidth, 520);
    assert.equal(f.writes.length, 0);
    f.layout.dispose();
  }
});

test('docking preserves at least 420px of terminal and restores saved widths after a narrow window', () => {
  const f = fixture({ width: 1000, stored: JSON.stringify({ version: 1, sidebarWidth: 440, inspectorWidth: 520, docked: false }) });
  f.open();
  f.elements['dock-inspector'].emit('click');
  let state = f.layout.refresh();
  assert.equal(state.docked, true);
  assert.equal(state.sidebarWidth, 254);
  assert.equal(state.inspectorWidth, 320);
  assert.equal(state.width - state.sidebarWidth - 6 - state.inspectorWidth, 420);
  assert.equal(f.saved().sidebarWidth, 440, 'temporary clamping must not destroy the saved width');
  assert.equal(f.saved().inspectorWidth, 520);
  f.width(1440);
  state = f.layout.refresh();
  assert.equal(state.sidebarWidth, 440);
  assert.equal(state.inspectorWidth, 520);
  f.width(900);
  assert.equal(f.layout.refresh().docked, false);
  assert.equal(f.elements['dock-inspector'].textContent, 'Dock when wider');
  assert.equal(f.saved().docked, true);
  f.width(1440);
  assert.equal(f.layout.refresh().docked, true);
});

test('mobile drawer, focus, settings and full-width files temporarily suspend docking and resizing', () => {
  const f = fixture();
  f.open(); f.elements['dock-inspector'].emit('click');
  for (const [element, className] of [[f.elements.app, 'focus-mode'], [f.elements.app, 'settings-open'],
    [f.elements.inspector, 'settings-page'], [f.elements.inspector, 'files-wide']]) {
    element.classList.add(className);
    assert.equal(f.layout.refresh().docked, false);
    assert.equal(f.elements['inspector-resizer'].hidden, true);
    assert.equal(f.elements['dock-inspector'].hidden, true);
    element.classList.remove(className);
    assert.equal(f.layout.refresh().docked, true);
  }
  f.width(360);
  assert.equal(f.layout.refresh().desktop, false);
  assert.equal(f.elements['sidebar-resizer'].tabIndex, -1);
  assert.equal(f.elements['inspector-resizer'].hidden, true);
  assert.equal(f.elements['dock-inspector'].hidden, true);
  assert.ok(f.layout.refresh().inspectorWidth <= 360);
  f.width(1440);
  assert.equal(f.layout.refresh().docked, true);
  f.elements.app.classList.add('sidebar-hidden');
  assert.equal(f.layout.refresh().sidebarVisible, false);
  assert.equal(f.elements['sidebar-resizer'].hidden, true);
  assert.equal(f.layout.refresh().docked, true);
});

test('window shrinking during a drag cancels it and preserves the prior desktop preference', () => {
  const f = fixture();
  const left = f.elements['sidebar-resizer'];
  down(left, 248);
  f.document.emit('pointermove', { pointerId: 7, clientX: 400 });
  f.width(500);
  f.document.emit('pointermove', { pointerId: 7, clientX: 440 });
  f.document.emit('pointerup', { pointerId: 7 });
  f.width(1440);
  assert.equal(f.layout.refresh().sidebarWidth, 248);
  assert.equal(f.writes.length, 0);
});

test('double-click resets a pane, reset clears saved layout, and disposal removes all listeners', () => {
  const f = fixture();
  f.open();
  arrow(f.elements['sidebar-resizer'], 'End');
  f.elements['sidebar-resizer'].emit('dblclick');
  assert.equal(f.saved().sidebarWidth, 248);
  f.elements['dock-inspector'].emit('click');
  f.layout.reset();
  assert.equal(f.values.has(KEY), false);
  assert.equal(f.layout.refresh().docked, false);
  f.layout.dispose(); f.layout.dispose();
  assert.equal(f.document.count() + f.view.count() + Object.values(f.elements).reduce((n, e) => n + e.count(), 0), 0);
  assert.equal(f.elements.app.properties.has('--sidebar'), false);
  assert.equal(f.elements['dock-inspector'].hidden, true);
  arrow(f.elements['sidebar-resizer'], 'End');
  assert.equal(f.values.has(KEY), false);
});

test('unavailable browser storage keeps layout usable with page-only changes', () => {
  const storage = { getItem() { throw new Error('synthetic denial'); }, setItem() { throw new Error('synthetic quota'); },
    removeItem() { throw new Error('synthetic denial'); } };
  const f = fixture({ storage });
  assert.doesNotThrow(() => { f.open(); arrow(f.elements['sidebar-resizer'], 'ArrowRight'); f.elements['dock-inspector'].emit('click'); });
  assert.equal(f.layout.refresh().sidebarWidth, 258);
  assert.equal(f.layout.refresh().docked, true);
  assert.doesNotThrow(() => f.layout.reset());
  const empty = initDesktopLayout({ document: { getElementById: () => null }, storage: null });
  assert.doesNotThrow(() => { empty.refresh(); empty.reset(); empty.dispose(); });
});
