# Entry points and security boundaries

The dashboard adds security-sensitive code beyond running Botainer in a normal
terminal. It is an operator control application, not a read-only viewer. Native
Botainer warnings still appear during ordinary launches, but several operations
also use dashboard helpers or temporarily wrap Botainer's Python internals.

This inventory describes implemented source, not proof of what a particular
running service has loaded. Selected profiles determine which controllers are
active. It is not a penetration-test report or a claim that the remaining
[release gates](../status.md) have passed.

## Who is trusted

The service runs with its operating-system account's authority, including its
configured Docker and SSH access. Every paired browser is an operator for all
loaded connections. There is no read-only account, per-project user permission,
or second approval credential for dangerous actions. GUI confirmations help an
operator choose correctly; they do not restrict an authenticated API client.

Application source, runtime profiles, selected interpreters/executables, installed
Botainer/plugins and SSH configuration must be trusted. Project contents, labels,
terminal output and remote observations are data to validate, not application
code or permission to control another target.

Loopback and owner-only files reduce access from other websites, networks and OS
accounts. They do not isolate the dashboard from a malicious same-account
process, a compromised browser profile/extension, or injected dashboard-origin
JavaScript. In particular, a native host agent can access files and credentials
beyond its project, potentially including dashboard control state. Project-root
restrictions are not a host-agent sandbox.

## Additional surfaces

| Surface | What it can do | Protection and remaining concern |
| --- | --- | --- |
| Browser HTTP API | Observe projects; create projects/sessions; stop work; edit configuration and machine selections | Loopback, exact Host/Origin checks and paired-browser credentials. A valid pairing grants powerful operator access. |
| WebSocket terminal | Send input, resize and receive output from a verified session | Cookie plus bearer, exact target binding, one writer, bounded frames/output and cleanup fencing. Terminal input can run commands with the target's authority. |
| Browser renderers and clipboard | Parse terminal escapes and help Markdown; copy/paste selected text | Fixed application assets, CSP, text/DOM rendering, no output-triggered URL or clipboard actions. xterm/browser parsing remains an attack surface; pasting is executable input to the target. |
| Machine setup and profiles | Probe a selected installation; select executable, source, state, roots and SSH identity | Fixed probe arguments and bounded results. **The selected interpreter is executed**, locally or over SSH; path checks and a “read-only” acknowledgement do not make an untrusted executable safe. Saved profile changes generally require restart to activate. |
| Host-agent executable review | Inspect and save an installed replacement for an existing Claude/Codex selection | Read-only canonical-path/hash inspection, explicit review, revision comparison and recheck at save. No executable runs during review. Immutable revisions must match a retained original profile in every field except existing agent paths/hashes; only then can projects, owners and browser grants retain their identity. A hash identifies bytes, not trustworthiness. |
| Botainer installation review | Inspect already-installed replacement code and save a pending connection revision | Executes the acknowledged selected interpreter using the same bounded setup probe. Save repeats inspection and compares the exact reviewed candidate, old profile digest and registry revision. Saved account/SSH, state, roots, environment, Docker and terminal-owner settings cannot change through this flow. Explicit local hook approvals and selected state-plugin pins are not expanded. Remote source updates include bundled plugin code. No installation, automatic activation, receipt migration or grant reuse is performed. |
| Project/configuration files | Create/register folders, read files, validate and save config | Existing Botainer projects require their selected native registry's exact UUID/path association and verified directory identity; setup destinations remain configured separately. Reads stay bounded to each project with no-follow checks and revision checks. Validation and saving are dashboard operations; config acceptance is not full launch preflight. |
| Local terminal ownership | Keep a CLI/host agent alive independently of browser views; attach/capture/close an exact owner | Private tmux sockets/config/records, executable and owner checks, writer locks. These are additional local control channels. Same-account code is not excluded by file permissions. |
| Botainer compatibility helpers | Invoke native commands while adding identity checks, receipts and lifecycle handling | Dedicated helper processes, pinned selected source and scoped substitutions. Private Botainer internals remain a compatibility and security-review burden. |
| Remote helper and attachment protocol | Run fixed operations over SSH; inspect Slurm; attach to the original Screen owner | SSH authentication/host-key policy, bounded messages, verified helper/source/target identities and viewer leases. No new cluster listener, but extra remotely executed code and a terminal protocol still exist. |
| Owner pairing mailbox | Obtain a usable one-time browser pairing code from one selected service | Private files, live lock identity, nonce, expiry and bounded messages; no network route. The local `pair` command requires an interactive terminal, refuses ambiguous services and binds the request to the selected service identity. Same-account access remains trusted. |
| Owner service mailbox | Inspect or gracefully stop one dashboard service, without backend Stop/Cancel | Separate private files, lifetime lock identity, nonce and bounded replies; no PID signals or HTTP endpoint. Same-account callers are trusted; unfinished launch/setup prompts can be interrupted. |

