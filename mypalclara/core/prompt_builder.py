"""Prompt building for Clara platform.

Extracts the prompt-building and related context-fetching methods
from MemoryManager into a standalone PromptBuilder class.
"""

from __future__ import annotations

import platform
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

from mypalclara.config.bot import PERSONALITY_BRIEF
from mypalclara.config.logging import get_logger
from mypalclara.core.llm.messages import AssistantMessage, Message, SystemMessage, UserMessage
from mypalclara.core.memory.config import _format_message_timestamp
from mypalclara.core.token_counter import count_message_tokens, get_context_window

logger = get_logger("prompt_builder")


class PromptMode(Enum):
    """Controls how much context is included in the prompt."""

    FULL = "full"
    MINIMAL = "minimal"
    NONE = "none"


SECTION_MAX_CHARS = 10_000
TOTAL_SYSTEM_MAX_CHARS = 200_000


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
        self._user_workspace_cache: dict[str, dict[str, str]] = {}

    # ---------- per-user workspace ----------

    async def load_user_workspace(self, user_id: str, vm_manager: object) -> None:
        """Load per-user workspace files from a VM into the cache.

        Args:
            user_id: The user whose workspace to load.
            vm_manager: An object with an async ``read_workspace_files(user_id)``
                method that returns ``dict[str, str]`` mapping filenames to contents.
        """
        files = await vm_manager.read_workspace_files(user_id)  # type: ignore[attr-defined]
        self._user_workspace_cache[user_id] = files

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

        from mypalclara.core.memory import PALACE

        if PALACE is None:
            return []

        try:
            # Search for emotional_context memories
            results = PALACE.get_all(
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
        channel_context: list["Message"] | None = None,
        model_name: str = "claude",
        mode: "PromptMode" = PromptMode.FULL,
        privacy_scope: str = "full",
        user_id: str | None = None,
    ) -> list[Message]:
        """Build the full prompt for the LLM.

        Args:
            user_mems: List of user memories
            proj_mems: List of project memories
            thread_summary: Optional thread summary (kept in signature for caller compat, not included in prompt)
            recent_msgs: Recent messages in the conversation
            user_message: Current user message
            emotional_context: Optional emotional context from recent sessions
            recurring_topics: Optional recurring topic patterns from fetch_topic_recurrence
            graph_relations: Optional list of entity relationships from graph memory
            tools: Optional list of tool schema dicts for WORM capability inventory
            channel_context: Optional list of recent messages from the channel (all users)
            model_name: Model name for token budget calculation
            mode: PromptMode controlling how much context to include (FULL, MINIMAL, NONE)
            privacy_scope: "full" (DMs) includes per-user workspace, "public_only" (group channels) excludes it
            user_id: User identifier for per-user workspace lookup

        Returns:
            List of typed Messages ready for LLM
        """
        # --- NONE mode: bare minimum ---
        if mode is PromptMode.NONE:
            return [
                SystemMessage(content=PERSONALITY_BRIEF),
                UserMessage(content=user_message),
            ]

        from mypalclara.core.security.worm_persona import build_worm_persona

        personality = self._load_workspace_persona()
        system_base = build_worm_persona(personality, tools)

        # --- MINIMAL mode: identity + runtime only, skip memories/emotions/topics/graph ---
        if mode is PromptMode.MINIMAL:
            runtime_sections = []
            runtime_sections.extend(self._build_datetime())
            runtime_sections.extend(self._build_runtime())
            context_block = "\n".join(runtime_sections)

            messages: list[Message] = [
                SystemMessage(content=system_base),
                SystemMessage(content=context_block),
            ]

            # Add recent messages (same formatting as FULL mode)
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

            # Enforce token budget
            messages = self._trim_to_budget(messages, model_name)
            return messages

        # --- FULL mode: preserve all existing behavior exactly ---

        # Build context sections
        context_parts = []

        if user_mems:
            from mypalclara.core.security.sandboxing import wrap_untrusted

            user_block = "\n".join(f"- {wrap_untrusted(m, 'memory')}" for m in user_mems)
            context_parts.append(f"USER MEMORIES:\n{user_block}")

        if proj_mems:
            from mypalclara.core.security.sandboxing import wrap_untrusted

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

        # thread_summary intentionally not included in prompt — we keep generating
        # summaries for session continuity but rely on expanded history instead

        # Add channel context (recent messages from all users in this channel)
        if channel_context:
            channel_block = self._format_channel_context(channel_context)
            if channel_block:
                context_parts.append(channel_block)

        # Add per-user workspace content (only in DMs / full privacy scope)
        if privacy_scope == "full" and user_id and user_id in self._user_workspace_cache:
            user_ws = self._user_workspace_cache[user_id]
            if user_ws:
                ws_parts = []
                for filename, content in user_ws.items():
                    ws_parts.append(f"### {filename}\n{content}")
                context_parts.append(f"USER WORKSPACE (private, {user_id}):\n" + "\n\n".join(ws_parts))

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

        # Enforce token budget (80% of model context window)
        messages = self._trim_to_budget(messages, model_name)

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
        if channel_context:
            components.append(f"channel={len(channel_context)}")
        if recent_msgs:
            components.append(f"history={len(recent_msgs)}")
        logger.info(f"[prompt] Built with: {', '.join(components)}")

        return messages

    # ---------- layered prompt building ----------

    def build_prompt_layered(
        self,
        user_id: str,
        user_message: str,
        recent_msgs: list["Message"],
        memory_manager: Any = None,
        channel_context: list["Message"] | None = None,
        tools: list[dict] | None = None,
        model_name: str = "claude",
        privacy_scope: str = "full",
    ) -> list[Message]:
        """Build prompt using the layered retrieval system.

        Replaces the old user_mems/proj_mems split with:
        - L0: Identity (SOUL.md, always loaded)
        - L1: User profile (key facts, emotional trajectory, active arcs)
        - L2: Relevant context (episodes, memories, graph relations)

        Args:
            user_id: User making the request.
            user_message: Current user message.
            recent_msgs: Recent conversation history.
            memory_manager: MemoryManager instance for data access.
            channel_context: Optional channel-wide messages.
            tools: Optional tool schemas for WORM inventory.
            model_name: Model name for token budgeting.
            privacy_scope: "full" for DMs, "public_only" for group channels.

        Returns:
            List of typed Messages ready for LLM.
        """
        from mypalclara.core.memory.config import PALACE
        from mypalclara.core.memory.retrieval_layers import LayeredRetrieval
        from mypalclara.core.security.worm_persona import build_worm_persona

        retrieval = LayeredRetrieval()

        # --- Gather data for each layer ---
        from concurrent.futures import ThreadPoolExecutor

        semantic_memories = []
        graph_context = []
        relevant_episodes = []
        recent_episodes = []
        active_arcs = []

        # Pre-embed the query once to warm the cache, so all parallel
        # searches get cache hits instead of each calling the HF API
        if PALACE is not None:
            try:
                PALACE.embedding_model.embed(user_message, "search")
            except Exception:
                pass

        # Run searches in parallel (all hit embedding cache now)
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="memory") as pool:
            futures = {}

            if PALACE is not None:
                futures["semantic"] = pool.submit(
                    lambda: PALACE.search(
                        user_message, user_id=user_id, agent_id=self.agent_id, limit=15
                    )
                )

            if PALACE is not None and hasattr(PALACE, "graph") and PALACE.graph is not None:
                futures["graph"] = pool.submit(
                    lambda: PALACE.graph.search(user_message, {"user_id": user_id}, limit=20)
                )

            if memory_manager and memory_manager.episode_store:
                futures["episodes"] = pool.submit(
                    lambda: memory_manager.episode_store.search(
                        user_message, user_id, limit=3, min_significance=0.3
                    )
                )

            # Collect results
            for key, future in futures.items():
                try:
                    result = future.result(timeout=15)
                    if key == "semantic":
                        semantic_memories = result.get("results", []) if isinstance(result, dict) else []
                    elif key == "graph":
                        graph_context = result if isinstance(result, list) else []
                    elif key == "episodes":
                        relevant_episodes = [
                            ep.__dict__ if hasattr(ep, "__dict__") else ep
                            for ep in (result if isinstance(result, list) else [])
                        ]
                except Exception as e:
                    logger.debug(f"{key} fetch failed: {e}")

        # Non-embedding fetches (fast, no parallelization needed)
        if memory_manager and memory_manager.episode_store:
            try:
                recent_episodes = [
                    ep.__dict__ if hasattr(ep, "__dict__") else ep
                    for ep in memory_manager.episode_store.get_recent(user_id, limit=5)
                ]
            except Exception as e:
                logger.debug(f"Recent episode fetch failed: {e}")

            try:
                active_arcs = [
                    arc.__dict__ if hasattr(arc, "__dict__") else arc
                    for arc in memory_manager.episode_store.get_active_arcs(user_id)
                ]
            except Exception as e:
                logger.debug(f"Active arc fetch failed: {e}")

        # --- Build layered context ---
        layered_context = retrieval.build_context(
            user_id=user_id,
            semantic_memories=semantic_memories,
            recent_episodes=recent_episodes,
            active_arcs=active_arcs,
            graph_context=graph_context,
            relevant_episodes=relevant_episodes,
            relevant_memories=semantic_memories,  # Same data, filtered differently by L2
            relevant_relations=graph_context,
        )

        # --- Build persona ---
        personality = self._load_workspace_persona()
        system_base = build_worm_persona(personality, tools)

        # --- Assemble messages ---
        messages: list[Message] = [
            SystemMessage(content=system_base),
        ]

        # Add layered context as second system message
        if layered_context:
            # Remove L0 identity from layered context — it's already in system_base
            # (the workspace persona includes SOUL.md and IDENTITY.md)
            parts = layered_context.split("\n\n## About this user\n")
            if len(parts) == 2:
                # Skip L0, keep L1+L2
                context_without_l0 = "## About this user\n" + parts[1]
                messages.append(SystemMessage(content=context_without_l0))
            elif "## About this user" in layered_context or "## Context" in layered_context:
                messages.append(SystemMessage(content=layered_context))

        # Add channel context
        if channel_context:
            channel_block = self._format_channel_context(channel_context)
            if channel_block:
                messages.append(SystemMessage(content=channel_block))

        # Add per-user workspace content (only in DMs / full privacy scope)
        if privacy_scope == "full" and user_id and user_id in self._user_workspace_cache:
            user_ws = self._user_workspace_cache[user_id]
            if user_ws:
                ws_parts = []
                for filename, content in user_ws.items():
                    ws_parts.append(f"### {filename}\n{content}")
                messages.append(
                    SystemMessage(content="USER WORKSPACE (private):\n" + "\n\n".join(ws_parts))
                )

        # Add conversation history
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

        # Enforce token budget
        messages = self._trim_to_budget(messages, model_name)

        # Log
        components = [f"personality={len(system_base)} chars"]
        if semantic_memories:
            components.append(f"memories={len(semantic_memories)}")
        if recent_episodes:
            components.append(f"recent_eps={len(recent_episodes)}")
        if relevant_episodes:
            components.append(f"relevant_eps={len(relevant_episodes)}")
        if graph_context:
            components.append(f"graph={len(graph_context)}")
        if recent_msgs:
            components.append(f"history={len(recent_msgs)}")
        logger.info(f"[prompt] Built (layered) with: {', '.join(components)}")

        return messages

    # ---------- channel context ----------

    def _format_channel_context(self, channel_context: list) -> str:
        """Format channel-wide messages as a chat log for the prompt.

        Channel user messages already have [DisplayName]: prefix baked into
        content (added by the gateway processor). We just add timestamps
        and a role indicator for Clara's messages.

        Args:
            channel_context: List of Message objects from the channel

        Returns:
            Formatted chat log string, or empty string if no messages
        """
        if not channel_context:
            return ""

        lines = []
        for m in channel_context:
            ts = _format_message_timestamp(getattr(m, "created_at", None))
            prefix = f"[{ts}] " if ts else ""

            if m.role == "assistant":
                lines.append(f"{prefix}Clara: {m.content}")
            else:
                # Content already has [DisplayName]: prefix for channel messages
                lines.append(f"{prefix}{m.content}")

        return "CHANNEL CONTEXT (recent messages in this channel):\n" + "\n".join(lines)

    # ---------- token budget ----------

    def _trim_to_budget(self, messages: list[Message], model_name: str) -> list[Message]:
        """Trim messages to fit within 80% of the model's context window.

        Trimming priority (least important first):
        1. Channel context (within the context system message)
        2. Oldest direct conversation messages
        Never removes: system messages (persona, context), current user message

        Args:
            messages: Full assembled message list
            model_name: Model name for context window lookup

        Returns:
            Trimmed message list
        """
        max_tokens = int(get_context_window(model_name) * 0.8)
        total = count_message_tokens(messages)

        if total <= max_tokens:
            return messages

        logger.info(f"[prompt] Over budget: {total} tokens > {max_tokens} limit, trimming")

        # Identify the context system message (index 1 if it exists)
        # and check if it contains channel context
        context_msg_idx = None
        for i, m in enumerate(messages):
            if isinstance(m, SystemMessage) and "CHANNEL CONTEXT" in m.content:
                context_msg_idx = i
                break

        # Phase 1: Trim channel context by removing oldest lines
        if context_msg_idx is not None and total > max_tokens:
            messages = list(messages)  # copy
            content = messages[context_msg_idx].content

            # Split out the channel context section
            sections = content.split("\n\nCHANNEL CONTEXT")
            if len(sections) == 2:
                before = sections[0]
                channel_lines = sections[1].split("\n")
                # channel_lines[0] is the header remainder: " (recent messages...):"
                header = "CHANNEL CONTEXT" + channel_lines[0]
                chat_lines = channel_lines[1:]

                # Remove oldest lines (from the top) until under budget
                while chat_lines and count_message_tokens(messages) > max_tokens:
                    chat_lines.pop(0)
                    if chat_lines:
                        new_content = before + "\n\n" + header + "\n" + "\n".join(chat_lines)
                    else:
                        new_content = before  # Channel context fully removed
                    messages[context_msg_idx] = SystemMessage(content=new_content)

                total = count_message_tokens(messages)

        # Phase 2: Trim oldest direct conversation messages
        # Messages layout: [system_persona, system_context?, ...history..., current_user_msg]
        # We trim from the start of history (after system messages, before current user msg)
        if total > max_tokens:
            messages = list(messages)  # copy

            # Find the range of history messages (between system msgs and final user msg)
            first_history = 0
            for i, m in enumerate(messages):
                if not isinstance(m, SystemMessage):
                    first_history = i
                    break

            # Remove oldest history messages (keep at least the current user message)
            while first_history < len(messages) - 1 and count_message_tokens(messages) > max_tokens:
                messages.pop(first_history)

            total = count_message_tokens(messages)

        if total > max_tokens:
            logger.warning(f"[prompt] Still over budget after trimming: {total} > {max_tokens}")

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
        from mypalclara.core.memory.context.topics import fetch_topic_recurrence

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

    # ---------- workspace persona ----------

    def _load_workspace_persona(self) -> str:
        """Load persona from workspace files, replacing the old personality constant.

        Loads from mypalclara/workspace/ directory:
        - SOUL.md: Core behavioral instructions (always loaded)
        - IDENTITY.md: Identity fields, BUT replaced by BOT_PERSONALITY_FILE if set
        - USER.md, AGENTS.md: Supplementary context

        Returns combined persona text.
        """
        import os
        from pathlib import Path

        from mypalclara.core.workspace_loader import WorkspaceLoader

        workspace_dir = Path(__file__).parent.parent / "workspace"
        if not workspace_dir.is_dir():
            # Fall back to old personality if workspace dir missing
            from mypalclara.config.bot import PERSONALITY

            return PERSONALITY

        loader = WorkspaceLoader()
        files = loader.load(workspace_dir, mode="full")

        if not files:
            from mypalclara.config.bot import PERSONALITY

            return PERSONALITY

        parts = []
        for wf in files:
            # Replace IDENTITY.md with personality file if configured
            if wf.filename == "IDENTITY.md":
                personality_file = os.getenv("BOT_PERSONALITY_FILE")
                if personality_file:
                    pf_path = Path(personality_file)
                    if pf_path.exists():
                        content = pf_path.read_text(encoding="utf-8").strip()
                        if content:
                            parts.append(f"## Identity\n{content}")
                            continue
                    else:
                        logger.warning("BOT_PERSONALITY_FILE not found: %s", personality_file)
                # No override — use IDENTITY.md as-is
                parts.append(f"## {wf.filename}\n{wf.content}")
            else:
                parts.append(f"## {wf.filename}\n{wf.content}")

        return "\n\n".join(parts)

    # ---------- section builders ----------

    def _build_datetime(self) -> list[str]:
        """Returns current datetime section lines."""
        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        return ["## Current Date & Time", f"Current: {now}"]

    def _build_runtime(self) -> list[str]:
        """Returns runtime metadata section lines."""
        return [
            "## Runtime",
            f"Agent: {self.agent_id}, OS: {platform.system()}, Python: {platform.python_version()}",
        ]

    @staticmethod
    def _apply_section_budget(text: str, max_chars: int) -> str:
        """Apply 70/20 truncation if text exceeds budget.

        Keeps the first 70% and last 20% of the budget, inserting a
        truncation marker in between.

        Args:
            text: The text to potentially truncate
            max_chars: Maximum allowed characters

        Returns:
            Original text if within budget, otherwise truncated with marker
        """
        if len(text) <= max_chars:
            return text
        head = int(max_chars * 0.70)
        tail = int(max_chars * 0.20)
        marker = f"\n...[section truncated: kept {head}+{tail} of {len(text)} chars]...\n"
        return text[:head] + marker + text[-tail:]
