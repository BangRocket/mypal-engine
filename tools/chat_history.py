"""Chat history tools.

Provides tools for searching and retrieving chat history.
Tools: search_chat_history, get_chat_history

Platform-specific: Currently only works with Discord.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ._base import ToolContext, ToolDef

MODULE_NAME = "chat_history"
MODULE_VERSION = "1.0.0"


# --- Tool Handlers ---


async def search_chat_history(args: dict[str, Any], ctx: ToolContext) -> str:
    """Search through chat history for messages matching a query."""
    query = args.get("query", "").lower()
    if not query:
        return "Error: No search query provided"

    limit = min(args.get("limit", 200), 1000)
    from_user = args.get("from_user", "").lower()

    # Get the Discord channel from context
    channel = ctx.extra.get("channel")
    if channel is None:
        return "Error: Chat history search requires a Discord channel context"

    try:
        matches = []
        count = 0

        async for msg in channel.history(limit=limit):
            count += 1
            content_lower = msg.content.lower()

            # Check if matches query
            if query not in content_lower:
                continue

            # Check user filter
            if from_user:
                author_name = msg.author.display_name.lower()
                if from_user not in author_name and from_user not in str(
                    msg.author.id
                ):
                    continue

            # Format match
            timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M")
            author = msg.author.display_name
            content = msg.content[:200] + ("..." if len(msg.content) > 200 else "")
            matches.append(f"[{timestamp}] **{author}:** {content}")

            if len(matches) >= 20:  # Cap results
                break

        if not matches:
            return f"No messages found matching '{args.get('query', '')}' in the last {count} messages."

        result = f"Found {len(matches)} matching message(s):\n\n"
        result += "\n\n".join(matches)
        return result

    except Exception as e:
        return f"Error searching chat history: {str(e)}"


async def get_chat_history(args: dict[str, Any], ctx: ToolContext) -> str:
    """Retrieve recent chat history."""
    count = min(args.get("count", 50), 200)
    before_hours = args.get("before_hours")
    user_filter = args.get("user_filter", "").lower()

    # Get the Discord channel from context
    channel = ctx.extra.get("channel")
    if channel is None:
        return "Error: Chat history retrieval requires a Discord channel context"

    try:
        # Calculate before time if specified
        before = None
        if before_hours:
            before = datetime.now(UTC) - timedelta(hours=before_hours)

        messages = []
        async for msg in channel.history(limit=count, before=before):
            # Apply user filter
            if user_filter:
                author_name = msg.author.display_name.lower()
                if user_filter not in author_name:
                    continue

            timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M")
            author = msg.author.display_name
            content = msg.content[:300] + ("..." if len(msg.content) > 300 else "")
            messages.append(f"[{timestamp}] **{author}:** {content}")

        if not messages:
            return "No messages found in the specified time range."

        # Reverse to chronological order
        messages.reverse()

        result = f"Chat history ({len(messages)} messages):\n\n"
        result += "\n\n".join(messages)
        return result

    except Exception as e:
        return f"Error retrieving chat history: {str(e)}"


# --- Tool Definitions ---

TOOLS = [
    ToolDef(
        name="search_chat_history",
        description=(
            "Search through the full chat history for messages matching a query. "
            "Use this to find past conversations, recall what was discussed, "
            "or find specific messages. Searches message content."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Text to search for in message content",
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        "Maximum messages to search through (default: 200, max: 1000)"
                    ),
                },
                "from_user": {
                    "type": "string",
                    "description": "Optional: only search messages from this username",
                },
            },
            "required": ["query"],
        },
        handler=search_chat_history,
        platforms=["discord"],  # Discord-specific
    ),
    ToolDef(
        name="get_chat_history",
        description=(
            "Retrieve recent chat history beyond what's in the current context. "
            "Use this to get a summary of past conversations or see what was "
            "discussed earlier. Returns messages in chronological order."
        ),
        parameters={
            "type": "object",
            "properties": {
                "count": {
                    "type": "integer",
                    "description": (
                        "Number of messages to retrieve (default: 50, max: 200)"
                    ),
                },
                "before_hours": {
                    "type": "number",
                    "description": (
                        "Only get messages older than this many hours ago. "
                        "Useful for looking at 'yesterday' or 'last week'."
                    ),
                },
                "user_filter": {
                    "type": "string",
                    "description": "Optional: only include messages from this username",
                },
            },
            "required": [],
        },
        handler=get_chat_history,
        platforms=["discord"],  # Discord-specific
    ),
]


# --- Lifecycle Hooks ---


async def initialize() -> None:
    """Initialize chat history module."""
    print("[chat_history] Loaded (Discord-specific)")


async def cleanup() -> None:
    """Cleanup on module unload."""
    pass
