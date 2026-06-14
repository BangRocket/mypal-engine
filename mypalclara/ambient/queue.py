"""SurfacedThought queue helpers (DI session factory for testability)."""

from __future__ import annotations

from datetime import datetime

from mypalclara.db.models import SurfacedThought, utcnow


def _default_factory():
    from mypalclara.db.connection import SessionLocal

    return SessionLocal


def enqueue(
    user_id: str, content: str, *, kind: str = "queue", expires_at: datetime | None = None, session_factory=None
) -> str:
    factory = session_factory or _default_factory()
    db = factory()
    try:
        row = SurfacedThought(user_id=user_id, content=content, kind=kind, expires_at=expires_at)
        db.add(row)
        db.commit()
        return row.id
    finally:
        db.close()


def fetch_undelivered(user_id: str, *, now: datetime | None = None, session_factory=None):
    now = now or utcnow()
    factory = session_factory or _default_factory()
    db = factory()
    try:
        rows = (
            db.query(SurfacedThought)
            .filter(SurfacedThought.user_id == user_id, SurfacedThought.delivered == "false")
            .order_by(SurfacedThought.created_at.asc())
            .all()
        )
        return [r for r in rows if r.expires_at is None or r.expires_at > now]
    finally:
        db.close()


def mark_delivered(ids: list[str], *, now: datetime | None = None, session_factory=None) -> None:
    if not ids:
        return
    now = now or utcnow()
    factory = session_factory or _default_factory()
    db = factory()
    try:
        db.query(SurfacedThought).filter(SurfacedThought.id.in_(ids)).update(
            {SurfacedThought.delivered: "true", SurfacedThought.surfaced_at: now},
            synchronize_session=False,
        )
        db.commit()
    finally:
        db.close()
