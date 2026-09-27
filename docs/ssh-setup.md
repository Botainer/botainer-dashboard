# Set up SSH for a dashboard machine

The dashboard uses the SSH client on the computer running its service. Prepare
and test SSH there, as the same operating-system user. This alpha supports a
browser on that same computer; remote-browser access is not qualified. Connecting
the dashboard to a remote cluster is a separate capability. The commands below
target macOS/Linux; native Windows service operation is not currently supported.
The dashboard does not need X11 forwarding, a remote desktop, or SSH agent
forwarding for its terminal route. It does not collect login passwords, private
keys, key passphrases or MFA responses in browser settings.

An **SSH alias** is a short connection name such as `research-cluster`. An
**SSH authentication agent** (`ssh-agent`) holds keys for SSH authentication;
it is unrelated to coding agents such as Claude or Codex. A **Botainer
installation** is the particular command/source/state selected after SSH works.
These are three separate choices.

Working SSH supplies access, not the entire Botainer setup. The implemented
ordinary remote adapter is a **maintainer-assisted, site-qualified preview** of
Botainer's Slurm/HPC workflow; an arbitrary remote Docker server is not enabled
merely by adding its SSH alias.
See the [support matrix](setup-support-matrix.md). If your alias already works,
start at [First login, then dashboard test](#first-login-then-dashboard-test).

## Before starting a cluster session

SSH preparation below establishes access. Before using **New session** on a
new cluster, the dashboard maintainer and operator must qualify that installation:

- Select an existing Botainer alpha5 installation with Python 3.11 or newer,
  its HPC plugin, Slurm access, the intended Apptainer image and agent login.
  Follow the site's allocation and resource rules.
- The remote project needs `nudge` in its existing `plugins_enabled` list.
  Despite the name, this Botainer plugin also supplies the durable Screen owner
  required for reconnect. New projects made from the standard HPC template may
  leave it disabled. Review and validate the project config before its first
  dashboard launch. The local Docker dashboard route instead requires `nudge`
  disabled because it uses its own tmux owner.
- The attach helper accepts only a specifically reviewed compute-node Screen
  binary and system Screen configuration. A matching version string is not
  enough. The current policy is fixed in
  [the attach helper](../tools/cluster_attach_supervisor.py); the machine wizard
  does not qualify another site or inspect a future allocated compute node.
- Personal `~/.screenrc` files are unsupported by this qualified attach route.
  Do not delete or rename an existing configuration just to pass a check; have
  the maintainer assess the route. Never disable executable/config verification
  or accept a fresh replacement agent as a reconnect workaround.

Qualification needs an authorized disposable allocation with actual terminal
input, viewer disconnect/reconnect to the same owner, and exact job cancellation
and cleanup. Verify Screen policy before scheduling useful work: a new site can
accept a job yet fail the later attach check. Until this route is qualified,
use the site's established terminal workflow and treat dashboard inventory as
observation only. The wizard's successful connection check is not permission to
claim general cluster support. See [the support matrix](setup-support-matrix.md).

## Choose an authentication route

| Your account | Setup | Dashboard behavior |
| --- | --- | --- |
| Existing working alias and reusable login | Keep it; test in the same service environment | Uses that alias; no credential copy |
| SSH key, no MFA | Use an existing approved key, normally protected by a passphrase, and the host's public-key registration process | Can connect if key use needs no new prompt or hardware confirmation; otherwise establish sharing interactively |
| Password login, no MFA | Authenticate in your own terminal and keep a shared SSH connection available | Uses the authenticated connection; never stores your password |
| SSH key plus MFA | Authenticate in your own terminal; connection sharing avoids a fresh challenge for every observation | Reports unavailable when a new interactive login is required |
| Jump host / bastion | Configure the site's approved SSH route first | Reuses a working alias; this dashboard route has not had live qualification |

Password, alternate MFA sites, jump-host and Windows routes are documented
options, **not tested dashboard support claims**. See the
[setup support matrix](setup-support-matrix.md). Site administrators determine
allowed authentication methods; do not change a server's security policy to fit
the dashboard.

## If you do not have an alias

Obtain the host name, account name, allowed login method and host-key fingerprint
from the machine owner or institution. Inspect your existing `~/.ssh/config`
before editing it; preserve unrelated entries. The following is a synthetic
example, not a usable account. Use a normal login alias without an automatic
`RemoteCommand` or a transport-only `SessionType none`: the dashboard supplies
its own remote commands. A shell alias such as `alias cluster='ssh ...'` is not
an entry in SSH configuration.

Use only SSH configuration you trust. `ProxyCommand`, `Match exec` and other
configured helpers can execute programs on your computer. The wizard's
read-only installation probe does not sandbox those SSH features. Review any
copied configuration and included files before using it.

Create the socket directory before the first login if absent. Confirm existing
paths belong to your account; do not repurpose a shared directory or symlink:

```sh
mkdir -p ~/.ssh/control
chmod 700 ~/.ssh ~/.ssh/control
```

Then add the reviewed entry to `~/.ssh/config`:

```sshconfig
Host research-cluster
    HostName login.example.edu
    User researcher
    IdentityFile ~/.ssh/id_ed25519_research
    IdentitiesOnly yes
    ForwardAgent no
    ForwardX11 no
    ClearAllForwardings yes
    ControlMaster auto
    ControlPath ~/.ssh/control/%C
    ControlPersist 15m
    ServerAliveInterval 30
    ServerAliveCountMax 3
```

Use a new, specific alias and place it before broad matching defaults. Substitute
your actual host, account and existing key. For a password-only account omit
`IdentityFile` and `IdentitiesOnly`; leave authentication selection to the site's
policy. These values describe the master you establish in your terminal; the
[runtime differences below](#what-the-dashboard-actually-uses) matter too.
Connection sharing grants programs running as your local account continued use
of the authenticated transport. Keep its socket directory private. Distinct
aliases can still share a socket when they resolve to the same destination,
port and remote user; the alias label or Botainer installation does not itself
provide SSH isolation.
[OpenSSH configuration reference](https://man.openbsd.org/ssh_config).

`ControlPersist 15m` means 15 minutes with **no connected clients**. It is not a
15-minute login lifetime. Open terminals or recurring dashboard requests can
keep the master available much longer. It is not a scheduled logout or a promise
to re-prompt for MFA at a particular time.
[Persistence reference](https://man.openbsd.org/ssh_config#ControlPersist).

`ServerAliveCountMax 3` counts unanswered keepalive probes. Together with
`ServerAliveInterval 30`, it lets SSH detect an unresponsive server after roughly
90 seconds. It does **not** limit terminals or shared sessions to three.
Increasing it tolerates longer outages but delays detection of a dead connection;
it does not add capacity or guarantee recovery after sleep.
[Keepalive reference](https://man.openbsd.org/ssh_config#ServerAliveCountMax).

`ControlMaster` enables sharing. The remote server's `MaxSessions` setting limits
simultaneous shell, command and subsystem sessions on one SSH connection;
OpenSSH's default is 10, but a site's value may differ. This is an administrator
setting, not something to add to the local alias. Dashboard terminal attachments,
background commands and other tools sharing the same connection can consume
these slots. This is not a limit on the total number of registered projects or
detached jobs. The site's actual limit and capacity under concurrent use must be
qualified separately.
[Session-limit reference](https://man.openbsd.org/sshd_config#MaxSessions).

Keep `~/.ssh/config` writable only by your account (normally mode `600`). These
are deliberate user setup steps; the dashboard does not edit your SSH
configuration. OpenSSH may maintain its own host-key records; see the runtime
differences below. Do not
copy another user's SSH directory or put credentials into a project repository.

For a site-approved bastion, a separate alias may use `ProxyJump gateway-alias`.
Configure and verify both hosts. Destination command-line options are not
automatically applied to the jump host, so its own authentication and host-key
settings matter. A prompt-free final command must work through the complete
route before trying the dashboard. No private key needs to be copied to the
bastion and agent forwarding is unnecessary for ProxyJump. This dashboard route
is still unqualified. [Jump-host reference](https://man.openbsd.org/ssh#J).

## Keys and passphrases

Reuse an existing suitable key where policy permits. If a new key is required,
choose a **new filename**, check that it is unused, and follow the host's policy.
For a site accepting Ed25519, an example is:

```sh
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_research
```

Choose a passphrase. Upload only the `.pub` public key through the site's
approved process. Never upload the private file, give it to a setup agent, or
overwrite an existing key. Generating a key changes local credentials and should
be an explicit user decision. [OpenSSH key tool](https://man.openbsd.org/ssh-keygen).

If the operating system already supplies an SSH agent, inspect fingerprints
with `ssh-add -l`. To load the selected key for a bounded time:

```sh
ssh-add -t 1h ~/.ssh/id_ed25519_research
```

Enter its passphrase in your terminal. The dashboard service must have access
to that agent. Adding a key to an agent it already uses needs no service restart;
changing the agent socket/environment may require a deliberate restart after
unfinished operations are resolved. Expiring or removing a key from the agent
does not close an already authenticated shared connection. Do not paste agent socket paths or key
material into dashboard configuration. On macOS, optional Keychain integration
is OS-specific; use the system's documented tools rather than assuming another
OpenSSH build accepts Apple options.
[SSH agent](https://man.openbsd.org/ssh-agent),
[ssh-add](https://man.openbsd.org/ssh-add).

## First login, then dashboard test

These commands use `/usr/bin/ssh`, the client currently used by the runtime.
Run them as the service's user on its computer. Substitute your actual SSH alias.
For first login:

```sh
/usr/bin/ssh research-cluster
```

On a first connection, compare the displayed host-key fingerprint with a trusted
source before accepting it; `ssh-keyscan` alone is not independent verification.
Complete password/passphrase/MFA prompts yourself. If `ControlPersist` is set
as above, exit the remote shell when finished; the shared master can persist.
Without persistence, keep its original terminal open while using shared clients.
Without sharing at all, an unrelated open terminal cannot authenticate dashboard
requests. A key-only account can instead authenticate each new request without
sharing if the runtime has usable credentials and no prompt is required.

For the shared-connection route, check the master from a local terminal:

```sh
/usr/bin/ssh -O check research-cluster
```

This checks the local master process, not remote responsiveness or available
session capacity. It may succeed while the network is broken. For key-only
access without sharing, its failure is expected; proceed to the command check.

Test a remote command with the runtime's main SSH options:

```sh
/usr/bin/ssh -nT \
    -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=8 \
    -o ControlMaster=no -o PermitLocalCommand=no \
    -o ForwardAgent=no -o ForwardX11=no -o ClearAllForwardings=yes \
    -o ServerAliveInterval=15 -o ServerAliveCountMax=2 \
    -o EscapeChar=none research-cluster true
```

The requested remote command is only `true`, which normally returns without
output. SSH helpers and remote shell startup files may still run. Immediately
run `echo $?`: `0` means that command succeeded. Login prompts or a nonzero exit
need diagnosis before dashboard use. `ConnectTimeout=8` bounds connection setup,
not the whole command; the dashboard also enforces request-specific deadlines.
If this manual check hangs, interrupt this check with Ctrl-C.

This tests command access, not Botainer, Slurm, container ownership or terminal
recovery. It still inherits your terminal's environment; the runtime uses the
more restricted environment described below.
Return to **Settings → Machines**, select the same alias, and run the wizard's
connection check. After reviewing/saving the profile, restart deliberately to
activate it and verify its inventory. Do not restart during an unresolved
launch. [OpenSSH client reference](https://man.openbsd.org/ssh).

## What the dashboard actually uses

The current setup probe and active runtime have differences that are not yet
unified. A successful setup check does not prove the full runtime route:

| Detail | Setup check | Active remote runtime |
| --- | --- | --- |
| SSH executable | First `ssh` on the service's PATH | `/usr/bin/ssh` |
| Environment | Service environment | Fixed system PATH and C locale; retains `HOME` and `SSH_AUTH_SOCK` when present |
| Connection sharing | Reads alias settings; may create a master if they request it | `ControlMaster=no`: reuses an existing matching master, but does not create a new shared master |
| Keepalives for a new connection | Reads alias settings | Overrides to 15 seconds and 2 unanswered probes, roughly 30 seconds |
| Host-key updates | `UpdateHostKeys=no` | Follows installed OpenSSH/configuration behavior for updates to an already trusted host |

Both use batch authentication, strict host-key checking, an eight-second
connection timeout, and disable agent/X11/port forwarding and `LocalCommand`
for their requests. These flags do not remove existing forwards from a shared
master or sandbox configured SSH helpers. A reused master keeps its original
transport settings: the runtime's 15/2 values do not change that master's
keepalives. Host-key checking occurs when a transport is established; reusing
one is not a fresh host-key check on every request.

If terminal login works but runtime requests fail, check the system client and
agent environment. A PATH-selected SSH build, a proxy helper installed outside
the runtime PATH, or a helper requiring other environment variables may work
in the wizard and fail during actual use. Do not fix this by weakening host-key
checking. Record the mismatch and use a route supported by the system client.
These differences remain a setup/runtime consistency limitation.

## Troubleshooting

Open **Settings → Machines** to see the connection's current problem and next
step. **Loaded** describes registration, not a successful SSH check. The same
connection may be loaded while its host name cannot be found, login is required,
or a remote request fails. Cached projects remain visible when available; their
presence does not establish current session activity.

| Symptom | What to do |
| --- | --- |
| Host name cannot be resolved | Check the alias spelling, `HostName`, network and VPN. An alias must exist on the service computer. |
| Timeout / no route | Check the required VPN, site status and firewall route. Do not open a public dashboard port as a workaround. |
| `Permission denied (publickey)` | Check the selected account and public-key registration. Use `ssh-add -l` to inspect loaded fingerprints. |
| Key works in terminal, not dashboard | Start the service from the environment with the usable agent, or establish a shared master; then retry. |
| Password or MFA works once, dashboard stays offline | Verify the shared master and the full command check above using `/usr/bin/ssh`. Merely having a separate SSH shell open is insufficient without sharing. |
| Master check succeeds but requests fail | The local master can exist during a network outage. Check the remote command result, VPN and authentication; distinguish a dead transport from a full session limit. |
| `Session open refused by peer` or `no more sessions` | A shared connection may have exhausted the server's session slots. Disconnect an unneeded dashboard terminal viewer, or finish an unneeded SSH command/file transfer; account for other tools and background checks. Do not close a foreground agent or stop a job to free a slot. These messages are hints, not proof of the cause. Changing `ServerAliveCountMax` cannot add slots. Do not kill the shared master or repeatedly retry launches. |
| Control socket cannot be created or its path is too long | Check ownership/permissions and that its private parent directory exists. Keep the path short and destination-specific, as with `%C`. Do not delete a socket belonging to a live master. |
| Passphrase / MFA needed after sleep or expiry | Log in deliberately in your terminal and retry the machine check. Do not repeatedly trigger MFA prompts. |
| Host identification changed | Stop and verify the change with the administrator through a trusted channel. Do not disable host-key checking or blindly remove known-host entries. |
| SSH works but Botainer is missing | A shell alias or interactive PATH may differ. Select the exact supported installation in the wizard; do not install a second Botainer automatically. |
| Machine is available but Connect is disabled | Inspect the session's ownership/capability reason. SSH access alone does not establish a persistent, attachable terminal. |
| Botainer source, launcher or Python changed | Use **Settings → Machines → Review Botainer update…** for the saved connection. Inspect the intended installed copy, review its code, then save for restart. A new SSH login alone cannot fix this; do not replace hashes merely to silence the warning. See [the update procedure](maintenance.md#update-botainer-or-the-dashboard). |
| Installation ownership or write permissions are unsafe | The connection reached a verification step but selected code is not protected from other accounts, or cannot be read safely. Use a protected installation or ask its administrator to review permissions. Do not disable verification or change shared-directory permissions indiscriminately. |
| Installed plugins or approved hooks changed | Review those host-side programs through connection setup. The limited Botainer update action does not approve changed or newly discovered installed plugins or expand local hook approval. |

For detailed login investigation, `ssh -v research-cluster` reports authentication
steps. Keep the output private and redact usernames, addresses, paths and key
fingerprints before sharing it. Never send private keys or passphrases.

Software verification, SSH access and scheduler access are separate checks.
An open SSH shell can coexist with an outdated Botainer selection. The update
form changes only the selected software after review; it does not edit the SSH
alias, account, project folders or scheduler settings. Updating a remote profile
requires a dashboard restart and new browser pairing. Old dashboard launch
receipts remain archived rather than being reassigned to the new installation.

Network recovery and session recovery are different. A qualified Slurm batch
owner may continue while the laptop is offline; an unavailable connection is
not an ended job. Reconnect only to the verified original session. Never start
a replacement merely because its terminal cannot currently be reached. See
[terminal recovery limits](sessions.md).

After network restoration or deliberate reauthentication, refresh the dashboard
to reconcile inventory, then use the offered reconnect action for the original
session. Automatic polling/retry exists but complete sleep/wake and terminal
reattachment recovery is not qualified. Signing out of the dashboard does not
close the shared SSH master. Closing that master would disconnect all clients
using it, including other applications; it is not a routine recovery step.
