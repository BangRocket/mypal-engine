"""Intention checking and firing system.

Intentions are future triggers/reminders that Clara stores to surface
information at the right time. This module handles:
- Checking if intentions should fire based on current context
- Managing intention lifecycle (create, fire, expire)
- Different trigger strategies for performance optimization

Trigger Types:
- keyword: Fire when specific keywords appear in message
- topic: Fire when message is semantically similar to a topic
- time: Fire at or after a specific time
- context: Fire based on contextual conditions (channel, time of day, etc.)
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from config.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.orm import Session as OrmSession

logger = get_logger("intentions")


class TriggerType(str, Enum):
    """Types of intention triggers."""

    KEYWORD = "keyword"
    TOPIC = "topic"
    TIME = "time"
    CONTEXT = "context"


class CheckStrategy(str, Enum):
    """Strategies for checking intentions.

    Different strategies trade off between thoroughness and performance:
    - EVERY_MESSAGE: Full check on every message (most thorough, slowest)
    - TIERED: Fast keyword scan always, full semantic check periodically
    - SESSION_START: Only check at session start (fastest, may miss triggers)
    """

    EVERY_MESSAGE = "every_message"
    TIERED = "tiered"
    SESSION_START = "session_start"


def check_intentions(
    user_id: str,
    message: str,
    context: dict[str, Any] | None = None,
    strategy: CheckStrategy = CheckStrategy.TIERED,
    agent_id: str = "clara",
    db: "OrmSession | None" = None,
) -> list[dict[str, Any]]:
    """Check if any intentions should fire for the given context.

    Args:
        user_id: The user to check intentions for
        message: Current user message
        context: Additional context (channel_name, time_of_day, is_dm, etc.)
        strategy: Checking strategy to use
        agent_id: Bot persona identifier
        db: Optional database session (creates one if not provided)

    Returns:
        List of fired intention dicts with keys:
        - id: Intention ID
        - content: What to remind about
        - trigger_type: What triggered it
        - priority: Intention priority
    """
    from db import SessionLocal
    from db.models import Intention

    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        # Load active (unfired, unexpired) intentions for user
        now = datetime.now(UTC).replace(tzinfo=None)
        query = db.query(Intention).filter(
            Intention.user_id == user_id,
            Intention.agent_id == agent_id,
            Intention.fired == False,  # noqa: E712
        )

        # Filter out expired intentions
        intentions = [
            i for i in query.all()
            if i.expires_at is None or i.expires_at > now
        ]

        if not intentions:
            return []

        fired_intentions = []
        context = context or {}

        for intention in intentions:
            trigger_conditions = _parse_trigger_conditions(intention.trigger_conditions)
            trigger_type = trigger_conditions.get("type", "keyword")

            # Apply strategy-based filtering
            if strategy == CheckStrategy.SESSION_START:
                # Only check time triggers at session start
                if trigger_type != TriggerType.TIME:
                    continue

            should_fire = False
            match_details = {}

            if trigger_type == TriggerType.KEYWORD:
                should_fire, match_details = _check_keyword_trigger(
                    message, trigger_conditions
                )
            elif trigger_type == TriggerType.TOPIC:
                # For tiered strategy, skip expensive topic checks unless needed
                if strategy == CheckStrategy.TIERED:
                    # Only do topic check if we passed a quick keyword pre-filter
                    keywords = trigger_conditions.get("quick_keywords", [])
                    if keywords and not any(kw.lower() in message.lower() for kw in keywords):
                        continue
                should_fire, match_details = _check_topic_trigger(
                    message, trigger_conditions
                )
            elif trigger_type == TriggerType.TIME:
                should_fire, match_details = _check_time_trigger(
                    now, trigger_conditions
                )
            elif trigger_type == TriggerType.CONTEXT:
                should_fire, match_details = _check_context_trigger(
                    context, trigger_conditions
                )

            if should_fire:
                fired_intentions.append({
                    "id": intention.id,
                    "content": intention.content,
                    "trigger_type": trigger_type,
                    "priority": intention.priority,
                    "match_details": match_details,
                    "source_memory_id": intention.source_memory_id,
                })

                # Mark as fired
                intention.fired = True
                intention.fired_at = now

                # Delete if fire_once
                if intention.fire_once:
                    db.delete(intention)

        db.commit()

        # Sort by priority (highest first)
        fired_intentions.sort(key=lambda x: x["priority"], reverse=True)

        if fired_intentions:
            logger.info(f"Fired {len(fired_intentions)} intentions for user {user_id}")

        return fired_intentions

    except Exception as e:
        logger.error(f"Error checking intentions: {e}", exc_info=True)
        return []
    finally:
        if close_db:
            db.close()


def _parse_trigger_conditions(conditions: str | dict) -> dict:
    """Parse trigger conditions from JSON string or dict."""
    if isinstance(conditions, str):
        try:
            return json.loads(conditions)
        except json.JSONDecodeError:
            logger.warning(f"Invalid trigger conditions JSON: {conditions}")
            return {"type": "keyword", "keywords": []}
    return conditions


def _check_keyword_trigger(
    message: str,
    conditions: dict,
) -> tuple[bool, dict]:
    """Check if message contains trigger keywords.

    Supports:
    - Simple keyword list
    - Case-insensitive matching
    - Optional regex patterns

    Args:
        message: User message
        conditions: Trigger conditions with "keywords" and optional "regex"

    Returns:
        Tuple of (should_fire, match_details)
    """
    keywords = conditions.get("keywords", [])
    regex_pattern = conditions.get("regex")
    case_sensitive = conditions.get("case_sensitive", False)

    message_check = message if case_sensitive else message.lower()
    matched_keywords = []

    for keyword in keywords:
        keyword_check = keyword if case_sensitive else keyword.lower()
        if keyword_check in message_check:
            matched_keywords.append(keyword)

    if regex_pattern:
        flags = 0 if case_sensitive else re.IGNORECASE
        if re.search(regex_pattern, message, flags):
            matched_keywords.append(f"regex:{regex_pattern}")

    if matched_keywords:
        return True, {"matched_keywords": matched_keywords}
    return False, {}


def _check_topic_trigger(
    message: str,
    conditions: dict,
) -> tuple[bool, dict]:
    """Check if message is semantically similar to trigger topic.

    Uses mem0's search capability to check semantic similarity.

    Args:
        message: User message
        conditions: Trigger conditions with "topic" and "threshold"

    Returns:
        Tuple of (should_fire, match_details)
    """
    topic = conditions.get("topic", "")
    threshold = conditions.get("threshold", 0.7)

    if not topic:
        return False, {}

    try:
        from clara_core.memory import MEM0

        if MEM0 is None:
            return False, {}

        # Use embeddings to check similarity
        # This is a simplified approach - could be optimized with cached embeddings
        from sentence_transformers import util

        # Get embeddings for topic and message
        # Note: This requires the embedding model to be available
        # For now, fall back to simple keyword matching
        topic_words = set(topic.lower().split())
        message_words = set(message.lower().split())
        overlap = len(topic_words & message_words) / max(len(topic_words), 1)

        if overlap >= threshold:
            return True, {"topic": topic, "similarity": overlap}

    except ImportError:
        # Sentence transformers not available, use keyword fallback
        topic_words = set(topic.lower().split())
        message_words = set(message.lower().split())
        overlap = len(topic_words & message_words) / max(len(topic_words), 1)
        if overlap >= threshold:
            return True, {"topic": topic, "similarity": overlap}
    except Exception as e:
        logger.debug(f"Topic trigger check failed: {e}")

    return False, {}


def _check_time_trigger(
    now: datetime,
    conditions: dict,
) -> tuple[bool, dict]:
    """Check if current time matches time trigger.

    Supports:
    - "at": Specific datetime (fires once when reached)
    - "after": Fire after this datetime
    - "recurring": Cron-like patterns (simplified)

    Args:
        now: Current datetime (UTC)
        conditions: Trigger conditions with time specification

    Returns:
        Tuple of (should_fire, match_details)
    """
    trigger_at = conditions.get("at")
    trigger_after = conditions.get("after")

    if trigger_at:
        try:
            target_time = datetime.fromisoformat(trigger_at.replace("Z", "+00:00"))
            target_time = target_time.replace(tzinfo=None)
            if now >= target_time:
                return True, {"trigger_time": trigger_at, "type": "at"}
        except (ValueError, TypeError) as e:
            logger.warning(f"Invalid time trigger format: {trigger_at}, {e}")

    if trigger_after:
        try:
            target_time = datetime.fromisoformat(trigger_after.replace("Z", "+00:00"))
            target_time = target_time.replace(tzinfo=None)
            if now >= target_time:
                return True, {"trigger_time": trigger_after, "type": "after"}
        except (ValueError, TypeError) as e:
            logger.warning(f"Invalid time trigger format: {trigger_after}, {e}")

    return False, {}


def _check_context_trigger(
    context: dict,
    conditions: dict,
) -> tuple[bool, dict]:
    """Check if current context matches trigger conditions.

    Supports matching on:
    - channel_name: Specific channel
    - is_dm: DM vs server channel
    - time_of_day: morning, afternoon, evening, night
    - day_of_week: monday, tuesday, etc.

    Args:
        context: Current context dict
        conditions: Trigger conditions to match

    Returns:
        Tuple of (should_fire, match_details)
    """
    match_conditions = conditions.get("conditions", {})
    if not match_conditions:
        return False, {}

    matched = {}

    # Check channel_name
    if "channel_name" in match_conditions:
        expected = match_conditions["channel_name"].lower()
        actual = context.get("channel_name", "").lower()
        if expected not in actual:
            return False, {}
        matched["channel_name"] = actual

    # Check is_dm
    if "is_dm" in match_conditions:
        expected = match_conditions["is_dm"]
        actual = context.get("is_dm", False)
        if expected != actual:
            return False, {}
        matched["is_dm"] = actual

    # Check time_of_day
    if "time_of_day" in match_conditions:
        expected = match_conditions["time_of_day"].lower()
        now = datetime.now(UTC)
        hour = now.hour

        # Define time periods
        time_periods = {
            "morning": (6, 12),
            "afternoon": (12, 17),
            "evening": (17, 21),
            "night": (21, 6),
        }

        if expected in time_periods:
            start, end = time_periods[expected]
            if expected == "night":
                # Night wraps around midnight
                in_period = hour >= start or hour < end
            else:
                in_period = start <= hour < end

            if not in_period:
                return False, {}
            matched["time_of_day"] = expected

    # Check day_of_week
    if "day_of_week" in match_conditions:
        expected = match_conditions["day_of_week"].lower()
        now = datetime.now(UTC)
        actual = now.strftime("%A").lower()
        if expected != actual:
            return False, {}
        matched["day_of_week"] = actual

    # All conditions matched
    if matched:
        return True, {"matched_conditions": matched}

    return False, {}


def create_intention(
    user_id: str,
    content: str,
    trigger_conditions: dict,
    agent_id: str = "clara",
    priority: int = 0,
    fire_once: bool = True,
    expires_at: datetime | None = None,
    source_memory_id: str | None = None,
    db: "OrmSession | None" = None,
) -> str:
    """Create a new intention.

    Args:
        user_id: User this intention is for
        content: What to remind about
        trigger_conditions: When to fire (see TriggerType for formats)
        agent_id: Bot persona identifier
        priority: Higher = more important
        fire_once: If true, delete after firing
        expires_at: Optional expiration time
        source_memory_id: Optional link to source memory
        db: Optional database session

    Returns:
        The created intention ID
    """
    from db import SessionLocal
    from db.models import Intention

    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        intention = Intention(
            user_id=user_id,
            agent_id=agent_id,
            content=content,
            trigger_conditions=json.dumps(trigger_conditions),
            priority=priority,
            fire_once=fire_once,
            expires_at=expires_at,
            source_memory_id=source_memory_id,
        )
        db.add(intention)
        db.commit()
        db.refresh(intention)

        logger.info(f"Created intention {intention.id} for user {user_id}")
        return intention.id

    finally:
        if close_db:
            db.close()


def list_intentions(
    user_id: str,
    agent_id: str = "clara",
    include_fired: bool = False,
    db: "OrmSession | None" = None,
) -> list[dict]:
    """List all intentions for a user.

    Args:
        user_id: User to list intentions for
        agent_id: Bot persona identifier
        include_fired: Whether to include already-fired intentions
        db: Optional database session

    Returns:
        List of intention dicts
    """
    from db import SessionLocal
    from db.models import Intention

    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        query = db.query(Intention).filter(
            Intention.user_id == user_id,
            Intention.agent_id == agent_id,
        )

        if not include_fired:
            query = query.filter(Intention.fired == False)  # noqa: E712

        intentions = query.order_by(Intention.priority.desc()).all()

        return [
            {
                "id": i.id,
                "content": i.content,
                "trigger_conditions": _parse_trigger_conditions(i.trigger_conditions),
                "priority": i.priority,
                "fire_once": i.fire_once,
                "fired": i.fired,
                "fired_at": i.fired_at.isoformat() if i.fired_at else None,
                "expires_at": i.expires_at.isoformat() if i.expires_at else None,
                "created_at": i.created_at.isoformat() if i.created_at else None,
            }
            for i in intentions
        ]

    finally:
        if close_db:
            db.close()


def delete_intention(
    intention_id: str,
    user_id: str | None = None,
    db: "OrmSession | None" = None,
) -> bool:
    """Delete an intention.

    Args:
        intention_id: ID of intention to delete
        user_id: Optional user ID for verification
        db: Optional database session

    Returns:
        True if deleted, False if not found
    """
    from db import SessionLocal
    from db.models import Intention

    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        query = db.query(Intention).filter(Intention.id == intention_id)
        if user_id:
            query = query.filter(Intention.user_id == user_id)

        intention = query.first()
        if intention:
            db.delete(intention)
            db.commit()
            logger.info(f"Deleted intention {intention_id}")
            return True
        return False

    finally:
        if close_db:
            db.close()


def cleanup_expired_intentions(db: "OrmSession | None" = None) -> int:
    """Delete all expired intentions.

    Args:
        db: Optional database session

    Returns:
        Number of intentions deleted
    """
    from db import SessionLocal
    from db.models import Intention

    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        now = datetime.now(UTC).replace(tzinfo=None)
        result = db.query(Intention).filter(
            Intention.expires_at.isnot(None),
            Intention.expires_at < now,
        ).delete()
        db.commit()

        if result:
            logger.info(f"Cleaned up {result} expired intentions")
        return result

    finally:
        if close_db:
            db.close()


def format_intentions_for_prompt(
    fired_intentions: list[dict],
    max_intentions: int = 3,
) -> str:
    """Format fired intentions for inclusion in the system prompt.

    Args:
        fired_intentions: List of fired intention dicts
        max_intentions: Maximum number to include

    Returns:
        Formatted string for the prompt
    """
    if not fired_intentions:
        return ""

    lines = ["## Reminders"]
    for intention in fired_intentions[:max_intentions]:
        content = intention["content"]
        lines.append(f"- {content}")

    return "\n".join(lines)
