"""Prompt building for Clara platform.

Extracts the prompt-building and related context-fetching methods
from MemoryManager into a standalone PromptBuilder class.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from clara_core.llm.messages import AssistantMessage, Message, SystemMessage, UserMessage
from clara_core.memory_manager import _format_message_timestamp
from config.bot import PERSONALITY
from config.logging import get_logger

logger = get_logger("prompt_builder")


class PromptBuilder:
    """Builds the full LLM prompt with persona, memories, and conversation history.

    Section ordering in build_prompt:
        persona → user memories → project memories → graph relations →
        emotional context → topics → summary → history → current message

    Return contract:
        [SystemMessage(persona), SystemMessage(context), ...history..., UserMessage(current)]
        Gateway processor inserts at index 1 and index 2 after build_prompt returns.
    """

    def __init__(self, agent_id: str, llm_callable: Callable | None = None) -> None:
        self.agent_id = agent_id
        self.llm_callable = llm_callable

    # ---------- emotional context ----------

    def fetch_emotional_context(
        self,
        user_id: str,
        limit: int = 3,
        max_age_days: int = 7,
    ) -> list[dict]:
        """
        Fetch recent emotional context memories for session warmth.

        Retrieves emotional summaries from recent conversations to help
        calibrate tone at session start.

        Args:
            user_id: The user to fetch emotional context for
            limit: Maximum number of emotional context memories to return
            max_age_days: Only return context from the last N days

        Returns:
            List of emotional context dicts with keys:
            - memory: The formatted emotional summary text
            - timestamp: When the conversation ended
            - arc: Emotional arc (stable, improving, declining, volatile)
            - energy: Energy level (stressed, focused, casual, etc.)
            - channel_name: Where the conversation happened
            - is_dm: Whether it was a DM
        """

        from clara_core.memory import ROOK

        if ROOK is None:
            return []

        try:
            # Search for emotional_context memories
            results = ROOK.get_all(
                user_id=user_id,
                agent_id=self.agent_id,
                limit=limit * 2,  # Fetch extra to filter by age
            )

            emotional_contexts = []
            cutoff = datetime.now(UTC) - timedelta(days=max_age_days)

            for r in results.get("results", []):
                metadata = r.get("metadata", {})

                # Only include emotional_context type memories
                if metadata.get("memory_type") != "emotional_context":
                    continue

                # Parse and filter by timestamp
                timestamp_str = metadata.get("timestamp")
                if timestamp_str:
                    try:
                        timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                        if timestamp < cutoff:
                            continue
                    except (ValueError, TypeError):
                        timestamp = None
                else:
                    timestamp = None

                emotional_contexts.append(
                    {
                        "memory": r.get("memory", ""),
                        "timestamp": timestamp_str,
                        "arc": metadata.get("emotional_arc", "stable"),
                        "energy": metadata.get("energy_level", "neutral"),
                        "channel_name": metadata.get("channel_name", "unknown"),
                        "is_dm": metadata.get("is_dm", False),
                        "ending_sentiment": metadata.get("ending_sentiment", 0.0),
                    }
                )

            # Sort by timestamp descending (most recent first)
            emotional_contexts.sort(
                key=lambda x: x.get("timestamp") or "",
                reverse=True,
            )

            return emotional_contexts[:limit]

        except Exception as e:
            logger.error(f"Error fetching emotional context: {e}", exc_info=True)
            return []

    # ---------- prompt building ----------

    def build_prompt(
        self,
        user_mems: list[str],
        proj_mems: list[str],
        thread_summary: str | None,
        recent_msgs: list["Message"],
        user_message: str,
        emotional_context: list[dict] | None = None,
        recurring_topics: list[dict] | None = None,
        graph_relations: list[dict] | None = None,
        tools: list[dict] | None = None,
    ) -> list[Message]:
        """Build the full prompt for the LLM.

        Args:
            user_mems: List of user memories
            proj_mems: List of project memories
            thread_summary: Optional thread summary
            recent_msgs: Recent messages in the conversation
            user_message: Current user message
            emotional_context: Optional emotional context from recent sessions
            recurring_topics: Optional recurring topic patterns from fetch_topic_recurrence
            graph_relations: Optional list of entity relationships from graph memory
            tools: Optional list of tool schema dicts for WORM capability inventory

        Returns:
            List of typed Messages ready for LLM
        """
        from clara_core.security.worm_persona import build_worm_persona

        system_base = build_worm_persona(PERSONALITY, tools)

        # Build context sections
        context_parts = []

        if user_mems:
            from clara_core.security.sandboxing import wrap_untrusted

            user_block = "\n".join(f"- {wrap_untrusted(m, 'memory')}" for m in user_mems)
            context_parts.append(f"USER MEMORIES:\n{user_block}")

        if proj_mems:
            from clara_core.security.sandboxing import wrap_untrusted

            proj_block = "\n".join(f"- {wrap_untrusted(m, 'memory')}" for m in proj_mems)
            context_parts.append(f"PROJECT MEMORIES:\n{proj_block}")

        # Add graph relations (entity relationships)
        if graph_relations:
            graph_block = self._format_graph_relations(graph_relations)
            if graph_block:
                context_parts.append(f"KNOWN RELATIONSHIPS:\n{graph_block}")

        # Add emotional context from recent sessions (for tone calibration)
        if emotional_context:
            emotional_block = self._format_emotional_context(emotional_context)
            if emotional_block:
                context_parts.append(f"RECENT EMOTIONAL CONTEXT:\n{emotional_block}")

        # Add recurring topic patterns (for awareness of what keeps coming up)
        if recurring_topics:
            topic_block = self._format_topic_recurrence(recurring_topics)
            if topic_block:
                context_parts.append(f"RECURRING TOPICS:\n{topic_block}")

        if thread_summary:
            context_parts.append(f"THREAD SUMMARY:\n{thread_summary}")

        messages: list[Message] = [
            SystemMessage(content=system_base),
        ]

        if context_parts:
            messages.append(SystemMessage(content="\n\n".join(context_parts)))

        # Add recent messages (only user messages get timestamps to avoid Clara mimicking the format)
        for m in recent_msgs:
            if m.role == "user":
                timestamp = _format_message_timestamp(getattr(m, "created_at", None))
                if timestamp:
                    content = f"[{timestamp}] {m.content}"
                else:
                    content = m.content
                messages.append(UserMessage(content=content))
            else:
                messages.append(AssistantMessage(content=m.content))

        messages.append(UserMessage(content=user_message))

        # Log prompt composition summary
        components = []
        components.append(f"personality={len(system_base)} chars")
        if user_mems:
            components.append(f"user_mems={len(user_mems)}")
        if proj_mems:
            components.append(f"proj_mems={len(proj_mems)}")
        if graph_relations:
            components.append(f"graph={len(graph_relations)}")
        if emotional_context:
            components.append(f"emotional={len(emotional_context)}")
        if recurring_topics:
            components.append(f"topics={len(recurring_topics)}")
        if thread_summary:
            components.append("summary")
        if recent_msgs:
            components.append(f"history={len(recent_msgs)}")
        logger.info(f"[prompt] Built with: {', '.join(components)}")

        return messages

    # ---------- formatting helpers ----------

    def _format_emotional_context(self, emotional_context: list[dict]) -> str:
        """
        Format emotional context for inclusion in the system prompt.

        Only includes non-neutral contexts to avoid noise. Includes channel
        hints so Clara understands the source (work channel vs personal DM).

        Args:
            emotional_context: List of emotional context dicts from fetch_emotional_context

        Returns:
            Formatted string for the prompt, or empty string if nothing meaningful
        """
        if not emotional_context:
            return ""

        lines = []
        for ctx in emotional_context:
            memory = ctx.get("memory", "")
            arc = ctx.get("arc", "stable")
            energy = ctx.get("energy", "neutral")
            channel_name = ctx.get("channel_name", "")
            is_dm = ctx.get("is_dm", False)
            timestamp_str = ctx.get("timestamp", "")

            # Skip stable/neutral contexts - not worth mentioning
            if arc == "stable" and energy in ("neutral", "casual"):
                continue

            # Format channel hint
            if is_dm:
                channel_hint = "DM"
            elif channel_name:
                channel_hint = channel_name if channel_name.startswith("#") else f"#{channel_name}"
            else:
                channel_hint = "unknown"

            # Format time hint
            time_hint = self._format_relative_time(timestamp_str)

            # Build the line with channel and time hints
            if time_hint:
                lines.append(f"- [{channel_hint}, {time_hint}] {memory}")
            else:
                lines.append(f"- [{channel_hint}] {memory}")

        return "\n".join(lines) if lines else ""

    def _format_relative_time(self, timestamp_str: str | None) -> str:
        """Format a timestamp as relative time (e.g., '2h ago', 'yesterday')."""
        if not timestamp_str:
            return ""

        try:
            timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            now = datetime.now(UTC)
            delta = now - timestamp

            if delta.days > 1:
                return f"{delta.days} days ago"
            elif delta.days == 1:
                return "yesterday"
            elif delta.seconds >= 3600:
                hours = delta.seconds // 3600
                return f"{hours}h ago"
            elif delta.seconds >= 60:
                mins = delta.seconds // 60
                return f"{mins}m ago"
            else:
                return "just now"
        except (ValueError, TypeError):
            return ""

    # ---------- topic recurrence ----------

    def fetch_topic_recurrence(
        self,
        user_id: str,
        lookback_days: int = 14,
        min_mentions: int = 2,
    ) -> list[dict]:
        """
        Fetch recurring topic patterns for a user.

        Wrapper around topic_recurrence.fetch_topic_recurrence that uses
        the PromptBuilder's agent_id.

        Args:
            user_id: The user to fetch topic recurrence for
            lookback_days: How many days to look back
            min_mentions: Minimum mentions to consider a topic recurring

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
        from clara_core.topic_recurrence import fetch_topic_recurrence

        return fetch_topic_recurrence(
            user_id=user_id,
            lookback_days=lookback_days,
            min_mentions=min_mentions,
            agent_id=self.agent_id,
        )

    def _format_topic_recurrence(self, recurring_topics: list[dict]) -> str:
        """
        Format recurring topics for inclusion in the system prompt.

        Only includes significant patterns - topics that have been mentioned
        multiple times with non-trivial emotional weight or sentiment changes.

        Args:
            recurring_topics: List of topic patterns from fetch_topic_recurrence

        Returns:
            Formatted string for the prompt, or empty string if nothing meaningful
        """
        if not recurring_topics:
            return ""

        lines = []
        for topic in recurring_topics[:3]:  # Max 3 topics
            topic_name = topic.get("topic", "")
            pattern_note = topic.get("pattern_note", "")
            mention_count = topic.get("mention_count", 0)
            sentiment_trend = topic.get("sentiment_trend", "stable")
            avg_weight = topic.get("avg_emotional_weight", "light")

            # Skip topics that aren't significant enough
            if mention_count < 2:
                continue
            if sentiment_trend == "stable" and avg_weight == "light":
                continue

            # Build the line
            if pattern_note:
                lines.append(f"- {topic_name}: {pattern_note}")
            else:
                lines.append(f"- {topic_name}: mentioned {mention_count} times")

        return "\n".join(lines) if lines else ""

    # ---------- graph relations ----------

    def _format_graph_relations(self, graph_relations: list[dict]) -> str:
        """
        Format graph relations for inclusion in the system prompt.

        Converts entity relationships from the graph store into a readable format.

        Args:
            graph_relations: List of relation dicts with keys: source, relationship, destination
                (or source, relationship, target for some providers)

        Returns:
            Formatted string for the prompt, or empty string if nothing meaningful
        """
        if not graph_relations:
            return ""

        lines = []
        seen = set()  # Deduplicate

        for rel in graph_relations:
            source = rel.get("source", "")
            relationship = rel.get("relationship", "")
            # Handle both "destination" and "target" keys
            destination = rel.get("destination") or rel.get("target", "")

            if not source or not relationship or not destination:
                continue

            # Clean up relationship name (convert snake_case to readable)
            readable_rel = relationship.replace("_", " ").lower()

            # Create dedup key
            key = (source.lower(), readable_rel, destination.lower())
            if key in seen:
                continue
            seen.add(key)

            # Format: "josh → works at → anthropic"
            lines.append(f"- {source} \u2192 {readable_rel} \u2192 {destination}")

        return "\n".join(lines) if lines else ""
