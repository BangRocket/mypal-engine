"""Progressive context compaction via multi-stage summarization.

When conversation history exceeds the token budget, this module compacts
older messages into summaries rather than simply dropping them. This
preserves conversational context while staying within token limits.

Pipeline:
  1. Check if compaction is needed (budget threshold)
  2. Separate system messages, history, and current message
  3. Keep recent 40% of history untouched
  4. Chunk older messages and summarize each chunk
  5. Merge summaries into a single SystemMessage
  6. Reassemble: system + summary + recent + current

Falls back to drop-oldest if summarization fails.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from mypalclara.core.llm.messages import SystemMessage
from mypalclara.core.token_counter import count_message_tokens, count_tokens

logger = logging.getLogger(__name__)

SAFETY_MARGIN = 0.80
RECENT_KEEP_RATIO = 0.40


@dataclass
class CompactionResult:
    """Result of a context compaction attempt.

    Attributes:
        messages: The (possibly compacted) message list.
        was_compacted: Whether compaction was actually performed.
        tokens_saved: Number of tokens saved by compaction.
        summary_tokens: Number of tokens in the generated summary.
    """

    messages: list
    was_compacted: bool
    tokens_saved: int = 0
    summary_tokens: int = 0


class ContextCompactor:
    """Compacts conversation context via progressive summarization.

    Args:
        budget_ratio: Fraction of budget_tokens to use as the effective limit.
        llm_callable: Optional async callable for LLM-based summarization.
            If None, a simple text extraction fallback is used.
    """

    def __init__(self, budget_ratio: float = 0.6, llm_callable=None):
        self._budget_ratio = budget_ratio
        self._llm_callable = llm_callable

    async def compact_if_needed(self, messages: list, budget_tokens: int) -> CompactionResult:
        """Compact messages if they exceed the token budget.

        Args:
            messages: The full message list to potentially compact.
            budget_tokens: The total token budget for the context window.

        Returns:
            CompactionResult with the (possibly compacted) messages.
        """
        current_tokens = count_message_tokens(messages)
        threshold = int(budget_tokens * self._budget_ratio * SAFETY_MARGIN)

        if current_tokens <= threshold:
            return CompactionResult(messages=messages, was_compacted=False)

        try:
            return await self._compact_with_summarization(messages, budget_tokens)
        except Exception as e:
            logger.warning("Summarization failed, falling back to drop-oldest: %s", e)
            return self._compact_drop_oldest(messages, budget_tokens)

    async def _compact_with_summarization(self, messages: list, budget_tokens: int) -> CompactionResult:
        """Compact by summarizing older message chunks.

        Separates system messages, splits history into compactable and
        recent portions, summarizes the compactable portion, and
        reassembles.
        """
        # Separate system messages from non-system history
        system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
        history = [m for m in messages if not isinstance(m, SystemMessage)]

        if not history:
            return CompactionResult(messages=messages, was_compacted=False)

        # Current message is always the last non-system message
        current_msg = history[-1]
        history = history[:-1]

        # Keep recent portion untouched
        keep_count = max(2, int(len(history) * RECENT_KEEP_RATIO))
        if keep_count < len(history):
            to_compact = history[:-keep_count]
            to_keep = history[-keep_count:]
        else:
            to_compact = []
            to_keep = history

        if not to_compact:
            return CompactionResult(messages=messages, was_compacted=False)

        # Sanitize untrusted content before sending to summarizer
        sanitized = self._sanitize_for_compaction(to_compact)

        # Split into chunks of ~5 messages each
        chunk_size = max(5, len(sanitized) // 3)
        chunks = [sanitized[i : i + chunk_size] for i in range(0, len(sanitized), chunk_size)]

        # Summarize each chunk
        summaries = []
        for chunk in chunks:
            summary = await self._summarize_chunk(chunk)
            summaries.append(summary)

        # Merge summaries
        merged = "\n\n".join(summaries)
        summary_msg = SystemMessage(content=f"## Conversation Summary\n{merged}")

        result_messages = system_msgs + [summary_msg] + to_keep + [current_msg]

        original_tokens = count_message_tokens(messages)
        new_tokens = count_message_tokens(result_messages)

        return CompactionResult(
            messages=result_messages,
            was_compacted=True,
            tokens_saved=original_tokens - new_tokens,
            summary_tokens=count_tokens(merged),
        )

    async def _summarize_chunk(self, messages: list) -> str:
        """Summarize a chunk of messages.

        If an LLM callable is provided, uses it for summarization.
        Otherwise falls back to simple text extraction.

        Args:
            messages: A chunk of messages to summarize.

        Returns:
            A text summary of the chunk.
        """
        if self._llm_callable is not None:
            return await self._llm_callable(messages)

        # Fallback: extract key content without LLM
        lines = []
        for msg in messages:
            content = getattr(msg, "content", None) or ""
            if content:
                lines.append(content[:200])
        return "Previous context: " + " | ".join(lines)

    def _sanitize_for_compaction(self, messages: list) -> list:
        """Replace untrusted content before sending to summarizer.

        Any message containing ``<untrusted_`` tags has its content
        replaced with a placeholder to prevent leaking sensitive tool
        results into summaries.

        Args:
            messages: Messages to sanitize.

        Returns:
            New list with sanitized copies where needed.
        """
        sanitized = []
        for msg in messages:
            content = getattr(msg, "content", None) or ""
            if "<untrusted_" in content:
                msg_type = type(msg)
                sanitized.append(msg_type(content="[tool result omitted for compaction]"))
            else:
                sanitized.append(msg)
        return sanitized

    def _compact_drop_oldest(self, messages: list, budget_tokens: int) -> CompactionResult:
        """Fallback: drop oldest non-system messages until under budget.

        Args:
            messages: The full message list.
            budget_tokens: The total token budget.

        Returns:
            CompactionResult with oldest messages removed.
        """
        system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
        history = [m for m in messages if not isinstance(m, SystemMessage)]

        if not history:
            return CompactionResult(messages=messages, was_compacted=False)

        current_msg = history[-1]
        history = history[:-1]

        target = int(budget_tokens * self._budget_ratio * SAFETY_MARGIN)
        while history and count_message_tokens(system_msgs + history + [current_msg]) > target:
            history.pop(0)

        original_tokens = count_message_tokens(messages)
        result = system_msgs + history + [current_msg]
        new_tokens = count_message_tokens(result)

        return CompactionResult(
            messages=result,
            was_compacted=True,
            tokens_saved=original_tokens - new_tokens,
        )
