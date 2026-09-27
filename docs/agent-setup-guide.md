# Operator and assistant setup guide

Use this runbook to prepare the reviewed dashboard alpha and connect existing
Botainer installations. It describes the work; it is not authorization to
install software, alter credentials, upgrade Botainer or launch workloads.
Respect the operator's scope, preserve unrelated files, and make required
changes reviewable before executing them.

## Establish the target

Read [status](status.md), [platform support](setup-support-matrix.md) and
[configuration scopes](configuration.md). Read [Installation](installation.md)
for the exact offline installer contract and its current acceptance limits.
Do not treat a prepared development computer, synthetic package tests or a
successful wheel build as proof of a fresh installation or live agent workflow.
Dashboard `0.1.0a1` and Botainer `0.1.0a5` are separate versioned products.
Keep the first recipient trial to the documented macOS/local Docker route.
Remote clusters and initial host-agent profile provisioning are
maintainer-assisted previews, not additional prerequisites for trying the
container dashboard.

Record the following privately, without keys, tokens or project content:

- Dashboard source revision/artifact, OS/CPU and existing Python, browser and SSH.
- Each requested account, machine, intended Botainer installation and state root.
- New-project setup parents, saved selection/profile paths and existing control roots.
- Application environment and private data directory, recorded separately.
- Known running sessions or unresolved operations that must be preserved.
- Authorized changes, required missing prerequisites and the rollback approach.

