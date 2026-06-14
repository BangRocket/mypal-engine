"""Run the real agent tool loop with NO adapter output.

generate_with_tools is an async generator the *caller* normally forwards to an
adapter. By consuming it here with websocket=None and forwarding nothing, the
turn is silent by construction — Phase 1 has no path to message the user.
"""

from __future__ import annotations

from typing import Any

from mypalclara.config.logging import get_logger

logger = get_logger("ambient.silent_turn")


async def run_silent_turn(orchestrator: Any, messages: list, tools: list,
                          user_id: str, request_id: str) -> str:
    final_text = ""
    async for event in orchestrator.generate_with_tools(
        messages=messages,
        tools=tools,
        user_id=user_id,
        request_id=request_id,
        websocket=None,
    ):
        if event.get("type") == "complete":
            final_text = event.get("text", "") or ""
    return final_text
