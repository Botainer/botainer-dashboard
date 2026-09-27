# Configuration scopes

Project defaults, dashboard registrations and executing runtime profiles are
separate. The interface should identify the exact target before any edit.
Changing a display preference or registration never silently switches a running
terminal to a different installation.

| Scope | Where it is managed | Effect |
| --- | --- | --- |
| Project configuration | Selected project's **Project config** | Botainer YAML defaults for future sessions; validation and write access depend on the selected backend. |
| Saved machine selection | **Settings → Machines** | Which reviewed profiles load next time the dashboard starts. Add, disable and remove require deliberate restart to activate. |
| Runtime profile | Add-machine preparation or advanced prepared-profile import; details are inspectable | Exact installation, state, account, tools, project-setup folders and owner-control records. Host profiles also restrict folder registration. |
| Dashboard registrations | Dashboard settings editor, when offered by the selected backend; `--config PATH` | Machine/installation registration data. Editing it does not replace a startup runtime profile or widen its allowed roots. |
| Botainer user/site policy | Botainer's own configuration tools | Policy for the selected installation/site. There is no general dashboard policy editor yet. |
| Browser display preferences | UI controls | Project pins/grouping, terminal text size and other presentation choices. |

See [Getting started](getting-started.md) for the machine wizard and
[startup](start-dashboard.md) for selection-file paths.

For native Claude or Codex, see [host-agent profiles](host-agents.md). These are
explicitly marked **Host · no container**. Their inventory, account settings and
profile-change limitations differ from Botainer connections.

Existing registered Botainer projects are verified where they live; they need
not be inside the profile's `project_roots`. For Botainer connections that field
selects parent folders offered when creating or opening an unregistered project.
For native host profiles it still limits dashboard folder registration. In both
cases file operations remain bounded to the verified individual project.

## Project config and save state

**Validate and save** checks the current draft before writing those exact bytes.
**Check config** validates without saving. The editor distinguishes unchanged,
invalid, read-only, checking, saving and externally changed content. A failed
or uncertain save retains the draft. A revision conflict requires reloading and
reconciling external changes; it is not permission to overwrite them.

Use **Download draft** before discarding or reloading text you want to keep.
Drafts survive panel changes in the current browser page, not closing the page
or signing out. The downloaded file may contain private configuration.

Project saves use optimistic revision checks, including a check immediately
before replacement. Avoid editing the same file in another program while saving
here. Dashboard locks coordinate dashboard operations; they cannot lock out an
external editor or every native CLI writer. A write between the final read and
replacement can still be overwritten. This is not an atomic compare-and-swap
guarantee.

Before replacement, the local and installed-remote project controllers keep
the known base text and submitted draft in an owner-only `config-recovery`
directory under that controller's private workspace/control directory, on the
machine saving the file. A confirmed save displays its recovery file path.
There are 16 rotating slots per controller, each with an ID and creation time;
later attempts can replace an older slot. These are the last attempts that
reached recovery, not every validation failure. Storage failure refuses the
save. Inspect the ID/time before using a slot, and reconcile its `base` or
`draft` manually with the current file. There is no automatic restore or new
file-access endpoint. Recovery cannot capture an external edit the dashboard
never observed. Keep this history private; it may contain secrets.

This recovery policy covers project config, not the separate dashboard
registration/settings editor. Recovery copies are bounded to about 13 MiB per
controller including its staging file; ordinary configs use much less.

Runtime capabilities are checked again before saving. A session starting while
an editor is open can make project configuration read-only. Changes to project
defaults apply to future sessions; they do not rewrite a running agent's launch
configuration. A per-launch agent choice is separate from the stored default.

## Saved selections and runtime profiles

