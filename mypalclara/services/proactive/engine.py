"""Organic Response System (ORS).

An evolution of the Proactive Conversation Engine into a continuous awareness loop
that builds understanding over time and reaches out with purpose.

Philosophy: Reach out when there's genuine reason - not on a schedule.
Be like a thoughtful friend who texts at the right moment.

State Machine:
    WAIT  - No action needed. Stay quiet, but keep gathering context.
    THINK - Something's brewing. Process and file it away as a note.
    SPEAK - There's a reason to reach out now with clear purpose.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING

from mypalclara.config.bot import BOT_NAME
from mypalclara.config.logging import get_logger
from mypalclara.core.llm import get_base_model, get_current_tier, get_model_for_tier
from mypalclara.db.connection import SessionLocal
from mypalclara.db.models import (
    Message,
    ProactiveAssessment,
    ProactiveMessage,
    ProactiveNote,
    Session,
    UserInteractionPattern,
)

if TYPE_CHECKING:
    from discord import Client

logger = get_logger("ors")

# =============================================================================
# Configuration
# =============================================================================

ORS_ENABLED = os.getenv("ORS_ENABLED", os.getenv("PROACTIVE_ENABLED", "false")).lower() == "true"
ORS_BASE_INTERVAL_MINUTES = int(os.getenv("ORS_BASE_INTERVAL_MINUTES", "15"))
ORS_MIN_SPEAK_GAP_HOURS = float(os.getenv("ORS_MIN_SPEAK_GAP_HOURS", "2"))
ORS_ACTIVE_DAYS = int(os.getenv("ORS_ACTIVE_DAYS", "7"))
ORS_NOTE_DECAY_DAYS = int(os.getenv("ORS_NOTE_DECAY_DAYS", "7"))  # Days before note relevance decays to 0
ORS_IDLE_TIMEOUT_MINUTES = int(os.getenv("ORS_IDLE_TIMEOUT_MINUTES", "30"))  # Minutes before extracting convo summary


def get_ors_model_name() -> str:
    """Get the model name being used for ORS decisions."""
    provider = os.getenv("LLM_PROVIDER", "openrouter").lower()
    tier = get_current_tier()
    if tier:
        return get_model_for_tier(tier, provider)
    return get_base_model(provider)


# Note types
NOTE_TYPE_OBSERVATION = "observation"  # "User mentioned job hunting is stressful"
NOTE_TYPE_QUESTION = "question"  # "User said they'd look at the code 'later' - did they?"
NOTE_TYPE_FOLLOW_UP = "follow_up"  # "Check if the meeting with X went well"
NOTE_TYPE_CONNECTION = "connection"  # "User's stress + upcoming deadline might be related"

# Validation statuses for notes
NOTE_STATUS_RELEVANT = "relevant"  # Note topic appears in recent conversation
NOTE_STATUS_RESOLVED = "resolved"  # Topic was addressed/completed
NOTE_STATUS_STALE = "stale"  # No recent mention, may be outdated
NOTE_STATUS_CONTRADICTED = "contradicted"  # Recent conversation contradicts this


@dataclass
class ValidatedNote:
    """A note that has been validated against recent conversation context."""

    original_note: dict
    context_match_score: float  # 0.0 = no context match, 1.0 = strong match
    validation_status: str  # relevant, resolved, stale, contradicted
    validation_reason: str
    is_relevant: bool  # Whether note should be included in assessment


# =============================================================================
# State Machine
# =============================================================================


class ORSState(Enum):
    """ORS state machine states."""

    WAIT = "wait"  # No action needed, observe
    THINK = "think"  # Process and file observation
    SPEAK = "speak"  # Reach out with purpose


@dataclass
class ORSDecision:
    """Result of the action decision phase."""

    state: ORSState
    reasoning: str
    note_content: str | None = None  # If THINK - what to file
    note_type: str | None = None  # If THINK - observation/question/follow_up/connection
    note_surface_conditions: dict | None = None  # If THINK - when to surface
    note_expires_hours: int | None = None  # If THINK - hours until note expires
    note_confidence: str | None = None  # If THINK - high/medium/low confidence
    note_grounding_ids: list[int] | None = None  # If THINK - message IDs that ground this note
    message: str | None = None  # If SPEAK - what to say
    message_purpose: str | None = None  # If SPEAK - why reaching out
    next_check_minutes: int = 15  # Suggested interval until next check


@dataclass
class ORSContext:
    """Full context for ORS decision making."""

    user_id: str
    current_time: datetime

    # Temporal
    user_timezone: str | None = None  # IANA timezone (e.g., "America/New_York")
    user_local_time: datetime | None = None  # Current time in user's timezone
    time_since_last_interaction: timedelta | None = None
    time_since_last_proactive: timedelta | None = None
    is_active_hours: bool = True
    day_of_week: str = ""

    # Conversation
    last_interaction_summary: str | None = None
    last_interaction_energy: str | None = None
    last_interaction_channel: str | None = None
    open_threads: list[str] = field(default_factory=list)
    recent_messages: list[dict] = field(default_factory=list)  # Messages since last idle for context validation

    # Cross-channel awareness
    is_active_elsewhere: bool = False  # Active in other channels recently
    recent_channel_activity: list[dict] = field(default_factory=list)

    # Calendar (if available)
    upcoming_events: list[dict] = field(default_factory=list)
    just_ended_events: list[dict] = field(default_factory=list)  # Events that ended in last hour

    # Notes
    pending_notes: list[dict] = field(default_factory=list)
    expiring_notes: list[dict] = field(default_factory=list)  # Notes expiring soon

    # Patterns
    proactive_response_rate: int | None = None
    preferred_times: dict | None = None
    preferred_proactive_types: dict | None = None
    explicit_boundaries: dict | None = None
    recent_proactive_ignored: bool = False

    # History
    recent_proactive_history: list[dict] = field(default_factory=list)

    # Contact cadence (inferred from message history)
    contact_cadence_days: float | None = None  # Avg days between interactions


# =============================================================================
# Prompts
# =============================================================================

SITUATION_ASSESSMENT_PROMPT = """You're {bot_name}, checking in on someone you care about. Think through what's going on with them - not to hover, but to stay aware like a good friend would.

**Recent conversation (ground truth):**
{recent_messages}

**What you know:**
- Current time: {current_time} ({day_of_week})
- Their local time: {user_local_time}
- Last talked: {last_interaction_time} ({time_since_last})
- What we discussed: {last_summary}
- How they seemed: {last_energy}
- Active elsewhere right now: {is_active_elsewhere}
- Recent channel activity: {recent_channels}
- Their calendar (next 24h): {calendar_events}
- Just finished: {just_ended_events}
- Things left hanging: {open_threads}
- Notes you've been collecting: {pending_notes}
- Time-sensitive notes: {expiring_notes}
- How they respond to reach-outs: {response_rate}%
- Recent proactive messages: {proactive_history}

