# Validation gates

The [development guide](README.md) lists source and optional local PTY checks.
Use existing tools; missing prerequisites do not authorize installation.

1. Source gate: portable unit/browser/model tests, generated files, document
   links, dependency/asset integrity and checkout whitespace.
2. Local integration: isolated deterministic PTY and loopback service behavior.
   This does not start real Botainer sessions or prove live agent behavior.
3. Runtime acceptance: explicitly selected disposable local/remote projects,
   actual native prompts, agent credentials, verified owner continuity and cleanup.
   Keep machine-specific harness configuration and raw evidence private.
4. Distribution: exact selected source export, full privacy/editorial review,
   standalone candidate tests, artifact/license inventory, fresh installation and
   supported-platform acceptance.
5. Publication: independent history and reviewed public identity, exact public
   tree, destination, visibility and artifact hashes. Export never implies push.

Track failed or untested gates in [status](../status.md) and private evidence.
Do not report synthetic helper success as a complete real-session result.
