"""Bounded workspace routes, behind the service's existing auth boundary."""
import asyncio
from fastapi import Request
from fastapi.responses import JSONResponse

from .backend_errors import BackendUnavailable
from .workspace import WorkspaceError


def register_workspace_routes(app, backend, *, json_body, opaque):
    async def invoke(name, *args):
        method = getattr(backend, name, None)
        if method is None:
            return JSONResponse({"error": "workspace-not-enabled"}, status_code=409)
        try:
            return await asyncio.to_thread(method, *args)
        except WorkspaceError as error:
            return JSONResponse({"error": str(error)}, status_code=409)
        except BackendUnavailable as error:
            return JSONResponse({"error": str(error)}, status_code=409)
        except (OSError, ValueError):
            return JSONResponse({"error": "workspace-operation-unavailable"}, status_code=503)

    async def fields(request, allowed, *, editor=False):
        value = await json_body(request, allowed, limit=128 * 1024 if editor else 4096)
        if "requestId" in value: opaque(value["requestId"])
        for key, item in value.items():
            if not isinstance(item, str): raise ValueError("text fields required")
            item.encode("utf-8")
        return value

    @app.exception_handler(WorkspaceError)
    async def workspace_error(_request, exc):
        return JSONResponse({"error": str(exc)}, status_code=409)

    @app.get("/api/workspace")
    async def metadata(): return await invoke("workspace_metadata")

    @app.post("/api/sessions/{runtime_id}/history")
    async def terminal_history(runtime_id: str, request: Request):
        try:
            data = await fields(request, {"contextNamespace"})
            opaque(runtime_id)
            opaque(data["contextNamespace"])
        except (ValueError, UnicodeError, asyncio.TimeoutError):
            return JSONResponse({"error": "invalid-terminal-history-request"}, status_code=400)
        return await invoke("read_terminal_history", data["contextNamespace"], runtime_id)

    @app.post("/api/projects")
    async def create(request: Request):
        try:
            required = {"mode", "rootId", "path", "name", "installationId", "requestId"}
            if getattr(backend, "requires_workspace_id", False):
                required.add("workspaceId")
            data = await fields(request, required)
        except (ValueError, UnicodeError, asyncio.TimeoutError):
            return JSONResponse({"error": "invalid-project-request"}, status_code=400)
        return await invoke("create_project", data)

    @app.post("/api/projects/{project_id}/open-host")
    async def open_host(project_id: str, request: Request):
        try:
            data = await fields(request, {"workspaceId", "requestId"})
            opaque(project_id)
            opaque(data["workspaceId"])
        except (ValueError, UnicodeError, asyncio.TimeoutError):
            return JSONResponse({"error": "invalid-host-project-request"}, status_code=400)
        # The router resolves the folder from its existing local catalog.
        # A caller selects identities, never a cwd, root or executable.
        return await invoke("open_host_project", project_id, data["workspaceId"], data["requestId"])

    @app.post("/api/projects/{project_id}/files")
    async def files(project_id: str, request: Request):
        try: data = await fields(request, {"path"}); opaque(project_id)
        except (ValueError, UnicodeError, asyncio.TimeoutError):
            return JSONResponse({"error": "invalid-files-request"}, status_code=400)
        return await invoke("list_files", project_id, data["path"])

    @app.post("/api/projects/{project_id}/file")
    async def file(project_id: str, request: Request):
        try: data = await fields(request, {"path"}); opaque(project_id)
        except (ValueError, UnicodeError, asyncio.TimeoutError):
            return JSONResponse({"error": "invalid-file-request"}, status_code=400)
        return await invoke("read_file", project_id, data["path"])

    @app.get("/api/projects/{project_id}/config")
    async def config(project_id: str):
        try: opaque(project_id)
        except ValueError: return JSONResponse({"error": "invalid-project-id"}, status_code=400)
        return await invoke("read_config", project_id)

    @app.post("/api/projects/{project_id}/config/validate")
    async def validate(project_id: str, request: Request):
        try: data = await fields(request, {"text", "revision"}, editor=True); opaque(project_id)
        except (ValueError, UnicodeError, asyncio.TimeoutError):
            return JSONResponse({"error": "invalid-config-request"}, status_code=400)
        return await invoke("validate_config", project_id, data["text"], data["revision"])

    @app.post("/api/projects/{project_id}/config/save")
    async def save(project_id: str, request: Request):
        try: data = await fields(request, {"text", "revision", "requestId"}, editor=True); opaque(project_id)
        except (ValueError, UnicodeError, asyncio.TimeoutError):
            return JSONResponse({"error": "invalid-config-request"}, status_code=400)
        return await invoke("save_config", project_id, data["text"], data["revision"])

    @app.get("/api/dashboard/config")
    async def dashboard_config(): return await invoke("dashboard_config")

    @app.post("/api/dashboard/config/validate")
    async def validate_dashboard(request: Request):
        try: data = await fields(request, {"text", "revision"}, editor=True)
        except (ValueError, UnicodeError, asyncio.TimeoutError):
            return JSONResponse({"error": "invalid-config-request"}, status_code=400)
        return await invoke("validate_dashboard_config", data["text"], data["revision"])

    @app.post("/api/dashboard/config/save")
    async def save_dashboard(request: Request):
        try: data = await fields(request, {"text", "revision", "requestId"}, editor=True)
        except (ValueError, UnicodeError, asyncio.TimeoutError):
            return JSONResponse({"error": "invalid-config-request"}, status_code=400)
        return await invoke("save_dashboard_config", data["text"], data["revision"])
