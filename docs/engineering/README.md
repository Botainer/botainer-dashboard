# Development guide

The source tree separates application code, synthetic tests and documentation.
The [documentation index](../README.md) groups the user guides.
Start with the [architecture](../architecture.md),
[dependencies](../dependencies.md) and [current status](../status.md). Existing
tools are required for the checks below; the commands do not install software.

| Directory | Responsibility |
| --- | --- |
| `src/botainer_dashboard/` | Python application, controllers, configuration and transport |
| `frontend/` | Served browser application and reviewed terminal assets |
| `tools/` | Entrypoints, fixed helpers and source checks |
| `tests/unit/` | Synthetic Python contract and state-machine checks |
| `tests/frontend/` | Node tests for browser logic and controlled renderer/transport doubles |
| `tests/integration/` | Local deterministic process, PTY and loopback transport checks |
| `prototype/` | Separate sample-data UI simulation |
| `requirements/`, `third_party/` | Current dependency identities, bundled assets and license inventories |
| `docs/` | Portable user and engineering documentation |

## Checks

Run the source gate with an existing compatible Python and Node:

```sh
python3 tools/check.py
```

It checks dependency and asset integrity, Python unit tests, frontend tests,
simulation behavior/generated files, document links and whitespace. In a Git
checkout it also checks patch whitespace. An exported source directory without
its own Git metadata skips the Git-specific step, not the source checks.
Dependencies unavailable in a test environment may cause explicitly reported
skips; examine them before claiming coverage.

The following adds local deterministic PTY and loopback HTTP/WebSocket tests:

```sh
python3 tools/check.py --pty
```

It requires the existing application libraries and permission to use local
sockets/processes/PTY devices. It does not contact a cluster, launch real
Botainer work or qualify agent credentials.

A private development checkout can additionally run `python3 tools/check.py
--private`. This opt-in suite is not part of the public source export, and the
command fails if that suite is absent. Machine-specific observations and tests
belong there; portable tests use synthetic identities and disposable state.

For a focused browser-logic change:

```sh
node --test tests/frontend/*.test.mjs
```

For dependency or bundled-asset changes, also use
`python3 requirements/verify.py` and the
[update process](dependency-updates.md).

## GitHub CI

The source repository's `.github/workflows/ci.yml` runs on pushes, pull requests
and manual requests. It uses a fresh GitHub-hosted `macos-15` arm64 runner,
Python 3.13.15 and Node 22.23.3. This matches the platform targeted by the
existing dependency lock; Linux runtime CI needs a separate wheel selection.

The workflow creates a disposable Python environment and installs the 14 exact
binary wheels from the hashed requirements. It checks their installed versions
and service imports, then runs `python -B tools/check.py --pty`. Missing or
mismatched runtime dependencies fail the job instead of silently reducing test
coverage. No application build, npm install or Botainer installation is needed.

These checks cover Python unit tests, browser logic, the simulation, generated
files, dependency integrity, documentation, and deterministic local terminal and
HTTP/WebSocket behavior. Five optional YAML tests and six opt-in tmux tests are
skipped in this environment. Real Botainer/agent sessions, Docker, SSH/clusters,
browser automation and installation acceptance are separate checks. CI does not
require Botainer alpha5; testing the official Botainer release remains necessary
before claiming real-session acceptance with that version.

To reproduce the test gate locally with existing application dependencies and
Node, run `python -B tools/check.py --pty`. The workflow's **Verify runtime and
run checks** step also shows the dependency preflight. The local rehearsal used
Python 3.13.15 and Node 22.18.0; it exercises the same commands, not GitHub runner
provisioning or the hosted Node patch. The first hosted run must still be checked.
Do not install dependencies into a working dashboard environment just to run CI.

On GitHub, enable Actions under **Settings → Actions → General** and allow the
three GitHub-owned actions named in the workflow. Push the workflow commit, then
open **Actions → CI** to inspect the result. Once it is on the default branch,
**Run workflow** also starts a manual run. If the workflow was previously disabled,
enable it there first. Fork pull requests may need maintainer approval to run;
keep that approval requirement. External patches remain paused under the
[contribution policy](../../CONTRIBUTING.md).

The job has read-only repository permissions, does not retain checkout
credentials, and uses no repository secrets, dependency caches or self-hosted
runner. It does not publish anything or access a user's machines. The test step
is offline apart from loopback sockets; provisioning downloads the pinned actions,
interpreters and wheels. Action revisions are pinned to full commit hashes.
Review action/interpreter pins and the wheel lock during dependency updates.
See [GitHub's runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
and [Actions settings](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository).

## Changes and evidence

Keep browser requests scoped to declared operations and exact target identities.
Preserve native Botainer warnings, confirmations and cleanup through its CLI.
Do not convert request text, project labels or config content into shell syntax.
Treat all project/container output as data, including terminal escape sequences.

Test the behavior a change can break: stale identities, partial failures,
concurrent views, bounded buffers, canceled work and uncertain outcomes.
Reconnection must preserve owner identity and must not replay input or silently
launch another agent. Passing helper tests establishes that helper's behavior,
not a complete real-session acceptance result.

Record reproducible test conditions and honest limits in portable documentation.
Keep actual transcripts, personal machine data, approvals and correspondence
outside the public candidate. Public release preparation uses an explicitly
selected source export with independent history. Installation, runtime trials,
pushing a remote and publication remain separate actions.
