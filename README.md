# Botainer Dashboard

A dashboard for managing interactive agents locally and on remote clusters,
including agents managed by [Botainer](https://github.com/botainer/botainer/).
This alpha focuses on local macOS/Docker and remote Slurm/Apptainer connections.

The dashboard connects to your existing Botainer installations. It brings
project discovery, session launch, terminal access and configuration into one
interface. Botainer is installed separately and launches sessions using Docker
or Apptainer, with its own warnings and confirmation prompts.

**Status: alpha candidate.** Dashboard `0.1.0a1` targets Botainer `0.1.0a5`;
these are separate products and version numbers. The offline installer and
packaged service have isolated macOS installation coverage. The pinned
dependencies retain their original licenses plus
[embedded-component notices](third_party/README.md#offline-dependency-bundle-review).
Final Botainer alpha5 lifecycle acceptance and some session recovery cases
remain pending; this is an assisted alpha, not a qualified unattended service.
See [status](docs/status.md), [platform support](docs/setup-support-matrix.md)
and the [short alpha testing guide](docs/alpha-testing.md).

## What you can do

- Find active and inactive projects across configured machines. Search, pin
  favorites, and sort by machine or recent activity.
- Initialize a project and start a new session with the selected agent. Read
  and answer Botainer's warnings and confirmations in the launch terminal.
- Work in interactive terminals, reconnect to supported sessions, and stop
  work using the controls available for that session or cluster job.
- Browse project files, preview text, edit project configuration, and manage
  machine connections where supported.
- Optionally run installed host agents through a maintainer-prepared profile.
  These are clearly marked because they run directly on the machine, without
  container isolation.

A **project** is the folder Botainer manages; a **session** is one agent run.
Each machine connection selects a particular Botainer installation and account,
with one local Botainer connection currently supported alongside multiple
remote connections and optional host-agent profiles.

## Install and start

Use the reviewed release bundle and follow [Installation](docs/installation.md) for prerequisites, the reviewed
offline package set, installation preview and current qualification limits.
The installer uses existing normal CPython **3.13.15 or later within 3.13** on
macOS arm64 and creates a new isolated environment. Local container sessions
also need existing Docker, tmux and a Botainer interpreter running Python 3.11
or newer. It does not install or replace Botainer or those host tools.

After installation, run the installed command in a terminal on the computer
hosting the dashboard. Use its full path if it is not on your shell's `PATH`:

```sh
botainer-dashboard check
botainer-dashboard start --open
```

The first command checks local prerequisites without contacting your machines.
The second starts the dashboard service and opens it in your browser. It reuses
saved connections. On first use, open **Settings → Machines → Add machine** to
configure your first connection. Keep the service's terminal open while using
the dashboard.

To choose a port, use `botainer-dashboard start --open --port 8765`.
An occupied port causes a refusal. In another terminal,
`botainer-dashboard status` finds services recorded in the selected data
directory; `botainer-dashboard stop` stops its single verifiable active
service. With several services, select one with `--port 8765` or `--instance`
and the ID reported by status. See [startup commands](docs/start-dashboard.md).

Finish Botainer setup and launch prompts before stopping the dashboard.
Stopping disconnects browser terminals without requesting a stop of durable
sessions, but service-owned setup and launch consoles can be interrupted.
Keep session/job stop actions separate from stopping the dashboard service.

If the browser asks for a pairing code, run `botainer-dashboard pair` in another
interactive terminal. The pairing page supplies a command with the right port
when needed. Run it again to request a replacement code.

Connections and recovery records live outside the installed application, in a
private per-user data directory. `--data-dir` selects another location; use the
same location for start, status, stop and pair. See [startup commands](docs/start-dashboard.md).

For an already prepared **source development checkout**, use
`python3 tools/dashboard.py` instead of `botainer-dashboard`, from that checkout.
It retains its `.local/envs/botainer_dashboard/` environment and `.local/` state;
the installed package does not automatically import them.

Already registered Botainer projects are discovered automatically; use
**Add project** to initialize another folder. Select a project, choose the
agent for the next run, and click **New session**.

See [Getting started](docs/getting-started.md) for pairing and machine setup,
[SSH setup](docs/ssh-setup.md) for remote access, and the
[agent setup guide](docs/agent-setup-guide.md) for setup instructions you can
delegate to an agent.

## Sessions and security

A running container or Slurm job does not automatically provide an attachable
terminal. The dashboard must verify which original session it can reconnect to
and which work a Stop action would affect. Some recovery cases are not fully
tested; see [sessions and recovery](docs/sessions.md).

The service runs on your computer and listens only on its local loopback
address. Browser access must be authenticated because the dashboard can start
processes and access configured project folders. See [Security](SECURITY.md)
for the supported boundaries and reporting guidance.

## Source layout

| Path | Purpose |
| --- | --- |
| `src/botainer_dashboard/` | Python service, machine connections and session management |
| `frontend/` | Browser interface and bundled terminal assets |
| `tests/` | Portable tests with synthetic fixtures |
| `tools/` | Launch, configuration and development checks |
| `prototype/` | Offline interface simulation, separate from the real application |
| `requirements/`, `third_party/` | Exact dependency inputs, integrity and notices |
| `docs/` | Public setup, behavior and contributor documentation |

The backend is Python; the browser interface uses JavaScript and xterm.js.
The interface simulation in `prototype/` is separate from the running dashboard.

## Contributing and license

Bug reports and usability feedback are welcome; external code contributions are
paused for this limited alpha. See
[reporting policy](CONTRIBUTING.md), the [test report template](docs/alpha-testing.md#report-a-result)
and [private security reporting](SECURITY.md#report-a-security-problem).
The planned public release uses an explicit source
export that excludes private settings and trial records; see the
[publication process](docs/engineering/publication.md).
The [documentation index](docs/README.md) groups guides by task.

The application is [Apache-2.0 licensed](LICENSE); bundled libraries retain
[their own notices](third_party/README.md).
