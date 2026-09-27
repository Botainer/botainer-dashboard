// Browser-only pane preferences. Resizing never sends terminal input or changes
// a project/session selection. Narrow windows temporarily clamp saved widths.
const STORAGE_KEY = 'botainer-dashboard:desktop-layout:v1';
const DEFAULTS = Object.freeze({ version: 1, sidebarWidth: 248, inspectorWidth: 520, docked: false });
const SIDEBAR = Object.freeze({ min: 200, max: 440 });
const INSPECTOR = Object.freeze({ min: 320, max: 800 });
const MOBILE_WIDTH = 850;
const TERMINAL_MIN = 420;
const SEPARATOR_WIDTH = 6;
const finite = value => typeof value === 'number' && Number.isFinite(value);
const clamp = (value, min, max) => Math.round(Math.max(min, Math.min(max, value)));

function readPreferences(storage) {
  try {
    const text = storage?.getItem(STORAGE_KEY);
    if (typeof text !== 'string' || text.length > 512) return { ...DEFAULTS };
    const value = JSON.parse(text);
    if (!value || value.version !== 1 || Array.isArray(value)) return { ...DEFAULTS };
    return { ...DEFAULTS,
      sidebarWidth: finite(value.sidebarWidth) ? clamp(value.sidebarWidth, SIDEBAR.min, SIDEBAR.max) : DEFAULTS.sidebarWidth,
      inspectorWidth: finite(value.inspectorWidth) ? clamp(value.inspectorWidth, INSPECTOR.min, INSPECTOR.max) : DEFAULTS.inspectorWidth,
      docked: value.docked === true };
  } catch { return { ...DEFAULTS }; }
}

