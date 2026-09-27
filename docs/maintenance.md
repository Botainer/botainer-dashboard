# Botainer maintenance

Use this guide to troubleshoot sessions, sign an agent in, rebuild an image,
or update the selected Botainer installation. The dashboard shows these
instructions; it does not run maintenance commands for you.

Three things are separate:

- **Botainer installation:** the copy of Botainer that starts your containers.
- **Botainer state folder** (called **state root** in settings): where that
  installation keeps settings, credentials and project/session records. Some
  project settings and credentials can live elsewhere.
- **Container image:** the prepared software environment used to create a
  container. Rebuilding it does not update Botainer or the dashboard.

## First, open the right terminal

For the Botainer tasks below, open your computer's Terminal application. Use the
machine and account shown in **Settings → Machines → Details & Botainer help**.
For a remote machine, connect to it with SSH first. Work in the affected
project's folder. The agent terminal inside a container is not the place to
maintain the host's Botainer installation.

The examples use `botainer`. If you normally use another command or alias for
this installation, use that instead; keep the rest of the command unchanged.
For example, `botainer doctor` means “run **doctor** using the selected copy of
Botainer.” Keep any environment settings your usual command needs.

If you have more than one installation, check which state folder the command
uses:

```sh
botainer where --state-root
```

This prints the selected folder path; it does not select a different folder. Compare it with **State root**
in the connection details. If the paths differ, use the correct Botainer
command before continuing.

## A session will not start or connect

1. If the machine is offline, restore the SSH connection first. Use
   [SSH troubleshooting](ssh-setup.md); rebuilding an image will not repair SSH.
2. Read the error in the dashboard's launch terminal. If Botainer is asking a
   question, answer it there.
3. In your own terminal, from the project's folder, run:

   ```sh
   botainer status
   botainer doctor
   ```

   `status` lists this project's sessions. `doctor` checks prerequisites and
   reports problems with suggested fixes. Follow the finding that matches the
   failure; there is no need to fix unrelated optional warnings.
4. If a session is already running, use [connection and recovery help](sessions.md)
   before starting another. **Controls unavailable** does not mean the agent
   stopped, and a successful doctor check does not guarantee that the dashboard
   can reconnect to its original terminal.

If the agent runs but cannot submit additional cluster jobs, run
`botainer hpc jobs-doctor` from that project's folder. It checks the project's
job-submission setup; it does not repair SSH or reconnect an existing terminal.

## The agent needs you to sign in

From the project's folder on the selected machine, run:

```sh
botainer auth status
botainer auth login --help
```

The first command shows the project's authentication setup and stored-login
status. The second explains how to sign in to a particular agent. For example,
for Codex with the default authentication profile:

```sh
botainer auth login --agent codex
```

Use your agent's name. If the project uses a named authentication profile or a
shared login, use the matching options shown by the help and status output;
do not assume the default command updates that login. Follow the login prompts,
then run `botainer auth status` again and retry the intended session. A stored
credential can still be expired; the agent's response determines whether it
works. Dashboard pairing and SSH login are separate from this agent login.

## An image is missing or needs rebuilding

Run `botainer image list` to see the available agent images. An image build can
download software and use substantial disk space. Choose the image for the
agent you intend to run; rebuilding every image is usually unnecessary.

For a Docker installation, this example builds the Codex image:

```sh
botainer image build agent-codex --runtime docker
```

For an Apptainer cluster installation, the corresponding command is:

```sh
botainer image build agent-codex --runtime apptainer
```

Replace `agent-codex` with the required plugin name from the listing or Botainer's
error. On a cluster, follow the site's instructions about where image builds
are permitted. A build runs where you type the command; it is not submitted
to the scheduler. Do not replace an image file used by active jobs. Consult
`botainer image build --help` before choosing to overwrite an existing image.
After a successful build, check
`botainer image list` again and retry the launch. Existing sessions do not switch
to the new image.

## Update Botainer or the dashboard

