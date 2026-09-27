# Projects, sessions and terminal views

A project is a registered folder. A session is running or recorded work in that
project. A terminal view displays one session. Opening a view does not request a
new agent; **New session** is the separate launch action and shows Botainer's
native warnings and consent prompts.

The sidebar spans enabled connections. Use its search, sorting and pins to find
projects. Names alone do not identify a runtime: installation, project and
session identity matter when several machines have similar labels.

| Action | Behavior |
| --- | --- |
| Project disclosure arrow | Expand or collapse its session list |
| Project name with one current session | Open that session, attaching when permitted |
| Project name with several current sessions | Show a chooser rather than selecting arbitrarily |
| Project name with no current sessions | Show overview and available launch actions |
| Existing connected session | Reuse its terminal view |
| Queued, unverified or restricted session | Show state and reason; do not invent a terminal |
| Ended session | Show retained output where available, read-only |
| New session | Launch separate work through the selected Botainer installation |

Requested agent overrides and recorded agent metadata are different. The UI must
not infer the actual agent solely from a project's current default configuration.
A bell/attention indicator is advisory terminal activity, not a reliable semantic
claim that every agent is waiting for input.

## Leaving versus stopping

| Action | Effect |
| --- | --- |
| Close view / Disconnect | Disconnect the viewer; send no stop request |
| Close browser tab | Leave the page and retain browser pairing |
| Sign out | Revoke this browser pairing and disconnect all its terminal views across machines and other tabs sharing that pairing |
| Stop session / Close host terminal | Request termination of the selected verified runtime/process |
| Cancel Slurm job | Cancel the whole allocation, which may contain other work |

Sign out does not stop the service, cancel jobs, close the shared SSH master,
revoke other independently paired browsers, or sign the coding agent out of its
provider. Supported durable owners are separate from browser views. After pairing
again, refresh inventory and reconnect to the verified surviving session.

This does not guarantee restoration of every tab or complete scrollback. Pending
launch consoles have deadlines. Host agents may create background tasks that
outlive their terminal. Uncertain cleanup, stale writer ownership and reconnect
failures remain [release blockers](status.md).

## When controls are unavailable

A running container or job can be observed without a verified original terminal
owner or an available safe control path. Read the specific reason before acting.
Refreshing SSH access does not establish ownership, and reconnect must not
silently launch a replacement. A running process is not ended merely because its
terminal cannot be reached. Use a verified per-target stop action when offered;
otherwise inspect it with the original Botainer/cluster tools.

Network restoration triggers inventory reconciliation, but automatic terminal
recovery is not fully qualified. Keep uncertain submissions visible and avoid
repeating a launch until its outcome is known. Cached inventory must remain
marked stale when the connection is unavailable.

Bulk stop and complete adoption of arbitrary external terminals are not finished.
Cancelling a cluster allocation is broader than detaching one terminal and needs
explicit review of other work sharing it.
