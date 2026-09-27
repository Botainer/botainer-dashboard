# Try the limited alpha

The first alpha is for a small number of testers using their own computer and
existing Botainer installation. Start with a disposable project and stay present
for launch prompts and cleanup. It is not yet qualified for unattended recovery.

## Scope

The intended first route is **macOS arm64, the reviewed offline bundle, normal
CPython 3.13.15+ within 3.13, Chrome, and local Docker with Botainer alpha5**.
Local tmux and a Botainer interpreter running Python 3.11 or newer are also
required. The installer supplies the dashboard and its pinned Python libraries;
it does not install Botainer, Docker, tmux, agents or images.

An isolated offline installation and authenticated service checks have passed on
an existing computer. A separate native CLI trial initialized a project with a
stock template, preserved preflight warnings and confirmed that declining a
launch dispatched no container. **Accepted real-agent launch, reconnect and
cleanup with the final exact dashboard/Botainer alpha5 artifact pair are still
pending**, as is a fresh visual browser check of that candidate.

The procedure below is a test plan, not a claim that every step has passed.
Check [Status](status.md) and the [support matrix](setup-support-matrix.md) for the
release you received. A maintainer must record the exact-pair acceptance before
advertising the route as qualified or inviting unassisted testers. Early trials
of the pending gate should be coordinated privately.

Remote clusters and initial host-agent profiles are maintainer-assisted
previews. A working SSH login does not qualify a new cluster. Safari, Firefox,
Linux service installation, Windows, mobile access and live-session upgrades
remain outside the qualified first route until separately tested.

## A short first trial

1. Follow [Installation](installation.md), including its read-only plan and
   reviewed apply step. Keep the release version and artifact checksum. Start
   the installed executable with `botainer-dashboard start --open`; use its full
   path if needed. Pair using the command printed by the service.
2. Follow [Getting started](getting-started.md) to add the intended local
   installation, review its folders and hooks, save and restart. Confirm the
   machine and project paths. Existing Botainer registrations should appear
   without adding every folder again.
3. Use **Add project** for a clearly named disposable project in an approved
   parent folder. Complete Botainer's native setup prompts and inspect
   **Project config**. For this local trial, confirm `nudge` is disabled: the
   dashboard's local tmux owner does not yet support Botainer's nested Screen
   wrapper. Use an already prepared image and agent sign-in. Leave other
   projects' settings alone; qualified cluster sessions require `nudge` enabled.
4. Choose **New session** and read the native launch output. First decline a
   confirmation and verify that it starts no workload. Then start one intended
   session and accept the native prompts. If an outcome is uncertain, inspect
   the original launch console and session state before trying again.
5. Give the intended agent a harmless prompt in that test project. Check typing,
   selection/copy/paste, terminal text size, window resizing and sidebar
   navigation. **Disconnect view**, then reconnect to the same session. Confirm
   its identity and continuing conversation; do not use **New session** as a
   substitute for reconnect.
6. Use **Stop session** for that test session, read the affected target and
   verify its stopped state. Check the native Botainer status if cleanup is
   uncertain. Do not stop other sessions to simplify the test.
7. Finish all pending launch/setup prompts, then run `botainer-dashboard status`
   and `botainer-dashboard stop` with the same data directory. Stopping the
   dashboard is separate from stopping a session. Keep the installed environment
   and recovery records until no surviving terminal owner depends on them.

Report a failure at the step where it happened. There is no need to exercise
network loss, laptop sleep, cluster cancellation or an upgrade during this first
trial. Those require separate recovery and workload-scope checks.

## Report a result

Use public **Issues → New issue** in the repository named in the release for
ordinary bugs, unclear instructions and usability feedback. If no public
repository is available yet, reply privately to the person who supplied the
preview. Code contributions are paused; a concise report is enough.

```text
Dashboard version and release artifact/checksum:
Botainer version and installation type (source / wheel / pipx):
OS / architecture and browser version:
Route (local container / qualified remote / host agent):
Step or action:
Expected result:
Observed result and sanitized error message:
Reproduction with invented names:
Can you still reach or stop the original session? (yes / no / unknown)
Checks skipped:
```

Do not attach raw logs, terminal transcripts, machine profiles, pairing codes,
credentials, real project names or private paths. Screenshots can contain the
same information; sanitize them before sharing.

For a suspected security problem, open **Security → Advisories → Report a
vulnerability** in the repository named in the release. This is a private form
and requires the repository owner to enable it. If the repository or form is
unavailable, contact the person who supplied the preview privately and ask where
to send the report. Never put exploit details in a public issue. The source
release also contains the full [security policy](../SECURITY.md#report-a-security-problem)
and [reporting policy](../CONTRIBUTING.md); those source-file links are not
available in the dashboard's Help reader.
