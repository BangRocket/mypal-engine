"""OpenClaw-inspired heartbeat system.

Periodically wakes Clara to check if anything needs attention using
workspace-driven HEARTBEAT.md instructions.

Runs per-user checks so the send callback always knows who to target.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from mypalclara.config.logging import get_logger

logger = get_logger("heartbeat")

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


HEARTBEAT_PROMPT = """You are Clara's heartbeat monitor. You run periodically to check on a specific user.

## Your Instructions
{heartbeat_md}

## Current State
- Current time: {current_time}
- User: {user_id}
- Idle: {idle_minutes} minutes
- Channel: {channel}

## Rules
- If this user doesn't need attention right now, reply exactly: HEARTBEAT_OK
- If they do need attention, reply with a short, natural message to send to them
- Do not infer tasks from prior conversations unless your instructions tell you to
- Keep messages brief and warm
"""


async def run_heartbeat_check(
    llm_callable,
    heartbeat_md: str,
    user: dict,
    current_time: str,
) -> tuple[bool, str]:
    """Run a single heartbeat check for one user.

    Args:
        llm_callable: async function(messages) -> str
        heartbeat_md: Contents of HEARTBEAT.md
        user: Dict with user_id, idle_minutes, channel
        current_time: ISO formatted current time

    Returns:
        (should_send, message) — if should_send is False, message is empty
    """
    prompt = HEARTBEAT_PROMPT.format(
        heartbeat_md=heartbeat_md,
        current_time=current_time,
        user_id=user["user_id"],
        idle_minutes=user["idle_minutes"],
        channel=user["channel"],
    )

    try:
        from mypalclara.core.llm.messages import SystemMessage, UserMessage

        messages = [
            SystemMessage(content=prompt),
            UserMessage(content="Run heartbeat check now."),
        ]
        response = await llm_callable(messages)
        response_text = str(response).strip()
    except Exception as e:
        logger.error(f"Heartbeat LLM call failed for {user['user_id']}: {e}")
        return False, ""

    ack_max = int(os.getenv("HEARTBEAT_ACK_MAX_CHARS", str(DEFAULT_ACK_MAX_CHARS)))

    if is_ack(response_text, max_chars=ack_max):
        logger.debug(f"Heartbeat: HEARTBEAT_OK for {user['user_id']}")
        return False, ""

    logger.info(f"Heartbeat wants to send to {user['user_id']}: {response_text[:100]}")
    return True, response_text


def _load_heartbeat_md() -> str:
    """Load HEARTBEAT.md from the workspace directory.

    Returns the file contents, or a default instruction if not found.
    """
    workspace_dir = Path(__file__).parent.parent / "workspace"
    heartbeat_path = workspace_dir / "HEARTBEAT.md"

    if heartbeat_path.exists():
        return heartbeat_path.read_text(encoding="utf-8").strip()

    return "No HEARTBEAT.md found. Reply HEARTBEAT_OK."


async def heartbeat_loop(
    llm_callable: Callable,
    send_fn: Callable,
    interval_minutes: float = DEFAULT_INTERVAL_MINUTES,
) -> None:
    """Run the heartbeat loop forever.

    Args:
        llm_callable: async function(messages) -> str
        send_fn: async function(user_id, channel_id, message_text) -> None
        interval_minutes: Minutes between heartbeat checks
    """
    interval = float(os.getenv("HEARTBEAT_INTERVAL_MINUTES", str(interval_minutes)))
    logger.info(f"Heartbeat loop started (interval: {interval}m)")

    while True:
        try:
            heartbeat_md = _load_heartbeat_md()
            context = gather_heartbeat_context()

            for user in context.get("active_users", []):
                should_send, message = await run_heartbeat_check(
                    llm_callable, heartbeat_md, user, context["current_time"]
                )

                if should_send and message:
                    await send_fn(user["user_id"], user["channel"], message)
                    logger.info(f"Heartbeat message delivered to {user['user_id']}")

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Heartbeat cycle error: {e}")

        await asyncio.sleep(interval * 60)
