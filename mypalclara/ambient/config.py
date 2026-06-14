"""Ambient reflection configuration (env-driven, matching the heartbeat/ORS precedent)."""

from __future__ import annotations

import os

AMBIENT_ENABLED: bool = os.getenv("AMBIENT_ENABLED", "false").lower() == "true"
AMBIENT_CRON: str = os.getenv("AMBIENT_CRON", "0 11,14,17,20 * * *")
AMBIENT_MIN_DM_GAP_HOURS: float = float(os.getenv("AMBIENT_MIN_DM_GAP_HOURS", "4"))
AMBIENT_JOURNAL_READBACK_DAYS: int = int(os.getenv("AMBIENT_JOURNAL_READBACK_DAYS", "3"))
AMBIENT_RECENT_ACTIVITY_SKIP_MIN: int = int(os.getenv("AMBIENT_RECENT_ACTIVITY_SKIP_MIN", "15"))
AMBIENT_ACTIVE_HOURS: str = os.getenv("AMBIENT_ACTIVE_HOURS", "8-22")  # local-hour window for DMs
AMBIENT_QUEUE_TTL_DAYS: int = int(os.getenv("AMBIENT_QUEUE_TTL_DAYS", "5"))
AMBIENT_JOURNAL_DIR: str = os.getenv(
    "AMBIENT_JOURNAL_DIR", ""
)  # empty → default under the package (resolved in journal.py)
