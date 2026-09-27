# Start, find and stop the dashboard

These commands use the installed dashboard executable. Read
[Installation](installation.md) first if you do not have it; that page also
states the tested installation route and remaining limits. Use the executable's full
path when its environment's `bin` directory is not on your shell's `PATH`.
Starting the service does not install Botainer or start an agent session.

Every `botainer-dashboard` command on this page runs in your own terminal
on the **computer hosting the dashboard service**, from any directory. These
commands manage the dashboard, not Botainer. Selecting a
remote machine in the GUI does not move the dashboard service to that machine.
For `botainer ...` maintenance commands, use the selected Botainer installation
on its own machine; see [Botainer maintenance](maintenance.md).

## Ordinary startup

On the service computer:

```sh
botainer-dashboard check
botainer-dashboard start --open
```

On first launch, no machines are configured. Open **Settings → Machines** to
add them. Later launches reuse `connections/selection.json` in the private data
directory, with no implicit trial-profile discovery. Shell activation is not
required when using the executable's full path.

Startup prints full-path commands for status, stop and pairing. They work from
any folder without activating the environment. Use those commands if the bare
`botainer-dashboard` name is not available in another terminal.

`check` checks local prerequisites and configuration without starting a service,
opening a browser or contacting Docker/SSH. It does not verify live workload
readiness. `start --open` starts the foreground service and asks an existing
browser to open its loopback address. Keep that terminal open. It does not
install a background service or arrange startup when the computer boots.

### Select the private data directory

Installed defaults are `~/Library/Application Support/Botainer Dashboard` on
macOS, and `$XDG_STATE_HOME/botainer-dashboard` or
`~/.local/state/botainer-dashboard` on Linux. Linux installation remains
unqualified. Code and private data live separately.

For another private location, use the same `--data-dir` on every command:

```sh
botainer-dashboard check --data-dir /absolute/private/dashboard-data
botainer-dashboard start --data-dir /absolute/private/dashboard-data --open
botainer-dashboard status --data-dir /absolute/private/dashboard-data
```

Replace the example path with your actual directory. Use it for `stop` and
`pair` too. There is no automatic migration from another data directory or a
source checkout; selecting an empty directory opens a new setup.

### Choose a port

To request a particular port, use this startup command instead:

```sh
botainer-dashboard start --open --port 8765
```

The service uses `http://127.0.0.1:8765`. If that port is occupied, startup refuses;
it does not stop the other process or silently select a different port. Without
`--port`, startup reuses the saved port. `start --port 0` requests
an available port, producing a new browser origin that may need fresh pairing.
Port `0` is a startup option, not a way to select a running service.
Different ports can run separate dashboard services, even with the same machine
selection; there is no selection-wide singleton lock. Check `status` before
starting another copy.

### Find a running dashboard

In another terminal, run:

```sh
botainer-dashboard status
botainer-dashboard status --port 8765
```

`status` lists services recorded in **the selected data directory**, including
their address and service instance ID. It checks local control records and owner
liveness without loading machine backends, contacting SSH or querying Docker.
It does not report whether your agents or cluster jobs are healthy. It is not a
system-wide process search; a dashboard using another data directory has its own
records. Include the service's `--data-dir` when you used one at startup.

Use the reported URL to open the existing dashboard. If multiple services use
the same port at different times, select the exact instance ID from `status`:

```sh
botainer-dashboard status --instance <run-id>
botainer-dashboard status --json
```

Replace `<run-id>` with the actual 32-character hexadecimal ID; do not type the
angle brackets. `--json` provides machine-readable status without pairing codes.
A record alone does not prove its service is still running; check the reported
control state.

For scripts, `status` exits `0` when it finds responsive managed services and no
uncertain managed records, `1` when none are active, and `2` for unavailable or
uncertain control (including only legacy records). Legacy history does not make
an otherwise healthy managed service fail. `stop` exits `0` only after confirmed
shutdown, `1` if no active managed service matches, and `2` for refusal or an
unconfirmed result.

### Stop or restart the dashboard service

Finish any Botainer initialization, preflight or confirmation prompts first.
Then press **Ctrl+C** in the service's original terminal, or use another terminal:

