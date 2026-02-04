"""Emotional context tracking for conversation continuity.

This module tracks sentiment across conversations and stores emotional
summaries to mem0 for retrieval at session start. The goal is to carry
forward emotional texture so new sessions don't start cold.

Key components:
- Per-message sentiment tracking (in-memory, per user/channel)
- Emotional arc computation (stable, improving, declining, volatile)
- mem0 storage with metadata for retrieval
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from clara_core.sentiment import analyze_sentiment
from config.logging import get_logger

logger = get_logger("emotional")

if TYPE_CHECKING:
    pass

# In-memory tracking of sentiment per active conversation
# Structure: {user_id: {channel_id: {"sentiments": [...], "started_at": datetime}}}
_conversation_sentiments: dict[str, dict[str, dict]] = {}

# Minimum messages needed for meaningful arc computation
MIN_MESSAGES_FOR_ARC = 3


@dataclass
class EmotionalSummary:
    """Summary of emotional context for a conversation."""

    starting_sentiment: float
    ending_sentiment: float
    arc: str  # stable, improving, declining, volatile
    dominant_emotion: str  # energy level from ORS
    topic_summary: str
    channel_id: str
    channel_name: str
    is_dm: bool
    timestamp: datetime


def track_message_sentiment(
    user_id: str,
    channel_id: str,
    message_content: str,
) -> float:
    """
    Track sentiment for a message in an active conversation.

    Args:
        user_id: The user sending the message
        channel_id: The channel/DM where the message was sent
        message_content: The message text to analyze

    Returns:
        The compound sentiment score (-1 to +1)
    """
    sentiment = analyze_sentiment(message_content)
    compound = sentiment["compound"]

    # Initialize tracking structures if needed
    if user_id not in _conversation_sentiments:
        _conversation_sentiments[user_id] = {}
    if channel_id not in _conversation_sentiments[user_id]:
        _conversation_sentiments[user_id][channel_id] = {
            "sentiments": [],
            "started_at": datetime.now(UTC),
        }

    _conversation_sentiments[user_id][channel_id]["sentiments"].append(compound)

    return compound


def get_conversation_sentiments(user_id: str, channel_id: str) -> list[float]:
    """Get tracked sentiments for a conversation."""
    return (
        _conversation_sentiments.get(user_id, {})
        .get(channel_id, {})
        .get("sentiments", [])
    )


def clear_conversation_sentiments(user_id: str, channel_id: str) -> None:
    """Clear tracked sentiments for a conversation (after finalization)."""
    if user_id in _conversation_sentiments:
        if channel_id in _conversation_sentiments[user_id]:
            del _conversation_sentiments[user_id][channel_id]


def compute_emotional_arc(sentiment_timeline: list[float]) -> str:
    """
    Classify the emotional arc of a conversation based on sentiment trajectory.

    Args:
        sentiment_timeline: List of compound sentiment scores in chronological order

    Returns:
        One of: "stable", "improving", "declining", "volatile"
    """
    if len(sentiment_timeline) < MIN_MESSAGES_FOR_ARC:
        return "stable"

    # Calculate start and end averages (first/last 3 messages)
    start_avg = sum(sentiment_timeline[:3]) / 3
    end_avg = sum(sentiment_timeline[-3:]) / 3

    # Calculate variance to detect volatility
    variance = (
        statistics.variance(sentiment_timeline)
        if len(sentiment_timeline) > 1
        else 0
    )

    # Classify arc based on trajectory and volatility
    if variance > 0.3:
        return "volatile"
    elif end_avg - start_avg > 0.2:
        return "improving"
    elif start_avg - end_avg > 0.2:
        return "declining"
    else:
        return "stable"


def finalize_conversation_emotional_context(
    user_id: str,
    channel_id: str,
    channel_name: str,
    is_dm: bool,
    energy: str,
    summary: str,
    agent_id: str = "clara",
    on_event: Callable[[str, dict], None] | None = None,
) -> EmotionalSummary | None:
    """
    Finalize emotional context for a conversation and store to mem0.

    Called when conversation goes idle (30+ min gap) or explicitly ends.

    Args:
        user_id: The user's ID
        channel_id: The channel/DM ID
        channel_name: Human-readable channel name (e.g., "#general" or "DM")
        is_dm: Whether this is a DM conversation
        energy: Energy level from ORS extraction (stressed, focused, casual, etc.)
        summary: Topic summary from ORS extraction
        agent_id: Clara's agent ID for mem0
        on_event: Optional callback for emotional context events.
            Called with ("emotional_context_stored", data_dict).

    Returns:
        EmotionalSummary if successful, None if no data to finalize
    """
    from clara_core.memory import ROOK

    sentiments = get_conversation_sentiments(user_id, channel_id)

    if not sentiments:
        return None

    # Compute emotional summary
    arc = compute_emotional_arc(sentiments)
    starting = sentiments[0] if sentiments else 0.0
    ending = sentiments[-1] if sentiments else 0.0
    now = datetime.now(UTC)

    emotional_summary = EmotionalSummary(
        starting_sentiment=starting,
        ending_sentiment=ending,
        arc=arc,
        dominant_emotion=energy,
        topic_summary=summary,
        channel_id=channel_id,
        channel_name=channel_name,
        is_dm=is_dm,
        timestamp=now,
    )

    # Store to mem0 as emotional context memory
    if ROOK:
        memory_text = _format_emotional_memory(emotional_summary)
        metadata = {
            "memory_type": "emotional_context",
            "timestamp": now.isoformat(),
            "channel_id": channel_id,
            "channel_name": channel_name,
            "is_dm": is_dm,
            "ending_sentiment": ending,
            "starting_sentiment": starting,
            "emotional_arc": arc,
            "energy_level": energy,
        }

        try:
            ROOK.add(
                [{"role": "system", "content": memory_text}],
                user_id=user_id,
                agent_id=agent_id,
                metadata=metadata,
            )
            logger.info(f"Stored emotional context for {user_id}: {arc} arc, {energy} energy")
            # Notify via callback if registered
            if on_event:
                on_event("emotional_context_stored", {
                    "user_id": user_id,
                    "arc": arc,
                    "energy": energy,
                    "channel_name": channel_name,
                    "is_dm": is_dm,
                })
        except Exception as e:
            logger.error(f"Error storing emotional context: {e}", exc_info=True)

    # Clear tracked sentiments for this conversation
    clear_conversation_sentiments(user_id, channel_id)

    return emotional_summary


def _format_emotional_memory(summary: EmotionalSummary) -> str:
    """
    Format emotional summary as a natural language memory for storage.

    The format is designed to be readable when surfaced in the prompt.
    """
    arc_descriptions = {
        "stable": "maintained consistent energy",
        "improving": "started tense but became more relaxed",
        "declining": "started light but became more stressed",
        "volatile": "had emotional ups and downs",
    }
    arc_desc = arc_descriptions.get(summary.arc, "had varied emotional tones")

    # Build natural language memory
    topic = summary.topic_summary or "general conversation"
    energy = summary.dominant_emotion or "neutral"

    return (
        f"Conversation about {topic}. "
        f"They {arc_desc} throughout. "
        f"Ended with {energy} energy."
    )


def has_pending_emotional_context(user_id: str, channel_id: str) -> bool:
    """Check if there's unfinalized emotional context for a conversation."""
    sentiments = get_conversation_sentiments(user_id, channel_id)
    return len(sentiments) > 0


