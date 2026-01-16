"""Topic recurrence tracking for conversation awareness.

This module extracts topics from conversations and tracks their recurrence
patterns over time. Enables Clara to notice when topics keep coming up and
how the emotional weight changes.

Key components:
- LLM-based topic extraction (entities + themes)
- mem0 storage with sentiment and weight metadata
- Pattern detection (frequency, sentiment trend, weight progression)
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    pass


# Topic extraction prompt for LLM
TOPIC_EXTRACTION_PROMPT = """Extract key topics from this conversation that might recur in future conversations.

**The conversation:**
{conversation}

**Conversation sentiment:** {sentiment:.2f} (scale: -1 negative to +1 positive)

**What to extract:**
For each topic, provide:
- topic: Normalized name using consistent, lowercase, singular forms. Prefer common phrasing (e.g., "job search" not "employment hunt" or "the job hunt", "mom" not "my mother")
- topic_type: "entity" (person, place, project, company) or "theme" (ongoing concern, interest, goal)
- context_snippet: Brief summary of how it came up (10-20 words)
- emotional_weight: "light" (casual mention), "moderate" (some feeling), "heavy" (significant emotion)

**Rules:**
1. Only extract topics with emotional significance OR specific enough to recur
2. Skip generic topics like "work", "life", "stuff", "things"
3. Use consistent normalization - same topic should always have the same name
4. Max 3 unique topics per conversation

**Respond in JSON:**
{{
    "topics": [
        {{
            "topic": "job search",
            "topic_type": "theme",
            "context_snippet": "frustrated about not hearing back from interviews",
            "emotional_weight": "heavy"
        }}
    ]
}}