## Complete browser route inventory

There are 33 route declarations: 32 HTTP routes and one WebSocket route. Some
routes return unavailable when the selected backend lacks that capability.
There is no health, OpenAPI, ReDoc or automatic API-documentation route.

All requests require the exact loopback Host; forwarded headers, duplicate
security headers and query strings are rejected. The public HTTP entries are:

| Method and path | Purpose |
| --- | --- |
| `GET /` | Pairing page, or application shell when its cookie is valid. Shell retrieval does not authorize the API. |
| `GET /unlock` | Pairing page. |
| `POST /auth` | Exchange a short-lived, single-use code for browser credentials; exact Origin and bounded form required. |
| `GET /vendor/{relative:path}` | JS/CSS inside the selected vendor directory, with canonical containment checks; not project files. |
| `GET /{relative:path}` | Only nine fixed frontend asset names: `auth.js`, `login.js`, `app.js`, `app.css`, `xterm-adapter.js`, `terminal-registry.js`, `connections.js`, `desktop-layout.js`, `workbench-state.js`. |

The following 27 HTTP routes require both an HttpOnly cookie and an independent
bearer. POST additionally requires exact Origin. GET can omit Origin, but a
supplied wrong Origin is rejected. Using POST for a read does not weaken these
checks.

| Method and path | Effect |
| --- | --- |
| `POST /logout` | Revoke this browser and disconnect its terminal views; does not request session stop. |
| `GET /api/state` | Inventory and current observations. |
| `GET /api/workspace` | Configured roots, capabilities and workspace metadata. |
| `POST /api/projects` | Create, open or register a folder under the selected controller's rules; may open native Botainer init prompts. |
| `POST /api/projects/{project_id}/open-host` | Register a known local project folder in a selected host profile; no agent launch by itself. |
| `POST /api/projects/{project_id}/sessions` | Start a session, with a supported optional agent override. |
| `POST /api/sessions/{runtime_id}/stop` | Stop the verified target, or explicitly request supported orphan-container recovery. |
| `POST /api/sessions/{runtime_id}/history` | Read bounded retained terminal output. |
| `POST /api/projects/{project_id}/files` | List bounded project-directory contents. |
| `POST /api/projects/{project_id}/file` | Read a bounded project text file. |
| `GET /api/projects/{project_id}/config` | Read project config and revision. |
| `POST /api/projects/{project_id}/config/validate` | Validate candidate text with the selected backend. |
| `POST /api/projects/{project_id}/config/save` | Validate and save project config with revision and lifecycle checks. |
| `GET /api/dashboard/config` | Read dashboard registration config. |
| `POST /api/dashboard/config/validate` | Validate dashboard registration text. |
| `POST /api/dashboard/config/save` | Save dashboard registration text with a revision check; activation depends on launch mode/restart. |
| `GET /api/connections` | Saved/loaded machine profiles and pending changes. |
| `GET /api/connections/help/{guide}` | One of twelve fixed, bounded Markdown guides from dashboard source. |
| `POST /api/connections/prepare` | Execute the selected installation probe locally or over SSH. |
| `POST /api/connections` | Save/import a reviewed machine profile and selection. |
| `POST /api/connections/{connection_id}/enabled` | Change saved enabled state with a revision check. |
| `POST /api/connections/{connection_id}/remove` | Remove from saved selection with a revision check; does not stop sessions. |
| `POST /api/connections/{connection_id}/agent-update/prepare` | Read one existing host agent's proposed executable path/hash. Empty path searches only the fixed agent name in the profile's PATH; no command is executed. |
| `POST /api/connections/{connection_id}/agent-update` | Save the exact reviewed path/hash after revision and file checks; retain original profile, state and authorization identity. Activation requires service restart; existing terminal owners are unchanged. |
| `GET /api/connections/{connection_id}/installation-update` | Read software path settings from the saved local/remote profile, even when its previous installation is missing. No executable runs. |
| `POST /api/connections/{connection_id}/installation-update/prepare` | Inspect an explicitly acknowledged existing Botainer update using saved connection scope. Returns a review and candidate digest; saves nothing. |
| `POST /api/connections/{connection_id}/installation-update` | Repeat the acknowledged inspection and save only the exact confirmed candidate using revision comparison. Retain the old immutable profile; require deliberate restart and fresh pairing. Remote history stays under its previous revision rather than being relabeled as current authority. |

