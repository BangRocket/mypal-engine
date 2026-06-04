"""MCP Smithery OAuth endpoints (internal, gateway-secret auth).

Encapsulates the full OAuth flow server-side so adapters stay thin: load the
server config, run the OAuth client, persist, and (for complete/set-token) start
the server. The redirect URI is computed from the engine's CLARA_API_URL.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from mypalclara.core.mcp import get_mcp_manager
from mypalclara.core.mcp.models import load_server_config, save_server_config
from mypalclara.core.mcp.oauth import SmitheryOAuthClient, load_oauth_state
from mypalclara.gateway.api.auth import require_gateway_secret

router = APIRouter()

_OOB = "urn:ietf:wg:oauth:2.0:oob"


def _redirect_uri() -> str:
    api_url = os.getenv("CLARA_API_URL", "")
    return f"{api_url}/oauth/mcp/callback" if api_url else _OOB


def _load_or_404(server: str):
    config = load_server_config(server)
    if not config:
        raise HTTPException(status_code=404, detail=f"Server '{server}' not found")
    return config


def _server_url(config) -> str | None:
    return getattr(config, "server_url", None)


class CompleteRequest(BaseModel):
    code: str


class TokenRequest(BaseModel):
    access_token: str
    refresh_token: str | None = None


async def _start_after_auth(server: str) -> dict:
    """Persist 'stopped' status and try to start the server; return connect info."""
    config = _load_or_404(server)
    config.status = "stopped"
    config.last_error = None
    save_server_config(config)

    manager = get_mcp_manager()
    connected = await manager.start_server(server)
    tool_count = 0
    if connected:
        status = manager.get_server_status(server)
        tool_count = status.get("tool_count", 0) if status else 0
    return {"connected": bool(connected), "tool_count": tool_count}


@router.post("/servers/{server}/oauth/start")
async def oauth_start(server: str, _: bool = Depends(require_gateway_secret)) -> dict:
    config = _load_or_404(server)
    if config.source_type != "smithery-hosted":
        raise HTTPException(
            status_code=400,
            detail=f"'{server}' is not a hosted Smithery server (OAuth is only for smithery-hosted).",
        )
    redirect_uri = _redirect_uri()
    auth_url = await SmitheryOAuthClient(server, _server_url(config)).start_oauth_flow(redirect_uri)
    if not auth_url:
        raise HTTPException(status_code=502, detail="Failed to start OAuth flow")
    return {"auth_url": auth_url, "redirect_uri": redirect_uri, "oob": redirect_uri == _OOB}


@router.post("/servers/{server}/oauth/complete")
async def oauth_complete(server: str, body: CompleteRequest, _: bool = Depends(require_gateway_secret)) -> dict:
    config = _load_or_404(server)
    redirect_uri = _redirect_uri()
    success = await SmitheryOAuthClient(server, _server_url(config)).exchange_code(body.code, redirect_uri)
    if not success:
        raise HTTPException(status_code=400, detail="Could not exchange authorization code")
    info = await _start_after_auth(server)
    return {"success": True, **info}


@router.get("/servers/{server}/oauth/status")
async def oauth_status(server: str, _: bool = Depends(require_gateway_secret)) -> dict:
    config = _load_or_404(server)
    if config.source_type != "smithery-hosted":
        return {"hosted": False, "source_type": config.source_type, "server_status": config.status}

    state = load_oauth_state(server)
    authorized = bool(state and state.tokens)
    expires_at = state.tokens.expires_at if authorized else None
    expired = state.tokens.is_expired() if authorized else None
    return {
        "hosted": True,
        "source_type": config.source_type,
        "server_status": config.status,
        "authorized": authorized,
        "expires_at": expires_at,
        "expired": expired,
    }


@router.post("/servers/{server}/oauth/token")
async def oauth_set_token(server: str, body: TokenRequest, _: bool = Depends(require_gateway_secret)) -> dict:
    config = _load_or_404(server)
    SmitheryOAuthClient(server, _server_url(config)).set_tokens_manually(body.access_token, body.refresh_token)
    info = await _start_after_auth(server)
    return {"success": True, **info}