```sh
botainer-dashboard stop
```

With one verifiable active service in the selected data directory, `stop` selects it. With
several, select the intended service explicitly:

```sh
botainer-dashboard stop --port 8765
botainer-dashboard stop --instance <run-id>
```

These are alternatives; use one matching your intended service. A stop request
uses private local control files owned by your account. It does not call a public
HTTP shutdown endpoint or kill a process by its recorded PID. The command waits
for the owner to release control before reporting confirmed shutdown. A timeout
means shutdown is **unconfirmed**, not that the command forcibly killed anything.
Check `status` and the original service terminal before retrying or restarting.

Stopping the dashboard disconnects its browser terminals. It does **not request
Stop session or Cancel Slurm job** for durable container, Screen or tmux sessions.
However, native initialization and launch consoles owned by the service can be
interrupted. Finish those prompts before stopping; do not assume every phase of
a launch survives. See [sessions and recovery](sessions.md) for reconnect limits.
To stop an agent or allocation, use its reviewed session/job action in the GUI.

To restart, stop the service and run the same `start` command, with the same
data directory, selection file and desired port. There is no automatic restart after an uncertain
shutdown. An older running service that predates this control protocol cannot be
stopped through the new command: finish pending prompts and use **Ctrl+C** in its
original terminal once, then start the updated dashboard normally. Never kill an
unknown process to reclaim a port.

## Use another selection file

```sh
botainer-dashboard check --connections /absolute/private/team-connections.json
botainer-dashboard start --connections /absolute/private/team-connections.json --open
```

These are placeholder paths. Changing the selection file does not change the
data directory used by `status`, `stop` or `pair`. Keep using the same file after
GUI changes. Saving machine additions, removal
or enable/disable changes marks them pending restart. Resolve active native
setup/launch prompts, stop the dashboard as above, and run the same `start`
command again. The GUI does not forcibly restart the service.

The selection file and stored profiles are private operating data. Back them up
privately; do not add them to public source control.

## Pair the browser

Leave the dashboard running. In **another interactive terminal on the same
computer, under the same account**, run:

```sh
botainer-dashboard pair
```

Copy **One-time pairing code** into the browser's pairing form. The command
also confirms the dashboard address and code expiration. **Need a replacement
code? Run the same command again.** No file path or service restart is needed.

If several dashboards may be running, the command refuses to guess. The pairing
page displays a command with **its actual port**, for example:

```sh
botainer-dashboard pair --port 8765
```

Use the command shown on your page, not the example port above. For an exact
instance, `pair --instance <run-id>` uses the ID reported by `status`. Startup
also prints a complete pairing command with full paths, usable from any folder.
If you selected another data directory, use the printed command or include that
same `--data-dir` here.

The command contacts only the selected service's private local control files;
it does not contact your selected machines or start/stop sessions. Code output
requires an interactive terminal. Redirecting or piping it is refused before
discovery or requesting a code.

Codes work once and expire ten minutes after issuance. An unused code keeps its
original expiration time; requesting it again does not extend that time. If the
code was used or expired, the running service issues another. This preserves
existing browser pairings and sessions. Remembered browser access can last up to
30 days. Keep codes out of URLs, screenshots, shared messages and recorded
terminal output.

**Sign out** revokes the current browser pairing and disconnects its terminal
views across tabs and machines. It does not send Stop or Cancel commands.
Simply closing a browser tab retains pairing. Reconnecting a surviving session
and restoring full terminal history are different capabilities; see
[sessions](sessions.md).

A different browser, address or installation selection can require fresh
pairing. Use the printed command again; renewing a code needs no service restart.

If the command fails, run `botainer-dashboard status` and check the
selected port/instance, account and service terminal. A
stopped or unresponsive pairing control cannot return a code. The command never
falls back to a saved code that may have expired or been used, and never restarts
anything automatically. If another request is in progress, let it finish before
retrying. An authentication-storage or browser-capacity error requires attention
to that condition; repeatedly requesting codes will not bypass it.