`WS /api/sessions/{runtime_id}/terminal` requires the cookie, bearer in the
WebSocket subprotocol and exact Origin. The initial message binds the namespace,
runtime and terminal dimensions before attachment. Messages allow bounded input,
resize and flow-control acknowledgement, not arbitrary backend method calls.
No credentials are placed in URLs. Credentials and target identity are checked
during transport; ambiguous attachment/cleanup must not grant a second writer.

The bearer is stored in origin-scoped browser storage. The cookie and bearer
defend different request paths; they are not two-factor authentication and do
not protect against malicious code already running in the dashboard origin.

Implementations: [service](../../src/botainer_dashboard/service.py),
[access](../../src/botainer_dashboard/access.py),
[workspace routes](../../src/botainer_dashboard/workspace_routes.py),
[connection routes](../../src/botainer_dashboard/connection_routes.py),
[connection probe](../../src/botainer_dashboard/connection_prepare.py).

Setup now applies the same selected-package ownership, mode and executable-layout
checks used by runtime helpers. This is still not attestation of the interpreter,
dependencies, ACLs or same-account writers. Checks do not change permissions.
The native terminal-owner tool keeps its separate canonical executable, stable
hash and private control-state checks; Python import rules are not applied to
its parent folders. During a Botainer update review, observed Docker and terminal
tool pins must match the saved selection before the Docker identity command runs.
Remote helper diagnostics use a small, versioned envelope containing only fixed
codes. They are advisory explanations, never launch receipts, proof of job exit
or permission to retry work. If the interpreter cannot start, no such helper
diagnostic can be produced; the dashboard does not try an untrusted fallback.

## Where operation execution differs from plain CLI

These are current installed-profile paths. Calling a native CLI entry function
inside a modified Python process is not the same as executing an unmodified
`botainer ...` command. No substitution below is made safe merely by being
temporary or by leaving installed source files unchanged.

