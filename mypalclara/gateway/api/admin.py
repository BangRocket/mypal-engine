"""Admin endpoints for user management."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session as DBSession

from mypalclara.db.models import CanonicalUser, PlatformLink
from mypalclara.gateway.api.auth import get_admin_user, get_db

logger = logging.getLogger("gateway.api.admin")
router = APIRouter()


@router.get("/users")
async def list_users(
    status: str | None = Query(None, pattern="^(pending|active|suspended)$"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    admin: CanonicalUser = Depends(get_admin_user),
    db: DBSession = Depends(get_db),
):
    """List all users, optionally filtered by status."""
    query = db.query(CanonicalUser)
    if status:
        query = query.filter(CanonicalUser.status == status)

    total = query.count()
    users = query.order_by(CanonicalUser.created_at.desc()).offset(offset).limit(limit).all()

    result = []
    for u in users:
        links = db.query(PlatformLink).filter(PlatformLink.canonical_user_id == u.id).all()
        result.append(
            {
                "id": u.id,
                "display_name": u.display_name,
                "email": u.primary_email,
                "avatar_url": u.avatar_url,
                "status": getattr(u, "status", "active"),
                "is_admin": getattr(u, "is_admin", False),
                "created_at": u.created_at.isoformat() if u.created_at else None,
                "platforms": [{"platform": l.platform, "display_name": l.display_name} for l in links],
            }
        )

    return {"users": result, "total": total, "offset": offset, "limit": limit}


@router.post("/users/{user_id}/approve")
async def approve_user(
    user_id: str,
    admin: CanonicalUser = Depends(get_admin_user),
    db: DBSession = Depends(get_db),
):
    """Approve a pending user."""
    user = db.query(CanonicalUser).filter(CanonicalUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.status = "active"
    db.commit()
    return {"ok": True, "user_id": user_id, "status": "active"}


@router.post("/users/{user_id}/suspend")
async def suspend_user(
    user_id: str,
    admin: CanonicalUser = Depends(get_admin_user),
    db: DBSession = Depends(get_db),
):
    """Suspend a user."""
    user = db.query(CanonicalUser).filter(CanonicalUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot suspend yourself")
    user.status = "suspended"
    db.commit()
    return {"ok": True, "user_id": user_id, "status": "suspended"}


@router.get("/users/pending/count")
async def pending_count(
    admin: CanonicalUser = Depends(get_admin_user),
    db: DBSession = Depends(get_db),
):
    """Get count of pending users for badge display."""
    count = db.query(CanonicalUser).filter(CanonicalUser.status == "pending").count()
    return {"count": count}