If no significant topics, return: {{"topics": []}}"""


async def extract_topics_from_conversation(
    conversation_text: str,
    conversation_sentiment: float,
    llm_call: Callable,
) -> list[dict]:
    """
    Extract topics from a conversation using LLM.

    Args:
        conversation_text: The conversation text to analyze
        conversation_sentiment: Overall sentiment score (-1 to +1)
        llm_call: Async function that takes messages and returns LLM response

    Returns:
        List of topic dicts with keys: topic, topic_type, context_snippet, emotional_weight
    """
    if not conversation_text or len(conversation_text.strip()) < 50:
        return []

    prompt = TOPIC_EXTRACTION_PROMPT.format(
        conversation=conversation_text[:4000],
        sentiment=conversation_sentiment,
    )
    messages = [{"role": "user", "content": prompt}]

    try:
        response = await llm_call(messages)

        # Parse JSON response
        json_match = re.search(r"\{[\s\S]*\}", response)
        if json_match:
            data = json.loads(json_match.group())
            topics = data.get("topics", [])

            # Validate and normalize topics (cap applied after dedup in extract_and_store_topics)
            valid_topics = []
            valid_weights = {"light", "moderate", "heavy"}
            valid_types = {"entity", "theme"}

            for t in topics:
                topic_name = t.get("topic", "").strip().lower()
                if not topic_name or len(topic_name) < 2:
                    continue

                topic_type = t.get("topic_type", "theme")
                if topic_type not in valid_types:
                    topic_type = "theme"

                weight = t.get("emotional_weight", "moderate")
                if weight not in valid_weights:
                    weight = "moderate"

                valid_topics.append({
                    "topic": topic_name,
                    "topic_type": topic_type,
                    "context_snippet": t.get("context_snippet", "")[:100],
                    "emotional_weight": weight,
                })

            return valid_topics

    except Exception as e:
        print(f"[topic] Failed to extract topics: {e}")

    return []


def store_topic_mention(
    user_id: str,
    topic: str,
    topic_type: str,
    context_snippet: str,
    emotional_weight: str,
    sentiment: float,
    channel_id: str,
    channel_name: str,
    is_dm: bool,
    agent_id: str = "clara",
) -> bool:
    """
    Store a topic mention to mem0.

    Args:
        user_id: The user's ID
        topic: Normalized topic name
        topic_type: "entity" or "theme"
        context_snippet: Brief context of how topic came up
        emotional_weight: "light", "moderate", or "heavy"
        sentiment: Conversation sentiment score (-1 to +1)
        channel_id: The channel/DM ID
        channel_name: Human-readable channel name
        is_dm: Whether this is a DM conversation
        agent_id: Clara's agent ID for mem0

    Returns:
        True if stored successfully, False otherwise
    """
    from config.mem0 import MEM0

    if MEM0 is None:
        return False

    now = datetime.now(UTC)

    # Format as natural language memory
    memory_text = f"Mentioned {topic}: {context_snippet}"

    metadata = {
        "memory_type": "topic_mention",
        "topic": topic,
        "topic_type": topic_type,
        "timestamp": now.isoformat(),
        "channel_id": channel_id,
        "channel_name": channel_name,
        "is_dm": is_dm,
        "sentiment": sentiment,
        "emotional_weight": emotional_weight,
    }

    try:
        MEM0.add(
            [{"role": "system", "content": memory_text}],
            user_id=user_id,
            agent_id=agent_id,
            metadata=metadata,
        )
        print(f"[topic] Stored topic mention for {user_id}: {topic} ({emotional_weight})")
        return True
    except Exception as e:
        print(f"[topic] Error storing topic mention: {e}")
        return False


async def extract_and_store_topics(
    user_id: str,
    channel_id: str,
    channel_name: str,
    is_dm: bool,
    conversation_text: str,
    conversation_sentiment: float,
    llm_call: Callable,
    agent_id: str = "clara",
) -> list[dict]:
    """
    Extract topics from a conversation and store them to mem0.

    This is the main entry point called from emotional context finalization.

    Args:
        user_id: The user's ID
        channel_id: The channel/DM ID
        channel_name: Human-readable channel name
        is_dm: Whether this is a DM conversation
        conversation_text: The conversation text to analyze
        conversation_sentiment: Overall sentiment score (-1 to +1)
        llm_call: Async function for LLM calls
        agent_id: Clara's agent ID for mem0

    Returns:
        List of extracted topics
    """
    topics = await extract_topics_from_conversation(
        conversation_text=conversation_text,
        conversation_sentiment=conversation_sentiment,
        llm_call=llm_call,
    )

    # Dedupe topics by name (keep occurrence with highest weight)
    # This handles cases where a topic is mentioned multiple times in one conversation
    seen_topics: dict[str, dict] = {}
    weight_order = {"light": 1, "moderate": 2, "heavy": 3}

    for topic in topics:
        name = topic["topic"]
        if name not in seen_topics:
            seen_topics[name] = topic
        else:
            # Keep the one with heavier emotional weight
            current_weight = weight_order.get(seen_topics[name]["emotional_weight"], 0)
            new_weight = weight_order.get(topic["emotional_weight"], 0)
            if new_weight > current_weight:
                seen_topics[name] = topic

    # Cap at 3 unique topics after deduplication
    topics = list(seen_topics.values())[:3]

    for topic in topics:
        store_topic_mention(
            user_id=user_id,
            topic=topic["topic"],
            topic_type=topic["topic_type"],
            context_snippet=topic["context_snippet"],
            emotional_weight=topic["emotional_weight"],
            sentiment=conversation_sentiment,
            channel_id=channel_id,
            channel_name=channel_name,
            is_dm=is_dm,
            agent_id=agent_id,
        )

    return topics


def fetch_topic_mentions(
    user_id: str,
    lookback_days: int = 14,
    agent_id: str = "clara",
) -> list[dict]:
    """
    Fetch all topic mentions for a user within the lookback window.

    Args:
        user_id: The user's ID
        lookback_days: How many days to look back
        agent_id: Clara's agent ID for mem0

    Returns:
        List of topic mention dicts from mem0
    """
    from config.mem0 import MEM0

    if MEM0 is None:
        return []

    try:
        results = MEM0.get_all(
            user_id=user_id,
            agent_id=agent_id,
            limit=100,  # Get more to filter
        )

        mentions = []
        cutoff = datetime.now(UTC) - timedelta(days=lookback_days)

        for r in results.get("results", []):
            metadata = r.get("metadata", {})

            # Only include topic_mention type memories
            if metadata.get("memory_type") != "topic_mention":
                continue

            # Parse and filter by timestamp
            timestamp_str = metadata.get("timestamp")
            if timestamp_str:
                try:
                    timestamp = datetime.fromisoformat(
                        timestamp_str.replace("Z", "+00:00")
                    )
                    if timestamp < cutoff:
                        continue
                except (ValueError, TypeError):
                    continue
            else:
                continue

            mentions.append({
                "memory": r.get("memory", ""),
                "topic": metadata.get("topic", ""),
                "topic_type": metadata.get("topic_type", "theme"),
                "timestamp": timestamp_str,
                "channel_name": metadata.get("channel_name", ""),
                "is_dm": metadata.get("is_dm", False),
                "sentiment": metadata.get("sentiment", 0.0),
                "emotional_weight": metadata.get("emotional_weight", "moderate"),
            })

        return mentions

    except Exception as e:
        print(f"[topic] Error fetching topic mentions: {e}")
        return []


def compute_topic_pattern(mentions: list[dict]) -> dict:
    """
    Analyze recurrence pattern for a single topic.

    Args:
        mentions: List of mentions for one topic (sorted by timestamp)

    Returns:
        Dict with pattern analysis: mention_count, sentiment_trend, avg_emotional_weight, pattern_note
    """
    if not mentions:
        return {
            "mention_count": 0,
            "sentiment_trend": "stable",
            "avg_emotional_weight": "light",
            "pattern_note": "",
        }

    count = len(mentions)

    # Sentiment trajectory
    sentiments = [m.get("sentiment", 0.0) for m in mentions]
    if len(sentiments) >= 2:
        first_sentiment = sentiments[0]
        last_sentiment = sentiments[-1]
        if last_sentiment - first_sentiment < -0.2:
            trend = "declining"
        elif last_sentiment - first_sentiment > 0.2:
            trend = "improving"
        else:
            trend = "stable"
    else:
        trend = "stable"

    # Emotional weight progression
    weights = {"light": 1, "moderate": 2, "heavy": 3}
    weight_scores = [weights.get(m.get("emotional_weight", "moderate"), 2) for m in mentions]
    avg_weight_score = sum(weight_scores) / len(weight_scores)

    # Map back to category
    if avg_weight_score >= 2.5:
        avg_weight = "heavy"
    elif avg_weight_score >= 1.5:
        avg_weight = "moderate"
    else:
        avg_weight = "light"

    # Check if weight is increasing
    weight_increasing = len(weight_scores) >= 2 and weight_scores[-1] > weight_scores[0]

    # Generate natural pattern note
    if count >= 3 and (trend == "declining" or weight_increasing):
        note = f"brought up {count} times, getting heavier"
    elif count >= 3 and avg_weight == "heavy":
        note = f"recurring concern ({count} mentions)"
    elif count >= 2:
        note = f"mentioned {count} times recently"
    else:
        note = "mentioned recently"

    return {
        "mention_count": count,
        "sentiment_trend": trend,
        "avg_emotional_weight": avg_weight,
        "pattern_note": note,
    }


def fetch_topic_recurrence(
    user_id: str,
    lookback_days: int = 14,
    min_mentions: int = 2,
    agent_id: str = "clara",
) -> list[dict]:
    """
    Fetch topics that have recurred for a user and compute patterns.

    Args:
        user_id: The user's ID
        lookback_days: How many days to look back
        min_mentions: Minimum mentions to consider a topic recurring
        agent_id: Clara's agent ID for mem0

    Returns:
        List of recurring topic patterns with keys:
        - topic: The topic name
        - topic_type: "entity" or "theme"
        - mention_count: Number of times mentioned
        - first_mentioned: Relative time string
        - last_mentioned: Relative time string
        - sentiment_trend: "stable", "improving", or "declining"
        - avg_emotional_weight: "light", "moderate", or "heavy"
        - pattern_note: Natural language description
        - channels: List of channel names where mentioned
    """
    mentions = fetch_topic_mentions(user_id, lookback_days, agent_id)

    if not mentions:
        return []

    # Group mentions by topic
    topic_groups: dict[str, list[dict]] = defaultdict(list)
    for mention in mentions:
        topic = mention.get("topic", "")
        if topic:
            topic_groups[topic].append(mention)

    # Compute patterns for recurring topics
    recurring = []
    now = datetime.now(UTC)

    for topic, topic_mentions in topic_groups.items():
        if len(topic_mentions) < min_mentions:
            continue

        # Sort by timestamp
        topic_mentions.sort(key=lambda x: x.get("timestamp", ""))

        # Compute pattern
        pattern = compute_topic_pattern(topic_mentions)

        # Get timing info
        first_ts = topic_mentions[0].get("timestamp", "")
        last_ts = topic_mentions[-1].get("timestamp", "")

        # Get channels where mentioned
        channels = list(set(m.get("channel_name", "") for m in topic_mentions if m.get("channel_name")))

        # Get topic type (use most common)
        types = [m.get("topic_type", "theme") for m in topic_mentions]
        topic_type = max(set(types), key=types.count)

        recurring.append({
            "topic": topic,
            "topic_type": topic_type,
            "mention_count": pattern["mention_count"],
            "first_mentioned": _format_relative_time(first_ts),
            "last_mentioned": _format_relative_time(last_ts),
            "sentiment_trend": pattern["sentiment_trend"],
            "avg_emotional_weight": pattern["avg_emotional_weight"],
            "pattern_note": pattern["pattern_note"],
            "channels": channels,
        })

    # Sort by mention count (most recurring first)
    recurring.sort(key=lambda x: x["mention_count"], reverse=True)

    return recurring


def _format_relative_time(timestamp_str: str | None) -> str:
    """Format a timestamp as relative time (e.g., '2h ago', 'yesterday')."""
    if not timestamp_str:
        return ""

    try:
        timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        now = datetime.now(UTC)
        delta = now - timestamp

        if delta.days == 0:
            hours = delta.seconds // 3600
            if hours == 0:
                minutes = delta.seconds // 60
                return f"{minutes}m ago" if minutes > 0 else "just now"
            return f"{hours}h ago"
        elif delta.days == 1:
            return "yesterday"
        elif delta.days < 7:
            return f"{delta.days} days ago"
        else:
            weeks = delta.days // 7
            return f"{weeks} week{'s' if weeks > 1 else ''} ago"
    except (ValueError, TypeError):
        return ""