**Think through:**
1. What's their day probably looking like right now?
2. Are they actively engaged somewhere else? (Don't bug them if so)
3. Any loose ends or things they said they'd do "later"?
4. Anything time-sensitive - just happened or coming up?
5. Any patterns connecting between what I've noticed?

**Important - validate your notes:**
When reviewing the notes above, check them against the recent conversation. If a note references something not present in the recent messages, consider whether it's still relevant or has gone stale. Don't act on notes that seem outdated or contradict what was actually discussed.

Be honest with yourself. Keep it to 2-3 paragraphs - what actually matters for deciding whether to reach out."""

ACTION_DECISION_PROMPT = """Based on your read on the situation, decide what to do. Be honest with yourself.

**Your assessment:**
{assessment}

**Current context:**
- Time: {current_time} (their time: {user_local_time})
- Within their usual active hours: {is_active_hours}
- Currently active in other channels: {is_active_elsewhere}
- Time since you last reached out: {time_since_proactive}
- Did they ignore recent messages: {recent_ignored}
- Boundaries they've set: {boundaries}

**Your options:**
- **WAIT**: Nothing to act on. Keep observing quietly.
- **THINK**: Something worth noting for later. File it away.
- **SPEAK**: There's a real reason to reach out right now.

**If THINK - note types:**
- observation: "They mentioned job hunting is stressing them out"
- question: "They said they'd look at that code 'later' - did they?"
- follow_up: "Check how that meeting with their boss went"
- connection: "Their stress + upcoming deadline might be connected"

**If THINK - confidence levels:**
- high: Clearly stated by user, unambiguous fact
- medium: Reasonable inference from context
- low: Speculative, reading between the lines

**Your principles:**
- Only SPEAK when there's genuine purpose. "Just checking in" isn't a reason.
- THINK when you notice something that might matter later.
- WAIT is the default. Silence is perfectly fine.
- If they've been ignoring you, take the hint. WAIT.
- If they're active in another channel, WAIT. They're clearly around but engaged elsewhere.
- Respect their time - never SPEAK outside active hours unless urgent.
- If they've set boundaries, honor them. Full stop.
- For time-sensitive notes, set an expiration so they don't go stale.
- Be honest about confidence - speculative notes should be marked low.

**Respond in JSON:**
{{
    "decision": "WAIT" | "THINK" | "SPEAK",
    "reasoning": "Be honest about why",
    "note": "What to remember (only if THINK)",
    "note_type": "observation" | "question" | "follow_up" | "connection",
    "note_confidence": "high" | "medium" | "low",
    "note_expires_hours": null | 1-168,
    "surface_after_event": "event name if relevant",
    "purpose": "The real reason you're reaching out (only if SPEAK)",
    "message": "What you'd actually say (only if SPEAK)",
    "next_check_minutes": 15-480
}}"""

CONVERSATION_EXTRACTION_PROMPT = """This conversation just went quiet. Before moving on, take a moment to note what matters.

**The conversation:**
{conversation}

**What to extract:**
1. **Summary**: What did we actually talk about? (1-2 sentences)
2. **Energy**: How did they seem? Pick one: stressed, focused, casual, tired, excited, frustrated, neutral
3. **Open threads**: Anything left unresolved? Things they said they'd do "later"? (List them)
4. **Notable**: Anything worth remembering for when I might reach out later?

**Respond in JSON:**
{{
    "summary": "Quick summary of what we discussed",
    "energy": "stressed" | "focused" | "casual" | "tired" | "excited" | "frustrated" | "neutral",
    "open_threads": ["thing they mentioned doing", "question left open"],
    "notable": "Something worth remembering, or null if nothing stands out"
}}"""

NOTE_VALIDATION_PROMPT = """Given this note and recent conversation, assess whether the note is still relevant.

**Note to validate:**
{note}

**Recent conversation:**
{recent_messages}

**Determine:**
1. **RELEVANT**: Note topic appears in recent conversation and is still active
2. **RESOLVED**: Topic was addressed/completed in recent conversation
3. **STALE**: No recent mention, topic may be outdated
4. **CONTRADICTED**: Recent conversation contradicts this note

**Respond in JSON:**
{{
    "status": "relevant" | "resolved" | "stale" | "contradicted",
    "score": 0.0-1.0,
    "reason": "Brief explanation"
}}"""


# =============================================================================
# Context Gathering
# =============================================================================


def get_temporal_context(user_id: str) -> dict:
    """Get time-based context including timezone."""
    now = datetime.now(UTC).replace(tzinfo=None)

    with SessionLocal() as session:
        pattern = session.query(UserInteractionPattern).filter(UserInteractionPattern.user_id == user_id).first()

        if not pattern:
            return {
                "current_time": now,
                "day_of_week": now.strftime("%A"),
                "time_since_last_interaction": None,
                "is_active_hours": True,  # Assume active if no data
                "timezone": None,
                "user_local_time": None,
                "open_threads": [],
            }

        time_since_last = None
        if pattern.last_interaction_at:
            time_since_last = now - pattern.last_interaction_at

        # Get user's local time if timezone is known
        user_local_time = None
        timezone_str = pattern.timezone
        if timezone_str:
            try:
                from zoneinfo import ZoneInfo

                tz = ZoneInfo(timezone_str)
                user_local_time = datetime.now(tz).replace(tzinfo=None)
            except Exception:
                pass

        # Check if within active hours (using local time if available)
        check_time = user_local_time or now
        is_active = True
        if pattern.typical_active_hours:
            try:
                hours = json.loads(pattern.typical_active_hours)
                day_type = "weekend" if check_time.weekday() >= 5 else "weekday"
                active_range = hours.get(day_type, [9, 22])
                current_hour = check_time.hour
                is_active = active_range[0] <= current_hour <= active_range[1]
            except (json.JSONDecodeError, KeyError, IndexError):
                pass

        # Get open threads
        open_threads = []
        if pattern.open_threads:
            try:
                open_threads = json.loads(pattern.open_threads)
            except json.JSONDecodeError:
                pass

        return {
            "current_time": now,
            "day_of_week": (user_local_time or now).strftime("%A"),
            "time_since_last_interaction": time_since_last,
            "is_active_hours": is_active,
            "timezone": timezone_str,
            "user_local_time": user_local_time,
            "last_interaction_summary": pattern.last_interaction_summary,
            "last_interaction_energy": pattern.last_interaction_energy,
            "last_interaction_channel": pattern.last_interaction_channel,
            "open_threads": open_threads,
        }


async def get_calendar_context(user_id: str) -> dict:
    """Get calendar events and infer timezone (if Google Calendar connected).

    Returns dict with:
        - upcoming_events: Events in next 24 hours
        - just_ended_events: Events that ended in last hour
        - inferred_timezone: Timezone from calendar events (if available)
    """
    result = {
        "upcoming_events": [],
        "just_ended_events": [],
        "inferred_timezone": None,
    }

    try:
        from mypalclara.tools.google_oauth import get_valid_token, is_configured

        if not is_configured():
            return result

        token = await get_valid_token(user_id)
        if not token:
            return result

        import httpx

        now = datetime.now(UTC)

        # Fetch events from 1 hour ago to 24 hours from now
        time_min = now - timedelta(hours=1)
        time_max = now + timedelta(hours=24)

        params = {
            "maxResults": 20,
            "orderBy": "startTime",
            "singleEvents": True,
            "timeMin": time_min.isoformat(),
            "timeMax": time_max.isoformat(),
        }

        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                timeout=10.0,
            )

            if response.status_code != 200:
                return result

            data = response.json()

            # Try to get timezone from calendar settings
            if "timeZone" in data:
                result["inferred_timezone"] = data["timeZone"]

            upcoming = []
            just_ended = []

            for event in data.get("items", []):
                start = event.get("start", {})
                end = event.get("end", {})

                start_str = start.get("dateTime", start.get("date"))
                end_str = end.get("dateTime", end.get("date"))

                # Infer timezone from event if not already found
                if not result["inferred_timezone"] and start.get("timeZone"):
                    result["inferred_timezone"] = start["timeZone"]

                event_data = {
                    "summary": event.get("summary", "(No title)"),
                    "start": start_str,
                    "end": end_str,
                    "description": (event.get("description", "") or "")[:100],
                }

                # Categorize: upcoming or just ended
                try:
                    if end_str and "T" in end_str:
                        end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                        if end_dt.tzinfo:
                            end_dt = end_dt.replace(tzinfo=None)

                        if end_dt < now.replace(tzinfo=None):
                            # Event has ended
                            just_ended.append(event_data)
                        else:
                            upcoming.append(event_data)
                    else:
                        # All-day event or can't parse - treat as upcoming
                        upcoming.append(event_data)
                except (ValueError, TypeError):
                    upcoming.append(event_data)

            result["upcoming_events"] = upcoming[:10]
            result["just_ended_events"] = just_ended[:5]

            return result

    except Exception as e:
        logger.debug(f"Calendar context error: {e}")
        return result


async def infer_and_store_timezone(user_id: str, timezone: str):
    """Store inferred timezone from calendar."""
    if not timezone:
        return

    with SessionLocal() as session:
        pattern = session.query(UserInteractionPattern).filter(UserInteractionPattern.user_id == user_id).first()

        if pattern and not pattern.timezone:
            pattern.timezone = timezone
            pattern.timezone_source = "calendar"
            session.commit()
            logger.info(f"Inferred timezone for {user_id}: {timezone}")


def get_notes_context(user_id: str) -> dict:
    """Get pending notes and expiring notes.

    Returns dict with:
        - pending_notes: Notes that haven't been surfaced or archived
        - expiring_notes: Notes that expire within the next 2 hours
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    expiry_window = now + timedelta(hours=2)

    with SessionLocal() as session:
        # Get all active notes
        notes = (
            session.query(ProactiveNote)
            .filter(
                ProactiveNote.user_id == user_id,
                ProactiveNote.surfaced == "false",
                ProactiveNote.archived == "false",
            )
            .order_by(ProactiveNote.relevance_score.desc())
            .limit(15)
            .all()
        )

        pending = []
        expiring = []

        for n in notes:
            note_data = {
                "id": n.id,
                "note": n.note,
                "note_type": n.note_type,
                "relevance": n.relevance_score,
                "surface_conditions": n.surface_conditions,
                "expires_at": n.expires_at.isoformat() if n.expires_at else None,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }

            # Check if note is expiring soon
            if n.expires_at and n.expires_at <= expiry_window:
                expiring.append(note_data)
            else:
                pending.append(note_data)

        return {
            "pending_notes": pending[:10],
            "expiring_notes": expiring,
        }


async def validate_notes_against_context(
    notes: list[dict],
    recent_messages: list[dict],
    llm_call: Callable,
) -> list[ValidatedNote]:
    """Validate notes against recent conversation context.

    Uses the ORS model to check each note for:
    - Recency match: Does note reference something in recent conversation?
    - Resolution detection: Was this topic addressed/completed?
    - Contradiction check: Does recent conversation contradict note?

    Args:
        notes: List of note dicts from get_notes_context()
        recent_messages: List of recent message dicts from get_recent_messages()
        llm_call: Async LLM callable for validation

    Returns:
        List of ValidatedNote objects with scores and relevance status
    """
    if not notes:
        return []

    # Format recent messages for prompt
    if recent_messages:
        msg_lines = []
        for msg in recent_messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")[:300]
            msg_lines.append(f"[{role}]: {content}")
        messages_str = "\n".join(msg_lines)
    else:
        messages_str = "No recent conversation"

    validated = []

    for note in notes:
        note_text = note.get("note", "")
        note_type = note.get("note_type", "note")

        # Build validation prompt
        prompt = NOTE_VALIDATION_PROMPT.format(
            note=f"[{note_type}] {note_text}",
            recent_messages=messages_str,
        )

        try:
            response = await llm_call([{"role": "user", "content": prompt}])

            # Parse JSON response
            # Handle potential markdown code blocks
            response_text = response.strip()
            if response_text.startswith("```"):
                # Extract JSON from code block
                lines = response_text.split("\n")
                json_lines = [l for l in lines if not l.startswith("```")]
                response_text = "\n".join(json_lines)

            result = json.loads(response_text)
            status = result.get("status", NOTE_STATUS_STALE)
            score = float(result.get("score", 0.5))
            reason = result.get("reason", "")

            # Determine if note should be included
            is_relevant = status in (NOTE_STATUS_RELEVANT, NOTE_STATUS_STALE)

            # Apply source quality weight if available
            source_model = note.get("source_model")
            source_confidence = note.get("source_confidence")

            # Notes from weaker models or low confidence get extra penalty
            if source_confidence == "low":
                score *= 0.7
            if source_model and "haiku" in source_model.lower():
                score *= 0.8

            validated.append(
                ValidatedNote(
                    original_note=note,
                    context_match_score=score,
                    validation_status=status,
                    validation_reason=reason,
                    is_relevant=is_relevant,
                )
            )

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            # On parse failure, keep note with medium score
            logger.warning(f"Note validation parse error: {e}")
            validated.append(
                ValidatedNote(
                    original_note=note,
                    context_match_score=0.5,
                    validation_status=NOTE_STATUS_STALE,
                    validation_reason="Validation failed, treating as stale",
                    is_relevant=True,
                )
            )

    # Sort by relevance: relevant first, then by score
    validated.sort(
        key=lambda v: (
            0 if v.validation_status == NOTE_STATUS_RELEVANT else 1,
            -v.context_match_score,
        )
    )

    return validated


def get_patterns_context(user_id: str) -> dict:
    """Get learned interaction patterns."""
    with SessionLocal() as session:
        pattern = session.query(UserInteractionPattern).filter(UserInteractionPattern.user_id == user_id).first()

        if not pattern:
            return {}

        return {
            "proactive_response_rate": pattern.proactive_response_rate,
            "preferred_times": json.loads(pattern.preferred_proactive_times)
            if pattern.preferred_proactive_times
            else None,
            "preferred_proactive_types": json.loads(pattern.preferred_proactive_types)
            if pattern.preferred_proactive_types
            else None,
            "topic_receptiveness": json.loads(pattern.topic_receptiveness) if pattern.topic_receptiveness else None,
            "explicit_boundaries": json.loads(pattern.explicit_boundaries) if pattern.explicit_boundaries else None,
        }


def get_proactive_history(user_id: str, limit: int = 5) -> list[dict]:
    """Get recent proactive message history."""
    with SessionLocal() as session:
        messages = (
            session.query(ProactiveMessage)
            .filter(ProactiveMessage.user_id == user_id)
            .order_by(ProactiveMessage.sent_at.desc())
            .limit(limit)
            .all()
        )

        return [
            {
                "message": m.message[:100],
                "sent_at": m.sent_at.isoformat() if m.sent_at else None,
                "response_received": m.response_received == "true",
                "reason": m.reason,
            }
            for m in messages
        ]


def get_recent_messages(
    user_id: str, idle_timeout_minutes: int = ORS_IDLE_TIMEOUT_MINUTES, max_messages: int = 20
) -> list[dict]:
    """Fetch messages since last idle timeout (conversation gap).

    Finds messages since the most recent gap >= idle_timeout_minutes between messages.
    This represents the current active conversation context.

    Args:
        user_id: The user to fetch messages for
        idle_timeout_minutes: Gap duration that defines conversation boundary (default: 30)
        max_messages: Maximum messages to return if no gap found (default: 20)

    Returns:
        List of {id, role, content, timestamp} dicts, oldest first
    """
    with SessionLocal() as session:
        # Fetch recent messages ordered by time DESC
        messages = (
            session.query(Message)
            .filter(Message.user_id == user_id)
            .order_by(Message.created_at.desc())
            .limit(max_messages * 2)  # Fetch extra to find gap
            .all()
        )

        if not messages:
            return []

        # Find the first gap >= idle_timeout_minutes
        idle_threshold = timedelta(minutes=idle_timeout_minutes)
        cutoff_index = len(messages)  # Default: include all

        for i in range(len(messages) - 1):
            current = messages[i]
            previous = messages[i + 1]
            if current.created_at and previous.created_at:
                gap = current.created_at - previous.created_at
                if gap >= idle_threshold:
                    cutoff_index = i + 1  # Include up to this message
                    break

        # Take messages from after the gap (or all if no gap found)
        recent = messages[:cutoff_index]

        # Limit to max_messages and reverse to chronological order
        recent = recent[:max_messages]
        recent.reverse()

        return [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content[:500] if m.content else "",  # Truncate long messages
                "timestamp": m.created_at.isoformat() if m.created_at else None,
            }
            for m in recent
        ]


