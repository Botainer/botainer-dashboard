# Third-party components

The application source uses the repository's Apache-2.0 license. Bundled browser
components retain their own MIT license and attribution; the application license
does not replace these notices.

The dashboard distribution declares `Apache-2.0 AND MIT` because it contains
both the application and these browser components. This describes the licenses
of different included files; it does not offer a choice between licenses or
relicense the application under MIT. The Python dependency implementations are
separate distributions and are not bundled inside the dashboard wheel.

| Component | Version | License notice |
| --- | --- | --- |
| `@xterm/xterm` | 6.0.0 | [Original MIT notice](../frontend/vendor/xterm/LICENSE) |
| `@xterm/addon-fit` | 0.11.0 | [Original MIT notice](../frontend/vendor/addon-fit/LICENSE) |
| `@xterm/addon-search` | 0.16.0 | [Original MIT notice](../frontend/vendor/addon-search/LICENSE) |

These packages are maintained in the [xterm.js repository](https://github.com/xtermjs/xterm.js).
Only packaged browser bundles, the terminal stylesheet, and original license
files are included. Both upstream script and ES-module bundles are preserved.
Source maps, upstream tests and build tools are excluded. Package scripts were
not executed to prepare these files.

[manifest.json](manifest.json) records each upstream package version, archive
URL/integrity and bundled file SHA-256. Archive integrity and every bundled file
were compared against the already-acquired upstream archive bytes. Keep the
notices with the bundles when redistributing them. The source distribution does
not include the third-party archive itself.

Python dependencies and their license metadata are listed in
[the dependency inputs](../requirements/README.md). The reviewed source selection
does not contain Python wheels. The **assembled offline alpha bundle does**:
`release/wheelhouse/` holds the exact 14 dependency wheels, including native
components where supplied by upstream. Original license and attribution files
remain inside each unmodified wheel and are installed with its distribution
metadata. Preserve the whole wheel when redistributing it.

### Additional attribution for FastAPI's Swagger UI HTML

The selected `fastapi==0.141.1` wheel contains Swagger UI v4.14.0's OAuth2 redirect
HTML in `fastapi/openapi/docs.py`, inside `get_swagger_ui_oauth2_redirect_html`.
FastAPI identifies this source in the file. The HTML is embedded and indented
inside a Python string and returned by a FastAPI function; the nonempty HTML
lines match the upstream file after trimming line-edge whitespace. The dashboard
does not modify the FastAPI wheel. This file is distributed even when the
application disables API-documentation routes.

The embedded Swagger UI portion is under Apache-2.0. Preserve its
[upstream attribution notice](fastapi-swagger-ui-NOTICE.txt) together with the
[full Apache-2.0 license text](../LICENSE). FastAPI's own MIT license remains in
its wheel. These upstream references identify the copied portion and its terms:

- [Swagger UI v4.14.0 OAuth redirect source](https://github.com/swagger-api/swagger-ui/blob/v4.14.0/dist/oauth2-redirect.html)
- [Swagger UI v4.14.0 NOTICE](https://github.com/swagger-api/swagger-ui/blob/v4.14.0/NOTICE)
- [Swagger UI v4.14.0 LICENSE](https://github.com/swagger-api/swagger-ui/blob/v4.14.0/LICENSE)

This supplement applies to the selected wheel with SHA-256
`bfb91aa2d334c61cb35ba9a116fc123b3d3df31640b801cf57a7a78ec3f603b3`.
An update requires a fresh review of the included portion and notices.

### Offline dependency bundle review

The pinned wheelhouse has artifact-specific supplements for the embedded code
and generated data identified during review. Keep the attribution maps and full
terms together; a map or SPDX label alone is not the license text.

| Dependency portions | Attribution and complete terms |
| --- | --- |
| AnyIO, Click, Uvicorn and websockets Python-derived code | [Origin/modification map](cpython-attribution.md), [Python terms](python-LICENSE.txt), and Click's separate [optparse terms](optparse-LICENSE.txt) |
| idna generated Unicode tables | [Exact table attribution](idna-unicode-NOTICE.txt) and [Unicode terms](Unicode-LICENSE.txt) |
| Pydantic Core's Cargo components, including ICU4X data | [Component map](pydantic-core-attribution.md) and [full collected notices](pydantic-core-NOTICES.txt) |
| Rust standard-library portions in the selected Core binary | [Runtime attribution](rust-stdlib-attribution.md) and [full collected notices](rust-stdlib-NOTICES.txt) |

The Core component inventory includes other-target and build dependencies;
the Rust collection likewise preserves a conservative runtime-workspace notice
superset. Neither claims every listed component is linked into the macOS binary.
The maps identify exact wheel/member bytes, notice sources, license alternatives
and provenance limits. No dependency implementation was changed or rebuilt.

These supplements address the identified attribution gaps for the exact pinned
artifacts. They do not certify arbitrary future versions, other platform wheels,
or the absence of all possible third-party claims. The release checks require
the original wheel notices plus the reviewed supplements, and verify that the
supplements are included in the application wheel's installed license metadata.
License coverage is separate from runtime and publication acceptance.

`release/MANIFEST.json` inventories the assembled bundle and
`release/SHA256SUMS` covers its source, application wheel, dependency wheels and
release records. `release/DEPENDENCY-NOTICES.json` maps each exact dependency
wheel to its retained primary notices and installed supplements. These release
files are created for a particular bundle and
are not substitutes for this browser-asset inventory. The dashboard wheel also
includes its license and these notices. No Python interpreter or Conda runtime
is supplied. The installer uses the separately identified `pip` already bundled
with the selected Python; it does not download another copy.

Redistributors must review the notices for the artifacts they actually supply,
including any additional interpreter or platform wheels. A package's recorded
license label alone does not replace its full license and attribution text.