Check the [route-specific prerequisites](installation.md#prerequisites-and-release-inputs)
before offering installation. In particular, local sessions require local tmux
and the Docker CLI/daemon; the selected Botainer Python must be at least 3.11,
independently of the dashboard Python. Inspect the existing Docker context and
select its local Unix endpoint in the wizard. Do not create privileged socket
symlinks, switch Docker contexts, or install another Botainer to hide a setup
mismatch. Tool hashes identify reviewed bytes, not an assurance that every tool
version has been exercised.

Do not read agent conversations or project files as routine setup discovery.
A working SSH login establishes transport, not trust in remote Botainer code or
its plugins. SSH config can itself run commands through proxies or `Match exec`;
review unfamiliar settings before invoking them.

## Prepare and check the dashboard runtime

For a new install, use the supplied `tools/install.py` and exact reviewed
dashboard wheel plus 14 dependency wheels. Run its default read-only plan with
the selected existing CPython 3.13.15+ (within 3.13), on macOS arm64. Review its
platform, hashes, destination and exact apply command. Linux installation has
not been qualified. No package download or alternate interpreter acquisition
is implied by this runbook.

Apply only after the operator's installation approval, using the plan's required
`--wheel-sha256` and `--apply`. Use a fresh versioned destination; never overwrite
an existing environment. Retain the install receipt and preserve earlier
environments while live terminal owners depend on them. Do not use online pip,
unreviewed build dependencies or an incidental Botainer install to make a plan
pass. The installer does not modify PATH or start a service.

Then use the installed executable (its full path if needed):

```sh
botainer-dashboard check
botainer-dashboard start --open
```

`check` inspects local package/startup requirements and saved configuration,
without starting a service or contacting Docker/SSH. It is not a fresh security
assessment of dependencies or proof of workload readiness. `start` runs the
foreground service. Use the same `--data-dir` for every later command if selecting
one explicitly. Installed defaults are listed in the installation guide; private
data must live outside the environment. Do not migrate a checkout's `.local/`
records automatically or silently pick another empty data directory.

For an already prepared development checkout only, substitute
`python3 tools/dashboard.py` from its source folder. It keeps the original
`.local/envs/botainer_dashboard/` runtime and `.local/` data. Downloading source
does not create that runtime. `python3 requirements/verify.py` checks source
dependency metadata/assets offline; it is not an installation procedure.

For source validation, an existing Node.js and the prepared Python runtime can
run `tools/check.py`. `--pty` adds local loopback/process/PTY tests; `--private`
is for a development tree that actually contains its private test suite.
Neither option tests a real cluster or authorizes agent credentials.

## Establish remote transport

Follow [SSH setup](ssh-setup.md). Prefer an existing working alias. For a new
one, prepare the exact host-specific stanza without overwriting unrelated SSH
configuration. The operator verifies the host fingerprint and enters their
password, passphrase or MFA in a normal terminal.

A key-only account may authenticate noninteractively. Password/MFA routes can
reuse a deliberately configured shared SSH connection, subject to site policy.
An open terminal alone does not establish connection sharing. A local
`ssh -O check` result also does not prove the remote host is reachable; use the
bounded remote check described in the SSH guide. Do not repeatedly trigger MFA,
remove known-host entries to silence warnings, or enable agent forwarding.

Authentication-agent keys and coding-agent credentials are separate. Do not
copy coding-agent credentials to a remote as part of SSH setup. Untested key,
password, bastion and MFA routes remain unqualified until exercised separately.
Before any cluster submission, follow the additional
[Screen and project-config requirements](ssh-setup.md#before-starting-a-cluster-session).
The fixed compute-side Screen policy is not established by an SSH probe on the
login node. Qualify that policy and same-owner recovery before inviting a user
to start useful work.

## Connect an existing Botainer installation

Start the dashboard using [startup instructions](start-dashboard.md), then use
**Settings → Machines → Add machine**. Supply the existing launcher/interpreter,
state root, parent folders for project setup and private control directory. Existing
registered Botainer projects are verified at their own locations and do not need
another folder allowlist. A custom
shell alias must be resolved to its actual installation paths without executing
arbitrary shell startup files.

Use an owner-private control folder with mode `700`; the local tmux route needs
an absolute path no longer than 55 encoded bytes. Actual container project
folders must neither contain nor sit inside dashboard code/data/control or
Botainer code/state directories. Broad setup parents may still hold safe sibling
projects and controls. Preserve this separation when adding existing projects;
do not solve a refusal by moving trusted control files into a project.

The wizard needs a supported source or native installed-package layout and
existing control folders. The wheel/pipx path pins the selected Botainer package,
distribution metadata and entry point rather than substituting a separate source
checkout. Its live acceptance is pending; preserve that qualification label in
the handoff. Existing source profiles keep their original format and identity.
The check reads bounded source/tool metadata and hashes; it does not import
Botainer, run its hooks, install packages, create remote folders or submit jobs.
A successful check identifies observed bytes, not trusted behavior or complete
session acceptance. Review the selected code, plugins and scope before the
separate trust acknowledgement and save.

Local setup also displays exact host-hook paths and fingerprints. The separate
unchecked hook approval permits only candidates matching the inspected source
or support-file pins. Hooks can execute host-side setup and cleanup, so obtain
the operator's trust decision; do not auto-check it to make launch succeed.
Inspection changes invalidate the review. Imported prepared profiles require
their own reviewed `approved_hooks` mapping. Native CLI consent still runs for
each ordinary launch.

A source/package layout or import-selection error should be resolved in preparation.
Never invent hashes, widen project-setup destinations, change accounts or select another
Botainer installation merely to make the check pass. **Advanced → Import
prepared profile** accepts an independently reviewed profile when appropriate.

Save the selection and restart deliberately with the same file. Confirm every
selected machine separately. Cached/offline observations must retain their age;
absence of a current observation is not proof that a session ended.

## Validate a usable workflow

After offline checks, perform only the live trials the operator authorized.
Use a clearly named disposable project, existing images and explicit resource
limits. On each intended route:

1. Check project discovery across unrelated folders, including inactive projects,
   missing/moved/ambiguous native identities, and bounded new-project setup.
2. Decline a native launch confirmation and verify no workload was started; then
   accept one intended launch.
3. Exercise actual terminal input, resize, viewer close/reopen and reconnect to
   the same original owner. Use the real supported agent before claiming its
   credential/helper lifecycle works.
4. Stop the exact session or cancel the exact scheduler job, confirming the
   impact and cleanup. Shared allocations need extra scope review.
5. Test connection removal/re-add without deleting remote work or recovery data.

An ambiguous operation is a result to investigate, not an instruction to launch
another agent. A deterministic shell fixture does not qualify real-agent
credentials, laptop sleep or network partition recovery.

## Hand over the setup

Provide the exact startup command, selection-file location, pairing procedure,
SSH login/recovery steps, and machine labels. Show **New session**, **Connect**,
**Stop**, and **Settings → Machines**. Link [Getting started](getting-started.md)
and [sessions](sessions.md).

Show `botainer-dashboard status` and `botainer-dashboard pair`, with the same
`--data-dir` and an exact port/instance if several services exist. The pairing
page and startup terminal print the appropriate command. The operator runs it
in their own interactive terminal and enters the code in the browser; no manual
file hunting is required. Do not collect codes in an agent transcript or setup
log. Renewal does not restart the service or revoke existing pairings. Older
services can use their printed `--pairing-code` command with the exact private
access-file path. See [pairing and recovery](start-dashboard.md#pair-the-browser).

Keep a private record of changes, test results, untested routes and how to
restore the previous reviewed selection. Preserve profiles and owner records
when disabling/removing a machine. A setup handoff must not include credentials,
terminal transcripts or account-specific configuration in public documentation.
