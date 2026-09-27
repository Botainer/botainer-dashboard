# Host agents: direct access to this computer

**⚠ A host agent runs without a Botainer container.** It runs as your operating
system account. Its own permission prompts still apply, but the dashboard adds
no operating-system sandbox. The selected project folder does not restrict what
the agent can read, change, execute or reach on the network with that account's
permissions.

Host projects and session rows use dark red backgrounds and warning labels.
Launch controls identify the agent and say **on host**. The terminal keeps a
red **HOST · NO CONTAINER** warning above its normal text area and a thin red
frame around the terminal output, including in
focus mode and when an open host terminal's inventory is unavailable. These
warnings identify the execution environment; they do not grant control or
change the agent's permissions.

Use a Botainer project when you want a container session. Opening the same folder
as a host project creates a separate dashboard entry; it does not convert the
Botainer session or carry its container isolation over to the host agent.

## Register an existing host folder

**Initial host-profile setup is maintainer-assisted in this alpha.** The ordinary
machine wizard cannot create one, and there is no supported self-service profile
generator yet. Use this workflow only after receiving and reviewing a profile
prepared for your own tools and folders. Do not copy another person's profile or
invent executable hashes to satisfy import checks. Local Botainer container
sessions do not need a host profile.

You need a **loaded** host connection. Host profiles currently use
**Settings → Machines → Advanced → Import prepared profile**, not the regular
Add machine wizard. A saved profile needs a dashboard restart before it can
be used; see [profile setup](#what-a-host-profile-means).

1. Select the loaded host connection in the left sidebar. Confirm its
   **Host · no container** marking and configured agent names.
2. Choose **Add project**. Under **Machine / runtime**, select that host
   connection; under **Action**, choose **Open an existing folder**.
3. Choose the allowed parent under **Workspace folder**, then enter the existing
   folder's path **relative to that workspace**. Confirm the **Host agent profile**
   and optionally enter a project display name. An empty path or `.` cannot
   register the workspace root itself. To use that folder as a project, the
   profile must allow its parent; choose that parent and enter the folder name.
   For example, to add `/work/projects/demo`, select `/work/projects` and enter
   `demo`, rather than the full path.
4. Choose **Open folder**. This registers the folder in the dashboard for that
   host profile. It does not initialize Botainer, create a container or start
   an agent.

To start work afterward, choose **New Claude session on host** (or Codex) and
read the launch warning. The agent runs with your account's access, **without
container isolation**. Registration does not connect to an agent already running
in that folder.

For an existing local Botainer project, **Project details → ⚠ Set up host agent ·
no container…** offers the same registration when available. It creates a
separate host entry and does not adopt an external session.

## Why some sessions are missing

The host view lists folders registered with the selected host profile and
sessions that the dashboard started through that profile. It is **not a list of
every Claude or Codex session on the computer**. It does not scan agent history
folders, find arbitrary terminal windows, or adopt a process that was started
elsewhere.

Automatic discovery of registered **Botainer** projects is a different source.
It does not fill a host profile's project registry or import Claude/Codex history.

To continue an existing session from another terminal, keep using that terminal
and the agent's own continuation controls. Starting a dashboard host session
creates new work; it does not reconnect that other session. External-session
discovery and adoption need separate implementation and ownership checks.

A folder may also be absent because it has not been registered as a host project,
its host profile is disabled, or that profile's allowed project roots do not
include it. Allowed roots limit dashboard folder selection and its file browser;
they do not limit the running agent's host access.

## Choose Claude or Codex

A reviewed host profile can offer Claude, Codex, or both. Select the desired
agent before starting a new session. The launch confirmation identifies the
selected agent, executable and project folder. A session's agent remains the
one selected for that launch; changing the next-session choice does not change
an existing session.

**New Claude session on host** (or Codex) starts a separate agent session in the
selected project's folder, even when another terminal is open. Other sessions
keep running and share the same files; concurrent edits can conflict. To return
to existing work, select its session row or open tab and use Connect if needed.

When a profile has only one agent, the launch button names it and no agent
dropdown is shown. With multiple agents, each appears once; the profile default
is selected initially and marked **(default)**. The choice applies only to the
next new session.

The dashboard starts the selected executable directly. It does not evaluate your
shell aliases, accept arbitrary launch arguments, or fall back to a host shell
if launching fails. Installed executables and the terminal owner are checked
against the prepared profile. Updating an executable can require a fresh review.

The current Claude launch adapter explicitly disables startup hooks and MCP
servers and passes settings intended to suppress automatic updates. Codex is
started without equivalent hook/MCP overrides. These are different startup
policies, **not equivalent isolation guarantees**. The agent's own settings and
permission behavior still matter, and the dashboard does not enable a
permission-bypass mode. Read the launch warning and selected profile details.

## After Claude or Codex updates

An agent update does not require removing its connection or registering its
projects again. The dashboard keeps the exact executable selected at setup until
you review a replacement. If the old file disappears or changes, new launches
of that agent are unavailable; the dashboard and other agents remain usable.
Existing sessions can still reconnect through their original verified terminal
owner, without needing the old agent executable on disk.

1. Open **Settings → Machines → Review agent update…** on the host connection.
2. Choose Claude or Codex and select **Inspect installed update**. This looks
   for that command in the connection's configured PATH. If needed, enter the
   new executable's absolute path. Shell aliases are not evaluated.
3. Review the old and proposed paths and file hashes, confirm that you trust the
   replacement, and choose **Save update for restart**. Inspection only reads the file;
   it does not run the agent, install software or establish that a binary is safe.
4. Restart the dashboard service to use the saved executable for new sessions.
   Reloading the browser is insufficient. Finish any setup/launch prompts before
   restarting; see [service shutdown](start-dashboard.md).

Projects, history, original session owners and browser pairing are preserved
across these executable-only updates. Normal pairing expiry, signing out or
choosing a different browser origin can still require a code. Existing agents
continue their original running version; an update never restarts them.

If the inspected file changes before saving, inspect again. If the original
version remains installed, the dashboard can continue using it until you choose
to update. The same workflow applies to both supported agents.

This action changes only the path and hash of an agent already in the profile.
It does not add agents, change accounts, project folders, environment, startup
restrictions or tmux. Those are separate configuration changes and may require
a new connection and browser pairing. A changed tmux executable still requires
separate review; the agent-update action cannot approve it.

## What a host profile means

A host profile selects installed agent executables, a home directory, a bounded
set of environment variables, allowed project folders and a private terminal
control directory. It is a dashboard launch configuration, **not another
Botainer installation** and not another computer.

The current registry accepts up to eight host profiles. Each profile can contain
one Claude executable and one Codex executable. Multiple profiles require unique
IDs and separate terminal control directories. They can point to the same
project folder, but concurrent agents may then change the same files; separate
profiles do not prevent this.

Profiles are currently added through **Settings → Machines → Advanced → Import
prepared profile**, after the exact tools and directories have been reviewed.
The regular Add machine wizard prepares Botainer connections; it does not yet
prepare host profiles. A maintainer must prepare and explain the exact executable
identities, tmux dependency, account/environment scope and private control folder
before import. A profile inspection does not install those tools or certify their
behavior. Saving an imported profile takes effect after a deliberate
dashboard restart. See [configuration scopes](configuration.md).

Settings lists both loaded profiles and saved selections for the next service
start. The sidebar shows configured agent names below each loaded host profile.
A newly saved profile appears there with **Saved · restart required**; clicking
it opens Settings. It cannot launch work until the dashboard service loads it.
Reloading the browser or refreshing the project list does not apply saved
profiles. In compact mode these pending entries remain visible.

Profile labels are descriptive names. The agent names below them come from the
loaded configuration, so a profile called “Host agents” may offer Claude only,
Codex only, or both. Names do not establish an account or a running session.

Do not edit an active profile in place. **Review agent update** writes a new
immutable revision and retains the original profile as proof of unchanged
connection scope. It preserves the original history and recovery identity.
Keep those retained profiles and control records while sessions need access.
Importing an unrelated changed profile still creates a separate history
namespace; broader configuration migration is not implemented.

## Multiple accounts

An agent name identifies the program, not its signed-in account. A display label
such as “Claude — work” is user-supplied information; the dashboard currently
does not verify that account, subscription or billing identity. A second label
does not create a second login.

The current profile has one shared home directory for its agents and a restricted
environment schema. It does not expose per-agent account/configuration directory
settings, credential selection or arbitrary command wrappers. Two profiles can
therefore use the same credentials despite having different labels or executable
paths. Changing a home directory is not an account-isolation mechanism and can
also change agent settings and login behavior.

Use the agent's own account display and sign-in workflow to confirm which account
is active. Do not paste credentials into a profile or use a launch label as proof
of account identity. A guided, verified multi-account setup remains future work.

## Leaving and stopping

Disconnecting a terminal view or signing out of the dashboard does not request
that the host agent stop. Dashboard-created host sessions use their own verified
terminal owners, separate from browser views. Reconnect only when that original
owner remains verifiable; an unknown owner is not replaced automatically.

**Close host terminal** requests termination of the selected verified host
terminal. It does not claim to stop every background process the agent may have
created. Host descendants have ordinary account privileges and may outlive their
original terminal. See [sessions and recovery](sessions.md) for current limits.

## Planned organization

The intended navigation is a computer containing a clearly separate **Host — no
container** section, with named agent profiles underneath it. For example:

```text
This computer
  Botainer
    Project A
  ⚠ Host — no container
    Claude — default
      Project B
    Codex — default
      Project C
```

This describes the target model, not a completed nested navigation feature.
Additional account profiles should be possible without pretending they are
separate machines or installations. Before expanding that model, implementation
needs:

- Explicit migration for broader configuration changes, extending the existing
  executable-only update workflow without changing live terminal owners.
- A host-profile wizard showing the executable, configuration scope, startup
  restrictions and account label; account identity remains marked unverified
  unless checked through a supported agent mechanism.
- Separate, reviewed configuration for each account, without arbitrary shell
  wrappers, permission bypasses or credential copying.
- A clear choice between starting new work and discovering or reconnecting
  external work; observing history alone must never authorize terminal control.

Host support remains part of the [development alpha](status.md); these planned
features are not claims of current account isolation or unattended readiness.
