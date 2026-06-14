"""ambient_turn(): one user's reflect → gate → deliver/queue, with guards.

Dependencies are injected (reflect/gate/enqueue/send_fn/user-ctx) so the
orchestration is unit-testable without a DB or LLM. The startup wiring binds
the real implementations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from mypalclara.ambient.config import (
    AMBIENT_ACTIVE_HOURS,
    AMBIENT_MIN_DM_GAP_HOURS,
    AMBIENT_QUEUE_TTL_DAYS,
    AMBIENT_RECENT_ACTIVITY_SKIP_MIN,
)
from mypalclara.ambient.guards import in_active_hours, past_min_gap, recently_active
from mypalclara.config.logging import get_logger

logger = get_logger("ambient.loop")


@dataclass
class UserCtx:
    timezone: str | None = None
    last_dm_at: datetime | None = None


def _default_user_ctx(user_id: str, *, session_factory=None) -> UserCtx:
    if session_factory is None:
        from mypalclara.db.connection import SessionLocal

        session_factory = SessionLocal
    from mypalclara.db.models import AmbientUserConfig

    db = session_factory()
    try:
        row = db.query(AmbientUserConfig).filter_by(user_id=user_id).first()
        if row is None:
            return UserCtx()
        return UserCtx(timezone=row.timezone, last_dm_at=row.last_dm_at)
    finally:
        db.close()


def _default_record_dm(user_id: str, now_naive: datetime, *, session_factory=None) -> None:
    if session_factory is None:
        from mypalclara.db.connection import SessionLocal

        session_factory = SessionLocal
    from mypalclara.db.models import AmbientUserConfig

    db = session_factory()
    try:
        row = db.query(AmbientUserConfig).filter_by(user_id=user_id).first()
        if row is None:
            row = AmbientUserConfig(user_id=user_id, reflection_opt_in="true")
            db.add(row)
        row.last_dm_at = now_naive
        db.commit()
    finally:
        db.close()


async def ambient_turn(
    user_id: str,
    *,
    orchestrator: Any,
    tool_executor: Any,
    gate_llm: Any,
    send_fn: Callable,
    now_utc: datetime | None = None,
    # injectable seams (default to real implementations):
    _reflect: Callable | None = None,
    _gate: Callable | None = None,
    _enqueue: Callable | None = None,
    _user_ctx: Callable | None = None,
    _record_dm: Callable | None = None,
) -> None:
    now_utc = now_utc or datetime.now(timezone.utc)
    now_naive = now_utc.astimezone(timezone.utc).replace(tzinfo=None)

    if recently_active(user_id, AMBIENT_RECENT_ACTIVITY_SKIP_MIN):
        logger.info(f"ambient_turn: {user_id} recently active — skipping")
        return

    reflect_fn = _reflect or (lambda uid, **kw: _import_reflect()(uid, **kw))
    gate_fn = _gate or (lambda uid, refl, **kw: _import_gate()(uid, refl, **kw))
    enqueue_fn = _enqueue or _import_enqueue()
    user_ctx_fn = _user_ctx or _default_user_ctx
    record_dm_fn = _record_dm or _default_record_dm

    reflection = await reflect_fn(user_id, orchestrator=orchestrator, tool_executor=tool_executor)
    if not (reflection or "").strip():
        return

    decision = await gate_fn(user_id, reflection, gate_llm=gate_llm)
    d = decision.get("decision", "nothing")
    content = decision.get("content", "")
    if d == "nothing" or not content:
        return

    expires = now_naive + timedelta(days=AMBIENT_QUEUE_TTL_DAYS)

    if d == "queue":
        enqueue_fn(user_id, content, kind="queue", expires_at=expires)
        return

    if d == "urgent":
        ctx = user_ctx_fn(user_id)
        ok_hours = in_active_hours(ctx.timezone, now_utc, AMBIENT_ACTIVE_HOURS)
        ok_gap = past_min_gap(ctx.last_dm_at, now_naive, AMBIENT_MIN_DM_GAP_HOURS)
        if ok_hours and ok_gap:
            delivered = await send_fn(user_id, f"dm-{user_id}", content)
            if delivered:
                record_dm_fn(user_id, now_naive)
        else:
            logger.info(f"ambient_turn: urgent for {user_id} downgraded to queue "
                        f"(hours={ok_hours}, gap={ok_gap})")
            enqueue_fn(user_id, content, kind="queue", expires_at=expires)


def _import_reflect():
    from mypalclara.ambient.reflect import reflect

    return reflect


def _import_gate():
    from mypalclara.ambient.surface_gate import surface_gate

    return surface_gate


def _import_enqueue():
    from mypalclara.ambient.queue import enqueue

    return enqueue
