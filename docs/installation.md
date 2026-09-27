# Install Botainer Dashboard

The alpha candidate packages the dashboard as a Python wheel with its browser
assets, help, fixed helper programs and license notices. The offline installer
previews its changes before creating a new isolated environment.

**This is an alpha candidate.** An isolated offline install on macOS arm64 with
CPython 3.13.15 passed startup, pairing, authenticated Help/assets, status and
shutdown checks, including a read-only application directory. This used a new
environment on an existing development computer; new-host setup and final
Botainer lifecycle acceptance remain separate gates. Dashboard `0.1.0a1` targets
the separately installed Botainer `0.1.0a5`. Check [status](status.md) before
choosing a route.

| Your situation | Start here |
| --- | --- |
| New recipient with the reviewed alpha bundle | Check prerequisites and preview installation below |
| Installed dashboard already present | [Start, status, stop and pairing](start-dashboard.md) |
| Prepared source development checkout | Use its existing source launcher; see the compatibility note below |
| Dashboard opens but a machine is missing | **Settings → Machines**; [Getting started](getting-started.md) |

## Prerequisites and release inputs

The first artifact set targets **macOS arm64 and normal CPython 3.13.15 or later
within 3.13**. Free-threaded Python builds are not included. Use an existing
compatible Python. The installer does not download Python, Conda, Botainer,
agents, container images, SSH or runtime tools. If a prerequisite is missing,
choose and approve its installation separately. Conda is not required for this
route. The current wheelhouse is not a Linux dependency bundle; Linux fresh
installation remains unqualified.

Check the prerequisites for the route you intend to try:

