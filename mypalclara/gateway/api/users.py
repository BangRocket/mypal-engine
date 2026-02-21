"""User management and adapter linking endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from mypalclara.db.models import CanonicalUser, PlatformLink, utcnow
from mypalclara.gateway.api.auth import get_approved_user, get_db

router = APIRouter()


class UserUpdate(BaseModel):
    display_name: str | None = None
    avatar_url: str | None = None


@router.get("/me")
async def get_me(
    user: CanonicalUser = Depends(get_approved_user),
    db: DBSession = Depends(get_db),
):
    """Get current user with linked accounts."""
    links = db.query(PlatformLink).filter(PlatformLink.canonical_user_id == user.id).all()
    return {
        "id": user.id,
        "display_name": user.display_name,
        "email": user.primary_email,
        "avatar_url": user.avatar_url,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "links": [
            {
                "id": l.id,
                "platform": l.platform,
                "platform_user_id": l.platform_user_id,
                "prefixed_user_id": l.prefixed_user_id,
                "display_name": l.display_name,
                "linked_at": l.linked_at.isoformat() if l.linked_at else None,
                "linked_via": l.linked_via,
            }
            for l in links
        ],
    }


@router.put("/me")
async def update_me(
    body: UserUpdate,
    user: CanonicalUser = Depends(get_approved_user),
    db: DBSession = Depends(get_db),
):
    """Update current user settings."""
    if body.display_name is not None:
        user.display_name = body.display_name
    if body.avatar_url is not None:
        user.avatar_url = body.avatar_url
    user.updated_at = utcnow()
    db.commit()
    return {"ok": True}


@router.get("/me/links")
async def get_links(
    user: CanonicalUser = Depends(get_approved_user),
    db: DBSession = Depends(get_db),
):
    """List platform links."""
    links = db.query(PlatformLink).filter(PlatformLink.canonical_user_id == user.id).all()
    return {
        "links": [
            {
                "id": l.id,
                "platform": l.platform,
                "platform_user_id": l.platform_user_id,
                "prefixed_user_id": l.prefixed_user_id,
                "display_name": l.display_name,
                "linked_at": l.linked_at.isoformat() if l.linked_at else None,
                "linked_via": l.linked_via,
            }
            for l in links
        ]
    }