| Operation | Current execution | What should eventually change |
| --- | --- | --- |
| Local inventory | Dashboard helper imports Botainer state modules and calls Docker inspection commands directly. | Prefer supported structured inventory with exact runtime identities. |
| Local init/start | Native CLI supplies prompts, but the helper wraps composition/launch, Docker argument rendering and reviewed hook execution. It records dispatch identity; the retained runtime paths include a cached-image/no-pull restriction. | Native operation receipts, lifecycle coordination and explicit image policy should replace substitutions after parity tests. |
| Local hook/broker startup | A separate helper verifies approved hooks and bootstraps their Python children through the selected source; exact broker spawning is temporarily wrapped. | Supported interpreter/source isolation for native hooks and descendants. |
| Local normal Stop | Native Stop is called with a wrapped private runtime-stop function and an exact container ID. Duplicate post-session cleanup in the Stop process is suppressed while the original CLI owns cleanup. | Native exact-target Stop and one authoritative cleanup owner. Removing suppression prematurely can duplicate cleanup. |
| Local orphan-container Stop | Separate recovery directly stops the independently verified Docker container after the original CLI owner ended. Helper cleanup remains unconfirmed. | Explicit native recovery with truthful partial outcomes; this is not normal Stop parity. |
| Local terminal reconnect/history | Dashboard-owned tmux provides attach and capture for the original CLI. External terminal owners are not adopted. | A documented foreground-owner contract may retain this transport; an upstream persistent-owner interface could replace it. |
| Remote inventory/init/Stop | Native `list --json`, init and `hpc stop` are used, plus private session-record reads and direct Slurm observations. Stop acknowledgement is followed by scheduler observation. | Supported inventory/identity/result contracts instead of private record assumptions. |
| Remote launch | Native HPC CLI supplies prompts, but the helper intercepts recognized Python subprocess boundaries and private submit/plan functions to journal one `sbatch` dispatch. | Native durable operation/session/job receipts, allowing the subprocess chain to run unmodified. |
| Remote reconnect | Fixed `srun` enters the verified allocation; a dashboard helper verifies the original Screen owner and uses a framed, leased `screen -r` viewer. It bypasses native `hpc attach` rather than permitting a fresh-agent fallback. | A strict native original-session attach contract; never infer ownership from a running job alone. |
| Local/remote config validation/save | A helper stages draft text, calls Botainer's loader and config check, and temporarily binds its private project-root lookup to that draft. Dashboard code performs revision-checked writes. | A supported nonmutating draft-validation interface with policy/plugin context. Full launch consent remains native. |
| Host agent launch/stop | Launch executes the selected Claude/Codex through a fixed helper in private tmux. Stop terminates the verified tmux pane; descendants may survive. No Botainer container or launch policy. | Keep explicitly separate and opt-in. This is a host capability, not a Botainer compatibility path. |

The local container controller refuses project folders that contain, equal or
sit inside selected dashboard/Botainer code, state, private control folders or
other loaded local profile files. It checks the actual new-project destination,
not its broad parent, so ordinary sibling projects remain usable. Existing
registered projects need no arbitrary folder allowlist. The same boundary is
checked again before native dispatch and for user-supplied extra mounts,
including read-only mounts. Native core/plugin mounts remain subject to
Botainer's policy and disclosures. These checks do not isolate host agents or
defend against a malicious process already running as the operator account.

Remote launch checks the supported Screen binary/system configuration on the
login host before submission. Compute attachment repeats its own check; a passed
login check cannot qualify a different compute node. An unsupported site is
refused with instructions before submission when detectable on the login host.

The local helper also retains an older detached-Docker branch; its existence
does not mean the durable foreground route uses it. Development proof/workspace
modes have separate restrictions and must not stand in for installed-runtime
acceptance.

Normal local Stop verifies the immutable container ID across names after native
Stop returns. A zero CLI exit code or disappearance of the old name is not proof
of termination. Unconfirmed results keep the terminal viewer available and show
bounded native diagnostics as plain text. Original foreground cleanup is a
separate outcome; stopping the container does not confirm every helper exited.

Selected-package imports use a fresh private cache namespace because Python's
`-B` alone still reads existing bytecode. The local helper, approved Python-hook
and broker helper, remote fixed helper, and known validator/remote CLI children
apply this policy. The Botainer package module set is checked and unsupported
extension/legacy bytecode shadows are refused. Source and executable paths get
POSIX owner/write checks. Startup customization, dependencies, arbitrary native
plugin subprocesses, ACLs and same-account mutation remain trusted prerequisites;
this is not complete executable attestation or a new sandbox. Existing installed
caches are left intact.

