"""Authenticated loopback HTTP/WebSocket adapter around an explicit trusted backend.

There is no raw shell-command or arbitrary-argv API. Connection setup can run
an explicitly selected interpreter; paired browsers therefore have trusted
operator authority, not a sandboxed inspection role. A backend owns runtime
identity, dispatch and durable lifetime; closing a transport only releases its
attachment. The default launcher must bind 127.0.0.1, disable proxy headers and
apply WS_MAX_SIZE/WS_MAX_QUEUE to Uvicorn as well as the application checks here.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
import json
from pathlib import Path
import re
import threading
from typing import Any, Protocol
from urllib.parse import parse_qs

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.websockets import WebSocketDisconnect

from .access import AccessDenied, LoopbackAccess
from .backend_errors import BackendUnavailable
from .pairing import BROWSER_LIFETIME, BrowserCredential, PairingStore

BODY_LIMIT = 4096
WS_MAX_SIZE = 16384
WS_MAX_QUEUE = 4
OUTPUT_WINDOW = 65536
IO_TIMEOUT = 5.0
ACK_TIMEOUT = 10.0
ATTACH_TIMEOUT = 45.0
COOKIE_NAME = "botainer_dashboard_session"
COOKIE_LIFETIME = BROWSER_LIFETIME
WS_PROTOCOL = "botainer-dashboard.v1"
_FRONTEND_ASSETS = {"auth.js", "login.js", "app.js", "app.css", "xterm-adapter.js", "terminal-registry.js", "connections.js",
                    "desktop-layout.js", "workbench-state.js"}
MAX_CONNECTIONS = 16
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,160}\Z")
_SECURITY_HEADERS = {b"host", b"origin", b"cookie", b"authorization", b"content-type", b"content-length", b"sec-websocket-protocol"}
_PUBLIC_LOGIN = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pair Botainer Dashboard</title><link rel="stylesheet" href="/app.css">
<main class="pairing-page"><h1>Botainer Dashboard</h1>
<p>Pair this browser once. It will stay paired for up to 30 days, including new tabs and dashboard restarts.</p>
<h2>Where do I get the code?</h2>
<ol><li>Leave the dashboard running. Open a <strong>second terminal on the same computer, under the same account</strong>, in the dashboard source folder.</li>
<li>Run this command:<pre><code>python3 tools/dashboard.py pair --port __PAIRING_PORT__</code></pre></li>
<li>If you chose a custom data directory, add the same <code>--data-dir</code> option used at startup. The service terminal prints the complete command.</li>
<li>Copy the displayed <strong>One-time pairing code</strong> and paste it below.</li></ol>
<form id="login-form" method="post" action="/auth"><label>One-time pairing code <input name="token" type="password" autocomplete="off" required maxlength="128" autofocus></label><button type="submit">Pair this browser</button></form>
<p id="login-error" role="alert"></p>
<p>Code used or expired? Run the same command again. Codes work once and expire after 10 minutes; requesting another does not restart the dashboard or end existing pairings.</p>
<p>Use Sign out to forget this browser. Pair only a browser profile you trust. Keep codes private; do not put them in page URLs or shared messages.</p>
</main><script type="module" src="/login.js"></script></html>"""


class Attachment(Protocol):
    def read(self, max_bytes: int, *, timeout: float = 0) -> bytes | None: ...
    def write(self, data: bytes) -> int: ...
    def resize(self, cols: int, rows: int) -> None: ...
    def close(self) -> Any: ...


class DashboardBackend(Protocol):
    """Trusted implementations bound every operation and sanitize snapshot data.

    Methods must have bounded execution. Mutation implementations journal intent,
    claim once and never retry an ambiguous outcome. ``attach`` must return only
    after verifying the exact runtime; it must never fall back to a host shell.
    """

    def snapshot(self) -> dict: ...
    def start_session(self, project_id: str, request_id: str) -> dict: ...
    def stop_session(self, context_namespace: str, runtime_id: str, request_id: str) -> dict: ...
    def attach(self, context_namespace: str, runtime_id: str, cols: int, rows: int) -> Attachment: ...


@dataclass
class _Credit:
    outstanding: int = 0


