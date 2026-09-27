# Project files: browsing, viewing and transfer

Research and implementation plan, reviewed 2026-09-23. The in-app read-only browser
has location labels, navigation, filtering, copy controls and a wider view;
see [the user guide](../getting-started.md#browse-project-files-without-starting-a-session).
Native file-manager opening, portal shortcuts and transfers remain proposed.
Source tests do not establish live browser or cluster acceptance for these changes.
See [current status](../status.md) for product limits.

## Recommended product shape

Keep **Browse files** beside the selected project's terminal. Show the machine, project
folder and whether that folder is host storage or container storage. Provide
breadcrumbs, a parent-folder action, refresh, file sizes and a text preview.
Opening a folder must not start an agent or a compute job.

Offer separate actions with concrete names:

| Action | Meaning | Suggested priority |
| --- | --- | --- |
| Browse project files | Read the selected folder inside the dashboard | Improve the existing bounded reader first |
| Open in Finder / file manager | Open the local project folder in the dashboard server computer's desktop application | Queued; needs a reviewed local-only action |
| Open cluster file portal | Open a configured institutional web portal in another tab | Optional connection setting; verify that site's route before supporting a project-folder deep link |
| Download / Upload | Transfer explicitly selected files | Separate implementation with progress, cancel, limits and failure handling |
| Open container terminal | Start a shell in a defined container/allocation | Separate terminal feature; a file browser does not imply a shell or new allocation |

The existing backends already expose directory listing and UTF-8 text reading.
The readers cap text at 64 KiB, limit directory results to 500 entries, and reject
several unsafe paths and file types. These are bounded previews, not a complete
filesystem manager. Local and remote implementations differ in their truncation
and error behavior; a unified UI must report those differences honestly.
Relevant code: [local reader](../../src/botainer_dashboard/workspace.py),
[host reader](../../src/botainer_dashboard/host_backend.py), and
[remote helper](../../tools/remote_botainer_helper.py).

The in-app browse/preview improvements are the immediate implementation scope.
The native file-manager opener, portal link settings and general file transfers
remain planned. A native opener needs an authenticated local-only operation that
resolves the selected project through backend directory validation and invokes
one fixed supported opener. It must not accept arbitrary command arguments,
reuse a generic subprocess endpoint, or treat an untrusted path as a `file:` URL.
It must explain that it opens on the server computer, which can differ from the
computer running the browser.

## Remote choices

**Use the existing SSH route for an integrated browser.** A fixed remote helper
can list and read files through the same authenticated route used for inventory.
The helper must bind each request to a registered project and validated relative
path. This design needs neither X11 nor a remote HTTP server. It is our proposed
extension of the current reader, not a general remote desktop.

**Use a cluster's existing web portal for fuller file management.** Open OnDemand
has a Files application and supports configurable favorite paths. Its
administrators can disable uploads/downloads and restrict accessible paths.
Upstream documentation gives approximately 10.7 GB default transfer limits, but
site configuration can differ; do not present that number as a tested site limit.
[Open OnDemand file customization documentation](https://osc.github.io/ood-documentation/latest/customizations.html#add-shortcuts-to-files-menu)
and [transfer limits](https://osc.github.io/ood-documentation/latest/customizations.html#set-upload-limits).

A portal link should open in a separate tab and retain the portal's own login,
MFA and VPN requirements. An authenticated dashboard SSH connection does not
establish browser authentication to a different service. Store a reviewed HTTPS
portal origin in machine settings, never credentials. Do not infer a portal from
an SSH hostname or embed it in an iframe. Show a copyable remote folder path if a
tested deep link is unavailable. These are dashboard design recommendations.

**SFTP is the standard file-transfer route over SSH.** It supports the SSH
configuration, keys and jump hosts. Modern OpenSSH `scp` also uses SFTP by
default; older installed versions can differ. No X11 is involved.
[OpenSSH sftp manual](https://man.openbsd.org/sftp) and
[OpenSSH scp manual](https://man.openbsd.org/scp).

SFTP can benefit from an existing SSH multiplexing configuration. Connection
sharing is conditional: without a usable control socket SSH normally attempts a
new connection. Dashboard transfers must use a bounded noninteractive attempt
and report **Authentication required** with reconnection instructions rather
than hanging on an invisible prompt. A dropped connection must not cause a
silent overwrite or automatic retry of an uncertain completed write.
[OpenSSH ControlMaster and ControlPersist documentation](https://man.openbsd.org/ssh_config#ControlMaster).

An existing graphical SFTP application is a reasonable user-selected option.
Do not assume opening an `sftp:` URL will work on every desktop or install a
client automatically. Apple's Finder documentation describes connecting to file
servers such as SMB; Apple's Terminal separately documents SFTP connections.
An SSH login alone does not establish a Finder-mounted share.
[Apple file-server connections](https://support.apple.com/guide/mac-help/connect-your-mac-to-shared-computers-and-servers-mchlp1140/mac),
[Apple Terminal remote connections](https://support.apple.com/guide/terminal/connect-to-servers-trml1018/mac).

**Do not make SSHFS a prerequisite.** It adds filesystem software and mount
management. Upstream SSHFS describes limited maintainer capacity and known
issues. Keeping it optional avoids making desktop mounts part of dashboard
reconnection and distribution support.
[SSHFS upstream status and requirements](https://github.com/libfuse/sshfs).

For large datasets, link to a site's existing transfer service, such as Globus,
instead of streaming everything through the dashboard. Globus requires suitable
collections and its own authorization; access to an arbitrary SSH host is not
sufficient. Local collection setup can require additional software and approval.
[Globus file-transfer tutorial](https://docs.globus.org/guides/tutorials/manage-files/transfer-files/).

## Viewing and uploading safely

The next reader should show plain text with line numbers, wrapping and a clear
size-limit message. Preserve **read-only** as an explicit label. Render filenames
and text as text, never project-provided HTML or script. Markdown, HTML, SVG,
notebooks and PDF need a separate preview review; do not load them as dashboard
application content. Plain `textContent` avoids HTML parsing.
[MDN textContent documentation](https://developer.mozilla.org/en-US/docs/Web/API/Node/textContent).

Before implementing uploads, define these product requirements:

- Explicit machine/project/destination confirmation; independent upload
  capability, retaining the dashboard's cookie and bearer protections.
- Streaming byte and file-count limits, bounded temporary storage, timeouts and
  visible progress. Quota or disk-full errors must preserve the original file.
- Project-bound, race-resistant path traversal; refuse symlink escapes and
  special files. Never turn a filename into shell syntax or parse `ls` output.
- Upload to a private temporary file in the destination directory, then commit
  atomically. No overwrite by default; replacement requires a checked revision
  and explicit confirmation. Verify completion before reporting success.
- No automatic archive extraction, execution, permission escalation or changes
  to dashboard helpers, agent credentials or configuration controls.
- Cancellation and disconnect receipts; distinguish failed, partial and
  completion-unknown transfers. Retrying must not silently duplicate or replace
  a file. Shared cluster storage and node-local storage must be distinguished.

These are proposed dashboard requirements. They apply OWASP's broader advice on
authorization, safe filenames, size limits, storage outside served application
content and hostile uploads.
[OWASP File Upload Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html).

## Acceptance before shipping expanded file support

Test filenames containing spaces, quotes, Unicode and option-like prefixes;
symlink swaps and moved project roots; huge directories and binary files;
permission errors, full disks and quotas; canceled and broken SSH transfers;
concurrent replacement; and account or project switching while a request is in
flight. No response may land in another project's view. Establish separately
what a supported remote site permits, including any transfer-node requirement.

The first implementation can remain dependency-free using the existing readers.
A later native local opener also needs no new package on supported desktops.
A robust SFTP integration needs a transport decision:
existing OpenSSH commands avoid a new library but require careful filename and
process handling; a protocol library would need dependency review and approval.
Do not label either option implemented until its end-to-end tests pass.
