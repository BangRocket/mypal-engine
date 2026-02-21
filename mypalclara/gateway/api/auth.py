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


def get_current_user(
    x_canonical_user_id: str | None = Header(None),
    x_gateway_secret: str | None = Header(None),
    db: DBSession = Depends(get_db),
) -> CanonicalUser:
    """Extract the current user from the X-Canonical-User-Id header.

    In the unified architecture, Rails authenticates users and forwards
    the canonical user ID via a trusted internal header. Optionally
    verifies X-Gateway-Secret if CLARA_GATEWAY_SECRET is set.
    """
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