**To update Botainer:** follow the update instructions for the way that copy was
installed, on its own machine. A source checkout and a packaged installation
have different update procedures. Record the command and state folder first so
you do not accidentally update a different copy. Afterward, run `botainer doctor`
there. **Review Botainer update** means the saved verification no longer matches
the installation. Restarting the dashboard alone does not renew it. The dashboard
keeps an exact record of the selected software so an upgrade cannot silently
select different executable code. A working SSH login does not clear this check.

Botainer's bundled plugins and the copies installed in its state folder are
separate. Updating its source checkout or package does not establish that both
sets match. Use that Botainer version's documented plugin inspection/update
procedure and review local customizations before replacing installed plugins.
Keep credentials, user configuration and active sessions intact. The dashboard
does not synchronize plugins for you. Its supported version and remaining
internal dependencies are described in [Botainer compatibility](architecture.md#botainer-compatibility).

To review an already installed update, open **Settings → Machines → Review
Botainer update…** on the saved connection:

1. Check the saved Python and Botainer import folder. For a remote connection,
   also check the launcher. Keep paths that still select the intended installation;
   enter new absolute paths only when it moved or you deliberately changed copies.
2. Use a Python interpreter you trust: the read-only check runs that interpreter
   through the saved connection. Acknowledge it, then select **Inspect Botainer
   update**. No software is installed and no sessions are started. Failed checks
   explain what needs attention; they do not offer a bypass.
3. Compare the saved and inspected paths. Review the code you installed,
   including its bundled plugins, before confirming trust. Previously selected
   installed plugins and approved local hooks must retain their exact bytes;
   changes to those require the broader connection setup review.
4. Select **Save update for restart**. The dashboard inspects again and refuses
   to save if the files or saved settings changed since your review.
5. Finish or reconcile pending native launch/consent prompts. Restart the dashboard
   using the same data directory and pair the browser again. Saving does not
   restart it for you or prove the new installation's launch, reconnect and stop
   behavior.

This review preserves account, project-folder, state, control and container-tool
settings. It does not change those settings or grant new hook approval. An
unsupported profile or a broader change needs connection setup; retain the old
profile and operation records instead of deleting them to dismiss a warning.

**Remote history:** a replacement remote profile has a new history identity.
Previous dashboard launch receipts remain archived; the dashboard does not
transfer their terminal authority to the new installation. Native Botainer
inventory is checked again after restart. Do not assume that every historical
dashboard tab can reconnect through the new profile. No session or job is stopped
by saving the update. Finish active work or verify its independent owner before
planning a service restart.

The GUI's **Review agent update…** is a separate action for host Claude/Codex
executables; it does not update Botainer.

If the missing or changed installation prevents the dashboard from starting,
use [setup-only recovery](start-dashboard.md#reconfigure-the-dashboard-after-a-botainer-installation-changes)
to open the saved connection settings without loading runtime controllers.
The same update review works there even when the old source or Python is gone.

While a connection cannot be verified, its project list may be incomplete,
especially after a dashboard restart. Missing rows do not establish that projects
were deleted, and unknown activity does not establish that agents stopped.
**Dashboard restart needed** is a different diagnosis: the dashboard helper or
selected profile changed, and the service must load its current files. Machine
details distinguish these failures from SSH login, network and delayed checks.

**To check the dashboard itself:** on the computer running its service, use its
installed executable in your Terminal application, from any folder:

```sh
botainer-dashboard check
```

Use the full executable path if it is not on PATH, and the same `--data-dir` you
use to start the service. This checks the dashboard's local files and settings.
It does not update anything or contact Docker/SSH. `botainer-dashboard status`
checks the service itself; it is separate from a Botainer project's status.

There is no automatic dashboard updater. Follow
[Installation → Upgrade, rollback and removal](installation.md#upgrade-rollback-and-removal) to install an
approved new version alongside the old one, preserving private data and any
owners that still depend on the previous environment. A prepared development
checkout instead uses `python3 tools/dashboard.py check` from its source folder;
that source-only command is not needed for an installed alpha.

An entry marked **Host agent** runs an agent directly on that machine. For that
entry, use the agent's own sign-in and update instructions; container-image
commands do not apply.

If you need help, include the action, error and software revision. Remove
credentials, private paths, project names and terminal content before sharing a
support report. See [current status](status.md) for known limitations.
