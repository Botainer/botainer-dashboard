"""Connection setup behind the existing cookie, bearer and Origin boundary.

Selection changes are pending a deliberate launcher restart. They never replace
live controllers, detach viewers or execute a profile's Botainer launcher.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import threading

from fastapi import Request
from fastapi.responses import JSONResponse

from .connections import ConnectionRegistryError
from .installation_update import InstallationUpdateError


GUIDES = {
    "getting-started": ("Getting started", "getting-started.md"),
    "installation": ("Installation", "installation.md"),
    "start": ("Start & pairing", "start-dashboard.md"),
    "ssh": ("SSH setup and troubleshooting", "ssh-setup.md"),
    "sessions": ("Sessions", "sessions.md"),
    "configuration": ("Configuration", "configuration.md"),
    "maintenance": ("Botainer maintenance", "maintenance.md"),
    "host": ("Host agents", "host-agents.md"),
    "agent": ("Setup guide for an assistant", "agent-setup-guide.md"),
    "support": ("Tested and untested setup routes", "setup-support-matrix.md"),
    "status": ("Alpha status and limits", "status.md"),
    "alpha-testing": ("Try the limited alpha", "alpha-testing.md"),
}


class ConnectionManager:
    def __init__(self, registry, *, active_entries=(), prepare=None):
        self.registry = registry
        self.active = [dict(entry) for entry in active_entries if entry["enabled"]]
        self.prepare_function = prepare
        self._inspection_lock = threading.Lock()

    def snapshot(self):
        saved = self.registry.read()
        active = {(e["kind"], e["id"], e["profileDigest"]) for e in self.active}
        desired = {(e["kind"], e["id"], e["profileDigest"]) for e in saved["entries"] if e["enabled"]}
        entries = [{**entry, **({"agents": self.registry.host_agent_metadata(entry)}
                                if entry["kind"] == "host" else {})} for entry in saved["entries"]]
        return {**saved, "entries": entries, "enabled": True, "active": self.active,
                "pendingRestart": active != desired,
                "selectionFile": str(self.registry.path),
                "notice": "Saved changes apply on your next dashboard restart. Running sessions, remote files and SSH settings are unchanged."}

    def prepare(self, request):
        return self.inspect(lambda: self._prepare(request))

    def _prepare(self, request):
        if self.prepare_function is None:
            from .connection_prepare import prepare_connection
            return prepare_connection(request)
        return self.prepare_function(request)

    def inspect(self, action):
        if not self._inspection_lock.acquire(blocking=False):
            raise ValueError("A connection check is already running. Wait for its result before trying another.")
        try:
            return action()
        finally:
            self._inspection_lock.release()


def register_connection_routes(app, *, manager, docs_root: Path, json_body):
    async def invoke(action):
        if manager is None:
            return JSONResponse({"error": "Machine setup is not enabled in this launch. Start with --connections PATH; see the getting-started guide."}, status_code=409)
        try:
            return await asyncio.to_thread(action)
        except ConnectionRegistryError as exc:
            return JSONResponse({"error": str(exc), "code": exc.code}, status_code=409)
        except InstallationUpdateError as exc:
            return JSONResponse({"error": str(exc), "code": exc.code}, status_code=409)
        except ValueError as exc:
            # Preparation/registry validation emits fixed or field-specific text,
            # never process output, credentials or profile contents.
            return JSONResponse({"error": str(exc)[:500]}, status_code=400)
        except OSError:
            return JSONResponse({"error": "The private connection settings could not be read or saved. Check folder permissions; nothing was activated."}, status_code=503)

    @app.get("/api/connections")
    async def connections():
        if manager is None:
            return {"enabled": False, "entries": [], "active": [], "pendingRestart": False,
                    "notice": "Start the dashboard with --connections PATH to enable saved machine setup. Existing explicit profiles can seed that file on first start."}
        return await invoke(manager.snapshot)

    @app.get("/api/connections/help/{guide}")
    async def help_page(guide: str):
        if guide not in GUIDES:
            return JSONResponse({"error": "Unknown setup guide"}, status_code=404)
        title, name = GUIDES[guide]
        path = docs_root / name
        try:
            if path.is_symlink() or path.resolve().parent != docs_root.resolve():
                raise ValueError("invalid guide")
            with path.open("rb") as stream:
                data = stream.read(256 * 1024 + 1)
            if len(data) > 256 * 1024:
                raise ValueError("guide too large")
            return {"title": title, "text": data.decode("utf-8")}
        except (OSError, ValueError):
            return JSONResponse({"error": "This setup guide is unavailable in the selected checkout."}, status_code=404)

    @app.post("/api/connections/prepare")
    async def prepare(request: Request):
        try:
            data = await json_body(request, {"request"}, limit=32 * 1024)
            if not isinstance(data["request"], dict): raise ValueError("Expected setup fields")
        except (ValueError, UnicodeError, asyncio.TimeoutError):
            return JSONResponse({"error": "Invalid connection preparation request"}, status_code=400)
        return await invoke(lambda: manager.prepare(data["request"]))

    @app.post("/api/connections")
    async def add(request: Request):
        try:
            data = await json_body(request, {"kind", "profile", "revision", "trusted"}, limit=512 * 1024)
            if type(data["revision"]) is not int or data["trusted"] is not True:
                raise ValueError("Explicit review and a saved revision are required")
        except (ValueError, UnicodeError, asyncio.TimeoutError):
            return JSONResponse({"error": "Review the connection and confirm its trusted installation before saving."}, status_code=400)
        def save():
            manager.registry.add(data["kind"], data["profile"], data["revision"], trusted=True)
            return manager.snapshot()
        return await invoke(save)

    @app.post("/api/connections/{connection_id}/enabled")
    async def enabled(connection_id: str, request: Request):
        try:
            data = await json_body(request, {"enabled", "revision"})
            if type(data["enabled"]) is not bool or type(data["revision"]) is not int: raise ValueError("invalid fields")
        except (ValueError, UnicodeError, asyncio.TimeoutError):
            return JSONResponse({"error": "Invalid connection selection"}, status_code=400)
        def save():
            manager.registry.enable(connection_id, data["enabled"], data["revision"])
            return manager.snapshot()
        return await invoke(save)

    @app.post("/api/connections/{connection_id}/remove")
    async def remove(connection_id: str, request: Request):
        try:
            data = await json_body(request, {"revision", "confirmed"})
            if type(data["revision"]) is not int or data["confirmed"] is not True: raise ValueError("confirmation required")
        except (ValueError, UnicodeError, asyncio.TimeoutError):
            return JSONResponse({"error": "Confirm removal from the dashboard; this does not stop any session."}, status_code=400)
        def save():
            manager.registry.remove(connection_id, data["revision"])
            return manager.snapshot()
        return await invoke(save)

    @app.post("/api/connections/{connection_id}/agent-update/prepare")
    async def prepare_agent_update(connection_id: str, request: Request):
        try:
            data = await json_body(request, {"revision", "agent", "path"}, limit=8192)
            if type(data["revision"]) is not int or not isinstance(data["agent"], str) or not isinstance(data["path"], str):
                raise ValueError("invalid fields")
        except (ValueError, UnicodeError, asyncio.TimeoutError):
            return JSONResponse({"error": "Choose an existing agent and an optional absolute executable path."}, status_code=400)
        return await invoke(lambda: manager.registry.prepare_agent_update(
            connection_id, data["agent"], data["path"], data["revision"]))

    @app.get("/api/connections/{connection_id}/installation-update")
    async def installation_update_settings(connection_id: str):
        return await invoke(lambda: manager.registry.installation_update_settings(connection_id))

    async def installation_request(request, *, saving=False):
        fields = {'revision', 'profileDigest', 'changes', 'acknowledged'}
        if saving:
            fields |= {'candidateDigest', 'confirmed'}
        data = await json_body(request, fields, limit=32 * 1024)
        if (type(data['revision']) is not int or not isinstance(data['profileDigest'], str)
                or not isinstance(data['changes'], dict) or data['acknowledged'] is not True
                or saving and (data['confirmed'] is not True or not isinstance(data['candidateDigest'], str))):
            raise ValueError('invalid installation review')
        return data

    @app.post("/api/connections/{connection_id}/installation-update/prepare")
    async def prepare_installation_update(connection_id: str, request: Request):
        try:
            data = await installation_request(request)
        except (ValueError, UnicodeError, asyncio.TimeoutError):
            return JSONResponse({'error': 'Review the saved installation and acknowledge its read-only check.'}, status_code=400)
        return await invoke(lambda: manager.inspect(lambda: manager.registry.prepare_installation_update(
            connection_id, data['changes'], data['revision'], data['profileDigest'], acknowledged=True)))

    @app.post("/api/connections/{connection_id}/installation-update")
    async def update_installation(connection_id: str, request: Request):
        try:
            data = await installation_request(request, saving=True)
        except (ValueError, UnicodeError, asyncio.TimeoutError):
            return JSONResponse({'error': 'Inspect the installed update and confirm the exact reviewed replacement.'}, status_code=400)
        def save():
            manager.registry.update_installation(connection_id, data['changes'], data['candidateDigest'],
                data['revision'], data['profileDigest'], acknowledged=True, confirmed=True)
            return manager.snapshot()
        return await invoke(lambda: manager.inspect(save))

    @app.post("/api/connections/{connection_id}/agent-update")
    async def update_agent(connection_id: str, request: Request):
        try:
            data = await json_body(request, {"revision", "agent", "path", "sha256", "confirmed"}, limit=8192)
            if (type(data["revision"]) is not int or data["confirmed"] is not True
                    or not all(isinstance(data[key], str) for key in ("agent", "path", "sha256"))):
                raise ValueError("review required")
        except (ValueError, UnicodeError, asyncio.TimeoutError):
            return JSONResponse({"error": "Inspect the installed executable and confirm the exact replacement before saving."}, status_code=400)
        def save():
            manager.registry.update_agent(connection_id, data["agent"], data["path"], data["sha256"],
                                          data["revision"], confirmed=True)
            return manager.snapshot()
        return await invoke(save)
