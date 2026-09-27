# Setup and platform support

Dashboard `0.1.0a1` is an alpha candidate targeting Botainer `0.1.0a5`.
Implemented code, offline test coverage,
controlled local/remote exercises and independent release qualification are
separate claims. No general recipient installation flow is release-qualified.
The [status page](status.md) records current implementation limits. The intended
limited-alpha path is one macOS arm64 service, an existing local Docker/Botainer
installation and a tested browser. Remote and host-agent previews need
maintainer assistance; their presence in the interface is not a claim that an
arbitrary new installation is qualified.

| Route | Current scope |
| --- | --- |
| Prepared macOS arm64 service | Existing development runtime and automated local service/terminal coverage. Separate from recipient installation and final native-version acceptance. |
| Dashboard wheel + offline installer, macOS arm64 | Fresh isolated environment on an existing computer passed offline install, packaged startup/pairing, authenticated Help/assets, status and shutdown with CPython 3.13.15 and exact reviewed wheels. Read-only application files and unrelated working directory exercised. New-host and real Botainer lifecycle acceptance remain separate. No automatic Botainer installation. |
| Local Botainer source installation | A native CLI trial initialized a disposable project with a stock template, preserved preflight warnings for an explicit agent selection and confirmed decline without container dispatch. Requires local Docker and tmux, and Botainer Python ≥3.11. Accepted real-agent launch, original-owner reconnect and cleanup with the final artifact pair remain pending. |
| Remote Linux SSH/Slurm with Botainer HPC support | Maintainer-assisted preview. Constrained shell workflows have been exercised; attach accepts only a reviewed Screen binary/system configuration and requires `nudge` enabled with no personal `.screenrc`. SSH or a successful machine probe does not qualify another site. See [cluster prerequisites](ssh-setup.md#before-starting-a-cluster-session). |
| Generic remote Linux/Docker server | A working SSH alias alone is insufficient. The current ordinary remote-control adapter targets the Botainer HPC route; generic remote Docker control is not qualified. |
| Key-only SSH account | Uses standard OpenSSH authentication; independent end-to-end onboarding for arbitrary accounts remains unqualified. |
| Password/MFA with shared SSH connection | Documented terminal-login route. No browser credential storage or login prompts; site-specific behavior still requires testing. |
| Bastion / ProxyJump / other SSH options | Standard SSH config may provide transport; not broadly qualified with the dashboard. |
| Linux service host | POSIX implementation target and offline coverage; no independently qualified fresh install across distributions/architectures. |
| Native Windows service | Unsupported by the current POSIX terminal backend. |
| WSL service or Windows browser through a tunnel | Proposed routes. Dependency, SSH-agent, loopback-origin and terminal behavior remain untested. |
| Native local host agents | Separate opt-in profiles, initially prepared with maintainer help; no self-service profile generator. Requires existing agent executables and tmux. Agents run without containers. |
| GUI machine preparation | Saved-selection, preparation and authenticated endpoint tests exist. Checks are read-only; saving requires restart and does not establish full session acceptance. |
| Native wheel/pipx Botainer `0.1.0a5` | Candidate support selects the existing launcher/Python (≥3.11) and pins package, metadata and entry point. Source profiles remain supported. Live installation/onboarding/session acceptance is pending; do not bypass preparation checks. |
| Dashboard pipx distribution | Intended to reuse the wheel and installed CLI; recipient installation and upgrade acceptance are deferred. |

The current prepared Python artifact selection is platform-specific. Bundled
browser assets and source portability do not establish that the Python runtime
or every native tool will work on another target. See the
[operator guide](agent-setup-guide.md) before preparing a new environment.

## What a new supported route needs to pass

### Browser checks

Chrome has a controlled production-UI check for pairing, interactive echo-terminal
input, Unicode output, observed bells, project/session switching, pane sizing and
disconnected view restoration. This is a browser/UI exercise, not real-agent or
remote-workload acceptance. A fresh visual check of the final candidate is still
pending; the local native setup/decline trial did not include browser interaction.
Safari and Firefox remain pending manual testing.

In each browser, open the service's printed URL and request a fresh pairing code.
Use an authorized test session to check typing, selection and copy/paste, Unicode,
terminal resizing, switching views, and narrow-window layout. Reload and confirm
restored tabs require **Connect**; reconnect to the same session. Record browser
version, failures and skipped checks. Clipboard permissions and browser shortcuts
can differ; a fallback to the browser's Copy/Paste command should remain usable.

### Runtime and installation checks

1. Start from documented prerequisites, the exact reviewed wheel/installer and
   platform dependency artifacts, with no undisclosed development settings or
   credentials. Verify plan, approved apply, packaged assets and execution from
   outside the source directory. Keep private data outside the environment.
2. Add the intended machine/account, authenticate, review installation and
   new-project setup parents, save and restart. Verify its inventory and
   offline/error states; existing registered project locations need no second
   parent-folder allowlist.
3. In an authorized disposable project, exercise native setup and launch prompts.
   Decline without dispatch, then accept one intended launch.
4. Test actual input, resize, viewer close/reopen and reconnect to the same owner.
   Test the real agent and its credential-helper behavior before claiming support.
5. Stop/cancel exactly the intended workload and confirm cleanup. Shared scheduler
   allocations require clear explanation of the affected work.
6. Remove/re-add the connection without deleting projects, revoking credentials
   or stopping unrelated work. Preserve owner recovery identity.
7. Exercise broken authentication, source/tool changes and the repair flow.
   Record limitations for sleep, network interruption, uncertain cleanup and
   bounded terminal history rather than inferring success.

Record versions, artifact identities, test outcome and untested routes privately
when the evidence contains real accounts or project data. Fresh installation,
upgrade, rollback and publication review are additional release gates.
Upgrades use a fresh environment and the same data directory, preserving older
environments while live owners depend on them. No automatic data migration or
live-owner upgrade compatibility should be inferred from package installation.

[Getting started](getting-started.md), [startup and repair](start-dashboard.md),
[SSH setup](ssh-setup.md) and [sessions](sessions.md) describe the operator workflow.
