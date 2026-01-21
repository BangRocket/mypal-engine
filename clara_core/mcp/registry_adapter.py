"""Registry Adapter for bridging MCP tools to Clara's ToolRegistry.

This module provides the MCPRegistryAdapter class which converts MCP tools
to Clara's ToolDef format and registers them with the tool registry.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from tools._base import ToolContext, ToolDef

if TYPE_CHECKING:
    from tools._registry import ToolRegistry

    from .client import MCPTool
    from .manager import MCPServerManager

logger = logging.getLogger(__name__)

# Module name for MCP tools in the registry
MCP_MODULE_PREFIX = "mcp"


class MCPRegistryAdapter:
    """Adapter that bridges MCP tools to Clara's ToolRegistry.

    Converts MCP tool definitions to ToolDef format and handles
    registration/unregistration when servers connect/disconnect.

    Usage:
        adapter = MCPRegistryAdapter(manager, registry)
        await adapter.sync_all()  # Register all tools from connected servers

        # When a server connects
        await adapter.register_server_tools("server_name")

        # When a server disconnects
        adapter.unregister_server_tools("server_name")
    """

    def __init__(
        self,
        manager: MCPServerManager,
        registry: ToolRegistry,
    ) -> None:
        """Initialize the adapter.

        Args:
            manager: MCPServerManager instance
            registry: Clara ToolRegistry instance
        """
        self.manager = manager
        self.registry = registry
        self._registered_tools: dict[str, list[str]] = {}  # server_name -> [tool_names]
        self._lock = asyncio.Lock()

    def _get_module_name(self, server_name: str) -> str:
        """Get the module name for a server's tools.

        Args:
            server_name: Name of the MCP server

        Returns:
            Module name like "mcp:server_name"
        """
        return f"{MCP_MODULE_PREFIX}:{server_name}"

    def _get_namespaced_tool_name(self, server_name: str, tool_name: str) -> str:
        """Get the namespaced tool name.

        Args:
            server_name: Name of the MCP server
            tool_name: Original tool name from MCP

        Returns:
            Namespaced name like "server_name__tool_name"
        """
        return f"{server_name}__{tool_name}"

    def _create_tool_handler(self, server_name: str, original_tool_name: str):
        """Create a handler function for an MCP tool.

        Args:
            server_name: Name of the MCP server
            original_tool_name: Original tool name from MCP

        Returns:
            Async handler function
        """

        async def handler(args: dict[str, Any], ctx: ToolContext) -> str:
            """Execute the MCP tool."""
            # Get the client for this server
            client = self.manager.get_client(server_name)
            if not client:
                return f"Error: MCP server '{server_name}' is not connected"

            if not client.is_connected:
                # Try to reconnect
                if not await client.reconnect():
                    return f"Error: Could not reconnect to MCP server '{server_name}'"

            # Call the tool
            return await client.call_tool(original_tool_name, args)

        return handler

    def _mcp_tool_to_tool_def(
        self,
        server_name: str,
        mcp_tool: MCPTool,
    ) -> ToolDef:
        """Convert an MCP tool to a Clara ToolDef.

        Args:
            server_name: Name of the MCP server
            mcp_tool: MCP tool definition

        Returns:
            Clara ToolDef
        """
        namespaced_name = self._get_namespaced_tool_name(server_name, mcp_tool.name)

        # Enhance the description with server info
        description = mcp_tool.description
        if not description.endswith("."):
            description += "."
        description += f" (MCP: {server_name})"

        return ToolDef(
            name=namespaced_name,
            description=description,
            parameters=mcp_tool.input_schema,
            handler=self._create_tool_handler(server_name, mcp_tool.name),
            platforms=None,  # Available on all platforms
            requires=[],  # No special requirements (MCP handles its own deps)
        )

    async def register_server_tools(self, server_name: str) -> list[str]:
        """Register all tools from an MCP server.

        Args:
            server_name: Name of the MCP server

        Returns:
            List of registered tool names
        """
        async with self._lock:
            client = self.manager.get_client(server_name)
            if not client:
                logger.warning(f"[MCP Adapter] Server '{server_name}' not found in manager")
                return []

            if not client.is_connected:
                logger.warning(f"[MCP Adapter] Server '{server_name}' not connected")
                return []

            module_name = self._get_module_name(server_name)
            registered_names = []

            for mcp_tool in client.get_tools():
                tool_def = self._mcp_tool_to_tool_def(server_name, mcp_tool)
                try:
                    self.registry.register(tool_def, source_module=module_name)
                    registered_names.append(tool_def.name)
                    logger.debug(f"[MCP Adapter] Registered tool: {tool_def.name}")
                except ValueError as e:
                    logger.warning(f"[MCP Adapter] Failed to register {tool_def.name}: {e}")

            self._registered_tools[server_name] = registered_names

            # Register a system prompt for this server
            if registered_names:
                tool_list = "\n".join(f"- {name}" for name in registered_names)
                system_prompt = f"""## MCP Server: {server_name}

