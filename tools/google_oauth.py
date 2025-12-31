"""Google OAuth 2.0 helper module.

Handles OAuth flow, token storage, refresh, and revocation for Google Workspace.
Tokens are stored per-user in the database.
"""

from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx

from db.connection import SessionLocal
from db.models import GoogleOAuthToken

# OAuth configuration from environment
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "")

# Google OAuth endpoints
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"

# Scopes for Google Workspace (Sheets, Drive, Docs, Calendar)
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",  # Read access to all Drive files
    "https://www.googleapis.com/auth/drive.file",      # Write access to app-created files
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/calendar",        # Full calendar access
]


def is_configured() -> bool:
    """Check if Google OAuth is configured."""
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REDIRECT_URI)


def get_authorization_url(user_id: str) -> str:
    """Generate OAuth authorization URL with user_id encoded in state.

    Args:
        user_id: User identifier to encode in state parameter

    Returns:
        Full authorization URL for user to visit
    """
    if not is_configured():
        raise ValueError("Google OAuth not configured - check env vars")

    # Encode user_id in state (base64 for URL safety)
    state = base64.urlsafe_b64encode(user_id.encode()).decode()

    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "state": state,
        "access_type": "offline",  # Get refresh token
        "prompt": "consent",  # Always show consent to get refresh token
    }

    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def decode_state(state: str) -> str:
    """Decode user_id from state parameter.

    Args:
        state: Base64-encoded state from callback

    Returns:
        Decoded user_id
    """
    return base64.urlsafe_b64decode(state.encode()).decode()


async def exchange_code_for_tokens(code: str, user_id: str) -> dict:
    """Exchange authorization code for access and refresh tokens.

    Args:
        code: Authorization code from callback
        user_id: User to store tokens for

    Returns:
        Token response dict
    """
    if not is_configured():
        raise ValueError("Google OAuth not configured")

    async with httpx.AsyncClient() as client:
        response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": GOOGLE_REDIRECT_URI,
            },
            timeout=30.0,
        )

        if response.status_code != 200:
            error_data = response.json() if response.text else {}
            err = error_data.get("error_description", response.text)
            raise ValueError(f"Token exchange failed: {err}")

        token_data = response.json()

    # Calculate expiry time
    expires_in = token_data.get("expires_in", 3600)
    expires_at = datetime.now(UTC).replace(tzinfo=None)
    from datetime import timedelta

    expires_at = expires_at + timedelta(seconds=expires_in)

    # Store in database
    with SessionLocal() as session:
        # Check for existing token
        existing = (
            session.query(GoogleOAuthToken)
            .filter(GoogleOAuthToken.user_id == user_id)
            .first()
        )

        if existing:
            # Update existing
            existing.access_token = token_data["access_token"]
            existing.refresh_token = token_data.get(
                "refresh_token", existing.refresh_token
            )
            existing.token_type = token_data.get("token_type", "Bearer")
            existing.expires_at = expires_at
            existing.scopes = json.dumps(GOOGLE_SCOPES)
        else:
            # Create new
            new_token = GoogleOAuthToken(
                user_id=user_id,
                access_token=token_data["access_token"],
                refresh_token=token_data.get("refresh_token"),
                token_type=token_data.get("token_type", "Bearer"),
                expires_at=expires_at,
                scopes=json.dumps(GOOGLE_SCOPES),
            )
            session.add(new_token)

        session.commit()

    return token_data


async def get_valid_token(user_id: str) -> str | None:
    """Get a valid access token for user, refreshing if needed.

    Args:
        user_id: User to get token for

    Returns:
        Valid access token or None if not connected
    """
    with SessionLocal() as session:
        token_record = (
            session.query(GoogleOAuthToken)
            .filter(GoogleOAuthToken.user_id == user_id)
            .first()
        )

        if not token_record:
            return None

        # Check if token is expired (with 5 min buffer)
        now = datetime.now(UTC).replace(tzinfo=None)
        from datetime import timedelta

        if token_record.expires_at and token_record.expires_at < now + timedelta(
            minutes=5
        ):
            # Need to refresh
            if not token_record.refresh_token:
                # No refresh token, user needs to re-auth
                return None

            new_access_token = await refresh_access_token(
                user_id, token_record.refresh_token
            )
            return new_access_token

        return token_record.access_token


async def refresh_access_token(user_id: str, refresh_token: str) -> str | None:
    """Refresh an expired access token.

    Args:
        user_id: User to refresh token for
        refresh_token: Current refresh token

    Returns:
        New access token or None on failure
    """
    if not is_configured():
        return None

    async with httpx.AsyncClient() as client:
        response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=30.0,
        )

        if response.status_code != 200:
            # Refresh failed - token might be revoked
            return None

        token_data = response.json()

    # Calculate new expiry
    expires_in = token_data.get("expires_in", 3600)
    from datetime import timedelta

    expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(
        seconds=expires_in
    )

    # Update in database
    with SessionLocal() as session:
        token_record = (
            session.query(GoogleOAuthToken)
            .filter(GoogleOAuthToken.user_id == user_id)
            .first()
        )

        if token_record:
            token_record.access_token = token_data["access_token"]
            token_record.expires_at = expires_at
            # Refresh tokens don't change unless Google sends a new one
            if "refresh_token" in token_data:
                token_record.refresh_token = token_data["refresh_token"]
            session.commit()

    return token_data["access_token"]


async def revoke_token(user_id: str) -> bool:
    """Revoke tokens and delete from database.

    Args:
        user_id: User to revoke tokens for

    Returns:
        True if revoked successfully
    """
    with SessionLocal() as session:
        token_record = (
            session.query(GoogleOAuthToken)
            .filter(GoogleOAuthToken.user_id == user_id)
            .first()
        )

        if not token_record:
            return True  # Already disconnected

        # Try to revoke with Google
        async with httpx.AsyncClient() as client:
            # Revoke refresh token (also invalidates access token)
            token_to_revoke = token_record.refresh_token or token_record.access_token
            await client.post(
                GOOGLE_REVOKE_URL,
                params={"token": token_to_revoke},
                timeout=30.0,
            )
            # Ignore response - we delete locally regardless

        # Delete from database
        session.delete(token_record)
        session.commit()

    return True


def is_user_connected(user_id: str) -> bool:
    """Check if user has connected their Google account.

    Args:
        user_id: User to check

    Returns:
        True if user has stored tokens
    """
    with SessionLocal() as session:
        token_record = (
            session.query(GoogleOAuthToken)
            .filter(GoogleOAuthToken.user_id == user_id)
            .first()
        )
        return token_record is not None
