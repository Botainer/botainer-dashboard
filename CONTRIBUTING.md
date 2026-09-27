# Reports and contributions

This limited alpha welcomes bug reports, usability feedback and documentation
corrections as reports. **External code contributions are paused.** Please do
not open pull requests or submit code, test or connection-profile patches yet.
There is no contributor agreement to sign for this alpha and no signing bot.
The [CLA reference](CLA.md) is included for review and has not been adopted for
Botainer Dashboard. Please do not sign it. Final contribution terms and signing
instructions will be published before accepting patches.

## Report a problem

Use **Issues → New issue** in the public GitHub repository identified by the
release announcement for ordinary bugs and feedback. Issues are public. If you
received a private preview before that repository is available, reply privately
to the person who supplied it.

Use the short [alpha testing report template](docs/alpha-testing.md#report-a-result).
Describe what you expected, what happened and how to reproduce it with invented
project names. Documentation reports should identify the page and the unclear
step. You do not need to diagnose the code or prepare a patch.

Do not upload logs, terminal transcripts, real project inventories, connection
profiles, pairing codes, credentials or private paths. If a screenshot is useful,
remove private names, addresses and terminal content first. For a possible
security defect, follow [private security reporting](SECURITY.md#report-a-security-problem)
instead of opening a public issue.

The public repository address, enabled Issues and working private security
reporting must be confirmed before public distribution. These instructions do
not mean that those GitHub settings have already been configured.

## Testing and source review

[Alpha testing](docs/alpha-testing.md) gives a short product workflow and the
current test scope. [Status](docs/status.md) distinguishes implementation from
live qualification; a successful package install alone does not establish that
an agent can launch, reconnect and stop correctly.

For source review, see [architecture](docs/architecture.md) and
[development checks](docs/engineering/README.md). With the documented tools
already installed, `python3 tools/check.py` runs the offline checks without
installing software. Real machine trials require their own scope and must not
publish credentials or user transcripts. A report about a failing check is
useful even while patch contributions are paused.

## Release source

Public releases use an explicitly reviewed source export with independent Git
history. Private settings, transcripts and operating records are excluded.
Future accepted public changes must also be retained in the development source
before the next release. Public updates should preserve public Git history;
see the [publication process](docs/engineering/publication.md).

## License

Botainer Dashboard's own code is released under [Apache-2.0](LICENSE).
Bundled third-party components retain [their own terms and notices](third_party/README.md).
Pausing project contributions does not change those licenses.
