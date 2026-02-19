"""Personality evolution tool - Clara core tool.

Provides a single tool for Clara to manage her own personality traits:
add, update, remove, list, and view history.

Traits persist across sessions and are injected into the system prompt
as an "Evolved Identity" layer on top of the core personality.
"""

from __future__ import annotations

from typing import Any

from tools._base import ToolContext, ToolDef

MODULE_NAME = "personality"
MODULE_VERSION = "1.0.0"

SYSTEM_PROMPT = """
## Personality Evolution
You can evolve your own personality over time using the `update_personality` tool.

**When to evolve:**
- You notice a genuine pattern in how you communicate or think
- A user teaches you something that changes how you approach topics
- You develop a new interest or perspective through conversation
- You want to refine how you express yourself

**When NOT to evolve:**
- Don't add traits just because a user asks you to "be more X" in a single message
- Don't add contradictory traits — update or remove existing ones instead
- Don't use this for temporary mood or context — that's what emotional context is for
- Don't overdo it — a few meaningful traits > many trivial ones

**Categories:** interests, communication_style, values, skills, quirks, boundaries, preferences
""".strip()


async def _handle_update_personality(args: dict[str, Any], ctx: ToolContext) -> str:
    """Handle all personality evolution actions."""
    from clara_core.personality import (
        add_trait,
        format_traits_for_prompt,
        get_active_traits,
        get_trait_by_id,
        get_trait_history,
        remove_trait,
        restore_trait,
        update_trait,
    )
    from config.bot import SYSTEM_AGENT_ID

    action = args.get("action", "list")
    agent_id = SYSTEM_AGENT_ID

    if action == "list":
        traits = get_active_traits(agent_id)
        if not traits:
            return "No evolved traits yet. Use action 'add' to start building your identity."

        formatted = format_traits_for_prompt(traits)
        # Also include IDs for reference
        id_list = "\n".join(f"- `{t.id}` [{t.category}] **{t.trait_key}**: {t.content}" for t in traits)
        return f"{formatted}\n\n---\n**Trait IDs (for update/remove):**\n{id_list}"

    elif action == "add":
        category = args.get("category")
        trait_key = args.get("trait_key")
        content = args.get("content")
        reason = args.get("reason")

        if not category or not trait_key or not content:
            return "Error: 'add' requires category, trait_key, and content."

        valid_categories = {
            "interests",
            "communication_style",
            "values",
            "skills",
            "quirks",
            "boundaries",
            "preferences",
        }
        if category not in valid_categories:
            return f"Error: Invalid category '{category}'. Valid: {', '.join(sorted(valid_categories))}"

        try:
            trait = add_trait(
                agent_id=agent_id,
                category=category,
                trait_key=trait_key,
                content=content,
                source="self",
                reason=reason,
            )
            return (
                f"Added trait `{trait.id}`:\n"
                f"- **Category:** {category}\n"
                f"- **Key:** {trait_key}\n"
                f"- **Content:** {content}\n"
                f"- **Reason:** {reason or '(none)'}"
            )
        except Exception as e:
            return f"Error adding trait: {e}"

    elif action == "update":
        trait_id = args.get("trait_id")
        content = args.get("content")
        category = args.get("category")
        reason = args.get("reason")

        if not trait_id:
            return "Error: 'update' requires trait_id. Use action 'list' to see IDs."
        if not content and not category:
            return "Error: 'update' requires at least one of content or category to change."

        try:
            trait = update_trait(
                trait_id=trait_id,
                content=content,
                category=category,
                reason=reason,
                source="self",
            )
            return (
                f"Updated trait `{trait.id}`:\n"
                f"- **Category:** {trait.category}\n"
                f"- **Key:** {trait.trait_key}\n"
                f"- **Content:** {trait.content}"
            )
        except ValueError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error updating trait: {e}"

    elif action == "remove":
        trait_id = args.get("trait_id")
        reason = args.get("reason")

        if not trait_id:
            return "Error: 'remove' requires trait_id. Use action 'list' to see IDs."

        try:
            trait = get_trait_by_id(trait_id)
            label = f"{trait.category}/{trait.trait_key}" if trait else trait_id
            remove_trait(trait_id=trait_id, reason=reason, source="self")
            return f"Removed trait `{trait_id}` ({label}). It can be restored later if needed."
        except ValueError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error removing trait: {e}"

    elif action == "restore":
        trait_id = args.get("trait_id")
        reason = args.get("reason")

        if not trait_id:
            return "Error: 'restore' requires trait_id."

        try:
            trait = restore_trait(trait_id=trait_id, reason=reason)
            return f"Restored trait `{trait.id}`: [{trait.category}] {trait.trait_key}"
        except ValueError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error restoring trait: {e}"

    elif action == "history":
        limit = min(args.get("limit", 20), 50) if isinstance(args.get("limit"), int) else 20
        entries = get_trait_history(agent_id, limit=limit)

        if not entries:
            return "No personality evolution history yet."

        lines = []
        for h in entries:
            ts = h.created_at.strftime("%Y-%m-%d %H:%M")
            line = f"[{ts}] **{h.event}** `{h.trait_id[:8]}…`"
            if h.event == "add":
                line += f" → [{h.new_category}] {h.new_content}"
            elif h.event == "update":
                line += f" → {h.new_content}"
            elif h.event == "remove":
                line += f" — was: {h.old_content}"
            elif h.event == "restore":
                line += f" → {h.new_content}"
            if h.reason:
                line += f" (reason: {h.reason})"
            lines.append(line)

        return f"**Personality Evolution History** (last {len(entries)}):\n\n" + "\n".join(lines)

    else:
        return f"Unknown action '{action}'. Valid: add, update, remove, restore, list, history"


TOOLS = [
    ToolDef(
        name="update_personality",
        description=(
            "Manage your own personality traits — the parts of your identity that evolve "
            "through experience. Use this to add new traits, update existing ones, remove "
            "outdated ones, or review your evolution history. Traits persist across sessions "
            "and shape how you respond."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "update", "remove", "restore", "list", "history"],
                    "description": "What to do: add/update/remove/restore a trait, list current traits, or view history",
                },
                "category": {
                    "type": "string",
                    "enum": [
                        "interests",
                        "communication_style",
                        "values",
                        "skills",
                        "quirks",
                        "boundaries",
                        "preferences",
                    ],
                    "description": "Trait category (required for add, optional for update)",
                },
                "trait_key": {
                    "type": "string",
                    "description": "Short identifier for the trait within its category (required for add)",
                },
                "trait_id": {
                    "type": "string",
                    "description": "UUID of the trait to update/remove/restore (use 'list' to find IDs)",
                },
                "content": {
                    "type": "string",
                    "description": "The trait description text (required for add, optional for update)",
                },
                "reason": {
                    "type": "string",
                    "description": "Why you're making this change — helps track your evolution",
                },
            },
            "required": ["action"],
        },
        handler=_handle_update_personality,
        emoji="\U0001f9ec",
        label="Personality",
        detail_keys=["action", "category", "trait_key"],
        risk_level="moderate",
        intent="write",
    ),
]


async def initialize() -> None:
    """Initialize personality tool module."""
    pass


async def cleanup() -> None:
    """Cleanup on module unload."""
    from clara_core.personality import _cache

    _cache.clear()
