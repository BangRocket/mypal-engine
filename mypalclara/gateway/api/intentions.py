"""Intentions CRUD endpoints."""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DBSession

from mypalclara.db.models import CanonicalUser, Intention, utcnow
from mypalclara.db.user_identity import resolve_all_user_ids_for_canonical
from mypalclara.gateway.api.auth import get_approved_user, get_db

router = APIRouter()


class IntentionCreate(BaseModel):
    content: str = Field(..., min_length=1)
    trigger_conditions: dict = Field(...)
    priority: int = 0
    fire_once: bool = True
    expires_at: str | None = None


class IntentionUpdate(BaseModel):
    content: str | None = None
    trigger_conditions: dict | None = None
    priority: int | None = None
    fire_once: bool | None = None
    expires_at: str | None = None


def _get_user_ids(user: CanonicalUser, db: DBSession) -> list[str]:
    return resolve_all_user_ids_for_canonical(user.id, db)


@router.get("")
async def list_intentions(
    fired: bool | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    user: CanonicalUser = Depends(get_approved_user),
    db: DBSession = Depends(get_db),
):
    """List intentions for the current user."""
    user_ids = _get_user_ids(user, db)
    if not user_ids:
        return {"intentions": [], "total": 0}

    query = db.query(Intention).filter(Intention.user_id.in_(user_ids))
    if fired is not None:
        query = query.filter(Intention.fired == fired)

    total = query.count()
    intentions = query.order_by(Intention.created_at.desc()).offset(offset).limit(limit).all()

    return {
        "intentions": [
            {
                "id": i.id,
                "content": i.content,
                "trigger_conditions": json.loads(i.trigger_conditions) if i.trigger_conditions else {},
                "priority": i.priority,
                "fire_once": i.fire_once,
                "fired": i.fired,
                "fired_at": i.fired_at.isoformat() if i.fired_at else None,
                "created_at": i.created_at.isoformat() if i.created_at else None,
                "expires_at": i.expires_at.isoformat() if i.expires_at else None,
            }
            for i in intentions
        ],
        "total": total,
    }


@router.post("")
async def create_intention(
    body: IntentionCreate,
    user: CanonicalUser = Depends(get_approved_user),
    db: DBSession = Depends(get_db),
):
    """Create a new intention."""
    user_ids = _get_user_ids(user, db)
    if not user_ids:
        raise HTTPException(status_code=400, detail="No platform linked")

    intention = Intention(
        id=str(uuid.uuid4()),
        user_id=user_ids[0],
        content=body.content,
        trigger_conditions=json.dumps(body.trigger_conditions),
        priority=body.priority,
        fire_once=body.fire_once,
    )
    db.add(intention)
    db.commit()

    return {"id": intention.id, "ok": True}


@router.put("/{intention_id}")
async def update_intention(
    intention_id: str,
    body: IntentionUpdate,
    user: CanonicalUser = Depends(get_approved_user),
    db: DBSession = Depends(get_db),
):
    """Update an intention."""
    user_ids = _get_user_ids(user, db)
    intention = db.query(Intention).filter(Intention.id == intention_id, Intention.user_id.in_(user_ids)).first()
    if not intention:
        raise HTTPException(status_code=404, detail="Intention not found")

    if body.content is not None:
        intention.content = body.content
    if body.trigger_conditions is not None:
        intention.trigger_conditions = json.dumps(body.trigger_conditions)
    if body.priority is not None:
        intention.priority = body.priority
    if body.fire_once is not None:
        intention.fire_once = body.fire_once
    db.commit()

    return {"ok": True}


@router.delete("/{intention_id}")
async def delete_intention(
    intention_id: str,
    user: CanonicalUser = Depends(get_approved_user),
    db: DBSession = Depends(get_db),
):
    """Delete an intention."""
    user_ids = _get_user_ids(user, db)
    intention = db.query(Intention).filter(Intention.id == intention_id, Intention.user_id.in_(user_ids)).first()
    if not intention:
        raise HTTPException(status_code=404, detail="Intention not found")

    db.delete(intention)
    db.commit()
    return {"ok": True}