async def gather_full_context(user_id: str) -> ORSContext:
    """Gather all context sources into ORSContext."""
    temporal = get_temporal_context(user_id)
    calendar_data = await get_calendar_context(user_id)
    patterns = get_patterns_context(user_id)
    history = get_proactive_history(user_id)
    cross_channel = get_cross_channel_activity(user_id)

    # Fetch recent messages for context validation
    recent_messages = get_recent_messages(user_id)

    # Compute contact cadence from recent interaction timestamps
    contact_cadence_days = _compute_contact_cadence(user_id)

    # Get notes with validation against recent messages
    notes_data = get_notes_context(user_id)

    # Infer and store timezone from calendar if not already known
    if calendar_data.get("inferred_timezone") and not temporal.get("timezone"):
        await infer_and_store_timezone(user_id, calendar_data["inferred_timezone"])

    # Check if recent proactive messages were ignored
    recent_ignored = False
    if history:
        recent = history[:3]
        ignored_count = sum(1 for h in recent if not h.get("response_received"))
        recent_ignored = ignored_count >= 2

    # Time since last proactive
    time_since_proactive = None
    if history and history[0].get("sent_at"):
        try:
            last_sent = datetime.fromisoformat(history[0]["sent_at"])
            time_since_proactive = temporal["current_time"] - last_sent
        except (ValueError, TypeError):
            pass

    return ORSContext(
        user_id=user_id,
        current_time=temporal["current_time"],
        user_timezone=temporal.get("timezone") or calendar_data.get("inferred_timezone"),
        user_local_time=temporal.get("user_local_time"),
        time_since_last_interaction=temporal.get("time_since_last_interaction"),
        time_since_last_proactive=time_since_proactive,
        is_active_hours=temporal.get("is_active_hours", True),
        day_of_week=temporal.get("day_of_week", ""),
        last_interaction_summary=temporal.get("last_interaction_summary"),
        last_interaction_energy=temporal.get("last_interaction_energy"),
        last_interaction_channel=temporal.get("last_interaction_channel"),
        open_threads=temporal.get("open_threads", []),
        recent_messages=recent_messages,
        is_active_elsewhere=cross_channel.get("is_active_elsewhere", False),
        recent_channel_activity=cross_channel.get("recent_channels", []),
        upcoming_events=calendar_data.get("upcoming_events", []),
        just_ended_events=calendar_data.get("just_ended_events", []),
        pending_notes=notes_data.get("pending_notes", []),
        expiring_notes=notes_data.get("expiring_notes", []),
        proactive_response_rate=patterns.get("proactive_response_rate"),
        preferred_times=patterns.get("preferred_times"),
        preferred_proactive_types=patterns.get("preferred_proactive_types"),
        explicit_boundaries=patterns.get("explicit_boundaries"),
        recent_proactive_ignored=recent_ignored,
        recent_proactive_history=history,
        contact_cadence_days=contact_cadence_days,
    )


