"""Intelligent tool result size capping.

Replaces blind truncation with content-aware strategies:
- JSON: Preserve structure, truncate array elements from middle (keep first 3 + last 2)
- Text: 70/20 split (70% head, 20% tail) with truncation marker
- Errors: Never truncate (detect by prefix)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Prefixes that indicate error output -- never truncate these.
_ERROR_PREFIXES = ("Error:", "Traceback ", "Exception:", "FAILED", "error:")

# Default maximum result size in characters.
DEFAULT_MAX_CHARS = 50_000


@dataclass
class CappedResult:
    """Result of capping a tool result."""

    content: str
    was_truncated: bool
    original_size: int
    strategy: str = "none"


class ToolResultGuard:
    """Intelligently caps tool result sizes with content-aware strategies."""

    def __init__(self, max_chars: int = DEFAULT_MAX_CHARS) -> None:
        self.max_chars = max_chars

    def cap(self, tool_name: str, tool_call_id: str, result: str) -> CappedResult:
        """Cap a tool result to the configured max size.

        Args:
            tool_name: Name of the tool that produced the result.
            tool_call_id: ID of the tool call.
            result: The raw tool result string.

        Returns:
            CappedResult with (possibly truncated) content and metadata.
        """
        original_size = len(result)

        # Short-circuit: already within limits.
        if original_size <= self.max_chars:
            return CappedResult(
                content=result,
                was_truncated=False,
                original_size=original_size,
            )

        # Errors are never truncated.
        if self._is_error(result):
            return CappedResult(
                content=result,
                was_truncated=False,
                original_size=original_size,
            )

        # Try JSON-aware truncation first.
        json_result = self._try_json_truncation(result)
        if json_result is not None:
            logger.debug(
                "JSON-truncated tool result for %s/%s: %d -> %d chars",
                tool_name,
                tool_call_id,
                original_size,
                len(json_result),
            )
            return CappedResult(
                content=json_result,
                was_truncated=True,
                original_size=original_size,
                strategy="json",
            )

        # Fall back to text 70/20 split.
        text_result = self._text_70_20(result)
        logger.debug(
            "Text-truncated tool result for %s/%s: %d -> %d chars",
            tool_name,
            tool_call_id,
            original_size,
            len(text_result),
        )
        return CappedResult(
            content=text_result,
            was_truncated=True,
            original_size=original_size,
            strategy="text_70_20",
        )

    def _is_error(self, result: str) -> bool:
        """Check whether the result looks like an error message."""
        return result.startswith(_ERROR_PREFIXES)

    def _text_70_20(self, result: str) -> str:
        """Truncate text with a 70% head / 20% tail split.

        The remaining 10% of max_chars budget is consumed by the marker.
        """
        head_size = int(self.max_chars * 0.70)
        tail_size = int(self.max_chars * 0.20)

        head = result[:head_size]
        tail = result[-tail_size:] if tail_size > 0 else ""
        total = len(result)

        marker = f"\n[truncated {total} chars]\n"
        return head + marker + tail

    def _try_json_truncation(self, result: str) -> str | None:
        """Attempt JSON-aware truncation.

        Parses the result as JSON, recursively trims arrays (keeping first 3
        and last 2 elements), re-serializes, and appends a truncation marker.

        Returns None if parsing fails or the trimmed output is still too large.
        """
        try:
            data = json.loads(result)
        except (json.JSONDecodeError, ValueError):
            return None

        trimmed = self._truncate_json_value(data)
        serialized = json.dumps(trimmed, ensure_ascii=False)

        marker = f"\n...[truncated: JSON arrays trimmed to 5 elements," f" original {len(result)} chars]...\n"
        output = serialized + marker

        # If still too large after trimming, give up and let text fallback
        # handle it.
        if len(output) > self.max_chars * 1.5:
            return None

        return output

    def _truncate_json_value(self, value: object) -> object:
        """Recursively truncate arrays in a parsed JSON value.

        Arrays longer than 5 elements are reduced to first 3 + last 2.
        """
        if isinstance(value, list):
            if len(value) > 5:
                kept = value[:3] + value[-2:]
            else:
                kept = value
            return [self._truncate_json_value(item) for item in kept]
        elif isinstance(value, dict):
            return {k: self._truncate_json_value(v) for k, v in value.items()}
        else:
            return value
