# Status and limits

The application has a Python control service, browser terminal interface,
connection setup, inventory, native Botainer launch prompts, verified terminal
attachment, and supported per-target stop operations. Dashboard `0.1.0a1` is an
alpha candidate targeting separately installed Botainer `0.1.0a5`.

The candidate wheel includes the application, browser assets and help. The
installed `botainer-dashboard` command provides check/start/status/stop/pair,
with private data outside the application environment. A read-only offline
installer plan and explicit hash-bound apply route passed an isolated macOS
arm64 installation with CPython 3.13.15 and the 14 reviewed dependency wheels.
The installed service passed startup, pairing, authenticated Help/assets,
credential-boundary checks, status and shutdown from an unrelated directory
with read-only application files. No existing machine profiles were imported.
This is not new-host, real-session or published-release acceptance.

The distribution excludes development proof backends from the wheel and keeps
their shared production utilities separate. Its license inventory includes the
application/browser terms and artifact-specific supplements for identified
embedded dependency components. Bundle checks bind the exact source, application
wheel, 14 unchanged dependency wheels and installed notices; they refuse unresolved
notice findings. See [third-party coverage](../third_party/README.md#offline-dependency-bundle-review).
Every rebuilt wheel still needs its own artifact and installation checks.

A separate local trial exercised the dashboard's native Botainer CLI path
against a selected source installation. Project initialization with a stock
template succeeded. An explicit agent selection preserved native preflight
warnings and confirmation; declining the launch produced a not-dispatched
receipt and started no container. This establishes setup and safe refusal for
that trial. Accepted real-agent launch, terminal interaction, original-owner
reconnect and stop/cleanup with the final released artifact pair remain pending.

Source tests cover synthetic ownership, configuration, browser state and failure
cases. Optional integration tests exercise deterministic local PTYs and loopback
HTTP/WebSocket behavior. These checks do not establish that every Botainer
version, SSH site, operating system or real agent works.

## Remaining release gates

- Terminal recovery still has control-availability and uncertain-cleanup cases.
  Reconnection must preserve the original owner; an external running process
  is not automatically adoptable. Unattended recovery is not fully qualified.
- A lost browser or SSH connection can leave a live agent. Retained inventory
  and output are observations, not proof of current permission or job completion.
- New-host prerequisite setup, live-owner upgrade/rollback and publication
  acceptance remain pending. The offline candidate uses existing CPython 3.13.15+ within
  3.13 on macOS arm64 and 14 exact dependency wheels. Linux installation needs a
  separately reviewed artifact set and acceptance. Prepared source checkouts
  retain their original local environment and state without automatic migration.
- Native Botainer wheel/pipx selection is candidate support pending live
  onboarding and session acceptance. Installing the dashboard does not install
  or replace Botainer, agents or container images.
- The final dashboard wheel paired with the final Botainer `0.1.0a5` artifact
  must pass native preflight, consent, accepted real-agent launch, reconnect and
  stop tests locally. Any advertised cluster route needs its own acceptance.
  Candidate source/metadata checks and synthetic adapter tests do not establish
  that acceptance. Existing dashboard compatibility helpers remain in use;
  see the [entry-point inventory](engineering/security-entry-points.md).
- Remote support is tied to the implemented Botainer Slurm/HPC adapter and
  verified persistent ownership. A generic SSH host or remote Docker server is
  not automatically supported.
- Native Windows hosting, additional cluster routes, browser forwarding, and
  mobile access are not qualified.
- Chrome navigation, machine setup guidance and rendered Help have live UI
  coverage, including a desktop-size responsive check. This does not establish
  an accepted real-agent launch or terminal recovery in the final installed
  artifact. Safari and Firefox acceptance remains pending.
- Public repository identity, enabled Issues and monitored private vulnerability
  reporting, and the exact release artifacts still need publication review.
  Reports are welcome; external code contributions are paused. See
  [reporting policy](../CONTRIBUTING.md) and [Security](../SECURITY.md).
  A clean source preview is not a released build.

Delayed, failed and stale observation presentation has synthetic test coverage.
The dashboard retains last reported state/time, keeps its 15-second freshness
limit and disables unavailable controls. This does not fix slow inventory probes
or qualify live behavior under every load or network failure. Connected-terminal
observation warnings use a compact note; no new control permission follows from
historical state.

Opening the dashboard after inactivity starts fresh inventory checks. An expired
successful cache shows **Checking connection** during that new check, with
last-known projects retained and stale controls disabled. A check exceeding its
deadline shows delayed/unavailable; an actual recorded failure stays visible
while retrying. This presentation does not extend the control freshness limit.

Connection failures now distinguish recognized Botainer verification changes
from dashboard restart requirements and preserve specific SSH hints. Unavailable
inventories are labeled potentially incomplete. Connection summaries and machine
settings show recognized causes and recovery guidance separately from whether
a profile is loaded. Recognized remote helper refusals distinguish changed source,
launcher, interpreter, plugins and unsafe installation permissions from SSH
authentication and transport failures. Unknown causes remain explicitly
unidentified; not every helper or scheduler failure has a specific diagnostic.

Saved local and remote Botainer connections offer **Review Botainer update…**
for an already installed software update. This is a read-only inspection and
explicit trust review followed by a separately confirmed save for restart. It
does not install software, change connection scope, approve new local hooks or
activate itself. Saving reinspects the candidate and checks the saved revision.
Remote history remains archived under its previous identity; updating does not
migrate terminal authority. Restart and fresh browser pairing remain explicit.
This recovery flow has synthetic source and browser-state tests. Its complete
live upgrade workflow, including existing session owners, is not yet qualified
by those tests. See [the update procedure](maintenance.md#update-botainer-or-the-dashboard).

See [support matrix](setup-support-matrix.md), [session behavior](sessions.md),
[roadmap](../TODO.md), and [publication gates](engineering/publication.md).