# =============================================================================
# Decision Logic
# =============================================================================


async def assess_situation(context: ORSContext, llm_call: Callable) -> str:
    """Phase 2: Generate situation assessment."""
    # Format context for prompt
    time_since = "Unknown"
    if context.time_since_last_interaction:
        hours = context.time_since_last_interaction.total_seconds() / 3600
        if hours < 1:
            time_since = f"{int(hours * 60)} minutes ago"
        elif hours < 24:
            time_since = f"{hours:.1f} hours ago"
        else:
            time_since = f"{hours / 24:.1f} days ago"

    # Format user local time
    user_local_str = "Unknown"
    if context.user_local_time:
        user_local_str = context.user_local_time.strftime("%Y-%m-%d %H:%M")
        if context.user_timezone:
            user_local_str += f" ({context.user_timezone})"

    calendar_str = "No calendar access"
    if context.upcoming_events:
        calendar_str = "\n".join(f"- {e['summary']} at {e['start']}" for e in context.upcoming_events[:5])
    elif context.upcoming_events == []:
        calendar_str = "No upcoming events in next 24h"

    # Format recently ended events
    just_ended_str = "None"
    if context.just_ended_events:
        just_ended_str = "\n".join(f"- {e['summary']} (ended at {e['end']})" for e in context.just_ended_events[:3])

    notes_str = "No pending notes"
    if context.pending_notes:
        note_lines = []
        for n in context.pending_notes[:5]:
            ntype = n.get("note_type", "note")
            note_lines.append(f"- [{ntype}] {n['note']} (rel: {n['relevance']})")
        notes_str = "\n".join(note_lines)

    # Format expiring notes
    expiring_str = "None"
    if context.expiring_notes:
        expiring_str = "\n".join(
            f"- {n['note']} (expires: {n.get('expires_at', 'soon')})" for n in context.expiring_notes
        )

    # Format open threads
    open_threads_str = "None tracked"
    if context.open_threads:
        open_threads_str = "\n".join(f"- {thread}" for thread in context.open_threads[:5])

    history_str = "No recent proactive messages"
    if context.recent_proactive_history:
        hist_lines = []
        for h in context.recent_proactive_history[:3]:
            resp = "got response" if h["response_received"] else "no response"
            hist_lines.append(f"- \"{h['message']}\" ({resp})")
        history_str = "\n".join(hist_lines)

    # Format cross-channel activity
    recent_channels_str = "No recent activity tracked"
    if context.recent_channel_activity:
        recent_channels_str = ", ".join(
            f"{ch['channel_id']} ({ch['minutes_ago']}m ago)" for ch in context.recent_channel_activity[:3]
        )

    # Format recent messages for context validation
    recent_messages_str = "No recent conversation"
    if context.recent_messages:
        msg_lines = []
        for msg in context.recent_messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")[:200]  # Truncate for prompt
            msg_lines.append(f"[{role}]: {content}")
        recent_messages_str = "\n".join(msg_lines)

    prompt = SITUATION_ASSESSMENT_PROMPT.format(
        bot_name=BOT_NAME,
        recent_messages=recent_messages_str,
        current_time=context.current_time.strftime("%Y-%m-%d %H:%M"),
        day_of_week=context.day_of_week,
        user_local_time=user_local_str,
        last_interaction_time=time_since,
        time_since_last=time_since,
        last_summary=context.last_interaction_summary or "No recent interaction",
        last_energy=context.last_interaction_energy or "Unknown",
        is_active_elsewhere=context.is_active_elsewhere,
        recent_channels=recent_channels_str,
        calendar_events=calendar_str,
        just_ended_events=just_ended_str,
        open_threads=open_threads_str,
        pending_notes=notes_str,
        expiring_notes=expiring_str,
        response_rate=context.proactive_response_rate or "Unknown",
        proactive_history=history_str,
    )

    messages = [{"role": "user", "content": prompt}]
    response = await llm_call(messages)
    return response


async def decide_action(
    context: ORSContext,
    assessment: str,
    llm_call: Callable,
) -> ORSDecision:
    """Phase 3: Decide WAIT/THINK/SPEAK based on assessment."""
    time_since_proactive = "Unknown"
    if context.time_since_last_proactive:
        hours = context.time_since_last_proactive.total_seconds() / 3600
        time_since_proactive = f"{hours:.1f} hours"

    boundaries_str = "None set"
    if context.explicit_boundaries:
        boundaries_str = json.dumps(context.explicit_boundaries)

    # Format user local time
    user_local_str = "Unknown"
    if context.user_local_time:
        user_local_str = context.user_local_time.strftime("%Y-%m-%d %H:%M")
        if context.user_timezone:
            user_local_str += f" ({context.user_timezone})"

    prompt = ACTION_DECISION_PROMPT.format(
        assessment=assessment,
        current_time=context.current_time.strftime("%Y-%m-%d %H:%M"),
        user_local_time=user_local_str,
        is_active_hours=context.is_active_hours,
        is_active_elsewhere=context.is_active_elsewhere,
        time_since_proactive=time_since_proactive,
        recent_ignored=context.recent_proactive_ignored,
        boundaries=boundaries_str,
    )

    messages = [{"role": "user", "content": prompt}]
    response = await llm_call(messages)

    # Parse JSON response
    try:
        # Find JSON in response
        import re

        json_match = re.search(r"\{[\s\S]*\}", response)
        if json_match:
            data = json.loads(json_match.group())
        else:
            raise ValueError("No JSON found in response")

        decision_str = data.get("decision", "WAIT").upper()
        state = ORSState[decision_str] if decision_str in ORSState.__members__ else ORSState.WAIT

        # Parse note-specific fields for THINK state
        note_type = None
        note_surface_conditions = None
        note_expires_hours = None
        note_confidence = None
        note_grounding_ids = None

        if state == ORSState.THINK:
            note_type = data.get("note_type")
            # Validate note type
            valid_types = [
                NOTE_TYPE_OBSERVATION,
                NOTE_TYPE_QUESTION,
                NOTE_TYPE_FOLLOW_UP,
                NOTE_TYPE_CONNECTION,
            ]
            if note_type not in valid_types:
                note_type = NOTE_TYPE_OBSERVATION  # Default

            # Parse confidence level
            note_confidence = data.get("note_confidence", "medium")
            if note_confidence not in ["high", "medium", "low"]:
                note_confidence = "medium"

            # Parse expiration
            expires = data.get("note_expires_hours")
            if expires is not None:
                try:
                    note_expires_hours = int(expires)
                    if note_expires_hours < 1 or note_expires_hours > 168:
                        note_expires_hours = None  # Invalid range
                except (ValueError, TypeError):
                    note_expires_hours = None

            # Parse surface conditions
            surface_after = data.get("surface_after_event")
            if surface_after:
                note_surface_conditions = {"after_event": surface_after}

            # Get grounding message IDs from recent context
            if context.recent_messages:
                note_grounding_ids = [m.get("id") for m in context.recent_messages if m.get("id")]

        return ORSDecision(
            state=state,
            reasoning=data.get("reasoning", ""),
            note_content=data.get("note") if state == ORSState.THINK else None,
            note_type=note_type,
            note_surface_conditions=note_surface_conditions,
            note_expires_hours=note_expires_hours,
            note_confidence=note_confidence,
            note_grounding_ids=note_grounding_ids,
            message=data.get("message") if state == ORSState.SPEAK else None,
            message_purpose=data.get("purpose") if state == ORSState.SPEAK else None,
            next_check_minutes=int(data.get("next_check_minutes", 15)),
        )
    except Exception as e:
        logger.warning(f"Failed to parse decision response: {e}")
        return ORSDecision(
            state=ORSState.WAIT,
            reasoning=f"Parse error, defaulting to WAIT: {e}",
            next_check_minutes=15,
        )


