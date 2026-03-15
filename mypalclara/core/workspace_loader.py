"""Workspace file loader with budget management.

Loads user-editable markdown files from workspace directories.
Files are loaded in a defined order with per-file and total character
budget enforcement using 70/20 truncation (70% head + 20% tail).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Load order for each mode
FULL_FILES = [
    "SOUL.md",
    "IDENTITY.md",
    "USER.md",
    "AGENTS.md",
    "TOOLS.md",
    "MEMORY.md",
]
MINIMAL_FILES = [
    "SOUL.md",
    "IDENTITY.md",
    "USER.md",
    "AGENTS.md",
]

# Regex for structured fields in IDENTITY.md: - **Key:** Value
_FIELD_PATTERN = re.compile(r"-\s*\*\*(\w+):\*\*\s*(.+)")


@dataclass
class WorkspaceFile:
    """A loaded workspace file with optional metadata."""

    filename: str
    content: str
    was_truncated: bool = False
    structured_fields: dict[str, str] | None = None


class WorkspaceLoader:
    """Loads workspace markdown files with budget management.

    Args:
        per_file_max: Maximum characters per individual file (default 20,000).
        total_max: Maximum total characters across all files (default 150,000).
    """

    def __init__(
        self,
        per_file_max: int = 20_000,
        total_max: int = 150_000,
    ) -> None:
        self.per_file_max = per_file_max
        self.total_max = total_max

    def load(
        self,
        directory: str | Path,
        mode: str = "full",
    ) -> list[WorkspaceFile]:
        """Load workspace files from the given directory.

        Args:
            directory: Path to the workspace directory.
            mode: "full" loads all files, "minimal" loads core subset only.

        Returns:
            List of WorkspaceFile objects for files that exist and fit budget.
        """
        directory = Path(directory)
        file_list = FULL_FILES if mode == "full" else MINIMAL_FILES

        results: list[WorkspaceFile] = []
        total_chars = 0

        for filename in file_list:
            filepath = directory / filename

            if not filepath.is_file():
                continue

            # Check if total budget is already exhausted
            if total_chars >= self.total_max:
                logger.debug(
                    "Total budget exhausted (%d/%d), skipping %s",
                    total_chars,
                    self.total_max,
                    filename,
                )
                break

            try:
                raw_content = filepath.read_text(encoding="utf-8")
            except OSError:
                logger.warning("Failed to read workspace file: %s", filepath)
                continue

            # Apply per-file truncation
            content, was_truncated = self._truncate(raw_content, self.per_file_max, filename)

            # Apply total budget truncation
            remaining_budget = self.total_max - total_chars
            if len(content) > remaining_budget:
                content, was_truncated = self._truncate(content, remaining_budget, filename)
                # After truncating for total budget, mark budget as exhausted
                # so remaining files are skipped

            total_chars += len(content)

            # Parse structured fields for IDENTITY.md
            structured_fields = None
            if filename == "IDENTITY.md":
                structured_fields = self._parse_identity_fields(raw_content)

            results.append(
                WorkspaceFile(
                    filename=filename,
                    content=content,
                    was_truncated=was_truncated,
                    structured_fields=structured_fields,
                )
            )

        return results

    def _truncate(
        self,
        content: str,
        max_chars: int,
        filename: str,
    ) -> tuple[str, bool]:
        """Apply 70/20 truncation if content exceeds max_chars.

        Keeps 70% from the head and 20% from the tail, with a marker
        indicating truncation in between.

        Returns:
            Tuple of (possibly truncated content, was_truncated flag).
        """
        if len(content) <= max_chars:
            return content, False

        total_original = len(content)
        head_size = int(max_chars * 0.70)
        tail_size = int(max_chars * 0.20)

        marker = f"\n[...truncated, see {filename} " f"for full content ({total_original} chars)...]\n"

        head = content[:head_size]
        tail = content[-tail_size:] if tail_size > 0 else ""
        truncated = head + marker + tail

        return truncated, True

    @staticmethod
    def _parse_identity_fields(content: str) -> dict[str, str]:
        """Parse structured fields from IDENTITY.md content.

        Looks for lines matching: - **Key:** Value
        Returns a dict with lowercase keys mapped to stripped values.
        """
        fields: dict[str, str] = {}
        for match in _FIELD_PATTERN.finditer(content):
            key = match.group(1).lower()
            value = match.group(2).strip()
            fields[key] = value
        return fields