The selected, hash-pinned macOS Docker Desktop CLI has one mode-bit exception:
its root-owned `/Applications` ancestor may be writable by the `admin` group,
but not by everyone. The exact standard Docker CLI path is required, and the
executable and every inner directory retain strict checks. Python and plugin
inputs cannot use this exception. Platform administrators are trusted to manage
that native application; this is not protection from an administrator replacing it.

Routine remote diagnostics retain at most 64 metadata entries, 64 KiB and seven
days, pruned on the next observation. They exclude paths, content bodies and
free-form stderr. Successful file/config reads therefore do not create retained
content snapshots. Completed, successful, recognized legacy read captures are
migrated conservatively; failed, malformed, incomplete, unknown or mutation
records remain for manual review. A diagnostic write failure leaves the read
result usable and reports diagnostic history as unavailable. Mutation intent
must still be recorded before dispatch, and unresolved operation evidence is
not automatically pruned. Repeated reconciliation polls keep fixed first/latest
valid receipt snapshots per request; failed or malformed polls cannot replace
those snapshots. Original mutation records and legacy receipt captures remain
intact. This bounds repeated polling, not lifetime storage for new operations.
Successful config-save receipts omit returned text;
private project recovery separately retains the known base and draft as
described in [configuration](../configuration.md#project-config-and-save-state).

Project replacement includes a final revision read and bounded private recovery,
but arbitrary external writers can still race the read/replace interval. A
recovery copy cannot recover an unseen write. Use one config editor at a time
until a native participating-writer update contract is available.

Source owners: [ordinary local helper](../../tools/ordinary_local_helper.py),
[hook helper](../../tools/ordinary_hook_helper.py),
[local terminal owner](../../src/botainer_dashboard/ordinary_owner.py),
[remote helper](../../tools/remote_botainer_helper.py),
[attachment supervisor](../../tools/cluster_attach_supervisor.py),
[config validator](../../tools/workspace_botainer_helper.py),
[workspace files](../../src/botainer_dashboard/workspace.py),
[host controller](../../src/botainer_dashboard/host_backend.py) and
[host executable helper](../../tools/host_agent_helper.py).

## Non-browser entry points

- [Dashboard CLI](../../src/botainer_dashboard/cli.py): installed
  `botainer-dashboard` command, with the compatible
  [source wrapper](../../tools/dashboard.py). Foreground `start` and `check` (older
  flag-only startup and `--check` remain supported), explicit
  local/remote/host profiles, saved connections and `--setup-only`. The lower
  source launcher also exposes restricted proof/workspace modes; the installed
  wheel excludes their backends, hides their flags and refuses them. These execute as the
  invoking account; they are not remote API commands. `--check` checks local
  prerequisites and does not qualify live Docker/SSH/agent behavior.
- `status` and `stop`, optionally selecting `--port` or an exact `--instance`:
  [service control](../../src/botainer_dashboard/service_control.py) reads this
  selected data directory's private run records without loading backend profiles or probing
  Docker/SSH. Only fixed status/shutdown requests are accepted. No raw command,
  backend action, PID signal or port takeover is available. A fresh nonce-bound
  response reports starting/running/stopping; only original lifetime-lock release
  confirms shutdown. Stale, ambiguous, failed or absent control does not authorize
  a force-stop. The launcher retains this lock through cleanup even when mailbox
  polling fails, independently of pairing. A CLI stop request rejects subsequent
  POST requests and terminal bindings before server draining. HTTP requests
  already admitted may still dispatch after acknowledgement, and an operation
  may finish or remain uncertain; launch requests are never automatically retried.
- [Offline installer](../../tools/install.py): the default plan reads the
  selected wheel and exact dependency inventory. Explicit `--apply` creates a
  new versioned virtual environment, uses that Python's bundled pip, and installs
  only the reviewed wheel bytes with no index, dependency resolution or source
  builds. It refuses an existing destination. It does not install Botainer or
  start the service. The interpreter, installer and selected release artifacts
  are trusted executable inputs; hashes alone do not establish publisher trust.
- [Application layout](../../src/botainer_dashboard/layout.py): packaged code,
  help and fixed helpers are separate from private writable operating data.
  `--data-dir` selects state, never code. Package inventory checks detect missing,
  extra or changed files; they do not resist an account owner replacing both
  inventory and code. Fixed native helpers explicitly import the dashboard
  modules from their own application installation while using the selected
  Botainer interpreter. They never search a project or state directory for code.
  Keep old application environments while their session owners are live; an
  ordinary virtual environment is dependency isolation, not a security sandbox.
  Ctrl+C uses the server's normal foreground shutdown path.
- `--pairing-code ACCESS_FILE`: a separate owner command using the exact private
  run directory's `access.json`, request/response files and lock files. The
  [mailbox](../../src/botainer_dashboard/pairing_control.py) only requests a code,
  never runtime actions. Output requires an interactive terminal. File safety,
  live-owner identity, nonce and expiry are checked; this is not a same-account
  security boundary.
- Private tmux Unix sockets, writer locks, launch receipts and owner records:
  used by the local CLI and host controllers. They need private directory/file
  permissions plus identity checks, not just a predictable session name.
- SSH stdin/helper requests and the remote viewer frame protocol: used by the
  installed remote controller. The dashboard publishes a hash-named private
  helper and verifies it before use. No dashboard HTTP service is installed on
  the cluster. SSH configuration itself can select executable proxy/helper
  programs and must be reviewed independently.
- [Connection preparation CLI](../../tools/prepare_connection.py) and the
  internal fixed helper programs: local command entry points for the same
  trusted setup/controller operations. They are not independent network APIs.
  The simulation and development trial tools are separate from production.

## Review and test obligations

Existing synthetic tests cover Host/Origin/authentication, pairing replay,
malformed bodies, exact target selection, stale revisions, traversal/symlinks,
owner changes, writer fencing, fixed probe arguments and hostile rendered text.
Local integration tests additionally exercise deterministic PTYs and loopback
HTTP/WebSocket behavior. Relevant suites include `test_access.py`,
`test_connection_routes.py`, `test_connection_prepare.py`, `test_workspace.py`,
`test_ordinary_owner.py`, `test_remote_backend.py`, `test_host_backend.py`,
`test_pairing_control.py`, `test_service_control.py`, `test_dashboard_command.py`,
and the service/owner integration suites (including `test_service_lifecycle.py`). Browser
adapter, clipboard, Help and terminal-registry tests cover rendering and target
binding. See [how to run the gates](gates.md).

These tests are not an independent security audit. Before broader distribution:

1. Review each enabled surface with its actual deployed source/profile and
   dependency versions, including authenticated misuse and hostile output.
2. Retire each Botainer substitution only after its supported replacement passes
   consent, single-dispatch, owner-recovery, Stop and cleanup tests. Preserve
   native warnings; do not create a second launch-policy implementation.
3. Keep host profiles optional and explain their same-account reach. Do not
   describe setup probes as safe for untrusted interpreters or SSH config.
4. Qualify failure/recovery on claimed platforms and complete dependency,
   installation, vulnerability-reporting and public-release gates. Loopback-only
   source checks do not qualify public listening, remote-browser or mobile use.

When adding a route, helper action, launch mode, plugin invocation, file parser
or executable selection, update this inventory in the same change. Record its
caller, authority, allowed inputs, affected resources, failure behavior and
tests. New routes cannot inherit safety merely from requiring a pairing.