export function initDesktopLayout({ document = globalThis.document, storage, onResize = () => {} } = {}) {
  if (storage === undefined) {
    try { storage = globalThis.localStorage; } catch { storage = null; }
  }
  const app = document?.getElementById('app');
  const sidebar = document?.getElementById('sidebar');
  const inspector = document?.getElementById('inspector');
  const dockButton = document?.getElementById('dock-inspector');
  const handles = { sidebar: document?.getElementById('sidebar-resizer'),
    inspector: document?.getElementById('inspector-resizer') };
  if (!app || !sidebar || !inspector || !dockButton || !handles.sidebar || !handles.inspector) {
    return { refresh() {}, reset() {}, dispose() {} };
  }
  const view = document.defaultView;
  let preferences = readPreferences(storage), state, signature, drag = null, disposed = false;
  const listeners = [];
  const listen = (target, name, fn) => {
    target?.addEventListener(name, fn);
    listeners.push(() => target?.removeEventListener(name, fn));
  };
  const persist = () => {
    try { storage?.setItem(STORAGE_KEY, JSON.stringify(preferences)); } catch { /* Page-only preference. */ }
  };
  const has = name => app.classList.contains(name);

  function setHandle(handle, visible, width, min, max) {
    handle.hidden = !visible;
    handle.tabIndex = visible ? 0 : -1;
    handle.setAttribute('aria-valuemin', String(min));
    handle.setAttribute('aria-valuemax', String(max));
    handle.setAttribute('aria-valuenow', String(width));
    handle.setAttribute('aria-valuetext', `${width} pixels`);
    handle.title = 'Drag to resize; arrow keys adjust width. Double-click to reset.';
  }

  function refresh() {
    if (disposed) return state;
    const measured = document.documentElement?.clientWidth || view?.innerWidth || app.clientWidth;
    const width = finite(measured) && measured > 0 ? Math.round(measured) : 1024;
    const desktop = width > MOBILE_WIDTH;
    const sidebarVisible = desktop && !sidebar.hidden && !has('sidebar-hidden') && !has('focus-mode') && !has('settings-open');
    const panelAvailable = !inspector.hidden && !has('focus-mode') && !has('settings-open') &&
      !inspector.classList.contains('settings-page') && !inspector.classList.contains('files-wide');
    const roomToDock = desktop && width >= TERMINAL_MIN + INSPECTOR.min + (sidebarVisible ? SIDEBAR.min + SEPARATOR_WIDTH : 0);
    const docked = panelAvailable && roomToDock && preferences.docked;
    const sidebarMax = Math.max(SIDEBAR.min, Math.min(SIDEBAR.max,
      width - SEPARATOR_WIDTH - TERMINAL_MIN - (docked ? INSPECTOR.min : 0)));
    const sidebarWidth = clamp(preferences.sidebarWidth, SIDEBAR.min, sidebarMax);
    const workspaceWidth = Math.max(0, width - (sidebarVisible ? sidebarWidth + SEPARATOR_WIDTH : 0));
    const inspectorMax = Math.max(0, Math.min(INSPECTOR.max, workspaceWidth - (docked ? TERMINAL_MIN : 0)));
    const inspectorMin = Math.min(INSPECTOR.min, inspectorMax);
    const inspectorWidth = clamp(preferences.inspectorWidth, inspectorMin, inspectorMax);
    state = { width, desktop, sidebarVisible, panelAvailable, docked, roomToDock,
      sidebarWidth, sidebarMin: SIDEBAR.min, sidebarMax,
      inspectorWidth, inspectorMin, inspectorMax };
    app.style.setProperty('--sidebar', `${sidebarWidth}px`);
    app.style.setProperty('--inspector', `${inspectorWidth}px`);
    app.classList.toggle('inspector-docked', docked);
    setHandle(handles.sidebar, sidebarVisible, sidebarWidth, SIDEBAR.min, sidebarMax);
    setHandle(handles.inspector, desktop && panelAvailable, inspectorWidth, inspectorMin, inspectorMax);
    dockButton.hidden = !desktop || !panelAvailable;
    dockButton.disabled = !roomToDock && !preferences.docked;
    dockButton.setAttribute('aria-pressed', String(preferences.docked));
    dockButton.textContent = preferences.docked ? (docked ? 'Undock panel' : 'Dock when wider') : 'Dock panel';
    dockButton.title = !roomToDock
      ? 'Docking needs enough room for the terminal. The panel overlays it in this window size.'
      : preferences.docked ? 'Let this panel overlay the terminal again' : 'Keep this panel beside the terminal';
    if (drag && handles[drag.kind].hidden) finishDrag(false);
    const next = JSON.stringify(state);
    if (next !== signature) {
      signature = next;
      onResize();
    }
    return state;
  }

  function setWidth(kind, width, save = false) {
    refresh();
    if (handles[kind].hidden) return;
    const key = `${kind}Width`;
    preferences[key] = clamp(width, state[`${kind}Min`], state[`${kind}Max`]);
    refresh();
    if (save) persist();
  }

  function finishDrag(save) {
    if (!drag) return;
    const current = drag;
    drag = null;
    app.classList.remove('pane-resizing');
    try { current.handle.releasePointerCapture?.(current.id); } catch { /* Capture may have ended already. */ }
    if (save) persist();
    else {
      preferences[`${current.kind}Width`] = current.previous;
      refresh();
    }
  }

  for (const [kind, handle] of Object.entries(handles)) {
    listen(handle, 'pointerdown', event => {
      refresh();
      if (drag || handle.hidden || event.button !== 0 || event.isPrimary === false || !finite(event.clientX)) return;
      event.preventDefault();
      handle.focus({ preventScroll: true });
      drag = { kind, handle, id: event.pointerId, start: event.clientX,
        width: state[`${kind}Width`], previous: preferences[`${kind}Width`] };
      app.classList.add('pane-resizing');
      try { handle.setPointerCapture?.(event.pointerId); } catch { /* Document listeners still bound the drag. */ }
    });
    listen(handle, 'lostpointercapture', event => { if (drag?.id === event.pointerId) finishDrag(false); });
    listen(handle, 'dblclick', event => {
      if (handle.hidden) return;
      event.preventDefault();
      preferences[`${kind}Width`] = DEFAULTS[`${kind}Width`];
      refresh(); persist();
    });
    listen(handle, 'keydown', event => {
      if (event.key === 'Escape' && drag) {
        event.preventDefault(); event.stopPropagation(); finishDrag(false); return;
      }
      if (event.altKey || event.ctrlKey || event.metaKey || handle.hidden ||
          !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault(); event.stopPropagation(); refresh();
      const amount = event.shiftKey ? 40 : 10;
      const direction = (event.key === 'ArrowRight' ? 1 : -1) * (kind === 'sidebar' ? 1 : -1);
      const width = event.key === 'Home' ? state[`${kind}Min`] : event.key === 'End'
        ? state[`${kind}Max`] : state[`${kind}Width`] + direction * amount;
      setWidth(kind, width, true);
    });
  }
  listen(document, 'pointermove', event => {
    if (!drag || event.pointerId !== drag.id || !finite(event.clientX)) return;
    event.preventDefault();
    const difference = (event.clientX - drag.start) * (drag.kind === 'sidebar' ? 1 : -1);
    setWidth(drag.kind, drag.width + difference);
  });
  listen(document, 'pointerup', event => { if (drag?.id === event.pointerId) finishDrag(true); });
  listen(document, 'pointercancel', event => { if (drag?.id === event.pointerId) finishDrag(false); });
  listen(view, 'blur', () => finishDrag(false));
  listen(view, 'resize', refresh);
  listen(dockButton, 'click', () => {
    refresh();
    if (dockButton.hidden || dockButton.disabled) return;
    preferences.docked = !preferences.docked;
    refresh(); persist();
  });
  const observer = view?.MutationObserver ? new view.MutationObserver(refresh) : null;
  observer?.observe(app, { attributes: true, attributeFilter: ['class'] });
  observer?.observe(inspector, { attributes: true, attributeFilter: ['class', 'hidden'] });
  refresh();
  return {
    refresh,
    reset() {
      if (disposed) return state;
      finishDrag(false);
      preferences = { ...DEFAULTS };
      try { storage?.removeItem(STORAGE_KEY); } catch { /* Page-only preference. */ }
      return refresh();
    },
    dispose() {
      if (disposed) return;
      finishDrag(false);
      disposed = true;
      observer?.disconnect();
      for (const remove of listeners) remove();
      app.classList.remove('pane-resizing', 'inspector-docked');
      app.style.removeProperty('--sidebar'); app.style.removeProperty('--inspector');
      handles.sidebar.hidden = handles.inspector.hidden = dockButton.hidden = true;
    },
  };
}
