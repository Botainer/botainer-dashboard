# Python-derived dependency code

This supplement identifies Python-derived portions in the exact dependency
wheels listed below. Preserve their original source comments and license files
as well as this supplement. The dashboard redistributes those wheels unchanged;
the adaptations described here were made by the dependency projects.

The [Python license](python-LICENSE.txt) supplies the PSF agreement and historical
Python terms. It is the unmodified license text distributed with CPython
3.13.15, including the copyright notice for 2001–2024. The original license in
`typing_extensions==4.16.0` remains in that wheel. This supplement does not
replace any dependency's own MIT or BSD license.

The upstream source versions below are fixed references for the identified
code and terms. Where a dependency identifies only a module or a Python minor
version, these references do not assert the original copying commit.

## AnyIO 4.15.1

Wheel SHA-256:
`6152fdbbf9a77fdec97731721bebf7c4c44f7c29b424b0065826173efc7ed101`.

- `anyio/_backends/_asyncio.py` includes a conditional backport of `_State`,
  `Runner` and `_cancel_all_tasks` from
  [CPython 3.11's asyncio runners](https://github.com/python/cpython/blob/v3.11.0/Lib/asyncio/runners.py).
  AnyIO adds annotations and compatibility guards, creates tasks through the
  selected context on older Python, and supplies a fallback executor shutdown.
  That fallback adapts the executor shutdown methods in
  [CPython's event loop](https://github.com/python/cpython/blob/v3.10.0/Lib/asyncio/base_events.py)
  into a function with a nested thread target.
- `anyio/_core/_sockets.py`, `setup_unix_local_socket`, adapts
  [pathlib socket detection and error filtering](https://github.com/python/cpython/blob/v3.10.0/Lib/pathlib.py).
  It calls `os.stat` directly, ignores the selected missing-path errors, and
  removes an existing socket as part of Unix socket setup. Its surrounding
  logic skips filesystem checks for abstract namespace sockets.
- `anyio/_core/_fileio.py`, the conditional `Path.with_stem` implementation,
  backports [Python 3.13's empty-stem rule](https://github.com/python/cpython/blob/v3.13.0/Lib/pathlib/_abc.py).
  It reads the wrapped path's suffix and returns AnyIO path objects, with type
  annotations and an older-Python version guard.

## Click 8.5.0

Wheel SHA-256:
`255bc9599cf7748b4b1a446ccc735421bd08a2ae529a8b88597d3de5664ee360`.

`click/parser.py` identifies its origin in the standard library's `optparse`
module. It retains these notices:

> Copyright 2001-2006 Gregory P. Ward. All rights reserved.
> Copyright 2002-2006 Python Software Foundation. All rights reserved.

The original [optparse terms](optparse-LICENSE.txt) are supplied separately,
including their conditions and disclaimer. They are preserved from the
[`optparse` copyright/license string](https://github.com/python/cpython/blob/v2.7.18/Lib/optparse.py),
which is a separate BSD-style notice within CPython.

Click's parser removes and relocates optparse features such as type conversion
and help formatting, uses Click option/context/error objects, and changes
option/argument handling. The module documents this adaptation; it is not an
unmodified copy of the complete standard-library parser.

`click/_textwrap.py` adapts `TextWrapper._wrap_chunks` and long-word handling
from [CPython textwrap](https://github.com/python/cpython/blob/v3.13.0/Lib/textwrap.py).
It measures displayed width with `term_len`, retains ANSI escape sequences when
splitting long words, and integrates that behavior with Click's wrapper.
The original textwrap copyright notices are:

> Copyright (C) 1999-2001 Gregory P. Ward.
> Copyright (C) 2002, 2003 Python Software Foundation.

The Python license applies to these textwrap-derived portions; the separate
optparse notice is for the parser-derived portions.

## Uvicorn 0.53.0

Wheel SHA-256:
`e8dca71ec86dce5f04e333f0d56cdedf942446e6643b9cea1af0d6d3a02cb03e`.

`uvicorn/_compat.py` supplies version-dependent `asyncio_run` implementations.
Its Python 3.11 branch selects the Runner-based call from
[CPython 3.12](https://github.com/python/cpython/blob/v3.12.0/Lib/asyncio/runners.py).
Its older-Python branch adapts `run` and `_cancel_all_tasks` from
[CPython 3.10](https://github.com/python/cpython/blob/v3.10.0/Lib/asyncio/runners.py),
adding an optional loop factory, annotations, public asyncio names and
conditional event-loop setup/cleanup. The source retains PSF license links.
These compatibility branches remain distributed even where the current
interpreter uses native `asyncio.run` instead.

Separately, `uvicorn/_types.py` already includes a complete Django Software
Foundation and contributors BSD notice. Preserve that source docstring; it is
not covered solely by Uvicorn's own license or by the Python supplement.

## websockets 17.1

Wheel SHA-256:
`fd8f47dbf2e8adb15c847215f83436de3fdb120b51fdae0fbbdf69fd97a3ad80`.

`websockets/legacy/protocol.py` identifies copied flow-control and stream
protocol portions: initialization of paused/drain state, `_drain_helper`,
`_drain`, reader transport setup, reader EOF/error propagation, and paused-writer
wakeup. Compare
[CPython's asyncio streams](https://github.com/python/cpython/blob/v3.8.20/Lib/asyncio/streams.py).

The methods are integrated into `WebSocketCommonProtocol` with annotations,
WebSocket connection state, direct reader/transport fields, a connection-lost
future, and WebSocket cleanup. The writer pause/resume methods retain the
related flow-control behavior while omitting asyncio's debug logging. The
original copying revision is not identified in the wheel; this reference is
a comparison source, not a claim that websockets was built from that tag.

## Distribution scope

These notices cover the identified Python-derived portions, including shipped
compatibility code. They do not certify unrelated Rust, Unicode data or native
extension components. A dependency update needs a fresh review of its actual
contents and applicable notices. No Python interpreter is bundled by including
its license as an attribution supplement.

## Exact source-member identities

The following SHA-256 values identify the source files inspected within the
unchanged wheels above. They are member hashes, distinct from whole-wheel hashes.

| Wheel member | SHA-256 |
| --- | --- |
| `anyio/_backends/_asyncio.py` | `56acbb3b5ca6984a714e23f37a14ae042553ce21802cd57e05bb0845a297b20e` |
| `anyio/_core/_sockets.py` | `0bc6fedf0e47f67d05a3883b5fc589be4d26e821a1e8d540005b627811872b78` |
| `anyio/_core/_fileio.py` | `abef9fb7d87963f1512f38409e862b5dfded3ae67c67220dd80f6609f31b6517` |
| `click/parser.py` | `a09f9f53fde6bf1ba022e36d1da0804d9e7a9601261d18208c9892a84818ae30` |
| `click/_textwrap.py` | `ed9d0ded59a7fbae933524d4c29e8e5c96dc5174666044fd87d736b9c0fca104` |
| `uvicorn/_compat.py` | `e97e3d73da2fce63078abd92decca29ce40ccd23c1729f0c36e23d650adb6225` |
| `uvicorn/_types.py` | `24333a13e86a67aa3f71266f8c350b8c0bfbecf147da92defd582ab217bca87e` |
| `websockets/legacy/protocol.py` | `dcc4e1d7ed24b564ad4425f4d252d5ebd80bcc5ffd8009fc60b144fdb664cd8c` |
