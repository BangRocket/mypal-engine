"""Chat session history endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session as DBSession

from mypalclara.db.models import CanonicalUser, Message
from mypalclara.db.models import Session as ChatSession
from mypalclara.db.user_identity import resolve_all_user_ids_for_canonical
from mypalclara.gateway.api.auth import get_approved_user, get_db

router = APIRouter()


def _get_user_ids(user: CanonicalUser, db: DBSession) -> list[str]:
    return resolve_all_user_ids_for_canonical(user.id, db)


def _get_user_session(session_id: str, user_ids: list[str], db: DBSession) -> ChatSession:
    """Get a session owned by the user or raise 404."""
    session = db.query(ChatSession).filter(ChatSession.id == session_id, ChatSession.user_id.in_(user_ids)).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.get("")
async def list_sessions(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    archived: bool = Query(False),
    user: CanonicalUser = Depends(get_approved_user),
    db: DBSession = Depends(get_db),
):
    """List chat sessions for the current user."""
    user_ids = _get_user_ids(user, db)
    if not user_ids:
        return {"sessions": [], "total": 0}

    archived_val = "true" if archived else "false"
    query = (
        db.query(ChatSession)
        .filter(ChatSession.user_id.in_(user_ids), ChatSession.archived == archived_val)
        .order_by(ChatSession.last_activity_at.desc())
    )
    total = query.count()
    sessions = query.offset(offset).limit(limit).all()

    return {
        "sessions": [
            {
                "id": s.id,
                "title": s.title,
                "user_id": s.user_id,
                "context_id": s.context_id,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "last_activity_at": s.last_activity_at.isoformat() if s.last_activity_at else None,
                "session_summary": s.session_summary,
                "archived": s.archived == "true",
            }
            for s in sessions
        ],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@router.get("/{session_id}")
async def get_session(
    session_id: str,
    user: CanonicalUser = Depends(get_approved_user),
    db: DBSession = Depends(get_db),
):
    """Get a session with its messages."""
    user_ids = _get_user_ids(user, db)
    session = _get_user_session(session_id, user_ids, db)

    messages = db.query(Message).filter(Message.session_id == session_id).order_by(Message.created_at.asc()).all()

    return {
        "id": session.id,
        "title": session.title,
        "user_id": session.user_id,
        "context_id": session.context_id,
        "started_at": session.started_at.isoformat() if session.started_at else None,
        "last_activity_at": session.last_activity_at.isoformat() if session.last_activity_at else None,
        "session_summary": session.session_summary,
        "context_snapshot": session.context_snapshot,
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in messages
        ],
    }


@router.put("/{session_id}")
async def update_session(
    session_id: str,
    title: str = Body(..., embed=True),
    user: CanonicalUser = Depends(get_approved_user),
    db: DBSession = Depends(get_db),
):
    """Rename a session."""
    user_ids = _get_user_ids(user, db)
    session = _get_user_session(session_id, user_ids, db)
    session.title = title
    db.commit()
    return {"ok": True, "id": session_id, "title": title}


@router.patch("/{session_id}/archive")
async def archive_session(
    session_id: str,
    user: CanonicalUser = Depends(get_approved_user),
    db: DBSession = Depends(get_db),
):
    """Archive a session."""
    user_ids = _get_user_ids(user, db)
    session = _get_user_session(session_id, user_ids, db)
    session.archived = "true"
    db.commit()
    return {"ok": True, "id": session_id}


@router.patch("/{session_id}/unarchive")
async def unarchive_session(
    session_id: str,
    user: CanonicalUser = Depends(get_approved_user),
    db: DBSession = Depends(get_db),
):
    """Unarchive a session."""
    user_ids = _get_user_ids(user, db)
    session = _get_user_session(session_id, user_ids, db)
    session.archived = "false"
    db.commit()
    return {"ok": True, "id": session_id}


@router.delete("/{session_id}")
async def delete_session(
    session_id: str,
    user: CanonicalUser = Depends(get_approved_user),
    db: DBSession = Depends(get_db),
):
    """Delete a session and its messages."""
    user_ids = _get_user_ids(user, db)
    session = _get_user_session(session_id, user_ids, db)
    db.query(Message).filter(Message.session_id == session_id).delete()
    db.delete(session)
    db.commit()
    return {"ok": True, "id": session_id}