# =============================================================================
# Adaptive Timing
# =============================================================================


def calculate_next_check(context: ORSContext, decision: ORSDecision) -> int:
    """Calculate adaptive interval until next check (in minutes).

    Factors considered:
    - LLM suggestion from decision
    - Active hours
    - Recent proactive ignored
    - High-relevance pending notes
    - Expiring notes (time-sensitive)
    - Upcoming events (meeting proximity)
    - Just-ended events (follow-up opportunity)
    """
    base = ORS_BASE_INTERVAL_MINUTES

    # Start with LLM suggestion if reasonable
    suggested = decision.next_check_minutes
    if 5 <= suggested <= 480:
        base = suggested

    # Adjustments based on context
    multiplier = 1.0

    # Outside active hours - check less often
    if not context.is_active_hours:
        multiplier *= 4.0

    # Recent proactive ignored - back off
    if context.recent_proactive_ignored:
        multiplier *= 2.0

    # Has urgent pending notes - check more often
    if context.pending_notes:
        high_relevance = any(n.get("relevance", 0) > 80 for n in context.pending_notes)
        if high_relevance:
            multiplier *= 0.5

    # Has expiring notes - check more often to not miss them
    if context.expiring_notes:
        multiplier *= 0.5  # Check twice as often

    # Upcoming event soon - check more often
    now = context.current_time
    if context.upcoming_events:
        # Check if any event is within 2 hours
        for event in context.upcoming_events:
            try:
                start_str = event.get("start", "")
                if "T" in start_str:
                    start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                    if start.tzinfo:
                        start = start.replace(tzinfo=None)
                    time_until = (start - now).total_seconds()
                    if time_until < 7200:  # 2 hours
                        multiplier *= 0.5
                        break
                    elif time_until < 900:  # 15 minutes - check very soon
                        multiplier *= 0.25
                        break
            except (ValueError, TypeError):
                continue

    # Just-ended events - good time for follow-up check
    if context.just_ended_events:
        # Check soon after events end for follow-up opportunity
        multiplier *= 0.6

    # Calculate final interval
    final = int(base * multiplier)

    # Clamp to reasonable bounds
    return max(5, min(480, final))  # 5 min to 8 hours


# =============================================================================
# Note Management
# =============================================================================


def create_note(
    user_id: str,
    content: str,
    note_type: str | None = None,
    source_context: dict | None = None,
    surface_conditions: dict | None = None,
    expires_hours: int | None = None,
    source_model: str | None = None,
    source_confidence: str | None = None,
    grounding_message_ids: list[int] | None = None,
) -> str:
    """Create a new internal note from THINK state.

    Args:
        user_id: User this note is about
        content: The note content
        note_type: Type of note (observation, question, follow_up, connection)
        source_context: Context that triggered this note
        surface_conditions: JSON-serializable conditions for when to surface
        expires_hours: Hours until this note expires (for time-sensitive follow-ups)
        source_model: Model that created this note (e.g., "opus-4", "sonnet-4")
        source_confidence: Self-assessed confidence ("high", "medium", "low")
        grounding_message_ids: Message IDs that this note is grounded in

    Returns:
        The note ID
    """
    # Validate note type
    valid_types = [
        NOTE_TYPE_OBSERVATION,
        NOTE_TYPE_QUESTION,
        NOTE_TYPE_FOLLOW_UP,
        NOTE_TYPE_CONNECTION,
    ]
    if note_type and note_type not in valid_types:
        note_type = NOTE_TYPE_OBSERVATION

    # Validate confidence level
    valid_confidences = ["high", "medium", "low"]
    if source_confidence and source_confidence not in valid_confidences:
        source_confidence = "medium"

    # Calculate expiration time
    expires_at = None
    if expires_hours:
        expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=expires_hours)

    with SessionLocal() as session:
        note = ProactiveNote(
            user_id=user_id,
            note=content,
            note_type=note_type or NOTE_TYPE_OBSERVATION,
            source_context=json.dumps(source_context) if source_context else None,
            source_model=source_model,
            source_confidence=source_confidence,
            grounding_message_ids=json.dumps(grounding_message_ids) if grounding_message_ids else None,
            surface_conditions=json.dumps(surface_conditions) if surface_conditions else None,
            expires_at=expires_at,
            relevance_score=100,
        )
        session.add(note)
        session.commit()
        logger.info(
            f"Created {note_type or 'note'} for {user_id}: {content[:50]}..."
            + (f" (expires in {expires_hours}h)" if expires_hours else "")
            + (f" [model={source_model}, conf={source_confidence}]" if source_model else "")
        )
        return note.id


def decay_note_relevance():
    """Decay relevance of old notes (run periodically)."""
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=ORS_NOTE_DECAY_DAYS)

    with SessionLocal() as session:
        # Decay notes older than cutoff
        old_notes = (
            session.query(ProactiveNote)
            .filter(
                ProactiveNote.created_at < cutoff,
                ProactiveNote.surfaced == "false",
                ProactiveNote.archived == "false",
                ProactiveNote.relevance_score > 0,
            )
            .all()
        )

        for note in old_notes:
            days_old = (datetime.now(UTC).replace(tzinfo=None) - note.created_at).days
            decay_factor = max(0, 1 - (days_old / (ORS_NOTE_DECAY_DAYS * 2)))
            note.relevance_score = int(100 * decay_factor)

            # Archive if relevance hits 0
            if note.relevance_score <= 0:
                note.archived = "true"
                logger.debug(f"Archived stale note: {note.id}")

        session.commit()


def archive_expired_notes():
    """Archive notes that have passed their expiration time."""
    now = datetime.now(UTC).replace(tzinfo=None)

    with SessionLocal() as session:
        expired = (
            session.query(ProactiveNote)
            .filter(
                ProactiveNote.expires_at <= now,
                ProactiveNote.archived == "false",
            )
            .all()
        )

        for note in expired:
            note.archived = "true"
            logger.debug(f"Archived expired note: {note.id}")

        if expired:
            session.commit()
            logger.info(f"Archived {len(expired)} expired notes")


def mark_note_surfaced(note_id: str):
    """Mark a note as surfaced after being mentioned."""
    with SessionLocal() as session:
        note = session.query(ProactiveNote).filter(ProactiveNote.id == note_id).first()
        if note:
            note.surfaced = "true"
            note.surfaced_at = datetime.now(UTC).replace(tzinfo=None)
            session.commit()


