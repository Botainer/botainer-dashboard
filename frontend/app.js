import { TerminalViewRegistry } from './terminal-registry.js';
import { createXtermAdapter, createWebSocketTransport, targetKey, terminalFontSize, DEFAULT_TERMINAL_FONT_SIZE } from './xterm-adapter.js';
import { getBearer, clearBearer, authHeaders } from './auth.js';
import { connectionRows, connectionAgentSummary, connectionDisplayLabel, mountConnectionsPanel } from './connections.js';
import { initDesktopLayout } from './desktop-layout.js';
import { readWorkbenchPreferences, writeWorkbenchPreferences, reconcileWorkbenchPreferences } from './workbench-state.js';

const ACTIVE = new Set(['running', 'starting', 'queued']);
const SAFE_STATES = new Set(['running', 'starting', 'queued', 'stopped', 'failed', 'unknown', 'connected', 'connecting', 'disconnected', 'detached']);
export const stateClass = value => SAFE_STATES.has(value) ? value : 'unknown';
export const isLaunchLog = session => session?.kind === 'launch' && (session.consoleEnded === true ||
  ['completed', 'declined', 'failed', 'unknown'].includes(session.launchState));
export const isProjectSetup = session => session?.kind === 'launch' && session.operation === 'init';

export async function copyTerminalSelection(registry, target, clipboard) {
  const text = registry.getSelection(target);
  if (!text) throw new Error('Select terminal text first. On a Mac, hold Option while dragging if the running app captures the mouse.');
  if (!clipboard?.writeText) throw new Error('Clipboard access is unavailable. Select text and use your browser’s Copy command.');
  await clipboard.writeText(text);
}

export async function pasteTerminalClipboard(registry, target, clipboard) {
  const paste = registry.preparePaste(target);
  if (!paste) throw new Error('Paste requires a connected, writable terminal. Completed launch logs are read-only.');
  if (!clipboard?.readText) throw new Error('Clipboard access is unavailable. Focus the terminal and use your browser’s Paste command.');
  const text = await clipboard.readText();
  if (!paste(text)) throw new Error('Paste was not sent: the terminal changed, disconnected, or the text exceeds the paste limit.');
  registry.focus(target);
}

export const TERMINAL_HISTORY_BYTES = 1024 * 1024;
export const TERMINAL_HISTORY_CAPTION = 'Screen and scrollback snapshot; refresh to update. Does not send input.';

export function canReadTerminalHistory(snapshot, session, { connected = true } = {}) {
  return Boolean(connected && session?.capabilities?.readTerminalHistory === true &&
    capability(snapshot, 'readTerminalHistory', session));
}

export async function loadTerminalHistory(session, { request = api, isCurrent = () => true } = {}) {
  if (session?.capabilities?.readTerminalHistory !== true || typeof session.contextNamespace !== 'string' ||
      !session.contextNamespace || typeof session.runtimeId !== 'string' || !session.runtimeId) {
    throw new Error('Scrollback is not available for this session.');
  }
  const body = Object.freeze({ contextNamespace: session.contextNamespace });
  const result = await request(`/api/sessions/${encodeURIComponent(session.runtimeId)}/history`,
    { method: 'POST', body, mutation: false });
  if (!isCurrent()) return null;
  if (!result || result.kind !== 'terminal-snapshot' || typeof result.text !== 'string' ||
      new TextEncoder().encode(result.text).byteLength > TERMINAL_HISTORY_BYTES ||
      result.truncated !== undefined && typeof result.truncated !== 'boolean') {
    throw new Error('The service returned an unsupported scrollback snapshot.');
  }
  return { text: result.text, truncated: result.truncated === true };
}

export function terminalHistoryText(document, text) {
  // Output is untrusted text, never HTML or an instruction to a terminal parser.
  const preview = document.createElement('textarea');
  preview.className = 'terminal-history-text';
  preview.readOnly = true; preview.spellcheck = false; preview.wrap = 'off';
  preview.setAttribute('aria-label', 'Terminal scrollback snapshot');
  preview.value = text;
  return preview;
}
export const isSettingsPage = panel => ['settings', 'machine', 'connections', 'help', 'dashboard-config'].includes(panel);
export const HOST_BADGE = '⚠ Host · no container';
export const HOST_INVENTORY_NOTE = 'Host projects are folders added to this profile. Use Add project to open an existing folder. Claude/Codex history and sessions opened elsewhere are not imported.';
export const HOST_ACCESS = 'Runs directly under your OS account with the agent’s existing local configuration and permissions. The project folder is a working directory, not an OS access boundary; the agent may access files, credentials and network services beyond it.';
export const DASHBOARD_CONFIG_SCOPE = 'This file stores dashboard registrations. It does not edit runtime profiles selected at startup or any project configuration. A restart applies this file only in launch modes that use these registrations.';

// Execution identity comes from the service, never a display name or agent type.
// Retained session DTOs keep the host warning even when inventory is unavailable.
export function isHostExecution(snapshot, entity) {
  return entity?.executionKind === 'host' || workspaceFor(snapshot, entity)?.executionKind === 'host' ||
    (!snapshot?.workspaces && snapshot?.executionKind === 'host');
}

export function hostTerminalWarning(snapshot, current, retained = null) {
  if (isHostExecution(snapshot, current)) return true;
  if (!isHostExecution(snapshot, retained)) return false;
  if (!current) return true;
  // Retain the warning for the same terminal when fresh metadata is incomplete.
  // This presentation rule never grants permission to connect or control it.
  return ['contextNamespace', 'runtimeId', 'workspaceId', 'projectId'].every(key =>
    typeof current[key] === 'string' && current[key].length > 0 && current[key] === retained[key]);
}

export function configuredHostAgents(snapshot, project) {
  const agents = project?.availableAgents ?? workspaceFor(snapshot, project)?.availableAgents ?? [];
  if (!Array.isArray(agents)) return [];
  const seen = new Set();
  return agents.filter(agent => agent && ['claude', 'codex'].includes(agent.id) && !seen.has(agent.id) &&
    typeof agent.executable === 'string' && agent.executable.startsWith('/') && (seen.add(agent.id), true))
    .map(agent => ({ id: agent.id, label: agent.id === 'claude' ? 'Claude' : 'Codex', executable: agent.executable,
      ...(agent.available === false ? { available: false } : {}),
      ...Object.fromEntries(['externalTools', 'startupHooks', 'updates'].filter(key => agent[key] === 'disabled')
        .map(key => [key, 'disabled'])) }));
}

export function hostAgentRestrictions(agent) {
  const disabled = [['externalTools', 'external MCP tools'], ['startupHooks', 'startup hooks'], ['updates', 'automatic updates']]
    .filter(([key]) => agent?.[key] === 'disabled').map(([, label]) => label);
  if (!disabled.length) return '';
  const list = disabled.length === 1 ? disabled[0] : `${disabled.slice(0, -1).join(', ')} and ${disabled.at(-1)}`;
  return `This profile disables ${list}. The agent’s native permission prompts and existing sign-in remain in use.`;
}

export function sessionAgentOptions(snapshot, project) {
  if (!isHostExecution(snapshot, project)) return [{ value: 'default', label: 'Project default' },
    { value: 'claude', label: 'Claude' }, { value: 'codex', label: 'Codex' }];
  const agents = configuredHostAgents(snapshot, project);
  const defaultAgent = project?.defaultAgent ?? workspaceFor(snapshot, project)?.defaultAgent;
  const chosen = agents.find(agent => agent.id === defaultAgent);
  const ordered = chosen ? [chosen, ...agents.filter(agent => agent.id !== chosen.id)] : agents;
  return ordered.map(agent => ({ value: agent.id,
    label: agent.label + (agents.length > 1 && agent === chosen ? ' (default)' : '') +
      (agent.available === false ? ' · update needed' : '') }));
}

function selectedHostAgent(snapshot, project, choice) {
  const id = choice === 'default' ? project?.defaultAgent ?? workspaceFor(snapshot, project)?.defaultAgent : choice;
  const agent = configuredHostAgents(snapshot, project).find(item => item.id === id);
  if (!isHostExecution(snapshot, project) || !agent) throw new Error('The selected host agent is not configured for this project.');
  if (agent.available === false) throw new Error('This host agent executable is missing or changed. Open Settings → Machines → Review agent update before starting a new session.');
  return agent;
}

export function hostLaunchDescription(snapshot, project, choice) {
  const agent = selectedHostAgent(snapshot, project, choice);
  return [`Run ${agent.label} directly on ${workspaceFor(snapshot, project)?.label || project.machineLabel || 'this computer'} in ${project.path}? Executable: ${agent.executable}.`,
    HOST_ACCESS, hostAgentRestrictions(agent), 'Read and answer the agent’s own prompts in the terminal; the dashboard does not answer them for you.'].filter(Boolean).join(' ');
}

export function hostLaunchPresentation(snapshot, project, choice) {
  const agent = selectedHostAgent(snapshot, project, choice);
  return { title: `Start a new ${agent.label} session on host?`,
    description: `Start a separate ${agent.label} session in ${project.path}. This does not reconnect or replace an existing session. Other sessions keep running; sessions in the same folder share files, so concurrent edits can conflict.`,
    warning: hostLaunchDescription(snapshot, project, choice), confirm: `⚠ Start new ${agent.label} session on host` };
}

export function projectUnavailableMessage(project) {
  if (project?.controlRestriction === 'outside-project-roots') {
    return 'Container sessions are not enabled for this folder. The selected Botainer runtime profile must include it in project_roots before the dashboard can start a session. Inspect the selected connection and profile in Project details; updating the runtime profile requires a dashboard restart.';
  }
  if (project?.unavailableReason === 'combined-project-unavailable') return 'This project is not currently verified. Check its connection and selected runtime profile in Settings.';
  return typeof project?.unavailableReason === 'string' ? project.unavailableReason : '';
}

export function projectStartUnavailableMessage(project) {
  return projectUnavailableMessage(project) ||
    (typeof project?.startUnavailableReason === 'string' ? project.startUnavailableReason : '');
}

export function hostOpeningOptions(snapshot, project) {
  const current = snapshot.projects.find(item => item.id === project?.id && item.workspaceId === project.workspaceId);
  const source = workspaceFor(snapshot, current);
  if (!current || isHostExecution(snapshot, current) || source?.kind !== 'local' || !Array.isArray(current.nativeHostWorkspaces)) return [];
  const seen = new Set();
  return current.nativeHostWorkspaces.flatMap(option => {
    const workspace = snapshot.workspaces?.find(item => item.id === option?.id && item.kind === 'local' &&
      item.executionKind === 'host' && item.status === 'available');
    if (!workspace || seen.has(workspace.id)) return [];
    seen.add(workspace.id);
    return [{ id: workspace.id, label: workspace.label }];
  });
}

export function hostProjectOpeningDescription(project) {
  return `Use the existing folder ${project.path} as a host-agent project. The dashboard registers this same folder; it does not create a container, change Botainer configuration, or start an agent. Choose New session afterward. ${HOST_ACCESS}`;
}

export function hostOpeningErrorMessage(error) {
  const messages = {
    'combined-native-project-outside-roots': 'This folder is outside the allowed project folders for that host profile. Check the host profile’s project folders in Settings.',
    'combined-native-source-local-required': 'Only an existing project on this computer can be opened with a local host agent. A remote folder cannot use this action.',
    'combined-native-source-unavailable': 'The original local project is not currently verified. Refresh its workspace before opening it with a host agent.',
    'combined-native-target-unavailable': 'The selected host profile is not currently available or has changed. Refresh and inspect its Settings before continuing.',
    'combined-native-source-changed': 'The original project folder or name changed while this request was being checked. Refresh the project before opening it again.',
  };
  return Object.hasOwn(messages, error?.message) ? messages[error.message]
    : typeof error?.message === 'string' && error.message || 'The host project could not be opened. Refresh and inspect the project list.';
}

export function includeHostProjectResponse(snapshot, result, workspaceId) {
  const workspace = snapshot.workspaces?.find(item => item.id === workspaceId && item.kind === 'local' && item.executionKind === 'host');
  const project = result?.project;
  if (!workspace || !project || project.workspaceId !== workspaceId || project.executionKind !== 'host' ||
      typeof project.path !== 'string' || !project.path || result.session) {
    throw new Error('The service did not confirm the selected host project. Refresh the project list before trying again.');
  }
  const existing = snapshot.projects.find(item => item.id === project.id);
  if (existing && (existing.workspaceId !== workspaceId || existing.path !== project.path || existing.executionKind !== 'host')) {
    throw new Error('The service returned an ambiguous host project. Refresh before continuing.');
  }
  return validateSnapshot({ ...snapshot, projects: [...snapshot.projects.filter(item => item.id !== project.id), project] });
}

export function filePreviewNote(snapshot, project) {
  return isHostExecution(snapshot, project)
    ? 'Read-only text preview. This host project uses a plain folder; agent configuration remains with the installed agent.'
    : 'Text preview. Edit project defaults in Project config.';
}

// Presentation helpers only. Backends retain authority over project identities,
// file access and paths; these functions never open or modify a file.
const CONTROL = /[\u0000-\u001f\u007f]/u;
const INVALID_UNICODE = /[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/u;
const encoder = new TextEncoder();

function relativeParts(path) {
  if (typeof path !== 'string' || path.length > 1024 || CONTROL.test(path) || INVALID_UNICODE.test(path)) {
    throw new Error('invalid-relative-path');
  }
  if (!path) return [];
  const parts = path.split('/');
  if (parts.length > 24 || parts.some(part => !part || part.startsWith('.') || part.includes('\\') || encoder.encode(part).length > 240)) {
    throw new Error('invalid-relative-path');
  }
  return parts;
}

export function fileBreadcrumbs(path) {
  const parts = relativeParts(path);
  return [{ label: 'Project root', path: '' }, ...parts.map((label, index) => ({ label, path: parts.slice(0, index + 1).join('/') }))];
}

// Project paths are display data from the selected project, never launch argv.
// Support drive-letter paths for display without claiming Windows execution.
export function fileLocation(projectPath, relativePath = '') {
  const parts = relativeParts(relativePath);
  if (typeof projectPath !== 'string' || !projectPath || projectPath.length > 4096 ||
      CONTROL.test(projectPath) || INVALID_UNICODE.test(projectPath) ||
      !(projectPath.startsWith('/') || /^[A-Za-z]:[\\/]/u.test(projectPath))) {
    throw new Error('invalid-project-location');
  }
  if (!parts.length) return projectPath;
  const separator = /^[A-Za-z]:\\/u.test(projectPath) ? '\\' : '/';
  return projectPath.replace(/[\\/]+$/u, '') + separator + parts.join(separator);
}

export function fileListingPresentation(result, query = '') {
  try {
    if (!result || typeof result !== 'object' || Array.isArray(result) ||
        !Array.isArray(result.entries) || result.entries.length > 2000 ||
        typeof query !== 'string' || query.length > 1024 || INVALID_UNICODE.test(query)) throw new Error();
    relativeParts(result.path);
    if (result.excluded !== undefined && (!Number.isSafeInteger(result.excluded) || result.excluded < 0)) throw new Error();
    if (result.truncated !== undefined && typeof result.truncated !== 'boolean') throw new Error();
    const seen = new Set();
    let unsupported = 0;
    const entries = result.entries.map(entry => {
      if (!entry || typeof entry !== 'object' || Array.isArray(entry) ||
          typeof entry.name !== 'string' || !entry.name || entry.name.includes('/') ||
          typeof entry.path !== 'string' || !['file', 'directory'].includes(entry.kind)) throw new Error();
      if (entry.path !== (result.path ? `${result.path}/` : '') + entry.name || seen.has(entry.path)) throw new Error();
      if (entry.size !== undefined && entry.size !== null && (!Number.isSafeInteger(entry.size) || entry.size < 0)) throw new Error();
      seen.add(entry.path);
      // Local filesystems allow names the bounded reader cannot traverse. Keep
      // the rest of that directory usable without sending unsupported paths.
      try { relativeParts(entry.name); relativeParts(entry.path); }
      catch { unsupported++; return null; }
      return { name: entry.name, path: entry.path, kind: entry.kind, size: entry.size ?? null };
    }).filter(Boolean);
    const filter = query.trim().toLocaleLowerCase();
    const shown = entries.filter(entry => entry.name.toLocaleLowerCase().includes(filter));
    shown.sort((a, b) => Number(b.kind === 'directory') - Number(a.kind === 'directory') ||
      a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }) || a.name.localeCompare(b.name));
    const excluded = result.excluded ?? null;
    const truncated = result.truncated === true;
    const summary = [filter ? `${shown.length} of ${entries.length} listed entries match.` : `${entries.length} ${entries.length === 1 ? 'entry' : 'entries'} listed.`];
    if (excluded) summary.push(`${excluded} ${excluded === 1 ? 'entry is' : 'entries are'} excluded from this view.`);
    if (unsupported) summary.push(`${unsupported} ${unsupported === 1 ? 'entry has a name or path' : 'entries have names or paths'} this browser cannot open and ${unsupported === 1 ? 'is' : 'are'} omitted.`);
    if (truncated) summary.push('This listing is incomplete. Open a subfolder or use a native file browser to inspect more files.');
    return { path: result.path, entries: shown, total: entries.length, shown: shown.length, excluded, unsupported, truncated,
      summary: summary.join(' '), emptyMessage: shown.length ? '' : filter
        ? 'No matching files or folders in the current listing. Clear the filter to see all listed entries.'
        : 'No files or folders are available in this view. Hidden and excluded items may still exist.' };
  } catch {
    throw new Error('unsupported-file-listing');
  }
}

export function fileBrowserError(error) {
  const code = typeof error === 'string' ? error : error?.message;
  if (error?.name === 'AbortError' || error instanceof TypeError ||
      ['remote-connection-unavailable', 'remote-observation-stale', 'combined-workspace-unavailable'].includes(code)) {
    return 'The file connection is unavailable. Check the selected machine in Settings → Machines, restore its connection, then refresh this folder.';
  }
  if (['file-access-excluded', 'host-file-access-excluded', 'Private file excluded', 'private file excluded'].includes(code)) {
    return 'This file is excluded from dashboard browsing. Hidden files, credential-like names and links are restricted. Use Project config for Botainer defaults, or inspect other files with your normal file tools.';
  }
  if (['text-size-limit', 'utf8-text-required', 'Text preview bound exceeded', 'text preview limit'].includes(code)) {
    return 'Preview supports plain UTF-8 text up to 64 KiB, without binary content. Open this file with your normal file tools; the dashboard has not changed it.';
  }
  if (['regular-unlinked-file-required', 'Only ordinary single-link text files can be read', 'regular unlinked file required'].includes(code)) {
    return 'Only ordinary files with one filesystem link can be previewed. Links and special files are excluded. Inspect this item with your normal file tools.';
  }
  if (['project-identity-changed', 'project-directory-changed', 'project-root-changed', 'root-identity-changed',
    'host-project-directory-changed', 'host-project-root-changed', 'invalid-project-identity'].includes(code)) {
    return 'The registered project folder has moved, been replaced or changed identity. Refresh the project list and check Project details before browsing it again.';
  }
  if (['Directory entry bound exceeded', 'directory entry limit'].includes(code)) {
    return 'This folder exceeds the remote listing limit of 500 scanned entries. Browse a smaller subfolder or use a native file client; this view cannot page through the remaining entries.';
  }
  if (['remote-files-unavailable', 'cluster-files-unavailable-inspect-private-receipt'].includes(code)) {
    return 'The remote folder could not be listed. Check the machine connection and diagnostic receipt. A folder with more than 500 scanned entries can also exceed this view’s limit; use a smaller subfolder or a native file client.';
  }
  if (['remote-file-unavailable', 'cluster-file-unavailable-inspect-private-receipt'].includes(code)) {
    return 'The remote file could not be previewed. Check the machine connection and diagnostic receipt. Preview is limited to ordinary UTF-8 text files up to 64 KiB; excluded files remain unavailable.';
  }
  if (['invalid-relative-path', 'invalid-project-location', 'unsupported-file-listing', 'unsupported-file-preview', 'Invalid relative path', 'invalid file path'].includes(code)) {
    return 'The folder response or path is not supported. Return to Project root and refresh. No file from this response was opened.';
  }
  if (['unknown-project', 'project-unregistered', 'host-project-unregistered', 'combined-project-unregistered', 'combined-project-operation-unavailable',
    'workspace-not-enabled', 'cluster-project-unregistered'].includes(code)) {
    return 'File browsing is not currently available for this project. Refresh the project list and check its machine connection and Project details.';
  }
  if (['remote-profile-changed-restart-required', 'remote-helper-changed-restart-required', 'host-helper-changed-restart-required'].includes(code)) {
    return 'The selected profile or file helper changed. Finish any pending launch prompts, then deliberately restart the dashboard with the intended configuration before browsing again.';
  }
  return 'The dashboard could not read this item. Refresh the folder and check its location, access permissions and machine connection. Inspect the service diagnostic receipt if it still fails.';
}

function isOrphanContainerStop(session) {
  return session?.stopMode === 'orphan-container' && session.stopScope === 'container';
}

export function sessionStopPresentation(snapshot, session, project = null) {
  const identity = `${sessionDisplayLabel(session)} [${session.runtimeId}] in ${project?.name || 'this project'}`;
  if (isHostExecution(snapshot, session)) return {
    label: 'Close host terminal', title: 'Close this host terminal?',
    description: `Close ${identity}? This closes the selected host terminal and stops its owned process. Detached background work and tasks in a shared agent daemon may continue. Use the agent’s task controls to inspect or stop that work. Closing only the terminal view leaves this process running.`,
    result: 'Host-terminal close request completed. The refreshed inventory reports its outcome; detached background work and shared-daemon tasks may still be running.',
  };
  if (isOrphanContainerStop(session)) {
    const container = [session.containerName, session.containerId].filter(value => typeof value === 'string' && value).join(' · ');
    return {
      label: 'Stop leftover container', title: 'Stop this leftover container?',
      description: `Stop ${identity}${container ? `? Container: ${container}.` : '?'} This ends all work inside this exact container and may interrupt an agent that is still working. The original Botainer launcher has ended, so its terminal cannot be recovered. Credential-helper cleanup may remain unconfirmed after the container stops. No replacement agent will be started.`,
      result: 'Container stop requested. Check the refreshed inventory for its outcome. Credential-helper cleanup may remain unconfirmed; no replacement agent was started.',
    };
  }
  if (session.stopScope === 'allocation') {
    const job = typeof session.jobId === 'string' && session.jobId ? `Slurm job ${session.jobId}` : 'this Slurm job';
    return {
      label: 'Cancel Slurm job', title: `Cancel ${job}?`,
      description: `Cancel ${job} for ${identity}? This cancels the entire allocation and ends all work within it, including other sessions or Open OnDemand (OOD) work. The dashboard does not know how many other workloads share this allocation. Other allocations on the same compute node are not cancelled. Closing the terminal view does not cancel this job.`,
      result: 'Job cancellation requested. The refreshed inventory reports the scheduler outcome.',
    };
  }
  const waiting = ['queued', 'starting'].includes(session.state);
  return { label: waiting ? 'Cancel session' : 'Stop session', title: waiting ? 'Cancel this session?' : 'Stop this session?',
    description: `${waiting ? 'Cancel' : 'Stop'} ${identity}? ${waiting ? 'This requests cancellation of its pending or starting allocation. Check the refreshed state for the outcome.' : 'This ends the running process. Closing the terminal view leaves it running.'}`,
    result: 'Stop request completed. The refreshed inventory reports the session outcome.' };
}

export function applySessionStopResult(snapshot, session, result, { registry, notice }) {
  const observed = result?.session;
  const matching = observed && observed.contextNamespace === session.contextNamespace &&
    observed.runtimeId === session.runtimeId;
  const allocation = session.stopScope === 'allocation';
  // Legacy host/workspace replies report a verified ended session. A scheduler
  // cancellation acknowledgement alone is never proof that its job has ended.
  // An explicit false verdict also overrides a conflicting sidebar state.
  const confirmed = Boolean(matching && (result.terminationConfirmed === true ||
    result.terminationConfirmed === undefined && !allocation && !observed.stale &&
    ['stopped', 'failed'].includes(observed.state)));
  let message;
  if (!matching) message = 'The Stop response could not be matched to this session. Its outcome is unknown; the terminal view remains open. Refresh before deciding what to do.';
  else if (!confirmed) message = allocation
    ? 'Job cancellation was acknowledged, but termination is not yet confirmed. The terminal view remains open; check the refreshed scheduler state.'
    : 'Stop is not confirmed. The session may still be running. The terminal view remains open; refresh to check its state.';
  else if (isHostExecution(snapshot, session)) message = 'The host terminal process has stopped. Detached background work and shared-daemon tasks may still be running.';
  else message = allocation ? 'The scheduler allocation has ended.' : 'The session runtime has stopped.';
  if (confirmed && result.helperCleanupConfirmed === false) {
    message += ' Botainer helper cleanup is still unconfirmed.';
  }
  if (matching && typeof result.diagnostic === 'string' && result.diagnostic) {
    const diagnostic = result.diagnostic.slice(0, 4096)
      .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F\u202A-\u202E\u2066-\u2069]/g, '');
    message += `\nBotainer reported:\n${diagnostic}`;
  }
  if (matching && result.diagnosticTruncated === true) message += '\n[Native stop diagnostic was shortened.]';
  if (confirmed) registry.detach(session);
  // The application's notice renderer assigns textContent, never innerHTML.
  notice(message);
  return { confirmed, message };
}

// Filtering inventory, inspecting connections and selecting work are independent
// navigation operations. None of them creates or attaches a runtime session.
export function navigateWorkbench(state, action, snapshot) {
  if (action.type === 'filter') return { ...state,
    filterId: snapshot.workspaces?.some(item => item.id === action.id) ? action.id : null };
  if (action.type === 'settings') return { ...state, panel: 'settings', settingsMachineId: null };
  if (action.type === 'connections') return { ...state, panel: 'connections', settingsMachineId: null };
  if (action.type === 'help') return { ...state, panel: 'help', settingsMachineId: null };
  if (action.type === 'machine') return snapshot.workspaces?.some(item => item.id === action.id)
    ? { ...state, panel: 'machine', settingsMachineId: action.id } : state;
  if (action.type === 'back') return { ...state, panel: null };
  if (action.type === 'project') return { ...state, projectId: action.id, target: null, panel: null };
  if (action.type === 'session') return { ...state, projectId: action.session.projectId,
    target: { contextNamespace: action.session.contextNamespace, runtimeId: action.session.runtimeId }, panel: null };
  throw new Error('Unknown workbench navigation action.');
}

export function retainedSessionObservation(snapshot, selectedTarget, retainedSession) {
  if (!selectedTarget || !retainedSession || targetKey(selectedTarget) !== targetKey(retainedSession)) return null;
  return snapshot.sessions.find(session => targetKey(session) === targetKey(selectedTarget) &&
    session.projectId === retainedSession.projectId && session.workspaceId === retainedSession.workspaceId) ?? null;
}

export function projectForRetainedSession(snapshot, selectedTarget, retainedSession) {
  const session = retainedSessionObservation(snapshot, selectedTarget, retainedSession);
  return session ? snapshot.projects.find(project => project.id === session.projectId && project.workspaceId === session.workspaceId) ?? null : null;
}

export function sessionDisplayLabel(session) {
  if (session.kind === 'launch') return !isLaunchLog(session) && ['waiting', 'running'].includes(session.launchState)
    ? `${isProjectSetup(session) ? 'Project setup' : 'Launch'} · Botainer CLI`
    : `${isProjectSetup(session) ? 'Setup' : 'Launch'} log · ${session.consoleEndReason === 'timeout' ? 'timed out' : session.launchState || 'unknown'}`;
  return session.label || session.runtimeId;
}

