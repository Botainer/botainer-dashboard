/* Dependency-free, in-memory demonstration. Nothing here invokes a shell,
 * connects to a host, reads a directory, or changes Botainer configuration. */
(function (root) {
  'use strict';

  const LIVE = new Set(['starting', 'queued', 'running']);
  const CONFIG_KEYS = ['agent', 'authMode', 'authProfile', 'credentialStore',
    'network', 'runtime', 'cpus', 'memoryGb', 'timeLimitMinutes'];
  const IDENTITY_KEYS = ['agent', 'authMode', 'authProfile', 'credentialStore'];
  const copy = value => JSON.parse(JSON.stringify(value));
  const freezeCopy = value => {
    const cloned = copy(value);
    function freeze(node) {
      if (node && typeof node === 'object') { Object.values(node).forEach(freeze); Object.freeze(node); }
      return node;
    }
    return freeze(cloned);
  };
  const stringifyConfig = config => JSON.stringify(config, null, 2) + '\n';
  const ok = value => Object.assign({ok: true}, value);
  const fail = (code, message, details) => ({ok: false,
    error: Object.assign({code, message}, details || {})});
  const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const defaults = runtime => ({agent: 'claude', authMode: 'isolated',
    authProfile: 'default', credentialStore: '', network: 'internet', runtime,
    cpus: 2, memoryGb: 4, timeLimitMinutes: 120});
  const policyDefaults = () => ({version: 'policy-v1', default_auth_mode: 'isolated',
    network: {default_mode: 'internet'}});

  function validateInstallationConfig(config) {
    const errors = [];
    if (!isObject(config)) return fail('INVALID_CONFIG', 'Instance policy must be an object.');
    if (Object.keys(config).some(key => !['version', 'default_auth_mode', 'network'].includes(key))) {
      errors.push('This demo supports version, default_auth_mode, and network in instance policy.');
    }
    if (config.version !== 'policy-v1') errors.push('Instance policy version must be policy-v1.');
    if (!['isolated', 'shared'].includes(config.default_auth_mode)) errors.push('Choose isolated or shared as the default credential mode.');
    if (!isObject(config.network) || Object.keys(config.network).some(key => key !== 'default_mode') ||
        !['internet', 'none'].includes(config.network.default_mode)) {
      errors.push('network.default_mode must be internet or none in this demo policy.');
    }
    return errors.length ? fail('INVALID_CONFIG', errors[0], {issues: errors}) : ok({config: copy(config)});
  }

  // JSON is a YAML-compatible subset. This prototype deliberately uses only
  // JSON.parse: it neither parses general YAML nor writes any actual policy file.
  function parseConfigText(text, validator) {
    if (typeof text !== 'string') return fail('INVALID_CONFIG_TEXT', 'Enter configuration as JSON text.');
    let parsed;
    try { parsed = JSON.parse(text); }
    catch (_) {
      return fail('CONFIG_PARSE_ERROR', 'Enter valid JSON. The demo accepts this YAML-compatible subset, not general YAML syntax.');
    }
    const result = validator(parsed);
    return result.ok ? ok({config: result.config, format: 'json'}) : result;
  }

  function validateConfig(config, host) {
    const errors = [];
    if (!isObject(config)) return fail('INVALID_CONFIG', 'Configuration must be an object.');
    Object.keys(config).forEach(key => {
      if (!CONFIG_KEYS.includes(key)) errors.push('Unknown setting: ' + key + '.');
    });
    if (!['claude', 'codex'].includes(config.agent)) errors.push('Choose Claude or Codex.');
    if (!['isolated', 'shared', 'broker'].includes(config.authMode)) errors.push('Choose a supported credential mode.');
    if (config.agent === 'codex' && config.authMode === 'broker') {
      errors.push('Codex broker is not verified for this prototype. Choose isolated or shared credentials.');
    }
    if (!/^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$/.test(config.authProfile || '')) {
      errors.push('Credential profile needs a name using letters, numbers, dots, underscores, or hyphens.');
    }
    if (typeof config.credentialStore !== 'string' || config.credentialStore.length > 120) {
      errors.push('Credential store must be a label of at most 120 characters.');
    }
    if (!['internet', 'none'].includes(config.network)) errors.push('Choose internet access or no network.');
    if (!['docker', 'apptainer'].includes(config.runtime)) errors.push('Choose a supported runtime.');
    if (host && config.runtime !== host.runtime) errors.push('This host uses ' + host.runtime + '.');
    if (host && host.kind === 'slurm' && config.authMode === 'broker') {
      errors.push('Broker mode is not supported by this simulated Slurm launch route.');
    }
    [['cpus', 1, 256, 'CPUs'], ['memoryGb', 1, 1024, 'Memory in GB'],
      ['timeLimitMinutes', 1, 10080, 'Time limit in minutes']].forEach(rule => {
      if (!Number.isInteger(config[rule[0]]) || config[rule[0]] < rule[1] || config[rule[0]] > rule[2]) {
        errors.push(rule[3] + ' must be an integer from ' + rule[1] + ' to ' + rule[2] + '.');
      }
    });
    return errors.length ? fail('INVALID_CONFIG', errors[0], {issues: errors}) : ok({config: copy(config)});
  }

  function createDemo() {
    let tickCount = 0;
    let projectCount = 4;
    let sessionCount = 4;
    let operationCount = 0;
    let entryCount = 0;
    const baseTime = Date.parse('2026-09-19T14:00:00.000Z');
    const requestResults = new Map();
    const state = {
      schemaVersion: 1,
      simulated: true,
      revision: 0,
      now: new Date(baseTime).toISOString(),
      hosts: [
        {id: 'local', name: 'This Mac', kind: 'local', runtime: 'docker', reachable: true, lastSeenAt: new Date(baseTime).toISOString()},
        {id: 'cluster', name: 'Research cluster', kind: 'slurm', runtime: 'apptainer', reachable: true, lastSeenAt: new Date(baseTime).toISOString()},
        {id: 'workstation', name: 'Workstation', kind: 'ssh', runtime: 'docker', reachable: false, lastSeenAt: new Date(baseTime - 12 * 60000).toISOString()}
      ],
      installations: [],
      projects: [],
      sessions: [],
      operations: []
    };

    const getHost = id => state.hosts.find(host => host.id === id) || null;
    const getInstallation = id => state.installations.find(installation => installation.id === id) || null;
    const defaultInstallation = hostId => state.installations.find(installation => installation.hostId === hostId) || null;
    const getProject = id => state.projects.find(project => project.id === id) || null;
    const getSession = id => state.sessions.find(session => session.id === id) || null;
    const getLiveSession = projectId => state.sessions.find(session => session.projectId === projectId && LIVE.has(session.status)) || null;
    const latestSession = projectId => state.sessions.filter(session => session.projectId === projectId).slice(-1)[0] || null;
    const sameCheckout = (a, b) => a.hostId === b.hostId && a.path === b.path;
    const checkoutLiveSession = project => state.sessions.find(session => LIVE.has(session.status) &&
      sameCheckout(project, getProject(session.projectId))) || null;
    function tick() {
      state.now = new Date(baseTime + (++tickCount) * 1000).toISOString();
      state.revision += 1;
      return state.now;
    }
    function append(session, kind, text) {
      session.transcript.push({id: 'entry-' + (++entryCount), kind, text, at: state.now});
      session.transcript = session.transcript.slice(-120);
    }
    function observed(session) {
      session.observedAt = state.now;
      getHost(session.hostId).lastSeenAt = state.now;
    }
    function seedInstallation(id, hostId, name, executable, stateRoot, version) {
      const config = policyDefaults();
      state.installations.push({id, hostId, name, executable, stateRoot, version,
        configRevision: 1, draftBaseRevision: 1, savedConfig: copy(config),
        draftConfig: copy(config), draftText: stringifyConfig(config), draftError: null,
        configScope: 'Instance policy', configPath: stateRoot + '/policy.yaml'});
    }
    seedInstallation('install-local-stable', 'local', 'Stable', '~/Tools/botainer-stable/bin/botainer', '~/.botainer', '0.1.0-demo');
    seedInstallation('install-local-dev', 'local', 'Development', '~/Projects/botainer/bin/botainer', '~/.botainer-dev', '0.1.x-dev-demo');
    seedInstallation('install-cluster', 'cluster', 'Cluster', '~/bin/botainer', '~/.botainer', '0.1.0-demo');
    seedInstallation('install-workstation', 'workstation', 'Workstation', '~/bin/botainer', '~/.botainer', '0.1.0-demo');

    function seedProject(id, name, path, hostId, overrides, installationId) {
      const installation = getInstallation(installationId) || defaultInstallation(hostId);
      const config = Object.assign(defaults(getHost(hostId).runtime), {
        authMode: installation.savedConfig.default_auth_mode,
        network: installation.savedConfig.network.default_mode
      }, overrides || {});
      const project = {id, name, path, hostId, installationId: installation.id,
        kind: 'existing', pinned: false,
        configRevision: 1, draftBaseRevision: 1, savedConfig: copy(config),
        draftConfig: copy(config), draftText: stringifyConfig(config), draftError: null,
        createdAt: state.now};
      state.projects.push(project);
      return project;
    }
    function makeSession(id, project, status, title, operationId) {
      const host = getHost(project.hostId);
      const installation = getInstallation(project.installationId);
      return {id, projectId: project.id, hostId: project.hostId, title,
        installationId: installation.id,
        installationSnapshot: freezeCopy({id: installation.id, hostId: installation.hostId,
          name: installation.name, executable: installation.executable,
          stateRoot: installation.stateRoot, version: installation.version,
          configRevision: installation.configRevision, config: installation.savedConfig}),
        status, operationId: operationId || null, runtime: project.savedConfig.runtime,
        launchConfig: Object.freeze(copy(project.savedConfig)),
        launchConfigRevision: project.configRevision, createdAt: state.now,
        startedAt: status === 'running' ? state.now : null, endedAt: null,
        observedAt: host.lastSeenAt, lastActivityAt: null, terminalConnected: false,
        jobId: host.kind === 'slurm' ? 'demo-job-' + id : null,
        queueReason: status === 'queued' ? 'Waiting for resources (simulated)' : null,
        transcript: []};
    }

    seedProject('p-dashboard', 'dashboard', '~/Projects/dashboard', 'local').pinned = true;
    seedProject('p-cluster', 'climate-study', '~/Projects/climate-study', 'cluster', {authMode: 'shared', cpus: 4, memoryGb: 16});
    seedProject('p-notes', 'notes-lab', '~/Projects/notes-lab', 'local', {agent: 'codex'}, 'install-local-dev');
    seedProject('p-workstation', 'spectra', '~/Projects/spectra', 'workstation');
    const history = makeSession('s-history', getProject('p-dashboard'), 'stopped', 'Review the launch flow');
    history.createdAt = new Date(baseTime - 45 * 60000).toISOString();
    history.startedAt = history.createdAt;
    history.endedAt = new Date(baseTime - 30 * 60000).toISOString();
    history.lastActivityAt = history.endedAt;
    history.launchConfig = Object.freeze(Object.assign({}, history.launchConfig, {network: 'none'}));
    append(history, 'system', 'Earlier simulated run. This retained transcript is sample data.');
    append(history, 'input', 'Review the new-session flow.');
    append(history, 'agent', 'The launch review should show the host and saved project settings before starting.');
    append(history, 'system', 'Simulated session stopped. Start a new run to continue working.');
    history.transcript.forEach(entry => { entry.at = history.endedAt; });
    const local = makeSession('s-local', getProject('p-dashboard'), 'running', 'Explore session navigation');
    local.createdAt = new Date(baseTime - 5 * 60000).toISOString();
    local.startedAt = local.createdAt;
    local.lastActivityAt = new Date(baseTime - 20 * 1000).toISOString();
    local.terminalConnected = true;
    append(local, 'system', 'Simulated agent terminal. Input changes this demo only; no shell or real agent runs.');
    append(local, 'input', 'Compare navigation options for the session view.');
    append(local, 'agent', 'A project sidebar keeps each host and run visible. The selected terminal stays in the main pane.');
    const cluster = makeSession('s-cluster', getProject('p-cluster'), 'queued', 'Run parameter sweep');
    append(cluster, 'system', 'Simulated allocation queued. Advance the demo allocation to make its terminal available.');
    const offline = makeSession('s-workstation', getProject('p-workstation'), 'running', 'Review fit residuals');
    offline.createdAt = getHost('workstation').lastSeenAt;
    offline.startedAt = offline.createdAt;
    offline.lastActivityAt = offline.createdAt;
    append(offline, 'system', 'Last observed: running. Workstation is unreachable; this run may still be active.');
    state.sessions.push(history, local, cluster, offline);

    function projectCheck(id) {
      const project = getProject(id);
      return project ? ok({project}) : fail('PROJECT_NOT_FOUND', 'Project not found.');
    }
    function sessionCheck(id, requireHost) {
      const session = getSession(id);
      if (!session) return fail('SESSION_NOT_FOUND', 'Session not found.');
      const host = getHost(session.hostId);
      if (requireHost && !host.reachable) return fail('HOST_UNREACHABLE', host.name + ' is unreachable. Reconnect the host first.');
      return ok({session, host});
    }
    function configDiff(projectId) {
      const project = getProject(projectId);
      if (!project) return [];
      return CONFIG_KEYS.filter(key => project.savedConfig[key] !== project.draftConfig[key])
        .map(key => ({key, before: project.savedConfig[key], after: project.draftConfig[key]}));
    }
    const hasDraftChanges = projectId => {
      const project = getProject(projectId);
      return Boolean(project && (project.draftError || configDiff(projectId).length > 0));
    };

    function projectActivity(projectId) {
      return state.sessions.filter(session => session.projectId === projectId)
        .flatMap(session => [session.createdAt, session.startedAt, session.endedAt, session.lastActivityAt])
        .filter(Boolean).sort().slice(-1)[0] || null;
    }
    function setProjectPinned(projectId, pinned) {
      const checked = projectCheck(projectId);
      if (!checked.ok) return checked;
      if (typeof pinned !== 'boolean') return fail('INVALID_PIN', 'Pinned must be true or false.');
      if (checked.project.pinned === pinned) return ok({project: checked.project, changed: false});
      checked.project.pinned = pinned;
      tick();
      return ok({project: checked.project, changed: true});
    }
    function listProjects(options) {
      options = options || {};
      const query = typeof options.query === 'string' ? options.query.trim().toLowerCase() : '';
      const sort = ['recent', 'name', 'active'].includes(options.sort) ? options.sort : 'recent';
      return state.projects.filter(project => {
        if (options.hostId && project.hostId !== options.hostId) return false;
        if (options.installationId && project.installationId !== options.installationId) return false;
        const installation = getInstallation(project.installationId);
        const titles = state.sessions.filter(session => session.projectId === project.id).flatMap(session => [session.title, session.launchConfig.agent]);
        return [project.name, project.path, project.savedConfig.agent, getHost(project.hostId).name,
          installation.name, installation.executable, installation.stateRoot, ...titles]
          .join(' ').toLowerCase().includes(query);
      }).sort((a, b) => {
        const pinned = Number(b.pinned) - Number(a.pinned);
        if (pinned) return pinned;
        if (sort === 'active') {
          const active = Number(Boolean(getLiveSession(b.id))) - Number(Boolean(getLiveSession(a.id)));
          if (active) return active;
        }
        if (sort !== 'name') {
          const recent = (projectActivity(b.id) || '').localeCompare(projectActivity(a.id) || '');
          if (recent) return recent;
        }
        return a.name.localeCompare(b.name) || a.id.localeCompare(b.id);
      });
    }

    function createProject(input) {
      if (!isObject(input)) return fail('INVALID_PROJECT', 'Enter the project details.');
      const installation = input.installationId ? getInstallation(input.installationId) : defaultInstallation(input.hostId);
      if (!installation) return fail('INSTALLATION_NOT_FOUND', 'Choose a known Botainer installation.');
      if (input.hostId && installation.hostId !== input.hostId) return fail('INSTALLATION_HOST_MISMATCH', 'That Botainer installation belongs to a different host.');
      const host = getHost(installation.hostId);
      if (!host) return fail('HOST_NOT_FOUND', 'Choose a known host.');
      if (!host.reachable) return fail('HOST_UNREACHABLE', host.name + ' is unreachable. Reconnect the host first.');
      const name = typeof input.name === 'string' ? input.name.trim() : '';
      const path = typeof input.path === 'string' ? input.path.trim().replace(/\/+$/, '') : '';
      if (!name || name.length > 80) return fail('INVALID_PROJECT', 'Enter a project name of at most 80 characters.');
      if (!path || path.length > 300 || /[\u0000-\u001f\u007f]/.test(path)) return fail('INVALID_PROJECT', 'Enter a folder path without control characters.');
      if (!['existing', 'empty'].includes(input.kind)) return fail('INVALID_PROJECT', 'Choose an existing folder or a new empty folder.');
      const existing = state.projects.find(project => project.hostId === host.id && project.path === path);
      if (existing) {
        const other = getInstallation(existing.installationId);
        const sameNamespace = other.stateRoot.replace(/\/+$/, '') === installation.stateRoot.replace(/\/+$/, '');
        return fail('PROJECT_EXISTS', 'Already registered via ' + other.name + '. ' +
          (sameNamespace ? 'Changing the executable does not create a separate state namespace. ' : 'The checkout still has one project configuration file. ') +
          'Choose a separate checkout.', {projectId: existing.id, sameNamespace});
      }
      if (input.config !== undefined && !isObject(input.config)) return fail('INVALID_CONFIG', 'Configuration must be an object.');
      const config = Object.assign(defaults(host.runtime), {
        authMode: installation.savedConfig.default_auth_mode,
        network: installation.savedConfig.network.default_mode
      }, input.config || {});
      const validation = validateConfig(config, host);
      if (!validation.ok) return validation;
      tick();
      const project = seedProject('p-created-' + (++projectCount), name, path, host.id, config, installation.id);
      project.kind = input.kind;
      host.lastSeenAt = state.now;
      return ok({project, simulated: true});
    }

    function updateDraft(projectId, patch) {
      const checked = projectCheck(projectId);
      if (!checked.ok) return checked;
      if (!isObject(patch) || Object.keys(patch).some(key => !CONFIG_KEYS.includes(key))) {
        return fail('INVALID_CONFIG', 'The draft contains an unknown setting.');
      }
      const project = checked.project;
      project.draftConfig = Object.assign({}, project.draftConfig, copy(patch));
      project.draftText = stringifyConfig(project.draftConfig);
      const validation = validateConfig(project.draftConfig, getHost(project.hostId));
      project.draftError = validation.ok ? null : validation.error;
      tick();
      return ok({project, changes: configDiff(projectId)});
    }
    function resetDraft(projectId) {
      const checked = projectCheck(projectId);
      if (!checked.ok) return checked;
      checked.project.draftConfig = copy(checked.project.savedConfig);
      checked.project.draftText = stringifyConfig(checked.project.savedConfig);
      checked.project.draftError = null;
      checked.project.draftBaseRevision = checked.project.configRevision;
      tick();
      return ok({project: checked.project});
    }
    function saveConfig(projectId, expectedRevision) {
      const checked = projectCheck(projectId);
      if (!checked.ok) return checked;
      const project = checked.project;
      const host = getHost(project.hostId);
      if (!host.reachable) return fail('HOST_UNREACHABLE', host.name + ' is unreachable. The draft is kept locally.');
      const expected = expectedRevision === undefined ? project.draftBaseRevision : expectedRevision;
      if (expected !== project.configRevision) return fail('CONFIG_CONFLICT', 'Saved settings changed after this draft was opened. Review the latest settings before saving.');
      if (project.draftError) return {ok: false, error: project.draftError};
      const validation = validateConfig(project.draftConfig, host);
      if (!validation.ok) return validation;
      const changes = configDiff(projectId);
      if (checkoutLiveSession(project) && changes.some(change => IDENTITY_KEYS.includes(change.key))) {
        return fail('CONFIG_REQUIRES_STOP', 'Stop this project’s current run before changing its agent or credential settings. Your draft is preserved.');
      }
      if (!changes.length) return ok({project, changed: false});
      tick();
      project.savedConfig = copy(project.draftConfig);
      project.configRevision += 1;
      project.draftBaseRevision = project.configRevision;
      host.lastSeenAt = state.now;
      return ok({project, changed: true});
    }

    function serializeProjectConfig(projectId, options) {
      const project = getProject(projectId);
      return project ? (options && options.draft === false ? stringifyConfig(project.savedConfig) : project.draftText) : null;
    }
    function validateProjectConfigText(projectId, text) {
      const checked = projectCheck(projectId);
      if (!checked.ok) return checked;
      return parseConfigText(text, config => validateConfig(config, getHost(checked.project.hostId)));
    }
    function updateProjectConfigText(projectId, text) {
      const checked = projectCheck(projectId);
      if (!checked.ok) return checked;
      if (typeof text !== 'string') return fail('INVALID_CONFIG_TEXT', 'Enter configuration as JSON text.');
      const project = checked.project;
      const result = validateProjectConfigText(projectId, text);
      project.draftText = text;
      project.draftError = result.ok ? null : result.error;
      if (result.ok) project.draftConfig = copy(result.config);
      tick();
      return Object.assign({}, result, {project});
    }
    function saveProjectConfigText(projectId, text, expectedRevision) {
      const project = getProject(projectId);
      if (!project) return fail('PROJECT_NOT_FOUND', 'Project not found.');
      const updated = updateProjectConfigText(projectId, text === undefined ? project.draftText : text);
      return updated.ok ? saveConfig(projectId, expectedRevision) : updated;
    }
    function serializeInstallationConfig(installationId, options) {
      const installation = getInstallation(installationId);
      return installation ? (options && options.draft === false ? stringifyConfig(installation.savedConfig) : installation.draftText) : null;
    }
    function validateInstallationConfigText(installationId, text) {
      if (!getInstallation(installationId)) return fail('INSTALLATION_NOT_FOUND', 'Botainer installation not found.');
      return parseConfigText(text, validateInstallationConfig);
    }
    function updateInstallationConfigText(installationId, text) {
      const installation = getInstallation(installationId);
      if (!installation) return fail('INSTALLATION_NOT_FOUND', 'Botainer installation not found.');
      if (typeof text !== 'string') return fail('INVALID_CONFIG_TEXT', 'Enter configuration as JSON text.');
      const result = validateInstallationConfigText(installationId, text);
      installation.draftText = text;
      installation.draftError = result.ok ? null : result.error;
      if (result.ok) installation.draftConfig = copy(result.config);
      tick();
      return Object.assign({}, result, {installation});
    }
    function installationConfigDiff(installationId) {
      const installation = getInstallation(installationId);
      if (!installation) return [];
      const saved = installation.savedConfig;
      const draft = installation.draftConfig;
      return [['default_auth_mode', saved.default_auth_mode, draft.default_auth_mode],
        ['network.default_mode', saved.network.default_mode, draft.network.default_mode]]
        .filter(change => change[1] !== change[2])
        .map(change => ({key: change[0], before: change[1], after: change[2]}));
    }
    function hasInstallationDraftChanges(installationId) {
      const installation = getInstallation(installationId);
      return Boolean(installation && (installation.draftError || installationConfigDiff(installationId).length));
    }
    function resetInstallationDraft(installationId) {
      const installation = getInstallation(installationId);
      if (!installation) return fail('INSTALLATION_NOT_FOUND', 'Botainer installation not found.');
      installation.draftConfig = copy(installation.savedConfig);
      installation.draftText = stringifyConfig(installation.savedConfig);
      installation.draftError = null;
      installation.draftBaseRevision = installation.configRevision;
      tick();
      return ok({installation});
    }
    function saveInstallationConfig(installationId, expectedRevision) {
      const installation = getInstallation(installationId);
      if (!installation) return fail('INSTALLATION_NOT_FOUND', 'Botainer installation not found.');
      const host = getHost(installation.hostId);
      if (!host.reachable) return fail('HOST_UNREACHABLE', host.name + ' is unreachable. The policy draft is kept locally.');
      const expected = expectedRevision === undefined ? installation.draftBaseRevision : expectedRevision;
      if (expected !== installation.configRevision) return fail('CONFIG_CONFLICT', 'Saved instance policy changed after this draft was opened. Review it before saving.');
      if (installation.draftError) return {ok: false, error: installation.draftError};
      const validation = validateInstallationConfig(installation.draftConfig);
      if (!validation.ok) return validation;
      if (!installationConfigDiff(installationId).length) return ok({installation, changed: false});
      tick();
      installation.savedConfig = copy(installation.draftConfig);
      installation.configRevision += 1;
      installation.draftBaseRevision = installation.configRevision;
      host.lastSeenAt = state.now;
      return ok({installation, changed: true});
    }
    function saveInstallationConfigText(installationId, text, expectedRevision) {
      const installation = getInstallation(installationId);
      if (!installation) return fail('INSTALLATION_NOT_FOUND', 'Botainer installation not found.');
      const updated = updateInstallationConfigText(installationId, text === undefined ? installation.draftText : text);
      return updated.ok ? saveInstallationConfig(installationId, expectedRevision) : updated;
    }

    function storeKey(project, config, installationSnapshot) {
      const installation = installationSnapshot || getInstallation(project.installationId);
      return config.credentialStore || [project.hostId, installation.stateRoot, config.agent, config.authProfile].join(':');
    }
    function authConflict(projectId, config) {
      const project = getProject(projectId);
      if (!project) return null;
      const candidate = config || project.savedConfig;
      if (candidate.authMode === 'isolated') return null;
      return state.sessions.find(session => {
        if (!LIVE.has(session.status)) return false;
        const other = session.launchConfig;
        if (other.authMode === 'isolated' || other.agent !== candidate.agent) return false;
        if (candidate.authMode !== 'shared' && other.authMode !== 'shared') return false;
        return storeKey(project, candidate) === storeKey(getProject(session.projectId), other, session.installationSnapshot);
      }) || null;
    }
    function startSession(projectId, options) {
      options = options || {};
      const checked = projectCheck(projectId);
      if (!checked.ok) return checked;
      if (options.requestId && requestResults.has(options.requestId)) {
        const prior = requestResults.get(options.requestId);
        if (prior.projectId !== projectId) return fail('REQUEST_CONFLICT', 'This request ID belongs to another project.');
        return ok({session: getSession(prior.sessionId), operation: state.operations.find(operation => operation.id === prior.operationId), reused: true});
      }
      const project = checked.project;
      const host = getHost(project.hostId);
      if (!host.reachable) return fail('HOST_UNREACHABLE', host.name + ' is unreachable. Reconnect the host first.');
      const installation = getInstallation(project.installationId);
      if (!installation || installation.hostId !== project.hostId) return fail('INSTALLATION_NOT_FOUND', 'This project needs a matching Botainer installation.');
      const live = checkoutLiveSession(project);
      if (live) return fail('PROJECT_BUSY', 'This project already has a live run. Reconnect to it or stop it before starting another.', {sessionId: live.id});
      const validation = validateConfig(project.savedConfig, host);
      if (!validation.ok) return validation;
      if (installation.savedConfig.network.default_mode === 'none' && project.savedConfig.network !== 'none') {
        return fail('POLICY_REFUSED', 'This instance policy allows no network. Change the project’s saved network setting or review instance policy before launching.');
      }
      const conflict = authConflict(projectId);
      if (conflict) return fail('AUTH_CONFLICT', 'Another live run can refresh the same shared login. Stop that run or choose an independent project login.', {sessionId: conflict.id, projectId: conflict.projectId});
      const title = typeof options.title === 'string' && options.title.trim() ? options.title.trim() : 'New session';
      if (title.length > 100) return fail('INVALID_TITLE', 'Use a session title of at most 100 characters.');
      tick();
      const status = host.kind === 'slurm' ? 'queued' : 'starting';
      const operation = {id: 'op-' + (++operationCount), action: 'start', projectId, status, createdAt: state.now};
      const session = makeSession('s-created-' + (++sessionCount), project, status, title, operation.id);
      operation.sessionId = session.id;
      state.operations.push(operation);
      state.sessions.push(session);
      observed(session);
      append(session, 'system', status === 'queued' ? 'Simulated allocation queued. Advance the demo allocation when ready.' : 'Starting a simulated container. Advance startup to connect its terminal.');
      if (options.requestId) requestResults.set(options.requestId, {projectId, sessionId: session.id, operationId: operation.id});
      return ok({session, operation, reused: false});
    }
    function advanceSession(sessionId) {
      const checked = sessionCheck(sessionId, true);
      if (!checked.ok) return checked;
      const session = checked.session;
      if (!['starting', 'queued'].includes(session.status)) return fail('INVALID_TRANSITION', 'Only a starting or queued run can advance to running.');
      tick();
      session.status = 'running';
      session.startedAt = state.now;
      session.queueReason = null;
      observed(session);
      const operation = state.operations.find(item => item.id === session.operationId);
      if (operation) { operation.status = 'ready'; operation.completedAt = state.now; }
      append(session, 'system', 'Simulated agent ready. Connect the terminal and type a prompt; no real command will execute.');
      return ok({session});
    }
    function connectTerminal(sessionId) {
      const checked = sessionCheck(sessionId, true);
      if (!checked.ok) return checked;
      const session = checked.session;
      if (session.status !== 'running') return fail('TERMINAL_UNAVAILABLE', 'The terminal is available only while this run is running.');
      if (session.terminalConnected) return ok({session, alreadyConnected: true});
      tick();
      session.terminalConnected = true;
      observed(session);
      return ok({session, alreadyConnected: false});
    }
    function disconnectTerminal(sessionId) {
      const checked = sessionCheck(sessionId, false);
      if (!checked.ok) return checked;
      const session = checked.session;
      if (!session.terminalConnected) return ok({session, alreadyDisconnected: true});
      tick();
      session.terminalConnected = false;
      return ok({session, alreadyDisconnected: false});
    }
    function syntheticReply(input, session) {
      const lower = input.toLowerCase();
      if (/\b(test|checks?)\b/.test(lower)) return 'Simulated response: I would run the focused checks and report the result. No tests or commands were executed by this prototype.';
      if (/\b(config|settings|network)\b/.test(lower)) return 'Simulated response: this run launched with ' + session.launchConfig.network + ' network access and ' + session.launchConfig.authMode + ' credentials. Saved defaults apply to the next run.';
      if (/\b(plan|next|steps?)\b/.test(lower)) return 'Simulated response: inspect the project, make one focused change, and verify its behavior. This transcript is synthetic; no project files were read or changed.';
      return 'Simulated response: received your prompt. In the working dashboard, the agent would respond here. This demo does not execute shell commands or contact an AI service.';
    }
    function sendInput(sessionId, text) {
      const checked = sessionCheck(sessionId, true);
      if (!checked.ok) return checked;
      const session = checked.session;
      if (session.status !== 'running' || !session.terminalConnected) return fail('TERMINAL_DISCONNECTED', 'Connect this running session’s terminal before typing.');
      if (typeof text !== 'string' || !text.trim()) return fail('EMPTY_INPUT', 'Type a prompt first.');
      if (text.length > 4000) return fail('INPUT_TOO_LONG', 'Keep demo prompts under 4,000 characters.');
      tick();
      append(session, 'input', text.trim());
      append(session, 'agent', syntheticReply(text, session));
      session.lastActivityAt = state.now;
      observed(session);
      return ok({session, reply: session.transcript[session.transcript.length - 1]});
    }
    function stopSession(sessionId) {
      const checked = sessionCheck(sessionId, true);
      if (!checked.ok) return checked;
      const session = checked.session;
      if (!LIVE.has(session.status)) return ok({session, alreadyEnded: true});
      tick();
      session.status = session.status === 'running' ? 'stopped' : 'cancelled';
      session.endedAt = state.now;
      session.terminalConnected = false;
      session.queueReason = null;
      observed(session);
      const operation = state.operations.find(item => item.id === session.operationId);
      if (operation && operation.status !== 'ready') { operation.status = 'cancelled'; operation.completedAt = state.now; }
      append(session, 'system', session.status === 'cancelled' ? 'Simulated launch cancelled. No agent is running.' : 'Simulated session stopped. Its transcript is retained in this page only.');
      return ok({session, alreadyEnded: false});
    }
    function setHostReachable(hostId, reachable) {
      const host = getHost(hostId);
      if (!host) return fail('HOST_NOT_FOUND', 'Host not found.');
      if (typeof reachable !== 'boolean') return fail('INVALID_CONNECTION', 'Reachability must be true or false.');
      if (host.reachable === reachable) return ok({host, changed: false});
      tick();
      host.reachable = reachable;
      if (reachable) host.lastSeenAt = state.now;
      state.sessions.filter(session => session.hostId === hostId).forEach(session => {
        session.terminalConnected = false;
        if (reachable) observed(session);
      });
      return ok({host, changed: true});
    }
    function sessionView(sessionId) {
      const session = getSession(sessionId);
      if (!session) return null;
      const host = getHost(session.hostId);
      const project = getProject(session.projectId);
      const live = LIVE.has(session.status);
      const unknown = !host.reachable && live;
      return {id: session.id, status: unknown ? 'unknown' : session.status,
        lastKnownStatus: session.status, hostReachable: host.reachable,
        terminalConnected: host.reachable && session.terminalConnected,
        canConnect: host.reachable && session.status === 'running' && !session.terminalConnected,
        canDisconnect: session.terminalConnected,
        canStop: host.reachable && live,
        canAdvance: host.reachable && ['starting', 'queued'].includes(session.status),
        configChanged: CONFIG_KEYS.some(key => session.launchConfig[key] !== project.savedConfig[key]),
        installationConfigChanged: session.installationSnapshot.configRevision !== getInstallation(session.installationId).configRevision,
        observedAt: session.observedAt,
        reason: unknown ? 'Host unreachable. Last observed ' + session.status + '; the run may still be active.' : (session.queueReason || '')};
    }

    return {state, getHost, getInstallation, getProject, getSession, getLiveSession, latestSession,
      projectActivity, listProjects, setProjectPinned,
      createProject, updateDraft, resetDraft, saveConfig, configDiff, hasDraftChanges,
      serializeProjectConfig, validateProjectConfigText, updateProjectConfigText, saveProjectConfigText,
      serializeInstallationConfig, validateInstallationConfigText, updateInstallationConfigText,
      saveInstallationConfigText, saveInstallationConfig, resetInstallationDraft,
      installationConfigDiff, hasInstallationDraftChanges,
      validateConfig, authConflict, startSession, advanceSession, connectTerminal,
      disconnectTerminal, sendInput, stopSession, setHostReachable, sessionView,
      snapshot: () => copy(state)};
  }

  const api = Object.freeze({createDemo, validateConfig, validateInstallationConfig});
  root.BotainerDemo = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(globalThis);
