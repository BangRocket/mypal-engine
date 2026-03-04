"""OpenClaw-inspired heartbeat system.

Periodically wakes Clara to check if anything needs attention using
workspace-driven HEARTBEAT.md instructions.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

ACK_TOKEN = "HEARTBEAT_OK"
DEFAULT_INTERVAL_MINUTES = 30
DEFAULT_ACK_MAX_CHARS = 300


def is_ack(response: str, max_chars: int = DEFAULT_ACK_MAX_CHARS) -> bool:
    """Check if a heartbeat response is an acknowledgement (nothing to report).

    Returns True if the response is effectively HEARTBEAT_OK with minimal
    surrounding text (under max_chars after stripping the token).
    """
    text = response.strip()
    if not text:
        return False

    if text == ACK_TOKEN:
        return True

    # Strip HEARTBEAT_OK from start or end, check remaining length
    remaining = text
    if remaining.startswith(ACK_TOKEN):
        remaining = remaining[len(ACK_TOKEN) :].strip()
    elif remaining.endswith(ACK_TOKEN):
        remaining = remaining[: -len(ACK_TOKEN)].strip()
    else:
        return False

    return len(remaining) <= max_chars


def get_session():
    """Get a database session (thin wrapper for testability)."""
    from mypalclara.db.connection import get_session as _get_session

    return _get_session()


def gather_heartbeat_context() -> dict:
    """Gather lightweight context for the heartbeat LLM call.

    Returns dict with:
        current_time: ISO formatted current time
        active_users: list of dicts with user_id, last_active, channel, idle_minutes
    """
    from mypalclara.db.models import Session

    now = datetime.now()
    cutoff = now - timedelta(hours=24)

    active_users = []
    with get_session() as db:
        recent_sessions = (
            db.query(Session)
            .filter(Session.last_activity_at >= cutoff)
            .order_by(Session.last_activity_at.desc())
            .limit(20)
            .all()
        )

        for s in recent_sessions:
            idle_minutes = int((now - s.last_activity_at).total_seconds() / 60)
            active_users.append(
                {
                    "user_id": s.user_id,
                    "channel": s.context_id,
                    "idle_minutes": idle_minutes,
                    "last_active": s.last_activity_at.isoformat(),
                }
            )

    return {
        "current_time": now.isoformat(),
        "active_users": active_users,
    }
