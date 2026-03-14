"""Google OAuth token provider for MCP integration.

Bridges Clara's per-user OAuth token storage (PostgreSQL) with
Google MCP server token requirements.

IMPORTANT: Current third-party Google MCP servers (google-workspace-mcp-server,
@presto-ai/google-workspace-mcp) use a single-user, static token model via
environment variables. They do NOT support per-user dynamic tokens.

This module provides the infrastructure for:
1. Future custom MCP wrapper that uses Clara's per-user tokens
2. Single-user scenarios where MCP_GOOGLE_REFRESH_TOKEN is set
3. Token validation/checking before MCP operations

For full multi-user support, a custom MCP wrapper server is needed.
See: mcp_servers/google_workspace/ (planned)
"""

import os
from typing import Optional

# Import Clara's existing OAuth infrastructure
from tools.google_oauth import (
    get_valid_token,
    is_user_connected,
)
from tools.google_oauth import (
    is_configured as is_google_oauth_configured,
)


async def get_user_token(user_id: str) -> Optional[str]:
    """Get a valid Google OAuth token for a user.

    This wraps Clara's existing token management which handles:
    - Token storage in PostgreSQL (GoogleOAuthToken table)
    - Automatic refresh when expired (5 min buffer)
    - Scopes: Sheets, Drive, Docs, Calendar, Gmail

    Args:
        user_id: Discord user ID or other identifier

    Returns:
        Valid access token or None if not connected
    """
    return await get_valid_token(user_id)


def is_user_google_connected(user_id: str) -> bool:
    """Check if a user has connected their Google account.

    Args:
        user_id: Discord user ID or other identifier

    Returns:
        True if user has stored OAuth tokens
    """
    return is_user_connected(user_id)


def is_google_configured() -> bool:
    """Check if Google OAuth is configured in environment.

    Returns:
        True if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are set
    """
    return is_google_oauth_configured()


def get_google_config() -> dict:
    """Get Google OAuth configuration for MCP server.

    Returns dict with:
    - client_id: OAuth client ID
    - client_secret: OAuth client secret
    - redirect_uri: OAuth redirect URI
    - configured: bool indicating if OAuth is set up
    - has_static_token: bool indicating if MCP_GOOGLE_REFRESH_TOKEN is set
    """
    return {
        "client_id": os.getenv("GOOGLE_CLIENT_ID", ""),
        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET", ""),
        "redirect_uri": os.getenv("GOOGLE_REDIRECT_URI", ""),
        "configured": is_google_configured(),
        "has_static_token": bool(os.getenv("MCP_GOOGLE_REFRESH_TOKEN")),
    }


def get_mcp_google_env() -> dict:
    """Get environment variables for Google MCP server.

    Returns dict suitable for passing to MCP server process.
    Uses MCP_GOOGLE_REFRESH_TOKEN for single-user scenarios.

    Note: This only works for single-user deployments. For multi-user,
    a custom MCP wrapper that calls get_user_token() per request is needed.
    """
    return {
        "GOOGLE_CLIENT_ID": os.getenv("GOOGLE_CLIENT_ID", ""),
        "GOOGLE_CLIENT_SECRET": os.getenv("GOOGLE_CLIENT_SECRET", ""),
        "GOOGLE_REFRESH_TOKEN": os.getenv("MCP_GOOGLE_REFRESH_TOKEN", ""),
    }


def can_use_google_mcp(user_id: Optional[str] = None) -> tuple[bool, str]:
    """Check if Google MCP can be used, with reason if not.

    Args:
        user_id: Optional user ID to check per-user token availability

    Returns:
        Tuple of (can_use: bool, reason: str)
    """
    if not is_google_configured():
        return False, "Google OAuth not configured (missing GOOGLE_CLIENT_ID/SECRET)"

    # For single-user MCP mode
    if os.getenv("MCP_GOOGLE_REFRESH_TOKEN"):
        return True, "Using static MCP_GOOGLE_REFRESH_TOKEN (single-user mode)"

    # For per-user mode (requires custom wrapper)
    if user_id and is_user_google_connected(user_id):
        return True, f"User {user_id} has Google connected (per-user mode)"

    if user_id:
        return False, f"User {user_id} has not connected Google account"

    return False, "No MCP_GOOGLE_REFRESH_TOKEN set and no user_id provided"
