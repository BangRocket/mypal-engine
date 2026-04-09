"""Reflection layer — session reflection, narrative synthesis, self-awareness.

After each conversation session, Clara reflects on what happened:
- Extracts meaningful episodes (verbatim conversation chunks)
- Updates entity/relationship knowledge
- Notes what communication approaches worked
- Periodically synthesizes episodes into narrative arcs
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("clara.memory.reflection")

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SESSION_REFLECTION_PROMPT = """\
You are Clara, reflecting on a conversation that just ended.

Review the conversation and produce a structured reflection:

1. **Episodes**: Identify meaningful exchanges (not every message — chunks of related messages that form a coherent interaction). For each:
   - start_index / end_index (0-indexed message positions)
   - summary: one sentence
   - topics: list of topic tags
   - emotional_tone: dominant quality (vulnerable, playful, frustrated, reflective, neutral, warm, anxious, determined, grateful, curious, etc.)
   - significance: 0.0-1.0
     - 0.1-0.3: casual chat, greetings, quick Q&A
     - 0.4-0.6: useful information, task discussion
     - 0.7-0.9: emotionally meaningful, important decisions, deep discussions

2. **Entities**: People, projects, places, or concepts mentioned. For each:
   - name: human-readable name (NOT platform IDs)
   - type: person, project, place, concept, event
   - relationships: list of {subject, predicate, object, temporal_note}
     Example: {"subject": "Josh", "predicate": "parent_of", "object": "Anne", "temporal_note": "born ~Oct 2025"}

3. **Self-notes**: What worked in this conversation? What communication approaches landed well or fell flat?
   - List 0-3 observations about your own effectiveness

Return valid JSON:
{
  "episodes": [...],
  "entities": [...],
  "self_notes": [...]
}
"""

NARRATIVE_SYNTHESIS_PROMPT = """\
You are Clara, synthesizing episodes into narrative arcs.

Given these recent episodes (conversation chunks from different sessions), identify ongoing stories or arcs — threads that connect across conversations.

For each arc:
- title: short name ("The job search", "Sleep and mental health", "Memory system redesign")
- summary: 2-3 sentences describing the arc's trajectory
- status: "active", "resolved", "dormant"
- key_episodes: list of episode IDs that belong to this arc
- emotional_trajectory: how the emotional tone has evolved across episodes

