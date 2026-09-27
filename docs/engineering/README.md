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