The following tools are available from the MCP server '{server_name}':
{tool_list}

To use these tools, call them by their full name (e.g., {registered_names[0]})."""
                self.registry.register_system_prompt(module_name, system_prompt)

            logger.info(f"[MCP Adapter] Registered {len(registered_names)} tools from '{server_name}'")
            return registered_names

    def unregister_server_tools(self, server_name: str) -> list[str]:
        """Unregister all tools from an MCP server.

        Args:
            server_name: Name of the MCP server

        Returns:
            List of unregistered tool names
        """
        module_name = self._get_module_name(server_name)
        removed = self.registry.unregister_module(module_name)
        self.registry.unregister_system_prompt(module_name)

        if server_name in self._registered_tools:
            del self._registered_tools[server_name]

        logger.info(f"[MCP Adapter] Unregistered {len(removed)} tools from '{server_name}'")
        return removed

    async def sync_all(self) -> dict[str, list[str]]:
        """Synchronize all MCP tools with the registry.

        Registers tools from all connected servers and unregisters
        tools from disconnected servers.

        Returns:
            Dict mapping server names to registered tool names
        """
        results = {}

        # Get current server states
        all_tools = self.manager.get_all_tools()
        connected_servers = {server_name for server_name, _ in all_tools}

        # Unregister tools from servers no longer connected
        for server_name in list(self._registered_tools.keys()):
            if server_name not in connected_servers:
                self.unregister_server_tools(server_name)

        # Register tools from connected servers
        for server_name in connected_servers:
            # Check if already registered with same tools
            if server_name in self._registered_tools:
                client = self.manager.get_client(server_name)
                if client:
                    current_tools = set(client.get_tool_names())
                    registered_tools = {
                        name.split("__", 1)[1]
                        for name in self._registered_tools[server_name]
                    }
                    if current_tools == registered_tools:
                        results[server_name] = self._registered_tools[server_name]
                        continue

            # Re-register
            results[server_name] = await self.register_server_tools(server_name)

        return results

    async def refresh_server(self, server_name: str) -> list[str]:
        """Refresh tools from a specific server.

        Useful when a server reconnects or its tools may have changed.

        Args:
            server_name: Name of the MCP server

        Returns:
            List of registered tool names
        """
        # Unregister existing tools
        self.unregister_server_tools(server_name)

        # Re-register
        return await self.register_server_tools(server_name)

    def get_registered_tools(self) -> dict[str, list[str]]:
        """Get all registered MCP tools grouped by server.

        Returns:
            Dict mapping server names to tool names
        """
        return dict(self._registered_tools)

    def get_tool_count(self) -> int:
        """Get total number of registered MCP tools.

        Returns:
            Total count
        """
        return sum(len(tools) for tools in self._registered_tools.values())

    def is_mcp_tool(self, tool_name: str) -> bool:
        """Check if a tool name is an MCP tool.

        Args:
            tool_name: Tool name to check

        Returns:
            True if it's an MCP tool
        """
        if "__" not in tool_name:
            return False

        server_name = tool_name.split("__", 1)[0]
        return server_name in self._registered_tools

    def get_server_for_tool(self, tool_name: str) -> str | None:
        """Get the server name for an MCP tool.

        Args:
            tool_name: Namespaced tool name

        Returns:
            Server name or None if not an MCP tool
        """
        if "__" not in tool_name:
            return None

        server_name = tool_name.split("__", 1)[0]
        if server_name in self._registered_tools:
            return server_name
        return None


# Singleton instance
_adapter: MCPRegistryAdapter | None = None


def get_mcp_adapter() -> MCPRegistryAdapter | None:
    """Get the global MCP registry adapter instance.

    Returns:
        MCPRegistryAdapter or None if not initialized
    """
    return _adapter


def init_mcp_adapter(
    manager: MCPServerManager,
    registry: ToolRegistry,
) -> MCPRegistryAdapter:
    """Initialize the global MCP registry adapter.

    Args:
        manager: MCPServerManager instance
        registry: Clara ToolRegistry instance

    Returns:
        The initialized adapter
    """
    global _adapter
    _adapter = MCPRegistryAdapter(manager, registry)
    return _adapter