class _AttachmentHandoff:
    """The worker owns a created client until the event loop explicitly claims it.

    Cancellation cannot cancel ownership: the worker closes every unclaimed
    client, even when the asyncio task wrapping its thread has been canceled.
    """

    def __init__(self, loop, create, release_lease):
        self.loop, self.create, self.release_lease = loop, create, release_lease
        self.ready = loop.create_future()
        self.decision = threading.Event()
        self.lock = threading.Lock()
        self.attachment = None
        self.error = None
        self.abandoned = False
        self.claimed = False
        self.cleaned = False

    def _notify(self, callback):
        try:
            self.loop.call_soon_threadsafe(callback)
        except RuntimeError:
            # A closed loop cannot claim ownership. The worker still cleans up.
            self.abandon()

    def _publish(self):
        if not self.ready.done():
            self.ready.set_result(None)

    def run(self):
        client = None
        try:
            with self.lock:
                abandoned = self.abandoned
            if abandoned:
                with self.lock:
                    self.cleaned = True
                return
            try:
                client = self.create()
            except Exception as error:
                with self.lock:
                    self.error, self.cleaned = error, True
                return
            with self.lock:
                self.attachment = client
            self._notify(self._publish)
            # A dead/stalled event loop never silently acquires a client. The
            # short decision deadline begins only after backend validation ends.
            if not self.decision.wait(IO_TIMEOUT):
                self.abandon()
            with self.lock:
                claimed = self.claimed
            if not claimed:
                try:
                    client.close()
                except Exception:
                    pass  # Unconfirmed cleanup keeps the target fenced.
                else:
                    with self.lock:
                        self.cleaned = True
        finally:
            self._notify(self._publish)
            with self.lock:
                release = self.cleaned and not self.claimed
            if release:
                self._notify(self.release_lease)

    def abandon(self):
        with self.lock:
            if not self.claimed:
                self.abandoned = True
        self.decision.set()

    def claim(self):
        with self.lock:
            if self.error is not None:
                raise self.error
            if self.abandoned or self.attachment is None:
                raise AccessDenied("attachment-not-available")
            self.claimed = True
            client = self.attachment
        self.decision.set()
        return client

    def cleanup_confirmed(self):
        with self.lock:
            return self.cleaned


def _headers(scope: dict) -> dict[str, str]:
    found: dict[bytes, bytes] = {}
    for name, value in scope.get("headers", []):
        name = name.lower()
        if name.startswith(b"x-forwarded-") or name == b"forwarded":
            raise AccessDenied("forwarded-headers-forbidden")
        if name in _SECURITY_HEADERS and name in found:
            raise AccessDenied("duplicate-security-header")
        found[name] = value
    if scope.get("query_string"):
        raise AccessDenied("query-forbidden")
    return {name.decode("latin-1"): value.decode("latin-1") for name, value in found.items()}


def _cookie(headers: dict[str, str], name: str = COOKIE_NAME) -> str:
    result = ""
    seen = False
    for part in headers.get("cookie", "").split(";"):
        if not part.strip():
            continue
        field_name, sep, value = part.strip().partition("=")
        if field_name == name:
            if seen or not sep:
                raise AccessDenied("invalid-cookie")
            result, seen = value, True
    return result


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("invalid identifier")
    return value


def _dimensions(message: dict) -> tuple[int, int]:
    cols, rows = message.get("cols"), message.get("rows")
    if any(type(value) is not int or not 1 <= value <= 1000 for value in (cols, rows)):
        raise ValueError("invalid dimensions")
    return cols, rows


def _json_unique(text: str) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON field")
            result[key] = value
        return result
    try:
        result = json.loads(text, object_pairs_hook=unique)
    except RecursionError as exc:
        raise ValueError("JSON nesting limit") from exc
    if not isinstance(result, dict):
        raise ValueError("object required")
    return result


async def _close(websocket: WebSocket, code: int, reason: str = "") -> None:
    try:
        await asyncio.wait_for(websocket.close(code=code, reason=reason), IO_TIMEOUT)
    except (RuntimeError, WebSocketDisconnect, asyncio.TimeoutError, OSError):
        pass


async def _body(request: Request, limit: int = BODY_LIMIT) -> bytes:
    chunks = bytearray()
    async def consume():
        async for chunk in request.stream():
            if len(chunks) + len(chunk) > limit:
                raise ValueError("body too large")
            chunks.extend(chunk)
    await asyncio.wait_for(consume(), IO_TIMEOUT)
    return bytes(chunks)


async def _json_body(request: Request, allowed: set[str], *, limit: int = BODY_LIMIT,
                     optional: frozenset[str] = frozenset()) -> dict:
    if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
        raise ValueError("JSON content type required")
    value = _json_unique((await _body(request, limit)).decode("utf-8"))
    if not allowed <= set(value) or set(value) - allowed - optional:
        raise ValueError("unexpected fields")
    return value


