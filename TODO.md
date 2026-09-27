# Release roadmap

This list tracks portable product work. Real-system evidence and internal
handoffs belong in private development records. The numbers below indicate
roadmap order; they are not stable issue identifiers.

1. Finish terminal recovery and control availability: retain original owner
   identity across viewer/sign-out/SSH failures, reconcile uncertain cleanup,
   and qualify safe adoption of externally started sessions. Do not create a
   replacement agent to conceal a failed attachment.
2. Qualify the complete local and remote lifecycle in a browser: native launch
   prompts, multiple sessions, disconnect/reconnect, stop, and allocation-wide
   cancellation. Keep observed state separate from permission to control it.
3. Deliver a clean-recipient installation workflow, packaging metadata and
   platform-specific dependency selections; test on fresh hosts.
4. Consolidate contextual help and setup troubleshooting; unify the SSH setup
   probe with runtime connection behavior.
5. Test project moves/deletions, large inventories, attention indicators,
   configuration validation and machine removal/re-enabling.
6. Design bulk stop with explicit target review, verified resource identity and
   shared-allocation warnings; never equate it with sign out or closing views.
7. Establish public release metadata, vulnerability reporting, CI and dependency
   maintenance, then qualify a release with independent public history. Keep
   external code contributions paused for the limited alpha; review and publish
   contribution terms before accepting patches.
8. Finish [host-agent profiles](docs/host-agents.md): guided Claude/Codex setup,
   migration for broader configuration changes, verified account selection,
   and clearly separate discovery from permission to control external sessions.
   Preserve the no-container warnings in compact, focused and offline views.
9. Expand [project file tools](docs/design/file-browsing.md): a validated
   native folder opener, optional cluster portal links, consistent large-directory
   handling, richer safe previews, then bounded downloads and explicit uploads.
   The current reader is read-only; transfers need conflict, quota, cancellation
   and interrupted-write tests.
10. Add [terminal-only operations](docs/design/terminal-only.md) as
    distinct capabilities: new container shell, additional shell in an existing
    container, compute-allocation shell, and visibly marked host/login shell.
    Preserve native consent, exact ownership and cleanup; qualify each route.
11. Support regular SSH servers alongside scheduled clusters: remote host agents,
    Botainer with Docker, and Botainer with standalone Apptainer. Separate SSH
    connection settings, installation selection and scheduler requirements;
    qualify launch, persistent ownership, reconnect and Stop for each route.
    Keep host-agent warnings explicit. Existing Slurm support does not establish
    support for these server routes.

[Current status](docs/status.md) distinguishes implemented behavior from release
qualification. Research-only ideas are not release prerequisites.
