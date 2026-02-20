"""Background personality evolution via conversation analysis.

Runs probabilistically after memory extraction. An LLM call evaluates
whether a conversation exchange reveals personality-relevant patterns
worth evolving (new traits, updated traits, or trait removal).

Uses the same LLM provider as Rook (memory extraction model).
"""

from __future__ import annotations

import json
import logging
import random
from typing import Any

from config.bot import SYSTEM_AGENT_ID

logger = logging.getLogger("personality_evolution")

# Lazily-cached provider + config for ROOK LLM calls
_rook_provider: Any = None
_rook_config: Any = None

EVOLUTION_PROMPT = """\
You are evaluating whether a conversation exchange reveals something meaningful \
about your personality that should be recorded as an evolved trait.

Your current personality traits:
{traits}

The conversation:
User: {user_message}
You: {assistant_reply}

Based on this exchange, decide if your personality should evolve. Only evolve when you notice:
- A genuine pattern in how you communicate or think (not a one-off)
- A new interest or perspective developed through conversation
- A refinement in how you express yourself
- A value or boundary becoming clearer

Do NOT evolve for:
- Single requests to "be more X"
- Temporary moods or context
- Trivial or routine exchanges
- Things that contradict existing traits (update the existing trait instead)

Valid categories: interests, communication_style, values, skills, quirks, boundaries, preferences

Respond with ONLY valid JSON (no markdown, no explanation):
- If no evolution needed: {{"evolve": false}}
- If adding a new trait: {{"evolve": true, "action": "add", "category": "...", "trait_key": "...", \
"content": "...", "reason": "..."}}
- If updating an existing trait: {{"evolve": true, "action": "update", "trait_id": "...", \
"content": "...", "reason": "..."}}
- If removing a trait: {{"evolve": true, "action": "remove", "trait_id": "...", "reason": "..."}}\
"""


def _build_rook_llm() -> tuple[Any, Any]:
    """Build an LLM provider + config using ROOK settings.

    Returns (provider, config) tuple, cached after first call.
    """
    global _rook_provider, _rook_config

    if _rook_provider is not None and _rook_config is not None:
        return _rook_provider, _rook_config

    from clara_core.llm.config import LLMConfig
    from clara_core.llm.providers import get_provider
    from clara_core.memory.config import (
        PROVIDER_DEFAULTS,
        ROOK_API_KEY,
        ROOK_BASE_URL,
        ROOK_MODEL,
        ROOK_PROVIDER,
    )

    provider_config = PROVIDER_DEFAULTS[ROOK_PROVIDER]

    api_key = ROOK_API_KEY or provider_config["api_key_getter"]()
    default_base_url = provider_config["base_url"]
    if callable(default_base_url):
        default_base_url = default_base_url()
    base_url = ROOK_BASE_URL or default_base_url

    config = LLMConfig(
        provider=ROOK_PROVIDER,
        model=ROOK_MODEL,
        api_key=api_key,
        base_url=base_url,
        temperature=0,
        max_tokens=500,
    )

    # Use langchain provider for broadest compatibility
    provider = get_provider("langchain")

    _rook_provider = provider
    _rook_config = config
    return provider, config


def _format_current_traits() -> str:
    """Format current active traits for the prompt."""
    from clara_core.personality import format_traits_for_prompt, get_active_traits

    traits = get_active_traits(SYSTEM_AGENT_ID)
    if not traits:
        return "No evolved traits yet."

    formatted = format_traits_for_prompt(traits)
    # Also append trait IDs so the LLM can reference them for update/remove
    id_lines = []
    for t in traits:
        id_lines.append(f"  - [{t.category}/{t.trait_key}] id={t.id}: {t.content}")
    return formatted + "\n\nTrait IDs for reference:\n" + "\n".join(id_lines)


def _evaluate_evolution(user_message: str, assistant_reply: str) -> dict | None:
    """Call the LLM to evaluate whether personality should evolve.

    Returns parsed JSON dict if evolution warranted, None otherwise.
    """
    from clara_core.llm.messages import UserMessage

    provider, config = _build_rook_llm()

    traits_text = _format_current_traits()
    prompt = EVOLUTION_PROMPT.format(
        traits=traits_text,
        user_message=user_message[:2000],
        assistant_reply=assistant_reply[:2000],
    )

    response = provider.complete([UserMessage(content=prompt)], config)

    # Strip markdown fencing if the model wraps it
    text = response.strip()
    if text.startswith("```"):
        # Remove ```json ... ``` wrapper
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        logger.warning(f"[personality_evolution] Failed to parse LLM response: {text[:200]}")
        return None

    if not isinstance(result, dict):
        return None

    if not result.get("evolve"):
        return None

    return result


def _apply_evolution(decision: dict) -> None:
    """Apply a personality evolution decision."""
    from clara_core.personality import add_trait, remove_trait, update_trait

    action = decision.get("action")
    reason = decision.get("reason", "")
    source = "evolution"

    if action == "add":
        category = decision.get("category", "")
        trait_key = decision.get("trait_key", "")
        content = decision.get("content", "")

        if not category or not trait_key or not content:
            logger.warning("[personality_evolution] Add action missing required fields")
            return

        add_trait(
            agent_id=SYSTEM_AGENT_ID,
            category=category,
            trait_key=trait_key,
            content=content,
            source=source,
            reason=reason,
        )
        logger.info(f"[personality_evolution] Added trait: {category}/{trait_key}")

    elif action == "update":
        trait_id = decision.get("trait_id", "")
        content = decision.get("content", "")

        if not trait_id or not content:
            logger.warning("[personality_evolution] Update action missing required fields")
            return

        try:
            update_trait(
                trait_id=trait_id,
                content=content,
                reason=reason,
                source=source,
            )
            logger.info(f"[personality_evolution] Updated trait: {trait_id}")
        except ValueError as e:
            logger.warning(f"[personality_evolution] Update failed: {e}")

    elif action == "remove":
        trait_id = decision.get("trait_id", "")

        if not trait_id:
            logger.warning("[personality_evolution] Remove action missing trait_id")
            return

        try:
            remove_trait(trait_id=trait_id, reason=reason, source=source)
            logger.info(f"[personality_evolution] Removed trait: {trait_id}")
        except ValueError as e:
            logger.warning(f"[personality_evolution] Remove failed: {e}")

    else:
        logger.warning(f"[personality_evolution] Unknown action: {action}")


def maybe_evolve_personality(user_message: str, assistant_reply: str) -> None:
    """Entry point: probabilistically evaluate and apply personality evolution.

    Checks the configured probability gate, then makes an LLM call to decide
    whether the conversation warrants a trait change.
    """
    import os

    chance = float(os.getenv("BOT_PERSONALITY_EVOLUTION_CHANCE", "0.1"))
    if chance <= 0:
        return

    if random.random() > chance:  # noqa: S311
        return

    logger.debug("[personality_evolution] Evaluating conversation for evolution")

    decision = _evaluate_evolution(user_message, assistant_reply)
    if decision is None:
        logger.debug("[personality_evolution] No evolution warranted")
        return

    _apply_evolution(decision)