| Route | Required before setup |
| --- | --- |
| Open the dashboard | Compatible Python with its bundled `ensurepip`, the matching offline bundle, and an existing browser. Chrome has controlled UI coverage; Safari/Firefox acceptance is pending. |
| Local Botainer container sessions | Existing Botainer alpha5, its own Python **3.11 or newer**, a working local Docker engine and Docker CLI, **tmux on this computer**, prepared images and the intended agent's Botainer sign-in. |
| Remote cluster preview | Working system SSH plus a separately qualified Botainer/Slurm/Apptainer installation and compute-node Screen policy. See [cluster prerequisites](ssh-setup.md#before-starting-a-cluster-session); SSH success alone is insufficient. |
| Optional host agents | Existing supported agent executables, local tmux and a maintainer-prepared host profile. No container isolation; not part of the first-run container path. |

The dashboard's Python and Botainer's Python are separate choices. Botainer may
itself support older Python versions; this dashboard's installation probe requires
3.11 or newer for the selected Botainer interpreter. Do not replace a working
Botainer environment just to satisfy a check without reviewing that change.

For a local container trial, the project's `nudge` plugin must be disabled:
the dashboard uses its own durable tmux owner and does not yet support nesting
Botainer's Screen wrapper inside it. Review **Project config** before launch;
do not change settings of existing work merely to try the dashboard. Qualified
cluster sessions have the opposite requirement: they need Botainer's Screen
owner through `nudge`. See [project setup](getting-started.md#3-find-a-project-or-add-a-folder)
and [cluster prerequisites](ssh-setup.md#before-starting-a-cluster-session).

If the dashboard Python is missing, the [official Python 3.13.15 release page](https://www.python.org/downloads/release/python-31315/)
provides a macOS installer. Choosing or installing it is a separate step. This
dashboard has not qualified every Python distributor's framework, shim or
environment layout; the installer's read-only plan checks the selected one.

Obtain the reviewed dashboard wheel, its matching offline wheelhouse and the
installer/source bundle from the same release. The wheelhouse must contain the
exact 14 dependency wheels selected for this platform. The installer uses the
bundle's dependency manifests and hashes to verify the 14 dependency artifacts.
It also checks the dashboard wheel's contents and reports its SHA-256. Compare
that hash with the separately reviewed release checksum or build receipt before
applying; the first plan cannot authenticate a wheel's publisher by itself. Do not substitute arbitrary wheels, allow source builds or
fill gaps with an online pip command. See the [dependency inventory](../requirements/README.md)
and [update process](dependencies.md) for package provenance.

An existing browser is sufficient for normal use; Node/npm and a frontend build
are not needed. Container sessions additionally need a working Botainer
installation and its runtime on the chosen machine. Local host agents are a
separate optional feature and have no container isolation.

### Check the existing local tools

In your normal terminal, inspect the tools already installed:

```sh
python3.13 --version
command -v botainer
command -v docker
command -v tmux
tmux -V
docker context ls
docker context inspect
```

These commands do not install tools or start sessions. Use your usual Botainer
command if it has another name. If it is a shell alias, the machine wizard needs
the actual launcher and Python paths behind it; an alias is not an executable.
The wizard verifies tool files, so a `tmux -V` result alone does not qualify a
different version. Missing tools need a separately reviewed installation.

In `docker context inspect`, find `Endpoints` → `docker` → `Host` for the context
you use with Botainer. Copy that **local `unix://…` endpoint** into the wizard's
**Installation paths and advanced settings → Docker endpoint** field. Review any
`DOCKER_HOST` or `DOCKER_CONTEXT` overrides your normal command uses. The wizard
does not automatically select the Docker CLI's current context; its fallback is
`unix:///var/run/docker.sock`. It checks the explicitly selected daemon before
saving. TCP/SSH Docker endpoints are outside this local route. [Docker context reference](https://docs.docker.com/engine/manage-resources/contexts/).

Docker Desktop can work without the optional `/var/run/docker.sock` symlink,
because its CLI uses the configured context. Select the existing endpoint; no
new socket symlink, `sudo` command or Docker settings change is needed merely
to identify it. [Docker Desktop socket guidance](https://docs.docker.com/desktop/setup/install/mac-permission-requirements/).

## Preview, then install into a new environment

From the extracted installer/source bundle, run its installer using an existing
normal CPython meeting that requirement. Replace the wheel and wheelhouse paths
with the supplied files. Choose a new destination whose parent already exists,
belongs to you, has no symlinked path components, and is not writable by other
users or groups:

```sh
python3.13 -I -B tools/install.py \
  --wheel release/botainer_dashboard-0.1.0a1-py3-none-any.whl \
  --wheelhouse release/wheelhouse \
  --destination "$HOME/botainer-dashboard-0.1.0a1"
```

Without `--apply`, this produces a **read-only JSON plan**. It does not create an
environment, run a subprocess, import the candidate package or download anything.
Review the interpreter, platform, destination, exact package list and hashes.
The destination must not already exist, and its name must include the version.
Stop here if anything is unexpected.

When the plan is correct and installation is approved, run the **exact apply
command printed in the plan**. It adds both `--apply` and the required
`--wheel-sha256` value, binding the installation to the dashboard artifact you
reviewed. Do not omit the hash or reuse an old plan after replacing the wheel.

Apply creates a venv, bootstraps the pip bundled with the selected existing
Python, then installs the reviewed local wheels with hash checking, without
dependency resolution or source builds. It retains an install
receipt and exact inputs under `.dashboard-install/` inside the new environment.
It does not install Botainer, alter your shell's PATH, import private profiles or
start the dashboard. A Python environment separates library versions; it is not
an OS sandbox.

Use the resulting executable directly, without activating an environment:

```sh
"$HOME/botainer-dashboard-0.1.0a1/bin/botainer-dashboard" check
"$HOME/botainer-dashboard-0.1.0a1/bin/botainer-dashboard" start --open
```

The other guides shorten this executable to `botainer-dashboard`. Use its full
path unless you have deliberately added that environment's `bin` directory to
your shell's `PATH`. No shell configuration edit is required to try it.

`check` inspects local startup requirements and saved configuration; it does not
contact Docker/SSH or establish workload readiness. `start --open` starts a
foreground loopback service and opens its browser window. Keep that terminal
open. Continue with [Getting started](getting-started.md) to pair the browser,
add machines and find projects.

## Application files and private data

The environment contains code and packaged resources. Private connections,
pairing and recovery records are stored separately:

| Service host | Default installed data directory |
| --- | --- |
| macOS | `~/Library/Application Support/Botainer Dashboard` |
| Linux | `$XDG_STATE_HOME/botainer-dashboard`, or `~/.local/state/botainer-dashboard` when unset; Linux installation is not yet qualified |

The saved selection is `connections/selection.json` under that directory.
Use `--data-dir /absolute/private/folder` to select a different location, and
include it consistently in `check`, `start`, `status`, `stop` and `pair` commands.
The directory must be outside the installed application, private to the user,
and must not use symlinked path components. Keep application, Botainer state
and dashboard data/control directories separate from actual container project
folders: a project must not contain those trusted files, or live inside them.
An allowed parent can still contain separate project and control folders. Keep
data backups private.

An installed dashboard does not automatically migrate a source checkout's
`.local/` state or import another installation's profiles. Pointing at a new
empty data directory gives a new setup, not a copy of existing ownership records.
Use an explicit reviewed migration plan before moving existing data.

## Upgrade, rollback and removal

Install each approved update into a **new versioned environment** using its
matching reviewed artifacts. Do not update the old environment in place. Keep
the old environment and data backup while any surviving terminal owners still
depend on them; removing their Python/helpers can break recovery.

Finish pending Botainer setup/launch prompts, stop the dashboard service and wait
for confirmed shutdown. Start the new executable with the **same data directory**
and intended port. Retain the previous environment for rollback. An application
upgrade does not authorize changes to Botainer, profiles or data formats; there
is no automatic data migration. Do not run incompatible versions concurrently
against shared state or assume rollback is safe after an undocumented migration.

Live-owner upgrade/recovery remains a separate acceptance gate. If it is not
qualified for the release, schedule the upgrade after the affected sessions
finish. A successful package install alone is not that qualification.

To remove an unused environment, first verify that its service and dependent
owners have ended. Removing that environment should leave the private data
directory, Botainer installations and project folders intact. Deleting dashboard
data is a separate deliberate action: recovery records may still be needed.

## Existing source checkout and later pipx support

An already prepared development checkout keeps its original
`.local/envs/botainer_dashboard/bin/python` runtime and `.local/` state. Run
`python3 tools/dashboard.py check` and `python3 tools/dashboard.py start --open`
from that source folder. Downloading source alone does not create the runtime.
Do not copy the development environment to another computer.

The installed `botainer-dashboard` entry point is also the intended basis for
future pipx installation. Pipx distribution and upgrade acceptance remain
separate work; do not use an unreviewed online installation as a shortcut around
the exact offline artifacts. Botainer may use its own pipx environment; it is
not installed into the dashboard's environment.