def create_app(*, access: LoopbackAccess, backend: DashboardBackend,
               static_root: Path, vendor_root: Path, pairing_store: PairingStore | None = None,
               connection_manager=None, docs_root: Path | None = None,
               pairing_control=None, service_control=None, installed_command=False) -> FastAPI:
    """Build a service without starting a listener or executing any backend action."""
    # Only a validated integer port is substituted, never request data or secrets.
    login_page = (_PUBLIC_LOGIN.replace("__PAIRING_PORT__", str(access.port))
                .replace("python3 tools/dashboard.py", "botainer-dashboard" if installed_command else "python3 tools/dashboard.py")
                .replace("in the dashboard source folder", "using the installed dashboard command" if installed_command else "in the dashboard source folder"))
    async def poll_pairing_control():
        while True:
            try:
                # The owner-only mailbox and /auth share this event loop.
                # No thread may mutate the pairing store concurrently.
                pairing_control.poll()
            except (OSError, ValueError):
                # Fail only the local code-request facility; do not tear down
                # terminal viewers or disclose private file contents in logs.
                app.state.pairing_control_failed = True
                with suppress(OSError, ValueError):
                    pairing_control.close()
                return
            await asyncio.sleep(0.1)

    async def poll_service_control():
        while True:
            try:
                service_control.poll()
            except (OSError, ValueError):
                # The launcher retains the separate lifetime lock until its
                # server actually exits. A broken mailbox is not a stopped
                # service and must not end sessions or browser grants.
                app.state.service_control_failed = True
                with suppress(OSError, ValueError):
                    service_control.failed()
                return
            await asyncio.sleep(0.1)

    @asynccontextmanager
    async def lifespan(_app):
        tasks = []
        if pairing_control is not None:
            tasks.append(asyncio.create_task(poll_pairing_control()))
        if service_control is not None:
            tasks.append(asyncio.create_task(poll_service_control()))
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.pairing_control_failed = False
    app.state.service_control_failed = False
    app.state.service_stopping = False
    cookie_name = f"{COOKIE_NAME}_{access.port}"
    pairings = pairing_store or PairingStore(origin=access.origin, token=access.token, lifetime=COOKIE_LIFETIME)
    if pairings.origin != access.origin:
        raise ValueError("pairing store must belong to the bound service origin")
    app.state.pairing_store = pairings
    leases: dict[tuple[str, str], object] = {}
    pending_workers: set[asyncio.Task] = set()
    connection_count = 0
    connections: dict[str, set[WebSocket]] = {}
    static_root = Path(static_root).resolve()
    vendor_root = Path(vendor_root).resolve()

    def credential_for(headers: dict[str, str]) -> BrowserCredential | None:
        return pairings.credential_for(_cookie(headers, cookie_name))

    def authenticated(headers: dict[str, str], *, bearer: str | None = None) -> bool:
        if bearer is None:
            authorization = headers.get("authorization", "")
            bearer = authorization[7:] if authorization.startswith("Bearer ") else ""
        return pairings.authenticated(_cookie(headers, cookie_name), bearer)

    def credential_live(cookie: str) -> bool:
        return pairings.credential_for(cookie) is not None

    def boundary(scope: dict, require_origin: bool) -> dict[str, str]:
        headers = _headers(scope)
        # Token checked separately for bootstrap/cookie; never trust proxy headers.
        access.authorize(host=headers.get("host", ""), origin=headers.get("origin"),
                         token=access.token, require_origin=require_origin)
        return headers

    @app.middleware("http")
    async def http_boundary(request: Request, call_next):
        try:
            headers = boundary(request.scope, require_origin=request.method not in {"GET", "HEAD"})
            public = request.method == "GET" and (request.url.path in {"/", "/unlock"}
                     or request.url.path[1:] in _FRONTEND_ASSETS or request.url.path.startswith("/vendor/"))
            bootstrap = request.method == "POST" and request.url.path == "/auth"
            if not public and not bootstrap and not authenticated(headers):
                response = JSONResponse({"error": "authentication-required"}, status_code=401)
            elif app.state.service_stopping and request.method == "POST" and request.url.path != "/logout":
                response = JSONResponse({"error": "dashboard-stopping"}, status_code=503)
            else:
                request.state.authenticated = credential_for(headers) is not None
                response = await call_next(request)
        except AccessDenied:
            response = JSONResponse({"error": "access-denied"}, status_code=403)
        response.headers.update({
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                f"connect-src 'self' ws://{access.authority}; img-src 'self' data:; "
                "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        })
        return response

    @app.get("/")
    async def index(request: Request):
        if not request.state.authenticated:
            return HTMLResponse(login_page)
        path = static_root / "index.html"
        if not path.is_file() or path.is_symlink():
            return JSONResponse({"error": "frontend-unavailable"}, status_code=503)
        return FileResponse(path, media_type="text/html")

    @app.get("/unlock")
    async def unlock():
        return HTMLResponse(login_page)

    @app.post("/auth")
    async def authenticate(request: Request):
        try:
            if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/x-www-form-urlencoded":
                raise ValueError("form required")
            fields = parse_qs((await _body(request, 256)).decode("ascii"), strict_parsing=True, max_num_fields=1)
            if set(fields) != {"token"} or len(fields["token"]) != 1:
                raise ValueError("one token required")
            # Keep exact Host/Origin checks, but the pairing store owns the
            # current one-time code; the immutable startup code may be spent.
            boundary(request.scope, require_origin=True)
            grant = pairings.pair(fields["token"][0])
        except (AccessDenied, ValueError, UnicodeError, asyncio.TimeoutError):
            return JSONResponse({"error": "authentication-failed"}, status_code=403)
        except OSError:
            return JSONResponse({"error": "authentication-storage-unavailable"}, status_code=503)
        response = JSONResponse({"bearer": grant.bearer, "expiresAt": grant.expires})
        response.set_cookie(cookie_name, grant.cookie, httponly=True, samesite="strict", max_age=grant.lifetime, path="/")
        return response

    @app.post("/logout")
    async def logout(request: Request):
        credential = _cookie(_headers(request.scope), cookie_name)
        try:
            pairings.revoke(credential)
        except (OSError, ValueError):
            return JSONResponse({"error": "authentication-storage-unavailable"}, status_code=503)
        for websocket in tuple(connections.get(credential, ())):
            await _close(websocket, 1008, "authentication-ended")
        response = JSONResponse({"locked": True})
        response.delete_cookie(cookie_name, path="/", httponly=True, samesite="strict")
        return response

    @app.get("/api/state")
    async def state():
        try:
            value = await asyncio.to_thread(backend.snapshot)
            return JSONResponse({**value, "dashboardEntryPoint": "installed" if installed_command else "source"})
        except BackendUnavailable as exc:
            return JSONResponse({"error": exc.code}, status_code=409)

    @app.post("/api/projects/{project_id}/sessions")
    async def start(project_id: str, request: Request):
        try:
            value = await _json_body(request, {"requestId"}, optional=frozenset({"agent"}))
            args = (_identifier(project_id), _identifier(value["requestId"]))
            if "agent" in value:
                if value["agent"] not in ("claude", "codex"):
                    raise ValueError("unsupported agent override")
                # The selected backend must independently authorize this
                # capability; a browser choice never supplies executable argv.
                snapshot = await asyncio.to_thread(backend.snapshot)
                project = next((item for item in snapshot.get("projects", []) if item.get("id") == project_id), None)
                caps = dict(snapshot.get("capabilities", {}))
                if project is not None:
                    caps.update(project.get("capabilities", {}))
                if project is None or not caps.get("agentOverride"):
                    raise BackendUnavailable("agent-override-unavailable")
                return JSONResponse(await asyncio.to_thread(backend.start_session, *args, agent=value["agent"]))
            return JSONResponse(await asyncio.to_thread(backend.start_session, *args))
        except (ValueError, UnicodeError, asyncio.TimeoutError):
            return JSONResponse({"error": "invalid-request"}, status_code=400)
        except BackendUnavailable as exc:
            return JSONResponse({"error": exc.code}, status_code=409)

    @app.post("/api/sessions/{runtime_id}/stop")
    async def stop(runtime_id: str, request: Request):
        try:
            value = await _json_body(request, {"contextNamespace", "requestId"},
                                     optional=frozenset({"expectedStopMode"}))
            mode = value.get("expectedStopMode", "normal")
            if mode not in ("normal", "orphan-container"):
                raise ValueError("unsupported stop mode")
            operation = backend.stop_session if mode == "normal" else getattr(backend, "stop_orphan_session", None)
            if not callable(operation):
                raise BackendUnavailable("orphan-stop-unavailable")
            return JSONResponse(await asyncio.to_thread(operation, _identifier(value["contextNamespace"]),
                                                        _identifier(runtime_id), _identifier(value["requestId"])))
        except (ValueError, UnicodeError, asyncio.TimeoutError):
            return JSONResponse({"error": "invalid-request"}, status_code=400)
        except BackendUnavailable as exc:
            return JSONResponse({"error": exc.code}, status_code=409)

    @app.websocket("/api/sessions/{runtime_id}/terminal")
    async def terminal(websocket: WebSocket, runtime_id: str):
        nonlocal connection_count
        attachment = None
        target = None
        lease_token = object()
        handoff = None
        handshake_tasks: list[asyncio.Task] = []
        tasks: list[asyncio.Task] = []

        def release_lease():
            if target is not None and leases.get(target) is lease_token:
                del leases[target]
        accepted = False
        counted = False
        credential = ""
        try:
            headers = boundary(websocket.scope, require_origin=True)
            protocols = [part.strip() for part in headers.get("sec-websocket-protocol", "").split(",")]
            if (len(protocols) != 2 or protocols[0] != WS_PROTOCOL
                    or not protocols[1].startswith("credential.")
                    or not authenticated(headers, bearer=protocols[1][11:])):
                raise AccessDenied("authentication-required")
            credential = _cookie(headers, cookie_name)
            _identifier(runtime_id)
            if app.state.service_stopping:
                await websocket.close(code=1013)
                return
            if connection_count >= MAX_CONNECTIONS:
                raise AccessDenied("connection-capacity")
            connection_count += 1
            counted = True
            await websocket.accept(subprotocol=WS_PROTOCOL)
            accepted = True
            connections.setdefault(credential, set()).add(websocket)
            event = await asyncio.wait_for(websocket.receive(), IO_TIMEOUT)
            text = event.get("text")
            if event.get("type") != "websocket.receive" or not isinstance(text, str) or len(text.encode("utf-8")) > 1024:
                raise ValueError("bind required")
            value = _json_unique(text)
            if set(value) != {"type", "contextNamespace", "cols", "rows"} or value["type"] != "bind":
                raise ValueError("bind required")
            cols, rows = _dimensions(value)
            if app.state.service_stopping:
                await websocket.close(code=1013)
                return
            candidate = (_identifier(value["contextNamespace"]), runtime_id)
            if candidate in leases:
                raise AccessDenied("writer-already-attached")
            if len(pending_workers) >= MAX_CONNECTIONS:
                raise AccessDenied("attachment-capacity")
            leases[candidate] = lease_token
            target = candidate
            handoff = _AttachmentHandoff(asyncio.get_running_loop(),
                                         lambda: backend.attach(*target, cols, rows), release_lease)
            worker = asyncio.create_task(asyncio.to_thread(handoff.run))
            pending_workers.add(worker)
            worker.add_done_callback(pending_workers.discard)
            waiter = asyncio.create_task(asyncio.wait_for(asyncio.shield(handoff.ready), ATTACH_TIMEOUT))
            incoming = asyncio.create_task(websocket.receive())
            handshake_tasks = [waiter, incoming]
            done, _ = await asyncio.wait(handshake_tasks, return_when=asyncio.FIRST_COMPLETED)
            if incoming in done:
                # Detach or pre-ready input abandons the attempt. Buffered input
                # is never held for replay into a client that appears later.
                incoming.result()
                raise AccessDenied("attachment-abandoned")
            waiter.result()
            incoming.cancel()
            await asyncio.gather(incoming, return_exceptions=True)
            if not credential_live(credential):
                raise AccessDenied("authentication-ended")
            attachment = handoff.claim()
            await asyncio.wait_for(websocket.send_json({"type": "ready"}), IO_TIMEOUT)
            credit = _Credit()
            drained = asyncio.Event()

            async def output():
                while True:
                    if not credential_live(credential):
                        raise AccessDenied("authentication-ended")
                    if credit.outstanding >= OUTPUT_WINDOW:
                        drained.clear()
                        await asyncio.wait_for(drained.wait(), ACK_TIMEOUT)
                    available = min(WS_MAX_SIZE, OUTPUT_WINDOW - credit.outstanding)
                    data = attachment.read(available, timeout=0)
                    if data is None:
                        await asyncio.sleep(0.01)
                        continue
                    if data == b"":
                        await asyncio.wait_for(websocket.send_json({"type": "eof"}), IO_TIMEOUT)
                        return
                    if not isinstance(data, bytes) or len(data) > available:
                        raise ValueError("backend output limit")
                    credit.outstanding += len(data)
                    await asyncio.wait_for(websocket.send_bytes(data), IO_TIMEOUT)

            async def receive():
                while True:
                    event = await websocket.receive()
                    if not credential_live(credential):
                        raise AccessDenied("authentication-ended")
                    if event["type"] == "websocket.disconnect":
                        return
                    data, text = event.get("bytes"), event.get("text")
                    if data is not None:
                        if not 1 <= len(data) <= WS_MAX_SIZE:
                            raise ValueError("input limit")
                        async def write_all():
                            remaining = data
                            while remaining:
                                if not credential_live(credential):
                                    raise AccessDenied("authentication-ended")
                                count = attachment.write(remaining)
                                if type(count) is not int or not 0 <= count <= len(remaining):
                                    raise ValueError("invalid backend write")
                                remaining = remaining[count:]
                                if remaining:
                                    await asyncio.sleep(0.01)
                        # Partial bytes remain only inside this connection task;
                        # cancellation drops them. Reconnect never retries input.
                        await asyncio.wait_for(write_all(), IO_TIMEOUT)
                    elif isinstance(text, str) and len(text.encode("utf-8")) <= 1024:
                        value = _json_unique(text)
                        if value.get("type") == "resize" and set(value) == {"type", "cols", "rows"}:
                            attachment.resize(*_dimensions(value))
                        elif value.get("type") == "ack" and set(value) == {"type", "bytes"}:
                            count = value["bytes"]
                            if type(count) is not int or not 1 <= count <= credit.outstanding:
                                raise ValueError("invalid acknowledgement")
                            credit.outstanding -= count
                            drained.set()
                        else:
                            raise ValueError("invalid control")
                    else:
                        raise ValueError("invalid frame")

            tasks = [asyncio.create_task(output()), asyncio.create_task(receive())]
            done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except (AccessDenied, BackendUnavailable, ValueError, UnicodeError, asyncio.TimeoutError):
            await _close(websocket, 1008, "connection-refused")
        except (WebSocketDisconnect, OSError):
            pass
        finally:
            if handoff is not None and attachment is None:
                handoff.abandon()
            for task in [*tasks, *handshake_tasks]:
                task.cancel()
            try:
                if tasks or handshake_tasks:
                    await asyncio.gather(*tasks, *handshake_tasks, return_exceptions=True)
            finally:
                cleanup_confirmed = attachment is None and (handoff is None or handoff.cleanup_confirmed())
                try:
                    if attachment is not None:
                        await asyncio.wait_for(asyncio.to_thread(attachment.close), IO_TIMEOUT)
                        cleanup_confirmed = True
                except (OSError, asyncio.TimeoutError):
                    # Allocation cancellation can remove the remote owner before
                    # detach is acknowledged. Treat this as uncertain cleanup,
                    # not an unhandled ASGI error or successful detach. A timed
                    # out worker may still finish; it cannot release this lease.
                    pass
                finally:
                    # A failed cleanup leaves this target fenced for the service
                    # lifetime. Never permit a second writer after uncertain close.
                    if target is not None and cleanup_confirmed:
                        release_lease()
                    if counted:
                        connection_count -= 1
                    if credential in connections:
                        connections[credential].discard(websocket)
                        if not connections[credential]:
                            del connections[credential]
                    if accepted:
                        await _close(websocket, 1000 if cleanup_confirmed else 1011,
                                     "" if cleanup_confirmed else "attachment-cleanup-unconfirmed")

    def asset(root: Path, relative: str):
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or path.suffix not in {".js", ".css"} or not path.is_file():
            return JSONResponse({"error": "asset-unavailable"}, status_code=404)
        return FileResponse(path, media_type="text/javascript" if path.suffix == ".js" else "text/css")

    from .workspace_routes import register_workspace_routes
    register_workspace_routes(app, backend, json_body=_json_body, opaque=_identifier)
    from .connection_routes import register_connection_routes
    register_connection_routes(app, manager=connection_manager, json_body=_json_body,
                               docs_root=docs_root or Path(__file__).resolve().parents[2] / "docs")

    @app.get("/vendor/{relative:path}")
    async def vendor(relative: str):
        return asset(vendor_root, relative)

    @app.get("/{relative:path}")
    async def frontend(relative: str):
        if relative not in _FRONTEND_ASSETS:
            return JSONResponse({"error": "asset-unavailable"}, status_code=404)
        return asset(static_root, relative)

    return app
