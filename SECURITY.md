# Security

The dashboard is an authenticated control service for the operating-system
account running it. Browser access can launch processes and interact with
projects on configured installations. The supported listening boundary is
loopback; exposing the service directly to a network is outside the current
support scope. See [architecture](docs/architecture.md).

This is not just a visual wrapper around unmodified Botainer commands. It adds
an HTTP/WebSocket control service, terminal owners, file/configuration operations
and compatibility helpers that call and temporarily wrap Botainer internals.
The [entry-point inventory](docs/engineering/security-entry-points.md) lists the
routes, local control channels, CLI exceptions, safeguards and remaining work.

A paired browser has operator access to all loaded connections; there is no
read-only role or per-project user authorization. Connection inspection can
execute the selected Python interpreter. Fixed probe arguments and an inspection
acknowledgement do not make an untrusted executable safe. Pair only a trusted
browser, and select only trusted installations and profiles.

Selected Botainer source hashes detect changed files; they do not attest the
whole Python environment. Native dashboard helpers ignore installed Python
bytecode caches for imports in their scope, reject unsupported Botainer-package
extension/sourceless bytecode artifacts, and check POSIX ownership/write modes.
Interpreter startup code, dependencies and arbitrary plugin descendants still
require a trusted installation. These checks do not protect against malicious
same-account writers or account-level ACL access.

On macOS, the pinned Docker Desktop CLI may live beneath the standard
root-owned, administrator-writable `/Applications` directory. This trusts
platform administrators; Docker's executable and inner directories still need
safe permissions and its bytes must match the reviewed hash. This allowance
does not apply to Python, Botainer source or plugin hooks.

Treat machine/runtime profiles as trusted operator configuration. SSH aliases
and their included configuration can invoke local helper programs. Review them
before connection setup; the dashboard does not sandbox SSH configuration.
Native host-agent sessions have no container isolation and require separate,
explicit configuration and launch consent.

Owner-only files and sockets protect against other operating-system accounts,
not a compromised process running as the same account. A native host agent can
have access to dashboard control files and other projects. Allowed project roots
constrain dashboard operations; they do not sandbox that agent. The cookie and
bearer are complementary browser checks, not two-factor authentication, and do
not contain a compromised dashboard origin or browser profile.

Browser pairing uses a short-lived, single-use code. The service prints its
private file path and an explicit local command for displaying a usable code.
That command uses owner-only files and a live ownership lock, with no new HTTP
control endpoint. It refuses redirected code output and does not restart the
service or revoke existing pairings. Ordinary API and terminal access still
require both the browser's HttpOnly cookie and its independent origin-scoped
bearer. See [pairing instructions](docs/start-dashboard.md#pair-the-browser).

The local `status` and `stop` commands use a separate owner-only service mailbox.
They do not add a network shutdown endpoint or kill processes by PID/port. Fresh
replies identify a responsive service; shutdown is confirmed only when that
service releases its lifetime lock. A broken mailbox leaves status uncertain and
requires the original foreground terminal. Stopping disconnects browser views
without requesting session Stop/Cancel, but can interrupt unfinished setup or
launch prompts. Same-account processes can access this mailbox too.

Keep pairing state, SSH keys, authentication-agent information, logs, runtime
records, and project content outside shared source. Do not submit them in public
issues. Use synthetic reproductions and redact diagnostic evidence before sharing.

Routine remote read diagnostics keep typed metadata, not file/config response
bodies or SSH stderr. That journal is bounded by count, bytes and age; pruning
runs during observations. Operation receipts, launch transcripts and private
config recovery copies are separate and can contain sensitive information.
Old failed, incomplete or unrecognized records are retained for review, not
silently deleted. See the [entry-point inventory](docs/engineering/security-entry-points.md)
and [config recovery policy](docs/configuration.md#project-config-and-save-state).

## Report a security problem

For a suspected authentication bypass, unintended file/process access or other
security defect, use private reporting rather than a public issue. In the public
GitHub repository identified by the release announcement, open **Security →
Advisories → Report a vulnerability**. GitHub may label the first tab **Security
and quality**. This form is available only after the repository owner enables
private vulnerability reporting. See [GitHub's reporting instructions](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/report-privately).

If that button or the public repository is unavailable, contact the person who
supplied your alpha privately through the channel you already use. Ask for a
private reporting route before sending details. Do not post exploit steps,
logs, tokens, connection profiles or project content in public Issues. A private
report should still avoid real credentials; use a small synthetic reproduction
and identify the affected dashboard/Botainer versions.

**Publication gate:** the repository owner must enable private vulnerability
reporting, verify that the form is available and monitored, and identify the
public repository in the release before public distribution. This file does
not enable the feature, and its availability has not yet been confirmed for a
public release. The limited alpha has no guaranteed response time or
supported-release security maintenance commitment.

## Changes and recovery

Changes must preserve strict origin/authentication checks, verified owner identity,
explicit native preflight/consent, and separate stop-versus-disconnect semantics.
An uncertain connection or cleanup result must not authorize a replacement agent
or replay terminal input. See [known limits](docs/status.md).
