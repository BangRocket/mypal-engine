"""Phase 2: decide whether a reflection is worth surfacing (high bar)."""

from __future__ import annotations

import json
from typing import Any

from mypalclara.ambient.prompts import SURFACE_GATE_PROMPT
from mypalclara.config.logging import get_logger

logger = get_logger("ambient.surface_gate")

VALID_DECISIONS = {"nothing", "queue", "urgent"}
_NOTHING = {"decision": "nothing", "content": "", "reason": ""}


def _parse(raw: str) -> dict:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
    except Exception:
        return dict(_NOTHING, reason="unparseable")
    if not isinstance(data, dict):
        return dict(_NOTHING, reason="not-an-object")
    decision = str(data.get("decision", "nothing")).lower()
    if decision not in VALID_DECISIONS:
        decision = "nothing"
    return {
        "decision": decision,
        "content": str(data.get("content", "")).strip(),
        "reason": str(data.get("reason", "")).strip(),
    }


async def surface_gate(user_id: str, reflection: str, *, gate_llm: Any) -> dict:
    from mypalclara.core.llm.messages import SystemMessage, UserMessage

    messages = [
        SystemMessage(content=SURFACE_GATE_PROMPT),
        UserMessage(content=f"Reflection:\n{reflection}\n\nReturn the JSON decision."),
    ]
    try:
        raw = await gate_llm(messages)
    except Exception as e:
        logger.error(f"surface_gate LLM failed for {user_id}: {e}")
        return dict(_NOTHING, reason="llm-error")
    return _parse(str(raw))
