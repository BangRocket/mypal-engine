"""Memory visibility tools -- manage public/private tagging on memories.

Gives Clara the ability to change a memory's visibility between
'public' (visible in group channels) and 'private' (DMs only),
and to list all public memories for a user.
"""

from __future__ import annotations

from typing import Any

from mypalclara.tools._base import ToolContext, ToolDef

MODULE_NAME = "memory_visibility"
MODULE_VERSION = "1.0.0"

SYSTEM_PROMPT = """
## Memory Privacy
You can manage the visibility of user memories:
- **private** (default): Only visible in DMs and personal context
- **public**: Visible in group channels so teammates can benefit

Use memory_set_visibility to change a memory's visibility.
Use memory_list_public to show what's currently public.
Never make a memory public without the user's explicit consent.
""".strip()


def _get_memory():
    """Get the Rook memory instance."""
    from mypalclara.core.memory import ROOK

    return ROOK


async def _handle_set_visibility(args: dict[str, Any], ctx: ToolContext) -> str:
    """Set a memory's visibility to 'public' or 'private'."""
    visibility = args.get("visibility", "")
    if visibility not in ("public", "private"):
        return f"Error: visibility must be 'public' or 'private', got '{visibility}'."

    memory_id = args.get("memory_id", "")
    if not memory_id:
        return "Error: memory_id is required."

    try:
        mem = _get_memory()
        mem.update_memory_visibility(memory_id, visibility)
        return f"Memory {memory_id} is now **{visibility}**."
    except Exception as e:
        return f"Error updating visibility: {e}"


async def _handle_list_public(args: dict[str, Any], ctx: ToolContext) -> str:
    """List all public memories for the current user."""
    try:
        mem = _get_memory()
        result = mem.search(
            "public memories",
            user_id=ctx.user_id,
            filters={"visibility": "public"},
            limit=50,
        )
        memories = result.get("results", [])
        if not memories:
            return "No public memories found for this user."

        lines = [f"**Public memories ({len(memories)}):**"]
        for m in memories:
            lines.append(f"- `{m.id}`: {m.memory}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing public memories: {e}"


TOOLS = [
    ToolDef(
        name="memory_set_visibility",
        description=(
            "Set a memory's visibility to 'public' (visible in group channels) "
            "or 'private' (DMs only). Always confirm with the user before making "
            "a memory public."
        ),
        parameters={
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "string",
                    "description": "ID of the memory to update",
                },
                "visibility": {
                    "type": "string",
                    "enum": ["public", "private"],
                    "description": "New visibility level",
                },
            },
            "required": ["memory_id", "visibility"],
        },
        handler=_handle_set_visibility,
        emoji="\U0001f512",
        label="Set Memory Visibility",
        detail_keys=["memory_id", "visibility"],
        risk_level="moderate",
        intent="write",
    ),
    ToolDef(
        name="memory_list_public",
        description="List all of the current user's public memories (visible in group channels).",
        parameters={"type": "object", "properties": {}},
        handler=_handle_list_public,
        emoji="\U0001f4cb",
        label="List Public Memories",
        detail_keys=[],
        risk_level="safe",
        intent="read",
    ),
]


async def initialize() -> None:
    """Initialize memory visibility tool module."""
    pass


async def cleanup() -> None:
    """Cleanup on module unload."""
    pass