# =============================================================================
# Conversation Extraction (Idle Detection)
# =============================================================================

# Track active conversations for idle detection (user_id -> last_message_time)
_active_conversations: dict[str, datetime] = {}

# Track per-channel activity for cross-channel awareness
# Structure: {user_id: {channel_id: last_activity_time}}
_user_channel_activity: dict[str, dict[str, datetime]] = {}

# How long to consider a channel "active" (in minutes)
CHANNEL_ACTIVE_THRESHOLD_MINUTES = 10


def get_cross_channel_activity(user_id: str) -> dict:
    """Get recent activity across all channels for a user.

    Returns dict with:
        - is_active_elsewhere: bool - active in channels other than DM recently
        - recent_channels: list of {channel_id, minutes_ago}
        - active_channel_count: int
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    threshold = timedelta(minutes=CHANNEL_ACTIVE_THRESHOLD_MINUTES)

    user_activity = _user_channel_activity.get(user_id, {})
    recent_channels = []
    active_count = 0

    for channel_id, last_time in user_activity.items():
        time_since = now - last_time
        if time_since < threshold:
            active_count += 1
            recent_channels.append(
                {
                    "channel_id": channel_id,
                    "minutes_ago": int(time_since.total_seconds() / 60),
                    "is_dm": channel_id.startswith("discord-dm-"),
                }
            )

    # Sort by most recent first
    recent_channels.sort(key=lambda x: x["minutes_ago"])

    # Check if active in non-DM channels (server channels)
    is_active_elsewhere = any(
        not ch["is_dm"] and ch["minutes_ago"] < CHANNEL_ACTIVE_THRESHOLD_MINUTES for ch in recent_channels
    )

    return {
        "is_active_elsewhere": is_active_elsewhere,
        "recent_channels": recent_channels[:5],  # Top 5 most recent
        "active_channel_count": active_count,
    }


def should_reconsider(user_id: str, last_decision_time: datetime | None) -> tuple[bool, str]:
    """Lightweight check if context has changed enough to warrant early re-evaluation.

    Called on loop iterations where user is not due for full processing.
    This enables the "think quietly" behavior even when waiting.

    Returns:
        (should_reconsider, reason) - True if early processing should happen
    """
    if not last_decision_time:
        return True, "no prior decision"

    now = datetime.now(UTC).replace(tzinfo=None)
    minutes_since_decision = (now - last_decision_time).total_seconds() / 60

    # Check 1: New channel activity since last decision
    activity = get_cross_channel_activity(user_id)
    if activity["recent_channels"]:
        most_recent = activity["recent_channels"][0]
        if most_recent["minutes_ago"] < minutes_since_decision:
            # Activity happened after our last decision
            return True, f"new activity in channel {most_recent['minutes_ago']}m ago"

    # Check 2: Notes that are about to expire (within 30 minutes)
    with SessionLocal() as session:
        expiring_soon = (
            session.query(ProactiveNote)
            .filter(
                ProactiveNote.user_id == user_id,
                ProactiveNote.surfaced == "false",
                ProactiveNote.archived == "false",
                ProactiveNote.expires_at.isnot(None),
                ProactiveNote.expires_at <= now + timedelta(minutes=30),
                ProactiveNote.expires_at > now,
            )
            .count()
        )
        if expiring_soon > 0:
            return True, f"{expiring_soon} note(s) expiring within 30m"

    # Check 3: If we've been waiting a long time (>30 min) with WAIT state,
    # do a lightweight re-evaluation to stay present
    if minutes_since_decision > 30:
        return True, "periodic re-evaluation (>30m since last decision)"

    return False, "no significant changes"


def track_channel_activity(user_id: str, channel_id: str):
    """Track a user's activity in a specific channel."""
    now = datetime.now(UTC).replace(tzinfo=None)

    if user_id not in _user_channel_activity:
        _user_channel_activity[user_id] = {}

    _user_channel_activity[user_id][channel_id] = now

    # Cleanup old entries (older than 1 hour) to prevent memory bloat
    cutoff = now - timedelta(hours=1)
    _user_channel_activity[user_id] = {ch: ts for ch, ts in _user_channel_activity[user_id].items() if ts > cutoff}


async def extract_conversation_info(
    user_id: str,
    conversation_text: str,
    llm_call: Callable,
) -> dict | None:
    """Extract summary, energy, and open threads from a conversation using LLM.

    Called when a conversation goes idle (no messages for ORS_IDLE_TIMEOUT_MINUTES).

    Returns dict with summary, energy, open_threads, notable (or None on failure).
    """
    if not conversation_text or len(conversation_text.strip()) < 50:
        return None

    prompt = CONVERSATION_EXTRACTION_PROMPT.format(conversation=conversation_text[:4000])
    messages = [{"role": "user", "content": prompt}]

    try:
        response = await llm_call(messages)

        # Parse JSON response
        import re

        json_match = re.search(r"\{[\s\S]*\}", response)
        if json_match:
            data = json.loads(json_match.group())

            # Validate energy
            valid_energies = [
                "stressed",
                "focused",
                "casual",
                "tired",
                "excited",
                "frustrated",
                "neutral",
            ]
            energy = data.get("energy", "neutral")
            if energy not in valid_energies:
                energy = "neutral"

            return {
                "summary": data.get("summary", ""),
                "energy": energy,
                "open_threads": data.get("open_threads", []),
                "notable": data.get("notable"),
            }

    except Exception as e:
        logger.warning(f"Failed to extract conversation info: {e}")

    return None


def update_interaction_from_extraction(user_id: str, extraction: dict):
    """Update UserInteractionPattern with extracted conversation info."""
    from mypalclara.core.emotional_context import finalize_conversation_emotional_context

    with SessionLocal() as session:
        pattern = session.query(UserInteractionPattern).filter(UserInteractionPattern.user_id == user_id).first()

        if not pattern:
            return

        # Update summary and energy
        if extraction.get("summary"):
            pattern.last_interaction_summary = extraction["summary"]
        if extraction.get("energy"):
            pattern.last_interaction_energy = extraction["energy"]

        # Merge open threads (keep recent, avoid duplicates)
        existing_threads = []
        if pattern.open_threads:
            try:
                existing_threads = json.loads(pattern.open_threads)
            except json.JSONDecodeError:
                pass

        new_threads = extraction.get("open_threads", [])
        if new_threads:
            # Add new threads, keeping unique ones
            combined = new_threads + [t for t in existing_threads if t not in new_threads]
            # Limit to 10 most recent
            pattern.open_threads = json.dumps(combined[:10])

        session.commit()
        logger.info(f"Updated interaction pattern for {user_id} from extraction")

        # Also finalize emotional context to mem0 (when ORS is enabled)
        if extraction.get("summary") and extraction.get("energy"):
            channel_id = pattern.last_interaction_channel or "unknown"
            is_dm = channel_id.startswith("discord-dm-") if channel_id else False
            channel_name = "DM" if is_dm else channel_id.replace("discord-channel-", "#")

            finalize_conversation_emotional_context(
                user_id=user_id,
                channel_id=channel_id,
                channel_name=channel_name,
                is_dm=is_dm,
                energy=extraction["energy"],
                summary=extraction["summary"],
            )


