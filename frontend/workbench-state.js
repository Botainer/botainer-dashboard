/** Bounded presentation preferences. Never credentials, output or terminal actions. */
const KEY = 'botainer-dashboard:workbench:v1';
const MAX_BYTES = 256 * 1024;
const MAX_VIEWS = 8;
const MAX_EXPANSIONS = 1000;
const encoder = new TextEncoder();

function defaults() {
  return { version: 1, filterId: null, projectId: null, projectWorkspaceId: null,
    selectedTarget: null, openViews: [], query: '', sort: 'recent', showEnded: false,
    projectExpansion: [], sidebarShown: true, focusMode: false };
}

function record(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value) &&
    [Object.prototype, null].includes(Object.getPrototypeOf(value));
}

// Do not invoke getters or inherited/toJSON methods when selecting data fields.
function field(value, name) {
  if (value === null || typeof value !== 'object') return undefined;
  return Object.getOwnPropertyDescriptor(value, name)?.value;
}

function id(value) {
  return typeof value === 'string' && value.length >= 1 && value.length <= 160 &&
    !/[^A-Za-z0-9_.:-]/.test(value) ? value : null;
}

function workspace(value) {
  return value === undefined || value === null ? null : id(value);
}

function target(value) {
  if (!record(value)) return null;
  const contextNamespace = id(field(value, 'contextNamespace'));
  const runtimeId = id(field(value, 'runtimeId'));
  const projectId = id(field(value, 'projectId'));
  const rawWorkspace = field(value, 'workspaceId');
  const workspaceId = workspace(rawWorkspace);
  if (!contextNamespace || !runtimeId || !projectId ||
      rawWorkspace !== undefined && rawWorkspace !== null && workspaceId === null) return null;
  return { contextNamespace, runtimeId, projectId, workspaceId };
}

const targetKey = value => JSON.stringify([value.contextNamespace, value.runtimeId]);
const sameTarget = (first, second) => ['contextNamespace', 'runtimeId', 'projectId', 'workspaceId']
  .every(name => first[name] === second[name]);

function normalize(value, requireVersion = false) {
  const result = defaults();
  if (!record(value)) return result;
  const version = field(value, 'version');
  if ((requireVersion || version !== undefined) && version !== 1) return result;
  result.filterId = id(field(value, 'filterId'));
  result.projectId = id(field(value, 'projectId'));
  result.projectWorkspaceId = workspace(field(value, 'projectWorkspaceId'));
  const rawWorkspace = field(value, 'projectWorkspaceId');
  if (rawWorkspace !== undefined && rawWorkspace !== null && result.projectWorkspaceId === null) result.projectId = null;
  if (result.projectId === null) result.projectWorkspaceId = null;
  const query = field(value, 'query');
  if (typeof query === 'string') result.query = query.slice(0, 200).replace(/[\u0000-\u001f\u007f]/g, '');
  const sort = field(value, 'sort');
  if (['recent', 'name', 'active'].includes(sort)) result.sort = sort;
  for (const name of ['showEnded', 'sidebarShown', 'focusMode']) {
    const item = field(value, name);
    if (typeof item === 'boolean') result[name] = item;
  }
  result.selectedTarget = target(field(value, 'selectedTarget'));
  const views = field(value, 'openViews');
  if (Array.isArray(views)) {
    const unique = new Map();
    for (let index = 0; index < Math.min(views.length, MAX_VIEWS); index++) {
      const item = target(field(views, String(index)));
      if (!item) continue;
      const key = targetKey(item);
      if (!unique.has(key)) unique.set(key, item);
      else if (unique.get(key) === null || !sameTarget(unique.get(key), item)) unique.set(key, null);
    }
    result.openViews = [...unique.values()].filter(Boolean);
  }
  const expansion = field(value, 'projectExpansion');
  if (Array.isArray(expansion)) {
    const seen = new Set();
    for (let index = 0; index < Math.min(expansion.length, MAX_EXPANSIONS); index++) {
      const pair = field(expansion, String(index));
      if (!Array.isArray(pair) || pair.length !== 2) continue;
      const projectId = id(field(pair, '0')), expanded = field(pair, '1');
      if (!projectId || typeof expanded !== 'boolean' || seen.has(projectId)) continue;
      seen.add(projectId); result.projectExpansion.push([projectId, expanded]);
    }
  }
  return result;
}

