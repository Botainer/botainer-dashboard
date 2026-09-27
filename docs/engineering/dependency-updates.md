# Dependency updates

Maintain the smallest tested dependency set that supports the dashboard. Exact
versions and artifact identities belong in [requirements](../../requirements/README.md),
with bundled asset notices in [third_party](../../third_party/README.md). The
current inputs cover 14 Python packages and three xterm packages. They are
source-distribution inputs, not a completed cross-platform installation system.

## Selection and review

Prefer supported stable releases with relevant fixes and compatible artifacts.
Do not select a version solely because it is newest, oldest or widely used.
Review every changed direct and transitive dependency, native component, source
origin and license. Keep compatible families together, including Pydantic/core
and xterm/addon releases.

Check upstream release notes, security advisories and exact platform artifacts.
Record unresolved applicability questions explicitly. Hashes identify bytes;
registry vulnerability fields and a successful dependency resolver are not a
complete security review. Use maintained official artifact sources and reject
unexpected source builds or installation scripts unless separately reviewed.

The maintainer should review dependencies before a release and periodically
while supporting it. No automatic updater or scheduled advisory service is
currently provided. Discovery/review must not silently install tools or alter
a running environment.

## Update sequence

1. Preserve the current application, dependency inputs and working environment.
   Prepare candidate changes separately; do not update the active environment
   in place.
2. Record each version, artifact/hash, dependency and license change. Review
   compatibility and security evidence, including components retained unchanged
   when a known issue may affect them.
3. Select the complete artifact set for each intended OS, CPU and Python ABI.
   A macOS wheel selection is not evidence for Linux or another architecture.
4. Obtain and inspect the approved artifacts through an explicit setup action.
   Verify hashes before use. Preserve upstream notices. For browser bundles,
   copy only the selected assets and licenses; do not run upstream npm scripts.
   Inspect embedded code, generated data and native-library notices as well as
   top-level package licenses. Recheck the artifact-specific maps in
   `third_party/` and include the full applicable terms. A build dependency
   inventory may be broader than the linked binary; label that scope honestly.
   Check that supplements survive application installation alongside the
   unchanged dependency wheels. Old notice coverage does not approve new bytes.
5. Test a fresh isolated environment. Check package consistency, imports and
   the [source gate](README.md), then exercise affected real HTTP/WebSocket,
   terminal, configuration and lifecycle behavior. Deterministic tests alone
   do not qualify a real Botainer or cluster workflow.
6. Update `requirements/manifest.json`, artifact metadata, hashed wheel inputs,
   `third_party/manifest.json` and `requirements/SHA256SUMS` together. The checksum
   inventory covers dependency inputs, notices, verifier and bundled browser
   files; it excludes the checksum file itself. Review the generated diff.
7. Test the selected source/artifact distribution independently of development
   state. Publish only after the separate release review. Document required
   operator action and the supported platform scope.

## Recovery and publication

A dependency change must not implicitly stop session owners. Preserve settings
and target identities while switching the dashboard service. Before claiming
upgrade/rollback support, test selecting the old application/environment and
restoring any changed persistent data format. A tested general upgrade/rollback
workflow is not yet provided.

New release provenance describes the public source and artifacts actually
selected for that release. Preserve historical development evidence separately;
do not rewrite old hashes to make them describe new files. Do not publish a
private development repository's history or local installation receipts.
