"""MCP memory integration with mem0.

Provides:
- Storage of MCP usage patterns to mem0 for long-term learning
- Retrieval of relevant MCP context for tool suggestions
- Learning user tool preferences and successful patterns
- Cross-session MCP knowledge persistence

This module bridges the MCP metrics system with mem0's semantic memory,
enabling Clara to learn from past tool usage and provide personalized
tool suggestions based on user patterns.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def utcnow():
    """Return current UTC time (naive, for SQLite compatibility)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class MCPMemoryIntegration:
    """Integrates MCP tool usage with mem0 semantic memory.

    This class provides:
    1. Pattern storage - After successful tool calls, stores patterns that
       might be useful for future reference (e.g., "User successfully used
       github_create_issue to track bug reports")

    2. Context retrieval - Before tool calls, fetches relevant memories
       about past tool usage to inform suggestions

    3. Preference learning - Tracks which tools users prefer for different
       tasks and surfaces these preferences

    Usage:
        integration = MCPMemoryIntegration(agent_id="clara")

        # After a successful tool call
        await integration.store_tool_success(
            user_id="discord-123",
            server_name="github",
            tool_name="create_issue",
            task_description="Track bug report",
            result_summary="Created issue #42"
        )

        # Before suggesting tools
        memories = await integration.fetch_tool_context(
            user_id="discord-123",
            task_description="I need to report a bug"
        )
    """

    def __init__(self, agent_id: str = "clara") -> None:
        """Initialize MCP memory integration.

        Args:
            agent_id: Bot persona identifier for entity-scoped memory
        """
        self.agent_id = agent_id
        self._last_stored: dict[str, datetime] = {}  # Debounce storage

    async def store_tool_success(
        self,
        user_id: str,
        server_name: str,
        tool_name: str,
        task_description: str | None = None,
        result_summary: str | None = None,
        arguments: dict[str, Any] | None = None,
    ) -> bool:
        """Store a successful tool call pattern to mem0.

        Creates a semantic memory about how the user successfully used
        a tool, which can be retrieved later for suggestions.

        Args:
            user_id: User who made the call
            server_name: MCP server name
            tool_name: Tool that was used
            task_description: What the user was trying to accomplish
            result_summary: Brief summary of the result (first ~200 chars)
            arguments: Tool arguments (for pattern learning)

        Returns:
            True if stored successfully, False otherwise
        """
        try:
            from config.mem0 import MEM0

            if MEM0 is None:
                logger.debug("[MCPMemory] mem0 not available, skipping storage")
                return False

            # Debounce: Don't store the same tool call within 5 minutes
            cache_key = f"{user_id}:{server_name}:{tool_name}"
            now = utcnow()
            last = self._last_stored.get(cache_key)
            if last and (now - last).total_seconds() < 300:
                logger.debug(f"[MCPMemory] Debouncing {cache_key}")
                return False

            # Build the memory content
            content = self._build_tool_memory(
                server_name=server_name,
                tool_name=tool_name,
                task_description=task_description,
                result_summary=result_summary,
                arguments=arguments,
            )

            if not content:
                return False

            # Store to mem0 with MCP-specific metadata
            messages = [
                {
                    "role": "system",
                    "content": (
                        "The user successfully used an MCP tool. Extract useful patterns "
                        "about how they use tools for future reference. Focus on the task, "
                        "tool choice, and any notable argument patterns."
                    ),
                },
                {"role": "user", "content": content},
            ]

            result = MEM0.add(
                messages,
                user_id=user_id,
                agent_id=self.agent_id,
                metadata={
                    "memory_type": "mcp_tool_usage",
                    "server_name": server_name,
                    "tool_name": tool_name,
                    "timestamp": now.isoformat(),
                },
            )

            if isinstance(result, dict) and result.get("results"):
                self._last_stored[cache_key] = now
                logger.info(
                    f"[MCPMemory] Stored {len(result['results'])} memories "
                    f"for {server_name}__{tool_name}"
                )
                return True

            return False

        except Exception as e:
            logger.warning(f"[MCPMemory] Failed to store tool success: {e}")
            return False

    def _build_tool_memory(
        self,
        server_name: str,
        tool_name: str,
        task_description: str | None,
        result_summary: str | None,
        arguments: dict[str, Any] | None,
    ) -> str | None:
        """Build a memory string from tool call details."""
        parts = []

        # What tool was used
        parts.append(f"Used the {tool_name} tool from {server_name} MCP server.")

        # What for
        if task_description:
            parts.append(f"Task: {task_description}")

        # Notable arguments (skip trivial ones)
        if arguments:
            notable_args = self._extract_notable_arguments(arguments)
            if notable_args:
                parts.append(f"Arguments: {notable_args}")

        # What happened
        if result_summary:
            # Truncate long results
            summary = result_summary[:200]
            if len(result_summary) > 200:
                summary += "..."
            parts.append(f"Result: {summary}")

        if len(parts) < 2:
            # Not enough context to be useful
            return None

        return " ".join(parts)

    def _extract_notable_arguments(
        self, arguments: dict[str, Any], max_len: int = 100
    ) -> str | None:
        """Extract notable arguments for pattern learning.

        Filters out trivial arguments and formats the rest.
        """
        if not arguments:
            return None

        # Skip trivial arguments
        skip_keys = {"limit", "offset", "page", "per_page", "format"}
        notable = {k: v for k, v in arguments.items() if k.lower() not in skip_keys}

        if not notable:
            return None

        # Format compactly
        try:
            formatted = json.dumps(notable, ensure_ascii=False)
            if len(formatted) > max_len:
                formatted = formatted[:max_len] + "..."
            return formatted
        except (TypeError, ValueError):
            return None

    async def store_tool_preference(
        self,
        user_id: str,
        server_name: str,
        tool_name: str,
        preference_type: str,
        preference_value: str,
    ) -> bool:
        """Store a user's tool preference to mem0.

        Preferences are explicit user choices like "always use X for Y"
        or learned patterns like "prefers markdown output".

        Args:
            user_id: User ID
            server_name: MCP server name
            tool_name: Tool name (or "*" for server-wide)
            preference_type: Type of preference (e.g., "default_format", "preferred_for")
            preference_value: The preference value

        Returns:
            True if stored successfully
        """
        try:
            from config.mem0 import MEM0

            if MEM0 is None:
                return False

            content = (
                f"User preference for MCP tools: "
                f"For {server_name}/{tool_name}, {preference_type}: {preference_value}"
            )

            messages = [
                {
                    "role": "system",
                    "content": "Extract the user's tool preference for future reference.",
                },
                {"role": "user", "content": content},
            ]

            result = MEM0.add(
                messages,
                user_id=user_id,
                agent_id=self.agent_id,
                metadata={
                    "memory_type": "mcp_preference",
                    "server_name": server_name,
                    "tool_name": tool_name,
                    "preference_type": preference_type,
                    "timestamp": utcnow().isoformat(),
                },
            )

            return bool(result and result.get("results"))

        except Exception as e:
            logger.warning(f"[MCPMemory] Failed to store preference: {e}")
            return False

    async def fetch_tool_context(
        self,
        user_id: str,
        task_description: str,
        server_name: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Fetch relevant MCP memories for a task.

        Searches mem0 for past tool usage patterns that might be
        relevant to the current task.

        Args:
            user_id: User ID
            task_description: What the user is trying to accomplish
            server_name: Optional server filter
            limit: Maximum memories to return

        Returns:
            List of relevant memories with keys:
            - memory: The memory text
            - server_name: Associated server (if any)
            - tool_name: Associated tool (if any)
            - relevance_score: mem0's similarity score
        """
        try:
            from config.mem0 import MEM0

            if MEM0 is None:
                return []

            # Build search query
            query = f"MCP tool usage for: {task_description}"
            if server_name:
                query = f"{server_name} {query}"

            # Search with MCP-specific filter
            filters = {"memory_type": "mcp_tool_usage"}
            if server_name:
                filters["server_name"] = server_name

            result = MEM0.search(
                query,
                user_id=user_id,
                agent_id=self.agent_id,
                limit=limit,
            )

            memories = []
            for r in result.get("results", []):
                metadata = r.get("metadata", {})
                # Only include MCP-related memories
                if metadata.get("memory_type", "").startswith("mcp_"):
                    memories.append({
                        "memory": r.get("memory", ""),
                        "server_name": metadata.get("server_name"),
                        "tool_name": metadata.get("tool_name"),
                        "relevance_score": r.get("score", 0),
                    })

            return memories

        except Exception as e:
            logger.warning(f"[MCPMemory] Failed to fetch tool context: {e}")
            return []

    async def fetch_user_preferences(
        self,
        user_id: str,
        server_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch user's MCP tool preferences.

        Args:
            user_id: User ID
            server_name: Optional server filter

        Returns:
            List of preferences with keys:
            - memory: The preference text
            - server_name: Associated server
            - tool_name: Associated tool
            - preference_type: Type of preference
        """
        try:
            from config.mem0 import MEM0

            if MEM0 is None:
                return []

            # Get all MCP preference memories
            filters: dict[str, Any] = {"memory_type": "mcp_preference"}
            if server_name:
                filters["server_name"] = server_name

            result = MEM0.get_all(
                user_id=user_id,
                agent_id=self.agent_id,
                limit=20,
            )

            preferences = []
            for r in result.get("results", []):
                metadata = r.get("metadata", {})
                if metadata.get("memory_type") == "mcp_preference":
                    if server_name and metadata.get("server_name") != server_name:
                        continue
                    preferences.append({
                        "memory": r.get("memory", ""),
                        "server_name": metadata.get("server_name"),
                        "tool_name": metadata.get("tool_name"),
                        "preference_type": metadata.get("preference_type"),
                    })

            return preferences

        except Exception as e:
            logger.warning(f"[MCPMemory] Failed to fetch preferences: {e}")
            return []

    async def get_tool_usage_summary(
        self,
        user_id: str,
        days: int = 30,
    ) -> dict[str, Any]:
        """Get a summary of user's MCP tool usage from memory.

        Combines database metrics with semantic memories for a
        comprehensive usage summary.

        Args:
            user_id: User ID
            days: Lookback period

        Returns:
            Summary dict with keys:
            - total_calls: Total tool calls from metrics
            - top_servers: Most used servers
            - top_tools: Most used tools
            - learned_patterns: Patterns learned from mem0
            - preferences: User preferences
        """
        try:
            from clara_core.mcp.metrics import get_metrics_tracker

            # Get metrics summary
            tracker = get_metrics_tracker()
            stats = await tracker.get_user_stats(user_id, days=days)

            # Get learned patterns from mem0
            patterns = await self.fetch_tool_context(
                user_id=user_id,
                task_description="tool usage patterns and preferences",
                limit=10,
            )

            # Get explicit preferences
            preferences = await self.fetch_user_preferences(user_id)

            return {
                "total_calls": stats.get("total_calls", 0),
                "success_rate": stats.get("success_rate", 0),
                "top_servers": stats.get("servers", {}),
                "learned_patterns": patterns,
                "preferences": preferences,
            }

        except Exception as e:
            logger.warning(f"[MCPMemory] Failed to get usage summary: {e}")
            return {}


# Global singleton
_integration: MCPMemoryIntegration | None = None


def get_mcp_memory_integration(agent_id: str = "clara") -> MCPMemoryIntegration:
    """Get the global MCP memory integration instance.

    Args:
        agent_id: Bot persona identifier

    Returns:
        MCPMemoryIntegration instance
    """
    global _integration
    if _integration is None:
        _integration = MCPMemoryIntegration(agent_id=agent_id)
    return _integration


async def on_tool_success(
    user_id: str,
    server_name: str,
    tool_name: str,
    task_description: str | None = None,
    result_summary: str | None = None,
    arguments: dict[str, Any] | None = None,
) -> None:
    """Convenience function to record a successful tool call.

    Can be called from the tool executor after successful calls.
    """
    integration = get_mcp_memory_integration()
    await integration.store_tool_success(
        user_id=user_id,
        server_name=server_name,
        tool_name=tool_name,
        task_description=task_description,
        result_summary=result_summary,
        arguments=arguments,
    )