export function readWorkbenchPreferences(storage) {
  try {
    if (storage === undefined) storage = globalThis.localStorage;
    const text = storage?.getItem(KEY);
    // Check character count before allocating an encoded copy, then bound bytes.
    if (typeof text !== 'string' || text.length > MAX_BYTES || encoder.encode(text).byteLength > MAX_BYTES) return defaults();
    return normalize(JSON.parse(text), true);
  } catch { return defaults(); }
}

export function writeWorkbenchPreferences(storage, state) {
  try {
    if (storage === undefined) storage = globalThis.localStorage;
    if (!storage || typeof storage.setItem !== 'function') return false;
    const text = JSON.stringify(normalize(state));
    if (encoder.encode(text).byteLength > MAX_BYTES) return false;
    storage.setItem(KEY, text);
    return true;
  } catch { return false; }
}

// A duplicated identity is ambiguous, even when the two rows look identical.
function uniqueRows(rows, keyFor) {
  const result = new Map();
  if (!Array.isArray(rows)) return result;
  for (let index = 0; index < rows.length; index++) {
    const item = field(rows, String(index));
    if (!record(item)) continue;
    const key = keyFor(item);
    if (key !== null) result.set(key, result.has(key) ? null : item);
  }
  return result;
}

export function reconcileWorkbenchPreferences(preferences, snapshot) {
  const result = normalize(preferences, true);
  const fresh = record(snapshot) && field(snapshot, 'stale') !== true;
  const workspaces = uniqueRows(fresh ? field(snapshot, 'workspaces') : null, item => id(field(item, 'id')));
  const combined = fresh && field(snapshot, 'workspaces') !== undefined;
  const projects = uniqueRows(fresh ? field(snapshot, 'projects') : null, item => id(field(item, 'id')));
  const sessions = uniqueRows(fresh ? field(snapshot, 'sessions') : null, item => {
    const identity = target(item);
    return identity ? targetKey(identity) : null;
  });
  const workspaceFresh = workspaceId => {
    if (!combined) return workspaceId === null;
    const item = workspaceId !== null && workspaces.get(workspaceId);
    return Boolean(item && field(item, 'stale') !== true &&
      (field(item, 'status') === undefined || field(item, 'status') === 'available'));
  };
  const matchingProject = (projectId, workspaceId) => {
    const item = projects.get(projectId);
    const rawWorkspace = item && field(item, 'workspaceId');
    return Boolean(item && field(item, 'stale') !== true &&
      (rawWorkspace === undefined || rawWorkspace === null || id(rawWorkspace) !== null) &&
      workspace(rawWorkspace) === workspaceId && workspaceFresh(workspaceId));
  };
  result.filterId = result.filterId && workspaces.get(result.filterId) ? result.filterId : null;
  if (!matchingProject(result.projectId, result.projectWorkspaceId)) {
    result.projectId = null;
    result.projectWorkspaceId = null;
  }
  result.openViews = result.openViews.filter(identity => {
    const item = sessions.get(targetKey(identity));
    const observed = item && target(item);
    return observed && sameTarget(identity, observed) && field(item, 'stale') !== true &&
      matchingProject(identity.projectId, identity.workspaceId);
  });
  if (!result.selectedTarget || result.selectedTarget.projectId !== result.projectId ||
      result.selectedTarget.workspaceId !== result.projectWorkspaceId ||
      !result.openViews.some(identity => sameTarget(identity, result.selectedTarget))) result.selectedTarget = null;
  result.projectExpansion = result.projectExpansion.filter(([projectId]) => {
    const item = projects.get(projectId);
    return item && matchingProject(projectId, workspace(field(item, 'workspaceId')));
  });
  return result;
}