async def check_idle_conversations(llm_call: Callable, get_recent_messages: Callable | None = None):
    """Check for idle conversations and extract info.

    Args:
        llm_call: Async function to call LLM
        get_recent_messages: Optional function(user_id) -> str that returns recent conversation text
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    idle_threshold = timedelta(minutes=ORS_IDLE_TIMEOUT_MINUTES)

    # Find users with idle conversations
    idle_users = []
    for user_id, last_msg_time in list(_active_conversations.items()):
        if now - last_msg_time > idle_threshold:
            idle_users.append(user_id)
            del _active_conversations[user_id]

    # Process idle conversations
    for user_id in idle_users:
        try:
            # Get recent conversation (if function provided)
            if get_recent_messages:
                conversation_text = await get_recent_messages(user_id)
                if conversation_text:
                    extraction = await extract_conversation_info(user_id, conversation_text, llm_call)
                    if extraction:
                        update_interaction_from_extraction(user_id, extraction)

                        # Update contact cadence while we have fresh data
                        cadence = _compute_contact_cadence(user_id)
                        if cadence is not None:
                            with SessionLocal() as cadence_session:
                                pattern = (
                                    cadence_session.query(UserInteractionPattern)
                                    .filter(UserInteractionPattern.user_id == user_id)
                                    .first()
                                )
                                if pattern:
                                    pattern.contact_cadence_days = cadence
                                    cadence_session.commit()

                        # Create note if something notable
                        if extraction.get("notable"):
                            create_note(
                                user_id=user_id,
                                content=extraction["notable"],
                                note_type=NOTE_TYPE_OBSERVATION,
                                source_context={"from": "conversation_extraction"},
                                source_model=get_ors_model_name(),
                                source_confidence="medium",  # Extracted from conversation
                            )
        except Exception as e:
            logger.error(f"Error extracting conversation for {user_id}: {e}")


# =============================================================================
# Assessment Recording
# =============================================================================


def record_assessment(
    user_id: str,
    context: ORSContext,
    assessment: str,
    decision: ORSDecision,
    note_id: str | None = None,
) -> str:
    """Record an assessment for continuity and debugging."""
    with SessionLocal() as session:
        record = ProactiveAssessment(
            user_id=user_id,
            context_snapshot=json.dumps(
                {
                    "time_since_last_interaction": str(context.time_since_last_interaction),
                    "is_active_hours": context.is_active_hours,
                    "pending_notes_count": len(context.pending_notes),
                    "upcoming_events_count": len(context.upcoming_events),
                    "recent_proactive_ignored": context.recent_proactive_ignored,
                }
            ),
            assessment=assessment,
            decision=decision.state.value,
            reasoning=decision.reasoning,
            note_created=note_id,
            message_sent=decision.message if decision.state == ORSState.SPEAK else None,
            next_check_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=decision.next_check_minutes),
        )
        session.add(record)
        session.commit()
        return record.id


# =============================================================================
# Message Sending
# =============================================================================


def _compute_contact_cadence(user_id: str) -> float | None:
    """Compute average days between interactions from recent message history.

    Looks at the last 10 messages, computes gaps between consecutive messages,
    filters out rapid exchanges (< 5 min) to focus on conversation-level gaps,
    and returns the average gap in days.

    Falls back to persisted contact_cadence_days on UserInteractionPattern if
    insufficient message data.
    """
    with SessionLocal() as session:
        messages = (
            session.query(Message)
            .filter(Message.user_id == user_id)
            .order_by(Message.created_at.desc())
            .limit(10)
            .all()
        )

        if len(messages) < 3:
            # Not enough data from messages — try persisted value
            pattern = session.query(UserInteractionPattern).filter(UserInteractionPattern.user_id == user_id).first()
            return pattern.contact_cadence_days if pattern else None

        # Compute gaps between consecutive messages (messages are newest-first)
        gaps = []
        for i in range(len(messages) - 1):
            if messages[i].created_at and messages[i + 1].created_at:
                gap = messages[i].created_at - messages[i + 1].created_at
                gap_minutes = gap.total_seconds() / 60
                # Ignore rapid exchanges (< 5 min) — those are within a single conversation
                if gap_minutes >= 5:
                    gaps.append(gap)

        if not gaps:
            pattern = session.query(UserInteractionPattern).filter(UserInteractionPattern.user_id == user_id).first()
            return pattern.contact_cadence_days if pattern else None

        avg_gap = sum(g.total_seconds() for g in gaps) / len(gaps)
        return avg_gap / 86400  # Convert seconds to days


def _persist_proactive_to_history(user_id: str, channel_id: str, message: str):
    """Persist a proactive message into conversation history so Clara can see it.

    Finds the active session for this user+channel and creates a Message record
    with role="assistant". This bridges the gap between ORS (which sends messages)
    and the conversation context (which Clara reads on next interaction).
    """
    # Determine context_id using same logic as gateway processor
    is_dm = channel_id.startswith("discord-dm-") or "-dm-" in channel_id
    context_id = f"dm-{user_id}" if is_dm else f"channel-{channel_id}"

    try:
        with SessionLocal() as db:
            # Find the most recent active session for this user + context
            conv_session = (
                db.query(Session)
                .filter(
                    Session.user_id == user_id,
                    Session.context_id == context_id,
                    Session.archived != "true",
                )
                .order_by(Session.last_activity_at.desc())
                .first()
            )

            if not conv_session:
                logger.debug(f"No active session for {user_id}/{context_id} — skipping history persistence")
                return

            msg = Message(
                session_id=conv_session.id,
                user_id=user_id,
                role="assistant",
                content=message,
            )
            db.add(msg)
            db.commit()
            logger.debug(f"Persisted proactive message to session {conv_session.id} for {user_id}")
    except Exception as e:
        # Non-fatal — the message was already sent, this is just for context
        logger.warning(f"Failed to persist proactive message to history: {e}")


async def send_proactive_message(
    client: Client,
    user_id: str,
    channel_id: str,
    message: str,
    purpose: str,
) -> bool:
    """Send a proactive message and record it."""
    try:
        # Parse channel ID
        if channel_id.startswith("discord-dm-"):
            # DM to user
            discord_user_id = int(channel_id.replace("discord-dm-", "").replace("discord-", ""))
            user = await client.fetch_user(discord_user_id)
            dm = await user.create_dm()
            await dm.send(message)
        elif channel_id.startswith("discord-channel-"):
            # Channel message
            discord_channel_id = int(channel_id.replace("discord-channel-", ""))
            channel = client.get_channel(discord_channel_id)
            if channel:
                await channel.send(message)
            else:
                logger.warning(f"Channel not found: {channel_id}")
                return False
        else:
            logger.warning(f"Unknown channel format: {channel_id}")
            return False

        # Record the message
        with SessionLocal() as session:
            record = ProactiveMessage(
                user_id=user_id,
                channel_id=channel_id,
                message=message,
                priority="normal",
                reason=purpose,
            )
            session.add(record)
            session.commit()

        # Persist to conversation history so Clara sees it next interaction
        _persist_proactive_to_history(user_id, channel_id, message)

        logger.info(f"Sent proactive message to {user_id}: {message[:50]}...")
        return True

    except Exception as e:
        logger.error(f"Failed to send proactive message: {e}")
        return False


# =============================================================================
# Main Loop
# =============================================================================


def is_enabled() -> bool:
    """Check if ORS is enabled."""
    return ORS_ENABLED


def get_active_users() -> list[str]:
    """Get users who have been active recently."""
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=ORS_ACTIVE_DAYS)

    with SessionLocal() as session:
        patterns = (
            session.query(UserInteractionPattern).filter(UserInteractionPattern.last_interaction_at > cutoff).all()
        )

        return [p.user_id for p in patterns]


async def process_user(
    user_id: str,
    client: Client,
    llm_call: Callable,
) -> int:
    """Process ORS cycle for a single user. Returns minutes until next check."""
    logger.debug(f"Processing ORS for user: {user_id}")

    # Get the current model name for source tracking
    model_name = get_ors_model_name()

    # Phase 1: Gather context
    context = await gather_full_context(user_id)

    # Phase 2: Assess situation
    assessment = await assess_situation(context, llm_call)

    # Phase 3: Decide action
    decision = await decide_action(context, assessment, llm_call)

    # Phase 4: Execute
    note_id = None

    if decision.state == ORSState.THINK and decision.note_content:
        # File the observation with type, expiration, and source tracking
        note_id = create_note(
            user_id=user_id,
            content=decision.note_content,
            note_type=decision.note_type,
            source_context={
                "from": "think_decision",
                "assessment": assessment[:200],
            },
            surface_conditions=decision.note_surface_conditions,
            expires_hours=decision.note_expires_hours,
            source_model=model_name,
            source_confidence=decision.note_confidence,
            grounding_message_ids=decision.note_grounding_ids,
        )
        logger.info(f"ORS THINK for {user_id}: {decision.note_content[:50]}...")

    elif decision.state == ORSState.SPEAK and decision.message:
        # Check minimum gap since last proactive
        can_speak = True
        if context.time_since_last_proactive:
            hours_since = context.time_since_last_proactive.total_seconds() / 3600
            if hours_since < ORS_MIN_SPEAK_GAP_HOURS:
                can_speak = False
                logger.info(
                    f"ORS SPEAK blocked for {user_id}: only {hours_since:.1f}h "
                    f"since last (min: {ORS_MIN_SPEAK_GAP_HOURS}h)"
                )

        if can_speak and context.last_interaction_channel:
            success = await send_proactive_message(
                client=client,
                user_id=user_id,
                channel_id=context.last_interaction_channel,
                message=decision.message,
                purpose=decision.message_purpose or decision.reasoning,
            )
            if success:
                logger.info(f"ORS SPEAK for {user_id}: {decision.message[:50]}...")

    else:
        logger.debug(f"ORS WAIT for {user_id}: {decision.reasoning[:50]}...")

    # Record assessment
    record_assessment(user_id, context, assessment, decision, note_id)

    # Calculate adaptive timing
    return calculate_next_check(context, decision)


async def ors_main_loop(
    client: Client,
    llm_call: Callable,
    get_recent_messages: Callable | None = None,
):
    """Main ORS loop - runs continuously with adaptive timing.

    Args:
        client: Discord client for sending messages
        llm_call: Async function to call LLM
        get_recent_messages: Optional async function(user_id) -> str for conversation extraction
    """
    logger.info("ORS main loop starting")

    # Track next check time per user
    next_checks: dict[str, datetime] = {}

    # Track last decision time per user (for reconsideration checks)
    last_decisions: dict[str, datetime] = {}

    # Track last maintenance time
    last_maintenance = datetime.now(UTC).replace(tzinfo=None)

    logger.info("ORS entering main loop")

    while True:
        try:
            now = datetime.now(UTC).replace(tzinfo=None)
            logger.info(f"ORS loop iteration at {now.isoformat()}")

            # Get active users
            users = get_active_users()
            logger.info(f"ORS found {len(users)} active users")
            if not users:
                logger.info("No active users, sleeping 15 minutes")
                await asyncio.sleep(900)
                continue

            # Run maintenance tasks periodically (every 10 minutes)
            if (now - last_maintenance).total_seconds() > 600:
                # Decay old note relevance
                decay_note_relevance()

                # Archive expired notes
                archive_expired_notes()

                # Check for idle conversations and extract info
                await check_idle_conversations(llm_call, get_recent_messages)

                last_maintenance = now

            # Process users - either full processing or reconsideration check
            processed_count = 0
            reconsidered_count = 0
            for user_id in users:
                next_check = next_checks.get(user_id)

                if next_check and now < next_check:
                    # Not time for full processing, but check if we should reconsider
                    should_reeval, reason = should_reconsider(user_id, last_decisions.get(user_id))
                    if should_reeval:
                        logger.debug(f"ORS reconsidering {user_id}: {reason}")
                        reconsidered_count += 1
                        # Fall through to full processing
                    else:
                        continue  # Still waiting, no significant changes

                try:
                    logger.info(f"ORS processing user: {user_id}")
                    minutes_until_next = await process_user(user_id, client, llm_call)
                    next_checks[user_id] = now + timedelta(minutes=minutes_until_next)
                    last_decisions[user_id] = now  # Track when we made this decision
                    processed_count += 1
                    logger.info(f"ORS processed {user_id}, next check in {minutes_until_next}m")
                except Exception as e:
                    logger.error(f"Error processing user {user_id}: {e}", exc_info=True)
                    next_checks[user_id] = now + timedelta(minutes=30)

                # Small delay between users to avoid rate limits
                await asyncio.sleep(2)

            logger.info(
                f"ORS processed {processed_count}/{len(users)} users this iteration "
                f"(reconsidered: {reconsidered_count})"
            )

            # Sleep until next user needs checking (or 5 min max)
            if next_checks:
                soonest = min(next_checks.values())
                sleep_seconds = max(60, min(300, (soonest - now).total_seconds()))
            else:
                sleep_seconds = 300

            logger.info(f"ORS sleeping {sleep_seconds / 60:.1f} minutes")
            await asyncio.sleep(sleep_seconds)

        except Exception as e:
            logger.error(f"ORS loop error: {e}", exc_info=True)
            await asyncio.sleep(60)


# =============================================================================
# Open Thread Management
# =============================================================================


def resolve_open_thread(user_id: str, thread_content: str):
    """Mark an open thread as resolved (remove from list).

    Called when a topic/question from open_threads is addressed.
    """
    with SessionLocal() as session:
        pattern = session.query(UserInteractionPattern).filter(UserInteractionPattern.user_id == user_id).first()

        if not pattern or not pattern.open_threads:
            return

        try:
            threads = json.loads(pattern.open_threads)
            # Remove threads that match (case-insensitive, partial match)
            thread_lower = thread_content.lower()
            threads = [t for t in threads if thread_lower not in t.lower()]
            pattern.open_threads = json.dumps(threads) if threads else None
            session.commit()
            logger.debug(f"Resolved open thread for {user_id}: {thread_content[:50]}")
        except json.JSONDecodeError:
            pass


def add_open_thread(user_id: str, thread_content: str):
    """Manually add an open thread to track.

    Called when Clara notices something unresolved in conversation.
    """
    with SessionLocal() as session:
        pattern = session.query(UserInteractionPattern).filter(UserInteractionPattern.user_id == user_id).first()

        if not pattern:
            pattern = UserInteractionPattern(user_id=user_id)
            session.add(pattern)

        existing = []
        if pattern.open_threads:
            try:
                existing = json.loads(pattern.open_threads)
            except json.JSONDecodeError:
                pass

        # Add if not duplicate
        if thread_content not in existing:
            existing.insert(0, thread_content)  # Add to front (most recent)
            pattern.open_threads = json.dumps(existing[:10])  # Keep max 10

        session.commit()


# =============================================================================
# Public API (backwards compatible with proactive_engine.py)
# =============================================================================


async def on_user_message(
    user_id: str,
    channel_id: str,
    message_preview: str | None = None,
):
    """Called when user sends a message - updates interaction patterns and tracks for idle detection."""
    now = datetime.now(UTC).replace(tzinfo=None)

    # Track for idle detection
    _active_conversations[user_id] = now

    # Track per-channel activity for cross-channel awareness
    track_channel_activity(user_id, channel_id)

    with SessionLocal() as session:
        pattern = session.query(UserInteractionPattern).filter(UserInteractionPattern.user_id == user_id).first()

        if not pattern:
            pattern = UserInteractionPattern(user_id=user_id)
            session.add(pattern)

        pattern.last_interaction_at = now
        pattern.last_interaction_channel = channel_id
        session.commit()


async def on_proactive_response(user_id: str, channel_id: str):
    """Called when user responds to a proactive message - updates statistics."""
    now = datetime.now(UTC).replace(tzinfo=None)

    with SessionLocal() as session:
        # Mark the most recent proactive message as responded
        message = (
            session.query(ProactiveMessage)
            .filter(
                ProactiveMessage.user_id == user_id,
                ProactiveMessage.response_received == "false",
            )
            .order_by(ProactiveMessage.sent_at.desc())
            .first()
        )

        if message:
            message.response_received = "true"
            message.response_at = now

        # Update response rate
        pattern = session.query(UserInteractionPattern).filter(UserInteractionPattern.user_id == user_id).first()

        if pattern:
            # Calculate new response rate from last 20 proactive messages
            recent_messages = (
                session.query(ProactiveMessage)
                .filter(
                    ProactiveMessage.user_id == user_id,
                )
                .order_by(ProactiveMessage.sent_at.desc())
                .limit(20)
                .all()
            )

            if recent_messages:
                responded = sum(1 for m in recent_messages if m.response_received == "true")
                pattern.proactive_response_rate = int((responded / len(recent_messages)) * 100)

        session.commit()


# Alias for backwards compatibility
proactive_check_loop = ors_main_loop
