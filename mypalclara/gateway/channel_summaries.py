"""Channel summary management for the Clara Gateway.

Handles:
- Time-based message categorization (old vs recent)
- LLM-generated summaries for old messages
- Incremental summary updates
"""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from config.logging import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger("gateway.summaries")

# Configuration
SUMMARY_AGE_MINUTES = int(os.getenv("DISCORD_SUMMARY_AGE_MINUTES", "30"))

# Thread pool for blocking operations
SUMMARY_EXECUTOR = ThreadPoolExecutor(
    max_workers=int(os.getenv("GATEWAY_SUMMARY_THREADS", "5")),
    thread_name_prefix="gateway-summary-",
)


@dataclass
class ChannelMessage:
    """A message in a channel conversation."""

    user_id: str
    username: str
    content: str
    timestamp: datetime
    is_bot: bool = False


class ChannelSummaryManager:
    """Manages channel conversation summaries.

    Splits messages into:
    - Old messages (> SUMMARY_AGE_MINUTES): Summarized via LLM
    - Recent messages (< SUMMARY_AGE_MINUTES): Kept verbatim
    """

    def __init__(self) -> None:
        """Initialize the summary manager."""
        self._initialized = False
        self._llm_callable: Any = None

    async def initialize(self) -> None:
        """Initialize with LLM callable."""
        if self._initialized:
            return

        try:
            from clara_core import make_llm

            self._llm_callable = make_llm
            self._initialized = True
            logger.info("ChannelSummaryManager initialized")
        except Exception as e:
            logger.error(f"Failed to initialize ChannelSummaryManager: {e}")
            raise

    async def get_or_update_summary(
        self,
        channel_id: str,
        messages: list[ChannelMessage],
    ) -> tuple[str, list[ChannelMessage]]:
        """Split messages into summary + recent based on time threshold.

        Args:
            channel_id: The channel identifier
            messages: List of messages (newest last)

        Returns:
            tuple: (summary_text, recent_messages_within_threshold)
        """
        from db import SessionLocal
        from db.models import ChannelSummary

        now = datetime.now(UTC)
        cutoff = now - timedelta(minutes=SUMMARY_AGE_MINUTES)

        # Split messages by age
        old_messages = [m for m in messages if m.timestamp < cutoff]
        recent_messages = [m for m in messages if m.timestamp >= cutoff]

        db = SessionLocal()
        try:
            summary_record = db.query(ChannelSummary).filter_by(channel_id=channel_id).first()

            # Check if we need to update summary
            needs_update = False
            if not summary_record:
                summary_record = ChannelSummary(channel_id=channel_id)
                db.add(summary_record)
                needs_update = bool(old_messages)
            elif old_messages:
                # Check if there are new old messages since last summary
                last_old_ts = old_messages[-1].timestamp.replace(tzinfo=None)
                if not summary_record.summary_cutoff_at or last_old_ts > summary_record.summary_cutoff_at:
                    needs_update = True

            if needs_update and old_messages:
                # Generate new summary including old summary + new old messages
                existing_summary = summary_record.summary or ""
                new_summary = await self._summarize_messages(existing_summary, old_messages)
                summary_record.summary = new_summary
                summary_record.summary_cutoff_at = old_messages[-1].timestamp.replace(tzinfo=None)
                db.commit()
                logger.debug(f"Updated channel summary for {channel_id}")

            return summary_record.summary or "", recent_messages
        finally:
            db.close()

    async def _summarize_messages(
        self,
        existing_summary: str,
        messages: list[ChannelMessage],
    ) -> str:
        """Generate a summary of messages, incorporating existing summary.

        Args:
            existing_summary: Previous summary to incorporate
            messages: New messages to summarize

        Returns:
            Updated summary text
        """
        loop = asyncio.get_event_loop()

        # Format messages for summarization
        formatted = []
        for msg in messages:
            role = "Clara" if msg.is_bot else msg.username
            content = msg.content[:500]  # truncate long messages
            formatted.append(f"{role}: {content}")

        conversation = "\n".join(formatted)

        if existing_summary:
            user_content = (
                f"Previous summary:\n{existing_summary}\n\n"
                f"New messages to incorporate:\n{conversation}\n\n"
                f"Provide an updated summary:"
            )
        else:
            user_content = f"Conversation:\n{conversation}\n\nProvide a summary:"

        from clara_core.llm.messages import SystemMessage, UserMessage

        prompt = [
            SystemMessage(
                content=(
                    "You are summarizing a channel conversation. "
                    "Create a concise summary (3-5 sentences) capturing key topics, "
                    "decisions, and context. Write in past tense. "
                    "Focus on information that would help continue the conversation."
                ),
            ),
            UserMessage(content=user_content),
        ]

        def call_llm():
            from clara_core import ModelTier

            llm = self._llm_callable(tier=ModelTier.LOW)  # Use fast model for summaries
            return llm(prompt)

        summary = await loop.run_in_executor(SUMMARY_EXECUTOR, call_llm)
        return summary

    async def clear_summary(self, channel_id: str) -> bool:
        """Clear the summary for a channel.

        Args:
            channel_id: The channel identifier

        Returns:
            True if cleared, False if not found
        """
        from db import SessionLocal
        from db.models import ChannelSummary

        db = SessionLocal()
        try:
            summary_record = db.query(ChannelSummary).filter_by(channel_id=channel_id).first()
            if summary_record:
                summary_record.summary = ""
                summary_record.summary_cutoff_at = None
                db.commit()
                logger.info(f"Cleared channel summary for {channel_id}")
                return True
            return False
        finally:
            db.close()

    async def get_summary(self, channel_id: str) -> str | None:
        """Get the current summary for a channel without updating.

        Args:
            channel_id: The channel identifier

        Returns:
            Summary text or None if not found
        """
        from db import SessionLocal
        from db.models import ChannelSummary

        db = SessionLocal()
        try:
            summary_record = db.query(ChannelSummary).filter_by(channel_id=channel_id).first()
            return summary_record.summary if summary_record else None
        finally:
            db.close()


# Global instance
_summary_manager: ChannelSummaryManager | None = None


def get_summary_manager() -> ChannelSummaryManager:
    """Get the global summary manager instance."""
    global _summary_manager
    if _summary_manager is None:
        _summary_manager = ChannelSummaryManager()
    return _summary_manager
