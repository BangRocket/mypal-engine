"""OAuth support for MCP server authentication.

This module provides OAuth 2.0 support for connecting to MCP servers
that require authentication, particularly Smithery-hosted servers.

The OAuth flow:
1. Client attempts connection, receives 401 with OAuth metadata
2. User is directed to authorization URL
3. User authorizes and receives an authorization code
4. Code is exchanged for access/refresh tokens
5. Tokens are stored and used for future connections
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

# OAuth token storage directory
OAUTH_TOKENS_DIR = Path(os.getenv("MCP_OAUTH_DIR", ".mcp_servers/.oauth"))


@dataclass
class OAuthTokens:
    """OAuth tokens for an MCP server."""

    access_token: str
    token_type: str = "Bearer"
    refresh_token: str | None = None
    expires_at: str | None = None  # ISO timestamp
    scope: str | None = None

    def is_expired(self) -> bool:
        """Check if the access token is expired."""
        if not self.expires_at:
            return False
        try:
            expires = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
            # Consider expired 5 minutes before actual expiry
            return datetime.now(timezone.utc) >= expires
        except (ValueError, TypeError):
            return False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OAuthTokens:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class OAuthClientInfo:
    """OAuth client registration info."""

    client_id: str
    client_secret: str | None = None
    redirect_uri: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OAuthClientInfo:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class OAuthState:
    """OAuth flow state for a server."""

    server_url: str
    authorization_endpoint: str | None = None
    token_endpoint: str | None = None
    registration_endpoint: str | None = None
    code_verifier: str | None = None
    state: str | None = None
    client_info: OAuthClientInfo | None = None
    tokens: OAuthTokens | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.client_info:
            d["client_info"] = self.client_info.to_dict()
        if self.tokens:
            d["tokens"] = self.tokens.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OAuthState:
        client_info = data.pop("client_info", None)
        tokens = data.pop("tokens", None)

        state = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

        if client_info:
            state.client_info = OAuthClientInfo.from_dict(client_info)
        if tokens:
            state.tokens = OAuthTokens.from_dict(tokens)

        return state


def _get_state_path(server_name: str) -> Path:
    """Get the path to store OAuth state for a server."""
    return OAUTH_TOKENS_DIR / f"{server_name}.json"


def save_oauth_state(server_name: str, state: OAuthState) -> bool:
    """Save OAuth state for a server."""
    try:
        OAUTH_TOKENS_DIR.mkdir(parents=True, exist_ok=True)
        state.updated_at = datetime.now(timezone.utc).isoformat()
        with open(_get_state_path(server_name), "w") as f:
            json.dump(state.to_dict(), f, indent=2)
        return True
    except OSError as e:
        logger.error(f"[OAuth] Failed to save state for {server_name}: {e}")
        return False


def load_oauth_state(server_name: str) -> OAuthState | None:
    """Load OAuth state for a server."""
    path = _get_state_path(server_name)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        return OAuthState.from_dict(data)
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"[OAuth] Failed to load state for {server_name}: {e}")
        return None


def delete_oauth_state(server_name: str) -> bool:
    """Delete OAuth state for a server."""
    path = _get_state_path(server_name)
    try:
        if path.exists():
            path.unlink()
        return True
    except OSError as e:
        logger.error(f"[OAuth] Failed to delete state for {server_name}: {e}")
        return False


def generate_code_verifier() -> str:
    """Generate a PKCE code verifier."""
    return secrets.token_urlsafe(32)


def generate_code_challenge(verifier: str) -> str:
    """Generate a PKCE code challenge from verifier."""
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def generate_state_token() -> str:
    """Generate a random state token for OAuth."""
    return secrets.token_urlsafe(16)


class SmitheryOAuthClient:
    """OAuth client for Smithery-hosted MCP servers.

    Handles the OAuth 2.0 flow for authenticating with Smithery servers:
    1. Discover OAuth endpoints from server metadata
    2. Register client dynamically (if needed)
    3. Generate authorization URL for user
    4. Exchange authorization code for tokens
    5. Refresh tokens when expired
    """

    # Smithery server base URL
    SMITHERY_SERVER_BASE = "https://server.smithery.ai"

    # Client metadata for dynamic registration
    CLIENT_NAME = "Clara AI Assistant"
    CLIENT_URI = "https://github.com/BangRocket/mypalclara"

    def __init__(self, server_name: str, server_url: str | None = None) -> None:
        """Initialize the OAuth client.

        Args:
            server_name: Name to identify this server (for state storage)
            server_url: Full server URL (e.g., https://server.smithery.ai/exa)
        """
        self.server_name = server_name
        self.server_url = server_url or f"{self.SMITHERY_SERVER_BASE}/{server_name}"
        self._state = load_oauth_state(server_name)

    @property
    def has_valid_tokens(self) -> bool:
        """Check if we have valid (non-expired) tokens."""
        if not self._state or not self._state.tokens:
            return False
        if self._state.tokens.is_expired():
            # Could try refresh here, but leave that for explicit refresh call
            return bool(self._state.tokens.refresh_token)
        return True

    @property
    def access_token(self) -> str | None:
        """Get the current access token."""
        if self._state and self._state.tokens:
            return self._state.tokens.access_token
        return None

    def get_auth_headers(self) -> dict[str, str]:
        """Get Authorization headers for requests."""
        if self._state and self._state.tokens:
            return {"Authorization": f"Bearer {self._state.tokens.access_token}"}
        return {}

    async def discover_oauth_metadata(self) -> bool:
        """Discover OAuth endpoints from the server.

        Returns:
            True if discovery was successful
        """
        # MCP OAuth metadata endpoint
        metadata_url = f"{self.server_url}/.well-known/oauth-authorization-server"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(metadata_url)

                if response.status_code != 200:
                    logger.warning(f"[OAuth] Metadata discovery failed: {response.status_code}")
                    return False

                data = response.json()

                if not self._state:
                    self._state = OAuthState(server_url=self.server_url)

                self._state.authorization_endpoint = data.get("authorization_endpoint")
                self._state.token_endpoint = data.get("token_endpoint")
                self._state.registration_endpoint = data.get("registration_endpoint")

                save_oauth_state(self.server_name, self._state)

                logger.info(f"[OAuth] Discovered endpoints for {self.server_name}")
                return True

        except Exception as e:
            logger.error(f"[OAuth] Metadata discovery error: {e}")
            return False

    async def register_client(self, redirect_uri: str) -> bool:
        """Dynamically register OAuth client with the server.

        Args:
            redirect_uri: Redirect URI for the OAuth callback

        Returns:
            True if registration was successful
        """
        if not self._state or not self._state.registration_endpoint:
            logger.error("[OAuth] No registration endpoint available")
            return False

        client_metadata = {
            "client_name": self.CLIENT_NAME,
            "client_uri": self.CLIENT_URI,
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "scope": "read write",
            "token_endpoint_auth_method": "none",
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self._state.registration_endpoint,
                    json=client_metadata,
                )

                if response.status_code not in (200, 201):
                    logger.error(f"[OAuth] Client registration failed: {response.status_code} - {response.text[:200]}")
                    return False

                data = response.json()

                self._state.client_info = OAuthClientInfo(
                    client_id=data["client_id"],
                    client_secret=data.get("client_secret"),
                    redirect_uri=redirect_uri,
                )

                save_oauth_state(self.server_name, self._state)

                logger.info(f"[OAuth] Registered client for {self.server_name}")
                return True

        except Exception as e:
            logger.error(f"[OAuth] Client registration error: {e}")
            return False

    def generate_authorization_url(self, redirect_uri: str | None = None) -> str | None:
        """Generate the authorization URL for the user to visit.

        Args:
            redirect_uri: Override redirect URI (uses stored one if not provided)

        Returns:
            Authorization URL or None if not ready
        """
        if not self._state or not self._state.authorization_endpoint:
            logger.error("[OAuth] No authorization endpoint available")
            return None

        if not self._state.client_info:
            logger.error("[OAuth] No client registration available")
            return None

        # Generate PKCE values
        self._state.code_verifier = generate_code_verifier()
        code_challenge = generate_code_challenge(self._state.code_verifier)
        self._state.state = generate_state_token()

        save_oauth_state(self.server_name, self._state)

        params = {
            "response_type": "code",
            "client_id": self._state.client_info.client_id,
            "redirect_uri": redirect_uri or self._state.client_info.redirect_uri,
            "scope": "read write",
            "state": self._state.state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

        return f"{self._state.authorization_endpoint}?{urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str | None = None) -> bool:
        """Exchange authorization code for tokens.

        Args:
            code: Authorization code from callback
            redirect_uri: Redirect URI used in authorization request

        Returns:
            True if token exchange was successful
        """
        if not self._state or not self._state.token_endpoint:
            logger.error("[OAuth] No token endpoint available")
            return False

        if not self._state.client_info or not self._state.code_verifier:
            logger.error("[OAuth] Missing client info or code verifier")
            return False

        form_data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri or self._state.client_info.redirect_uri,
            "client_id": self._state.client_info.client_id,
            "code_verifier": self._state.code_verifier,
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self._state.token_endpoint,
                    data=form_data,
                )

                if response.status_code != 200:
                    logger.error(f"[OAuth] Token exchange failed: {response.status_code} - {response.text[:200]}")
                    return False

                token_data = response.json()

                # Calculate expiry time
                expires_at = None
                if "expires_in" in token_data:
                    from datetime import timedelta

                    expires_at = (
                        datetime.now(timezone.utc) + timedelta(seconds=token_data["expires_in"])
                    ).isoformat()

                self._state.tokens = OAuthTokens(
                    access_token=token_data["access_token"],
                    token_type=token_data.get("token_type", "Bearer"),
                    refresh_token=token_data.get("refresh_token"),
                    expires_at=expires_at,
                    scope=token_data.get("scope"),
                )

                # Clear code verifier after use
                self._state.code_verifier = None

                save_oauth_state(self.server_name, self._state)

                logger.info(f"[OAuth] Token exchange successful for {self.server_name}")
                return True

        except Exception as e:
            logger.error(f"[OAuth] Token exchange error: {e}")
            return False

    async def refresh_tokens(self) -> bool:
        """Refresh the access token using the refresh token.

        Returns:
            True if refresh was successful
        """
        if not self._state or not self._state.token_endpoint:
            logger.error("[OAuth] No token endpoint available")
            return False

        if not self._state.tokens or not self._state.tokens.refresh_token:
            logger.error("[OAuth] No refresh token available")
            return False

        if not self._state.client_info:
            logger.error("[OAuth] No client info available")
            return False

        form_data = {
            "grant_type": "refresh_token",
            "refresh_token": self._state.tokens.refresh_token,
            "client_id": self._state.client_info.client_id,
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self._state.token_endpoint,
                    data=form_data,
                )

                if response.status_code != 200:
                    logger.error(f"[OAuth] Token refresh failed: {response.status_code} - {response.text[:200]}")
                    return False

                token_data = response.json()

                # Calculate expiry time
                expires_at = None
                if "expires_in" in token_data:
                    from datetime import timedelta

                    expires_at = (
                        datetime.now(timezone.utc) + timedelta(seconds=token_data["expires_in"])
                    ).isoformat()

                self._state.tokens = OAuthTokens(
                    access_token=token_data["access_token"],
                    token_type=token_data.get("token_type", "Bearer"),
                    refresh_token=token_data.get("refresh_token", self._state.tokens.refresh_token),
                    expires_at=expires_at,
                    scope=token_data.get("scope"),
                )

                save_oauth_state(self.server_name, self._state)

                logger.info(f"[OAuth] Token refresh successful for {self.server_name}")
                return True

        except Exception as e:
            logger.error(f"[OAuth] Token refresh error: {e}")
            return False

    async def ensure_valid_token(self) -> bool:
        """Ensure we have a valid access token, refreshing if needed.

        Returns:
            True if we have a valid token
        """
        if not self._state or not self._state.tokens:
            return False

        if not self._state.tokens.is_expired():
            return True

        if self._state.tokens.refresh_token:
            return await self.refresh_tokens()

        return False

    def set_tokens_manually(self, access_token: str, refresh_token: str | None = None) -> None:
        """Manually set tokens (for pre-configured access).

        Args:
            access_token: OAuth access token
            refresh_token: Optional refresh token
        """
        if not self._state:
            self._state = OAuthState(server_url=self.server_url)

        self._state.tokens = OAuthTokens(
            access_token=access_token,
            refresh_token=refresh_token,
        )

        save_oauth_state(self.server_name, self._state)
        logger.info(f"[OAuth] Manually set tokens for {self.server_name}")

    async def start_oauth_flow(self, redirect_uri: str) -> str | None:
        """Start the OAuth flow and return the authorization URL.

        This is a convenience method that:
        1. Discovers OAuth endpoints
        2. Registers the client
        3. Generates the authorization URL

        Args:
            redirect_uri: Callback URL for OAuth redirect

        Returns:
            Authorization URL to send user to, or None on failure
        """
        # Step 1: Discover endpoints
        if not await self.discover_oauth_metadata():
            logger.error(f"[OAuth] Failed to discover OAuth endpoints for {self.server_name}")
            return None

        # Step 2: Register client if needed
        if not self._state.client_info:
            if not await self.register_client(redirect_uri):
                logger.error(f"[OAuth] Failed to register client for {self.server_name}")
                return None

        # Step 3: Generate auth URL
        return self.generate_authorization_url(redirect_uri)

    def clear_tokens(self) -> None:
        """Clear stored tokens (for logout/disconnect)."""
        delete_oauth_state(self.server_name)
        self._state = None
        logger.info(f"[OAuth] Cleared tokens for {self.server_name}")


def get_smithery_server_url(server_name: str) -> str:
    """Get the Smithery server URL for a given server name.

    Args:
        server_name: Smithery server name (e.g., "exa", "@smithery/notion")

    Returns:
        Full server URL
    """
    return f"{SmitheryOAuthClient.SMITHERY_SERVER_BASE}/{server_name}"
