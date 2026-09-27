# Disposable Botainer terminal fixture

This repository-authored test fixture is not an AI agent or a general shell. It
accepts only `identity`, `size`, `echo TEXT`, and `quit`; input is never evaluated.
The manifest describes a test plugin, not an upstream plugin endorsement or a
production installation recommendation.

Use only in a disposable Botainer test environment. Mount this directory
read-only at `/mnt/dashboard-fixture`, select an already available container
image by its full `sha256:` identity, and disable network access. The manifest's
image tag is illustrative; it must not cause an image pull as part of a test.

The project, writable home, package cache and scratch space must belong to the
disposable test environment. Do not mount credentials, real project folders or
other user data. No host hooks, extra plugins, port forwards or sidecars are
needed. The fixture itself needs only `/bin/sh` and the standard process and
terminal utilities used by `terminal.sh`.

## Test procedure

1. Inspect the effective runtime configuration and verify the image, network,
   mounts, entrypoint and empty credential scope before launching.
2. Start once and record the full session identity, container identity and start
   time. Verify that stdin and a TTY are enabled.
3. Attach through the Botainer terminal route. Check the fixture identity,
   send `echo TEXT`, resize the terminal and verify `size`.
4. Detach the viewer, then reconnect. Repeat after losing the viewer and after a
   dashboard service restart. The underlying process and container must stay the
   same; a replacement launch does not count as reconnection.
5. Stop by the exact session identity, independently confirm termination and
   check session bookkeeping. If an outcome is ambiguous, investigate it without
   automatically launching a replacement.

The fixture requires a Botainer route that retains stdin and the TTY across
viewer disconnection. Test the adapter's actual behavior; this directory does
not patch or qualify Botainer. A successful deterministic fixture run establishes
only that route's terminal behavior. Ordinary agents, credential helpers, remote
schedulers, network interruptions and laptop sleep need separate acceptance
coverage.

Keep transcripts, runtime identities, environment profiles and acceptance
receipts outside the public source tree. Synthetic offline tests belong under
`tests/`; machine-specific trial evidence belongs under `private/`.
