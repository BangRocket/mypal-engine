"""OAuth callback handler for MCP server authentication.

Provides HTTP endpoints for handling OAuth callbacks from Smithery
and other MCP servers that require authentication.

Can be run standalone or integrated into a FastAPI/ASGI application:

Standalone:
    python -m clara_core.mcp.oauth_callback --port 8080

Integration:
    from mypalclara.core.mcp.oauth_callback import get_oauth_router
    app.include_router(get_oauth_router())
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

# Callback state storage - maps state tokens to server names
_pending_callbacks: dict[str, dict[str, Any]] = {}


def register_callback(
    state_token: str,
    server_name: str,
    user_id: str | None = None,
    redirect_uri: str | None = None,
) -> None:
    """Register a pending OAuth callback.

    Args:
        state_token: OAuth state token (from generate_state_token)
        server_name: MCP server name for this auth flow
        user_id: Optional user ID for multi-user support
        redirect_uri: Redirect URI used in the auth request
    """
    _pending_callbacks[state_token] = {
        "server_name": server_name,
        "user_id": user_id,
        "redirect_uri": redirect_uri,
    }
    logger.debug(f"[OAuth Callback] Registered pending callback for {server_name}")


def get_pending_callback(state_token: str) -> dict[str, Any] | None:
    """Get and remove a pending callback.

    Args:
        state_token: OAuth state token

    Returns:
        Callback info or None if not found
    """
    return _pending_callbacks.pop(state_token, None)


async def handle_oauth_callback(
    code: str,
    state: str,
    error: str | None = None,
    error_description: str | None = None,
) -> dict[str, Any]:
    """Handle an OAuth callback.

    Args:
        code: Authorization code from the OAuth provider
        state: State token for CSRF protection
        error: Error code if authorization failed
        error_description: Human-readable error message

    Returns:
        Result dict with success status and message
    """
    # Check for OAuth error
    if error:
        logger.warning(f"[OAuth Callback] OAuth error: {error} - {error_description}")
        return {
            "success": False,
            "error": error,
            "error_description": error_description,
            "message": f"Authorization failed: {error_description or error}",
        }

    # Find the pending callback
    callback_info = get_pending_callback(state)
    if not callback_info:
        logger.warning(f"[OAuth Callback] Unknown state token: {state[:20]}...")
        return {
            "success": False,
            "error": "invalid_state",
            "message": "Unknown or expired state token. Please restart the authorization flow.",
        }

    server_name = callback_info["server_name"]
    user_id = callback_info.get("user_id")
    redirect_uri = callback_info.get("redirect_uri")

    logger.info(f"[OAuth Callback] Processing callback for server: {server_name}")

    try:
        from mypalclara.core.mcp.oauth import SmitheryOAuthClient

        # Create client and exchange code
        client = SmitheryOAuthClient(server_name)

        if not await client.exchange_code(code, redirect_uri):
            return {
                "success": False,
                "error": "token_exchange_failed",
                "message": "Failed to exchange authorization code. Please try again.",
            }

        # Store token in database for multi-user support
        if user_id:
            await _store_token_in_db(
                user_id=user_id,
                server_name=server_name,
                access_token=client.access_token,
                refresh_token=client._state.tokens.refresh_token if client._state and client._state.tokens else None,
                expires_at=client._state.tokens.expires_at if client._state and client._state.tokens else None,
            )

        # Try to connect the server
        from mypalclara.core.mcp.manager import MCPServerManager
        from mypalclara.core.mcp.models import load_remote_server_config, save_remote_server_config

        config = load_remote_server_config(server_name)
        if config:
            config.status = "stopped"
            config.last_error = None
            save_remote_server_config(config)

        manager = MCPServerManager.get_instance()
        connected = await manager.start_server(server_name)

        return {
            "success": True,
            "server_name": server_name,
            "connected": connected,
            "message": f"Authorization successful for {server_name}. "
            + ("Server connected!" if connected else "Please try connecting the server again."),
        }

    except Exception as e:
        logger.error(f"[OAuth Callback] Error processing callback: {e}")
        return {
            "success": False,
            "error": "internal_error",
            "message": f"Error processing authorization: {e}",
        }


async def _store_token_in_db(
    user_id: str,
    server_name: str,
    access_token: str,
    refresh_token: str | None,
    expires_at: str | None,
) -> None:
    """Store OAuth token in database for multi-user support.

    Args:
        user_id: User ID
        server_name: MCP server name
        access_token: OAuth access token
        refresh_token: OAuth refresh token
        expires_at: Token expiration time (ISO format)
    """
    try:
        from datetime import datetime

        from mypalclara.db import SessionLocal
        from mypalclara.db.mcp_models import MCPOAuthToken

        db = SessionLocal()
        try:
            # Find or create token record
            token = (
                db.query(MCPOAuthToken)
                .filter(MCPOAuthToken.user_id == user_id)
                .filter(MCPOAuthToken.server_name == server_name)
                .first()
            )

            if not token:
                token = MCPOAuthToken(
                    user_id=user_id,
                    server_name=server_name,
                )
                db.add(token)

            token.access_token = access_token
            token.refresh_token = refresh_token
            token.status = "authorized"

            if expires_at:
                try:
                    token.expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00")).replace(tzinfo=None)
                except ValueError:
                    pass

            db.commit()
            logger.info(f"[OAuth Callback] Stored token for {user_id}/{server_name}")

        finally:
            db.close()

    except Exception as e:
        logger.error(f"[OAuth Callback] Failed to store token in DB: {e}")


def get_oauth_router():
    """Get a FastAPI router for OAuth callbacks.

    Returns:
        FastAPI APIRouter with OAuth callback endpoints
    """
    try:
        from fastapi import APIRouter, Query, Request
        from fastapi.responses import HTMLResponse, JSONResponse
    except ImportError:
        logger.error("[OAuth Callback] FastAPI not available")
        return None

    router = APIRouter(prefix="/oauth/mcp", tags=["mcp-oauth"])

    @router.get("/callback")
    async def oauth_callback(
        request: Request,
        code: str | None = Query(None),
        state: str | None = Query(None),
        error: str | None = Query(None),
        error_description: str | None = Query(None),
    ):
        """Handle OAuth callback from MCP servers."""
        if not state:
            return JSONResponse(
                status_code=400,
                content={"error": "missing_state", "message": "No state parameter provided"},
            )

        result = await handle_oauth_callback(
            code=code or "",
            state=state,
            error=error,
            error_description=error_description,
        )

        # Return a nice HTML page for browser callbacks
        if result["success"]:
            html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Authorization Successful</title>
                <style>
                    body {{ font-family: system-ui, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; }}
                    .success {{ color: #22c55e; }}
                    .message {{ margin-top: 20px; }}
                </style>
            </head>
            <body>
                <h1 class="success">✓ Authorization Successful</h1>
                <p class="message">{result['message']}</p>
                <p>You can close this window and return to Clara.</p>
            </body>
            </html>
            """
        else:
            html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Authorization Failed</title>
                <style>
                    body {{ font-family: system-ui, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; }}
                    .error {{ color: #ef4444; }}
                    .message {{ margin-top: 20px; }}
                </style>
            </head>
            <body>
                <h1 class="error">✗ Authorization Failed</h1>
                <p class="message">{result['message']}</p>
                <p>Please try the authorization process again.</p>
            </body>
            </html>
            """

        return HTMLResponse(content=html)

    @router.get("/status/{server_name}")
    async def oauth_status(server_name: str):
        """Check OAuth status for a server."""
        from mypalclara.core.mcp.oauth import load_oauth_state

        state = load_oauth_state(server_name)
        if not state:
            return JSONResponse(content={"status": "not_configured", "server_name": server_name})

        if state.tokens:
            return JSONResponse(
                content={
                    "status": "authorized",
                    "server_name": server_name,
                    "expires_at": state.tokens.expires_at,
                    "is_expired": state.tokens.is_expired(),
                }
            )

        return JSONResponse(content={"status": "pending", "server_name": server_name})

    return router


async def run_standalone_server(host: str = "0.0.0.0", port: int = 8080) -> None:
    """Run a standalone OAuth callback server.

    Args:
        host: Bind address
        port: Port to listen on
    """
    try:
        import uvicorn
        from fastapi import FastAPI
    except ImportError:
        logger.error("[OAuth Callback] FastAPI/Uvicorn not available")
        return

    app = FastAPI(title="Clara MCP OAuth Callback")
    router = get_oauth_router()
    if router:
        app.include_router(router)

    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


def main():
    """Entry point for standalone server."""
    import argparse

    parser = argparse.ArgumentParser(description="MCP OAuth Callback Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address")
    parser.add_argument("--port", type=int, default=8080, help="Port to listen on")
    args = parser.parse_args()

    asyncio.run(run_standalone_server(args.host, args.port))


if __name__ == "__main__":
    main()
