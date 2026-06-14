"""Phase 1: silent, tool-enabled reflection → journal + memory."""

from __future__ import annotations

import asyncio
from typing import Any

from mypalclara.ambient import journal
from mypalclara.ambient.config import AMBIENT_JOURNAL_READBACK_DAYS
from mypalclara.ambient.prompts import REFLECTION_PROMPT
from mypalclara.ambient.silent_turn import run_silent_turn
from mypalclara.config.logging import get_logger
from mypalclara.db.models import gen_uuid

logger = get_logger("ambient.reflect")

# Read-only conversation tools available during reflection. This engine has no
# agent-callable Palace memory tools; memory consolidation happens via
# MemoryManager.add_to_memory() below (the automatic extraction pipeline).
REFLECTION_TOOL_ALLOWLIST: set[str] = {"search_chat_history", "get_chat_history"}


def _filter_tools(all_tools: list, allow: set[str]) -> list:
    out = []
    for t in all_tools:
        name = t.get("function", {}).get("name") if isinstance(t, dict) else None
        if name in allow:
            out.append(t)
    return out


def _resolve_memory_manager():
    try:
        from mypalclara.core.memory_manager import MemoryManager

        return MemoryManager.get_instance()
    except Exception as e:  # not initialized / unavailable
        logger.warning(f"reflect: MemoryManager unavailable: {e}")
        return None


async def reflect(user_id: str, *, orchestrator: Any, tool_executor: Any,
                  memory_manager: Any = None) -> str:
    recent = journal.read_recent(user_id, days=AMBIENT_JOURNAL_READBACK_DAYS)
    all_tools = await tool_executor.get_all_tools(user_id=user_id)
    tools = _filter_tools(all_tools, REFLECTION_TOOL_ALLOWLIST)

    from mypalclara.core.llm.messages import SystemMessage, UserMessage

    messages = [
        SystemMessage(content=REFLECTION_PROMPT),
        SystemMessage(content=f"## Your recent journal\n\n{recent or '(empty — a fresh start)'}"),
        UserMessage(content="Reflect now. Consolidate, notice patterns, look over recent "
                            "conversations if useful, and end with a short journal entry."),
    ]
    text = await run_silent_turn(orchestrator, messages, tools, user_id, f"ambient-reflect-{gen_uuid()}")
    if not text.strip():
        logger.info(f"reflect: empty reflection for {user_id}; nothing journaled")
        return text

    journal.append_entry(user_id, text)

    # Consolidate the reflection into Palace via the automatic extraction pipeline.
    # add_to_memory is synchronous and may make an LLM call, so run it off the loop.
    mm = memory_manager if memory_manager is not None else _resolve_memory_manager()
    if mm is not None:
        try:
            await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: mm.add_to_memory(
                    user_id=user_id,
                    user_message="[ambient reflection]",
                    assistant_reply=text,
                    is_dm=True,
                ),
            )
        except Exception as e:
            logger.warning(f"reflect: Palace write failed for {user_id}: {e}")
    return text
