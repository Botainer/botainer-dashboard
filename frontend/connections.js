// Setup is separate from runtime control. No selection change starts, stops or
// re-targets a live terminal. Server revisions fence edits from other browsers.
export const SETUP_LIMIT = 256 * 1024;

const HELP_GUIDE_FILES = Object.freeze({
  'installation.md': 'installation', 'start-dashboard.md': 'start',
  'getting-started.md': 'getting-started', 'ssh-setup.md': 'ssh',
  'sessions.md': 'sessions', 'configuration.md': 'configuration',
  'maintenance.md': 'maintenance', 'host-agents.md': 'host',
  'agent-setup-guide.md': 'agent', 'setup-support-matrix.md': 'support',
  'status.md': 'status',
  'alpha-testing.md': 'alpha-testing',
});

// A bounded renderer for the maintained Help documents, not a general Markdown
// engine or a project-file preview. All content becomes text or fixed DOM nodes.
// Raw HTML, images, embedded media and arbitrary relative URLs stay inert.
export function renderHelpMarkdown(markdown, { document = globalThis.document,
  guideId = 'getting-started', onNavigate, omitFirstHeading = false } = {}) {
  if (typeof markdown !== 'string' || markdown.length > SETUP_LIMIT ||
      new TextEncoder().encode(markdown).length > SETUP_LIMIT) throw new Error('This help document is too large to render.');
  const lines = markdown.replace(/\r\n?/g, '\n').split('\n');
  if (lines.length > 12000) throw new Error('This help document has too many lines to render.');
  let nodes = 0, firstHeading = true;
  const anchors = new Map(), headingCounts = new Map();
  const make = (tag, text, className) => {
    if (++nodes > 20000) throw new Error('This help document is too complex to render.');
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  };
  const literal = (parent, text) => {
    if (text) {
      if (++nodes > 20000) throw new Error('This help document is too complex to render.');
      parent.append(document.createTextNode(text));
    }
  };
  const slug = value => value.toLowerCase().replace(/[\x60*_]/g, '').replace(/[^\p{L}\p{N}\s-]/gu, '').trim().replace(/\s+/g, '-');
  const jump = anchor => {
    const heading = anchors.get(anchor);
    heading?.scrollIntoView?.({ block: 'start' });
    heading?.focus?.({ preventScroll: true });
    return Boolean(heading);
  };
  const link = (parent, label, target, depth) => {
    if (/^https:\/\//i.test(target) && !/[\s\u0000-\u001f\u007f]/u.test(target)) {
      try {
        const url = new URL(target);
        if (url.protocol === 'https:' && !url.username && !url.password) {
          const a = make('a'); a.setAttribute('href', url.href);
          a.setAttribute('target', '_blank'); a.setAttribute('rel', 'noopener noreferrer');
          inline(a, label, depth + 1, false); parent.append(a); return;
        }
      } catch { /* An invalid URL remains ordinary text. */ }
    }
    const match = /^([^#]*)(?:#([a-zA-Z0-9_-]+))?$/.exec(target);
    const file = match?.[1], anchor = match?.[2] || '';
    const destination = file === '' ? guideId : HELP_GUIDE_FILES[file];
    if (match && destination && (destination === guideId && anchor || typeof onNavigate === 'function')) {
      const b = make('button', undefined, 'help-doc-link'); b.setAttribute('type', 'button');
      inline(b, label, depth + 1, false);
      b.addEventListener('click', () => {
        if (destination === guideId && anchor) jump(anchor);
        else onNavigate?.({ guideId: destination, anchor });
      });
      parent.append(b); return;
    }
    inline(parent, label, depth + 1, false);
    // Keep unavailable source references useful without creating broken links.
    if (/^(?:[A-Za-z0-9_.-]+\/)*[A-Za-z0-9_.-]+\.(?:md|json|py)(?:#[A-Za-z0-9_-]+)?$/.test(target) ||
        /^(?:\.\.\/)+[A-Za-z0-9_./-]+\.(?:md|json|py)(?:#[A-Za-z0-9_-]+)?$/.test(target)) {
      const reference = make('span', undefined, 'help-source-reference');
      literal(reference, ' (source: '); reference.append(make('code', target)); literal(reference, ')'); parent.append(reference);
    }
  };
  function inline(parent, source, depth = 0, allowLinks = true) {
    if (depth > 8) { literal(parent, source); return; }
    // Bounded alternatives avoid rescanning arbitrarily long malformed markup.
    const tokens = /\\([\\\x60*_[\]{}()#+.!|>-])|(\x60{1,12})([^\n]{0,8192}?)\2(?!\x60)|(!?)\[([^\]\n]{1,512})\]\(([^)\n]{1,2048})\)|\*\*([^*\n]{1,4096})\*\*|__([^_\n]{1,4096})__|\*([^*\n]{1,4096})\*/g;
    let offset = 0, match;
    while ((match = tokens.exec(source))) {
      literal(parent, source.slice(offset, match.index));
      if (match[1]) literal(parent, match[1]);
      else if (match[2]) parent.append(make('code', match[3]));
      else if (match[5]) {
        if (match[4] || !allowLinks) literal(parent, match[0]);
        else link(parent, match[5], match[6], depth);
      } else {
        const child = make(match[7] || match[8] ? 'strong' : 'em');
        inline(child, match[7] || match[8] || match[9], depth + 1, allowLinks); parent.append(child);
      }
      offset = tokens.lastIndex;
    }
    literal(parent, source.slice(offset));
  }
  const tableCells = line => {
    const cells = []; let cell = '', codeLength = 0;
    for (let i = 0; i < line.length; i++) {
      if (line[i] === '\\' && i + 1 < line.length) { cell += line[i] + line[++i]; continue; }
      if (line.charCodeAt(i) === 96) {
        let end = i + 1; while (line.charCodeAt(end) === 96) end++;
        const length = end - i;
        if (!codeLength) codeLength = length; else if (codeLength === length) codeLength = 0;
        cell += line.slice(i, end); i = end - 1; continue;
      }
      if (line[i] === '|' && !codeLength) { cells.push(cell.trim()); cell = ''; }
      else cell += line[i];
    }
    cells.push(cell.trim());
    if (cells[0] === '') cells.shift();
    if (cells.at(-1) === '') cells.pop();
    return cells;
  };
  const fence = line => /^ {0,3}(\x60{3,}|~{3,})(.*)$/.exec(line);
  const listItem = line => /^( *)([-+*]|\d+[.)])([ \t]+)(.*)$/.exec(line);
  const heading = line => /^ {0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?$/.exec(line);
  function blocks(parent, source, depth = 0) {
    if (depth > 12) { parent.append(make('pre', source.join('\n'))); return; }
    let i = 0;
    while (i < source.length) {
      if (!source[i].trim()) { i++; continue; }
      const codeFence = fence(source[i]), title = heading(source[i]), item = listItem(source[i]);
      if (codeFence) {
        const delimiter = codeFence[1], content = []; i++;
        while (i < source.length) {
          const end = source[i].trim();
          if (end.length >= delimiter.length && [...end].every(c => c === delimiter[0])) { i++; break; }
          content.push(source[i++]);
        }
        const pre = make('pre'), code = make('code', content.join('\n')); pre.tabIndex = 0;
        pre.setAttribute('aria-label', 'Code example'); pre.append(code); parent.append(pre); continue;
      }
      if (title) {
        const base = slug(title[2]) || 'section', count = headingCounts.get(base) || 0;
        headingCounts.set(base, count + 1);
        const anchor = base + (count ? '-' + count : '');
        const h = make('h' + Math.min(6, title[1].length + (omitFirstHeading ? 2 : 1))); inline(h, title[2]);
        h.id = 'help-doc-' + (Object.values(HELP_GUIDE_FILES).includes(guideId) ? guideId : 'guide') + '-' + anchor;
        h.tabIndex = -1; anchors.set(anchor, h);
        if (!(firstHeading && title[1].length === 1 && omitFirstHeading)) parent.append(h);
        else anchors.set(anchor, parent);
        firstHeading = false; i++; continue;
      }
      const headers = tableCells(source[i]), rules = i + 1 < source.length ? tableCells(source[i + 1]) : [];
      if (source[i].includes('|') && headers.length && headers.length === rules.length && rules.every(cell => /^:?-{3,}:?$/.test(cell))) {
        const wrap = make('div', undefined, 'help-table-scroll'); wrap.tabIndex = 0;
        wrap.setAttribute('role', 'region'); wrap.setAttribute('aria-label', 'Reference table');
        const table = make('table'), thead = make('thead'), tr = make('tr'), tbody = make('tbody');
        for (const value of headers) { const cell = make('th'); cell.setAttribute('scope', 'col'); inline(cell, value); tr.append(cell); }
        thead.append(tr); table.append(thead, tbody); wrap.append(table); parent.append(wrap); i += 2;
        while (i < source.length && source[i].trim() && source[i].includes('|')) {
          const values = tableCells(source[i]); if (values.length > headers.length) break;
          const row = make('tr');
          for (let column = 0; column < headers.length; column++) {
            const cell = make('td'); inline(cell, values[column] || '');
            if (/^:.*:$/.test(rules[column])) cell.className = 'help-cell-center';
            else if (rules[column].endsWith(':')) cell.className = 'help-cell-right';
            row.append(cell);
          }
          tbody.append(row); i++;
        }
        continue;
      }
      if (item && !item[1]) {
        const ordered = /^\d/.test(item[2]), list = make(ordered ? 'ol' : 'ul');
        if (ordered && parseInt(item[2], 10) !== 1) list.setAttribute('start', String(parseInt(item[2], 10)));
        while (i < source.length) {
          const next = listItem(source[i]);
          if (!next || next[1] || /^\d/.test(next[2]) !== ordered) break;
          const indent = next[2].length + next[3].length, itemLines = [next[4]]; i++;
          while (i < source.length) {
            if (!source[i].trim()) { itemLines.push(''); i++; continue; }
            const spaces = /^ */.exec(source[i])[0].length;
            if (spaces >= indent) { itemLines.push(source[i].slice(indent)); i++; continue; }
            if (itemLines.at(-1) !== '' && !listItem(source[i]) && !heading(source[i]) && !fence(source[i])) {
              itemLines.push(source[i++]); continue;
            }
            break;
          }
          const li = make('li'); blocks(li, itemLines, depth + 1); list.append(li);
        }
        parent.append(list); continue;
      }
      const paragraph = [source[i++]];
      while (i < source.length && source[i].trim() && !heading(source[i]) && !fence(source[i]) && !listItem(source[i])) {
        if (i + 1 < source.length && source[i].includes('|') && tableCells(source[i + 1]).every(cell => /^:?-{3,}:?$/.test(cell))) break;
        paragraph.push(source[i++]);
      }
      const p = make('p'); inline(p, paragraph.map(line => line.trim()).join(' ')); parent.append(p);
    }
  }
  const root = make('div', undefined, 'help-document'); root.tabIndex = -1;
  blocks(root, lines);
  // Used by the Help panel after loading a cross-document section link.
  root.navigateToSection = jump;
  return root;
}

export function sshSnippet({ alias, host, user, port = '22', identity = '' }) {
  if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$/.test(alias || '') ||
      !/^[A-Za-z0-9][A-Za-z0-9.:-]{0,252}$/.test(host || '') ||
      !/^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$/.test(user || '') ||
      !/^\d{1,5}$/.test(String(port)) || +port < 1 || +port > 65535 ||
      identity && !/^(?:\/|~\/)[A-Za-z0-9_./-]+$/.test(identity)) {
    throw new Error('Use a simple alias, host name, login name and port (1–65535). An optional key path must start with / or ~/ and contain no spaces or shell syntax.');
  }
  return `Host ${alias}\n    HostName ${host}\n    User ${user}\n    Port ${port}\n` +
    (identity ? `    IdentityFile ${identity}\n    IdentitiesOnly yes\n` : '') +
    '    ForwardAgent no\n    ForwardX11 no\n    ClearAllForwardings yes\n    ControlMaster auto\n    ControlPath ~/.ssh/dashboard-%C\n    ControlPersist 15m\n    ServerAliveInterval 30\n    ServerAliveCountMax 3\n';
}

export function connectionRows(saved) {
  const desired = new Map(saved.entries.map(entry => [entry.id, entry]));
  const active = new Map(saved.active.map(entry => [entry.id, entry]));
  return [...new Set([...desired.keys(), ...active.keys()])].map(id => {
    const entry = desired.get(id), running = active.get(id);
    const same = running && entry && running.profileDigest === entry.profileDigest && running.kind === entry.kind;
    return { ...(entry || running), saved: Boolean(entry), active: Boolean(running), status:
      !entry ? 'Removal saved · still loaded until restart' : !entry.enabled
        ? running ? 'Disable saved · still loaded until restart' : 'Disabled'
        : same ? 'Loaded in this dashboard' : 'Saved · loads after restart' };
  });
}

export function connectionAgentSummary(workspace) {
  if (workspace?.executionKind !== 'host' && workspace?.kind !== 'host') return '';
  const agents = Array.isArray(workspace.availableAgents) ? workspace.availableAgents : workspace.agents;
  const configured = new Set(Array.isArray(agents) ? agents.map(agent => agent?.id) : []);
  const names = [['claude', 'Claude'], ['codex', 'Codex']]
    .filter(([id]) => configured.has(id)).map(([, label]) => label);
  return `${names.length ? names.join(' + ') : 'Host'} · no container`;
}

export function connectionDisplayLabel(entry, workspace = null) {
  return workspace?.label || entry?.label || entry?.id || '';
}

export function preparationRequest(values) {
  const request = { kind: values.kind, id: values.kind === 'local' ? 'local' : values.id.trim(),
    label: values.label.trim(), project_roots: values.project_roots.split('\n').map(s => s.trim()).filter(Boolean),
    read_only_acknowledged: values.read_only_acknowledged === true };
  for (const key of ['python', 'source_root', 'state_root', 'launcher', 'control_root']) {
    if (values[key]?.trim()) request[key] = values[key].trim();
  }
  if (values.kind === 'remote') request.ssh_alias = values.ssh_alias.trim();
  else for (const key of ['docker_path', 'docker_host', 'tmux_path', 'home']) {
    if (values[key]?.trim()) request[key] = values[key].trim();
  }
  return request;
}

export function reviewedProfile(raw) {
  if (typeof raw !== 'string' || new TextEncoder().encode(raw).length > SETUP_LIMIT) throw new Error('Profile exceeds the 256 KiB limit.');
  let value;
  try { value = JSON.parse(raw); } catch { throw new Error('The prepared profile must be valid JSON.'); }
  if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error('The profile must be a JSON object.');
  return value;
}

export function createConnectionEditor(request) {
  let revision = null, candidate = null, generation = 0, hooks = {}, originalHooks = {};
  const clear = () => { generation++; candidate = null; hooks = {}; originalHooks = {}; };
  const clone = value => reviewedProfile(JSON.stringify(value));
  return {
    get generation() { return generation; },
    get hookCandidates() { return { ...hooks }; },
    invalidate: clear,
    accept(snapshot) {
      if (revision !== null && revision !== snapshot.revision) clear();
      revision = snapshot.revision;
    },
    async inspect(fields) {
      clear(); const token = generation;
      const result = await request('/api/connections/prepare', { method: 'POST', mutation: false, timeoutMs: 45000, body: { request: fields } });
      if (token !== generation) return null;
      if (result.profile) {
        candidate = { kind: result.kind, profile: clone(result.profile) };
        hooks = reviewedHookCandidates(candidate.kind, candidate.profile, result.hook_candidates);
        originalHooks = { ...candidate.profile.approved_hooks };
      }
      return result;
    },
    import(kind, raw) { clear(); candidate = { kind, profile: reviewedProfile(raw) }; return clone(candidate.profile); },
    approveHooks(approved, reviewedGeneration) {
      if (reviewedGeneration !== generation || !candidate || candidate.kind !== 'local' ||
          typeof approved !== 'boolean' || !Object.keys(hooks).length) {
        throw new Error('Inspect this local installation again before approving its hooks.');
      }
      candidate.profile = { ...candidate.profile, approved_hooks: approved ? { ...originalHooks, ...hooks } : { ...originalHooks } };
      return clone(candidate.profile);
    },
    async save(trusted) {
      if (trusted !== true || !candidate || !Number.isInteger(revision)) throw new Error('Review the exact prepared profile and confirm trust before saving.');
      const token = generation;
      const result = await request('/api/connections', { method: 'POST', body: { ...candidate, revision, trusted: true } });
      if (token === generation) candidate = null;
      revision = result.revision;
      return result;
    },
    async change(id, action, enabled) {
      if (!Number.isInteger(revision) || !['enabled', 'remove'].includes(action)) throw new Error('Refresh saved machines first.');
      const result = await request(`/api/connections/${encodeURIComponent(id)}/${action}`, {
        method: 'POST', body: action === 'remove' ? { revision, confirmed: true } : { revision, enabled } });
      revision = result.revision; return result;
    },
  };
}

// A probe result is display data, not permission. Only exact files already
// bound by the reviewed profile can be offered for a separate local opt-in.
export function reviewedHookCandidates(kind, profile, observed) {
  const mapping = value => value && typeof value === 'object' && !Array.isArray(value);
  const absolute = value => typeof value === 'string' && value.startsWith('/') &&
    !/[\\\u0000-\u001f\u007f]/.test(value) && value.slice(1).split('/').every(part => part && part !== '.' && part !== '..');
  if (kind !== 'local' || !mapping(observed) || Object.keys(observed).length > 2048) return {};
  const source = absolute(profile.source_root) ? profile.source_root + '/' : null;
  return Object.fromEntries(Object.entries(observed).filter(([path, hash]) => {
    if (!absolute(path) || typeof hash !== 'string' || !/^[a-f0-9]{64}$/.test(hash)) return false;
    const relative = source && path.startsWith(source) ? path.slice(source.length) : null;
    const pins = [];
    if (relative && mapping(profile.source_hashes) && Object.hasOwn(profile.source_hashes, relative)) pins.push(profile.source_hashes[relative]);
    if (mapping(profile.support_hashes) && Object.hasOwn(profile.support_hashes, path)) pins.push(profile.support_hashes[path]);
    return pins.length > 0 && pins.every(pin => pin === hash);
  }));
}

// This editor only changes an existing host agent's reviewed executable. It
// never imports a profile, installs an update, or launches the executable.
export function createHostAgentUpdateEditor(request) {
  let revision = null, candidate = null, generation = 0;
  return {
    get generation() { return generation; },
    invalidate() { generation++; candidate = null; },
    accept(snapshot) {
      if (revision !== snapshot.revision) { generation++; candidate = null; }
      revision = snapshot.revision;
    },
    async inspect(id, agent, path = '') {
      if (!Number.isInteger(revision)) throw new Error('Refresh saved machines before reviewing an update.');
      const token = ++generation, inspectedRevision = revision; candidate = null;
      const result = await request(`/api/connections/${encodeURIComponent(id)}/agent-update/prepare`, {
        method: 'POST', mutation: false, timeoutMs: 45000,
        body: { revision: inspectedRevision, agent, path: path.trim() },
      });
      if (token !== generation || inspectedRevision !== revision) return null;
      if (result.id !== id || result.agent !== agent || result.revision !== revision ||
          typeof result.candidate?.path !== 'string' || !result.candidate.path.startsWith('/') ||
          !/^[a-f0-9]{64}$/.test(result.candidate.sha256 || '')) {
        throw new Error('The update review is no longer current. Refresh saved machines and inspect again.');
      }
      if (result.changed === true) candidate = { id, agent, revision,
        path: result.candidate.path, sha256: result.candidate.sha256 };
      return result;
    },
    async save(confirmed) {
      if (confirmed !== true || !candidate || candidate.revision !== revision) {
        throw new Error('Inspect the installed update and confirm that you trust the reviewed executable before saving.');
      }
      const reviewed = candidate, token = generation;
      // A failed or ambiguous write requires a fresh inspection, never a retry.
      candidate = null;
      const { id, ...body } = reviewed;
      const result = await request(`/api/connections/${encodeURIComponent(id)}/agent-update`, {
        method: 'POST', body: { ...body, confirmed: true },
      });
      if (token === generation) candidate = null;
      revision = result.revision;
      return result;
    },
  };
}

// Reviews software only; the server preserves machine, account and project
// scope and reinspects exact bytes before a compare-and-swap registry write.
export function createInstallationUpdateEditor(request) {
  let revision = null, entries = [], baseline = null, candidate = null, generation = 0;
  const digest = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
  const currentError = () => new Error('The Botainer update review is no longer current. Refresh saved machines and inspect again.');
  return {
    get generation() { return generation; },
    invalidate() { generation++; candidate = null; },
    accept(snapshot) {
      if (revision !== snapshot.revision) { generation++; baseline = null; candidate = null; }
      revision = snapshot.revision; entries = snapshot.entries || [];
    },
    async load(id) {
      const token = ++generation, loadedRevision = revision;
      baseline = null; candidate = null;
      const selected = entries.find(entry => entry.id === id && ['local', 'remote'].includes(entry.kind));
      if (!Number.isInteger(revision) || !selected || !digest(selected.profileDigest)) {
        throw new Error('Refresh saved machines before reviewing a Botainer update.');
      }
      const result = await request(`/api/connections/${encodeURIComponent(id)}/installation-update`);
      if (token !== generation || loadedRevision !== revision) return null;
      if (result.id !== id || result.kind !== selected.kind || result.revision !== revision ||
          result.profileDigest !== selected.profileDigest || !result.settings || typeof result.settings !== 'object' ||
          !['python', 'source_root', ...(result.kind === 'remote' ? ['launcher'] : [])].every(key =>
            typeof result.settings[key] === 'string' && result.settings[key].startsWith('/'))) throw currentError();
      baseline = JSON.parse(JSON.stringify(result));
      return result;
    },
    async inspect(changes, acknowledged) {
      const token = ++generation; candidate = null;
      if (!baseline || baseline.revision !== revision) throw currentError();
      if (acknowledged !== true) throw new Error('Acknowledge the read-only installation check before inspecting.');
      const allowed = baseline.kind === 'remote' ? ['python', 'source_root', 'launcher'] : ['python', 'source_root'];
      if (!changes || typeof changes !== 'object' || Array.isArray(changes) ||
          Object.keys(changes).some(key => !allowed.includes(key) || typeof changes[key] !== 'string' || !changes[key].trim())) {
        throw new Error('Only installed Botainer software paths can be reviewed here.');
      }
      const reviewedChanges = Object.fromEntries(Object.entries(changes).map(([key, value]) => [key, value.trim()]));
      const selected = baseline;
      const result = await request(`/api/connections/${encodeURIComponent(selected.id)}/installation-update/prepare`, {
        method: 'POST', mutation: false, timeoutMs: 45000,
        body: { revision, profileDigest: selected.profileDigest, changes: reviewedChanges, acknowledged: true },
      });
      if (token !== generation || baseline !== selected || selected.revision !== revision) return null;
      if (result.id !== selected.id || result.kind !== selected.kind || result.revision !== revision ||
          result.profileDigest !== selected.profileDigest || (result.profile &&
          (result.profile.id !== selected.id || !digest(result.candidateDigest)))) throw currentError();
      if (result.profile && result.changed !== false) candidate = {
        id: selected.id, revision, profileDigest: selected.profileDigest,
        changes: reviewedChanges, candidateDigest: result.candidateDigest,
      };
      return result;
    },
    async save(confirmed) {
      if (confirmed !== true || !candidate || candidate.revision !== revision) {
        throw new Error('Inspect Botainer and confirm that you trust the reviewed installation before saving.');
      }
      const { id, ...body } = candidate; candidate = null;
      const result = await request(`/api/connections/${encodeURIComponent(id)}/installation-update`, {
        method: 'POST', timeoutMs: 45000, body: { ...body, acknowledged: true, confirmed: true },
      });
      baseline = null; revision = result.revision;
      return result;
    },
  };
}

export async function mountConnectionsPanel(container, { document, api, isCurrent = () => true,
  isVisible = () => true, initialPage = 'machines', onPageChange = () => {}, onInspectMachine = null,
  activeMachines = () => [], onConnectionsChange = () => {}, presentMachine = () => null, onRefreshMachines = null } = {}) {
  const el = (tag, text, cls) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (cls) element.className = cls;
    return element;
  };
  const button = (label, action, cls) => {
    const b = el('button', label, cls); b.type = 'button'; b.addEventListener('click', action); return b;
  };
  const editor = createConnectionEditor(api);
  const agentUpdateEditor = createHostAgentUpdateEditor(api);
  const installationUpdateEditor = createInstallationUpdateEditor(api);
  const page = el('section', undefined, 'connections-panel');
  const machines = el('section', undefined, 'machines-page');
  const helpPage = el('section', undefined, 'help-page');
  const status = el('p', 'Loading saved machines…', 'validation-message'); status.setAttribute('role', 'status');
  const list = el('div', undefined, 'connections-list'), workspace = el('div', undefined, 'connection-workspace');
  const overview = el('section', undefined, 'machine-overview');
  const heading = el('header', undefined, 'machines-heading');
  const title = el('h2', 'Machines'); title.tabIndex = -1;
  heading.append(title);
  overview.append(heading, el('p', 'Manage Botainer connections and host-agent profiles. Saved changes apply after a dashboard restart.'), status, list);
  workspace.hidden = true;
  machines.append(overview, workspace);
  page.append(machines, helpPage);
  container.append(page);
  let saved, busy = false, view = 0, currentPage = initialPage, formOpen = false, returnControl = null, invalidateReview = null;
  let loadedMachineSignature = null;
  const loadedHealth = new Map();
  let helpGeneration = 0, selectedGuide = null;
  const focus = element => { if (isVisible()) { element?.focus?.({ preventScroll: true }); element?.scrollIntoView?.({ block: 'nearest' }); } };
  const showError = error => { if (isCurrent()) { status.hidden = false; status.textContent = error.message; status.setAttribute('role', 'alert'); } };
  const operate = async action => {
    if (busy) return;
    busy = true; page.setAttribute('aria-busy', 'true');
    try { await action(); } catch (error) { showError(error); }
    finally { busy = false; page.setAttribute('aria-busy', 'false'); }
  };
  const machineSignature = values => JSON.stringify(values.map(machine => [machine.id, machine.label,
    machine.kind, machine.executionKind, Array.isArray(machine.availableAgents)
      ? machine.availableAgents.map(agent => [agent?.id, agent?.available, agent?.unavailableReason]) : null,
    presentMachine(machine)]));
  function renderHealth(state, health) {
    state.replaceChildren();
    if (!health) return;
    state.append(el('strong', health.summary));
    if (health.message) state.append(el('p', health.message));
    if (health.recovery) state.append(el('p', `Next: ${health.recovery}`));
    if (health.command) {
      const command = el('p', 'In your terminal: '); command.append(el('code', health.command)); state.append(command);
    }
  }
  function update(result, notify = true) {
    const previousGeneration = editor.generation;
    saved = result; editor.accept(result); agentUpdateEditor.accept(result); installationUpdateEditor.accept(result);
    if (previousGeneration !== editor.generation) invalidateReview?.();
    status.setAttribute('role', 'status');
    status.textContent = (result.pendingRestart ? 'Restart needed. ' : '') + result.notice;
    status.hidden = result.enabled && !result.pendingRestart;
    list.replaceChildren();
    loadedHealth.clear();
    const loadedMachines = new Map(activeMachines().map(machine => [machine.id, machine]));
    loadedMachineSignature = machineSignature([...loadedMachines.values()]);
    const loadedEntries = new Map(result.active.map(entry => [entry.id, entry]));
    const rows = result.enabled ? connectionRows(result) : [...loadedMachines.values()].map(machine => ({
      id: machine.id, label: machine.label, kind: machine.executionKind === 'host' ? 'host' : machine.kind === 'local' ? 'local' : 'remote',
      active: true, saved: false, status: 'Loaded · managed by startup flags',
    }));
    for (const row of rows) {
      const runtime = loadedMachines.get(row.id), activeEntry = loadedEntries.get(row.id);
      // Only merge availability for the exact revision currently loaded. A
      // pending update must not inherit the old executable's stale status.
      const matching = row.active && activeEntry?.profileDigest === row.profileDigest;
      const entry = row.kind === 'host' && Array.isArray(row.agents) && matching
        ? { ...row, agents: row.agents.map(agent => {
          const observed = runtime?.availableAgents?.find(item => item.id === agent.id && item.executable === agent.path);
          return observed ? { ...agent, available: observed.available, unavailableReason: observed.unavailableReason } : agent;
        }) } : row;
      const loaded = entry.active ? loadedMachines.get(entry.id) || loadedEntries.get(entry.id) : null;
      const label = connectionDisplayLabel(entry, loaded);
      const summary = connectionAgentSummary(loaded || entry);
      const health = entry.active && runtime ? presentMachine(runtime) : null;
      const card = el('section', undefined, `connection-card${summary ? ' host-connection' : ''}`);
      const identity = el('div', undefined, 'connection-identity');
      identity.append(el('h3', label), el('p', `${summary ? `⚠ ${summary}` : (loaded || entry).kind === 'local' ? 'This computer · Botainer' : 'SSH · Botainer'} · ${entry.id}`), el('strong', entry.status, entry.status.includes('restart') ? 'pending-restart' : 'connection-loaded'));
      if (health) {
        const state = el('div', undefined, 'connection-health');
        renderHealth(state, health); loadedHealth.set(entry.id, state);
        identity.append(state);
      }
      if (entry.saved && entry.label !== label) identity.append(el('p', `Saved name: ${entry.label}. The name above matches the loaded connection in the sidebar.`, 'connection-saved-name'));
      if (!entry.active && entry.enabled) identity.append(el('p', 'Saved only · not available for projects or sessions yet. Restart the dashboard service to load this profile; reloading the browser is not enough.', 'pending-restart'));
      if (entry.profilePath) {
        const paths = el('details'); paths.append(el('summary', 'Prepared profile'), el('pre', entry.profilePath)); identity.append(paths);
      }
      if (entry.kind === 'host' && Array.isArray(entry.agents)) {
        for (const agent of entry.agents.filter(agent => agent.available === false)) {
          identity.append(el('p', `${agent.label || agent.id}: update review needed. ${agent.unavailableReason || 'The reviewed executable is unavailable or has changed.'}`, 'host-warning'));
        }
      }
      const actions = el('div', undefined, 'connection-row-actions');
      if (health?.recovery && onRefreshMachines) actions.append(button('Check again', () => operate(async () => {
        await onRefreshMachines(); refreshActiveMachines();
      }), 'quiet'));
      if (health?.recovery && onInspectMachine) actions.append(button('Connection help', () => onInspectMachine(entry.id), 'quiet'));
      if (!health?.recovery && entry.active && onInspectMachine) actions.append(button(summary ? 'Details & agent help' : 'Details & Botainer help', () => onInspectMachine(entry.id), 'quiet'));
      if (entry.saved) {
        if (entry.kind === 'host' && entry.agents?.some(agent => ['claude', 'codex'].includes(agent.id))) {
          actions.append(button('Review agent update…', () => { if (!busy) reviewAgentUpdate(entry); }));
        }
        if (['local', 'remote'].includes(entry.kind)) {
          actions.append(button('Review Botainer update…', () => { if (!busy) reviewInstallationUpdate(entry); }));
        }
        actions.append(button(entry.enabled ? 'Disable…' : 'Enable…', () => confirmChange(entry, 'enabled')),
          button('Remove…', () => confirmChange(entry, 'remove'), 'quiet'));
      }
      card.append(identity, actions);
      list.append(card);
    }
    if (!rows.length) list.append(el('p', result.enabled ? 'No machines saved yet. Choose Add machine to connect this computer or an SSH cluster.' : 'No loaded machine details are available in this launch.'));
    add.disabled = !result.enabled; advanced.disabled = !result.enabled;
    if (notify) onConnectionsChange(result);
  }
  function refreshActiveMachines() {
    if (!saved || machineSignature(activeMachines()) === loadedMachineSignature) return;
    // Health must recover visibly even while a user keeps focus on a card action.
    // Update only text here; the existing action controls retain their identity.
    for (const machine of activeMachines()) {
      const state = loadedHealth.get(machine.id);
      if (state) renderHealth(state, presentMachine(machine));
    }
    // Polling must not remove a focused Details/Disable/Remove control between
    // keyboard or pointer events. A later refresh applies the new metadata.
    for (let node = document.activeElement; node; node = node.parentNode) {
      if (node === list) return;
    }
    update(saved, false);
  }
  function showPage(next, notify = false) {
    if (!['machines', 'help'].includes(next)) throw new Error('Unknown settings page.');
    currentPage = next;
    machines.hidden = next !== 'machines'; helpPage.hidden = next !== 'help';
    helpReturn.textContent = formOpen ? '← Back to setup' : '← Back to machines';
    if (notify && isVisible()) onPageChange(next);
    if (next === 'help' && selectedGuide === null) void help('getting-started', false);
  }
  function resetWorkspace() { view++; editor.invalidate(); agentUpdateEditor.invalidate(); installationUpdateEditor.invalidate(); workspace.replaceChildren(); return view; }
  function backToMachines({ stayInHelp = false } = {}) {
    resetWorkspace(); formOpen = false; overview.hidden = false; workspace.hidden = true;
    overview.insertBefore(status, list);
    if (stayInHelp && currentPage === 'help') { helpReturn.textContent = '← Back to machines'; return; }
    showPage('machines', true); focus(returnControl || add);
  }
  function beginWorkspace(label, origin = null) {
    resetWorkspace(); formOpen = true; returnControl = origin;
    overview.hidden = true; workspace.hidden = false; showPage('machines');
    const workspaceTitle = el('h2', label); workspaceTitle.tabIndex = -1;
    workspace.append(button('← Back to machines', backToMachines, 'quiet'), workspaceTitle, status);
    focus(workspaceTitle);
  }
  function confirmChange(entry, action) {
    if (busy) return;
    const verb = action === 'remove' ? 'Remove' : entry.enabled ? 'Disable' : 'Enable';
    beginWorkspace(`${verb} ${entry.label}?`);
    workspace.append(el('p',
      'This changes the saved selection for the next restart. It does not stop sessions, cancel jobs, delete project folders, remove Botainer or edit SSH settings. Saved profile files are retained for recovery.'),
      button(`${verb} for next restart`, () => operate(async () => {
        const result = await editor.change(entry.id, action, !entry.enabled);
        if (isCurrent()) { update(result); backToMachines({ stayInHelp: true }); }
      })), button('Cancel', backToMachines, 'quiet'));
  }
  const helpHeading = el('h2', 'Help'); helpHeading.tabIndex = -1;
  const helpReturn = button('← Back to machines', () => {
    showPage('machines', true); focus(formOpen ? workspace.children[1] : title);
  }, 'quiet');
  helpPage.append(helpReturn, helpHeading, el('p', 'Choose a guide. Machine settings and unfinished setup stay where you left them.'));
  const helpLayout = el('div', undefined, 'help-layout');
  const topics = el('nav', undefined, 'help-topics'); topics.setAttribute('aria-label', 'Help topics');
  const article = el('article', undefined, 'help-article'); article.setAttribute('aria-live', 'polite');
  const topicButtons = new Map();
  helpLayout.append(topics, article); helpPage.append(helpLayout);
  async function help(id, notify = true, anchor = '') {
    if (!topicButtons.has(id)) throw new Error('Unknown setup guide.');
    const token = ++helpGeneration; selectedGuide = id;
    helpReturn.textContent = formOpen ? '← Back to setup' : '← Back to machines';
    for (const [key, b] of topicButtons) b.setAttribute('aria-current', key === id ? 'page' : 'false');
    showPage('help', notify);
    const loading = el('h3', 'Loading guide…'); loading.tabIndex = -1;
    article.replaceChildren(loading); article.setAttribute('aria-busy', 'true'); focus(loading);
    try {
      const guide = await api(`/api/connections/help/${id}`);
      if (!isCurrent() || token !== helpGeneration) return;
      const articleTitle = el('h3', guide.title); articleTitle.tabIndex = -1;
      const rendered = renderHelpMarkdown(guide.text, { document, guideId: id, omitFirstHeading: true,
        onNavigate: next => help(next.guideId, true, next.anchor) });
      const raw = el('pre', guide.text, 'help-markdown-source'); raw.hidden = true; raw.tabIndex = 0;
      raw.setAttribute('aria-label', 'Markdown source');
      const sourceButton = button('Markdown source', () => {
        raw.hidden = !raw.hidden; rendered.hidden = !raw.hidden;
        sourceButton.textContent = raw.hidden ? 'Markdown source' : 'Rendered guide';
        sourceButton.setAttribute('aria-pressed', String(!raw.hidden));
      }, 'quiet');
      sourceButton.setAttribute('aria-pressed', 'false');
      const articleHeader = el('div', undefined, 'help-article-header'); articleHeader.append(articleTitle, sourceButton);
      // Removing the loading heading clears browser focus. Capture ownership
      // first, without stealing focus if the user moved elsewhere meanwhile.
      const restoreFocus = isVisible() && currentPage === 'help' && document.activeElement === loading;
      article.replaceChildren(articleHeader, rendered, raw);
      if (restoreFocus) {
        if (!anchor || !rendered.navigateToSection(anchor)) focus(articleTitle);
      }
    } catch (error) {
      if (isCurrent() && token === helpGeneration) article.replaceChildren(el('p', error.message, 'validation-message'),
        button('Retry guide', () => help(id)));
    } finally { if (token === helpGeneration) article.setAttribute('aria-busy', 'false'); }
  }
  const add = button('Add machine', () => { if (!busy) wizard(); }, 'primary');
  const advanced = button('Import prepared profile', () => { if (!busy) importProfile(); });
  const actions = el('div', undefined, 'connection-actions');
  const advancedMenu = el('details', undefined, 'connection-advanced'); advancedMenu.append(el('summary', 'Advanced'), advanced);
  actions.append(button('Refresh saved machines', () => operate(async () => { update(await api('/api/connections')); }), 'quiet'), advancedMenu, add);
  heading.append(actions);
  for (const [id, label] of [['getting-started', 'Getting started'], ['installation', 'Installation'], ['start', 'Start & pairing'], ['ssh', 'SSH setup'], ['sessions', 'Sessions'], ['configuration', 'Configuration'], ['maintenance', 'Botainer maintenance'], ['host', 'Host agents'], ['agent', 'Setup agent guide'], ['support', 'Support & tested routes'], ['status', 'Alpha status & limits'], ['alpha-testing', 'Try the alpha']]) {
    const b = button(label, () => help(id), 'help-topic'); topicButtons.set(id, b); topics.append(b);
  }

  function field(parent, label, { value = '', placeholder = '', multiline = false, options, required = false } = {}) {
    const wrap = el('label', label, 'connection-field');
    const input = el(options ? 'select' : multiline ? 'textarea' : 'input');
    if (options) for (const [v, title] of options) { const option = el('option', title); option.value = v; input.append(option); }
    input.value = value; input.placeholder = placeholder; input.required = required;
    input.autocomplete = 'off'; input.spellcheck = false;
    if (!options) input.maxLength = multiline ? SETUP_LIMIT : 4096;
    wrap.append(input); parent.append(wrap); return input;
  }
  function checkbox(parent, label) {
    const wrap = el('label', undefined, 'connection-checkbox'), input = el('input'); input.type = 'checkbox';
    wrap.append(input, document.createTextNode(label)); parent.append(wrap); return input;
  }
  function reviewInstallationUpdate(entry) {
    beginWorkspace(`Review Botainer update · ${entry.label}`);
    workspace.append(el('p', 'A working SSH connection does not mean the saved Botainer installation still matches. After an update, review the installed software here.'),
      el('p', 'This reads an existing installation. It does not install software, change SSH settings or start sessions. Account, project folders and existing trust scope cannot be changed here.'),
      el('p', 'Finish any pending launch or consent prompts before restarting. Saving does not activate the update or restart the dashboard.'));
    const loading = el('p', 'Loading saved installation…'); workspace.append(loading);
    const loadingView = view, loadingGeneration = installationUpdateEditor.generation + 1;
    void operate(async () => {
      try {
        const selected = await installationUpdateEditor.load(entry.id);
        if (!selected || !isCurrent() || loadingView !== view) return;
        loading.hidden = true;
        const remote = selected.kind === 'remote';
        workspace.append(el('p', remote
          ? 'After restart, this remote installation needs new browser pairing. Records from the previous installation revision remain archived; its session history and terminal controls are not transferred automatically.'
          : 'After restart, pair this browser again. The dashboard rechecks the saved installation before activation. Existing sessions retain their original terminal owner; this review does not move them.'));
        const form = el('form'); workspace.append(form);
        const labels = { python: 'Python interpreter', source_root: 'Botainer import folder', launcher: 'Botainer launcher' };
        const keys = ['python', 'source_root', ...(remote ? ['launcher'] : [])];
        const fields = Object.fromEntries(keys.map(key => [key, field(form, labels[key], { value: selected.settings[key], required: true })]));
        form.append(el('p', 'Keep these paths if the software was updated in place. If you selected another installation, enter its absolute paths. These details stay in your private dashboard settings.'));
        const acknowledged = checkbox(form, 'I allow a read-only check using this saved connection and the selected Python interpreter, which I trust.');
        const inspect = button('Inspect Botainer update', () => {}); inspect.type = 'submit'; inspect.disabled = true;
        const result = el('section'); result.setAttribute('aria-live', 'polite'); form.append(inspect, result);
        let saving = false;
        const invalidate = () => { installationUpdateEditor.invalidate(); result.replaceChildren(); };
        for (const input of Object.values(fields)) input.addEventListener('input', () => {
          acknowledged.checked = false; inspect.disabled = true; invalidate();
        });
        acknowledged.addEventListener('change', () => { invalidate(); inspect.disabled = !acknowledged.checked; });
        invalidateReview = () => {
          if (loadingView !== view || saving) return;
          result.replaceChildren(el('p', 'Saved machines changed. Return to Machines and reopen this review before inspecting again.', 'validation-message'));
          inspect.disabled = true;
        };
        form.addEventListener('submit', event => {
          event.preventDefault();
          if (!acknowledged.checked || inspect.disabled) return;
          const checkingView = view, checkingGeneration = installationUpdateEditor.generation + 1;
          void operate(async () => {
            inspect.disabled = true; result.replaceChildren();
            try {
              const changes = Object.fromEntries(keys.filter(key => fields[key].value.trim() !== selected.settings[key])
                .map(key => [key, fields[key].value.trim()]));
              const outcome = await installationUpdateEditor.inspect(changes, acknowledged.checked);
              if (!outcome || !isCurrent() || checkingView !== view) return;
              result.append(el('h3', outcome.profile ? 'Review the inspected installation' : 'Installation check did not pass'));
              for (const check of outcome.checks || []) result.append(el('p', `${check.name || 'Check'}: ${check.message || check.status || 'No result'}`));
              for (const instruction of outcome.instructions || []) result.append(el('p', String(instruction)));
              for (const summary of outcome.summary || []) result.append(el('p', String(summary)));
              if (outcome.notice) result.append(el('p', String(outcome.notice)));
              if (!outcome.profile) return;
              const paths = { python: outcome.profile.python || outcome.profile.remote_python,
                source_root: outcome.profile.source_root, launcher: outcome.profile.launcher?.path || outcome.profile.launcher };
              const details = el('dl');
              for (const key of keys) {
                details.append(el('dt', `${labels[key]} · saved`), el('dd', selected.settings[key]),
                  el('dt', `${labels[key]} · inspected`), el('dd', String(paths[key] || 'Unavailable')));
              }
              result.append(details);
              const technical = el('details'); technical.append(el('summary', 'Technical details · inspected profile'),
                el('pre', outcome.candidateDigest), el('p', 'This fingerprint binds the save to the inspected files. It does not certify that software is safe. Changed files require a fresh review.'));
              result.append(technical);
              if (outcome.changed === false) { result.append(el('p', 'No update needs saving.')); return; }
              const trust = checkbox(result, 'I reviewed these software paths and trust this installed Botainer code, including its bundled plugins.');
              const reviewedGeneration = installationUpdateEditor.generation, reviewedView = view;
              const save = button('Save update for restart', () => {
                if (save.disabled || !trust.checked || !acknowledged.checked || reviewedGeneration !== installationUpdateEditor.generation || reviewedView !== view) return;
                void operate(async () => {
                  const savingGeneration = installationUpdateEditor.generation, savingView = view;
                  saving = true; save.disabled = true; trust.disabled = true;
                  try {
                    const updated = await installationUpdateEditor.save(trust.checked);
                    if (!isCurrent()) return;
                    update(updated);
                    if (savingGeneration === installationUpdateEditor.generation && savingView === view) backToMachines({ stayInHelp: true });
                    else if (formOpen) {
                      status.hidden = false; status.textContent = 'The reviewed Botainer update was saved for restart. Your newer draft was not saved. Return to Machines and reopen the update review.';
                    }
                  } catch (error) {
                    if (isCurrent() && savingView === view && savingGeneration === installationUpdateEditor.generation) {
                      result.replaceChildren(el('p', 'The update was not confirmed. Refresh saved machines and inspect again before saving.', 'validation-message'));
                      throw error;
                    }
                  } finally { saving = false; }
                });
              }, 'primary');
              save.disabled = true;
              trust.addEventListener('change', () => { save.disabled = !trust.checked; }); result.append(save);
            } catch (error) {
              if (checkingView === view && checkingGeneration === installationUpdateEditor.generation) throw error;
            } finally { inspect.disabled = !acknowledged.checked; }
          });
        });
      } catch (error) {
        if (loadingView === view && loadingGeneration === installationUpdateEditor.generation) {
          loading.textContent = 'Could not load the saved installation. Refresh saved machines and try again.'; throw error;
        }
      }
    });
  }
  function reviewAgentUpdate(entry) {
    beginWorkspace(`Review agent update · ${entry.label}`);
    workspace.append(el('p', '⚠ HOST ACCESS: this agent runs as your OS account, outside containers. Only approve an executable from an installation you trust.', 'host-warning'),
      el('p', 'Inspect an already-installed Claude or Codex update. This reads the executable path and fingerprint; it does not install software or run the agent.'),
      el('p', 'Saving preserves this connection’s projects, history and browser pairing. It applies after a dashboard restart. Existing sessions stay with their original terminal owner.'));
    const form = el('form'); workspace.append(form);
    const agents = entry.agents.filter(agent => ['claude', 'codex'].includes(agent.id));
    const agent = field(form, 'Agent to update', { value: agents.find(agent => agent.available === false)?.id || agents[0].id,
      options: agents.map(agent => [agent.id, `${agent.id === 'claude' ? 'Claude' : 'Codex'}${agent.label && agent.label.toLowerCase() !== agent.id ? ` · ${agent.label}` : ''}`]) });
    const path = field(form, 'Installed executable (optional absolute path)', { placeholder: 'Leave blank to find the installed agent' });
    form.append(el('p', 'Normally, leave this blank. The dashboard looks for the installed agent using this connection’s command search path. If you have multiple installations, enter the one you want. Shell aliases are not used.'));
    const result = el('section'); result.setAttribute('aria-live', 'polite');
    const invalidate = () => { agentUpdateEditor.invalidate(); result.replaceChildren(); };
    agent.addEventListener('change', invalidate);
    for (const input of [agent, path]) input.addEventListener('input', invalidate);
    const inspect = button('Inspect installed update', () => {}); inspect.type = 'submit';
    form.append(inspect, result);
    form.addEventListener('submit', event => {
      event.preventDefault();
      const checkingView = view;
      void operate(async () => {
        inspect.disabled = true; result.replaceChildren();
        const checkingGeneration = agentUpdateEditor.generation + 1;
        try {
          const outcome = await agentUpdateEditor.inspect(entry.id, agent.value, path.value);
          if (!outcome || !isCurrent() || checkingView !== view) return;
          result.append(el('h3', outcome.changed ? 'Review the installed update' : 'Already using this executable'));
          const details = el('dl');
          for (const [label, value] of [['Current executable', outcome.current?.path], ['Installed executable', outcome.candidate.path]]) {
            details.append(el('dt', label), el('dd', String(value || 'Unavailable')));
          }
          result.append(details);
          const technical = el('details'), fingerprints = el('dl');
          technical.append(el('summary', 'Technical details · file fingerprints'));
          for (const [label, value] of [['Current SHA-256', outcome.current?.sha256], ['Installed SHA-256', outcome.candidate.sha256]]) {
            fingerprints.append(el('dt', label), el('dd', String(value || 'Unavailable')));
          }
          technical.append(fingerprints, el('p', 'The dashboard checks these automatically to ensure it saves the file you inspected. A fingerprint does not certify that software is safe.'));
          result.append(technical);
          if (outcome.notice) result.append(el('p', String(outcome.notice)));
          if (outcome.changed !== true) {
            result.append(el('p', 'No update needs saving.')); return;
          }
          const trust = checkbox(result, 'I reviewed this executable selection and trust this installed update for new sessions.');
          const reviewedGeneration = agentUpdateEditor.generation, reviewedView = view;
          const save = button('Save update for restart', () => {
            if (save.disabled || !trust.checked || reviewedGeneration !== agentUpdateEditor.generation || reviewedView !== view) return;
            void operate(async () => {
              const savingGeneration = agentUpdateEditor.generation, savingView = view;
              save.disabled = true; trust.disabled = true;
              try {
                const updated = await agentUpdateEditor.save(trust.checked);
                if (!isCurrent()) return;
                update(updated);
                if (savingGeneration === agentUpdateEditor.generation && savingView === view) backToMachines({ stayInHelp: true });
                else if (formOpen) { status.hidden = false; status.textContent = 'The reviewed agent update was saved for restart. Your newer draft has not been saved; inspect it separately.'; }
              } catch (error) {
                if (isCurrent() && savingView === view && savingGeneration === agentUpdateEditor.generation) {
                  result.replaceChildren(el('p', 'The update was not confirmed. Refresh saved machines and inspect again before saving.', 'validation-message'));
                  throw error;
                }
              }
            });
          }, 'primary');
          save.disabled = true;
          trust.addEventListener('change', () => { save.disabled = !trust.checked; }); result.append(save);
        } catch (error) {
          if (checkingView === view && checkingGeneration === agentUpdateEditor.generation) throw error;
        } finally { inspect.disabled = false; }
      });
    });
  }
  function review(parent, kind, profile, instructions = [], hooks = {}) {
    const reviewedGeneration = editor.generation, reviewedView = view;
    const current = () => isCurrent() && reviewedGeneration === editor.generation && reviewedView === view;
    parent.append(el('h3', 'Review before saving'), el('p', 'Checks completed does not mean ready for unattended use. This connection still needs a native preflight, launch, reconnect and stop test. Saving activates nothing until restart.'));
    if (kind === 'host') parent.append(el('p', '⚠ HOST ACCESS: agents run as your OS account, outside containers. The working folder does not limit access to your files, credentials or network.', 'host-warning'));
    const details = el('dl');
    for (const [label, value] of [['Name', profile.label], ['ID', profile.id], ['SSH alias', profile.ssh_alias],
      ['Interpreter', profile.python || profile.remote_python], ['Launcher', typeof profile.launcher === 'object' ? profile.launcher?.path : profile.launcher], ['Botainer import root', profile.source_root],
      ['Botainer state', profile.state_root], ['Allowed project folders', JSON.stringify(profile.project_roots)],
      ['Docker endpoint', profile.docker?.host], ['Dashboard control files', profile.control_root || profile.terminal_owner?.control_root]]) {
      if (value) details.append(el('dt', label), el('dd', String(value)));
    }
    parent.append(details);
    for (const instruction of instructions) parent.append(el('p', instruction));
    const eligibleHooks = editor.hookCandidates;
    let hookTrust = null;
    if (Object.keys(eligibleHooks).length) {
      const detail = el('details'); detail.open = true;
      detail.append(el('summary', 'Installed Botainer hooks — review before enabling'),
        el('p', '⚠ These plugin startup and cleanup hooks execute on this computer as your OS account, outside the container. Approve only installed code you trust. This does not approve project scripts or bypass Botainer’s launch prompts.', 'host-warning'),
        el('pre', Object.entries(eligibleHooks).map(([path, hash]) => `${path}\nSHA-256: ${hash}`).join('\n\n')));
      parent.append(detail);
      hookTrust = checkbox(parent, 'I reviewed these exact installed hooks and allow Botainer to execute them on this host.');
      parent.append(el('p', 'Optional: leave unchecked to save without new hook approvals. Projects that require these hooks will remain blocked until they are reviewed. No hooks run during setup.'));
    } else if (hooks && Object.keys(hooks).length) {
      parent.append(el('p', kind === 'local' ? 'Reported hook candidates do not match this profile’s pinned files; they cannot be approved here. Inspect the installation again.'
        : 'Local host-hook approval does not apply to this connection type.'));
    }
    const raw = el('details'), rawProfile = el('pre', JSON.stringify(profile, null, 2));
    raw.append(el('summary', 'Full prepared profile and fingerprints'), rawProfile); parent.append(raw);
    const trust = checkbox(parent, 'I reviewed this installation, tools and allowed folders and trust their code. Fingerprints are change detection, not a safety certification.');
    const save = button('Save for restart', () => operate(async () => {
      if (!current()) throw new Error('Review the exact prepared profile and confirm trust before saving.');
      const savingGeneration = editor.generation, savingView = view;
      const result = await editor.save(trust.checked);
      if (isCurrent()) {
        update(result);
        if (savingGeneration === editor.generation && savingView === view) backToMachines({ stayInHelp: true });
        else if (formOpen) { status.hidden = false; status.textContent = 'The reviewed profile was saved for restart. Your newer draft has not been saved; review it separately.'; }
      }
    }), 'primary');
    save.disabled = true; trust.addEventListener('change', () => { save.disabled = !current() || !trust.checked; }); parent.append(save);
    hookTrust?.addEventListener('change', () => {
      if (!current() || busy) return;
      try {
        rawProfile.textContent = JSON.stringify(editor.approveHooks(hookTrust.checked, reviewedGeneration), null, 2);
        trust.checked = false; save.disabled = true;
      } catch (error) { showError(error); }
    });
    invalidateReview = () => {
      trust.checked = false; trust.disabled = true; save.disabled = true;
      if (hookTrust) { hookTrust.checked = false; hookTrust.disabled = true; }
    };
  }
  function importProfile() {
    beginWorkspace('Import prepared profile', advanced);
    workspace.append(el('p', 'Use this for a separately reviewed installation or host agents. Importing verifies the profile against current loader rules; it does not install software or run a session.'));
    const kind = field(workspace, 'Profile type', { value: 'remote', options: [['remote', 'SSH Botainer'], ['local', 'Local Botainer'], ['host', '⚠ Native host agents · no container']] });
    const raw = field(workspace, 'Prepared profile JSON', { multiline: true, placeholder: 'Paste the reviewed profile file contents here. Never paste credentials.' });
    const result = el('section');
    for (const input of [kind, raw]) input.addEventListener('input', () => { editor.invalidate(); result.replaceChildren(); });
    workspace.append(button('Review profile', () => {
      if (busy) return;
      try { const profile = editor.import(kind.value, raw.value); result.replaceChildren(); review(result, kind.value, profile); }
      catch (error) { showError(error); }
    }), result);
  }
  function wizard() {
    beginWorkspace('Add machine', add);
    workspace.append(el('p', 'Use an existing Botainer installation. Checks read selected files and tool identities; local checks also query Docker. They do not install software, run Botainer hooks or start sessions.'));
    const form = el('form'); workspace.append(form);
    const fields = {};
    fields.kind = field(form, 'Where is Botainer?', { value: 'local', options: [['local', 'This computer'], ['remote', 'Remote machine over SSH']] });
    fields.label = field(form, 'Display name', { required: true, placeholder: 'This computer or Research cluster' });
    fields.id = field(form, 'Connection ID (stable, no spaces)', { value: 'local', required: true });
    const remote = el('div'); form.append(remote);
    fields.ssh_alias = field(remote, 'Existing SSH alias', { placeholder: 'research-cluster' });
    const aliasHelp = el('details'); aliasHelp.append(el('summary', 'I don’t have an SSH alias / how to sign in')); remote.append(aliasHelp);
    aliasHelp.append(el('p', 'The dashboard uses the system SSH client. First authenticate in your own terminal, including host-key verification and any password, key passphrase or MFA. An SSH authentication agent is separate from Claude or Codex. Do not enter secrets here.'));
    const aliasFields = {};
    for (const [key, label, value] of [['alias', 'New alias', 'research-cluster'], ['host', 'Host name', ''], ['user', 'SSH login name', ''], ['port', 'Port', '22'], ['identity', 'Existing private key path (optional)', '']]) {
      aliasFields[key] = field(aliasHelp, label, { value });
    }
    const snippet = el('pre'); aliasHelp.append(button('Show SSH configuration to copy', () => {
      try {
        const v = Object.fromEntries(Object.entries(aliasFields).map(([key, input]) => [key, input.value.trim()]));
        snippet.textContent = '# Review and add to ~/.ssh/config; do not replace existing entries.\n' + sshSnippet(v) + `\n# Then run in your own terminal:\nssh ${v.alias}\n\n# Leave the connection open while testing the dashboard.\n# ControlPersist is an idle timeout, not an authentication lifetime.\n# Dashboard activity can keep the shared connection open.`;
      } catch (error) { showError(error); }
    }), snippet, button('Full SSH instructions and troubleshooting', () => help('ssh'), 'quiet'));
    fields.project_roots = field(form, 'Allowed parent project folders (absolute paths on that machine, one per line)', {
      multiline: true, required: true, placeholder: '/home/your-name/projects' });
    fields.control_root = field(form, 'Private dashboard control folder on that machine', {
      required: true, placeholder: '/home/your-name/.bd-control' });
    form.append(el('p', 'Choose an existing folder owned by your login account, with mode 700, dedicated to dashboard control files outside project folders. For this computer, its full absolute path must be at most 55 filesystem-encoded bytes; non-ASCII characters may use more than one byte. Missing-folder checks explain how to create it in your terminal. Do not move a control folder that owns existing terminals. Project folders also must exist.'));
    const advancedFields = el('details'); advancedFields.append(el('summary', 'Installation paths and advanced settings')); form.append(advancedFields);
    advancedFields.append(el('p', 'For shell aliases, select the real installation paths behind the alias. The dashboard does not execute shell startup files to resolve it. Leave the import root blank to discover the source checkout or wheel/pipx site-packages from the selected Botainer Python. Defaults are inspected where supported; missing paths receive an explanation.'));
    for (const [key, label, placeholder] of [['launcher', 'Botainer launcher (absolute)', '/path/to/environment/bin/botainer'], ['python', 'Botainer Python interpreter (absolute)', '/path/to/environment/bin/python'],
      ['source_root', 'Botainer import root (source checkout or site-packages)', '/path/to/botainer-source-or-site-packages'], ['state_root', 'Botainer state root', '/home/your-name/.botainer']]) {
      fields[key] = field(advancedFields, label, { placeholder });
    }
    const local = el('div'); advancedFields.append(local);
    for (const [key, label, placeholder] of [['docker_path', 'Docker executable', '/usr/local/bin/docker'], ['docker_host', 'Docker endpoint', 'unix:///var/run/docker.sock'], ['tmux_path', 'tmux executable', '/usr/local/bin/tmux'], ['home', 'Home folder', '/home/your-name']]) {
      fields[key] = field(local, label, { placeholder });
    }
    const ack = checkbox(form, 'Run a bounded read-only inspection using the selected interpreter and SSH connection. I trust these selected tools. No installation or SSH configuration change is authorized.');
    const inspect = el('button', 'Check connection', 'primary'); inspect.type = 'submit'; inspect.disabled = true;
    const result = el('section'); form.append(inspect, result);
    const invalidate = () => { editor.invalidate(); result.replaceChildren(); };
    for (const input of Object.values(fields)) input.addEventListener('input', invalidate);
    ack.addEventListener('change', () => { inspect.disabled = !ack.checked; invalidate(); });
    let previousKind = 'local', remoteId = '';
    function kindChanged() {
      if (previousKind === 'remote') remoteId = fields.id.value;
      const isRemote = fields.kind.value === 'remote'; remote.hidden = !isRemote; local.hidden = isRemote;
      fields.id.readOnly = !isRemote; fields.id.value = isRemote ? remoteId : 'local'; fields.ssh_alias.required = isRemote;
      previousKind = fields.kind.value;
      invalidate();
    }
    fields.kind.addEventListener('change', kindChanged); kindChanged();
    form.addEventListener('submit', event => {
      event.preventDefault();
      if (!ack.checked || busy) return;
      operate(async () => {
        const checkingView = view;
        const values = Object.fromEntries(Object.entries(fields).map(([key, input]) => [key, input.value]));
        result.replaceChildren(el('p', 'Checking selected machine… No installation or session launch.'));
        inspect.disabled = true;
        try {
          const outcome = await editor.inspect(preparationRequest({ ...values, read_only_acknowledged: ack.checked }));
          if (!outcome || !isCurrent()) return;
          result.replaceChildren();
          for (const check of outcome.checks || []) result.append(el('p', `${check.status === 'passed' ? '✓' : '⚠'} ${check.name}: ${check.message}`));
          if (outcome.profile) review(result, outcome.kind, outcome.profile, outcome.instructions, outcome.hook_candidates);
          else for (const instruction of outcome.instructions || []) result.append(el('p', instruction));
        } catch (error) { if (checkingView === view) throw error; }
        finally { inspect.disabled = !ack.checked; }
      });
    });
  }
  showPage(initialPage);
  try { const result = await api('/api/connections'); if (isCurrent()) update(result); }
  catch (error) { add.disabled = true; advanced.disabled = true; showError(error); }
  return { showPage, openHelp: help, refreshActiveMachines,
    focusPage: () => focus(currentPage === 'help' ? helpHeading : formOpen ? workspace.children[1] : title) };
}
