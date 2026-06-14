"""Anti-noise guards for the ambient loop."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def in_active_hours(tz_name: str | None, now_utc: datetime, window: str = "8-22") -> bool:
    lo, hi = (int(x) for x in window.split("-"))
    local = now_utc
    if tz_name:
        try:
            local = now_utc.astimezone(ZoneInfo(tz_name))
        except Exception:
            local = now_utc
    return lo <= local.hour < hi


def past_min_gap(last_dm_at: datetime | None, now_naive: datetime, gap_hours: float) -> bool:
    if last_dm_at is None:
        return True
    return (now_naive - last_dm_at).total_seconds() >= gap_hours * 3600


def recently_active(user_id: str, skip_minutes: int, *, now: datetime | None = None,
                    session_factory=None) -> bool:
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    if session_factory is None:
        from mypalclara.db.connection import SessionLocal

        session_factory = SessionLocal
    from mypalclara.db.models import Session as DbSession

    cutoff = now - timedelta(minutes=skip_minutes)
    db = session_factory()
    try:
        row = (
            db.query(DbSession)
            .filter(DbSession.user_id == user_id, DbSession.last_activity_at >= cutoff)
            .first()
        )
        return row is not None
    finally:
        db.close()
