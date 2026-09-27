# Runtime dependency inputs

This directory describes the dashboard's current dependency selection. It
contains manifests and hashes, not the wheel files or an interpreter. A reviewed
offline release bundle supplies matching wheels under `release/` and uses
[`tools/install.py`](../tools/install.py); follow [Installation](../docs/installation.md)
for its read-only plan and explicit apply steps. Botainer is a separate existing
installation selected by the operator; the dashboard installer never installs it.

| Input | Purpose |
| --- | --- |
| `dashboard-macos-arm64-py313.candidate.txt` | Fourteen exact Python package versions with one wheel SHA-256 each |
| `dashboard-macos-arm64-py313.metadata.json` | Dependency graph, selected wheel origins, license metadata and target |
| `frontend.package.json` | Exact versions of the three xterm browser packages |
| `frontend.metadata.json` | Upstream registry metadata and archive SHA-512 integrity |
| `manifest.json` | Current dependency scope and direct runtime dependencies |
| `SHA256SUMS` | Independent integrity inventory for these inputs and the bundled browser files |
| `verify.py` | Offline integrity and manifest-consistency check using the Python standard library |

The Python selection was exercised in the development runtime and a fresh
isolated offline installation on the same computer. It targets normal CPython
3.13.15+ within 3.13 on macOS arm64 and is **not a cross-platform lock**. The native
`pydantic-core` and `websockets` wheels are platform-specific. A different
architecture or Linux service host needs its own artifact selection and
acceptance. New-host onboarding and final Botainer session lifecycle remain
separate from the completed package smoke; see [status](../docs/status.md).

The source selection contains no Python wheels. The assembled offline bundle
contains the dashboard wheel and the exact 14 dependency wheels, plus
`release/MANIFEST.json` and `release/SHA256SUMS`. Their original notices remain
inside those wheels. No interpreter or Conda runtime is bundled, and the
installer does not resolve or download dependencies. It separately identifies
the `pip` bundled with the selected existing Python for venv bootstrap.

The four direct Python dependencies are FastAPI, Uvicorn, websockets, and
Pydantic. The remaining ten entries are their selected dependencies. Python
packages retain their upstream license notices when installed from their wheels;
their registry license metadata is recorded here for review.

The browser assets under `frontend/vendor/` are unmodified packaged xterm
JavaScript/CSS with their original MIT license notices. Their package and file
inventory is in [third-party notices](../third_party/README.md). Using the
application does not require npm or a browser asset download.

Run this read-only check from the source root:

```sh
python3 requirements/verify.py
```

The check requires an existing Python 3.10 or newer. It performs no installation,
network access, package import, or command execution. Hashes establish identity
against this inventory; they do not authenticate a publisher or certify safety.

For updates, select exact versions separately, review direct and transitive
changes, upstream security advisories, compatibility, platform artifacts and
licenses, then test a fresh isolated environment. Update source and artifact
inventories together only after review. Preserve the previous working
environment for recovery. Do not refresh dependencies as part of application
startup or silently run package-manager update commands.