For an **older service** that has pairing control but no service-status records,
use the complete `--pairing-code` command printed by its startup terminal. That
compatibility command selects `run/<run-id>/access.json` under that service's
data directory explicitly; older source checkouts keep this under `.local/`.
Startup prints the exact **Private pairing file** path. Do not guess the newest
file. A still older process without pairing control needs its own startup
instructions until a deliberate normal restart, after resolving pending
setup/launch operations. A pairing-control failure alone does not mean the
dashboard or its sessions have stopped. Never weaken authentication or expose
the service on a public interface as a pairing workaround.

## Reconfigure the dashboard after a Botainer installation changes

For a saved local or remote Botainer connection, use **Settings → Machines →
Review Botainer update…**. Inspect the already installed copy, review its exact
selection and save the update for the next dashboard restart. This preserves
the connection's saved project folders, state and control settings. It does not
install software, activate the replacement or stop sessions. Follow the
[Botainer update review](maintenance.md#update-botainer-or-the-dashboard) for
trust checks, pending launch prompts and remote-history limitations.

After a host-agent update, use **Settings → Machines → Review agent update…**
for its existing host profile. Inspect the installed executable, review the
changed path/hash and save the update. This retains project registrations and
browser pairing, and applies after a dashboard restart; existing sessions retain
their original owner. It neither installs an agent nor runs its executable while
inspecting. See [host-agent updates](host-agents.md).

If the dashboard still opens, review the update there. If a missing or changed
Botainer installation prevents startup, open the saved selection without loading
runtime controllers:

```sh
botainer-dashboard check --setup-only
botainer-dashboard start --setup-only --open
```

These commands use the ordinary saved selection. If you normally use a custom
file, include the same `--connections` argument here, and preserve `--data-dir`.

Open **Settings → Machines**, find the affected connection, and choose **Review
Botainer update…**. If its old source folder or Python no longer exists, enter
the intended installation's current absolute paths before inspecting it. The
review reads the retained selection without needing to run the old installation.

For a connection you want to leave offline, use **Disable… → Disable for next
restart** instead; its profile remains saved. Broader account, project-folder or
tool changes require connection setup. Preserve old profiles and operation
records; removing an entry is not the ordinary update procedure.

Stop this setup service,
then start normally without `--setup-only`. This mode may need separate browser
pairing. It does not repair an uncertain running session. Corrupted registry or
profile bytes still require a reviewed private backup; setup mode does not
bypass storage-integrity checks.

This repairs the dashboard's selection of an existing Botainer installation.
It does not install, upgrade or repair Botainer itself. There is no automatic
dashboard updater yet either; follow the separate
[environment upgrade procedure](installation.md#upgrade-rollback-and-removal).

## Advanced explicit-profile startup

For diagnosis or an existing prepared selection:

```sh
botainer-dashboard check \
  --local-profile /absolute/private/profiles/local.json \
  --remote-profile /absolute/private/profiles/cluster.json
```

These are placeholder paths, not files created by the command. Replace `check`
with `start` and add `--open` to start those profiles. Omit the remote option for local
only, or the local option for remote only. Repeat `--remote-profile` for multiple
remote installations. Optional `--host-profile` selects agents running directly
on the host, **without containers**.

Explicit-profile startup fixes the selection in its arguments; use saved
`--connections` selection for routine GUI add/remove. Profiles can seed a new
selection on its first start, but cannot silently replace an existing selection.
Do not combine installed profiles with the older prepared trial flags
`--local-only`, `--cluster-only`, `--cluster-profile` or `--native-cli`.

## Prepared source checkout compatibility

From an already prepared development checkout, replace `botainer-dashboard`
with `python3 tools/dashboard.py`. That launcher retains the existing
`.local/envs/botainer_dashboard/` environment and defaults to the checkout's
`.local/` data. It does not use the installed package's per-user data directory
or automatically move any records. Older `--check`, `--open` and the printed
`--pairing-code` forms remain compatibility options for prepared setups.
The installed package does not support the older trial-mode flags.

For SSH login use [SSH setup](ssh-setup.md). For normal project work see
[Getting started](getting-started.md), and check [status](status.md) before
relying on a particular recovery route. `prototype/index.html` is a simulation
and cannot operate real projects.
