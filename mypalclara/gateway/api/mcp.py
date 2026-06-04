"""MCP server management endpoints (internal, gateway-secret auth).

Thin HTTP wrappers over the in-process MCP manager / installer / Smithery client,
so adapters can manage MCP servers without importing engine internals.

Scope: server listing/status, tool listing, lifecycle (start/stop/restart/
enable/disable), reload, Smithery search, install/uninstall. The interactive
Smithery OAuth flow (start/exchange/manual-token) is intentionally NOT here yet —
it is stateful (redirect URIs, code exchange) and tracked as a follow-up.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from mypalclara.core.mcp import get_mcp_manager
from mypalclara.core.mcp.installer import MCPInstaller, SmitheryClient
from mypalclara.gateway.api.auth import require_gateway_secret

router = APIRouter()

_LIFECYCLE_ACTIONS = {"start", "stop", "restart", "enable", "disable"}


def _jsonable(obj: Any) -> Any:
    """Best-effort JSON-safe conversion for engine return objects."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if is_dataclass(obj):
        return _jsonable(asdict(obj))
    if hasattr(obj, "model_dump"):
        return _jsonable(obj.model_dump())
    if hasattr(obj, "__dict__"):
        return _jsonable({k: v for k, v in vars(obj).items() if not k.startswith("_")})
    return str(obj)


class InstallRequest(BaseModel):
    source: str
    name: str | None = None
    installed_by: str | None = None


@router.get("/servers")
async def list_servers(_: bool = Depends(require_gateway_secret)) -> list[dict]:
    return get_mcp_manager().get_all_server_status()


@router.get("/servers/{server_name}/status")
async def server_status(server_name: str, _: bool = Depends(require_gateway_secret)) -> dict:
    status = get_mcp_manager().get_server_status(server_name)
    if status is None:
        raise HTTPException(status_code=404, detail=f"Unknown MCP server: {server_name}")
    return status


@router.get("/tools")
async def list_tools(_: bool = Depends(require_gateway_secret)) -> list[dict]:
    tools = get_mcp_manager().get_all_tools()
    return [
        {
            "server": server,
            "name": getattr(tool, "name", None),
            "description": getattr(tool, "description", None),
        }
        for server, tool in tools
    ]


@router.post("/servers/{server_name}/{action}")
async def lifecycle(
    server_name: str,
    action: str,
    _: bool = Depends(require_gateway_secret),
) -> dict:
    if action not in _LIFECYCLE_ACTIONS:
        raise HTTPException(status_code=400, detail=f"action must be one of {sorted(_LIFECYCLE_ACTIONS)}")
    method = getattr(get_mcp_manager(), f"{action}_server")
    ok = await method(server_name)
    return {"ok": bool(ok)}


@router.post("/reload")
async def reload_servers(_: bool = Depends(require_gateway_secret)) -> dict:
    return await get_mcp_manager().reload()


@router.get("/search")
async def search_smithery(
    query: str,
    page: int = 1,
    page_size: int = 10,
    _: bool = Depends(require_gateway_secret),
) -> dict:
    result = await SmitheryClient().search(query, page=page, page_size=page_size)
    return {"result": _jsonable(result)}


@router.post("/install")
async def install_server(body: InstallRequest, _: bool = Depends(require_gateway_secret)) -> dict:
    result = await MCPInstaller().install(body.source, name=body.name, installed_by=body.installed_by)
    return _jsonable(result)


@router.delete("/servers/{server_name}")
async def uninstall_server(server_name: str, _: bool = Depends(require_gateway_secret)) -> dict:
    ok = await MCPInstaller().uninstall(server_name)
    return {"ok": bool(ok)}