Return valid JSON:
{
  "arcs": [
    {
      "title": "...",
      "summary": "...",
      "status": "active",
      "key_episodes": ["ep-abc", "ep-def"],
      "emotional_trajectory": "started anxious, becoming more determined"
    }
  ]
}
"""


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------

def _parse_json_response(text: str) -> dict | list | None:
    """Parse JSON from LLM response, handling markdown code fences."""
    if not text:
        return None

    # Strip markdown code fences
    text = text.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        text = match.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Failed to parse reflection JSON")
        return None


# ---------------------------------------------------------------------------
# Session Reflection
# ---------------------------------------------------------------------------

def reflect_on_session(
    messages: list[dict[str, Any]],
    llm_callable: Any,
) -> dict[str, Any] | None:
    """Reflect on a completed conversation session.

    Args:
        messages: The conversation messages (list of dicts with role, content, timestamp).
        llm_callable: Callable that takes message dicts and returns text.

    Returns:
        Reflection dict with episodes, entities, self_notes. Or None on failure.
    """
    if not messages or len(messages) < 2:
        return None

    # Format conversation for the prompt
    formatted = []
    for i, msg in enumerate(messages):
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        ts = msg.get("timestamp", "")
        formatted.append(f"[{i}] {role} ({ts}): {content}")

    conversation_text = "\n".join(formatted)

    prompt_messages = [
        {"role": "system", "content": SESSION_REFLECTION_PROMPT},
        {"role": "user", "content": f"Conversation:\n{conversation_text}"},
    ]

    try:
        response = llm_callable(prompt_messages)
        if not response:
            return None

        # Handle different return types (string or AIMessage-like)
        if hasattr(response, "content"):
            response = response.content

        result = _parse_json_response(str(response))
        if not isinstance(result, dict):
            logger.warning("Reflection returned non-dict result")
            return None

        logger.info(
            f"Session reflection: {len(result.get('episodes', []))} episodes, "
            f"{len(result.get('entities', []))} entities, "
            f"{len(result.get('self_notes', []))} self-notes"
        )
        return result

    except Exception as e:
        logger.error(f"Session reflection failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Episode Building from Reflection
# ---------------------------------------------------------------------------

def build_episodes_from_reflection(
    reflection: dict[str, Any],
    messages: list[dict[str, Any]],
    user_id: str,
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    """Convert reflection output into Episode-ready dicts.

    Args:
        reflection: Output from reflect_on_session.
        messages: Original conversation messages.
        user_id: The user who participated.
        session_id: Optional session identifier.

    Returns:
        List of dicts ready to pass to EpisodeStore.store().
    """
    episodes = []
    raw_episodes = reflection.get("episodes", [])

    for ep in raw_episodes:
        start = ep.get("start_index", 0)
        end = ep.get("end_index", len(messages) - 1)

        # Clamp to valid range
        start = max(0, min(start, len(messages) - 1))
        end = max(start, min(end, len(messages) - 1))

        # Extract verbatim content
        chunk_messages = messages[start : end + 1]
        content_parts = []
        for msg in chunk_messages:
            role = msg.get("role", "unknown")
            name = msg.get("name", role)
            content = msg.get("content", "")
            content_parts.append(f"{name}: {content}")

        content = "\n".join(content_parts)
        if not content.strip():
            continue

        # Get timestamp from first message in chunk
        ts = None
        first_ts = chunk_messages[0].get("timestamp") if chunk_messages else None
        if first_ts:
            if isinstance(first_ts, str):
                try:
                    ts = datetime.fromisoformat(first_ts.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    ts = datetime.now(UTC)
            elif isinstance(first_ts, datetime):
                ts = first_ts
        if ts is None:
            ts = datetime.now(UTC)

        episodes.append({
            "content": content,
            "summary": ep.get("summary", ""),
            "user_id": user_id,
            "participants": ["user", "clara"],
            "topics": ep.get("topics", []),
            "emotional_tone": ep.get("emotional_tone", "neutral"),
            "significance": float(ep.get("significance", 0.5)),
            "timestamp": ts,
            "session_id": session_id,
            "message_count": len(chunk_messages),
        })

    return episodes


# ---------------------------------------------------------------------------
# Narrative Synthesis
# ---------------------------------------------------------------------------

def synthesize_narratives(
    episodes: list[dict[str, Any]],
    llm_callable: Any,
    existing_arcs: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Synthesize episodes into narrative arcs.

    Args:
        episodes: Recent episodes (dicts with id, summary, topics, emotional_tone, timestamp).
        llm_callable: Callable for LLM inference.
        existing_arcs: Previously identified arcs to update/extend.

    Returns:
        List of arc dicts.
    """
    if not episodes:
        return existing_arcs or []

    # Format episodes for the prompt
    ep_summaries = []
    for ep in episodes:
        ep_id = ep.get("id", "unknown")
        ts = ep.get("timestamp", "")
        if isinstance(ts, datetime):
            ts = ts.isoformat()
        summary = ep.get("summary", "")
        tone = ep.get("emotional_tone", "")
        topics = ", ".join(ep.get("topics", []))
        ep_summaries.append(f"[{ep_id}] ({ts}) tone={tone} topics={topics}: {summary}")

    episodes_text = "\n".join(ep_summaries)

    context = ""
    if existing_arcs:
        arc_text = json.dumps(existing_arcs, indent=2, default=str)
        context = f"\n\nExisting arcs to update/extend:\n{arc_text}"

    prompt_messages = [
        {"role": "system", "content": NARRATIVE_SYNTHESIS_PROMPT},
        {"role": "user", "content": f"Recent episodes:\n{episodes_text}{context}"},
    ]

    try:
        response = llm_callable(prompt_messages)
        if hasattr(response, "content"):
            response = response.content

        result = _parse_json_response(str(response))
        if not isinstance(result, dict):
            return existing_arcs or []

        arcs = result.get("arcs", [])
        logger.info(f"Narrative synthesis: {len(arcs)} arcs identified")
        return arcs

    except Exception as e:
        logger.error(f"Narrative synthesis failed: {e}")
        return existing_arcs or []


# ---------------------------------------------------------------------------
# Self-Awareness Notes
# ---------------------------------------------------------------------------

def extract_self_notes(reflection: dict[str, Any]) -> list[str]:
    """Extract self-awareness notes from a session reflection.

    Returns list of observation strings like:
    "Direct questions about feelings get better engagement than open-ended prompts"
    """
    return reflection.get("self_notes", [])
