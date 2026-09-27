# Browser application

This directory is the service-facing dashboard. `prototype/` contains a separate
sample-data simulation. The browser application uses ordinary ES modules and
locally served xterm bundles; it requires no frontend framework, npm build or
CDN. [Third-party notices](../third_party/README.md) identify the bundled assets.

| File | Responsibility |
| --- | --- |
| `index.html`, `app.css` | Responsive application shell and terminal layout |
| `app.js` | Inventory, project navigation, selected views and capability-gated actions |
| `connections.js` | Machine setup, retained draft/review state and separate Help reader |
| `desktop-layout.js` | Bounded sidebar/panel widths, keyboard resizing and optional panel docking |
| `workbench-state.js` | Allowlisted browser preferences and exact inventory reconciliation on reload |
| `login.js`, `auth.js` | Pairing exchange and origin-scoped browser credentials |
| `terminal-registry.js` | Exact terminal identities, retained renderers and connection generations |
| `xterm-adapter.js` | Rendering, fitting, search, WebSocket transport and output credit |
| `vendor/` | Unmodified xterm JavaScript/CSS and original license notices |

`package.json` declares ES-module scope only. The selected upstream dependency
versions and archive hashes are in `requirements/`, and the bundled-file
inventory is in `third_party/manifest.json`.

## Navigation and state

Project navigation uses backend inventory. Search, sorting, machine filtering,
pins and compact display help organize it. Labels and browser preferences are
presentation state; they are not authority to control a runtime. Current work
and bounded history are distinct. Reported launch/end times do not establish
when an agent last made progress.

The Open strip selects retained work views. Switching views preserves their
terminal and transport state. Settings separates Dashboard, Machines and Help;
machine Add/Import uses a focused form. Opening Help retains the setup draft and
review state. Browser reload is not durable draft storage. Project file/config
views stay scoped to their selected project.

**Switch…** (Ctrl/⌘Shift+P) searches projects, sessions, connections and view
actions. **Sessions** lists open terminal views. Neither menu starts an agent;
selecting a session uses the existing exact-target connection checks. Drag pane
edges or focus their separators and use arrow keys to resize. **Dock panel**
keeps the inspector beside the terminal when the window has enough room.

The browser remembers pane widths, filters, project expansion and up to eight
open view identities. Reload restores only exact targets verified in fresh
inventory, with disconnected renderers. It never restores a transport or typed
input. Terminal output, editor drafts and bell badges are not saved. Preferences
are origin-scoped and shared across tabs; the latest saved choices win.

Connect, Start and Stop require backend capabilities. An unavailable control
shows the restriction rather than simulating success. Closing a view detaches
its viewer; Stop requests runtime termination separately. Sign out revokes the
browser pairing and viewer access rather than issuing runtime Stop commands.
Unverified owners and uncertain connection cleanup remain visible restrictions.
See [status](../docs/status.md) for unresolved recovery cases.

## Terminal transport

Each view has an immutable context namespace and runtime ID from the service.
Connection generations fence callbacks from old attachments. Input is enabled
only after the server confirms readiness, sent to the selected live target and
never queued for replay after reconnect. Output credit is replenished after
xterm has processed the corresponding bytes. Resize uses the same bound target.

Output is passed to xterm, not inserted as HTML. There is no automatic link
opening, project-provided script loading or generic host shell fallback. Copy
and Paste require explicit gestures; Paste checks that the original selected
connection is still writable after a clipboard read.

Text size, fitting and terminal scrollback are browser presentation features.
Retained page history and bounded read-only history snapshots are not a complete
session transcript. A reload or disconnected interval can leave missing output.
Bell badges only reflect observed terminal bell events while connected; they do
not prove that an agent is waiting for input and cannot monitor unopened views.
The browser title shows a generic bell count; the header bell selects the next
open view with an observed bell. No operating-system notifications are requested.

## Authentication

API requests require both an HttpOnly cookie and an independent bearer stored
for the browser origin. WebSockets provide that bearer using a credential
subprotocol; credentials are never placed in URLs. The service also validates
Host/Origin and target writer ownership. Frontend controls and disabled buttons
are not the authorization boundary.

## Verification

The Help reader in `connections.js` renders the maintained guides through a
bounded DOM renderer without a Markdown dependency. It supports headings,
paragraphs, emphasis, inline/fenced code, lists and pipe tables; it is not a
general CommonMark implementation. Raw HTML, images and embeds stay inert.
Only explicit credential-free HTTPS links become external links. Known guide
links use Help navigation; other source references remain visible text.
Project file previews remain plain text and do not use this renderer.

When changing guide syntax, extend the supported subset and tests deliberately.
Tests cover real shipped tables, commands nested in numbered steps, links,
malformed input and focus during asynchronous navigation. Full Markdown or rich
media support requires a separate dependency/security review.

With an existing Node.js:

```sh
node --test tests/frontend/*.test.mjs
```

These checks exercise browser logic with controlled doubles, including stale
callbacks, draft retention, identity routing, navigation, buffers and input
refusal. They do not establish real browser layout, terminal rendering fidelity,
sleep recovery or Botainer lifecycle acceptance. Use the
[development gate](../docs/engineering/README.md) and separate real-workflow
checks for the behavior affected by a change.
