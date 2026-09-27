# Dependencies

The runtime uses four direct Python libraries and three browser terminal
packages. Their selected transitive closure contains **14 Python packages**.
Versions and artifact identities are recorded in
[requirements](../requirements/README.md). The [offline installer](installation.md)
uses that exact platform-specific set; it does not perform dependency resolution
or fetch updates.

| Direct dependency | Selected version | Purpose |
| --- | --- | --- |
| FastAPI | 0.141.1 | HTTP endpoints and request models |
| Uvicorn | 0.53.0 | ASGI server |
| websockets | 17.1 | WebSocket transport support |
| Pydantic | 2.13.5 | Structured request and data validation |
| `@xterm/xterm` | 6.0.0 | Browser terminal rendering |
| `@xterm/addon-fit` | 0.11.0 | Terminal geometry fitting |
| `@xterm/addon-search` | 0.16.0 | Terminal scrollback search |

The Python closure also pins annotated-doc, annotated-types, anyio, click, h11,
idna, pydantic-core, starlette, typing-extensions and typing-inspection. The
metadata records exact versions, declared dependencies, wheel URLs, SHA-256
hashes and upstream license metadata for every package.

## Bundled assets and external tools

Browser JavaScript/CSS and original MIT notices are included under
`frontend/vendor/`. Their upstream archive and per-file integrity are recorded in
[third-party notices](../third_party/README.md). No npm build, CDN or browser asset
download is required to use these files. `frontend/package.json` only declares
ES-module scope; it is not an installation manifest.

Botainer, container engines, agents, SSH, tmux, Screen and scheduler commands are
external tools used by particular configured workflows. Their presence and
compatibility are checked separately from the dashboard's Python libraries.
Local container sessions require Docker and tmux on the service computer; the
selected Botainer interpreter must be Python 3.11 or newer. Remote previews also
need separately qualified SSH/Slurm/Apptainer/Screen setup. See the
[prerequisite table](installation.md#prerequisites-and-release-inputs).

The source selection includes no Python wheels. An assembled offline release
adds the dashboard wheel and 14 pinned dependency wheels under `release/`, with
its own full inventory and checksums. Python wheels retain their upstream
license notices; see [third-party components](../third_party/README.md).
Neither form supplies an interpreter, Conda environment, Botainer installation,
Docker/tmux or agent credentials.

The current wheel selection targets normal CPython 3.13.15+ within 3.13 on macOS
arm64. It was exercised in the development runtime and an isolated offline
installation on that computer. The installed package smoke does not qualify
new-host setup or a real Botainer agent lifecycle. Other host platforms need
their own wheel selection and testing. A hash is an identity check, not a
security certification. An empty registry advisory list is not proof that a
package is safe.

## Checking and updating

With an existing Python interpreter:

```sh
python3 requirements/verify.py
```

This offline check validates the input inventory, wheel pins, package agreement
and every bundled asset/license file. It does not install, contact registries,
audit current vulnerabilities or authenticate the publisher.

Follow the [dependency update process](engineering/dependency-updates.md) for
new versions. Changing package pins, obtaining artifacts, installing packages
and publishing a release are separate steps. Application startup does none of
these automatically. Current release limitations are in [status](status.md).
