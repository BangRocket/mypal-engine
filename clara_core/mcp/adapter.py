"""MCP Tool Adapter - Routes tool calls to MCP or legacy registry.

This adapter is the key to zero-downtime migration. It:
1. Checks feature flags to decide MCP vs legacy routing
2. Tries MCP first if enabled, falls back to legacy on failure
3. Aggregates tools from both MCP servers and legacy registry
4. Provides consistent interface regardless of backend

Usage:
    adapter = MCPToolAdapter(mcp_manager, legacy_registry)
    tools = adapter.get_tools(platform="discord")
    result = await adapter.execute("tool_name", {"arg": "value"}, context)
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Any

from .client_manager import MCPClientManager
from .feature_flags import MCPFeatureFlags, get_mcp_flags

if TYPE_CHECKING:
    from tools._base import ToolContext
    from tools._registry import ToolRegistry

# Use stderr for logging
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("[%(name)s] %(levelname)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


class MCPToolAdapter:
    """Adapter that routes tool calls to MCP or legacy registry.

    The adapter aggregates tools from both systems and routes calls
    based on feature flags. This enables incremental migration without
    downtime.

    Attributes:
        mcp_manager: MCP client manager for MCP server connections
        legacy_registry: Legacy ToolRegistry for fallback
        flags: Feature flags controlling routing behavior
    """

    def __init__(
        self,
        mcp_manager: MCPClientManager,
        legacy_registry: "ToolRegistry",
        flags: MCPFeatureFlags | None = None,
    ) -> None:
        """Initialize the adapter.

        Args:
            mcp_manager: MCP client manager instance
            legacy_registry: Legacy tool registry instance
            flags: Feature flags (uses global if not provided)
        """
        self.mcp_manager = mcp_manager
        self.legacy_registry = legacy_registry
        self.flags = flags or get_mcp_flags()

        # Track which tools are available via MCP
        self._mcp_tools: set[str] = set()
        self._refresh_mcp_tools()

    def _refresh_mcp_tools(self) -> None:
        """Refresh the set of tools available via MCP."""
        mcp_tools = self.mcp_manager.get_all_tools()
        self._mcp_tools = {tool["name"] for tool in mcp_tools}

    def get_tools(
        self,
        platform: str | None = None,
        capabilities: dict[str, bool] | None = None,
        format: str = "openai",
    ) -> list[dict[str, Any]]:
        """Get aggregated tools from MCP and legacy registries.

        MCP tools take precedence over legacy tools with the same name.
        Tools are deduplicated based on name.

        Args:
            platform: Filter to tools available on this platform
            capabilities: Dict of capability -> available
            format: Output format - "openai", "mcp", or "claude"

        Returns:
            List of tool definitions in requested format
        """
        self._refresh_mcp_tools()
        tools_by_name: dict[str, dict[str, Any]] = {}

        # First, add legacy tools
        legacy_tools = self.legacy_registry.get_tools(
            platform=platform,
            capabilities=capabilities,
            format=format,
        )
        for tool in legacy_tools:
            name = tool.get("function", {}).get("name") if format == "openai" else tool.get("name")
            if name:
                tools_by_name[name] = tool

        # Then, add MCP tools (overwrite legacy if same name and MCP enabled)
        if self.flags.enabled:
            mcp_tools = self.mcp_manager.get_all_tools()
            for mcp_tool in mcp_tools:
                name = mcp_tool["name"]
                # Check if this tool should use MCP
                if self.flags.should_use_mcp(name):
                    # Convert to requested format
                    if format == "openai":
                        tools_by_name[name] = {
                            "type": "function",
                            "function": {
                                "name": name,
                                "description": mcp_tool.get("description", ""),
                                "parameters": mcp_tool.get("inputSchema", {}),
                            },
                        }
                    elif format == "claude":
                        tools_by_name[name] = {
                            "name": name,
                            "description": mcp_tool.get("description", ""),
                            "input_schema": mcp_tool.get("inputSchema", {}),
                        }
                    else:  # mcp format
                        tools_by_name[name] = mcp_tool

        return list(tools_by_name.values())

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: "ToolContext",
    ) -> str:
        """Execute a tool, routing to MCP or legacy as appropriate.

        Routing logic:
        1. If MCP disabled or tool blocklisted, use legacy
        2. If tool available in MCP and enabled, try MCP
        3. On MCP failure and fallback enabled, try legacy
        4. Return result or error message

        Args:
            tool_name: Name of the tool to execute
            arguments: Arguments to pass to the tool
            context: Execution context

        Returns:
            Tool execution result as string
        """
        # Decide routing
        use_mcp = (
            self.flags.enabled
            and self.flags.should_use_mcp(tool_name)
            and tool_name in self._mcp_tools
        )

        if use_mcp:
            try:
                logger.debug(f"Routing {tool_name} to MCP")
                result = await self.mcp_manager.call_tool(tool_name, arguments)

                # Convert MCP result to string
                if hasattr(result, "content"):
                    # Handle MCP CallToolResult
                    contents = []
                    for item in result.content:
                        if hasattr(item, "text"):
                            contents.append(item.text)
                        elif hasattr(item, "data"):
                            contents.append(str(item.data))
                        else:
                            contents.append(str(item))
                    return "\n".join(contents)
                return str(result)

            except Exception as e:
                logger.warning(f"MCP call failed for {tool_name}: {e}")
                if self.flags.fallback_enabled:
                    logger.info(f"Falling back to legacy for {tool_name}")
                else:
                    return f"Error: MCP call failed for {tool_name}: {e}"

        # Use legacy registry
        logger.debug(f"Routing {tool_name} to legacy registry")
        return await self.legacy_registry.execute(tool_name, arguments, context)

    def is_mcp_tool(self, tool_name: str) -> bool:
        """Check if a tool is available via MCP.

        Args:
            tool_name: Name of the tool

        Returns:
            True if tool is available via MCP
        """
        return tool_name in self._mcp_tools

    def get_routing_info(self, tool_name: str) -> dict[str, Any]:
        """Get routing information for a tool (for debugging).

        Args:
            tool_name: Name of the tool

        Returns:
            Dict with routing details
        """
        in_mcp = tool_name in self._mcp_tools
        in_legacy = tool_name in self.legacy_registry
        should_mcp = self.flags.should_use_mcp(tool_name)

        return {
            "tool_name": tool_name,
            "available_in_mcp": in_mcp,
            "available_in_legacy": in_legacy,
            "mcp_enabled_for_tool": should_mcp,
            "would_use": "mcp" if (in_mcp and should_mcp) else "legacy",
        }
