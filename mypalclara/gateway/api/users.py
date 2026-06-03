"""User management and adapter linking endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from mypalclara.db.models import CanonicalUser, PlatformLink, utcnow
from mypalclara.gateway.api.auth import get_approved_user, get_db, require_gateway_secret

router = APIRouter()


class UserUpdate(BaseModel):
    display_name: str | None = None
    avatar_url: str | None = None


class LinkCreate(BaseModel):
    platform: str
    platform_user_id: str
    prefixed_user_id: str
    display_name: str | None = None
    canonical_user_id: str | None = None
    linked_via: str | None = None


def _serialize_link(link: PlatformLink) -> dict:
    return {
        "id": link.id,
        "canonical_user_id": link.canonical_user_id,
        "platform": link.platform,
        "platform_user_id": link.platform_user_id,
        "prefixed_user_id": link.prefixed_user_id,
        "display_name": link.display_name,
        "linked_at": link.linked_at.isoformat() if link.linked_at else None,
        "linked_via": link.linked_via,
    }


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


# --- Identity-link management (internal, gateway-secret auth) ---
# Replaces the CLI adapter's direct DB access for resolving/creating/deleting
# PlatformLinks. Declared AFTER the static /me routes so those take precedence.


@router.get("/links/{prefixed_user_id}")
async def resolve_link(
    prefixed_user_id: str,
    _: bool = Depends(require_gateway_secret),
    db: DBSession = Depends(get_db),
):
    """Resolve a platform link (and its canonical user) by prefixed_user_id."""
    link = db.query(PlatformLink).filter(PlatformLink.prefixed_user_id == prefixed_user_id).first()
    if not link:
        raise HTTPException(status_code=404, detail="Link not found")
    cu = db.query(CanonicalUser).filter(CanonicalUser.id == link.canonical_user_id).first()
    return {
        "link": _serialize_link(link),
        "canonical_user": (
            {"id": cu.id, "display_name": cu.display_name, "status": getattr(cu, "status", None)} if cu else None
        ),
    }


@router.get("/{canonical_id}/links")
async def list_user_links(
    canonical_id: str,
    _: bool = Depends(require_gateway_secret),
    db: DBSession = Depends(get_db),
):
    """List all platform links for a canonical user."""
    links = db.query(PlatformLink).filter(PlatformLink.canonical_user_id == canonical_id).all()
    return {"links": [_serialize_link(l) for l in links]}


@router.post("/links", status_code=201)
async def create_link(
    body: LinkCreate,
    _: bool = Depends(require_gateway_secret),
    db: DBSession = Depends(get_db),
):
    """Create a platform link, creating a CanonicalUser when none is given."""
    existing = db.query(PlatformLink).filter(PlatformLink.prefixed_user_id == body.prefixed_user_id).first()
    if existing:
        raise HTTPException(status_code=409, detail="prefixed_user_id already linked")

    canonical_user_id = body.canonical_user_id
    if not canonical_user_id:
        cu = CanonicalUser(display_name=body.display_name or body.platform_user_id)
        db.add(cu)
        db.flush()  # populate cu.id (gen_uuid default)
        canonical_user_id = cu.id

    link = PlatformLink(
        canonical_user_id=canonical_user_id,
        platform=body.platform,
        platform_user_id=body.platform_user_id,
        prefixed_user_id=body.prefixed_user_id,
        display_name=body.display_name,
        linked_via=body.linked_via or "api",
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return _serialize_link(link)


@router.delete("/links/{prefixed_user_id}")
async def delete_link(
    prefixed_user_id: str,
    _: bool = Depends(require_gateway_secret),
    db: DBSession = Depends(get_db),
):
    """Delete a platform link by prefixed_user_id. Idempotent."""
    link = db.query(PlatformLink).filter(PlatformLink.prefixed_user_id == prefixed_user_id).first()
    if not link:
        return {"deleted": False}
    db.delete(link)
    db.commit()
    return {"deleted": True}