// A retained native specification can lack a scheduler handle. This observation
// is not an ended state, nor proof that a job never ran. Keep all runtime guards.
export const hasNoRecordedJob = session => session?.kind !== 'launch' && session?.runtime === 'apptainer' &&
  session.state === 'unknown' && !session.jobId && !session.stale && session.unavailableReason === 'No recorded scheduler job';

// Scheduler membership is separate from agent lifetime. Only a fresh successful
// observation of a recorded job can move an unknown record out of Current.
export const isPastSchedulerRecord = session => session?.kind !== 'launch' && session?.runtime === 'apptainer' &&
  session.state === 'unknown' && !session.stale && Boolean(session.jobId) && session.schedulerObservation === 'not-active';

export const hasActiveAllocationWithoutTerminal = session => session?.kind !== 'launch' && session?.runtime === 'apptainer' &&
  session.state === 'unknown' && !session.stale && Boolean(session.jobId) && session.schedulerObservation === 'active';

export function sessionStateLabel(session) {
  const observation = observationStatusLabel(session);
  if (session.stale && observation) return `${observation}${['running', 'starting', 'queued', 'stopped', 'failed'].includes(session.lastKnownState) ? ` · last ${session.lastKnownState}` : ''}`;
  if (hasNoRecordedJob(session)) return 'No job recorded';
  if (isPastSchedulerRecord(session)) return 'past · outcome unverified';
  if (hasActiveAllocationWithoutTerminal(session)) return 'allocation active · unverified';
  return session.state === 'unknown' ? 'unverified' : session.kind === 'launch'
    ? isLaunchLog(session) ? 'log' : 'launching' : session.state || 'unknown';
}

// A launch console is not the container it eventually creates. Resolve only the
// service's exact result identity, in the same observed project and machine.
export function nativeLaunchResult(snapshot, launch) {
  const target = launch?.resultTarget;
  if (launch?.kind !== 'launch' || isProjectSetup(launch) || launch.launchState !== 'completed' ||
      typeof target?.contextNamespace !== 'string' || !target.contextNamespace ||
      typeof target.runtimeId !== 'string' || !target.runtimeId) return null;
  return snapshot.sessions.find(session => session.kind !== 'launch' &&
    session.contextNamespace === target.contextNamespace && session.runtimeId === target.runtimeId &&
    session.projectId === launch.projectId && session.workspaceId === launch.workspaceId) ?? null;
}

export function presentLaunchResult(launch, selectedTarget, inspector = null) {
  return Boolean(launch?.kind === 'launch' && selectedTarget && !inspector &&
    targetKey(launch) === targetKey(selectedTarget));
}

export function includeLaunchResponse(snapshot, project, session) {
  const supported = capability(snapshot, isProjectSetup(session) ? 'nativeCliProjectSetup' : 'nativeCliLaunch', project);
  if (!supported || session?.kind !== 'launch' ||
      session.projectId !== project.id || session.workspaceId !== project.workspaceId) {
    throw new Error('The service returned an unsupported launch console. Refresh before attempting another launch.');
  }
  const existing = snapshot.sessions.find(item => item.contextNamespace === session.contextNamespace && item.runtimeId === session.runtimeId);
  if (existing && (existing.kind !== 'launch' || existing.projectId !== project.id || existing.workspaceId !== project.workspaceId)) {
    throw new Error('The service returned an ambiguous launch console. Refresh before attempting another launch.');
  }
  // The POST response is a current service observation. A cached inventory poll
  // may lag behind it; open this exact console now so its prompts are usable.
  return validateSnapshot({ ...snapshot,
    sessions: [...snapshot.sessions.filter(item => item.contextNamespace !== session.contextNamespace ||
      item.runtimeId !== session.runtimeId), session] });
}

export function includeProjectSetupResponse(snapshot, result, workspaceId = null) {
  const project = result?.project, session = result?.session;
  if (!workspaceCapability(snapshot, workspaceId, 'nativeCliProjectSetup') || !project ||
      snapshot.workspaces && project.workspaceId !== workspaceId || session && !isProjectSetup(session)) {
    throw new Error('The service returned an unsupported project setup. Refresh before attempting another setup request.');
  }
  const existing = snapshot.projects.find(item => item.id === project.id);
  if (existing && (existing.workspaceId !== project.workspaceId || existing.path !== project.path)) {
    throw new Error('The service returned an ambiguous project setup. Refresh before attempting another setup request.');
  }
  const observed = validateSnapshot({ ...snapshot,
    projects: [...snapshot.projects.filter(item => item.id !== project.id), project] });
  return session ? includeLaunchResponse(observed, project, session) : observed;
}

export function nativeSetupResult(snapshot, launch) {
  if (!isProjectSetup(launch) || launch.launchState !== 'completed' || typeof launch.resultProjectId !== 'string') return null;
  return snapshot.projects.find(project => project.id === launch.resultProjectId && project.workspaceId === launch.workspaceId) ?? null;
}

// A per-run choice never changes the project's saved configuration. Unknown or
// unsupported values fail closed instead of silently selecting another agent.
export function sessionLaunchRequest(snapshot, project, agent, requestId) {
  const current = snapshot.projects.find(item => item.id === project?.id && item.workspaceId === project.workspaceId);
  if (!current || !capability(snapshot, 'startSession', current)) throw new Error('Session launch is no longer available for this project.');
  if (!['default', 'claude', 'codex'].includes(agent)) throw new Error('Choose Project default, Claude or Codex.');
  if (agent !== 'default' && !capability(snapshot, 'agentOverride', current)) throw new Error('Agent choice is not available for this project.');
  if (isHostExecution(snapshot, current)) return { requestId, agent: selectedHostAgent(snapshot, current, agent).id };
  return { requestId, ...(agent === 'default' ? {} : { agent }) };
}

const RECOVERY_DELAYS = [2000, 5000, 10000];
export function terminalRecoveryIntent(session, { queued = false } = {}) {
  if (!session || session.kind === 'launch') return null;
  return { contextNamespace: session.contextNamespace, runtimeId: session.runtimeId,
    projectId: session.projectId, workspaceId: session.workspaceId,
    phase: queued ? 'queued' : 'connecting', attempts: 0, connectedAt: null, nextAt: 0, expiresAt: null };
}

export function advanceTerminalRecovery(intent, event, now = Date.now()) {
  if (!intent || targetKey(intent) !== targetKey(event.target)) return intent;
  if (event.state === 'connected') return { ...intent, phase: 'connected', connectedAt: now, expiresAt: null };
  if (['detached', 'disposed'].includes(event.state)) return null;
  if (event.state !== 'disconnected') return intent;
  if (event.reason !== 'connection_lost' || intent.phase !== 'connected') return null;
  // A stable connection earns a new recovery budget; flapping open/close pairs
  // retain the old budget and cannot reset themselves into an endless loop.
  const attempts = now - intent.connectedAt >= 30000 ? 0 : intent.attempts;
  if (attempts >= RECOVERY_DELAYS.length) return null;
  return { ...intent, phase: 'retry', attempts, nextAt: now + RECOVERY_DELAYS[attempts], expiresAt: now + 120000 };
}

export function terminalRecoveryTarget(snapshot, intent, selectedTarget, { now = Date.now(), connected = true,
  hidden = false, inspector = null, busy = false } = {}) {
  if (!intent || !selectedTarget || targetKey(intent) !== targetKey(selectedTarget) || !connected || hidden || inspector || busy ||
      !['queued', 'retry'].includes(intent.phase) || now < intent.nextAt || intent.expiresAt !== null && now > intent.expiresAt) return null;
  const session = snapshot.sessions.find(item => targetKey(item) === targetKey(intent) && item.projectId === intent.projectId &&
    item.workspaceId === intent.workspaceId && item.kind !== 'launch');
  return session?.state === 'running' && capability(snapshot, 'attachTerminal', session) ? session : null;
}

export function beginTerminalRecovery(intent) {
  return intent && ['queued', 'retry'].includes(intent.phase)
    ? { ...intent, phase: 'connecting', attempts: intent.attempts + (intent.phase === 'retry' ? 1 : 0), connectedAt: null } : null;
}

function sessionDisplayLabels(sessions) {
  const counts = new Map();
  const group = session => JSON.stringify([session.projectId, sessionDisplayLabel(session)]);
  for (const session of sessions) counts.set(group(session), (counts.get(group(session)) || 0) + 1);
  return new Map(sessions.map(session => [targetKey(session), sessionDisplayLabel(session) +
    (counts.get(group(session)) > 1 ? ` · ${session.runtimeId.slice(0, 8)}` : '')]));
}

export function capability(snapshot, name, entity = null) {
  const workspace = workspaceFor(snapshot, entity);
  if (entity && Array.isArray(snapshot?.workspaces) && (!workspace || workspace.status !== 'available')) return false;
  const capabilities = entity?.capabilities;
  return capabilities && Object.hasOwn(capabilities, name)
    ? capabilities[name] === true : workspace ? workspace.capabilities?.[name] === true : snapshot?.capabilities?.[name] === true;
}

export function workspaceFor(snapshot, entity) {
  const id = entity?.workspaceId ?? snapshot?.projects?.find(project => project.id === entity?.projectId)?.workspaceId;
  return snapshot?.workspaces?.find(workspace => workspace.id === id) ?? null;
}

export function displayedWorkspace(snapshot, workspaceId) {
  if (Array.isArray(snapshot?.workspaces)) return snapshot.workspaces.find(workspace => workspace.id === workspaceId) ?? null;
  if (!snapshot?.clusterSettings) return null;
  return { kind: 'cluster', label: snapshot.clusterSettings.site || 'Cluster workspace',
    status: snapshot.connectionStatus || 'checking', clusterSettings: snapshot.clusterSettings,
    capabilities: snapshot.capabilities, notice: snapshot.notice };
}

export function workspaceCapability(snapshot, id, name) {
  if (!Array.isArray(snapshot?.workspaces)) return capability(snapshot, name);
  const workspace = snapshot.workspaces.find(item => item.id === id);
  return Boolean(workspace && workspace.status === 'available' && workspace.capabilities?.[name] === true);
}

export function projectSetupOptions(snapshot, metadata, workspaceId = null) {
  const scoped = items => Array.isArray(items) ? items.filter(item => !snapshot.workspaces || item.workspaceId === workspaceId) : [];
  return { roots: scoped(metadata.roots), installations: scoped(metadata.installations),
    modes: [['projectOpen', 'open', 'Open an existing folder'], ['projectCreate', 'create', 'Create a new project folder'], ['projectRegister', 'register', 'Register an existing Botainer project']]
      .filter(([cap, value]) => workspaceCapability(snapshot, workspaceId, cap) &&
        (value !== 'register' || !workspaceCapability(snapshot, workspaceId, 'nativeCliProjectSetup') &&
          !isHostExecution(snapshot, { workspaceId })))
      .map(([, value, label]) => ({ value, label })) };
}

export function preparedTrial(snapshot, entity = null) {
  const mode = workspaceFor(snapshot, entity)?.mode ?? snapshot.mode;
  return ['native-cli-workspace', 'local-workspace', 'cluster-workspace', 'combined-workspace'].includes(mode);
}

export function projectExecutionLabel(snapshot, entity) {
  if (isHostExecution(snapshot, entity)) return HOST_BADGE;
  const mode = workspaceFor(snapshot, entity)?.mode ?? snapshot.mode;
  return ['native-cli-workspace', 'local-workspace', 'cluster-workspace'].includes(mode) ? 'Shell trial' : '';
}

export function projectSetupDescription(snapshot, workspaceId = null) {
  if (isHostExecution(snapshot, { workspaceId })) return `To add a folder you already use with Claude or Codex, choose Open an existing folder. Select its parent under Workspace folder, then enter the path relative to that parent (for example, team/my-project). This registers a dashboard project; it does not initialize Botainer, create a container, or start an agent. Existing agent history and sessions are not imported. ${HOST_ACCESS}`;
  if (workspaceCapability(snapshot, workspaceId, 'nativeCliProjectSetup')) {
    return 'Open an existing folder or create a new one within the selected workspace. An uninitialized folder opens Botainer’s native init prompts in a terminal; an existing Botainer project keeps its saved configuration. Project setup does not start an agent. Choose New session afterward.';
  }
  return preparedTrial(snapshot, { workspaceId })
    ? 'This prepared trial accepts folders within the workspace root shown below. Create writes trial defaults; Register accepts an already initialized project in that root. This does not discover ordinary Botainer projects or select a production AI agent.'
    : 'Choose a configured workspace and installation. Use a relative folder path within that workspace. Creating a project writes its initial defaults; registering uses an existing Botainer project and its saved defaults.';
}

export const schedulerReason = value => typeof value === 'string' && !['', 'none', '(null)', 'n/a', 'unknown'].includes(value.trim().toLowerCase()) ? value : '';

export const dashboardCommand = snapshot => snapshot?.dashboardEntryPoint === 'installed' ? 'botainer-dashboard' : 'python3 tools/dashboard.py';

export function observationStatusLabel(entity) {
  switch (entity?.observationStatus) {
    case 'delayed': return 'Status check delayed';
    case 'failed': return 'Status check failed';
    case 'stale': return 'Status out of date';
    default: return '';
  }
}

export function lastReportedSession(session, now = Date.now()) {
  if (!['running', 'starting', 'queued', 'stopped', 'failed'].includes(session?.lastKnownState)) return '';
  const at = session.lastKnownAt;
  const age = relativeTimestamp(at, now);
  return `Last reported: ${session.lastKnownState}${typeof at === 'string' && Number.isFinite(Date.parse(at)) ? ` at ${at}${age ? ` (${age})` : ''}` : ''}.`;
}

const WORKSPACE_DIAGNOSTIC_CODES = new Set(['botainer-installation-changed', 'dashboard-restart-required', 'workspace-check-failed']);
const INCOMPLETE_PROJECT_INVENTORY = 'Project inventory may be incomplete; missing projects are not confirmed deleted.';
const UNVERIFIED_WORKSPACE_STATUS = 'Existing sessions may still be running; controls stay disabled until verified.';

function boundedConnectionDiagnostic(value, codes = null) {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      typeof value.message !== 'string' || !value.message.trim() || value.message.length > 1000 ||
      typeof value.recovery !== 'string' || !value.recovery.trim() || value.recovery.length > 1500 ||
      codes && !codes.has(value.code) || value.code !== undefined &&
        (typeof value.code !== 'string' || value.code.length > 128)) return null;
  return value;
}

function workspaceConnectionDiagnostic(workspace) {
  if (workspace?.status !== 'unavailable') return null;
  const diagnostic = boundedConnectionDiagnostic(workspace.connectionDiagnostic, WORKSPACE_DIAGNOSTIC_CODES);
  if (diagnostic && diagnostic.code !== 'workspace-check-failed') return diagnostic;
  // A slow or stale observation alone does not establish a connection failure.
  if (['delayed', 'stale'].includes(workspace.observationStatus)) return null;
  return boundedConnectionDiagnostic(workspace.clusterSettings?.connectionDiagnostic) || diagnostic;
}

function workspaceDiagnosticLabel(workspace) {
  switch (workspaceConnectionDiagnostic(workspace)?.code) {
    case 'botainer-installation-changed': return 'Review Botainer update';
    case 'dashboard-restart-required': return 'Dashboard restart needed';
    case 'ssh-authentication-required': return 'SSH sign-in needed';
    case 'ssh-host-key-unverified': return 'Verify SSH host key';
    case 'ssh-host-key-changed': return 'SSH host key changed';
    case 'ssh-name-resolution-failed': return 'Host name not found';
    case 'ssh-connection-refused': return 'SSH connection refused';
    case 'ssh-network-unreachable': return 'SSH host unreachable';
    case 'ssh-connection-lost': return 'SSH connection lost';
    case 'ssh-timeout': return 'Cluster check timed out';
    case 'ssh-local-client-unavailable': return 'SSH client unavailable';
    case 'ssh-request-failed': return 'SSH request failed';
    case 'remote-check-failed': return 'Remote workspace check failed';
    case 'workspace-check-failed': return 'Connection check failed';
    default: return '';
  }
}

export function workspaceConnectionPresentation(workspace, { connected = true } = {}) {
  if (!connected) return { summary: 'Dashboard service offline',
    message: `Current connection and session status cannot be read. ${UNVERIFIED_WORKSPACE_STATUS}`,
    recovery: 'Restore the dashboard service, then choose Check again.', command: null };
  if (!workspace) return { summary: 'Connection unavailable', message: 'No current connection details are reported.',
    recovery: 'Open Connection help to review the selected machine.', command: null };
  if (workspace.status === 'available') return { summary: 'Connected', message: 'Current workspace status is available.', recovery: '', command: null };
  const diagnostic = workspaceConnectionDiagnostic(workspace);
  if (diagnostic && !['workspace-check-failed', 'remote-check-failed', 'ssh-request-failed', 'checking'].includes(diagnostic.code)) {
    return { summary: workspaceDiagnosticLabel(workspace) || 'Connection check failed',
      message: `${diagnostic.message} ${UNVERIFIED_WORKSPACE_STATUS}`,
      recovery: diagnostic.code === 'botainer-installation-changed'
        ? 'Ask the dashboard maintainer to review the Botainer update and renew this connection’s saved verification. This alpha cannot do that review in the GUI. Restarting alone will not fix it.'
        : diagnostic.recovery,
      command: diagnostic.code?.startsWith('ssh-') ? sshRecoveryCommand(workspace) : null };
  }
  if (workspace.observationStatus === 'delayed') return { summary: 'Status check delayed',
    message: `The latest status request is taking longer than usual; its outcome is not known yet. ${UNVERIFIED_WORKSPACE_STATUS}`,
    recovery: 'Checks continue automatically. Choose Check again or open Connection help if the delay persists.', command: null };
  if (workspace.observationStatus === 'stale') return { summary: 'Status out of date',
    message: 'No recent workspace status response is available. Existing sessions may still be running.',
    recovery: 'Choose Check again. If status stays out of date, open Connection help.', command: null };
  if (workspace.status === 'checking') return { summary: 'Checking connection',
    message: 'Waiting for this machine’s first verified status response.', recovery: '', command: null };
  const host = workspace.executionKind === 'host';
  return { summary: workspaceDiagnosticLabel(workspace) || 'Connection check failed',
    message: `The dashboard could not read current ${host ? 'status for this connection' : 'Botainer or session status'}. The cause is not yet identified. ${UNVERIFIED_WORKSPACE_STATUS}`,
    recovery: host ? 'Choose Check again. If it still fails, open Connection help to review this machine’s profile and configured agent executables.'
      : diagnostic?.code === 'ssh-request-failed'
      ? 'Try the configured SSH alias in your system terminal. If it connects, open Connection help to review the profile and installation.'
      : 'Choose Check again. If it still fails, open Connection help to review this machine’s profile and installation.',
    command: diagnostic?.code === 'ssh-request-failed' ? sshRecoveryCommand(workspace) : null };
}

export function projectInventoryNotice(snapshot, workspaceId = null, { connected = true } = {}) {
  const workspaces = Array.isArray(snapshot?.workspaces)
    ? snapshot.workspaces.filter(workspace => workspaceId === null || workspace.id === workspaceId)
    : [displayedWorkspace(snapshot, null)].filter(Boolean);
  return !connected || snapshot?.stale || workspaces.some(workspace => workspace.status === 'unavailable')
    ? INCOMPLETE_PROJECT_INVENTORY : '';
}

export function workspaceAvailability(workspace) {
  if (!workspace) return '';
  if (workspace.status === 'available') return workspace.notice || 'Connected workspace';
  const observation = observationStatusLabel(workspace);
  const at = workspace.lastObservationAt;
  const last = typeof at === 'string' && Number.isFinite(Date.parse(at)) ? ` Last successful status received at ${at}.` : '';
  const diagnostic = workspaceConnectionDiagnostic(workspace);
  const inventory = workspace.status === 'unavailable' ? ` ${INCOMPLETE_PROJECT_INVENTORY}` : '';
  if (workspace.status === 'unavailable' && (!diagnostic && workspace.observationStatus === 'failed' ||
      ['workspace-check-failed', 'remote-check-failed', 'ssh-request-failed'].includes(diagnostic?.code))) {
    const presentation = workspaceConnectionPresentation(workspace);
    return `${presentation.message} ${presentation.recovery}${observation ? ` ${observation}.` : ''}${last}${inventory} No launch will be retried automatically.`;
  }
  if (diagnostic) {
    const updateHelp = diagnostic.code === 'botainer-installation-changed' ? ` ${workspaceConnectionPresentation(workspace).recovery}` : '';
    return `${diagnostic.message} ${diagnostic.recovery}${updateHelp}${observation ? ` ${observation}.` : ''}${last} Session state is unverified; existing sessions may still be running. Checks continue automatically; controls stay disabled until verified.${inventory} No launch will be retried automatically.`;
  }
  if (observation) {
    return `${observation}.${last} Checks continue automatically; controls stay disabled until verified. Existing sessions may still be running.${inventory}`;
  }
  if (workspace.status === 'checking') return 'Checking this machine… Actions stay disabled until its status is verified. This does not mean its sessions have ended.';
  if (workspace.executionKind === 'host') return `This computer’s host-agent profile or session owner is not currently verified. Check the local dashboard service and configured agent executables, then Refresh. Existing agents may still be running.${inventory}`;
  if (workspace.kind === 'local') {
    const presentation = workspaceConnectionPresentation(workspace);
    return `${presentation.message} ${presentation.recovery}${inventory}`;
  }
  const alias = workspace.clusterSettings?.sshAlias;
  const command = typeof alias === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(alias) ? `run ssh ${alias}` : 'connect using your configured SSH alias';
  return `This cluster connection is not currently verified. If SSH access has expired, open your normal terminal, ${command}, complete any login prompts, then Refresh. Existing sessions may still be running.${inventory}`;
}

export function workspaceSummaries(snapshot) {
  return (snapshot.workspaces ?? []).map(workspace => ({ id: workspace.id, label: workspace.label,
    status: workspace.status === 'available' ? 'connected' : workspaceDiagnosticLabel(workspace) ||
      (workspace.observationStatus === 'failed' ? 'Connection check failed' : observationStatusLabel(workspace)) || (workspace.status === 'checking' ? 'checking' : 'unavailable'),
    projectCount: snapshot.projects.filter(project => project.workspaceId === workspace.id).length }));
}

// Saved connections are navigation to setup only. They never enter the live
// workspace inventory or acquire project/session capabilities from their label.
export function pendingConnections(snapshot, saved) {
  if (saved?.enabled !== true || !Array.isArray(saved.entries) || !Array.isArray(saved.active)) return [];
  return connectionRows(saved).filter(entry => entry.enabled && !entry.active &&
    !snapshot.workspaces?.some(workspace => workspace.id === entry.id));
}

export function workspaceConnectionLabel(workspace) {
  const label = connectionDisplayLabel(workspace);
  const agents = connectionAgentSummary(workspace);
  return agents ? `${label} · ${agents}` : label;
}

export function sshRecoveryCommand(workspace) {
  const alias = workspace?.clusterSettings?.sshAlias;
  return workspace && workspace.kind !== 'local' && workspace.status === 'unavailable' &&
    !['delayed', 'stale'].includes(workspace.observationStatus) &&
    typeof alias === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(alias) ? `ssh ${alias}` : null;
}

export function presentationUnchanged(expected, current) {
  return Boolean(expected && current && ['revision', 'workspaceId', 'projectId', 'targetKey', 'inspector']
    .every(key => expected[key] === current[key]));
}

export function updateNavigationList(container, content, activeElement, focusTargets, fallbackFocus = null) {
  const children = [...content.childNodes];
  // Unchanged observations must not detach a button between pointerdown and
  // click, or interrupt keyboard navigation. Compare rendered content, not
  // timestamps such as lastObservedAt which can change on every inventory poll.
  if (children.length === container.childNodes.length &&
      children.every((child, index) => child.isEqualNode(container.childNodes[index]))) return false;
  const scrollTop = container.scrollTop, scrollLeft = container.scrollLeft;
  const focusedKey = container.contains(activeElement) ? activeElement?.dataset?.navigationKey : null;
  container.replaceChildren(content);
  if (focusedKey) (focusTargets.get(focusedKey) || fallbackFocus)?.focus({ preventScroll: true });
  container.scrollTop = scrollTop; container.scrollLeft = scrollLeft;
  return true;
}

export function launchResponsePresentation(expected, current, session) {
  if (!presentationUnchanged(expected, current)) return null;
  return { inspector: session?.kind === 'launch' || session?.executionKind === 'host' ? null : current.inspector };
}

export function sessionControls(snapshot, session, { connected = true, busy = false } = {}) {
  const enabled = Boolean(session && connected && !busy);
  const recovery = isOrphanContainerStop(session);
  const unavailableOwner = ['original-owner-ended', 'external-terminal-owner-unverified', 'orphan-stop-unconfirmed'].includes(session?.controlRestriction);
  return {
    attach: enabled && !recovery && !unavailableOwner && (isLaunchLog(session) || session.state === 'running' &&
      (session.kind !== 'launch' || ['waiting', 'running'].includes(session.launchState))) && capability(snapshot, 'attachTerminal', session),
    stop: enabled && session.kind !== 'launch' && ACTIVE.has(session.state) &&
      session.controlRestriction !== 'orphan-stop-unconfirmed' &&
      (session.controlRestriction !== 'original-owner-ended' || recovery) &&
      (!(recovery || unavailableOwner) || session.capabilities?.stopSession === true) && capability(snapshot, 'stopSession', session),
  };
}

export function sessionStopTarget(snapshot, confirmedSession, options = {}) {
  const current = confirmedSession && snapshot.sessions.find(session =>
    session.contextNamespace === confirmedSession.contextNamespace && session.runtimeId === confirmedSession.runtimeId &&
    session.projectId === confirmedSession.projectId && session.workspaceId === confirmedSession.workspaceId);
  if (!sessionControls(snapshot, current, options).stop || current.stopMode !== confirmedSession.stopMode ||
      current.stopScope !== confirmedSession.stopScope || current.containerId !== confirmedSession.containerId ||
      current.jobId !== confirmedSession.jobId) {
    throw new Error('Session control changed while the confirmation was open. Refresh and review its current Stop action.');
  }
  return Object.freeze({ contextNamespace: current.contextNamespace, runtimeId: current.runtimeId,
    expectedStopMode: isOrphanContainerStop(current) ? 'orphan-container' : 'normal' });
}

export function projectPrimaryAction(snapshot, project, { connected = true, busy = false } = {}) {
  if (!project || !connected || busy) return null;
  project = snapshot.projects.find(item => item.id === project.id);
  if (!project) return null;
  const current = snapshot.sessions.filter(session => session.projectId === project.id && !isHistoricalSession(session));
  if (current.length === 1 && !isLaunchLog(current[0]) && sessionControls(snapshot, current[0]).attach) {
    const session = current[0];
    return { kind: 'connect', label: isProjectSetup(session) ? 'Resume setup console' : session.kind === 'launch' ? 'Resume launch console' : 'Connect session',
      target: { contextNamespace: session.contextNamespace, runtimeId: session.runtimeId } };
  }
  if (!current.length && capability(snapshot, 'startSession', project) &&
      (!isHostExecution(snapshot, project) || sessionAgentOptions(snapshot, project).length)) return { kind: 'start', label: 'Start session' };
  return null;
}

