# Architecture

The dashboard is a loopback web application with a Python service and a browser
terminal. It connects selected existing Botainer installations into one view.
Botainer remains responsible for project configuration, launch policy, native
preflight and the container or scheduler lifecycle. The dashboard does not
install Botainer or replace its consent prompts.

## Layers and boundaries

| Layer | Main files | Responsibility |
| --- | --- | --- |
| Browser | `frontend/` | Project navigation, setup, configuration editing and retained terminal views |
| HTTP/WebSocket service | `service.py`, `access.py`, `pairing.py`, route modules | Authentication, origin checks, typed requests and bounded terminal transport |
| Local pairing control | `pairing_control.py`, `tools/dashboard.py` | Owner-only request for a usable browser code from one explicitly selected running service |
| Local service control | `service_control.py`, `launcher.py`, `tools/dashboard.py` | Exact-instance status and graceful service shutdown through a separate private mailbox |
| Selection and inventory | `connections.py`, `connection_prepare.py`, `inventory.py`, `combined_backend.py` | Saved installation identities, discovery, per-installation state and capability routing |
| Installed local controller | `ordinary_local.py`, `ordinary_owner.py` | Existing Botainer projects, durable local owners and exact runtime actions |
| Installed remote controller | `remote_backend.py`, `remote_profile.py`, `cluster_attach.py` | Fixed operations over SSH, scheduler/session identity and framed attachments |
| Optional host controller | `host_backend.py` | Explicitly selected native agents without container isolation |
| Files and configuration | `workspace.py`, `config.py` | Bounded paths, revision checks and separation of dashboard settings from project configuration |

Python modules in the table live under `src/botainer_dashboard/`. Entrypoints
and fixed helper programs live under `tools/`. The
[frontend guide](../frontend/README.md) describes renderer ownership and browser
state. [Dependency inputs](dependencies.md) describe the runtime libraries.

## Commands and ownership

Browser requests select operations and opaque target identities. They do not
supply a raw shell command or arbitrary argument list. Connection preparation
does accept an interpreter path and executes a fixed probe through it; this is
a trusted operator action, not a sandbox. Controllers validate installation/profile
identity and use fixed argument construction. Project labels, terminal output
and filesystem content are untrusted data. The
[entry-point inventory](engineering/security-entry-points.md) enumerates the
control channels and distinguishes native CLI calls from compatibility code.

Ordinary local operations use `ordinary_local_helper.py`; its compatibility
bridge preserves terminal ownership while running Botainer's native CLI. The
separate `ordinary_hook_helper.py` handles a narrowly recognized broker startup
boundary. `workspace_botainer_helper.py` runs configuration validation using the
selected Botainer installation. These are dashboard compatibility helpers,
not a stable upstream API or a general command execution service.

Remote operations use `remote_botainer_helper.py` over SSH. The helper retains
native CLI launch and records the scheduler submission boundary. The
`cluster_attach_supervisor.py` helper owns the terminal attachment client; it
must not take ownership of the durable Screen server. Attachment capability
requires the expected session, allocation, executable and owner identities.
Shared SSH transport lives in `ssh_transport.py`; console and lifecycle locks
live in `launch_console.py`. Installed controllers do not depend on the
development proof backends or the cluster trial command.

A browser view, its WebSocket, the CLI owner and the container/scheduler job have
different lifetimes. Closing a view or signing out requests viewer disconnection;
Stop is a separate action against a selected runtime. An uncertain cleanup or
unverified owner can disable controls even when inventory still reports work
running. Reconnect must never silently start a replacement agent or replay input.
See [status](status.md) for recovery limitations and qualification gaps.

The dashboard service has its own lifetime, separate from project sessions.
`botainer-dashboard start` runs it in the foreground; `status` and `stop` consult
only the selected private data directory's service records. A prepared source
checkout uses `python3 tools/dashboard.py` and defaults to its `.local/` state.
Each run has a new identity and a lifetime lock held until the launcher finishes
closing its server. Fresh
nonce-bound replies distinguish readiness from mere record existence. A stop
acknowledgement means shutdown was requested; owner-lock release confirms it.
No PID or listening-port owner is killed. A CLI stop request rejects subsequent
POST requests and terminal bindings, then disconnects browser views without
calling a backend's Stop/Cancel operation. Requests already admitted can still
dispatch during shutdown; their outcome may be uncertain.
Service-owned initialization or launch-prompt terminals can still be interrupted.

