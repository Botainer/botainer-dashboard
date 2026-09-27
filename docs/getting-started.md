# Getting started

Use Botainer Dashboard to find your projects, start an agent, and work in its
terminal from one browser window. This guide takes you from an installed
dashboard to your first session.

A **Botainer project** is a folder managed by Botainer. Optional **host projects**
are ordinary folders registered with the dashboard for agents running without
containers. A **session** is one run in a project. A **connection** selects a
Botainer installation and account, or a reviewed host-agent profile. Several
connections can appear together in the same dashboard.

## Before you begin

You need an installed dashboard. For container sessions, you also
need an existing Botainer installation on each machine you want to use.
Botainer's container runtime, images and agent sign-in must be set up there.
For a first alpha trial, use the local macOS/Docker route. It also requires
**local tmux** and a Botainer interpreter running Python **3.11 or newer**.
The local test project's `nudge` plugin must be disabled; the dashboard uses
tmux for local ownership and does not yet support nesting the Screen wrapper.
Review this in **Project config**, without changing other running projects.
The dashboard has its own separate Python requirement. Check the
[prerequisite table and Docker endpoint instructions](installation.md#prerequisites-and-release-inputs)
before opening the machine wizard.

**Installing for the first time?** Start with [Installation](installation.md)
for the offline alpha installer and its qualification limits. Startup commands
do not install packages. The [operator and assistant setup guide](agent-setup-guide.md)
can help someone set it up for you. See [platform support](setup-support-matrix.md)
for the tested routes.

For a remote cluster, first establish SSH access from the computer running the
dashboard. Follow [SSH setup](ssh-setup.md), including connection sharing if your
login needs a password or MFA. An open SSH terminal alone may not be enough.
The current remote adapter is a **maintainer-assisted, site-qualified preview**
of Botainer's SSH/Slurm HPC workflow. A working SSH alias does not qualify a new
cluster; review [cluster prerequisites](ssh-setup.md#before-starting-a-cluster-session)
before launching anything there.

## 1. Open the dashboard

On the computer that will run the dashboard, open your Terminal application.
Use the executable's full path if it is not on your shell's `PATH`.
Check the installation:

```sh
botainer-dashboard check
```

If the check reports missing requirements, resolve them before continuing.
It checks local startup files and settings; it does not test your SSH connection
or launch a container. When the check passes, start the service:

```sh
botainer-dashboard start --open
```

Your browser opens the dashboard. **Keep this service terminal open.** If the
browser does not open, use the address printed after **Dashboard:**. If the
service may already be running, use `botainer-dashboard status` in another
terminal to find its address. Status checks the selected data directory's service
records; it does not contact your machines or inspect their workloads. If you
selected `--data-dir` at startup, use that same option for later commands.

For a fixed port, start with
`botainer-dashboard start --open --port 8765` instead. An occupied port
causes a refusal; the dashboard does not displace the other process. These
commands run the dashboard; they do not install or update Botainer. See
[start, status, stop and ports](start-dashboard.md) for the full command guide.

An already prepared source development checkout can use
`python3 tools/dashboard.py` in place of `botainer-dashboard`, from its source
folder. It keeps its existing environment and `.local/` state; installed commands
do not automatically import those records.

### Pair your browser if asked

Pairing gives this browser access to the dashboard's controls.

1. Leave the service running. Open **another terminal on the same computer,
   under the same account**.
2. Run the command shown on the pairing page. It looks like
   `botainer-dashboard pair --port 8765`, using your dashboard's actual
   port. With only one running dashboard, `botainer-dashboard pair`
   is enough. Include the same `--data-dir` if you used one at startup; the
   complete command printed by the service includes it.
3. Copy the displayed **One-time pairing code** into the browser and choose
   **Pair this browser**.

Codes work once and expire after ten minutes. **Need a new code? Run the same
command again.** This does not restart the service or disturb existing
pairings. Keep codes private. A remembered browser usually needs no new code.
See [startup and pairing help](start-dashboard.md#pair-the-browser) if an older
running service does not offer the command or pairing fails.

## 2. Connect your first Botainer installation

Open **Settings → Machines**. If your intended connection already says **Loaded
in this dashboard**, skip to step 3, even if it has no projects yet. Otherwise
choose **Add machine**. First startup has no machines configured; this is where
you add them.

Choose **This computer** or **Remote machine over SSH**, then give the connection
a recognizable **Display name**. For a remote, also choose a stable
**Connection ID** without spaces, such as `research-cluster`, and enter your
working **Existing SSH alias**. The local connection ID is fixed as `local`.
The current dashboard supports one local Botainer connection and multiple remote
connections.

The form asks which folders and installation it may use:

| Field | What to enter |
| --- | --- |
| Allowed parent project folders | Existing parent folders for adding or creating projects, one absolute path per line. Existing registered Botainer projects are verified at their own locations; these parents are not an extra launch allowlist. |
| Private dashboard control folder | An existing folder owned by your account with mode `700`, dedicated to dashboard control records. For local tmux, its complete absolute path must fit within **55 encoded bytes**. Keep it separate from actual projects, application code and Botainer state. Follow the check's creation instructions if missing. |
| Installation paths and advanced settings | Open this for a custom installation or when discovery fails. Select its actual Botainer launcher and Python; identify the source checkout or installed package location as requested. For Docker, select the correct local Unix socket as described in [Installation](installation.md#check-the-existing-local-tools). |
| Botainer state root | The state folder used by that Botainer installation. It contains its records and settings; it is not the Botainer source folder or a project folder. |

Use paths **on the selected machine**. If you normally invoke Botainer with a
shell alias, use the installation paths behind that alias in this form. Source
installations and the candidate native wheel/pipx path have different integrity
checks. Botainer wheel/pipx onboarding is still awaiting live acceptance; do not
bypass a failed preparation check. The dashboard never installs Botainer here.

1. Read and select the acknowledgement for inspecting the selected tools, then
   choose **Check connection**. This performs bounded read-only checks, using
   SSH for a remote and also querying Docker locally. It starts no agent.
2. Resolve any reported problems and check again. Under **Review before saving**,
   confirm the installation, account, folders and available capabilities. For a
   local installation, review any **host hook** paths and fingerprints. These
   programs run on the host for Botainer setup or cleanup; approve them separately
   only if you trust the selected installation and plugins. Leaving them
   unapproved prevents projects requiring them from launching. Then select the
   installation trust acknowledgement and **Save for restart**. This does not
   bypass Botainer's own warnings or consent when starting a session.
3. Finish any pending setup or launch prompts. In the service terminal, press
   **Ctrl+C**, then run the same startup command again. With the ordinary command
   above, run `botainer-dashboard start --open` again. If you used a custom data
   directory or selection file, keep using the same arguments.
4. Reopen the printed dashboard address and check the connection in
   **Settings → Machines**. Its status should change from **Saved · loads after
   restart** to **Loaded in this dashboard**.

**Reloading the browser does not activate a saved connection.** Saving also does
not start an agent. Add further machines with the same process when needed.
For path and configuration details, see [Configuration](configuration.md).

Entries marked **Host · no container** are a separate opt-in feature. Their
agents run directly with your account's host access. For a Botainer container
session, choose a Botainer connection. To work with a host agent instead, follow
[Register an existing host folder](host-agents.md#register-an-existing-host-folder).
Host profiles use **Advanced → Import prepared profile**, not **Add machine**,
and must be loaded before they can register folders or launch agents. Initial
host-profile preparation is maintainer-assisted in this alpha; skip it for an
ordinary Botainer container trial.

## 3. Find a project or add a folder

**Existing registered Botainer projects appear automatically**, including
inactive projects. You do not need to add each one again. The list comes from
your selected Botainer installations; it is not a scan of every folder on disk.
This discovery applies to Botainer projects. Host folders have a separate
dashboard registry for each host profile; Claude/Codex history is not imported.

An existing Botainer project does not need to live under a dashboard setup
folder. The dashboard checks the selected Botainer installation's registration
and the project's actual folder identity before enabling controls. Configured
parent folders are destinations for **Add project**, not an extra allowlist for
existing projects. Missing, moved or ambiguous registrations remain visible with
an explanation; the dashboard does not silently adopt a replacement folder.

In the left sidebar, use **Filter by connection**, the search box and **Sort**
to find a project. Sorting offers **Recent launches**, **Name** and **Active
first**. Pin frequently used projects. The arrow beside a project expands or
collapses its sessions; **Compact** fits more projects on screen.

For a folder Botainer has not registered yet, choose **Add project**. Select the
**Machine / runtime**, then **Open an existing folder** or **Create a new project
folder**. Choose the **Workspace folder** and **Botainer installation**, and enter
the folder path **relative to that workspace**. Choose **Open folder** or
**Create folder** to continue.

If initialization is needed, answer Botainer's prompts in the **Project setup ·
Botainer CLI** terminal. When setup finishes, review **Project config**. Creating
or registering a project does not start an agent; that is the next step.
Local durable sessions require `nudge` to be disabled. Qualified cluster sessions
require it enabled for Screen ownership; the default HPC template may not enable
it. Keep these project defaults appropriate to the selected runtime; see
[cluster prerequisites](ssh-setup.md#before-starting-a-cluster-session).

**For an existing host folder:** select its loaded host connection in the
sidebar, then choose **Add project → Machine / runtime →** that host connection.
Set **Action** to **Open an existing folder**, choose an allowed parent under
**Workspace folder**, enter the relative folder path and choose **Open folder**.
Use a child folder: an empty path or `.` cannot register the workspace root
itself. To use that folder, select an allowed parent and enter its folder name.
This only registers the folder; it does not adopt a running external session.
Starting **New Claude session on host** (or Codex) is a separate action with a
**no container isolation** warning. Allowed parent folders limit dashboard folder
selection and browsing, not the host agent's account permissions. See
[Host agents](host-agents.md) for the full workflow and the optional registration
shortcut in a local Botainer project's details.

## 4. Start a session or open existing work

Select your project and confirm its machine and folder at the top of the work
area. To start new work:

1. If offered, choose **Agent for new session**, or leave **Project default**
   selected. Otherwise the saved default is used. This choice applies to the
   next run; it does not change the project's saved defaults.
2. Choose **New session**. The button may name the agent, such as **New Codex
   session**.
3. Read Botainer's preflight output and warnings in **Launch · Botainer CLI**.
   Answer any confirmation there, including `y/n` prompts.
4. After a successful launch, use the session terminal. A cluster session may
   wait in the scheduler queue before it can be connected.

To resume work, select an existing session in the sidebar and use **Connect**
when offered. Selecting a project with one current session opens that session;
with several, the dashboard lets you choose. **New session starts separate work**
and does not reconnect an existing agent or a pending launch console.

If a launch is already in progress, return to its **Launch · Botainer CLI** entry
to finish the prompts. If controls are unavailable, read the displayed reason.
A running container or job may lack a verified attachable terminal; the agent
may still be running. See [sessions and recovery](sessions.md).

## 5. Work, leave and return

Use **Focus terminal** for more terminal space. **••• → Text size** adjusts the
terminal font. **Copy**, **Paste** and **Find** are in the terminal toolbar.
Open terminal tabs let you switch between work on different connections.

Use **Switch…** (Ctrl/⌘Shift+P) to search projects and sessions across connections,
or **Sessions** beside the open tabs to find an open view. Drag the sidebar or
details-panel edge to resize it; keyboard users can focus that edge and use the
arrow keys. **Dock panel** keeps details beside the terminal when there is room.

Pane widths, filters and open view identities are remembered in this browser.
After a reload, surviving views reopen **disconnected**: choose **Connect** when
ready. Terminal contents, unsent input and editor drafts are not restored.
The header bell and browser-title count reflect bells observed in connected
terminals only; they cannot determine whether every agent needs your input.

To leave a terminal without requesting a stop, use **••• → Disconnect view** or
**Close view**. Closing the browser also sends no stop request. **Sign out**
revokes this browser's dashboard access and disconnects its views; you will
need to pair again.

To end work, use the selected session's **Stop session** action and review its
scope. **Cancel Slurm job** cancels the whole allocation, which may contain other
work. Supported durable sessions are separate from browser views, but recovery
is not fully qualified: finish pending launch prompts before leaving, and check
the session's state before reconnecting or starting another.

To stop the **dashboard service**, finish any setup or launch prompts first,
then press **Ctrl+C** in its service terminal or run
`botainer-dashboard stop` in another terminal, with the same data directory.
With multiple services, choose one using `stop --port 8765` or its instance ID.
This disconnects browser views without requesting a stop of durable sessions;
service-owned setup or launch consoles can be interrupted. Wait for confirmed
shutdown before restarting. A timeout is not confirmation. See
[stopping and restarting](start-dashboard.md#stop-or-restart-the-dashboard-service).

## Browse project files without starting a session

Choose **Browse files**, or click the selected project's folder path. The panel
shows the folder on that machine; remote files are read over the existing SSH
connection. Opening it starts no agent or scheduler job.

Use the breadcrumbs, **Parent folder** and **Refresh folder** to navigate.
**Filter names** filters the current listing. **Full width** gives the panel more
room; **Side pane** returns it beside your terminal.

Ordinary UTF-8 files have a read-only preview up to 64 KiB, with **Copy text** and
**Copy file path**. **Copy folder path** is available for a listing. Remote paths
belong to the remote machine. Hidden/protected names, links and special files
are restricted, so the view is not a complete filesystem inventory.

Use **Project config** to edit Botainer defaults where supported. **Check config**
validates a draft; **Validate and save** checks and saves it. The editor explains
when it is read-only or a save needs attention. Changes apply to future sessions.
See [configuration and save behavior](configuration.md#project-config-and-save-state).

## When something needs attention

- **A project is missing:** choose **All connections**, clear search and any bell
  filter, or use **Show all projects** when offered. Check the machine's connection
  and use **Refresh**. An offline machine may show dated cached observations.
- **You cannot start a session:** check for **Restart needed**, an offline
  connection, existing active work, or an unverified project/session identity.
  Existing registered projects need no separate parent-folder approval. Read the
  specific reason before retrying.
- **A connection cannot report current status:** read its explanation in
  **Settings → Machines**. A loaded connection is configured for this dashboard;
  it may still need SSH login, network access or an installation update review.
  The connection's reason and next step distinguish these problems. If the cause
  is unknown, the dashboard says so rather than assuming that Docker or a job
  stopped. Use **Refresh** after correcting the problem; ordinary checks also
  continue automatically while the dashboard is open.
- **A status check is delayed:** the dashboard keeps the last reported state
  while waiting for a current result. New controls stay disabled once that
  information is too old. This does not mean the session stopped. An already
  connected terminal keeps its view, with a compact status note. A reported
  running state is not proof that the agent is ready for input.
- **A launch failed or its outcome is unclear:** inspect its existing console and
  session state before starting again. [Botainer maintenance](maintenance.md)
  explains diagnostics, agent sign-in and image preparation.
- **SSH stopped working:** follow [SSH troubleshooting](ssh-setup.md) in your own
  terminal. The dashboard does not collect passwords, key passphrases or MFA codes.
- **A bell appears:** it records terminal bell activity from connected views. It
  can suggest attention is needed, but absence of a bell does not mean every
  agent is ready or finished.

Use **Settings → Help** for these guides. Opening Help from the machine wizard
preserves the draft; **Back to setup** returns to it. For later machine changes,
use **Settings → Machines**. **Disable** and **Remove** take effect on restart;
they do not delete projects, uninstall Botainer or stop running work.
