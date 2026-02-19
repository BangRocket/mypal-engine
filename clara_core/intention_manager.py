"""Intention management for Clara platform.

Handles setting, checking, and formatting intentions/reminders.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING


class IntentionManager:
    """Manages intentions: future triggers and reminders for proactive memory surfacing."""

    def __init__(self, agent_id: str = "clara"):
        self.agent_id = agent_id

    def set_intention(
        self,
        user_id: str,
        content: str,
        trigger_conditions: dict,
        expires_at: datetime | None = None,
        source_memory_id: str | None = None,
    ) -> str:
        """Create a new intention/reminder for future surfacing.

        Args:
            user_id: User this intention is for
            content: What to remind about
            trigger_conditions: When to fire (see intentions.py)
            expires_at: Optional expiration time
            source_memory_id: Optional link to source memory

        Returns:
            The created intention ID
        """
        from clara_core.intentions import create_intention

        return create_intention(
            user_id=user_id,
            content=content,
            trigger_conditions=trigger_conditions,
            agent_id=self.agent_id,
            expires_at=expires_at,
            source_memory_id=source_memory_id,
        )

    def check_intentions(
        self,
        user_id: str,
        message: str,
        context: dict | None = None,
    ) -> list[dict]:
        """Check if any intentions should fire for the given context.

        Args:
            user_id: User to check intentions for
            message: Current user message
            context: Additional context

        Returns:
            List of fired intention dicts
        """
        from clara_core.intentions import CheckStrategy, check_intentions

        return check_intentions(
            user_id=user_id,
            message=message,
            context=context,
            strategy=CheckStrategy.TIERED,
            agent_id=self.agent_id,
        )

    def format_intentions_for_prompt(
        self,
        fired_intentions: list[dict],
    ) -> str:
        """Format fired intentions for the system prompt.

        Args:
            fired_intentions: List of fired intention dicts

        Returns:
            Formatted string for the prompt
        """
        from clara_core.intentions import format_intentions_for_prompt

        return format_intentions_for_prompt(fired_intentions)
