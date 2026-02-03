"""Tool name normalization and aliasing.

This module provides consistent tool naming with support for aliases,
allowing multiple names to refer to the same tool.

Inspired by OpenClaw's normalization system.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# Default tool aliases mapping canonical names to alternative names
DEFAULT_ALIASES: dict[str, list[str]] = {
    # Shell/bash execution
    "execute_shell": ["bash", "shell", "exec", "run_shell"],
    # Python execution
    "execute_python": ["python", "py", "run_python"],
    # Code execution (generic)
    "run_code": ["code", "execute", "eval"],
    # File operations
    "read_file": ["cat", "read", "get_file"],
    "write_file": ["save", "put_file"],
    "list_directory": ["ls", "dir", "list_files"],
    # Web operations
    "web_search": ["search", "google", "query"],
    "fetch_url": ["curl", "wget", "http_get", "get_url"],
    # Memory operations
    "search_memories": ["memory_search", "recall", "remember"],
    "add_memory": ["memorize", "store_memory", "save_memory"],
    # Local file storage
    "save_to_local": ["local_save", "store_local"],
    "read_local_file": ["local_read", "get_local"],
    "list_local_files": ["local_list", "ls_local"],
    "delete_local_file": ["local_delete", "rm_local"],
}


class ToolNameNormalizer:
    """Normalizes tool names and resolves aliases.

    Provides consistent tool naming by:
    - Converting to lowercase
    - Replacing hyphens with underscores
    - Resolving aliases to canonical names
    """

    def __init__(self) -> None:
        """Initialize the normalizer."""
        # Canonical name -> list of aliases
        self._aliases: dict[str, list[str]] = {}
        # Alias -> canonical name (reverse lookup)
        self._alias_to_canonical: dict[str, str] = {}

        # Load default aliases
        for canonical, aliases in DEFAULT_ALIASES.items():
            self.register_aliases(canonical, aliases)

    def normalize(self, name: str) -> str:
        """Normalize a tool name.

        Applies normalization rules:
        - Lowercase
        - Replace hyphens with underscores
        - Strip whitespace

        Args:
            name: Tool name to normalize

        Returns:
            Normalized name
        """
        # Strip whitespace
        name = name.strip()
        # Convert to lowercase
        name = name.lower()
        # Replace hyphens with underscores
        name = name.replace("-", "_")
        # Replace multiple underscores with single
        name = re.sub(r"_+", "_", name)
        # Strip leading/trailing underscores
        name = name.strip("_")
        return name

    def register_aliases(
        self,
        canonical_name: str,
        aliases: list[str],
        overwrite: bool = False,
    ) -> None:
        """Register aliases for a canonical tool name.

        Args:
            canonical_name: The canonical (primary) tool name
            aliases: List of alternative names for the tool
            overwrite: If True, overwrite existing aliases
        """
        # Normalize the canonical name
        canonical_name = self.normalize(canonical_name)

        # Initialize or extend aliases list
        if canonical_name not in self._aliases or overwrite:
            self._aliases[canonical_name] = []

        for alias in aliases:
            normalized_alias = self.normalize(alias)

            # Skip if alias already maps to a different canonical name
            existing = self._alias_to_canonical.get(normalized_alias)
            if existing and existing != canonical_name and not overwrite:
                logger.warning(
                    f"Alias '{alias}' already maps to '{existing}', "
                    f"skipping registration for '{canonical_name}'"
                )
                continue

            if normalized_alias not in self._aliases[canonical_name]:
                self._aliases[canonical_name].append(normalized_alias)

            self._alias_to_canonical[normalized_alias] = canonical_name

        # Also map canonical name to itself for easy lookup
        self._alias_to_canonical[canonical_name] = canonical_name

        logger.debug(
            f"Registered aliases for '{canonical_name}': "
            f"{self._aliases[canonical_name]}"
        )

    def unregister_aliases(self, canonical_name: str) -> bool:
        """Remove all aliases for a canonical name.

        Args:
            canonical_name: The canonical name to unregister

        Returns:
            True if aliases were removed
        """
        canonical_name = self.normalize(canonical_name)

        if canonical_name not in self._aliases:
            return False

        # Remove reverse lookups
        for alias in self._aliases[canonical_name]:
            if alias in self._alias_to_canonical:
                del self._alias_to_canonical[alias]

        # Remove from canonical lookup
        if canonical_name in self._alias_to_canonical:
            del self._alias_to_canonical[canonical_name]

        del self._aliases[canonical_name]
        return True

    def get_canonical(self, name: str) -> str:
        """Get the canonical name for a tool.

        If the name is an alias, returns the canonical name.
        If the name is unknown, returns the normalized name.

        Args:
            name: Tool name (possibly an alias)

        Returns:
            Canonical tool name
        """
        normalized = self.normalize(name)
        return self._alias_to_canonical.get(normalized, normalized)

    def get_aliases(self, canonical_name: str) -> list[str]:
        """Get all aliases for a canonical tool name.

        Args:
            canonical_name: The canonical name

        Returns:
            List of aliases (not including the canonical name itself)
        """
        canonical_name = self.normalize(canonical_name)
        aliases = self._aliases.get(canonical_name, [])
        return [a for a in aliases if a != canonical_name]

    def is_alias(self, name: str) -> bool:
        """Check if a name is a registered alias.

        Args:
            name: Name to check

        Returns:
            True if the name is a registered alias
        """
        normalized = self.normalize(name)
        return normalized in self._alias_to_canonical

    def resolve(self, name: str) -> str:
        """Resolve a tool name to its canonical form.

        Combines normalization and alias resolution.

        Args:
            name: Tool name to resolve

        Returns:
            Canonical, normalized tool name
        """
        return self.get_canonical(name)

    def match(self, name1: str, name2: str) -> bool:
        """Check if two tool names refer to the same tool.

        Args:
            name1: First tool name
            name2: Second tool name

        Returns:
            True if both names refer to the same canonical tool
        """
        return self.get_canonical(name1) == self.get_canonical(name2)

    def get_all_aliases(self) -> dict[str, list[str]]:
        """Get all registered aliases.

        Returns:
            Dict mapping canonical names to alias lists
        """
        return dict(self._aliases)


# Global normalizer singleton
_normalizer: ToolNameNormalizer | None = None


def get_normalizer() -> ToolNameNormalizer:
    """Get the global normalizer singleton.

    Returns:
        ToolNameNormalizer instance
    """
    global _normalizer
    if _normalizer is None:
        _normalizer = ToolNameNormalizer()
    return _normalizer


def reset_normalizer() -> None:
    """Reset the global normalizer. Useful for testing."""
    global _normalizer
    _normalizer = None


def normalize_tool_name(name: str) -> str:
    """Convenience function to normalize a tool name.

    Args:
        name: Tool name to normalize

    Returns:
        Normalized name
    """
    return get_normalizer().normalize(name)


def resolve_tool_name(name: str) -> str:
    """Convenience function to resolve a tool name to its canonical form.

    Args:
        name: Tool name to resolve

    Returns:
        Canonical tool name
    """
    return get_normalizer().resolve(name)