export function projectCurrentSessions(snapshot, projectId) {
  const project = snapshot.projects.find(item => item.id === projectId);
  if (!project) return [];
  return snapshot.sessions.filter(session => session.projectId === project.id && session.workspaceId === project.workspaceId &&
    !isHistoricalSession(session) && !isLaunchLog(session));
}

// Called only by an explicit user gesture, never by polling or generic selection.
// Showing retained output is allowed; opening a transport needs fresh membership
// and capabilities for the exact project, workspace and session identity.
export function openSessionView(snapshot, requested, { registry, selectSession, connected = true, busy = false } = {}) {
  if (!requested) return null;
  const current = snapshot.sessions.find(session => targetKey(session) === targetKey(requested) &&
    session.projectId === requested.projectId && session.workspaceId === requested.workspaceId &&
    snapshot.projects.some(project => project.id === session.projectId && project.workspaceId === session.workspaceId));
  const view = selectSession(current ?? requested);
  if (!view || !view.visible || targetKey(view.target) !== targetKey(requested) || registry.get(requested) !== view) return null;
  const log = isLaunchLog(current ?? requested);
  if (['connected', 'connecting'].includes(view.state)) {
    if (!log) registry.focus(requested);
    return view;
  }
  // A fully loaded log is retained in its existing renderer, not replayed on
  // every navigation. The explicit View log action can still reload it.
  if (log && view.reason === 'session_eof') return view;
  const project = current && snapshot.projects.find(item => item.id === current.projectId && item.workspaceId === current.workspaceId);
  if (!snapshot.stale && !current?.stale && !project?.stale && sessionControls(snapshot, current, { connected, busy }).attach) {
    registry.attach(current);
    if (!log) registry.focus(current);
  }
  return view;
}

export function projectActivity(sessions) {
  return { sessions: sessions.filter(session => session.kind !== 'launch' && ACTIVE.has(session.state)).length,
    launches: sessions.filter(session => session.kind === 'launch' && !isLaunchLog(session) && ['waiting', 'running'].includes(session.launchState)).length };
}

export const isHistoricalSession = session => ['stopped', 'failed'].includes(session?.state) || isPastSchedulerRecord(session);

export function projectStatusSummary(project, sessions, { serviceConnected = true, workspace = null } = {}) {
  if (!project) return '';
  const diagnostic = serviceConnected && workspaceDiagnosticLabel(workspace);
  if (diagnostic) return `Activity unverified · ${diagnostic}`;
  if (serviceConnected && project.stale && observationStatusLabel(project)) return `Activity unverified · ${observationStatusLabel(project)}`;
  if (!serviceConnected || project.stale || sessions.some(session => session.stale) ||
      project.unavailableReason && !project.controlRestriction) return 'Activity unverified · Observation unavailable';
  const activity = projectActivity(sessions);
  const unknown = sessions.filter(session => session.state === 'unknown' && !isHistoricalSession(session)).length;
  const parts = [];
  if (activity.sessions || !unknown) parts.push(`${activity.sessions} active session${activity.sessions === 1 ? '' : 's'}`);
  if (unknown) parts.push(`${unknown} unverified`);
  if (activity.launches) parts.push(`${activity.launches} Botainer command${activity.launches === 1 ? '' : 's'} in progress`);
  parts.push(project.controlRestriction ? 'Controls unavailable' : 'Service connected');
  return parts.join(' · ');
}

export function projectSessionGroups(sessions, { selectedTarget = null, showHistory = false,
  showAllHistory = false, historyLimit = 5 } = {}) {
  const ordered = [...sessions].sort((a, b) => (Date.parse(b.createdAt ?? '') || 0) -
    (Date.parse(a.createdAt ?? '') || 0) || targetKey(a).localeCompare(targetKey(b)));
  const current = ordered.filter(session => !isHistoricalSession(session));
  const history = ordered.filter(isHistoricalSession);
  const visibleHistory = showHistory ? history.slice(0, showAllHistory ? history.length : historyLimit) : [];
  const selectedHistory = selectedTarget && history.find(session => targetKey(session) === targetKey(selectedTarget));
  // Keep a selected old run reachable even outside the latest-five window or
  // with the global history preference off. No terminal is opened by grouping.
  if (selectedHistory && !visibleHistory.includes(selectedHistory)) visibleHistory.push(selectedHistory);
  return { current, history: visibleHistory, historyCount: history.length,
    hiddenHistoryCount: history.length - visibleHistory.length, selectedHistory: selectedHistory || null };
}

