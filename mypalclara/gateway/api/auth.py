"""Gateway API authentication — trusts X-Canonical-User-Id from Rails."""

from __future__ import annotations

import os

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session as DBSession

from mypalclara.db.connection import SessionLocal
from mypalclara.db.models import CanonicalUser


def get_db():
    """Yield a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_gateway_secret(
    x_gateway_secret: str | None = Header(None),
) -> bool:
    """Authorize a trusted internal caller (an adapter) via the shared secret.

    Management endpoints (MCP, channel/guild config, backup, sandbox) are not
    user-scoped; they authorize on CLARA_GATEWAY_SECRET alone. Raises 401 if the
    secret is unset on the server or does not match.
    """
    expected = os.getenv("CLARA_GATEWAY_SECRET")
    if not expected or x_gateway_secret != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing gateway secret",
        )
    return True


def get_current_user(
    x_canonical_user_id: str | None = Header(None),
    x_gateway_secret: str | None = Header(None),
    authorization: str | None = Header(None),
    db: DBSession = Depends(get_db),
) -> CanonicalUser:
    """Resolve the current user.

    Web app: ``Authorization: Bearer <clerk-jwt>`` (validated against Clerk).
    Adapters/Rails: trusted ``X-Canonical-User-Id`` header (+ optional secret).
    """
    if authorization and authorization.lower().startswith("bearer "):
        from mypalclara.gateway.api.clerk_auth import (
            ClerkAuthError,
            get_or_create_clerk_user,
            verify_clerk_jwt,
        )

        token = authorization.split(" ", 1)[1].strip()
        try:
            claims = verify_clerk_jwt(token)
        except ClerkAuthError as e:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {e}") from e
        return get_or_create_clerk_user(
            db,
            sub=claims["sub"],
            email=claims.get("email"),
            name=claims.get("name") or claims.get("first_name"),
            avatar=claims.get("image_url") or claims.get("picture"),
        )

    # --- existing trusted-header path below (unchanged) ---
    # Verify gateway secret if configured
    expected_secret = os.getenv("CLARA_GATEWAY_SECRET")
    if expected_secret and x_gateway_secret != expected_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing gateway secret",
        )

    if not x_canonical_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Canonical-User-Id header",
        )

    user = db.query(CanonicalUser).filter(CanonicalUser.id == x_canonical_user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    return user


def get_approved_user(
    user: CanonicalUser = Depends(get_current_user),
) -> CanonicalUser:
    """Require an approved (active) user. Raises 403 if pending/suspended."""
    user_status = getattr(user, "status", "active")
    if user_status != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Account is {user_status}. Admin approval required.",
        )
    return user


def get_admin_user(
    user: CanonicalUser = Depends(get_approved_user),
) -> CanonicalUser:
    """Require an admin user."""
    if not getattr(user, "is_admin", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user
