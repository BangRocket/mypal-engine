"""Simple cron scheduler for serve mode."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Callable

logger = logging.getLogger(__name__)


def parse_cron_schedule(schedule: str) -> tuple[int | None, int | None]:
    """Parse a simple cron schedule (minute hour * * *).

    Returns (minute, hour) where None means "any".
    Only supports integers and '*' for the minute and hour fields.
    """
    parts = schedule.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Expected 5 cron fields, got {len(parts)}: {schedule!r}")

    minute = None if parts[0] == "*" else int(parts[0])
    hour = None if parts[1] == "*" else int(parts[1])

    if minute is not None and not (0 <= minute <= 59):
        raise ValueError(f"Minute must be 0-59, got {minute}")
    if hour is not None and not (0 <= hour <= 23):
        raise ValueError(f"Hour must be 0-23, got {hour}")

    return minute, hour


def next_run_time(minute: int | None, hour: int | None) -> datetime:
    """Calculate the next matching datetime from now."""
    now = datetime.now()
    candidate = now.replace(second=0, microsecond=0)

    # If minute is fixed, set it
    if minute is not None:
        candidate = candidate.replace(minute=minute)

    # If hour is fixed, set it
    if hour is not None:
        candidate = candidate.replace(hour=hour)

    # If the candidate is in the past (or right now), advance
    if candidate <= now:
        if minute is not None and hour is not None:
            # Both fixed: next occurrence is tomorrow
            candidate += timedelta(days=1)
        elif minute is not None:
            # Minute fixed, hour wildcard: next hour
            candidate += timedelta(hours=1)
        elif hour is not None:
            # Hour fixed, minute wildcard: next minute
            candidate += timedelta(minutes=1)
        else:
            # Both wildcard: next minute
            candidate += timedelta(minutes=1)

    return candidate


def run_scheduler(schedule: str, callback: Callable[[], None]) -> None:
    """Run a blocking scheduler loop.

    Parses the cron schedule, sleeps until the next matching time,
    runs the callback, then recalculates.
    """
    cron_minute, cron_hour = parse_cron_schedule(schedule)

    while True:
        target = next_run_time(cron_minute, cron_hour)
        wait_seconds = (target - datetime.now()).total_seconds()

        if wait_seconds > 0:
            logger.info(f"Next backup scheduled at {target.strftime('%Y-%m-%d %H:%M')}")
            time.sleep(wait_seconds)

        logger.info("Cron trigger: starting scheduled backup")
        try:
            callback()
        except Exception:
            logger.exception("Scheduled backup failed")

        # Sleep past the current minute to avoid re-triggering
        time.sleep(61)