The installed launcher uses `connections/selection.json` inside its private
data directory: normally `~/Library/Application Support/Botainer Dashboard` on
macOS. **Dashboard data** in the startup output names the actual directory.
An explicit `--data-dir` changes that location; use the same option for every
service command. Only an already prepared source checkout defaults to
`.local/connections/selection.json`. See [application files and private data](installation.md#application-files-and-private-data).
The registry stores a reviewed profile for each selected entry and a revision for concurrent
edit checks. It uses private file permissions. Saving is distinct from activation:
restart with the same selection file to apply pending changes.

Profiles carry exact source/tool identities as well as paths. This prevents a
profile from silently selecting a different installation after an upgrade.
A changed tool/source can therefore require review and re-preparation. Use the
[startup repair route](start-dashboard.md#reconfigure-the-dashboard-after-a-botainer-installation-changes) if normal
startup is blocked. Do not delete recovery data to clear an identity mismatch.

For installed host Claude/Codex updates, use **Settings → Machines → Review agent
update…**. This preserves the connection's projects, existing terminal owners and
browser pairing while selecting reviewed executable bytes for the next restart.
It does not authorize changing accounts, folder scope, environment or tmux; see
[agent updates](host-agents.md#after-claude-or-codex-updates).

Keep profiles, selections and control records outside public source control.
Removing a selection does not uninstall Botainer, delete a project or stop a
session. Different labels do not establish independent installations: shared
state roots, project folders, containers or credentials can still overlap.

## Dashboard registration files

This is advanced registration metadata. For ordinary setup, use **Settings →
Machines**; the saved machine selection and runtime profile are described above.
Creating the JSON below is not an extra first-run requirement.

Registration files use strict JSON by default. The smallest valid example is:

```json
{ "version": 1 }
```

The expanded schema is illustrated by [config.example.json](../config.example.json).
These parsed defaults do not by themselves enable runtime controls; those need
a reviewed runtime profile. Select an explicit configuration file for an
installed startup check with:

```sh
botainer-dashboard check --config /absolute/private/config.json
```

Use the installed executable's full path if it is not on PATH. This checks the
selected configuration and local startup requirements without contacting Docker
or SSH. It does not create the file. Copy and edit the example deliberately.
For source development only, use
`python3 tools/check_config.py --config config.example.json` to validate the
example's registration schema without loading a runtime.

The standalone configuration loader checks, in order: an explicit path;
`BOTAINER_DASHBOARD_CONFIG`; then
`~/.config/botainer-dashboard/config.json`. An absolute `XDG_CONFIG_HOME` replaces
`~/.config`. The dashboard launcher first selects `config.json` in its chosen
data directory when present and no explicit `--config` was supplied. In a
prepared source checkout that is `.local/config.json`; in an installed dashboard
it is under the installed data directory above.
The settings editor's displayed path identifies the actual target.

An absent default file uses parsed defaults without creating anything. An
explicitly selected missing or invalid file fails instead of falling back to
another installation. No project-folder configuration is discovered implicitly.

## Parallel installations and aliases

In dashboard registration and profile fields, use the actual executable or
interpreter path selected by a shell alias. In your own terminal, you can keep
using that alias: replace the `botainer` prefix in maintenance examples with
your usual command, preserving any wrapper and environment settings it needs.
Run it on the selected machine with the same environment and state root; for
project commands, use the selected project's folder. `botainer where --state-root`
displays the selected state root so you can compare it with the dashboard's
connection details; it does not set the state root.
An alias suitable for your interactive shell is not an executable path the
dashboard can launch.

For example, a separate development installation can be represented as
registration data:

```json
{
  "version": 1,
  "machines": {
    "local": {
      "default_installation": "development",
      "installations": {
        "development": {
          "launcher": {
            "mode": "python_module",
            "path": "~/.venvs/botainer-dev/bin/python"
          },
          "state_root": "~/.botainer-dev"
        }
      }
    }
  }
}
```

`python_module` selects that interpreter for `python -I -m botainer.cli.main`;
`executable` selects an existing Botainer executable. The installation is not
copied or updated. Shell commands, aliases, environment substitution and extra
launcher arguments are not accepted configuration syntax.

Machine/installation identifiers use 1–48 letters, digits, underscores or
hyphens, starting with a letter or digit. Local paths may be absolute or begin
with `~/`; remote paths must be absolute. Remote registration data names an SSH
alias but does not create credentials, establish a connection or replace a
remote runtime profile. See [SSH setup](ssh-setup.md).

## Optional YAML

An explicit `.yaml` or `.yml` filename selects the optional YAML parser; `.json`
remains JSON. YAML requires PyYAML in the dashboard environment. It is not part
of the core dependency selection, and the dashboard does not install it
automatically. JSON continues to work without it.

The same registration schema applies. Validation rejects duplicate keys,
anchors/aliases, explicit tags and merge keys, with size and nesting limits.
The editor saves exact validated text so comments are preserved. Files are not
automatically converted. Runtime profiles and saved selections remain JSON;
project YAML continues to follow Botainer's authoritative validation.

Check [status](status.md) and the [support matrix](setup-support-matrix.md) for
capabilities that remain incomplete.
