"""Phase 1: silent, tool-enabled reflection → journal + memory."""

from __future__ import annotations

from typing import Any

from mypalclara.ambient import journal
from mypalclara.ambient.config import AMBIENT_JOURNAL_READBACK_DAYS
from mypalclara.ambient.prompts import REFLECTION_PROMPT
from mypalclara.ambient.silent_turn import run_silent_turn
from mypalclara.config.logging import get_logger
from mypalclara.db.models import gen_uuid

logger = get_logger("ambient.reflect")

# Memory-only allowlist. Real tool names discovered from:
#   mypalclara/core/plugins/normalization.py:34-35  — canonical names + aliases
#   mypalclara/core/plugins/policies.py:79-83       — group:memory membership
# Canonical names: "search_memories" (read) and "add_memory" (write).
REFLECTION_TOOL_ALLOWLIST: set[str] = {"search_memories", "add_memory"}


def _filter_tools(all_tools: list, allow: set[str]) -> list:
    out = []
    for t in all_tools:
        name = t.get("function", {}).get("name") if isinstance(t, dict) else None
        if name in allow:
            out.append(t)
    return out


async def reflect(user_id: str, *, orchestrator: Any, tool_executor: Any) -> str:
    recent = journal.read_recent(user_id, days=AMBIENT_JOURNAL_READBACK_DAYS)
    all_tools = await tool_executor.get_all_tools(user_id=user_id)
    tools = _filter_tools(all_tools, REFLECTION_TOOL_ALLOWLIST)

    from mypalclara.core.llm.messages import SystemMessage, UserMessage

    messages = [
        SystemMessage(content=REFLECTION_PROMPT),
        SystemMessage(content=f"## Your recent journal\n\n{recent or '(empty — a fresh start)'}"),
        UserMessage(content="Reflect now. Consolidate, notice patterns, update memory as useful, "
                            "and end with a short journal entry."),
    ]
    text = await run_silent_turn(orchestrator, messages, tools, user_id, f"ambient-reflect-{gen_uuid()}")
    if text.strip():
        journal.append_entry(user_id, text)
    else:
        logger.info(f"reflect: empty reflection for {user_id}; nothing journaled")
    return text