## Botainer compatibility

The alpha targets Botainer `0.1.0a5`. A source installation and a packaged
installation are two supported selection formats, not two different Botainer
implementations. The dashboard does not need a private fork simply because an
installation uses a source checkout. It does, however, still depend on several
private Python interfaces in the selected Botainer version. The
[entry-point inventory](engineering/security-entry-points.md#where-operation-execution-differs-from-plain-cli)
describes those dependencies and the safeguards they provide.

Keep three checks separate:

- **Installation identity:** paths, exact file hashes, state folder and approved
  plugins identify which code the operator selected. Matching hashes establish
  unchanged bytes; they do not establish support for a new Botainer version.
- **Operation compatibility:** a controller must recognize the native data and
  call shapes it uses. Launch, original-owner attach and exact Stop need their
  own tests. Successful inventory is not proof that lifecycle operations work.
- **Live qualification:** the tested dashboard/Botainer pair, runtime and site
  determine the support claim. [Status](status.md) records the remaining gates.

Updating Botainer source or a wheel may leave older plugins installed in its
state folder. Those copies are separate executable inputs and can change
behavior even when the source version is current. The dashboard checks the
selected copies and does not overwrite or silently approve replacements.
Use [the maintenance procedure](maintenance.md#update-botainer-or-the-dashboard)
to review an installation change.

Future versions should use a small, explicit compatibility adapter around
documented native operations. Prefer a versioned Botainer capability contract
over guessing support from its version string. The desired native boundary
includes inventory with stable identities, nonmutating draft validation,
durable launch receipts, strict original-session attachment, and exact Stop
with cleanup outcomes. This is a direction for development, not an API already
provided by this alpha.

Retire an existing substitution only when its native replacement preserves
warnings and consent, single dispatch, owner identity and cleanup behavior in
normal and interrupted runs. Unsupported operations should explain the missing
capability and remain unavailable; they must not start a replacement agent or
weaken verification. No claim of compatibility with all newer versions follows
from accepting an updated file hash.

## Security model

The service binds loopback and validates Host and Origin. Authenticated requests
require both an HttpOnly cookie and an independent origin-scoped bearer.
Credentials are not put in URLs. A target-bound writer lease prevents the
browser from choosing a different runtime through display text or stale state.
Application scripts and terminal assets come from the dashboard's own source
and bundled assets, never a project or container.

The owner requests a pairing code with `botainer-dashboard pair`, selecting an
exact service when more than one is running. The startup output and pairing
page supply the command; explicit access-file selection remains available for
compatibility. This uses a bounded file mailbox in that service's private
run directory, with ownership locks, a request nonce and a short timeout. It
provides no network endpoint or runtime command dispatch. Only the running
service updates its pairing store, on the same event loop as browser pairing;
the CLI never opens a competing store. A failed local mailbox disables code
requests without intentionally ending browser grants or sessions.

Service control uses independent lock and mailbox files. Its lifetime lock stays
held even if its polling fails, so a control failure cannot claim the server
stopped. Status neither loads runtime profiles nor queries Docker/SSH. Ambiguous
records and multiple services require an exact selector; old services without
this protocol require their original foreground terminal. Both owner mailboxes
trust the operating-system account; neither is exposed as an HTTP route.

Remote connections use the operator's configured SSH identity and host-key
checks. They are not a public remote-control endpoint. Host-agent profiles are
explicitly distinct from container profiles: an agent running on the host has
the account's host access and is not sandboxed by the dashboard.

## Development-only paths

The source checkout retains restricted proof/workspace backends for controlled
lifecycle tests. The installed wheel excludes these backends and the trial
transport command; its CLI does not advertise development launch modes.
Related source-only trial
helpers include `shell_botainer_helper.py`,
`native_cli_botainer_helper.py` and `cluster_workspace_helper.py`. Their prepared
state, special restrictions and deterministic children do not establish ordinary
agent or general cluster support. Do not present those modes as the default
recipient setup.

`prototype/` is a synthetic UI simulation. It exercises navigation/model choices
without controlling real sessions. The served application is `frontend/`, with
an authenticated backend selected by the launcher.
