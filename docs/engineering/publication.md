# Source distribution and release checks

The dashboard is a development alpha. A reviewed source preview is not yet a
qualified installation or an approved release. See [status](../status.md) and
the [support matrix](../setup-support-matrix.md) for current limits.

## What belongs in a distribution

A source distribution contains explicitly reviewed application code, synthetic
tests, product documentation, dependency records and required license notices.
Private development history, correspondence, personal machine configuration,
approval records and raw trial logs are excluded. Ignore rules alone do not
define a release's contents.

Keep portable product guides under `docs/`, application code under `src/` and
`frontend/`, runtime/source-check tools under `tools/`, and synthetic tests under
`tests/`. The separate `prototype/` tree contains the UI simulation and portable
studies. Dependency inputs and notices have their own folders. Conversation
records, actual-machine fixtures, credentials and raw logs do not belong in
these product folders.

In the private development checkout, tracked maintainer tooling is isolated in
`dev/`, sensitive records in ignored `private/`, and environments/runtime state
in `.local/`. An explicit per-file manifest selects reviewed public content;
the exporter rejects those internal roots even if a manifest tries to rename
their files into a product directory. Folder placement and a clean Git status
do not approve a file for release.

Public source history is independent of private development history. New and
changed files need content review before inclusion. Dependency hashes establish
byte identity, not safety or editorial suitability.

## Checking a source copy

With the required Python packages and an existing Node.js installation, run:

```sh
python3 tools/check.py
```

The check verifies dependency records, bundled assets, portable tests, generated
prototype files and documentation. It does not install packages, contact a
cluster, or start Botainer sessions. A source archive needs no private checkout
or Git history. Optional `--pty` checks exercise local sockets, pseudo-terminals
and deterministic child processes; they do not qualify real agents or clusters.
See the [development guide](README.md) for prerequisites and test scope.

## Before a release

- Review the exact selected contents, licenses, examples, links and public
  metadata, including commit identities and messages.
- Run checks on a separate copy of that selection, without private fixtures or
  prepared development state. Verify the original selection remains unchanged.
- Inspect built archives for unexpected files and test installation and normal
  operation on each platform claimed as supported.
- Publish a support matrix that distinguishes tested behavior from limitations
  and untested routes. A successful source check alone does not pass these gates.
- Approve the exact destination and artifacts separately from preparing them.
  Retain public history and contributions across subsequent releases.

Private release tooling and operator records are not required to use an exported
source copy. Contribution and agreement requirements are described in
[Contributing](../../CONTRIBUTING.md).
