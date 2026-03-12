"""MCP Feature Flags - Control MCP vs legacy routing per tool.

Feature flags allow incremental migration from legacy tools to MCP.
Each tool can be individually enabled for MCP routing, with automatic
fallback to legacy if MCP is unavailable.

Environment variables:
- MCP_ENABLED: Master switch (default: false)
- MCP_TOOLS_ENABLED: Comma-separated list of tool names to route via MCP
- MCP_TOOLS_DISABLED: Comma-separated list of tool names to force legacy
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class MCPFeatureFlags:
    """Feature flags for MCP tool routing.

    Attributes:
        enabled: Master switch for MCP routing
        tool_allowlist: Tools explicitly enabled for MCP (if set, only these use MCP)
        tool_blocklist: Tools explicitly disabled for MCP (always use legacy)
        fallback_enabled: Whether to fall back to legacy on MCP failure
    """

    enabled: bool = False
    tool_allowlist: set[str] = field(default_factory=set)
    tool_blocklist: set[str] = field(default_factory=set)
    fallback_enabled: bool = True

    @classmethod
    def from_env(cls) -> "MCPFeatureFlags":
        """Load feature flags from environment variables."""
        enabled = os.getenv("MCP_ENABLED", "false").lower() == "true"

        allowlist_str = os.getenv("MCP_TOOLS_ENABLED", "")
        allowlist = {t.strip() for t in allowlist_str.split(",") if t.strip()}

        blocklist_str = os.getenv("MCP_TOOLS_DISABLED", "")
        blocklist = {t.strip() for t in blocklist_str.split(",") if t.strip()}

        fallback = os.getenv("MCP_FALLBACK_ENABLED", "true").lower() == "true"

        return cls(
            enabled=enabled,
            tool_allowlist=allowlist,
            tool_blocklist=blocklist,
            fallback_enabled=fallback,
        )

    def should_use_mcp(self, tool_name: str) -> bool:
        """Determine if a tool should be routed via MCP.

        Decision logic:
        1. If MCP disabled globally, return False
        2. If tool in blocklist, return False
        3. If allowlist is set and tool not in it, return False
        4. Otherwise, return True (try MCP)

        Args:
            tool_name: Name of the tool

        Returns:
            True if tool should try MCP routing
        """
        if not self.enabled:
            return False

        if tool_name in self.tool_blocklist:
            return False

        if self.tool_allowlist and tool_name not in self.tool_allowlist:
            return False

        return True

    def enable_tool(self, tool_name: str) -> None:
        """Enable MCP routing for a specific tool."""
        self.tool_allowlist.add(tool_name)
        self.tool_blocklist.discard(tool_name)

    def disable_tool(self, tool_name: str) -> None:
        """Disable MCP routing for a specific tool (force legacy)."""
        self.tool_blocklist.add(tool_name)
        self.tool_allowlist.discard(tool_name)


# Global singleton
_flags: MCPFeatureFlags | None = None


def get_mcp_flags() -> MCPFeatureFlags:
    """Get the global MCP feature flags instance."""
    global _flags
    if _flags is None:
        _flags = MCPFeatureFlags.from_env()
    return _flags


def reset_mcp_flags() -> None:
    """Reset feature flags (for testing)."""
    global _flags
    _flags = None
