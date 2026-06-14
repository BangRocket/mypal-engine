"""Register a single cron 'ambient_tick' that fans out over opted-in users."""

from __future__ import annotations

from typing import Callable

from mypalclara.ambient.config import AMBIENT_CRON
from mypalclara.config.logging import get_logger
from mypalclara.gateway.scheduler import ScheduledTask, TaskType

logger = get_logger("ambient.scheduling")


def get_opted_in_users(*, session_factory=None) -> list[str]:
    if session_factory is None:
        from mypalclara.db.connection import SessionLocal

        session_factory = SessionLocal
    from mypalclara.db.models import AmbientUserConfig

    db = session_factory()
    try:
        rows = db.query(AmbientUserConfig).filter(AmbientUserConfig.reflection_opt_in == "true").all()
        return [r.user_id for r in rows]
    finally:
        db.close()


def register_ambient_task(scheduler, *, runner: Callable, cron: str | None = None) -> None:
    task = ScheduledTask(
        name="ambient_tick",
        type=TaskType.CRON,
        handler=runner,
        cron=cron or AMBIENT_CRON,
        description="Unified ambient reflection — reflect + surface for opted-in users",
    )
    scheduler.add_task(task)
    logger.info(f"Registered ambient_tick (cron={task.cron})")