export function relativeTimestamp(value, now = Date.now()) {
  if (typeof value !== 'string') return '';
  const timestamp = Date.parse(value ?? '');
  if (!Number.isFinite(timestamp)) return '';
  const seconds = Math.floor((now - timestamp) / 1000);
  if (seconds < -60) return `at ${new Date(timestamp).toISOString().slice(0, 16).replace('T', ' ')} UTC`;
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

export function runtimeStartedAt(session) {
  if (!session || session.kind === 'launch') return '';
  if (Number.isFinite(Date.parse(session.startedAt ?? ''))) return session.startedAt;
  // Existing local shell adapters use createdAt for Docker StartedAt. A cluster
  // createdAt is submission time; it cannot establish when an allocation began.
  if (Object.hasOwn(session, 'schedulerState') || ['queued', 'starting'].includes(session.state)) return '';
  return Number.isFinite(Date.parse(session.createdAt ?? '')) ? session.createdAt : '';
}

export function sessionTimestamp(session, now = Date.now()) {
  const startedAt = runtimeStartedAt(session);
  const value = relativeTimestamp(startedAt || session.createdAt, now);
  if (!value) return '';
  return `${startedAt ? 'Started' : 'Requested'} ${value}`;
}

export function sessionRecordedEnd(session, now = Date.now()) {
  const value = relativeTimestamp(session?.recordedEndedAt, now);
  return value ? `End recorded ${value}` : '';
}

export function projectLaunchTimestamp(sessions, now = Date.now(), lastLaunchAt = '') {
  const latest = sessions.reduce((value, session) => Math.max(value, Date.parse(runtimeStartedAt(session)) || 0), Date.parse(lastLaunchAt) || 0);
  return latest ? `Last launch ${relativeTimestamp(new Date(latest).toISOString(), now)}` : '';
}

export function observedAgents(sessions) {
  return [...new Set(sessions.filter(session => session.kind !== 'launch' && !isHistoricalSession(session))
    .map(session => recordedAgent(session)).filter(Boolean))].sort();
}

export function recordedAgent(session) {
  const agent = typeof session?.agent === 'string' ? session.agent.trim() : '';
  return /^(agent not recorded|unknown|not reported)$/i.test(agent) ? '' : agent;
}

export function projectListSignals(snapshot, project, sessions, { connected = true } = {}) {
  const workspace = workspaceFor(snapshot, project);
  const unavailable = !connected || snapshot.stale || project.stale || sessions.some(session => session.stale) ||
    workspace && workspace.status !== 'available' || project.unavailableReason && !project.controlRestriction;
  const warning = !connected ? 'Service offline' : workspace?.status === 'unavailable' ? workspaceDiagnosticLabel(workspace) || 'Connection unavailable'
    : workspace && workspace.status !== 'available' ? 'Connection not verified'
    : project.unavailableReason && !project.controlRestriction ? 'Project unavailable'
      : project.controlRestriction ? 'Controls unavailable' : '';
  if (unavailable) return { text: 'Activity unverified', warning };
  const current = sessions.filter(session => !isHistoricalSession(session));
  const counts = ['running', 'starting', 'queued'].map(state => {
    const count = current.filter(session => session.kind !== 'launch' && session.state === state).length;
    return count ? `${count} ${state}` : '';
  }).filter(Boolean);
  const launches = projectActivity(current).launches;
  const unknown = current.filter(session => session.state === 'unknown' && !hasNoRecordedJob(session)).length;
  const noJob = current.filter(hasNoRecordedJob).length;
  if (launches) counts.push(`${launches} launching`);
  if (unknown) counts.push(`${unknown} unverified`);
  if (noJob) counts.push(`${noJob} without recorded jobs`);
  return { text: counts.join(' · ') || 'No current sessions', warning };
}

export function observedBellKey(session, view, target, event) {
  if (event?.kind !== 'bell' || !session || !view || session.state !== 'running' || isLaunchLog(session) ||
      view.state !== 'connected' || targetKey(session) !== targetKey(target) || targetKey(view.target) !== targetKey(target)) return null;
  return targetKey(session);
}

export const BELL_NOTICE = 'Terminal bell observed in this connected view. This is an advisory signal, not a confirmed request for input. Unconnected sessions are not monitored. Select the session or use Mark seen to clear the badge; this does not answer an agent prompt.';

export function workbenchChoices(snapshot, { openViews = [], query = '', openOnly = false, bells = new Map() } = {}) {
  const projects = snapshot.projects ?? [], sessions = snapshot.sessions ?? [];
  const labels = sessionDisplayLabels(sessions);
  const workspaceLabel = entity => snapshot.workspaces?.find(item => item.id === entity.workspaceId)?.label || entity.machineLabel || 'This connection';
  const retained = new Map(openViews.map(item => [targetKey(item), item]));
  const candidates = new Map(retained);
  if (!openOnly) for (const session of sessions) {
    if (!isHistoricalSession(session) && !isLaunchLog(session) && !candidates.has(targetKey(session))) candidates.set(targetKey(session), session);
  }
  const items = [];
  for (const [key, original] of candidates) {
    const current = sessions.find(item => targetKey(item) === key && item.projectId === original.projectId && item.workspaceId === original.workspaceId);
    const session = current ?? original;
    const project = projects.find(item => item.id === session.projectId && item.workspaceId === session.workspaceId);
    const host = hostTerminalWarning(snapshot, session, original);
    items.push({ kind: 'session', key, session: original, host, bell: bells.has(key),
      label: `${project?.name || session.projectId} · ${(current && labels.get(key)) || sessionDisplayLabel(session)}`,
      detail: [workspaceLabel(session), recordedAgent(session), host ? HOST_BADGE : '',
        retained.has(key) ? 'Open view' : 'Session', current ? sessionStateLabel(session) : 'No longer verified', bells.has(key) ? 'Bell observed' : ''].filter(Boolean).join(' · ') });
  }
  if (!openOnly) for (const project of projects) items.push({ kind: 'project', key: `project:${project.id}`, project,
    label: project.name || project.id, host: isHostExecution(snapshot, project), bell: false,
    detail: ['Project overview', workspaceLabel(project), isHostExecution(snapshot, project) ? HOST_BADGE : '',
      observedAgents(sessions.filter(session => session.projectId === project.id && session.workspaceId === project.workspaceId)).join(', ')].filter(Boolean).join(' · ') });
  const words = String(query).toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
  return items.filter(item => words.every(word => `${item.label} ${item.detail}`.toLocaleLowerCase().includes(word)));
}

export function restoreWorkbenchViews(preferences, snapshot, { registry, views }) {
  const restored = reconcileWorkbenchPreferences(preferences, snapshot);
  const accepted = [];
  for (const identity of restored.openViews) {
    const session = snapshot.sessions.find(item => item.contextNamespace === identity.contextNamespace &&
      item.runtimeId === identity.runtimeId && item.projectId === identity.projectId && (item.workspaceId ?? null) === identity.workspaceId);
    if (!session) continue;
    try {
      // Restoring presentation never invokes attach/start or replays input.
      const view = registry.ensure(session);
      views.set(view.key, session); accepted.push(identity);
    } catch { /* An unavailable renderer must not turn restore into a launch. */ }
  }
  return { ...restored, openViews: accepted,
    selectedTarget: restored.selectedTarget && accepted.some(item => targetKey(item) === targetKey(restored.selectedTarget)) ? restored.selectedTarget : null };
}

export function projectWorkflow(snapshot, project, { canStart = false } = {}) {
  if (!project) return 'Choose a project in the sidebar. New session starts work; Connect opens a session that is already running. Open terminal tabs stay available across machines.';
  const sessions = snapshot.sessions.filter(session => session.projectId === project.id);
  const activity = projectActivity(sessions);
  if (isHostExecution(snapshot, project)) return `${HOST_BADGE}. ` +
    (sessions.some(session => session.state === 'unknown')
      ? 'A session outcome is unverified. Refresh and inspect it before starting more work. Reconnect never launches another agent. '
      : activity.sessions ? 'Connect opens an existing agent terminal. New session starts separate work in this folder. '
        : canStart ? (sessionAgentOptions(snapshot, project).length > 1
          ? 'Choose an installed agent and New session to open its terminal. '
          : 'Choose New session to start the configured agent in this folder. ')
          : 'Host-agent launch is unavailable for this project. ') + HOST_ACCESS +
    (sessions.length ? ' Show recent history includes sessions started by this dashboard.' : '');
  const native = capability(snapshot, 'nativeCliLaunch', project);
  const trial = native && preparedTrial(snapshot, project);
  const scope = trial ? ' This local trial uses a cached Alpine shell with networking off and no AI-agent credentials.' : '';
  const noJobCount = sessions.filter(hasNoRecordedJob).length;
  const noJobNotice = noJobCount ? ` Botainer retains ${noJobCount} session record${noJobCount === 1 ? '' : 's'} with no scheduler job ID. A missing ID does not establish whether a job ever ran; review the corresponding launch log for its outcome.` : '';
  if (sessions.some(session => isProjectSetup(session) && !isLaunchLog(session) && ['waiting', 'running'].includes(session.launchState))) {
    return 'Project setup is in progress. Resume its Project setup · Botainer CLI console to answer Botainer’s initialization prompts. A session starts only when you choose New session after setup completes.';
  }
  if (activity.launches) return 'A Botainer launch is already in progress. Select its Launch · Botainer CLI entry to read preflight output and answer any prompts. Connect resumes that console; New session does not reconnect it.' + scope;
  if (!canStart && projectStartUnavailableMessage(project)) return projectStartUnavailableMessage(project) + noJobNotice + scope;
  if (sessions.some(session => session.state === 'unknown' && !hasNoRecordedJob(session) && !isHistoricalSession(session))) return 'Some session or launch outcomes are unverified. Select the entry and Refresh to inspect its state before starting more work. Reconnecting never submits another launch.' + noJobNotice + scope;
  if (activity.sessions) return (projectPrimaryAction(snapshot, project)?.kind === 'connect'
    ? 'Connect session opens the running session’s terminal. '
    : 'Choose a session below or in the sidebar to open its terminal or see why it is unavailable. ') +
    (trial ? 'This local trial allows one active session per project. Connect to the current session, or Stop it before starting another.'
      : 'Queued sessions wait for their allocation. New session creates separate work; it does not reconnect an existing session.') + noJobNotice + scope;
  const start = canStart ? native
    ? 'Choose New session to open Botainer’s actual CLI. Read its preflight details and warnings, then answer any prompts in that terminal. After a successful launch, the dashboard opens the session terminal; preflight output remains in its Launch log tab.'
    : 'Choose New session to start work from this project’s saved defaults.'
    : projectStartUnavailableMessage(project) || 'No active sessions are reported. Session launch is unavailable for this project.';
  return start + (capability(snapshot, 'agentOverride', project) ? ' Project default uses the saved agent; Claude or Codex applies to the next session only.' : '') +
    (sessions.length ? ' Enable Show recent history to revisit previous runs and launch logs.' : '') + noJobNotice + scope;
}

export function singleFlight(action) {
  let pending = null;
  return (...args) => {
    if (!pending) pending = Promise.resolve().then(() => action(...args)).finally(() => { pending = null; });
    return pending;
  };
}

export function clusterSettingsDetails(settings) {
  if (!settings || typeof settings !== 'object' || Array.isArray(settings)) return [];
  const display = (value, suffix = '') =>
    typeof value === 'string' && value !== '' || typeof value === 'number' && Number.isFinite(value) ? `${value}${suffix}` : '';
  return [
    ['Site', display(settings.site)], ['Preset', display(settings.preset)], ['SSH alias', display(settings.sshAlias)],
    ['Partition', display(settings.partition)], ['Time limit', display(settings.timeMinutes, ' minutes')],
    ['CPUs', display(settings.cpus)], ['Memory', display(settings.memoryMiB, ' MiB')],
    ['Private profile file', display(settings.profilePath)], ['Qualification', display(settings.qualification)],
  ].filter(([, value]) => value !== '');
}

export function sessionGuidance(session, { viewState, reason, canAttach = false, canStart = false, canStop = false,
  remote = false, serviceConnected = true, startUnavailableReason = '' } = {}) {
  const knownLaunchOutcome = session?.kind === 'launch' && ['completed', 'declined', 'failed'].includes(session.launchState);
  const unavailable = knownLaunchOutcome ? (typeof session.launchReason === 'string' ? session.launchReason : '')
    : typeof session?.unavailableReason === 'string' ? session.unavailableReason
    : session?.kind === 'launch' && typeof session.launchReason === 'string' ? session.launchReason : '';
  const observation = [session?.lastKnownState ? `Last known state: ${session.lastKnownState}.` : '',
    session?.lastObservedAt ? `Last observed: ${session.lastObservedAt}.` : ''].filter(Boolean).join(' ');
  const result = (title, message, note, action = null) => ({ title, message, note, action });
  if (!viewState) return result('', '', '');
  if (!serviceConnected) return result('Session status may be stale',
    'The local service inventory is unavailable. Refresh when the service is reachable; this does not establish whether the session has ended.',
    viewState === 'connected' ? 'Terminal connected; inventory is unavailable.' : 'Inventory unavailable. Session state is unverified.');
  if (session?.kind === 'launch' && session.consoleEndReason === 'timeout') return result(
    `${isProjectSetup(session) ? 'Project setup' : 'Botainer launch'} console timed out`,
    ['The dashboard closed this CLI connection after its time limit. Its pending prompt cannot be resumed.', unavailable,
      'Read the retained log and Refresh to confirm the project and session state before another request.'].filter(Boolean).join(' '),
    'Console closed · read-only log. No automatic retry or relaunch will occur.', canAttach ? 'log' : null);
  if (session?.kind === 'launch' && ['waiting', 'running'].includes(session.launchState) &&
      (session.consoleEnded === true || reason === 'session_eof')) return result('Botainer CLI console ended',
    [unavailable, 'The CLI connection ended. Refresh to check its recorded outcome before another request; the previous prompt may no longer be available.'].filter(Boolean).join(' '),
    'Input is disabled. No automatic retry or relaunch will occur.', session.consoleEnded === true && canAttach ? 'log' : null);
  if (isProjectSetup(session)) {
    if (session.launchState === 'completed') return result('Project setup completed',
      'Botainer’s initialization command finished. Review the project configuration, then choose New session when you are ready to launch. This setup log does not contain a running agent.',
      'Setup log · read-only. Viewing it never starts a session.', canAttach ? 'log' : null);
    if (['declined', 'failed', 'unknown'].includes(session.launchState)) return result(
      session.launchState === 'unknown' ? 'Project setup outcome is unverified' : `Project setup ${session.launchState}`,
      unavailable || 'Read Botainer’s output and inspect the project folder before another setup request. Files may have been written; no session was launched by project setup.',
      'Project setup will not be retried automatically.', canAttach ? 'log' : null);
    return result(viewState === 'connected' ? 'Botainer CLI · project setup' : 'Open the project setup console',
      viewState === 'connected' ? 'Read Botainer’s initialization prompts and type your answers below. Project setup configures the folder; it does not start an agent.'
        : 'Connect to this same initialization command to read its output and answer prompts. Do not repeat Add project to recover the console.',
      viewState === 'connected' ? 'Native Botainer init · type answers here.' : 'Input is disabled until connected and will not be replayed.',
      canAttach && !['connected', 'connecting'].includes(viewState) ? 'connect' : null);
  }
  if (session?.kind === 'launch') {
    if (session.launchState === 'completed') return result('Botainer launch completed',
      session.resultTarget ? 'The created session is listed separately in this project. Select that session and Connect to use its terminal. View launch log shows Botainer’s preflight and launch output again.'
        : 'The CLI finished, but its resulting session is not yet identified. Refresh to inspect the result before starting more work.',
      'Launch log · read-only. Viewing it never starts a session.', canAttach ? 'log' : null);
    if (session.launchState === 'declined') return result('Launch declined',
      unavailable || 'Botainer did not start a session. Its warnings and your response remain in this launch log.',
      'No session was started. Nothing will be retried automatically.', canAttach ? 'log' : null);
    if (session.launchState === 'failed') return result('Botainer launch failed',
      unavailable || 'Read the CLI output below for the failure. Inspect the project state before trying a new launch.',
      'Launch failed. No automatic retry will occur.', canAttach ? 'log' : null);
    if (!['waiting', 'running'].includes(session.launchState)) return result('Launch outcome is unverified',
      unavailable || 'The service cannot confirm this launch outcome. Inspect the project state; do not submit another launch until it is resolved.',
      'Unknown launch outcome. No automatic retry will occur.', canAttach && isLaunchLog(session) ? 'log' : null);
    if (viewState === 'connected') return result('Botainer CLI · launch in progress',
      'Preflight details, warnings and any confirmation prompts appear below. Type replies here; a successful launch opens the session and keeps this launch log.',
      'Botainer CLI · read its warnings and type answers directly here.');
    if (viewState === 'connecting') return result('Opening Botainer CLI…',
      'The native command’s warnings and prompts will appear here. Input is enabled when connected.',
      'Connecting to this launch console; no second launch is being submitted.');
    return result('Botainer launch console is disconnected',
      'Reconnect to the same launch console to read its output and answer any prompt. Do not start another session to recover this console.',
      'Input is disabled and will not be replayed.', canAttach ? 'connect' : null);
  }
  const observationStatus = observationStatusLabel(session);
  if (session?.stale && observationStatus) {
    const last = lastReportedSession(session);
    const detail = session.observationStatus === 'delayed' ? 'The latest inventory check is still pending.'
      : session.observationStatus === 'failed' ? 'The latest inventory check failed.' : 'The last inventory is too old to verify current state.';
    return { ...result(observationStatus,
      [detail, last, 'Checks continue automatically. New controls wait for verification; this does not establish that the session stopped. See Settings → Machines for connection details.'].filter(Boolean).join(' '),
      [viewState === 'connected' ? 'Terminal connected' : 'Terminal controls unavailable', observationStatus, last].filter(Boolean).join(' · ')),
      quiet: viewState === 'connected' };
  }
  if (hasNoRecordedJob(session)) return result('No scheduler job recorded',
    'Botainer retained this session record without a scheduler job ID. This does not establish whether a job ever ran or has stopped. Review the corresponding launch log for its outcome, and Refresh to check the recorded state.',
    'No verified terminal target. The record is retained; nothing will be retried automatically.');
  if (isPastSchedulerRecord(session)) return result('Past record · outcome unverified',
    ['A successful scheduler check did not find this recorded job active. The agent’s outcome and exact end time remain unverified.',
      unavailable, 'The original record and any recorded timestamps are retained.'].filter(Boolean).join(' '),
    'Past scheduler record · no verified terminal target.');
  if (hasActiveAllocationWithoutTerminal(session)) return result(
    session.schedulerState === 'RUNNING' ? 'Allocation running · persistent terminal unavailable' : 'Active allocation · persistent terminal unavailable',
    [`The scheduler reports this allocation as active${schedulerReason(session.schedulerState) ? ` (${session.schedulerState})` : ''}.`,
      unavailable, 'The dashboard has not verified the original agent’s persistent terminal. An active allocation alone does not establish a reconnectable agent session. No replacement agent is started.'].filter(Boolean).join(' '),
    'Allocation observed · agent terminal unverified. Input and session controls remain unavailable.');
  if (!session || !['running', 'starting', 'queued', 'stopped', 'failed'].includes(session.state)) {
    return result('Session status is unverified',
      [unavailable || 'The latest observation could not establish this session’s state.', observation,
        'Refresh to check again. No new session will be started by reconnecting.'].filter(Boolean).join(' '),
      viewState === 'connected' ? 'Terminal connected; runtime state is unverified.' : 'Session state is unverified; terminal attachment is unavailable.');
  }
  if (['stopped', 'failed'].includes(session.state)) return result(
    session.state === 'failed' ? 'This session failed' : 'This session has stopped',
    [unavailable && session.controlRestriction !== 'external-terminal-owner-unverified' ? unavailable : 'There is no running terminal here.', !canStart && startUnavailableReason
      ? startUnavailableReason : canStart ? 'Select a running session, or start a new session.' : 'Select a running session to inspect its state.'].filter(Boolean).join(' '),
    canStart ? 'Session ended. Select a running session or start a new one.'
      : startUnavailableReason || 'Session ended. Select a running session; a new launch is currently unavailable.', canStart ? 'start' : null);
  if (session.state === 'queued') return result('Waiting for an allocation',
    [schedulerReason(session.queueReason) ? `Scheduler reason: ${schedulerReason(session.queueReason)}.` : 'The scheduler has not started this allocation.',
      'The terminal becomes available after the session starts and its owner is verified.'].join(' '),
    'Queued. No terminal is available yet.');
  if (session.state === 'starting') return result('Session is starting',
    unavailable || 'The launch request is recorded. Wait for the running session and its terminal owner to be verified; do not submit another launch.',
    'Starting. Terminal readiness is not yet confirmed.');
  if (session.state === 'running' && !canAttach &&
      (session.controlRestriction === 'original-owner-ended' || isOrphanContainerStop(session))) return result(
    'Container running · launcher ended',
    [unavailable || 'The original Botainer launcher has ended. The running container no longer has its original managed terminal.',
      'Running confirms the container state, not that its agent is working. Reconnecting cannot recover the ended launcher. Stopping this container interrupts all work inside it; credential-helper cleanup may remain unconfirmed.'].join(' '),
    'Original terminal unavailable. No replacement agent will be started.',
    canStop && isOrphanContainerStop(session) ? 'stop' : null);
  if (session.state === 'running' && !canAttach && session.controlRestriction === 'external-terminal-owner-unverified') return result(
    'Running elsewhere · use the original terminal',
    [unavailable || 'This session was opened elsewhere, and the dashboard cannot verify its original terminal owner.',
      'Continue in the terminal where you started it. Taking over a local session opened outside the dashboard is not supported yet; closing that terminal does not enable attachment here. Do not start a second agent to reconnect. A verified Stop action is separate and ends the selected session.'].join(' '),
    'Use the original terminal. Taking over this external session is not supported yet.', canStop ? 'stop' : null);
  if (session.state === 'running' && !canAttach && !['connected', 'connecting'].includes(viewState)) return result(
    'Observed running · control unavailable',
    [unavailable || 'This session is reported running, but its terminal is not controllable through the selected profile.',
      'The dashboard has no verified terminal connection for this session. This does not mean the agent has stopped.'].join(' '),
    'Observed running; terminal control is unavailable. Input is disabled and will not be replayed.', canStop ? 'stop' : null);
  if (viewState === 'connecting') return result('Connecting to your session…',
    'Checking the session and opening its terminal. Typing becomes available when connected. Disconnect view cancels this connection without stopping the session.',
    'Waiting for attachment. Input is disabled.');
  if (viewState === 'connected') return result('', '',
    'Click to type. Closing this view or losing its connection does not stop the session.');
  if (viewState === 'disconnected') return result(
    reason === 'connection_timeout' ? 'The terminal connection timed out' : 'The terminal is disconnected',
    unavailable || (remote
      ? 'The remote view ended; no session stop was requested. No manual detach is needed before a connection drops. Reconnect verifies the original owner and previous view cleanup. Input will not be replayed.'
      : 'Connection loss does not stop the session. No manual detach is needed before a connection drops. Check the local service, then try Reconnect. Input is disabled.'),
    'Connection lost. Input is disabled and will not be replayed.', canAttach ? 'connect' : null);
  return result(canAttach ? 'Connect to this session' : 'Terminal attachment is unavailable',
    unavailable || (remote
      ? 'This view is closed; the session was not stopped. Previous view cleanup may still be completing. Connect checks the original owner before resuming; an unconfirmed previous attachment will be refused.'
      : canAttach ? 'Open the running terminal, then type directly into it. Connecting keeps the existing session.'
        : 'Terminal attachment is unavailable for this session. Refresh to check its current state.'),
    remote ? 'View closed; no session stop was requested.' : canAttach ? 'Select Connect to use this terminal.' : 'Terminal attachment is unavailable.',
    canAttach ? 'connect' : null);
}

export function sessionGuidanceButton(guidance, { stopLabel = 'Stop session', viewState, setup = false } = {}) {
  if (guidance.action === 'stop') return { control: 'stop-session', label: stopLabel, danger: true };
  if (guidance.action === 'log') return { control: 'connect', label: `View ${setup ? 'setup' : 'launch'} log`, danger: false };
  if (guidance.action === 'start') return { control: 'new-session', label: 'Start a new session', danger: false };
  if (guidance.action === 'connect') return { control: 'connect', label: viewState === 'disconnected' ? 'Reconnect terminal' : 'Connect terminal', danger: false };
  return null;
}

export function configCanSave(draft) {
  return Boolean(draft && !draft.requiresReload && draft.text !== draft.savedText && draft.validation?.valid === true &&
    draft.validation.text === draft.text && draft.validation.revision === draft.revision);
}

export function configCanRequestSave(snapshot, target, draft, options = {}) {
  return Boolean(draft && !draft.pending && !draft.requiresReload && draft.text !== draft.savedText &&
    configurationWritable(snapshot, target, draft, options));
}

export function configurationFormat(draft, target) {
  return ['yaml', 'json'].includes(draft?.format) ? draft.format : target?.scope === 'dashboard' ? 'json' : 'yaml';
}

export function configurationWritable(snapshot, target, draft, { connected = true } = {}) {
  if (!target || !draft?.writable || !connected) return false;
  if (target.scope === 'dashboard') return capability(snapshot, 'dashboardConfigWrite');
  if (target.scope !== 'project') return false;
  const project = snapshot.projects.find(item => item.id === target.projectId);
  return Boolean(project && capability(snapshot, 'configWrite', project));
}

export function configurationStatus(snapshot, target, draft, options = {}) {
  if (draft?.pending) return draft.phase === 'validating' ? 'Validating this exact draft… Nothing has been saved.'
    : draft.phase === 'saving' ? 'Saving the validated draft…' : 'Configuration request in progress…';
  if (!configurationWritable(snapshot, target, draft, options)) {
    if (options.connected === false) return 'Read-only: the dashboard service is unavailable. Your draft is retained.';
    if (draft?.readOnlyReason) return `Read-only${target?.scope === 'project' ? ' for this project' : ''}: ${draft.readOnlyReason}`;
    if (target?.scope === 'dashboard') return 'Read-only: reconnect the service or check dashboard configuration permissions, then reload.';
    const project = snapshot.projects.find(item => item.id === target?.projectId);
    const owner = workspaceFor(snapshot, project);
    if (projectUnavailableMessage(project)) return `Read-only for this project: ${projectUnavailableMessage(project)} Your draft is retained.`;
    if (!project || owner && owner.status !== 'available') return 'Read-only for this project: its current connection or identity is not verified. Refresh, then reload the file.';
    if (snapshot.sessions.some(session => session.projectId === project.id &&
        (ACTIVE.has(session.state) || session.state === 'unknown'))) {
      return 'Read-only for this project: an active or unverified session currently prevents editing. Resolve its state, then reload the configuration.';
    }
    return 'Read-only for this project. Check its connection, active sessions and configuration permissions, then reload.';
  }
  if (draft.requiresReload) return draft.reloadReason || 'Save was not confirmed. Your draft is retained. Reload the saved file before another save.';
  if (draft.text === draft.savedText) return 'Saved version loaded · no unsaved changes.';
  if (draft.validation && (draft.validation.text !== draft.text || draft.validation.revision !== draft.revision)) {
    return 'Unsaved draft · the earlier validation no longer matches this text and revision. Validate and save checks again.';
  }
  if (draft.validation?.valid === false) return 'Unsaved draft · validation failed. Fix the errors below, then Validate and save.';
  if (configCanSave(draft)) return 'Unsaved draft · validation passed. Validate and save checks the current revision again before writing.';
  return 'Unsaved draft · Validate and save checks this text before writing. Your draft is retained while you switch panels.';
}

export function configDocument(value) {
  if (!value || typeof value.text !== 'string' || value.text.length > 262144 ||
      typeof value.revision !== 'string' || !value.revision || value.revision.length > 160) {
    throw new Error('The service returned an unsupported configuration document.');
  }
  return { text: value.text, savedText: value.text, revision: value.revision, writable: value.writable !== false,
    path: typeof value.path === 'string' ? value.path.slice(0, 4096) : null,
    format: ['yaml', 'json'].includes(value.format) ? value.format : null,
    readOnlyReason: typeof value.readOnlyReason === 'string' ? value.readOnlyReason.slice(0, 2000) : '',
    validation: null, pending: false, phase: null, requiresReload: false, reloadReason: '', report: '' };
}

// One user action, two independently guarded requests. Validation never grants a
// later write for changed text, a changed revision, or newly revoked permission.
export async function submitConfigurationDraft(draft, { check, save = null, writable, onChange = () => {} }) {
  if (draft.pending) return { status: 'busy' };
  if (!writable()) return { status: 'read-only' };
  if (draft.requiresReload) return { status: 'reload-required' };
  if (save && draft.text === draft.savedText) return { status: 'unchanged' };
  const submitted = Object.freeze({ text: draft.text, revision: draft.revision });
  const sameDraft = () => draft.text === submitted.text && draft.revision === submitted.revision;
  const reportLines = value => Array.isArray(value) ? value.filter(line => typeof line === 'string').slice(0, 40) : [];
  draft.pending = true; draft.phase = 'validating'; draft.report = ''; onChange();
  try {
    const checked = await check(submitted);
    if (!sameDraft()) {
      draft.validation = null; draft.report = 'The draft changed while validation ran. Nothing was saved; validate the current text again.';
      return { status: 'draft-changed' };
    }
    if (checked?.revision !== undefined && checked.revision !== submitted.revision) {
      draft.validation = null; draft.requiresReload = true;
      draft.reloadReason = 'The saved configuration revision changed. Your draft is retained. Reload the saved file before another save.';
      draft.report = draft.reloadReason; return { status: 'revision-changed' };
    }
    draft.validation = { ...submitted, valid: checked?.valid === true };
    const warnings = reportLines(checked?.warnings);
    draft.report = draft.validation.valid ? ['Validation passed for this exact draft.', ...warnings.map(line => `Warning: ${line}`)].join('\n')
      : reportLines(checked?.errors).join('\n') || 'Validation failed. Nothing was saved; correct the configuration and check it again.';
    if (!draft.validation.valid) return { status: 'invalid' };
    if (!save) return { status: 'checked' };
    if (!writable()) {
      draft.report = 'Validation passed, but editing permission changed. Nothing was saved. Your draft is retained.';
      return { status: 'read-only' };
    }
    draft.phase = 'saving'; onChange();
    if (!sameDraft() || !configCanSave(draft) || !writable()) {
      draft.report = 'The draft or editing permission changed before saving. Nothing was saved.';
      return { status: 'changed-before-save' };
    }
    const result = await save(submitted);
    if (result?.saved !== true) throw new Error(reportLines(result?.errors).join('\n') ||
      'The service did not confirm this configuration was saved. Reload the file before trying again.');
    const saved = configDocument(result);
    if (saved.text !== submitted.text) throw new Error('The service acknowledged different configuration text. Your draft is retained; reload the saved file to inspect the outcome.');
    if (sameDraft()) draft.text = saved.text;
    draft.savedText = saved.text; draft.revision = saved.revision; draft.writable = saved.writable;
    draft.format = saved.format ?? draft.format; draft.readOnlyReason = saved.readOnlyReason; draft.validation = null;
    draft.report = [result.restartRequired ? 'Saved. Restart is required for launch modes that use this registration file. Separately selected runtime profiles are unchanged.' : 'Saved. New sessions will use these defaults.',
      ...(typeof result.recovery?.path === 'string' ? [`Private recovery copy (rotating history): ${result.recovery.path.slice(0, 4096)}${typeof result.recovery.id === 'string' ? ` · ID ${result.recovery.id.slice(0, 64)}` : ''}`] : []),
      ...warnings.map(line => `Warning: ${line}`)].join('\n');
    return { status: 'saved', result };
  } catch (error) {
    const revisionConflict = error?.message === 'config-revision-conflict';
    draft.validation = null;
    draft.report = revisionConflict ? 'The configuration file changed on disk. Your draft is retained. Reload and reconcile the saved file before trying to save again.' : error.message;
    if (revisionConflict || draft.phase === 'saving') {
      draft.requiresReload = true;
      draft.reloadReason = revisionConflict ? draft.report : 'Save was not confirmed. Your draft is retained. Reload the saved file before another save.';
    }
    return { status: 'error' };
  } finally {
    draft.pending = false; draft.phase = null; onChange();
  }
}

export function configurationTarget(scope, project = null) {
  if (scope === 'dashboard') return Object.freeze({ scope, key: 'dashboard', endpoint: '/api/dashboard/config', projectId: null });
  if (scope !== 'project') throw new TypeError('An explicit configuration scope is required.');
  if (!project) return null; // A missing project never promotes the action to global settings.
  if (typeof project.id !== 'string' || !project.id) throw new TypeError('A project identity is required.');
  return Object.freeze({ scope, key: `project:${project.id}`, endpoint: `/api/projects/${encodeURIComponent(project.id)}/config`, projectId: project.id });
}

export function validateSnapshot(value) {
  if (!value || !Array.isArray(value.projects) || !Array.isArray(value.sessions) ||
      value.projects.length > 10000 || value.sessions.length > 10000 || !value.capabilities) {
    throw new Error('The service returned an unsupported inventory.');
  }
  const projectIds = new Set(), sessionIds = new Set(), projectWorkspaces = new Map();
  let workspaceIds = null;
  if (value.workspaces !== undefined) {
    if (!Array.isArray(value.workspaces) || value.workspaces.length > 100) throw new Error('The service returned unsupported machine scopes.');
    workspaceIds = new Set();
    for (const workspace of value.workspaces) {
      if (!workspace || typeof workspace.id !== 'string' || !workspace.id || typeof workspace.label !== 'string' ||
          !workspace.capabilities || workspaceIds.has(workspace.id)) throw new Error('The service returned an ambiguous machine scope.');
      workspaceIds.add(workspace.id);
    }
  }
  for (const project of value.projects) {
    if (typeof project.id !== 'string' || !project.id || typeof project.name !== 'string' || projectIds.has(project.id) || workspaceIds && !workspaceIds.has(project.workspaceId)) {
      throw new Error('The service returned an ambiguous project identity.');
    }
    projectIds.add(project.id);
    projectWorkspaces.set(project.id, project.workspaceId);
  }
  for (const session of value.sessions) {
    if (typeof session.contextNamespace !== 'string' || !session.contextNamespace ||
        typeof session.runtimeId !== 'string' || !session.runtimeId || !projectIds.has(session.projectId) || sessionIds.has(targetKey(session)) ||
        workspaceIds && (session.workspaceId !== projectWorkspaces.get(session.projectId))) {
      throw new Error('The service returned an ambiguous session identity.');
    }
    sessionIds.add(targetKey(session));
  }
  return value;
}

export function sortProjects(snapshot, { query = '', sort = 'recent', pins = [] } = {}) {
  const pinned = new Set(pins), needle = query.trim().toLocaleLowerCase();
  const summaries = new Map(snapshot.projects.map(project => [project.id, { active: 0,
    time: Date.parse(project.lastLaunchAt ?? '') || 0, text: [project.name, project.path, project.machineLabel, project.installationLabel,
      isHostExecution(snapshot, project) ? HOST_BADGE : ''].filter(Boolean).join(' ') }]));
  for (const session of snapshot.sessions) {
    const summary = summaries.get(session.projectId);
    if (!summary) continue;
    if (ACTIVE.has(session.state)) summary.active++;
    summary.time = Math.max(summary.time, Date.parse(runtimeStartedAt(session)) || 0);
    summary.text += ` ${session.label ?? ''} ${sessionDisplayLabel(session)} ${session.agent ?? ''} ${session.state ?? ''} ${session.launchState ?? ''} ${session.runtimeId}`;
  }
  return snapshot.projects.filter(project => !needle || summaries.get(project.id).text.toLocaleLowerCase().includes(needle))
    .sort((a, b) => Number(pinned.has(b.id)) - Number(pinned.has(a.id)) ||
      (sort === 'active' ? summaries.get(b.id).active - summaries.get(a.id).active : 0) ||
      (sort === 'name' ? 0 : summaries.get(b.id).time - summaries.get(a.id).time) || a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
}

export function projectListView(snapshot, { workspaceId = null, query = '', sort = 'recent', pins = [], bellTargets = null } = {}) {
  const scoped = snapshot.projects.filter(project => !workspaceId || project.workspaceId === workspaceId);
  const bellProjects = bellTargets && new Set(snapshot.sessions.filter(session => bellTargets.has(targetKey(session))).map(session => session.projectId));
  const projects = sortProjects(snapshot, { query, sort, pins }).filter(project => (!workspaceId || project.workspaceId === workspaceId) &&
    (!bellProjects || bellProjects.has(project.id)));
  const filtered = Boolean(workspaceId || query.trim() || bellTargets);
  return { projects, scopedCount: scoped.length, filtered,
    countLabel: filtered ? `${projects.length} of ${snapshot.projects.length}` : String(projects.length) };
}

export async function api(path, { method = 'GET', body, fetch = globalThis.fetch, timeoutMs = 15000, bearer = getBearer(), mutation = method !== 'GET' } = {}) {
  if (!path.startsWith('/api/') || path.includes('?') || path.includes('#')) throw new Error('Invalid service endpoint.');
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(path, { method, credentials: 'same-origin', cache: 'no-store', redirect: 'error',
      headers: { ...authHeaders(bearer), ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
      body: body === undefined ? undefined : JSON.stringify(body), signal: controller.signal });
    if (response.status === 401) {
      clearBearer();
      if (typeof document !== 'undefined') globalThis.location.replace('/unlock');
      throw new Error('Browser access expired or was signed out. Pair this browser using a fresh one-time code from the local launcher.');
    }
    if (!response.ok) {
      let message = `The service refused this request (${response.status}).`;
      try {
        const detail = await response.json();
        if (typeof detail.detail === 'string') message = detail.detail.slice(0, 400);
        else if (typeof detail.error === 'string') message = detail.error.slice(0, 400);
      } catch { /* Fixed status is sufficient. */ }
      throw new Error(message);
    }
    return response.status === 204 ? null : await response.json();
  } catch (error) {
    if (mutation && (error.name === 'AbortError' || error instanceof TypeError)) {
      throw new Error('The operation outcome is unknown. Refresh and inspect the session state before deciding what to do. This request will not be retried automatically.');
    }
    throw error;
  } finally { clearTimeout(timer); }
}

export function boot(document = globalThis.document) {
  const $ = id => document.getElementById(id);
  const app = $('app');
  $('lock-dashboard').addEventListener('click', () => showDialog({
    title: 'Sign out this browser?', confirm: 'Sign out',
    description: 'This forgets this browser’s dashboard access and disconnects its terminal views. It sends no Stop command to agents or jobs. To return, run the pairing command shown on the sign-in page for a fresh one-time code. No service restart is needed. Use Close view instead if you only want to leave a terminal.',
    action: async () => {
      const response = await fetch('/logout', { method: 'POST', credentials: 'same-origin', cache: 'no-store',
        redirect: 'manual', headers: authHeaders() });
      if (!response.ok || (await response.json()).locked !== true) throw new Error('The service could not confirm browser access was revoked. Try Sign out again when the service is available.');
      clearBearer();
      registry.disposeAll();
      globalThis.location.replace('/unlock');
    },
  }));
  const text = (id, value) => { $(id).textContent = String(value ?? ''); };
  const node = (tag, content, className) => {
    const element = document.createElement(tag);
    if (content !== undefined) element.textContent = String(content);
    if (className) element.className = className;
    return element;
  };
  const button = (label, click, className) => {
    const element = node('button', label, className); element.type = 'button'; element.addEventListener('click', click); return element;
  };
  let snapshot = { capabilities: {}, projects: [], sessions: [] };
  let sessionLabels = new Map();
  const sessionLabel = session => sessionLabels.get(targetKey(session)) || sessionDisplayLabel(session);
  let selectedProject = null, selectedTarget = null, connectedService = false, refreshGeneration = 0;
  let selectedWorkspace = null;
  const initialPreferences = readWorkbenchPreferences();
  let pendingPreferences = initialPreferences, restoringWorkbench = false, leavingPage = false;
  let savedConnections = null, connectionGeneration = 0;
  const narrowWindow = globalThis.matchMedia('(max-width: 850px)');
  let desktopSidebarShown = true;
  let navigationRevision = 0;
  let settingsMachineId = null;
  let inspector = null, noticeTimer = null, busy = false, modalAction = null, inspectorGeneration = 0;
  let recoveryIntent = null, recoveryTimer = null;
  function setRecoveryIntent(value) {
    recoveryIntent = value;
    clearTimeout(recoveryTimer); recoveryTimer = null;
    if (value?.phase === 'retry') recoveryTimer = setTimeout(() => {
      recoveryTimer = null;
      if (!document.hidden) refresh();
    }, Math.max(0, value.nextAt - Date.now()));
  }
  let refreshConfigControls = null;
  let serviceNotice = '', serviceFailed = false;
  const configDrafts = new Map(), sessionAgentChoices = new Map();
  // Retain setup DOM/editor in this page only. Reading help or returning to a
  // terminal must not destroy an unfinished machine draft or its exact review.
  let connectionPanelHost = null, connectionPanelReady = null, connectionPanelController = null;
  const fileDirectories = new Map(), fileFilters = new Map();
  let filesExpanded = false;
  let pins = [];
  try {
    const stored = JSON.parse(localStorage.getItem('botainer-dashboard:pins:v1') ?? '[]');
    if (Array.isArray(stored)) pins = stored.filter(id => typeof id === 'string').slice(0, 10000);
  } catch { /* Navigation works when browser storage is disabled. */ }
  const views = new Map(), renderers = new Map(), presentedLaunchResults = new Set();
  let terminalTextSize = DEFAULT_TERMINAL_FONT_SIZE;
  try { terminalTextSize = terminalFontSize(Number(localStorage.getItem('botainer-dashboard:terminal-font-size:v1'))); }
  catch { /* The size control still works when browser storage is unavailable. */ }
  const fontSizeControl = $('terminal-font-size');
  for (let size = 10; size <= 24; size++) {
    const option = node('option', `${size} px${size === DEFAULT_TERMINAL_FONT_SIZE ? ' (default)' : ''}`);
    option.value = String(size); fontSizeControl.append(option);
  }
  fontSizeControl.value = String(terminalTextSize);
  fontSizeControl.addEventListener('change', () => {
    terminalTextSize = terminalFontSize(Number(fontSizeControl.value));
    fontSizeControl.value = String(terminalTextSize);
    for (const renderer of renderers.values()) renderer.setFontSize(terminalTextSize);
    try { localStorage.setItem('botainer-dashboard:terminal-font-size:v1', String(terminalTextSize)); }
    catch { /* Retain the preference for this page without blocking resizing. */ }
  });
  let compactProjects = false;
  try { compactProjects = localStorage.getItem('botainer-dashboard:compact-projects:v1') === 'true'; } catch { /* Page-only preference. */ }
  const renderDensity = () => {
    $('sidebar').classList.toggle('compact-projects', compactProjects);
    $('compact-projects').setAttribute('aria-pressed', String(compactProjects));
  };
  renderDensity();
  const projectExpansion = new Map(), historyExpansion = new Map(), fullHistory = new Set(), unreadBells = new Map();
  $('project-search').value = initialPreferences.query;
  $('project-sort').value = initialPreferences.sort;
  $('show-ended').checked = initialPreferences.showEnded;
  for (const [id, expanded] of initialPreferences.projectExpansion) projectExpansion.set(id, expanded);
  desktopSidebarShown = initialPreferences.sidebarShown;
  const maintenanceGuidesOpen = new Set();
  let attentionRenderPending = false;
  const bellBadge = () => {
    const badge = node('span', undefined, 'attention-badge'); badge.title = BELL_NOTICE;
    const icon = node('span', '🔔'); icon.setAttribute('aria-hidden', 'true');
    badge.append(icon, node('span', ' Bell', 'attention-label'));
    badge.setAttribute('aria-label', BELL_NOTICE); return badge;
  };
  function renderAttention() {
    if (attentionRenderPending) return;
    attentionRenderPending = true;
    queueMicrotask(() => { attentionRenderPending = false; renderProjects(); renderTerminalMetadata(); });
  }
  function notice(message) {
    text('notice', message); $('notice').hidden = false;
    clearTimeout(noticeTimer); noticeTimer = setTimeout(() => { $('notice').hidden = true; }, 12000);
  }
  const retainedSelection = () => selectedTarget ? views.get(targetKey(selectedTarget)) ?? null : null;
  const getProject = () => {
    const retained = retainedSelection();
    return snapshot.projects.find(project => project.id === selectedProject && (!retained ||
      project.id === retained.projectId && project.workspaceId === retained.workspaceId)) ?? null;
  };
  const getSession = () => retainedSessionObservation(snapshot, selectedTarget, retainedSelection());
  const getWorkspace = () => displayedWorkspace(snapshot, selectedWorkspace);
  const navigate = action => {
    if (!restoringWorkbench) pendingPreferences = null;
    if (['project', 'settings', 'machine', 'connections', 'help'].includes(action.type) ||
        action.type === 'session' && recoveryIntent && targetKey(action.session) !== targetKey(recoveryIntent)) setRecoveryIntent(null);
    const next = navigateWorkbench({ filterId: selectedWorkspace, projectId: selectedProject, target: selectedTarget,
      panel: inspector, settingsMachineId }, action, snapshot);
    selectedWorkspace = next.filterId; selectedProject = next.projectId; selectedTarget = next.target;
    inspector = next.panel; settingsMachineId = next.settingsMachineId;
    if (connectionPanelController && ['connections', 'help'].includes(inspector)) {
      connectionPanelController.showPage(inspector === 'help' ? 'help' : 'machines');
    }
    navigationRevision++;
    rememberWorkbench();
  };
  function rememberWorkbench() {
    if (pendingPreferences || restoringWorkbench || leavingPage) return;
    const retained = selectedTarget && views.get(targetKey(selectedTarget));
    const project = getProject();
    writeWorkbenchPreferences(undefined, { version: 1, filterId: selectedWorkspace,
      projectId: selectedProject, projectWorkspaceId: project?.workspaceId ?? null,
      selectedTarget: retained || null, openViews: [...views.values()],
      query: $('project-search').value, sort: $('project-sort').value,
      showEnded: $('show-ended').checked, projectExpansion: [...projectExpansion],
      sidebarShown: desktopSidebarShown, focusMode: app.classList.contains('focus-mode') });
  }
  const presentationContext = () => Object.freeze({ revision: navigationRevision, workspaceId: selectedWorkspace,
    projectId: selectedProject, targetKey: selectedTarget ? targetKey(selectedTarget) : null,
    inspector });
  const entityWorkspace = entity => workspaceFor(snapshot, entity);
  const hostBadge = () => { const badge = node('span', HOST_BADGE, 'host-badge'); badge.title = HOST_ACCESS; return badge; };
  const isRemote = entity => {
    const workspace = entityWorkspace(entity);
    return workspace ? Boolean(workspace.clusterSettings) || /cluster|remote/.test(workspace.kind || '')
      : Boolean(snapshot.clusterSettings) || /cluster|remote/.test(snapshot.mode || '');
  };
  const clusterSettings = entity => entityWorkspace(entity)?.clusterSettings ?? (!snapshot.workspaces ? snapshot.clusterSettings : null);
  const projectCreators = () => snapshot.workspaces
    ? snapshot.workspaces.filter(workspace => (!selectedWorkspace || workspace.id === selectedWorkspace) &&
      ['projectCreate', 'projectOpen', 'projectRegister'].some(name => workspaceCapability(snapshot, workspace.id, name)))
    : ['projectCreate', 'projectOpen', 'projectRegister'].some(name => capability(snapshot, name)) ? [{ id: null, label: 'Local workspace' }] : [];
  const allowed = (name, entity) => connectedService && !busy && capability(snapshot, name, entity);
  const registry = new TerminalViewRegistry({
    createTerminal(target, { onInput }) {
      const adapter = createXtermAdapter({ target, container: $('terminal-hosts'), fontSize: terminalTextSize,
        onInput: data => isLaunchLog(snapshot.sessions.find(session => targetKey(session) === targetKey(target))) ? false : onInput(data),
        onAttention: (boundTarget, event) => {
          const session = snapshot.sessions.find(item => targetKey(item) === targetKey(boundTarget));
          const key = observedBellKey(session, registry.get(boundTarget), boundTarget, event);
          if (!key || unreadBells.has(key)) return;
          unreadBells.set(key, true); renderAttention();
        },
        onDimensions: (boundTarget, cols, rows) => registry.resize(boundTarget, cols, rows) });
      renderers.set(targetKey(target), adapter); return adapter;
    },
    createTransport(target, callbacks) {
      return createWebSocketTransport({ target, callbacks, terminal: renderers.get(targetKey(target)) });
    },
    onStateChange(event) {
      setRecoveryIntent(advanceTerminalRecovery(recoveryIntent, event));
      queueMicrotask(renderTerminalMetadata);
      if (event.state === 'disconnected') {
        if (views.get(event.key)?.kind === 'launch') {
          // CLI completion normally closes this transport. Observe its recorded
          // result promptly rather than calling normal completion a lost session.
          queueMicrotask(() => refresh());
        } else {
          notice(recoveryIntent?.phase === 'retry' ? 'Terminal connection interrupted. The dashboard will recheck this exact session and make up to three bounded reconnection attempts. Input is disabled and will not be replayed.'
            : event.reason === 'connection_timeout' ? 'Terminal connection timed out. Nothing you typed was sent. Check the local service, then choose Reconnect.'
              : 'The terminal connection ended; no session stop was requested. Input is disabled. Reconnect checks the original session and will not replay input.');
          queueMicrotask(() => refresh());
        }
      }
    },
    onHistoryRelease(event) {
      if (recoveryIntent && targetKey(recoveryIntent) === event.key) setRecoveryIntent(null);
      renderers.delete(event.key); views.delete(event.key);
      if (unreadBells.delete(event.key)) renderAttention();
      if (event.reason === 'capacity') notice('A detached view was closed to free memory. Its local terminal history was released; its session was not stopped.');
    },
  });
  const desktopLayout = initDesktopLayout({ document,
    onResize: () => { if (selectedTarget) registry.get(selectedTarget)?.terminal.measure(); } });
  function restoreWorkbench() {
    if (!pendingPreferences || snapshot.stale || snapshot.workspaces?.some(item => item.status === 'checking')) return;
    restoringWorkbench = true;
    let restored;
    try {
      restored = restoreWorkbenchViews(pendingPreferences, snapshot, { registry, views });
      pendingPreferences = null;
      selectedWorkspace = restored.filterId;
      selectedProject = restored.projectId;
      for (const session of views.values()) if (session.kind === 'launch') presentedLaunchResults.add(targetKey(session));
      if (restored.selectedTarget) {
        const session = views.get(targetKey(restored.selectedTarget));
        if (session) selectSession(session);
      }
      setSidebar(narrowWindow.matches ? false : restored.sidebarShown);
      setFocus(restored.focusMode);
    } finally {
      restoringWorkbench = false;
    }
    rememberWorkbench();
    if (restored.openViews.length) notice('Open views restored without connecting. Choose Connect to check and reconnect an original session. Terminal contents and typed input were not saved.');
  }
  document.addEventListener('focusin', event => {
    // Clicking Connect requests focus after attachment. Moving to another
    // control while it connects cancels that request, so editors keep typing.
    if (selectedTarget && event.target !== document.body && event.target !== document.documentElement &&
        !$('terminal-hosts').contains(event.target)) registry.cancelPendingFocus(selectedTarget);
  });
  // Modal closure, disabled buttons and refreshed DOM can move focus without
  // user navigation. Only explicit user interaction cancels result presentation;
  // scope changes are independently compared against the captured action.
  for (const type of ['pointerdown', 'keydown', 'input']) document.addEventListener(type, event => {
    if (event.isTrusted) { navigationRevision++; pendingPreferences = null; }
  }, true);
  function appendConnectionRecovery(content, workspace, focusTargets) {
    const presentation = workspaceConnectionPresentation(workspace, { connected: connectedService });
    content.append(node('strong', presentation.summary), node('br'), node('span', presentation.message));
    if (presentation.recovery) content.append(node('br'), node('span', `Next: ${presentation.recovery}`));
    if (presentation.command) content.append(node('br'), node('span', 'In your terminal: '), node('code', presentation.command));
    if (presentation.recovery) {
      const check = button('Check again', () => refresh(), 'quiet');
      const help = button('Connection help', async () => {
        if (!connectedService) {
          navigate({ type: 'settings' }); renderInspector();
        } else if (inspector === 'machine' && settingsMachineId === workspace?.id) {
          navigate({ type: 'help' }); await renderInspector();
          await connectionPanelController?.openHelp(workspace.executionKind === 'host' ? 'host'
            : workspaceConnectionDiagnostic(workspace)?.code?.startsWith('ssh-') ? 'ssh' : 'maintenance');
        } else {
          navigate(workspace?.id ? { type: 'machine', id: workspace.id } : { type: 'connections' }); renderInspector();
        }
      }, 'quiet');
      check.dataset.navigationKey = `connection-check:${workspace?.id || ''}`;
      help.dataset.navigationKey = `connection-help:${workspace?.id || ''}`;
      focusTargets?.set(check.dataset.navigationKey, check); focusTargets?.set(help.dataset.navigationKey, help);
      content.append(node('br'), check, node('span', ' '), help);
    }
  }
  function renderWorkspaceSelector() {
    const selector = $('workspace-select');
    const value = selectedWorkspace || '';
    const options = [{ id: '', label: 'All connections' }, ...(snapshot.workspaces ?? [])];
    // Preserve the focused selector while polling when its choices are unchanged.
    const signature = JSON.stringify([connectedService, options.map(item => [item.id, workspaceConnectionLabel(item), item.status, item.executionKind, workspaceDiagnosticLabel(item)])]);
    if (selector.dataset.signature !== signature) {
      selector.replaceChildren();
      for (const workspace of options) {
        const option = node('option', (workspace.executionKind === 'host' ? '⚠ ' : '') + workspaceConnectionLabel(workspace) + (workspace.id && !connectedService ? ' · unverified' : workspace.status === 'unavailable' ? ` · ${workspaceDiagnosticLabel(workspace) || 'unavailable'}` : workspace.status === 'checking' ? ' · checking' : ''));
        option.value = workspace.id; selector.append(option);
      }
      selector.dataset.signature = signature;
    }
    selector.value = value;
    selector.hidden = !snapshot.workspaces;
    const shortcuts = $('workspace-shortcuts'), content = document.createDocumentFragment(), focusTargets = new Map();
    const pending = pendingConnections(snapshot, savedConnections);
    shortcuts.hidden = !snapshot.workspaces?.length && !pending.length;
    shortcuts.classList.toggle('has-pending-connections', pending.length > 0);
    for (const observed of workspaceSummaries(snapshot)) {
      const item = { ...observed, status: connectedService ? observed.status : 'unverified' };
      const control = button('', () => selectWorkspace(item.id), 'workspace-shortcut');
      const host = isHostExecution(snapshot, { workspaceId: item.id });
      const workspace = snapshot.workspaces.find(workspace => workspace.id === item.id);
      const agents = connectionAgentSummary(workspace);
      const label = workspaceConnectionLabel(workspace);
      control.classList.toggle('host-connection', host);
      control.dataset.navigationKey = item.id; focusTargets.set(item.id, control);
      control.setAttribute('aria-pressed', String(selectedWorkspace === item.id));
      control.setAttribute('aria-label', `${label}${host ? ` · ${HOST_BADGE}` : ''}: ${item.projectCount} projects, ${item.status}. Filter project list.`);
      control.title = `${label}${host ? ` · ${HOST_BADGE}` : ''} · ${item.projectCount} reported projects · ${item.status}. This only filters the list; selected work stays open.`;
      const identity = node('span', undefined, 'workspace-shortcut-identity');
      identity.append(node('span', `${host ? '⚠ ' : ''}${connectionDisplayLabel(workspace)}`, 'workspace-shortcut-label'));
      if (agents) identity.append(node('span', agents, 'workspace-shortcut-agents'));
      control.append(identity,
        node('span', `${item.projectCount} · ${item.status}`, `workspace-shortcut-status ${stateClass(item.status)}`));
      content.append(control);
    }
    for (const entry of pending) {
      const label = connectionDisplayLabel(entry);
      const control = button('', () => { navigate({ type: 'connections' }); renderInspector(); },
        `workspace-shortcut pending-connection${entry.kind === 'host' ? ' host-connection' : ''}`);
      control.dataset.navigationKey = `pending:${entry.id}`; focusTargets.set(control.dataset.navigationKey, control);
      control.title = `${label}: saved, not loaded. Restart the dashboard service to use it; reloading the browser is not enough. Open Settings to review.`;
      control.setAttribute('aria-label', control.title);
      const identity = node('span', undefined, 'workspace-shortcut-identity');
      identity.append(node('span', `${entry.kind === 'host' ? '⚠ ' : ''}${label}`, 'workspace-shortcut-label'),
        node('span', 'Saved · restart required', 'workspace-shortcut-agents'));
      control.append(identity, node('span', 'Settings →', 'workspace-shortcut-status'));
      content.append(control);
    }
    updateNavigationList(shortcuts, content, document.activeElement, focusTargets);
    const workspace = getWorkspace();
    const hostFilter = isHostExecution(snapshot, workspace ? { workspaceId: workspace.id } : null);
    $('sidebar').classList.toggle('host-scope', hostFilter);
    $('host-inventory-note').hidden = !hostFilter;
    text('host-inventory-note', HOST_INVENTORY_NOTE);
    if (workspace && (!connectedService || workspace.status !== 'available')) {
      const statusContent = document.createDocumentFragment(), statusTargets = new Map();
      appendConnectionRecovery(statusContent, workspace, statusTargets);
      updateNavigationList($('workspace-status'), statusContent, document.activeElement, statusTargets);
    } else {
      const statusContent = document.createDocumentFragment();
      statusContent.append(node('span', !connectedService ? 'Service offline · observations unverified' : workspace
        ? `${workspaceConnectionLabel(workspace)} · connected` : snapshot.workspaces ? `${snapshot.workspaces.length} connections · filters the list below` : ''));
      updateNavigationList($('workspace-status'), statusContent, document.activeElement, new Map(), selector);
    }
    $('workspace-status').title = $('workspace-status').textContent;
    const recovery = sshRecoveryCommand(workspace || entityWorkspace(getProject()));
    $('copy-ssh').hidden = !recovery;
    $('copy-ssh').title = recovery ? `Copy ${recovery} for your own terminal. The dashboard does not execute this command or collect login prompts.` : '';
  }
  function renderProjects() {
    rememberWorkbench();
    renderWorkspaceSelector();
    text('project-list-title', preparedTrial(snapshot) && (!snapshot.workspaces || snapshot.workspaces.every(item =>
      preparedTrial(snapshot, { workspaceId: item.id }))) ? 'Trial projects' : 'Projects');
    const container = $('projects'), content = document.createDocumentFragment();
    const focusTargets = new Map();
    const trackFocus = (element, key) => { element.dataset.navigationKey = key; focusTargets.set(key, element); return element; };
    const list = projectListView(snapshot, { workspaceId: selectedWorkspace, query: $('project-search').value, sort: $('project-sort').value, pins,
      bellTargets: $('show-bells').checked ? unreadBells : null });
    const projects = list.projects;
    const byProject = new Map();
    for (const session of snapshot.sessions) {
      if (!byProject.has(session.projectId)) byProject.set(session.projectId, []);
      byProject.get(session.projectId).push(session);
    }
    text('project-count', list.countLabel);
    const bellCount = snapshot.sessions.filter(session => unreadBells.has(targetKey(session))).length;
    text('bell-filter-label', `With bell (${bellCount})`);
    $('project-count').title = `${projects.length} projects shown; ${snapshot.projects.length} reported across all workspaces`;
    $('show-all-projects').hidden = !list.filtered;
    const listedWorkspace = getWorkspace();
    const inventoryNotice = projectInventoryNotice(snapshot, selectedWorkspace, { connected: connectedService });
    if (inventoryNotice) content.append(node('p', inventoryNotice, 'list-empty'));
    if (!projects.length) content.append(node('p', $('show-bells').checked ? 'No matching projects with a recorded bell. Unopened and disconnected terminals are not monitored.' : list.scopedCount ? 'No matching projects.' : listedWorkspace && listedWorkspace.status !== 'available'
      ? workspaceAvailability(listedWorkspace) : isHostExecution(snapshot, listedWorkspace ? { workspaceId: listedWorkspace.id } : null)
        ? 'No folders added to this host profile yet. Choose Add project, then Open an existing folder.'
        : inventoryNotice ? 'No projects are currently reported.' : 'No registered projects in this workspace.', 'list-empty'));
    for (const project of projects) {
      const item = node('div', undefined, `project-item${isHostExecution(snapshot, project) ? ' host-project' : ''}`);
      const heading = node('div', undefined, `project-heading${project.id === selectedProject ? ' selected' : ''}`);
      const expanded = projectExpansion.get(project.id) ?? project.id === selectedProject;
      const childrenId = `project-sessions-${encodeURIComponent(project.id)}`;
      const toggle = trackFocus(button('', () => {
        projectExpansion.set(project.id, !expanded); renderProjects();
      }, 'quiet project-toggle'), `toggle:${project.id}`);
      toggle.setAttribute('aria-label', `${expanded ? 'Collapse' : 'Expand'} sessions for ${project.name}`);
      toggle.title = `${expanded ? 'Hide' : 'Show'} sessions for ${project.name}. This only changes the list.`;
      toggle.setAttribute('aria-expanded', String(expanded)); toggle.setAttribute('aria-controls', childrenId);
      const select = trackFocus(button('', () => activateProject(project.id), 'project-select'), `project:${project.id}`);
      select.append(node('span', project.name, 'project-name'));
      select.setAttribute('aria-current', String(project.id === selectedProject && !selectedTarget));
      const projectSessions = byProject.get(project.id) ?? [];
      const signals = projectListSignals(snapshot, project, projectSessions, { connected: connectedService });
      const agents = observedAgents(projectSessions);
      const meta = node('span', undefined, 'project-meta');
      const workspace = entityWorkspace(project);
      if (isHostExecution(snapshot, project)) meta.append(hostBadge());
      else if (projectExecutionLabel(snapshot, project)) meta.append(node('span', 'Shell trial', 'trial-badge'));
      if (workspace?.label || project.machineLabel) meta.append(node('span', workspace?.label || project.machineLabel, 'machine-badge'));
      meta.append(node('span', agents.slice(0, 2).join(', ') + (agents.length > 2 ? ` +${agents.length - 2}` : ''), 'project-agents'),
        node('span', signals.text, 'project-signals'));
      if (signals.warning) meta.append(node('span', signals.warning, 'project-warning'));
      meta.title = [workspace?.label || project.machineLabel, project.installationLabel, agents.length ? `Reported agents: ${agents.join(', ')}` : '', signals.text, signals.warning].filter(Boolean).join(' · ');
      select.append(meta);
      const launchTime = projectLaunchTimestamp(projectSessions, Date.now(), project.lastLaunchAt);
      if (launchTime) select.append(node('span', launchTime, 'project-time'));
      select.title = [project.path ?? project.name, meta.title, launchTime, project.unavailableReason].filter(Boolean).join(' · ');
      const pinned = pins.includes(project.id);
      const pin = trackFocus(button(pinned ? '★' : '☆', () => {
        pins = pinned ? pins.filter(id => id !== project.id) : [...pins, project.id];
        try { localStorage.setItem('botainer-dashboard:pins:v1', JSON.stringify(pins)); } catch { /* Session-only pins. */ }
        renderProjects();
      }, `quiet pin${pinned ? ' pinned' : ''}`), `pin:${project.id}`);
      pin.setAttribute('aria-label', `${pinned ? 'Unpin' : 'Pin'} ${project.name}`);
      pin.setAttribute('aria-pressed', String(pinned));
      heading.append(toggle, select);
      if (projectSessions.some(session => unreadBells.has(targetKey(session)))) heading.append(bellBadge());
      heading.append(pin); item.append(heading);
      const children = node('div', undefined, 'project-sessions'); children.id = childrenId; children.hidden = !expanded;
      const appendSession = session => {
        const selected = selectedTarget && targetKey(session) === targetKey(selectedTarget);
        const entry = trackFocus(button('', () => {
          // Equal rendered rows retain their listeners across polls. Resolve
          // current metadata without changing the button's exact owner identity.
          const current = snapshot.sessions.find(item => targetKey(item) === targetKey(session) &&
            item.projectId === session.projectId && item.workspaceId === session.workspaceId);
          if (current) activateSession(current);
        }, `session-item${selected ? ' selected' : ''}${isHostExecution(snapshot, session) ? ' host-session' : ''}`), `session:${targetKey(session)}`);
        const state = node('span', undefined, `state-dot ${stateClass(session.state)}`); state.setAttribute('aria-hidden', 'true');
        const description = node('span', undefined, 'session-description');
        description.append(node('span', sessionLabel(session), 'session-label'));
        const agentLabel = recordedAgent(session) || 'Agent not recorded';
        if (session.kind !== 'launch' && sessionLabel(session).toLocaleLowerCase() !== agentLabel.toLocaleLowerCase()) {
          description.append(node('span', agentLabel, 'session-agent-name'));
        }
        if (isHostExecution(snapshot, session)) description.append(hostBadge());
        const time = [sessionTimestamp(session), sessionRecordedEnd(session)].filter(Boolean).join(' · ');
        if (time) description.append(node('span', time, 'session-time'));
        entry.append(state, description);
        if (unreadBells.has(targetKey(session))) entry.append(bellBadge());
        entry.append(node('span', sessionStateLabel(session), `session-state ${stateClass(session.state)}`));
        entry.title = [sessionLabel(session), session.state || 'unknown', session.unavailableReason || schedulerReason(session.queueReason),
          isHostExecution(snapshot, session) ? HOST_BADGE : '', session.agent, time, session.createdAt, `${session.contextNamespace} / ${session.runtimeId}`].filter(Boolean).join(' · ');
        entry.setAttribute('aria-label', `${entry.title}${unreadBells.has(targetKey(session)) ? ' · Terminal bell observed' : ''}`);
        entry.setAttribute('aria-current', String(Boolean(selected)));
        children.append(entry);
      };
      if (expanded) {
        const showHistory = historyExpansion.get(project.id) ?? $('show-ended').checked;
        const groups = projectSessionGroups(projectSessions, { selectedTarget, showHistory, showAllHistory: fullHistory.has(project.id) });
        children.append(node('p', `${groups.current.some(session => session.state === 'unknown') ? 'Current / unverified' : 'Current'} (${groups.current.length})`, 'session-group-title'));
        if (!groups.current.length) children.append(node('p', 'No current session', 'session-group-empty'));
        for (const session of groups.current) appendSession(session);
        if (groups.historyCount) {
          const historyLabel = projectSessions.some(isPastSchedulerRecord) ? 'Past records and history' : 'History';
          const history = trackFocus(button(`${historyLabel} (${groups.historyCount})`, () => {
            historyExpansion.set(project.id, !showHistory); renderProjects();
          }, 'quiet history-toggle'), `history:${project.id}`);
          history.setAttribute('aria-label', `${showHistory ? 'Hide' : 'Show'} recent past records, ended sessions and launch logs for ${project.name}`);
          history.setAttribute('aria-expanded', String(showHistory)); children.append(history);
          if (!showHistory && groups.selectedHistory) children.append(node('p', 'Selected from history', 'session-group-empty'));
          for (const session of groups.history) appendSession(session);
          if (showHistory && (groups.hiddenHistoryCount || fullHistory.has(project.id) && groups.historyCount > 5)) {
            children.append(trackFocus(button(fullHistory.has(project.id) ? 'Show latest 5' : `Show all ${groups.historyCount}`, () => {
              if (fullHistory.has(project.id)) fullHistory.delete(project.id); else fullHistory.add(project.id);
              renderProjects();
            }, 'quiet history-more'), `history-more:${project.id}`));
          }
        }
      }
      item.append(children); content.append(item);
    }
    updateNavigationList(container, content, document.activeElement, focusTargets);
  }
  function renderHeading() {
    const project = getProject();
    const host = isHostExecution(snapshot, project);
    text('project-title', project?.name ?? (selectedTarget ? 'Retained terminal' : 'Choose a project'));
    text('project-context', entityWorkspace(project)?.label || project?.machineLabel || (selectedTarget ? 'Project unavailable' : snapshot.workspaces ? 'All workspaces' : 'Local workspace'));
    $('project-context').title = [entityWorkspace(project)?.label || project?.machineLabel, project?.installationLabel].filter(Boolean).join(' / ');
    const executionLabel = projectExecutionLabel(snapshot, project);
    $('project-execution').hidden = !project || !executionLabel;
    $('project-execution').textContent = executionLabel;
    $('project-execution').className = host ? 'host-badge' : 'trial-badge';
    $('project-execution').title = host ? HOST_ACCESS : 'Prepared shell trial; this is not a production agent workspace.';
    text('project-path', project?.path ?? 'Select a registered project to get started.');
    $('project-path').title = project ? `${project.path || project.name} · Browse files` : 'Select a project first';
    $('project-path').disabled = !project;
    $('show-config').disabled = !project;
    $('show-config').hidden = Boolean(project && host);
    $('show-project-details').hidden = !project || Boolean(selectedTarget);
    $('show-files').disabled = !project || !allowed('filesRead', project);
    $('show-config').title = project ? capability(snapshot, 'configWrite', project)
      ? `Edit defaults for ${project.name}` : `View project configuration for ${project.name}`
      : 'Select a project to view its configuration';
    const projectSessions = snapshot.sessions.filter(session => session.projectId === project?.id);
    const activity = projectActivity(projectSessions);
    text('project-status', projectStatusSummary(project, projectSessions, { serviceConnected: connectedService, workspace: entityWorkspace(project) }));
    $('project-status').title = project?.unavailableReason || (connectedService ? 'The local service is connected; each remote session reports its own observation state.' : 'The inventory may be stale.');
    const nativeLaunchPending = (capability(snapshot, 'nativeCliLaunch', project) || capability(snapshot, 'nativeCliProjectSetup', project)) && activity.launches > 0;
    const nativeSessionActive = capability(snapshot, 'nativeCliLaunch', project) && preparedTrial(snapshot, project) && activity.sessions > 0;
    const agentOptions = sessionAgentOptions(snapshot, project);
    $('new-session').disabled = !project || nativeLaunchPending || !allowed('startSession', project) || host && !agentOptions.length;
    $('new-session').title = nativeLaunchPending ? 'A Botainer command is already in progress. Resume its existing CLI console to read or answer prompts.'
      : nativeSessionActive ? 'This local trial allows one active session per project. Connect to the current session, or Stop it before starting another.'
      : $('new-session').disabled ? projectStartUnavailableMessage(project) || 'Session launch is unavailable for this project and backend.'
      : host ? `Start a separate session in ${project.path}. Existing sessions keep running and share this folder. ${HOST_ACCESS}`
      : capability(snapshot, 'nativeCliLaunch', project) ? 'Open Botainer CLI with the selected agent or project defaults; read its warnings and answer its prompts in the terminal.'
        : 'Start one new session using the saved project defaults';
    $('session-agent-label').hidden = !project || !capability(snapshot, 'agentOverride', project) || host && agentOptions.length < 2;
    $('session-agent').disabled = $('new-session').disabled;
    const signature = JSON.stringify(agentOptions);
    if ($('session-agent').dataset.options !== signature) {
      $('session-agent').replaceChildren(...agentOptions.map(option => { const entry = node('option', option.label); entry.value = option.value; return entry; }));
      $('session-agent').dataset.options = signature;
    }
    const choice = project && sessionAgentChoices.get(project.id);
    $('session-agent').value = agentOptions.some(option => option.value === choice) ? choice : agentOptions[0]?.value || '';
    const requestedAgent = !$('session-agent-label').hidden && ['claude', 'codex'].includes($('session-agent').value)
      ? $('session-agent').value === 'claude' ? 'Claude' : 'Codex' : '';
    const hostAgentId = $('session-agent').value === 'default' ? project?.defaultAgent ?? entityWorkspace(project)?.defaultAgent : $('session-agent').value;
    const hostAgent = host && project ? configuredHostAgents(snapshot, project).find(agent => agent.id === hostAgentId) : null;
    if (hostAgent?.available === false) {
      $('new-session').disabled = true;
      $('new-session').title = 'This agent executable is missing or changed. Open Settings → Machines → Review agent update. Existing sessions can still reconnect to their original terminal.';
    }
    text('new-session', host ? `⚠ New ${hostAgent?.label || 'agent'} session on host` : `New${requestedAgent ? ` ${requestedAgent}` : ''} session`);
    $('new-session').classList.toggle('host-launch', Boolean(host));
    $('session-agent').classList.toggle('host-agent-select', Boolean(host));
    const launchUnavailable = project && $('new-session').disabled ? $('new-session').title : '';
    $('session-agent-note').hidden = !project || !launchUnavailable && !host && $('session-agent-label').hidden;
    text('session-agent-note', launchUnavailable || (host ? 'Separate session · same folder · no container' : 'New sessions only · saved settings unchanged'));
    $('session-agent').title = host ? 'Choose the agent for a new, separate session in this folder. This does not change the agent in the terminal you are viewing. The default is selected initially.'
      : 'Agent for new sessions. Botainer resolves Project default from saved project configuration at launch. Existing sessions and saved project settings stay unchanged.';
    $('session-agent-note').title = launchUnavailable || $('session-agent').title;
    $('add-project').disabled = !connectedService || busy || !projectCreators().length;
    $('add-project').title = $('add-project').disabled ? 'Project setup is unavailable for this backend.' : 'Create a project or register a folder in a configured workspace';
  }
  function renderTerminalMetadata() {
    const active = selectedTarget ? registry.get(selectedTarget) : null;
    const current = getSession();
    document.title = unreadBells.size ? `(${unreadBells.size} bell${unreadBells.size === 1 ? '' : 's'}) Botainer dashboard` : 'Botainer dashboard';
    $('next-attention').hidden = !unreadBells.size;
    text('next-attention', `🔔 ${unreadBells.size}`);
    $('next-attention').setAttribute('aria-label', `Next terminal bell (${unreadBells.size})`);
    $('next-attention').title = BELL_NOTICE;
    text('open-session-list', `Sessions${views.size ? ` (${views.size})` : ''}`);
    rememberWorkbench();
    app.classList.toggle('session-selected', Boolean(active));
    const tabs = $('session-tabs'), tabContent = document.createDocumentFragment();
    const focusTargets = new Map();
    if (!active) {
      const tab = button(getProject() ? `${getProject().name} · Overview` : 'Projects', () => selectProject(selectedProject), 'session-tab overview-tab');
      tab.setAttribute('role', 'tab'); tab.setAttribute('aria-selected', 'true');
      tab.dataset.navigationKey = 'overview'; focusTargets.set('overview', tab); tabContent.append(tab);
    }
    for (const [key, item] of views) {
      const session = snapshot.sessions.find(value => targetKey(value) === key &&
        value.projectId === item.projectId && value.workspaceId === item.workspaceId) ?? item;
      const tab = button('', () => {
        // Keep the retained tab's original membership even if a newer inventory
        // reports the same key under a different project or connection.
        activateSession(item);
      }, 'session-tab');
      tab.dataset.targetKey = key; tab.dataset.navigationKey = key; focusTargets.set(key, tab);
      const projectName = snapshot.projects.find(project => project.id === session.projectId)?.name || session.projectId;
      const tabLabel = `${projectName} · ${sessionLabel(session)}`;
      const machineLabel = entityWorkspace(session)?.label;
      const host = hostTerminalWarning(snapshot, session, item);
      tab.classList.toggle('host-tab', host);
      tab.setAttribute('role', 'tab'); tab.setAttribute('aria-selected', String(key === (selectedTarget && targetKey(selectedTarget))));
      tab.tabIndex = key === (selectedTarget && targetKey(selectedTarget)) ? 0 : -1;
      tab.setAttribute('aria-label', [tabLabel, machineLabel, host ? HOST_BADGE : '', unreadBells.has(key) ? 'Terminal bell observed' : ''].filter(Boolean).join(' / '));
      tab.append(node('span', undefined, `state-dot ${stateClass(registry.get(item)?.state)}`), node('span', tabLabel, 'session-label'));
      if (unreadBells.has(key)) tab.append(bellBadge());
      if (host) tab.append(hostBadge());
      if (machineLabel) tab.append(node('span', machineLabel, 'machine-badge'));
      tab.title = `${tabLabel} · ${session.contextNamespace} / ${session.runtimeId}`;
      tabContent.append(tab);
    }
    updateNavigationList(tabs, tabContent, document.activeElement, focusTargets);
    $('terminal-toolbar').hidden = !active;
    $('terminal-bell').hidden = !active || !unreadBells.has(active.key);
    $('terminal-bell').title = BELL_NOTICE;
    const retained = views.get(selectedTarget && targetKey(selectedTarget));
    const hostTerminal = active ? hostTerminalWarning(snapshot, current, retained) : isHostExecution(snapshot, getProject());
    $('terminal-execution').hidden = !hostTerminal;
    $('terminal-execution').title = HOST_ACCESS;
    const terminalAgent = active ? recordedAgent(current || retained) : '';
    text('host-terminal-title', `⚠ HOST${terminalAgent ? ` · ${terminalAgent}` : ' AGENT'} · NO CONTAINER`);
    $('connect').classList.toggle('host-launch', Boolean(active && hostTerminal));
    text('session-title', current ? sessionLabel(current) : active ? 'Retained terminal' : '');
    text('session-context', current ? [current.agent, current.kind === 'launch' ? 'Botainer CLI' : sessionStateLabel(current)].filter(Boolean).join(' · ') : 'Session no longer reported');
    $('session-title').title = current ? `${current.contextNamespace} / ${current.runtimeId}` : '';
    $('terminal-empty').hidden = Boolean(active);
    $('terminal-hosts').hidden = !active;
    const identity = [getProject()?.machineLabel, getProject()?.installationLabel].filter(Boolean).join(' / ') || 'Local service';
    const log = isLaunchLog(current);
    text('connection-state', log ? `${isProjectSetup(current) ? 'Setup' : 'Launch'} log · ${current.launchState} · ${identity}`
      : active ? `${active.state} · ${identity}${current ? ` · ${isProjectSetup(current) ? 'Botainer project setup' : current.kind === 'launch' ? 'Botainer CLI launch' : sessionStateLabel(current)}` : ' · session no longer reported'}` : 'No terminal selected');
    $('connection-state').title = current ? `${current.contextNamespace} / ${current.runtimeId}` : '';
    const controls = sessionControls(snapshot, current, { connected: connectedService, busy });
    const attachable = controls.attach;
    const awaitingConsoleOutcome = current?.kind === 'launch' && !log && active?.reason === 'session_eof';
    $('connect').disabled = !active || !attachable || awaitingConsoleOutcome || ['connected', 'connecting'].includes(active.state);
    text('connect', log ? ['connected', 'connecting'].includes(active?.state) ? 'Loading log…' : `View ${isProjectSetup(current) ? 'setup' : 'launch'} log`
      : awaitingConsoleOutcome ? 'Checking CLI outcome…' : active?.state === 'connected' ? 'Connected' : active?.state === 'connecting' ? 'Connecting…' : active?.state === 'disconnected' ? 'Reconnect' : 'Connect');
    $('connect').title = log ? 'Read the recorded Botainer CLI output. This does not start or reconnect a running session.'
      : current?.kind === 'launch' ? 'Resume this same Botainer CLI console to read warnings and answer any prompts.'
        : 'Open this existing session’s terminal. This never starts a new session.';
    $('connect').classList.toggle('connected', active?.state === 'connected');
    $('detach').disabled = !active || !['connected', 'connecting', 'disconnected'].includes(active.state);
    text('detach', 'Disconnect view');
    $('detach').title = 'Close this terminal connection and keep its history. This does not stop the session; disconnecting manually is optional.';
    $('close-view').disabled = !active;
    $('close-view').title = 'Close this terminal tab and release its local history. This does not stop the session.';
    $('stop-session').disabled = !active || !controls.stop;
    const stopLabel = current ? sessionStopPresentation(snapshot, current).label : 'Stop session';
    text('stop-session', stopLabel);
    $('stop-session').setAttribute('aria-label', stopLabel);
    $('open-search').disabled = !active;
    $('copy-terminal').disabled = !active;
    $('paste-terminal').disabled = !active || active.state !== 'connected' || log || !connectedService;
    const historyAvailable = Boolean(active && canReadTerminalHistory(snapshot, current, { connected: connectedService }));
    $('show-scrollback').disabled = !historyAvailable;
    const historyRefresh = $('refresh-scrollback');
    if (historyRefresh) historyRefresh.disabled = !historyAvailable || historyRefresh.dataset.loading === 'true';
    const remote = isRemote(current || getProject());
    const owner = entityWorkspace(current || getProject());
    const guidanceSession = current && owner && owner.status !== 'available' ? { ...current, unavailableReason: workspaceAvailability(owner) } : current;
    const guidance = sessionGuidance(guidanceSession, { viewState: active?.state, reason: active?.reason,
      canAttach: attachable, canStart: !$('new-session').disabled, canStop: controls.stop, remote, serviceConnected: connectedService,
      startUnavailableReason: $('new-session').disabled ? $('new-session').title : '' });
    text('terminal-note', recoveryIntent?.phase === 'retry' && current && targetKey(recoveryIntent) === targetKey(current)
      ? `Checking the original session before terminal retry ${recoveryIntent.attempts + 1}/3. Input is disabled and will not be replayed.` : guidance.note);
    $('terminal-note').title = $('terminal-note').textContent;
    $('terminal-guidance').hidden = !active || !guidance.title || guidance.quiet === true;
    const action = sessionGuidanceButton(guidance, { stopLabel, viewState: active?.state, setup: isProjectSetup(current) });
    text('terminal-guidance-title', guidance.title); text('terminal-guidance-message', guidance.message);
    text('terminal-guidance-action', action?.label || ''); $('terminal-guidance-action').hidden = !action;
    const hostGuidance = action?.control === 'new-session' ? isHostExecution(snapshot, getProject())
      : action?.control === 'connect' && hostTerminal && guidance.action === 'connect';
    $('terminal-guidance-action').classList.toggle('host-launch', Boolean(hostGuidance));
    if (hostGuidance) text('terminal-guidance-action', action.control === 'new-session' ? $('new-session').textContent
      : active?.state === 'disconnected' ? '⚠ Reconnect host terminal' : '⚠ Connect host terminal');
    $('terminal-guidance-action').dataset.control = action?.control || '';
    $('terminal-guidance-action').classList.toggle('danger', action?.danger === true);
    $('terminal-guidance-action').classList.toggle('primary', action?.danger !== true);
    $('terminal-guidance-action').disabled = !action || $(action.control).disabled;
    if (!active) renderEmpty();
  }
  function renderEmpty() {
    const project = getProject(), workspace = getWorkspace();
    const owner = entityWorkspace(project) || workspace;
    const settings = clusterSettings(project);
    const limit = settings?.timeMinutes ? ` Cluster sessions have a ${settings.timeMinutes}-minute allocation limit.` : '';
    text('empty-title', project?.name ?? workspace?.label ?? 'Choose a project to get started');
    text('empty-message', (owner && owner.status !== 'available' ? workspaceAvailability(owner) : '') || projectStartUnavailableMessage(project) ||
      (!project && isHostExecution(snapshot, workspace ? { workspaceId: workspace.id } : null)
        ? 'Add a folder to this host profile with Add project below the project list, then Open an existing folder. Adding it starts no agent. Afterward, choose the agent and start a separate session without a container.'
        : projectWorkflow(snapshot, project, { canStart: Boolean(project && !$('new-session').disabled) }) + limit));
    const action = projectPrimaryAction(snapshot, project, { connected: connectedService, busy });
    text('empty-action', action?.label || ''); $('empty-action').hidden = !action;
    const hostProject = isHostExecution(snapshot, project);
    $('empty-action').classList.toggle('host-launch', Boolean(action && hostProject));
    if (action && hostProject) text('empty-action', action.kind === 'start' ? $('new-session').textContent : '⚠ Open host session');
    $('host-project-note').hidden = !hostProject;
    text('host-project-note', HOST_INVENTORY_NOTE);
    const chooser = $('project-session-chooser');
    const sessions = projectCurrentSessions(snapshot, project?.id);
    $('terminal-empty').classList.toggle('has-current-sessions', Boolean(sessions.length));
    const content = document.createDocumentFragment(), focusTargets = new Map();
    for (const session of sessions) {
      const host = isHostExecution(snapshot, session);
      const item = button('', () => activateSession(session), `project-session-choice${host ? ' host-session' : ''}`);
      item.dataset.navigationKey = targetKey(session); focusTargets.set(item.dataset.navigationKey, item);
      const controls = sessionControls(snapshot, session, { connected: connectedService, busy });
      const guidance = sessionGuidance(session, { canAttach: controls.attach, canStop: controls.stop,
        remote: isRemote(session), serviceConnected: connectedService });
      item.append(node('strong', sessionLabel(session)), node('span', sessionStateLabel(session), 'muted'));
      if (host) item.append(hostBadge());
      const description = [recordedAgent(session), sessionTimestamp(session),
        controls.attach ? 'Open terminal' : guidance.title || 'Open session details'].filter(Boolean).join(' · ');
      item.append(node('span', description, 'project-session-choice-description'));
      if (!controls.attach && guidance.message) item.append(node('span', guidance.message, 'project-session-choice-description'));
      content.append(item);
    }
    updateNavigationList(chooser, content, document.activeElement, focusTargets);
    chooser.hidden = !sessions.length;
  }
  function selectWorkspace(id) {
    navigate({ type: 'filter', id });
    renderProjects(); renderHeading();
  }
  function revealSelectedTab() {
    if (!isSettingsPage(inspector)) $('session-tabs').querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }
  function selectProject(id) {
    if (selectedTarget) registry.hide(selectedTarget);
    navigate({ type: 'project', id });
    $('terminal-menu').open = false;
    $('terminal-search').hidden = true;
    renderProjects(); renderHeading(); renderTerminalMetadata();
    const project = getProject();
    renderEmpty();
    if (projectUnavailableMessage(project)) notice(projectUnavailableMessage(project));
    renderInspector();
    revealSelectedTab();
    if (globalThis.matchMedia('(max-width: 850px)').matches) setSidebar(false);
  }
  function activateProject(id) {
    const sessions = projectCurrentSessions(snapshot, id);
    if (sessions.length === 1) activateSession(sessions[0]);
    else selectProject(id);
  }
  function activateSession(session) {
    try {
      const view = openSessionView(snapshot, session, { registry, connected: connectedService, busy,
        selectSession: current => selectSession(current, { acknowledge: true }) });
      const current = snapshot.sessions.find(item => targetKey(item) === targetKey(session) &&
        item.projectId === session.projectId && item.workspaceId === session.workspaceId);
      const project = current && snapshot.projects.find(item => item.id === current.projectId && item.workspaceId === current.workspaceId);
      if (view && !isLaunchLog(current) && ['connected', 'connecting'].includes(view.state) &&
          project && !snapshot.stale && !current.stale && !project.stale &&
          sessionControls(snapshot, current, { connected: connectedService, busy }).attach &&
          (!recoveryIntent || targetKey(recoveryIntent) !== targetKey(current))) {
        setRecoveryIntent(terminalRecoveryIntent(current));
      }
      renderTerminalMetadata();
    } catch (error) { notice(error.message); }
  }
  function selectSession(session, { acknowledge = false } = {}) {
    try {
      const view = registry.show(session);
      navigate({ type: 'session', session });
      $('terminal-menu').open = false;
      selectedTarget = view.target;
      views.set(view.key, session);
      if (acknowledge) unreadBells.delete(view.key);
      $('terminal-search').hidden = true;
      renderProjects(); renderHeading(); renderTerminalMetadata();
      view.terminal.measure();
      renderInspector();
      revealSelectedTab();
      if (globalThis.matchMedia('(max-width: 850px)').matches) setSidebar(false);
      return view;
    } catch (error) { notice(error.message); return null; }
  }
  function setSidebar(shown) {
    if (!narrowWindow.matches) desktopSidebarShown = shown;
    app.classList.toggle('sidebar-hidden', !shown);
    $('toggle-projects').setAttribute('aria-expanded', String(shown && !app.classList.contains('focus-mode')));
    desktopLayout.refresh(); rememberWorkbench();
  }
  function setFocus(enabled) {
    if (enabled && inspector) { inspector = null; renderInspector(); }
    app.classList.toggle('focus-mode', enabled);
    $('focus-mode').setAttribute('aria-pressed', String(enabled));
    text('focus-mode', enabled ? 'Exit focus' : 'Focus terminal');
    setSidebar(!app.classList.contains('sidebar-hidden'));
  }
  const refreshConnections = singleFlight(async () => {
    const generation = ++connectionGeneration;
    try {
      const saved = await api('/api/connections', { timeoutMs: 5000 });
      if (generation !== connectionGeneration) return;
      savedConnections = saved;
    } catch {
      if (generation !== connectionGeneration) return;
      // A failed settings read must neither hide loaded workspaces nor present
      // a cached saved selection as current after a service restart.
      savedConnections = null;
    }
    renderWorkspaceSelector();
  });
  const refresh = singleFlight(async () => {
    void refreshConnections();
    const generation = ++refreshGeneration;
    try {
      const value = validateSnapshot(await api('/api/state'));
      if (generation !== refreshGeneration) return;
      snapshot = value; sessionLabels = sessionDisplayLabels(snapshot.sessions); connectedService = true;
      restoreWorkbench();
      connectionPanelController?.refreshActiveMachines?.();
      for (const launch of snapshot.sessions.filter(session => session.kind === 'launch')) {
        const result = nativeLaunchResult(snapshot, launch), key = targetKey(launch);
        const configuredProject = nativeSetupResult(snapshot, launch);
        if (configuredProject && !presentedLaunchResults.has(key)) {
          presentedLaunchResults.add(key);
          if (presentLaunchResult(launch, selectedTarget, inspector)) {
            selectProject(configuredProject.id);
            notice('Project setup completed. Review Project config, then choose New session to start an agent. The setup log remains in its open tab.');
          }
        }
        if (!result || presentedLaunchResults.has(key)) continue;
        presentedLaunchResults.add(key);
        if (presentLaunchResult(launch, selectedTarget, inspector)) {
          selectSession(result);
          setRecoveryIntent(terminalRecoveryIntent(result, { queued: !sessionControls(snapshot, result).attach }));
          if (capability(snapshot, 'attachTerminal', result) && result.state === 'running') {
            try { registry.attach(result); registry.focus(result); }
            catch (error) { notice(error.message); }
          }
        }
      }
      if (selectedWorkspace && !snapshot.workspaces?.some(workspace => workspace.id === selectedWorkspace)) selectedWorkspace = null;
      text('service-state', `Local service · ${snapshot.mode || 'connected'}`);
      serviceNotice = snapshot.notice === 'Choose a workspace to see its runtime and settings.'
        ? 'One dashboard for your registered machines and runtimes. Each workspace is checked independently. Select one in Settings for connection details.'
        : snapshot.notice || (snapshot.mode === 'fixture' ? 'Local terminal transport test. These sessions are deterministic test processes, not Botainer agents.' : '');
      serviceFailed = false;
      text('service-notice', serviceNotice ? (snapshot.mode?.includes('proof') || snapshot.mode === 'fixture' ? 'Isolated trial' : 'Service details') : '');
      $('service-notice').title = serviceNotice;
      $('service-notice').setAttribute('aria-label', serviceNotice ? `${serviceNotice} Open service details.` : 'Service details');
      $('service-notice').hidden = !serviceNotice;
      // A restarted service may first report checking workspaces with no rows.
      // Keep the open view; restore its project when that exact session/project
      // pair returns in the original workspace, without navigating elsewhere.
      if (!getProject()) selectedProject = projectForRetainedSession(snapshot, selectedTarget, retainedSelection())?.id ?? null;
      if (recoveryIntent) {
        const current = snapshot.sessions.find(item => targetKey(item) === targetKey(recoveryIntent));
        if (!current || ['stopped', 'failed'].includes(current.state) || recoveryIntent.expiresAt !== null && Date.now() > recoveryIntent.expiresAt) setRecoveryIntent(null);
      }
      const recover = terminalRecoveryTarget(snapshot, recoveryIntent, selectedTarget,
        { connected: connectedService, hidden: document.hidden, inspector, busy });
      if (recover && ['detached', 'disconnected'].includes(registry.get(recover)?.state)) {
        setRecoveryIntent(beginTerminalRecovery(recoveryIntent));
        try { registry.attach(recover); } // Preserve focus; automatic recovery never types or starts work.
        catch (error) { setRecoveryIntent(null); notice(error.message); }
      }
      renderProjects(); renderHeading(); renderTerminalMetadata();
      refreshConfigControls?.();
      if (['details', 'settings', 'machine'].includes(inspector)) renderInspector();
    } catch (error) {
      if (generation !== refreshGeneration) return;
      connectedService = false; text('service-state', 'Service unavailable · inventory may be stale');
      connectionPanelController?.refreshActiveMachines?.();
      serviceNotice = error.message; serviceFailed = true;
      text('service-notice', 'Service unavailable'); $('service-notice').title = error.message; $('service-notice').hidden = false;
      $('service-notice').setAttribute('aria-label', `${error.message} Open service details.`);
      renderProjects(); renderHeading(); renderTerminalMetadata();
      refreshConfigControls?.();
      if (['details', 'settings', 'machine'].includes(inspector)) renderInspector();
    }
  });
  function showDialog({ title, description, confirm, warning = '', fields = [], action }) {
    text('action-title', title); text('action-description', description); text('confirm-action', confirm);
    $('confirm-action').classList.toggle('host-launch', Boolean(warning));
    $('action-fields').replaceChildren();
    if (warning) {
      const panel = node('section', undefined, 'host-warning'); panel.setAttribute('role', 'note');
      panel.append(node('strong', '⚠ Host execution · no container'), node('p', warning));
      $('action-fields').append(panel);
    }
    for (const field of fields) {
      const label = node('label', field.label); label.htmlFor = `field-${field.name}`;
      const input = node(field.options ? 'select' : 'input'); input.id = label.htmlFor; input.name = field.name; input.required = field.required !== false; input.autocomplete = 'off';
      if (field.options) for (const option of field.options) { const entry = node('option', option.label); entry.value = option.value; input.append(entry); }
      if (field.placeholder) input.placeholder = field.placeholder;
      if (field.value !== undefined) input.value = field.value;
      if (field.maxLength) input.maxLength = field.maxLength;
      $('action-fields').append(label, input);
    }
    modalAction = action; $('action-dialog').showModal();
  }
  async function perform(action) {
    if (busy) return;
    busy = true; renderHeading(); renderTerminalMetadata();
    try { await action(); }
    catch (error) { notice(error.message); }
    finally { busy = false; await refresh(); }
  }
  function addDetail(list, label, value) {
    if (value === null || value === undefined || value === '') return;
    list.append(node('dt', label), node('dd', value));
  }
  async function renderConfig(content, project, generation, scope) {
    const target = configurationTarget(scope, project);
    const dashboard = scope === 'dashboard';
    const readCapability = dashboard ? 'dashboardConfigRead' : 'configRead';
    const writeCapability = dashboard ? 'dashboardConfigWrite' : 'configWrite';
    const entity = dashboard ? null : project;
    if ((!dashboard && !project) || !capability(snapshot, readCapability, entity)) {
      const owner = workspaceFor(snapshot, project);
      content.append(node('p', !dashboard && !project ? 'Select a project to edit its defaults.'
        : !dashboard && projectUnavailableMessage(project) ? `Project configuration is unavailable: ${projectUnavailableMessage(project)}`
          : !dashboard && owner && owner.status !== 'available' ? workspaceAvailability(owner)
            : 'This configuration file is not exposed by the selected profile. Check its configuration permissions in Settings.'));
      return;
    }
    const { endpoint, key } = target;
    const context = node('dl', undefined, 'config-context');
    addDetail(context, 'Applies to', dashboard ? 'Dashboard registration file only' : project.name);
    if (!dashboard) {
      addDetail(context, 'Project folder', project.path);
      addDetail(context, 'Machine', project.machineLabel);
      addDetail(context, 'Botainer installation', project.installationLabel);
    }
    content.append(context);
    const panel = node('div', undefined, 'config-panel'); content.append(panel);
    panel.append(node('p', 'Loading configuration…'));
    try {
      if (!configDrafts.has(key)) {
        if (configDrafts.size >= 32) {
          const clean = [...configDrafts].find(([, draft]) => !draft.pending && draft.text === draft.savedText);
          if (clean) configDrafts.delete(clean[0]);
          else throw new Error('Save or discard an open configuration draft before opening another file.');
        }
        const loaded = configDocument(await api(endpoint));
        if (!configDrafts.has(key)) configDrafts.set(key, loaded);
      }
      if (generation !== inspectorGeneration) return;
      const draft = configDrafts.get(key);
      panel.replaceChildren();
      if (draft.path) panel.append(node('p', draft.path, 'file-location'));
      panel.append(node('p', dashboard ? `${DASHBOARD_CONFIG_SCOPE} Validate and save checks your changes before writing.` : 'Defaults for new sessions in this project. Active or unverified sessions may keep the file read-only. Avoid editing this file elsewhere while saving here: revision checks detect observed changes, but cannot lock out other editors.'));
      const format = configurationFormat(draft, target).toUpperCase();
      const label = node('label', dashboard ? `Dashboard settings ${format}` : `Project ${format} · ${project.name}`); label.htmlFor = 'config-text';
      const editor = node('textarea'); editor.id = label.htmlFor; editor.value = draft.text; editor.spellcheck = false; editor.maxLength = 65536;
      editor.readOnly = !capability(snapshot, writeCapability, entity);
      const status = node('div', undefined, 'draft-status'); status.id = 'config-save-status'; status.setAttribute('role', 'status');
      const report = node('div', draft.report, 'validation-message'); report.setAttribute('role', 'status');
      const setReport = message => { draft.report = message; report.textContent = message; };
      const actions = node('div', undefined, 'editor-actions');
      const validate = button('Check config', () => run(false));
      validate.title = `Validate this ${format} draft without saving it.`;
      const save = button('Validate and save', () => run(true), 'primary');
      save.setAttribute('aria-describedby', status.id);
      const reload = button('Reload saved file', () => {
        const discard = () => { configDrafts.delete(key); renderInspector(); };
        if (draft.text !== draft.savedText) showDialog({ title: 'Discard this draft?', description: 'Your unsaved changes in this configuration file will be replaced with the current saved file.', confirm: 'Discard and reload', action: discard });
        else discard();
      });
      const download = button('Download draft', () => {
        const blob = new Blob([draft.text], { type: 'text/plain;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const link = node('a'); link.href = url;
        link.download = `config-draft.${format === 'YAML' ? 'yaml' : 'json'}`;
        document.body.append(link); link.click(); link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      });
      download.title = 'Keep a local copy before reloading. Configuration can contain private information.';
      const update = () => {
        const writable = configurationWritable(snapshot, target, draft, { connected: connectedService });
        status.textContent = configurationStatus(snapshot, target, draft, { connected: connectedService });
        validate.disabled = draft.pending || !writable || draft.requiresReload;
        save.disabled = !configCanRequestSave(snapshot, target, draft, { connected: connectedService });
        save.textContent = draft.pending ? draft.phase === 'saving' ? 'Saving…' : 'Validating…' : 'Validate and save';
        save.title = status.textContent;
        reload.disabled = draft.pending;
        editor.readOnly = draft.pending || !writable;
      };
      const run = async saveRequested => {
        const result = await submitConfigurationDraft(draft, {
          writable: () => configurationWritable(snapshot, target, draft, { connected: connectedService }),
          check: submitted => api(`${endpoint}/validate`, { method: 'POST', body: submitted, mutation: false }),
          save: saveRequested ? submitted => api(`${endpoint}/save`, {
            method: 'POST', body: { ...submitted, requestId: crypto.randomUUID() } }) : null,
          onChange: () => { editor.value = draft.text; report.textContent = draft.report; update(); },
        });
        if (result.status === 'saved') await refresh();
        update();
        // A replacement panel shares this same pending draft and cannot submit a
        // second write. Re-render it once the original request has completed.
        const stillSelected = dashboard ? inspector === 'dashboard-config' : inspector === 'config' && getProject()?.id === target.projectId;
        if (generation !== inspectorGeneration && stillSelected) renderInspector();
      };
      refreshConfigControls = () => { if (generation === inspectorGeneration) update(); };
      editor.addEventListener('input', () => { draft.text = editor.value; draft.validation = null; setReport(''); update(); });
      actions.append(save, validate, download, reload);
      panel.append(label, editor, status, actions, report); update();
    } catch (error) { if (generation === inspectorGeneration) panel.replaceChildren(node('p', error.message)); }
  }
  async function renderFiles(content, project, path = '') {
    const generation = ++inspectorGeneration;
    const current = () => generation === inspectorGeneration && inspector === 'files' && getProject()?.id === project.id;
    content.replaceChildren(node('p', 'Loading folder…'));
    const endpoint = `/api/projects/${encodeURIComponent(project.id)}`;
    const scope = () => {
      const remote = isRemote(project), host = isHostExecution(snapshot, project);
      const machine = entityWorkspace(project)?.label || project.machineLabel || 'This computer';
      content.append(node('p', `${host ? '⚠ Host folder · no container' : remote ? 'Remote project folder · over SSH' : 'Local project folder'} · ${machine}`, 'file-scope'));
      content.append(node('p', 'Read-only browser · UTF-8 text previews up to 64 KiB. Hidden and protected names, symbolic links and special files are omitted; some linked files cannot be previewed. This shows the project folder on this machine, not container-only files. Opening Files starts no agent or job.', 'file-limits'));
    };
    const copy = async (value, label) => {
      try {
        if (!globalThis.navigator?.clipboard?.writeText) throw new Error('Clipboard unavailable');
        await globalThis.navigator.clipboard.writeText(value);
        notice(`${label} copied.${isRemote(project) && label.includes('path') ? ' This path belongs to the remote machine.' : ''}`);
      } catch { notice('Clipboard access is unavailable or was declined. Select the displayed text and use your browser’s Copy command.'); }
    };
    const breadcrumbs = (folder) => {
      const nav = node('nav', undefined, 'file-navigation'); nav.setAttribute('aria-label', 'Folder location');
      for (const crumb of fileBreadcrumbs(folder)) {
        const control = button(crumb.label, () => renderFiles(content, project, crumb.path));
        if (crumb.path === folder) control.setAttribute('aria-current', 'location');
        nav.append(control);
      }
      return nav;
    };
    try {
      const result = await api(`${endpoint}/files`, { method: 'POST', body: { path }, mutation: false });
      if (!current()) return;
      const listing = fileListingPresentation(result);
      if (listing.path !== path) throw new Error('unsupported-file-listing');
      const location = fileLocation(project.path, listing.path);
      fileDirectories.set(project.id, listing.path);
      content.replaceChildren();
      scope();
      content.append(node('p', location, 'file-location'), breadcrumbs(listing.path));
      const navigation = node('div', undefined, 'file-navigation');
      if (listing.path) navigation.append(button('Parent folder', () => renderFiles(content, project, listing.path.split('/').slice(0, -1).join('/'))));
      navigation.append(button('Refresh folder', () => renderFiles(content, project, listing.path)), button('Copy folder path', () => copy(location, 'Folder path')));
      content.append(navigation);
      const filterKey = JSON.stringify([project.id, listing.path]);
      const filterLabel = node('label', 'Filter names', 'file-filter');
      const filter = node('input'); filter.type = 'search'; filter.placeholder = 'Files and folders in this listing';
      filter.value = fileFilters.get(filterKey) || ''; filter.maxLength = 240;
      filterLabel.append(filter); content.append(filterLabel);
      const summary = node('p', undefined, 'file-list-summary'); summary.setAttribute('role', 'status');
      const rows = node('div', undefined, 'file-list'); content.append(summary, rows);
      const draw = () => {
        const visible = fileListingPresentation(result, filter.value);
        summary.textContent = visible.summary;
        rows.replaceChildren();
        if (!visible.entries.length) rows.append(node('p', visible.emptyMessage));
        for (const entry of visible.entries) {
          const item = button(`${entry.kind === 'directory' ? '▸ ' : ''}${entry.name}${entry.kind === 'directory' ? '/' : ''}`, () => {
            if (entry.kind === 'directory') renderFiles(content, project, entry.path);
            else if (entry.kind === 'file') readFile(entry.path, listing.path);
          }, 'file-item');
          item.disabled = !['file', 'directory'].includes(entry.kind);
          item.title = entry.kind === 'file' && Number.isSafeInteger(entry.size) ? `${entry.path} · ${entry.size.toLocaleString()} bytes` : entry.path;
          rows.append(item);
        }
      };
      filter.addEventListener('input', () => { fileFilters.set(filterKey, filter.value); draw(); });
      draw();
    } catch (error) {
      if (current()) { content.replaceChildren(); scope(); content.append(node('p', fileBrowserError(error)), button('Project root', () => renderFiles(content, project, ''))); }
    }
    async function readFile(filePath, folderPath) {
      const fileGeneration = ++inspectorGeneration;
      const fileCurrent = () => fileGeneration === inspectorGeneration && inspector === 'files' && getProject()?.id === project.id;
      content.replaceChildren(node('p', 'Loading text…'));
      try {
        const result = await api(`${endpoint}/file`, { method: 'POST', body: { path: filePath }, mutation: false });
        if (!fileCurrent()) return;
        if (!result || result.path !== filePath || typeof result.text !== 'string') throw new Error('unsupported-file-preview');
        if (result.text.length > 65536 || encoder.encode(result.text).length > 65536 || result.text.includes('\0') || INVALID_UNICODE.test(result.text)) throw new Error('text-size-limit');
        const location = fileLocation(project.path, result.path);
        content.replaceChildren(); scope();
        const actions = node('div', undefined, 'file-navigation');
        actions.append(button('Back to folder', () => renderFiles(content, project, folderPath)), button('Copy file path', () => copy(location, 'File path')), button('Copy text', () => copy(result.text, 'File text')));
        const preview = node('pre', result.text, 'file-preview'); preview.tabIndex = 0; preview.setAttribute('aria-label', 'Read-only text preview');
        content.append(breadcrumbs(folderPath), actions, node('p', location, 'file-location'), preview);
        content.append(node('p', filePreviewNote(snapshot, project)));
      } catch (error) {
        if (fileCurrent()) { content.replaceChildren(); scope(); content.append(button('Back to folder', () => renderFiles(content, project, folderPath)), node('p', fileBrowserError(error))); }
      }
    }
  }
  async function renderInspector() {
    const generation = ++inspectorGeneration;
    refreshConfigControls = null;
    const settings = isSettingsPage(inspector);
    app.classList.toggle('settings-open', settings);
    $('inspector').hidden = !inspector;
    $('inspector').classList.toggle('settings-page', settings);
    $('inspector').classList.toggle('terminal-history-panel', inspector === 'history');
    $('inspector').classList.toggle('files-panel', inspector === 'files');
    $('inspector').classList.toggle('files-wide', inspector === 'files' && filesExpanded);
    $('expand-files').hidden = inspector !== 'files';
    $('expand-files').setAttribute('aria-pressed', String(filesExpanded));
    text('expand-files', filesExpanded ? 'Side pane' : 'Full width');
    $('workspace-content').hidden = settings;
    $('back-to-work').hidden = !settings;
    $('focus-mode').disabled = settings;
    $('toggle-projects').disabled = settings;
    for (const name of ['config', 'files', 'details']) $(`show-${name}`).setAttribute('aria-pressed', String(inspector === name));
    $('show-project-details').setAttribute('aria-pressed', String(inspector === 'details' && !selectedTarget));
    $('dashboard-settings').setAttribute('aria-pressed', String(settings));
    $('show-scrollback').setAttribute('aria-pressed', String(inspector === 'history'));
    desktopLayout.refresh();
    if (!inspector) return;
    const project = getProject(), session = getSession();
    text('inspector-title', settings ? 'Dashboard settings' : inspector === 'history' ? 'Terminal scrollback' : inspector === 'config' ? 'Project config' : inspector === 'details' ? session ? 'Session details' : 'Project details' : 'Project files');
    text('inspector-context', settings ? 'Dashboard · machines · help' : [project?.name, entityWorkspace(project)?.label || project?.machineLabel,
      ['details', 'history'].includes(inspector) && session ? sessionLabel(session) : null].filter(Boolean).join(' / '));
    const content = $('inspector-content');
    if (['settings', 'machine'].includes(inspector)) { renderSettings(content); return; }
    content.replaceChildren();
    if (['connections', 'help'].includes(inspector)) {
      const layout = node('div', undefined, 'settings-layout settings-setup-layout');
      const article = node('section', undefined, 'settings-section');
      layout.append(settingsNavigation(), article); content.append(layout);
      try {
        if (generation !== inspectorGeneration || !['connections', 'help'].includes(inspector)) return;
        if (!connectionPanelHost) {
          connectionPanelHost = node('div');
          connectionPanelReady = mountConnectionsPanel(connectionPanelHost, { document, api,
            initialPage: inspector === 'help' ? 'help' : 'machines',
            isVisible: () => ['connections', 'help'].includes(inspector),
            activeMachines: () => snapshot.workspaces ?? [],
            presentMachine: machine => workspaceConnectionPresentation(machine, { connected: connectedService }),
            onRefreshMachines: () => refresh(),
            onConnectionsChange: saved => {
              connectionGeneration++; savedConnections = saved; renderWorkspaceSelector();
            },
            onPageChange: page => {
              const type = page === 'help' ? 'help' : 'connections';
              if (['connections', 'help'].includes(inspector) && inspector !== type) { navigate({ type }); renderInspector(); }
            },
            onInspectMachine: id => { navigate({ type: 'machine', id }); renderInspector(); },
          });
        }
        article.append(connectionPanelHost);
        const panel = await connectionPanelReady;
        connectionPanelController = panel;
        if (generation === inspectorGeneration) {
          panel.showPage(inspector === 'help' ? 'help' : 'machines');
          panel.refreshActiveMachines?.();
          if (!connectionPanelHost.contains(document.activeElement)) panel.focusPage();
        }
      } catch {
        if (generation === inspectorGeneration) article.append(node('p', 'Machine setup could not load. Check the dashboard connection and reload this page. See docs/getting-started.md for setup and troubleshooting.'));
      }
      return;
    }
    if (inspector === 'history') {
      if (!canReadTerminalHistory(snapshot, session, { connected: connectedService })) {
        content.append(node('p', 'Scrollback is not currently available for this session.')); return;
      }
      const key = targetKey(session);
      const current = () => generation === inspectorGeneration && inspector === 'history' &&
        selectedTarget && targetKey(selectedTarget) === key;
      const caption = node('p', TERMINAL_HISTORY_CAPTION);
      const reload = button('Refresh snapshot', () => renderInspector());
      reload.id = 'refresh-scrollback'; reload.disabled = true; reload.dataset.loading = 'true';
      const status = node('p', 'Loading scrollback…'); status.setAttribute('role', 'status');
      content.append(caption, reload, status);
      try {
        const history = await loadTerminalHistory(session, { isCurrent: current });
        if (!history) return;
        status.textContent = history.truncated ? 'Earlier output was omitted because the retained history is bounded.' : 'Snapshot loaded.';
        content.append(terminalHistoryText(document, history.text));
      } catch (error) { if (current()) status.textContent = error.message; }
      finally {
        if (current()) {
          reload.dataset.loading = 'false';
          reload.disabled = !canReadTerminalHistory(snapshot, getSession(), { connected: connectedService });
        }
      }
      return;
    }
    if (inspector === 'details') {
      const details = node('dl');
        addDetail(details, 'Project', project?.name); addDetail(details, 'Folder', project?.path);
        addDetail(details, 'Machine', project?.machineLabel); addDetail(details, isHostExecution(snapshot, project) ? 'Host agent profile' : 'Installation', project?.installationLabel);
        if (isHostExecution(snapshot, session || project)) {
          addDetail(details, 'Execution', HOST_BADGE);
          addDetail(details, 'Access', HOST_ACCESS);
          addDetail(details, 'Executable', session?.executable);
        }
        addDetail(details, 'Project unavailable', projectUnavailableMessage(project));
        addDetail(details, 'Session', session?.runtimeId); addDetail(details, 'Context namespace', session?.contextNamespace);
        addDetail(details, 'State', session?.state); addDetail(details, 'Agent', session?.agent);
        addDetail(details, 'Requested agent override', session?.requestedAgent);
        addDetail(details, runtimeStartedAt(session) ? 'Recorded start' : 'Request created', runtimeStartedAt(session) || session?.createdAt);
        addDetail(details, 'Recorded end', session?.recordedEndedAt);
        if (session?.recordedEndedAt) content.append(node('p', isHostExecution(snapshot, session)
          ? 'Recorded end is when the host session owner recorded completion; it may not be the exact process exit time.'
          : 'Recorded end is Botainer bookkeeping time; it may reflect when an exit was observed rather than the exact process exit time.'));
        addDetail(details, 'Last observed', session?.lastObservedAt);
        addDetail(details, 'Last known state', session?.lastKnownState);
        addDetail(details, 'Session unavailable', session?.unavailableReason);
        addDetail(details, 'Scheduler state', session?.schedulerState);
        addDetail(details, 'Scheduler observation', session?.schedulerObservation);
        addDetail(details, 'Queue reason', schedulerReason(session?.queueReason));
        addDetail(details, 'Node', session?.node);
        addDetail(details, 'Time limit', session?.timeLimit);
        if (session) addDetail(details, 'Last reported activity', session.lastActiveAt ?? 'Not reported; terminal silence does not imply readiness.');
      content.append(details);
      if (project) appendMaintenanceGuide(content, { scope: 'project', key: project.id,
        host: isHostExecution(snapshot, project), project, workspace: entityWorkspace(project) });
      if (project && entityWorkspace(project)) {
        content.append(button('View connection and profile', () => {
          navigate({ type: 'machine', id: project.workspaceId }); renderInspector();
        }));
      }
      if (hostOpeningOptions(snapshot, project).length) {
        const setup = node('section', undefined, 'settings-scope');
        setup.append(node('p', 'Optional: use this folder with an installed agent directly on this computer, outside containers. Setup only registers the folder.'));
        const open = button('⚠ Set up host agent · no container…', () => openHostProject(project), 'host-launch');
        open.disabled = !connectedService || busy;
        setup.append(open); content.append(setup);
      }
      return;
    }
    if (inspector === 'dashboard-config') {
      content.append(button('Back to dashboard settings', () => { inspector = 'settings'; renderInspector(); }));
      await renderConfig(content, null, generation, 'dashboard'); return;
    }
    if (inspector === 'config') {
      if (isHostExecution(snapshot, project)) content.append(node('p', `This is a plain project folder with no dashboard-managed Botainer configuration. ${HOST_ACCESS} Inspect configured executables in this workspace’s Settings.`));
      else await renderConfig(content, project, generation, 'project');
      return;
    }
    if (!project) { content.append(node('p', 'Select a project first.')); return; }
    if (inspector === 'files' && capability(snapshot, 'filesRead', project)) { await renderFiles(content, project, fileDirectories.get(project.id) || ''); return; }
    content.append(node('p', 'File browsing is not exposed by this backend. Project paths shown here are informational.'));
    if (generation !== inspectorGeneration) return;
    const details = node('dl'); addDetail(details, 'Project folder', project.path); addDetail(details, 'Installation', project.installationLabel); content.append(details);
  }
  function settingsNavigation(focusTargets = new Map()) {
    const navigation = node('nav', undefined, 'settings-navigation');
    navigation.setAttribute('aria-label', 'Settings sections');
    const navButton = (label, key, selected, action) => {
      const control = button(label, () => { navigate(action); renderInspector(); }, 'settings-link');
      control.dataset.navigationKey = key; control.setAttribute('aria-current', selected ? 'page' : 'false');
      focusTargets.set(key, control); navigation.append(control);
    };
    navButton('Dashboard', 'settings:dashboard', inspector === 'settings', { type: 'settings' });
    navButton('Machines', 'settings:connections', ['connections', 'machine'].includes(inspector), { type: 'connections' });
    navButton('Help', 'settings:help', inspector === 'help', { type: 'help' });
    return navigation;
  }
  function renderSettings(content) {
    const fragment = document.createDocumentFragment(), focusTargets = new Map();
    const layout = node('div', undefined, 'settings-layout');
    const navigation = settingsNavigation(focusTargets);
    const article = node('section', undefined, 'settings-section');
    const machine = snapshot.workspaces?.find(item => item.id === settingsMachineId);
    if (inspector === 'machine') {
      article.append(button('← Back to machines', () => { navigate({ type: 'connections' }); renderInspector(); }, 'quiet'));
      const host = machine?.executionKind === 'host';
      article.append(node('h2', machine?.label || 'Connection unavailable'), node('p', host ? 'Host agents and working folders · read-only' : 'Connection and Botainer installation · read-only'));
      if (machine) {
        if (!connectedService || machine.status !== 'available') {
          const recovery = node('p', undefined, 'settings-scope');
          appendConnectionRecovery(recovery, machine, focusTargets); article.append(recovery);
        }
        const details = node('dl');
        addDetail(details, 'Connection', connectedService ? machine.status || 'unknown' : 'Unverified');
        addDetail(details, 'Status', workspaceConnectionPresentation(machine, { connected: connectedService }).summary);
        addDetail(details, 'Type', machine.kind);
        addDetail(details, 'Project config access', host ? 'Plain folders; no dashboard-managed Botainer config' : 'Check each project’s config panel for its current permissions; connection and session state may restrict editing');
        if (host) { addDetail(details, 'Execution', HOST_BADGE); addDetail(details, 'Access', HOST_ACCESS); }
        article.append(details);
        const connectionHelp = node('section', undefined, 'settings-scope');
        connectionHelp.append(node('h3', 'Connection help'), node('p', host
          ? 'This profile selects existing agents and working folders on the dashboard computer. Change the saved profile and restart the dashboard deliberately to apply those changes.'
          : machine.kind === 'local'
          ? 'This connection uses the selected local runtime profile. Changing its executable, state root or allowed project folders requires updating that profile and restarting the dashboard.'
          : 'SSH login is separate from agent sign-in. Complete SSH authentication in your own terminal; the dashboard can refresh inventory when access returns. Reconnecting a terminal never starts a replacement agent.'));
        connectionHelp.append(node('p', host
          ? 'Use the installed agent’s own configuration, sign-in and update procedures. Dashboard startup and checks are under Settings → Dashboard.'
          : 'Connection registration does not change project defaults or grant access to every folder. The instructions below manage Botainer on this machine. Dashboard startup and checks are under Settings → Dashboard.'));
        article.append(connectionHelp);
        const installations = (snapshot.installations ?? []).filter(item => item.workspaceId === machine.id);
        for (const installation of installations) {
          const section = node('section', undefined, 'settings-scope');
          const details = node('dl');
          addDetail(details, host ? 'Host agent profile' : 'Botainer installation', installation.label ?? installation.name ?? installation.id);
          addDetail(details, 'Executable', installation.executable); addDetail(details, 'State root', installation.stateRoot);
          addDetail(details, 'Version', installation.version); section.append(details);
          appendMaintenanceGuide(section, { scope: 'installation', key: `${machine.id}:${installation.id}`, host, workspace: machine, focusTargets });
          article.append(section);
        }
        if (!installations.length) article.append(node('p', 'No installation details are reported for this machine.'));
        if (host) {
          for (const agent of configuredHostAgents(snapshot, { workspaceId: machine.id })) {
            const details = node('dl', undefined, 'settings-scope');
            addDetail(details, 'Installed agent', agent.label); addDetail(details, 'Executable', agent.executable);
            addDetail(details, 'Profile default', machine.defaultAgent === agent.id ? 'Yes' : 'No');
            addDetail(details, 'Profile startup restrictions', hostAgentRestrictions(agent)); article.append(details);
          }
          article.append(node('p', 'Only sessions started by this dashboard are listed here. Agents already running in other terminals are not adopted. Agent configuration and authentication remain with the installed agent.'));
        }
        appendClusterProfile(article, machine.clusterSettings);
      } else article.append(node('p', 'This machine is no longer registered. Choose a machine in settings to inspect its connection.'));
    } else {
      article.append(node('h2', 'Dashboard'), node('p', 'Service status and dashboard preferences. Use Machines to manage connections and their installations, or Help for setup and troubleshooting. Project files and defaults stay with each project.'));
      if (capability(snapshot, 'dashboardConfigRead')) {
        const edit = button(capability(snapshot, 'dashboardConfigWrite') ? 'Edit dashboard settings' : 'View dashboard settings', () => {
          inspector = 'dashboard-config'; navigationRevision++; renderInspector();
        });
        edit.dataset.navigationKey = 'settings:config'; focusTargets.set(edit.dataset.navigationKey, edit);
        article.append(edit, node('p', DASHBOARD_CONFIG_SCOPE));
      }
      const service = node('section', undefined, 'settings-scope');
      service.append(node('h3', 'Service status'), node('p', serviceNotice || (connectedService ? 'Local dashboard service connected.' : 'Local service unavailable.')));
      const details = node('dl'); addDetail(details, 'Mode', snapshot.mode || 'Not reported'); service.append(details);
      if (serviceFailed) service.append(button('Retry service connection', refresh));
      article.append(service);
      const maintenance = node('section', undefined, 'settings-scope maintenance-guide');
      maintenance.append(node('h3', 'Dashboard startup & maintenance'),
        node('p', `Run these commands in a normal terminal on the computer running the dashboard service${snapshot.dashboardEntryPoint === 'installed' ? '' : ', from the dashboard source folder'}. They manage the dashboard, not the selected Botainer installations or remote jobs. Use the same --data-dir if you chose a custom data directory.`),
        node('h4', 'Check dashboard startup prerequisites'), node('code', `${dashboardCommand(snapshot)} check`),
        node('p', 'Checks existing local files and settings. Does not start the dashboard, connect to machines or install software.'),
        node('h4', 'Start the dashboard when it is stopped'), node('code', `${dashboardCommand(snapshot)} start --open`),
        node('p', 'Uses the saved machine selection and opens the browser. If the service is already running, use its existing browser address. Resolve unfinished launch prompts before deliberately restarting.'),
        node('h4', 'Find the running dashboard'), node('code', `${dashboardCommand(snapshot)} status`),
        node('h4', 'Get a browser pairing code'), node('code', `${dashboardCommand(snapshot)} pair`),
        node('p', 'There is no in-place dashboard upgrade command. Installation and update instructions are in Help → Getting started. Botainer authentication, images and policy have separate instructions under Machines.'));
      article.append(maintenance);
      if (!snapshot.workspaces) {
        for (const installation of snapshot.installations ?? []) {
          const details = node('dl'); addDetail(details, 'Installation', installation.label ?? installation.id);
          addDetail(details, 'Executable', installation.executable); addDetail(details, 'State root', installation.stateRoot); article.append(details);
          appendMaintenanceGuide(article, { scope: 'installation', key: installation.id, host: isHostExecution(snapshot), focusTargets });
        }
        appendClusterProfile(article, snapshot.clusterSettings);
      }
    }
    layout.append(navigation, article); fragment.append(layout);
    updateNavigationList(content, fragment, document.activeElement, focusTargets, focusTargets.get('settings:connections'));
  }
  function appendMaintenanceGuide(content, { scope, key, host = false, project = null, workspace = null, focusTargets = null }) {
    const guide = node('details', undefined, 'maintenance-guide');
    const guideKey = `maintenance:${scope}:${key}`;
    const disclosure = (element, id, label) => {
      element.open = maintenanceGuidesOpen.has(id);
      const summary = node('summary', label);
      summary.dataset.navigationKey = id; focusTargets?.set(id, summary);
      element.append(summary);
      element.addEventListener('toggle', () => {
        if (!element.isConnected) return;
        if (element.open) maintenanceGuidesOpen.add(id); else maintenanceGuidesOpen.delete(id);
      });
    };
    disclosure(guide, guideKey, host ? 'Help with host agents' : scope === 'project' ? 'Help with this Botainer project' : 'Help with Botainer');
    const task = (id, title, introduction, steps, result, caution = '') => {
      const section = node('details', undefined, 'maintenance-task');
      disclosure(section, `${guideKey}:${id}`, title);
      if (introduction) section.append(node('p', introduction));
      if (caution) {
        const note = node('p'); note.append(node('strong', 'Before making changes: '), document.createTextNode(caution)); section.append(note);
      }
      const list = node('ol');
      for (const [text, command] of steps) {
        const item = node('li', text);
        if (command) item.append(node('code', command));
        list.append(item);
      }
      section.append(list);
      if (result) {
        const next = node('p'); next.append(node('strong', 'What next? '), document.createTextNode(result)); section.append(next);
      }
      guide.append(section);
    };
    if (host) {
      guide.append(node('p', 'These agents run directly on your computer, without a Botainer container. Choose the problem you need help with. Opening this guide does not run commands.'));
      task('folders', 'Add a folder I already use with Claude or Codex', 'Host folders are registered separately from Botainer projects. Agent histories and external terminal sessions are not imported.', [
        ['Select this loaded host connection using Filter by connection in the sidebar, then choose Add project.'],
        ['Choose Open an existing folder. Select Workspace folder as its allowed parent and enter the path relative to that parent, such as team/my-project.'],
        ['Choose Open folder. The folder is added to this host profile; its files stay in place and no agent starts.'],
      ], 'Select the added project and use the named New session on host button when you want a new agent. An existing session in another terminal stays there; this does not attach to it.');
      task('agent', 'The agent needs sign-in or an update', '', [
        ['Look at Installed agent and Executable in this connection’s details to identify the agent you are using.'],
        ['Open your computer’s normal terminal. Follow that agent’s own sign-in or update instructions. Botainer commands do not manage this host agent.'],
      ], 'Return to the project to start a new host session when needed. Starting another session does not reconnect an existing one.',
      'A host agent has your account’s access to files and credentials. Its project folder does not contain or restrict that access.');
      content.append(guide); return;
    }
    const remote = workspace ? Boolean(workspace.clusterSettings) || /cluster|remote/.test(workspace.kind || '')
      : Boolean(snapshot.clusterSettings) || /cluster|remote/.test(snapshot.mode || '');
    guide.append(node('p', 'Choose the problem you want to solve below. These are instructions you carry out in a normal terminal; the dashboard does not run them for you.'));
    const setup = node('section', undefined, 'maintenance-start');
    setup.append(node('h4', 'Where to run the commands'));
    const location = node('dl');
    addDetail(location, 'Computer', remote ? `Remote machine${workspace?.label ? ` · ${workspace.label}` : ''}. Open your normal terminal and SSH there first.`
      : workspace || !snapshot.workspaces ? `This computer${workspace?.label ? ` · ${workspace.label}` : ''}. Open its normal terminal.`
        : 'Use the machine listed in Project details. Check which connection it belongs to first.');
    if (scope === 'project') addDetail(location, 'Project folder', project?.path || 'The selected project folder on that machine');
    setup.append(location,
      node('p', 'Run Botainer commands outside the agent’s container. Use your usual Botainer command. If it is an alias, replace botainer in each example with that alias: for example, my-botainer auth status.'),
      node('p', scope === 'project' ? 'Change to the project folder shown above before following a task.' : 'For a problem with one project, change to that project’s folder first.'));
    guide.append(setup);
    if (scope === 'project') {
      task('start', 'A new session will not start', 'Start with the error in the launch terminal. It may already tell you what needs fixing.', [
        ['Check this project’s settings. Look for warnings or errors and the suggested fixes.', 'botainer config check'],
        ['If the settings are valid but startup still fails, check the tools and image needed to start the container.', 'botainer doctor'],
      ], 'Fix the reported problem, then return to New session. If a launch has already started or its outcome is uncertain, check for that session before launching another one.');
      task('connect', 'A session is running, but I cannot connect', 'Connect opens an existing session. New session starts another agent.', [
        ['Open the session’s details and read the reason beside the unavailable controls.'],
        [remote ? 'If the machine is offline, restore your SSH connection, return to the dashboard and refresh its project list.' : 'If the dashboard lost its connection, reconnect to the dashboard service and refresh its project list.'],
        ['Use Connect when it becomes available. If it stays unavailable, return to the original terminal if you still have it. Do not repeat the start command to reconnect.'],
      ], 'The agent may still be running even though this window cannot show it. Rebuilding its image or signing in again does not establish permission to attach. Some reconnect cases still need development.');
      task('config', 'Change the agent or project settings', '', [
        ['For only the next session, choose the agent beside New session.'],
        ['For saved project defaults, open Project config, edit the file, and use Validate and save.'],
        ['To inspect how Botainer interprets the settings, use its explanation.', 'botainer config explain'],
      ], 'Saved defaults affect new sessions. An agent that is already running keeps the settings it started with.');
    }
    task('login', 'The agent asks me to sign in', 'Do this from the affected project’s folder, for the agent you intend to use.', [
      ['Check which login the project uses and whether credentials are missing or expired.', 'botainer auth status'],
      ['Read the available login options. This command shows help; it does not sign you in.', 'botainer auth login --help'],
      ['Start the login for your agent and follow Botainer’s prompts. This example is for Codex; use claude instead if that is your agent.', 'botainer auth login --agent codex'],
      ['Check the result after login finishes.', 'botainer auth status'],
    ], 'Return to the dashboard and retry the operation that needed login. A successful status check does not guarantee that every provider request will succeed.',
    'A shared login can be used by several projects. Check the login mode and account before continuing. If you use a named authentication profile, select that same profile using the options in login help; the example below uses the default profile.');
    if (scope === 'project' && remote) {
      task('jobs', 'My agent cannot submit work to the cluster', 'This checks jobs submitted by the agent from inside a session. It is not a check of the dashboard’s SSH connection.', [
        ['Run the project’s job-submission check and read its verdict and proposed fix.', 'botainer hpc jobs-doctor'],
      ], 'Apply the fix to this project’s cluster job settings. This command does not reconnect the agent terminal or cancel a queued job.');
    }
    if (scope === 'installation') {
      task('image', 'An agent image is missing or needs rebuilding', 'An image is the packaged environment used to start a container. Building an image does not update the Botainer program itself.', [
        ['List the images and find the entry for the agent you need. Note its name in Botainer, such as agent-claude.', 'botainer image list'],
        ['Read the build options supported by this Botainer installation.', 'botainer image build --help'],
        [remote ? 'Use the cluster’s approved build machine and storage. This example builds the Claude image with Apptainer; substitute the image name you selected.' : 'Build only the image you need. This example builds the Claude image with Docker; substitute the image name you selected.',
          remote ? 'botainer image build agent-claude --runtime apptainer' : 'botainer image build agent-claude --runtime docker'],
        ['After a successful build, list the images again to check the result.', 'botainer image list'],
      ], 'Return to the dashboard and start a new session when ready. Rebuilding does not upgrade an already running container. It uses the build instructions already installed with Botainer; getting newer instructions is a separate update.',
      remote ? 'Building can download software and use substantial storage. Do not replace an image file used by active cluster jobs. A build runs on the machine where you enter the command; it is not automatically sent to the scheduler.'
        : 'Building can download software, use substantial storage and replace the image selected by other projects.');
      task('update', 'Update Botainer or the dashboard', 'These are two separate programs. Updating one does not update the other.', [
        ['For Botainer: use the update procedure for the copy you installed. Keep its settings/data folder and your existing projects. There is no update button in this dashboard yet.'],
        ['If a Botainer update makes the saved dashboard connection invalid, review and prepare that connection again under Settings → Machines.'],
        ['For the dashboard: use its own source and dependency update instructions on the computer running the dashboard service. Its startup commands are under Settings → Dashboard.'],
      ], 'Finish any unanswered launch prompts before restarting the dashboard. Read Help → Getting started for setup and repair instructions.');
      task('advanced', 'Advanced: check the Botainer copy, plugins or policy', 'Use this when several copies of Botainer are installed, or when its settings are unexpected.', [
        ['Print the Botainer settings/data folder, called the state root. Compare it with State root in the connection details above. This displays the path; it does not change it.', 'botainer where --state-root'],
        ['List installed plugins: the components that add agents and other Botainer features.', 'botainer plugin list'],
        ['Show the rules that Botainer applies, including restrictions set by the machine’s administrator.', 'botainer policy show'],
        ['Locate the user policy file if you need to inspect it.', 'botainer policy path'],
      ], 'If the state folder is different, use the alias or wrapper for the intended copy of Botainer before changing anything. A Python executable path in the connection details is not necessarily the complete command you should type.');
      guide.append(node('p', 'Disk cleanup and automatic upgrades are not provided yet. Removing a dashboard connection does not delete its projects, images or credentials.'));
    } else guide.append(node('p', 'For image builds or software updates, open Settings → Machines → Details & Botainer help.'));
    content.append(guide);
  }
  function appendClusterProfile(content, settings) {
    if (!settings) return;
    const section = node('section', undefined, 'settings-scope');
    section.append(node('h3', 'Cluster connection profile'), node('p', 'Read-only connection and allocation settings. Project session defaults are separate.'));
    const details = node('dl');
    for (const [label, value] of clusterSettingsDetails(settings)) addDetail(details, label, value);
    section.append(details);
    if (Array.isArray(settings.setupSteps)) {
      const list = node('ol');
      for (const step of settings.setupSteps.filter(value => typeof value === 'string').slice(0, 20)) list.append(node('li', step));
      if (list.childElementCount) section.append(node('h4', 'Connection setup'), list);
    }
    content.append(section);
  }
  $('toggle-projects').addEventListener('click', () => {
    if (app.classList.contains('focus-mode')) {
      setFocus(false); setSidebar(true);
    } else setSidebar(app.classList.contains('sidebar-hidden'));
  });
  $('sidebar-backdrop').addEventListener('click', () => setSidebar(false));
  $('project-search').addEventListener('input', () => { pendingPreferences = null; renderProjects(); });
  $('show-all-projects').addEventListener('click', () => {
    $('project-search').value = '';
    $('show-bells').checked = false;
    selectWorkspace(null); // Filtering never changes the selected work or its terminal.
  });
  $('workspace-select').addEventListener('change', () => selectWorkspace($('workspace-select').value));
  $('copy-ssh').addEventListener('click', async () => {
    const command = sshRecoveryCommand(getWorkspace() || entityWorkspace(getProject()));
    if (!command) return;
    try {
      if (!globalThis.navigator?.clipboard?.writeText) throw new Error('Clipboard unavailable');
      await globalThis.navigator.clipboard.writeText(command);
      notice(`Copied ${command}. Run it in your own terminal and complete any login prompts there. Keep that terminal open if connection sharing does not persist after it closes, then Refresh the dashboard.`);
    } catch { notice(`Open your own terminal and run: ${command}. Keep it open if connection sharing does not persist after it closes, then Refresh the dashboard.`); }
  });
  $('show-ended').addEventListener('change', () => { pendingPreferences = null; historyExpansion.clear(); fullHistory.clear(); renderProjects(); renderEmpty(); });
  $('project-sort').addEventListener('change', () => { pendingPreferences = null; renderProjects(); });
  $('compact-projects').addEventListener('click', () => {
    compactProjects = !compactProjects;
    try { localStorage.setItem('botainer-dashboard:compact-projects:v1', String(compactProjects)); } catch { /* Page-only preference. */ }
    renderDensity();
  });
  $('collapse-projects').addEventListener('click', () => {
    for (const project of snapshot.projects) projectExpansion.set(project.id, false);
    renderProjects();
  });
  $('show-bells').addEventListener('change', renderProjects);
  $('mark-bell-seen').addEventListener('click', () => {
    if (selectedTarget) unreadBells.delete(targetKey(selectedTarget));
    renderAttention();
  });
  $('refresh').addEventListener('click', refresh);
  $('copy-terminal').addEventListener('click', async () => {
    if (!selectedTarget) return;
    try { await copyTerminalSelection(registry, selectedTarget, globalThis.navigator?.clipboard); notice('Selected terminal text copied.'); }
    catch (error) { notice(error.name === 'NotAllowedError' ? 'Clipboard access was declined. Select text and use your browser’s Copy command.' : error.message); }
  });
  $('paste-terminal').addEventListener('click', async () => {
    if (!selectedTarget || $('paste-terminal').disabled) return;
    try { await pasteTerminalClipboard(registry, selectedTarget, globalThis.navigator?.clipboard); }
    catch (error) { notice(error.name === 'NotAllowedError' ? 'Clipboard access was declined. Focus the terminal and use your browser’s Paste command.' : error.message); }
  });
  $('show-scrollback').addEventListener('click', () => {
    if ($('show-scrollback').disabled || !selectedTarget) return;
    if (inspector === 'history') { closePanel(); return; }
    setFocus(false);
    registry.cancelPendingFocus(selectedTarget);
    inspector = 'history'; navigationRevision++;
    renderInspector(); $('inspector-title').focus({ preventScroll: true });
  });
  $('focus-mode').addEventListener('click', () => setFocus(!app.classList.contains('focus-mode')));
  function openProjectPanel(name) {
    if (inspector === name) { closePanel(); return; }
    setFocus(false);
    if (selectedTarget) registry.cancelPendingFocus(selectedTarget);
    inspector = name; renderInspector(); $('inspector-title').focus({ preventScroll: true });
  }
  for (const name of ['config', 'files', 'details']) $(`show-${name}`).addEventListener('click', () => openProjectPanel(name));
  $('show-project-details').addEventListener('click', () => openProjectPanel('details'));
  $('project-path').addEventListener('click', () => openProjectPanel('files'));
  function openSettings() {
    setFocus(false); navigate({ type: 'settings' });
    if (selectedTarget) registry.cancelPendingFocus(selectedTarget);
    $('terminal-menu').open = false;
    renderInspector(); $('inspector-title').focus({ preventScroll: true });
    if (globalThis.matchMedia('(max-width: 850px)').matches) setSidebar(false);
  }
  function closePanel() {
    const settings = isSettingsPage(inspector);
    const history = inspector === 'history';
    const returnControl = settings ? $('dashboard-settings') : inspector === 'config' ? $('show-config')
      : inspector === 'files' ? $('show-files') : history ? $('show-scrollback') : selectedTarget ? $('show-details') : $('show-project-details');
    navigate({ type: 'back' }); renderInspector();
    if (selectedTarget) registry.get(selectedTarget)?.terminal.measure();
    if (history && selectedTarget && registry.focus(selectedTarget)) return;
    if (!returnControl.hidden && !returnControl.disabled) returnControl.focus({ preventScroll: true });
  }
  $('service-notice').addEventListener('click', openSettings);
  $('dashboard-settings').addEventListener('click', () => {
    if (isSettingsPage(inspector)) closePanel(); else openSettings();
  });
  $('close-inspector').addEventListener('click', closePanel);
  $('expand-files').addEventListener('click', () => {
    if (inspector !== 'files') return;
    filesExpanded = !filesExpanded;
    $('inspector').classList.toggle('files-wide', filesExpanded);
    $('expand-files').setAttribute('aria-pressed', String(filesExpanded));
    text('expand-files', filesExpanded ? 'Side pane' : 'Full width');
    desktopLayout.refresh();
  });
  $('back-to-work').addEventListener('click', closePanel);
  $('session-tabs').addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    const tabs = [...$('session-tabs').querySelectorAll('[role="tab"]')];
    const index = tabs.indexOf(document.activeElement); if (index < 0) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1
      : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    tabs[next].focus({ preventScroll: true }); tabs[next].scrollIntoView({ block: 'nearest', inline: 'nearest' });
  });
  for (const id of ['detach', 'close-view', 'stop-session']) $(id).addEventListener('click', () => { $('terminal-menu').open = false; });
  $('connect').addEventListener('click', () => {
    const session = getSession();
    if (!sessionControls(snapshot, session, { connected: connectedService, busy }).attach) return;
    try {
      if (isLaunchLog(session)) {
        // This explicit read reloads a recorded transcript. Replace its renderer
        // rather than append another copy to the existing local scrollback.
        registry.dispose(selectedTarget); selectSession(session);
      }
      setRecoveryIntent(terminalRecoveryIntent(session));
      registry.attach(selectedTarget);
      if (!isLaunchLog(session)) registry.focus(selectedTarget);
    }
    catch (error) { notice(error.message); }
  });
  $('empty-action').addEventListener('click', () => {
    const action = projectPrimaryAction(snapshot, getProject(), { connected: connectedService, busy });
    if (action?.kind === 'start') { if (!$('new-session').disabled) $('new-session').click(); return; }
    if (action?.kind !== 'connect') return;
    const session = snapshot.sessions.find(item => targetKey(item) === targetKey(action.target));
    if (!session) return;
    inspector = null; renderInspector();
    selectSession(session, { acknowledge: true });
    if (selectedTarget && targetKey(selectedTarget) === targetKey(action.target) && !$('connect').disabled) $('connect').click();
  });
  $('terminal-guidance-action').addEventListener('click', () => {
    const id = $('terminal-guidance-action').dataset.control;
    if (!['new-session', 'connect', 'stop-session'].includes(id)) return;
    const control = $(id);
    if (!control.disabled) control.click();
  });
  $('detach').addEventListener('click', () => {
    if (!selectedTarget) return;
    setRecoveryIntent(null);
    registry.detach(selectedTarget);
    notice(getSession()?.kind === 'launch' ? isLaunchLog(getSession()) ? 'CLI log view closed. Its finished prompt cannot be resumed; no new launch was submitted.'
      : 'Launch console view disconnected. Refresh to check whether the native command is still awaiting input before reconnecting. No new launch will be submitted.'
      : 'Terminal connection closed; the session keeps running. Manual disconnection is optional. Reconnect returns to the original session after its connection is verified.');
  });
  $('close-view').addEventListener('click', () => {
    if (!selectedTarget) return;
    setRecoveryIntent(null);
    registry.dispose(selectedTarget); selectedTarget = null; $('terminal-search').hidden = true;
    notice('Terminal view closed and local history released. Its session was not stopped.');
    renderProjects(); renderHeading(); renderTerminalMetadata();
  });
  $('session-agent').addEventListener('change', () => {
    const project = getProject();
    if (project && capability(snapshot, 'agentOverride', project)) sessionAgentChoices.set(project.id, $('session-agent').value);
    renderHeading();
    renderTerminalMetadata();
  });
  $('new-session').addEventListener('click', () => {
    const project = getProject(); if (!project || $('new-session').disabled || !allowed('startSession', project)) return;
    const selectedAgent = capability(snapshot, 'agentOverride', project) ? $('session-agent').value : 'default';
    // A host confirmation identifies one executable; resolve the profile default
    // now so an inventory update cannot change the agent behind that dialog.
    const agentChoice = isHostExecution(snapshot, project) && selectedAgent === 'default'
      ? project.defaultAgent ?? entityWorkspace(project)?.defaultAgent : selectedAgent;
    const limit = clusterSettings(project)?.timeMinutes;
    const launch = () => perform(async () => {
      const expectedPresentation = presentationContext();
      const body = sessionLaunchRequest(snapshot, project, agentChoice, crypto.randomUUID());
      const result = await api(`/api/projects/${encodeURIComponent(project.id)}/sessions`, { method: 'POST', body });
      const returned = result?.session ?? (result?.runtimeId ? result : null);
      if (capability(snapshot, 'nativeCliLaunch', project)) {
        snapshot = includeLaunchResponse(snapshot, project, returned);
        sessionLabels = sessionDisplayLabels(snapshot.sessions);
      } else await refresh();
      const session = returned && snapshot.sessions.find(value => targetKey(value) === targetKey(returned) && value.projectId === project.id);
      const presentation = launchResponsePresentation(expectedPresentation, presentationContext(), session);
      if (!presentation) {
        notice(`Session request recorded for ${project.name}. Your current view was kept; inspect that project's reported session state.`);
      } else if (session) {
        if (inspector !== presentation.inspector) {
          // Opening the requested CLI is explicit navigation. Retain editor
          // drafts, while making its later session transition the default.
          inspector = presentation.inspector; renderInspector();
        }
        selectSession(session);
        if (capability(snapshot, 'attachTerminal', session) && session.state === 'running') {
          setRecoveryIntent(terminalRecoveryIntent(session)); registry.attach(session); registry.focus(session);
        }
      }
      else notice('Launch request recorded. Check the reported session state; no automatic retry will occur.');
    });
    if (isHostExecution(snapshot, project)) {
      try { showDialog({ ...hostLaunchPresentation(snapshot, project, agentChoice), action: launch }); }
      catch (error) { notice(error.message); }
    }
    else if (capability(snapshot, 'nativeCliLaunch', project)) launch();
    else showDialog({ title: 'Start a new session', description: `Start one session in ${project.name} using its saved defaults on ${entityWorkspace(project)?.label || project.machineLabel || 'the configured machine'}?${limit ? ` This allocation has a ${limit}-minute limit; the scheduler ends it when that limit is reached.` : ''}`, confirm: 'Start session', action: launch });
  });
  $('stop-session').addEventListener('click', () => {
    const session = getSession(); if (!sessionControls(snapshot, session, { connected: connectedService, busy }).stop) return;
    const stop = sessionStopPresentation(snapshot, session, getProject());
    showDialog({ title: stop.title, description: stop.description, confirm: stop.label, action: () => {
      let target;
      try { target = sessionStopTarget(snapshot, session, { connected: connectedService, busy }); }
      catch (error) { notice(error.message); return; }
      return perform(async () => {
        const result = await api(`/api/sessions/${encodeURIComponent(target.runtimeId)}/stop`, { method: 'POST', body: {
          contextNamespace: target.contextNamespace, expectedStopMode: target.expectedStopMode, requestId: crypto.randomUUID(),
        } });
        const outcome = applySessionStopResult(snapshot, session, result, { registry, notice });
        if (outcome.confirmed && recoveryIntent && targetKey(recoveryIntent) === targetKey(session)) setRecoveryIntent(null);
      });
    } });
  });
  function openHostProject(project) {
    const choices = hostOpeningOptions(snapshot, project);
    if (!project || !connectedService || busy || !choices.length) return;
    showDialog({ title: 'Set up this folder for a host agent', description: `${choices.length === 1 ? `Host workspace: ${choices[0].label}. ` : ''}This step registers the existing folder only. No agent is started.`,
      warning: hostProjectOpeningDescription(project), confirm: 'Set up host project', fields: choices.length > 1
        ? [{ name: 'hostWorkspaceId', label: 'Host workspace', options: choices.map(item => ({ value: item.id, label: item.label })) }]
        : [], action: () => {
        const workspaceId = choices.length > 1 ? $('field-hostWorkspaceId').value : choices[0].id;
        if (!hostOpeningOptions(snapshot, project).some(item => item.id === workspaceId)) {
          notice('This folder is no longer available to that host profile. Refresh and check its allowed project folders in Settings.'); return;
        }
        return perform(async () => {
          const expectedPresentation = presentationContext();
          let result;
          try {
            result = await api(`/api/projects/${encodeURIComponent(project.id)}/open-host`, {
              method: 'POST', body: { workspaceId, requestId: crypto.randomUUID() } });
          } catch (error) { throw new Error(hostOpeningErrorMessage(error)); }
          await refresh();
          snapshot = includeHostProjectResponse(snapshot, result, workspaceId);
          if (!presentationUnchanged(expectedPresentation, presentationContext())) {
            notice(`Host project opened for ${project.name}. Your current view was kept; no agent was started.`); return;
          }
          selectProject(result.project.id);
          notice('Existing folder opened as a host project. Choose New session to start an installed agent. No agent has been started.');
        });
      } });
  }
  $('add-project').addEventListener('click', async () => {
    if (!connectedService || busy || !projectCreators().length) return;
    try {
      const setupPresentation = presentationContext();
      const workspace = await api('/api/workspace');
      if (!presentationUnchanged(setupPresentation, presentationContext())) return;
      if (!Array.isArray(workspace?.roots) || !Array.isArray(workspace?.installations)) throw new Error('Workspace setup is unavailable.');
      const creators = projectCreators().filter(item => {
        const choices = projectSetupOptions(snapshot, workspace, item.id);
        return choices.roots.length && choices.installations.length && choices.modes.length;
      });
      if (!creators.length) throw new Error('Configure a supported workspace folder and runtime profile in dashboard settings first.');
      const initial = creators.find(item => item.id === getProject()?.workspaceId) || creators[0];
      const choices = projectSetupOptions(snapshot, workspace, initial.id);
      showDialog({ title: 'Add a project', description: projectSetupDescription(snapshot, initial.id), confirm: 'Open project', fields: [
        ...(snapshot.workspaces ? [{ name: 'workspaceId', label: 'Machine / runtime', value: initial.id, options: creators.map(item => ({ value: item.id, label: item.label })) }] : []),
        { name: 'mode', label: 'Action', options: choices.modes },
        { name: 'rootId', label: 'Workspace folder', options: choices.roots.map(root => ({ value: root.id, label: [root.label, root.path].filter(Boolean).join(' · ') })) },
        { name: 'installationId', label: isHostExecution(snapshot, { workspaceId: initial.id }) ? 'Host agent profile' : 'Botainer installation', options: choices.installations.map(installation => ({ value: installation.id, label: installation.label || installation.id })) },
        { name: 'path', label: 'Folder path relative to workspace', placeholder: 'my-project', maxLength: 1000 },
        { name: 'name', label: 'Project display name', placeholder: 'My project', required: false, maxLength: 120 },
      ], action: () => {
        const body = Object.fromEntries(['mode', 'rootId', 'installationId', 'path', 'name'].map(key => [key, $(`field-${key}`).value]));
        if (snapshot.workspaces) body.workspaceId = $('field-workspaceId').value;
        const currentChoices = projectSetupOptions(snapshot, workspace, body.workspaceId ?? null);
        if (!currentChoices.modes.some(item => item.value === body.mode) || !currentChoices.roots.some(item => item.id === body.rootId) ||
            !currentChoices.installations.some(item => item.id === body.installationId)) { notice('Project setup choices are no longer available for that machine.'); return; }
        body.name = body.name.trim() || body.path.split('/').filter(Boolean).at(-1) || '';
        body.requestId = crypto.randomUUID();
        return perform(async () => {
          const expectedPresentation = presentationContext();
          const result = await api('/api/projects', { method: 'POST', body });
          const nativeSetup = workspaceCapability(snapshot, body.workspaceId ?? null, 'nativeCliProjectSetup');
          if (nativeSetup) {
            snapshot = includeProjectSetupResponse(snapshot, result, body.workspaceId ?? null);
            sessionLabels = sessionDisplayLabels(snapshot.sessions);
          } else await refresh();
          if (!presentationUnchanged(expectedPresentation, presentationContext())) {
            notice(`Project setup returned for ${body.name}. Your current view was kept; select that project to review its defaults.`);
          } else if (result?.project?.id && snapshot.projects.some(project => project.id === result.project.id)) {
            if (selectedWorkspace && body.workspaceId) selectedWorkspace = body.workspaceId;
            if (nativeSetup && result.session) {
              selectSession(result.session);
              if (sessionControls(snapshot, result.session).attach) { registry.attach(result.session); registry.focus(result.session); }
              return;
            }
            selectProject(result.project.id);
            const host = isHostExecution(snapshot, result.project);
            inspector = host ? null : 'config'; renderInspector();
            notice(host ? `Project folder ${body.mode === 'create' ? 'created' : 'opened'}. Choose an installed agent and New session. ${HOST_BADGE}; no agent has been started.`
              : body.mode === 'create' ? 'Project created. Review its saved defaults before starting a session.' : 'Project opened. Review its defaults and reported sessions.');
          } else notice('Project setup returned. Refresh the project list before attempting another setup request.');
        });
      } });
      const updateSetupCopy = () => {
        const workspaceId = snapshot.workspaces ? $('field-workspaceId').value : null;
        text('action-description', projectSetupDescription(snapshot, workspaceId));
        $('action-fields').querySelector('label[for="field-installationId"]').textContent = isHostExecution(snapshot, { workspaceId }) ? 'Host agent profile' : 'Botainer installation';
        text('confirm-action', $('field-mode').value === 'create' ? 'Create folder' : $('field-mode').value === 'open' ? 'Open folder' : 'Register project');
      };
      $('field-mode').addEventListener('change', updateSetupCopy);
      if (snapshot.workspaces) $('field-workspaceId').addEventListener('change', () => {
        const choices = projectSetupOptions(snapshot, workspace, $('field-workspaceId').value);
        const replace = (id, options) => {
          const select = $(`field-${id}`); select.replaceChildren();
          for (const item of options) { const option = node('option', item.label); option.value = item.value; select.append(option); }
        };
        replace('mode', choices.modes);
        replace('rootId', choices.roots.map(item => ({ value: item.id, label: [item.label, item.path].filter(Boolean).join(' · ') })));
        replace('installationId', choices.installations.map(item => ({ value: item.id, label: item.label || item.id })));
        updateSetupCopy();
      });
      updateSetupCopy();
    } catch (error) { notice(error.message); }
  });
  $('action-form').addEventListener('submit', event => { event.preventDefault(); const action = modalAction; modalAction = null; $('action-dialog').close(); action?.(); });
  $('cancel-action').addEventListener('click', () => { modalAction = null; $('action-dialog').close(); });
  $('open-search').addEventListener('click', () => { $('terminal-search').hidden = false; $('terminal-query').focus(); });
  const find = previous => {
    const renderer = selectedTarget && registry.get(selectedTarget)?.terminal;
    if (renderer && !renderer.find($('terminal-query').value, previous)) notice('No matching text in the retained terminal history.');
  };
  $('terminal-search').addEventListener('submit', event => { event.preventDefault(); find(false); });
  $('search-previous').addEventListener('click', () => find(true));
  $('close-search').addEventListener('click', () => { $('terminal-search').hidden = true; if (selectedTarget) registry.focus(selectedTarget); });
  let switcherOpenOnly = false;
  function nextAttention() {
    const key = [...unreadBells.keys()].find(value => views.has(value));
    if (key) activateSession(views.get(key));
  }
  function renderSwitcher() {
    const query = $('switcher-query').value;
    const choices = workbenchChoices(snapshot, { openViews: [...views.values()], query,
      openOnly: switcherOpenOnly, bells: unreadBells }).map(item => ({ ...item, action: () => {
        if (item.kind === 'session') activateSession(item.session);
        else if (snapshot.projects.some(project => project.id === item.project.id && project.workspaceId === item.project.workspaceId)) selectProject(item.project.id);
        else notice('This project is no longer in the current inventory. Refresh to check its location.');
      } }));
    if (!switcherOpenOnly) {
      const commands = [
        { label: 'Settings and help', action: openSettings },
        { label: app.classList.contains('focus-mode') ? 'Leave terminal focus mode' : 'Focus terminal', action: () => setFocus(!app.classList.contains('focus-mode')) },
        { label: app.classList.contains('sidebar-hidden') ? 'Show projects sidebar' : 'Hide projects sidebar', action: () => setSidebar(app.classList.contains('sidebar-hidden')) },
        { label: 'Reset pane sizes', action: () => desktopLayout.reset() },
        ...(getProject() ? [
          { label: 'Project details', action: () => openProjectPanel('details') },
          { label: 'Project files', action: () => openProjectPanel('files') },
        ] : []),
        ...(unreadBells.size ? [{ label: 'Next terminal bell', action: nextAttention }] : []),
      ];
      const words = query.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
      for (const command of commands) if (words.every(word => command.label.toLocaleLowerCase().includes(word))) choices.push({ ...command, detail: 'View action' });
    }
    const results = $('switcher-results'); results.replaceChildren();
    for (const choice of choices.slice(0, 80)) {
      const control = button('', () => { $('switcher-dialog').close(); choice.action(); }, `switcher-item${choice.host ? ' host-choice' : ''}`);
      control.append(node('span', `${choice.bell ? '🔔 ' : ''}${choice.label}`, 'switcher-label'), node('span', choice.detail, 'switcher-meta'));
      results.append(control);
    }
    const empty = $('switcher-empty');
    empty.hidden = choices.length > 0 && choices.length <= 80;
    empty.textContent = choices.length > 80 ? 'Showing the first 80 matches. Type more to narrow the list.'
      : switcherOpenOnly && !views.size ? 'No terminal views open yet. Choose a session in Projects or use Switch to find one.' : 'No matching projects, sessions or view actions.';
  }
  function openSwitcher(openOnly = false) {
    if ($('action-dialog').open || $('switcher-dialog').open) return;
    switcherOpenOnly = openOnly;
    $('switcher-query').value = '';
    $('switcher-query').placeholder = openOnly ? 'Search open terminal views' : 'Search projects, sessions, machines or view actions';
    renderSwitcher(); $('switcher-dialog').showModal(); $('switcher-query').focus();
  }
  $('open-switcher').addEventListener('click', () => openSwitcher());
  $('open-session-list').addEventListener('click', () => openSwitcher(true));
  $('next-attention').addEventListener('click', nextAttention);
  $('close-switcher').addEventListener('click', () => $('switcher-dialog').close());
  $('switcher-query').addEventListener('input', renderSwitcher);
  $('switcher-dialog').addEventListener('keydown', event => {
    if (event.isComposing || !['ArrowDown', 'ArrowUp', 'Enter'].includes(event.key)) return;
    const controls = [...$('switcher-results').querySelectorAll('button')];
    if (!controls.length) return;
    const index = controls.indexOf(document.activeElement);
    if (event.key === 'Enter') {
      if (document.activeElement === $('switcher-query')) { event.preventDefault(); controls[0].click(); }
      return;
    }
    if (document.activeElement !== $('switcher-query') && index < 0) return;
    event.preventDefault();
    const next = index < 0 ? (event.key === 'ArrowDown' ? 0 : controls.length - 1)
      : (index + (event.key === 'ArrowDown' ? 1 : -1) + controls.length) % controls.length;
    controls[next].focus();
  });
  // Capture this navigation chord before a focused terminal can send it as input.
  document.addEventListener('keydown', event => {
    if (!event.isComposing && !event.altKey && (event.ctrlKey || event.metaKey) && event.shiftKey && event.key.toLowerCase() === 'p') {
      event.preventDefault(); event.stopPropagation(); openSwitcher();
    }
  }, true);
  document.addEventListener('keydown', event => {
    if (event.isComposing || $('switcher-dialog').open || $('action-dialog').open) return;
    if ((event.ctrlKey || event.metaKey) && event.shiftKey && event.key.toLowerCase() === 'f' && selectedTarget && !inspector) {
      event.preventDefault(); $('terminal-search').hidden = false; $('terminal-query').focus();
    }
    if (event.key === 'Escape') {
      if (inspector) closePanel();
      $('terminal-menu').open = false;
      $('terminal-search').hidden = true;
      if (globalThis.matchMedia('(max-width: 850px)').matches) setSidebar(false);
    }
  });
  if (globalThis.matchMedia('(max-width: 850px)').matches) setSidebar(false);
  narrowWindow.addEventListener('change', event => setSidebar(event.matches ? false : desktopSidebarShown));
  globalThis.addEventListener('resize', revealSelectedTab);
  refresh();
  const interval = setInterval(() => { if (!document.hidden && !busy) refresh(); }, 8000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  globalThis.addEventListener('pagehide', () => {
    rememberWorkbench(); leavingPage = true;
    clearInterval(interval); clearTimeout(noticeTimer); setRecoveryIntent(null); desktopLayout.dispose(); registry.disposeAll();
  }, { once: true });
  globalThis.addEventListener('beforeunload', event => {
    if ([...configDrafts.values()].some(draft => draft.text !== draft.savedText)) { event.preventDefault(); event.returnValue = ''; }
  });
  globalThis.addEventListener('storage', () => {
    if (!getBearer()) { registry.disposeAll(); globalThis.location.replace('/unlock'); }
  });
  globalThis.addEventListener('pageshow', event => { if (event.persisted) globalThis.location.reload(); });
  return { registry, refresh };
}

if (typeof document !== 'undefined') {
  if (getBearer()) boot();
  else globalThis.location.replace('/unlock');
}
