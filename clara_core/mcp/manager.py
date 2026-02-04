"""MCP Server Manager - Unified management for local and remote MCP servers.

This module provides the MCPServerManager class which unifies management of:
- Local MCP servers (stdio transport, run as subprocesses)
- Remote MCP servers (HTTP transport, connect to external endpoints)

The manager provides a single interface for:
- Initializing all servers
- Starting/stopping servers
- Calling tools across all servers
- Getting aggregated tool lists
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, ClassVar

from .local_server import LocalServerManager, MCPTool
from .models import (
    LocalServerConfig,
    RemoteServerConfig,
    ServerType,
    find_server_config,
    list_all_server_configs,
    load_local_server_config,
    load_remote_server_config,
    save_local_server_config,
    save_remote_server_config,
    utcnow_iso,
)
from .remote_server import RemoteServerManager

logger = logging.getLogger(__name__)


class MCPServerManager:
    """Unified manager for all MCP server connections.

    This singleton class manages both local and remote MCP servers,
    providing a unified interface for tool discovery and execution.

    Usage:
        manager = MCPServerManager.get_instance()
        await manager.initialize()  # Load and connect all enabled servers

        # Get all tools from all servers
        tools = manager.get_all_tools()

        # Call a tool (manager routes to correct server)
        result = await manager.call_tool("server_name__tool_name", {})

        # Shutdown
        await manager.shutdown()
    """

    _instance: ClassVar[MCPServerManager | None] = None

    def __init__(self) -> None:
        """Initialize the manager. Use get_instance() instead."""
        self._local = LocalServerManager()
        self._remote = RemoteServerManager()
        self._initialized = False
        self._lock = asyncio.Lock()

    @classmethod
    def get_instance(cls) -> MCPServerManager:
        """Get or create the singleton manager instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton instance. Useful for testing."""
        cls._instance = None

    async def initialize(self) -> dict[str, bool]:
        """Initialize all enabled MCP servers (local and remote).

        Returns:
            Dict mapping server names to connection success status
        """
        async with self._lock:
            if self._initialized:
                logger.debug("[MCP] Manager already initialized")
                return self._get_current_status()

            logger.info("[MCP] Initializing MCP server manager...")
            results = {}

            # Initialize local servers
            local_results = await self._local.initialize()
            results.update(local_results)

            # Initialize remote servers
            remote_results = await self._remote.initialize()
            results.update(remote_results)

            self._initialized = True
            connected = sum(1 for v in results.values() if v)
            logger.info(
                f"[MCP] Initialization complete: {connected}/{len(results)} servers connected"
            )
            return results

    def _get_current_status(self) -> dict[str, bool]:
        """Get current connection status of all servers."""
        status = {}
        for name, process in self._local._servers.items():
            status[name] = process.is_connected
        for name, conn in self._remote._connections.items():
            status[name] = conn.is_connected
        return status

    async def start_server(self, server_name: str) -> bool:
        """Start a specific server by name.

        Args:
            server_name: Name of the server to start

        Returns:
            True if the server was started successfully
        """
        result = find_server_config(server_name)
        if not result:
            logger.error(f"[MCP] Server '{server_name}' not found")
            return False

        server_type, config = result

        if server_type == ServerType.LOCAL:
            return await self._local.start_server(server_name)
        else:
            return await self._remote.connect_server(server_name)

    async def stop_server(self, server_name: str) -> bool:
        """Stop a specific server by name.

        Args:
            server_name: Name of the server to stop

        Returns:
            True if the server was stopped successfully
        """
        # Try local first
        if server_name in self._local._servers:
            return await self._local.stop_server(server_name)

        # Try remote
        if server_name in self._remote._connections:
            return await self._remote.disconnect_server(server_name)

        logger.warning(f"[MCP] Server '{server_name}' not running")
        return False

    async def restart_server(self, server_name: str) -> bool:
        """Restart a specific server.

        Args:
            server_name: Name of the server to restart

        Returns:
            True if the server was restarted successfully
        """
        # Try local first
        if server_name in self._local._servers:
            return await self._local.restart_server(server_name)

        # Try remote
        if server_name in self._remote._connections:
            return await self._remote.reconnect_server(server_name)

        # Server not running, try to start it
        return await self.start_server(server_name)

    async def enable_server(self, server_name: str) -> bool:
        """Enable a server and start it.

        Args:
            server_name: Name of the server to enable

        Returns:
            True if successful
        """
        result = find_server_config(server_name)
        if not result:
            return False

        server_type, config = result

        if server_type == ServerType.LOCAL:
            config.enabled = True
            save_local_server_config(config)
        else:
            config.enabled = True
            save_remote_server_config(config)

        return await self.start_server(server_name)

    async def disable_server(self, server_name: str) -> bool:
        """Disable a server and stop it.

        Args:
            server_name: Name of the server to disable

        Returns:
            True if successful
        """
        await self.stop_server(server_name)

        result = find_server_config(server_name)
        if not result:
            return False

        server_type, config = result

        if server_type == ServerType.LOCAL:
            config.enabled = False
            save_local_server_config(config)
        else:
            config.enabled = False
            save_remote_server_config(config)

        return True

    async def hot_reload_server(self, server_name: str) -> bool:
        """Enable hot reload for a local server.

        Args:
            server_name: Name of the server

        Returns:
            True if hot reload was enabled
        """
        return await self._local.hot_reload_server(server_name)

    async def disable_hot_reload(self, server_name: str) -> bool:
        """Disable hot reload for a local server.

        Args:
            server_name: Name of the server

        Returns:
            True if hot reload was disabled
        """
        return await self._local.disable_hot_reload(server_name)

    def get_all_tools(self) -> list[tuple[str, MCPTool]]:
        """Get all tools from all connected servers.

        Returns:
            List of (server_name, tool) tuples
        """
        tools = []
        tools.extend(self._local.get_all_tools())
        tools.extend(self._remote.get_all_tools())
        return tools

    def get_namespaced_tools(self) -> dict[str, MCPTool]:
        """Get all tools with namespaced names.

        Tool names are prefixed with server name: {server}__{tool}

        Returns:
            Dict mapping namespaced tool names to MCPTool objects
        """
        result = {}
        result.update(self._local.get_namespaced_tools())
        result.update(self._remote.get_namespaced_tools())
        return result

    def parse_tool_name(self, namespaced_name: str) -> tuple[str | None, str]:
        """Parse a namespaced tool name into server and tool components.

        Args:
            namespaced_name: Tool name, either "server__tool" or just "tool"

        Returns:
            Tuple of (server_name, tool_name). Server is None if not namespaced.
        """
        if "__" in namespaced_name:
            parts = namespaced_name.split("__", 1)
            return parts[0], parts[1]
        return None, namespaced_name

    def find_tool(self, tool_name: str) -> tuple[str, str] | None:
        """Find a tool by name, handling ambiguous names.

        Args:
            tool_name: Either "server__tool" or just "tool"

        Returns:
            Tuple of (server_name, actual_tool_name) or None if not found
        """
        server_hint, base_name = self.parse_tool_name(tool_name)

        if server_hint:
            # Explicit server specified
            # Check local
            process = self._local.get_server(server_hint)
            if process and base_name in process.get_tool_names():
                return (server_hint, base_name)

            # Check remote
            conn = self._remote.get_connection(server_hint)
            if conn and base_name in conn.get_tool_names():
                return (server_hint, base_name)

            return None

        # Search all servers for the tool
        matches = []

        # Check local servers
        for server_name, process in self._local._servers.items():
            if base_name in process.get_tool_names():
                matches.append((server_name, base_name))

        # Check remote servers
        for server_name, conn in self._remote._connections.items():
            if base_name in conn.get_tool_names():
                matches.append((server_name, base_name))

        if len(matches) == 1:
            return matches[0]
        elif len(matches) > 1:
            logger.warning(
                f"[MCP] Ambiguous tool name '{base_name}' found in: "
                f"{[m[0] for m in matches]}. Using '{matches[0][0]}'."
            )
            return matches[0]

        return None

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Call a tool by name, routing to the correct server.

        Args:
            tool_name: Tool name (either "server__tool" or just "tool")
            arguments: Arguments to pass to the tool

        Returns:
            Tool execution result as a string
        """
        location = self.find_tool(tool_name)
        if not location:
            available = list(self.get_namespaced_tools().keys())
            return (
                f"Error: Tool '{tool_name}' not found. "
                f"Available MCP tools: {', '.join(available[:10])}"
            )

        server_name, actual_tool_name = location

        # Try local first
        if server_name in self._local._servers:
            return await self._local.call_tool(server_name, actual_tool_name, arguments)

        # Try remote
        if server_name in self._remote._connections:
            return await self._remote.call_tool(server_name, actual_tool_name, arguments)

        return f"Error: Server '{server_name}' not connected"

    def get_server_status(self, server_name: str) -> dict[str, Any] | None:
        """Get status of a specific server.

        Args:
            server_name: Name of the server

        Returns:
            Status dict or None if not found
        """
        # Check local
        status = self._local.get_server_status(server_name)
        if status:
            return status

        # Check remote
        return self._remote.get_server_status(server_name)

    def get_all_server_status(self) -> list[dict[str, Any]]:
        """Get status of all known servers.

        Returns:
            List of status dicts for all servers
        """
        statuses = []
        statuses.extend(self._local.get_all_status())
        statuses.extend(self._remote.get_all_status())
        return statuses

    async def shutdown(self) -> None:
        """Shutdown all MCP server connections."""
        async with self._lock:
            total = len(self._local) + len(self._remote)
            logger.info(f"[MCP] Shutting down {total} servers...")

            # Suppress noisy asyncio errors during async generator cleanup.
            # These occur when stdio_client generators are closed from a different task.
            # We intentionally do NOT restore the original handler because the async
            # generators from stdio_client may not be cleaned up until the event loop
            # fully terminates (after this method returns). Keeping this handler active
            # ensures those late cleanup errors are also suppressed.
            loop = asyncio.get_running_loop()
            original_handler = loop.get_exception_handler()

            def _shutdown_exception_handler(loop, context):
                msg = context.get("message", "")
                exc = context.get("exception")
                # Suppress known harmless shutdown errors
                if "closing of asynchronous generator" in msg:
                    return
                if exc and ("cancel scope" in str(exc).lower() or "GeneratorExit" in str(exc)):
                    return
                # Fall through to original handler for other errors
                if original_handler:
                    original_handler(loop, context)
                else:
                    loop.default_exception_handler(context)

            loop.set_exception_handler(_shutdown_exception_handler)

            # Also suppress the asyncio logger which logs these errors directly
            # before the exception handler can intercept them
            class _AsyncgenShutdownFilter(logging.Filter):
                def filter(self, record):
                    msg = record.getMessage()
                    # Check the message text
                    if "closing of asynchronous generator" in msg:
                        return False
                    if "cancel scope" in msg.lower() and "different task" in msg.lower():
                        return False
                    if "stdio_client" in msg:
                        return False
                    # Also check exception info if present
                    if record.exc_info:
                        exc_text = str(record.exc_info[1]) if record.exc_info[1] else ""
                        if "cancel scope" in exc_text.lower():
                            return False
                        if "GeneratorExit" in exc_text:
                            return False
                        # Check exception type
                        exc_type = record.exc_info[0]
                        if exc_type and exc_type.__name__ in ("RuntimeError", "GeneratorExit", "BaseExceptionGroup"):
                            exc_str = str(record.exc_info[1])
                            if "cancel scope" in exc_str.lower() or "stdio_client" in exc_str:
                                return False
                    return True

            asyncio_logger = logging.getLogger("asyncio")
            shutdown_filter = _AsyncgenShutdownFilter()
            asyncio_logger.addFilter(shutdown_filter)

            await self._local.shutdown()
            await self._remote.shutdown()

            self._initialized = False
            logger.info("[MCP] Shutdown complete")

    async def reload(self) -> dict[str, bool]:
        """Reload all server configurations from storage.

        Stops servers that were disabled, starts newly enabled servers.

        Returns:
            Dict mapping server names to connection status
        """
        async with self._lock:
            results = {}
            all_configs = list_all_server_configs()

            # Build sets of enabled servers
            enabled_local = {
                c.name for c in all_configs
                if isinstance(c, LocalServerConfig) and c.enabled
            }
            enabled_remote = {
                c.name for c in all_configs
                if isinstance(c, RemoteServerConfig) and c.enabled
            }

            # Handle local servers
            running_local = set(self._local._servers.keys())

            # Stop disabled local servers
            for name in running_local - enabled_local:
                logger.info(f"[MCP] Stopping disabled local server '{name}'")
                await self._local.stop_server(name)
                results[name] = False

            # Start newly enabled local servers
            for name in enabled_local:
                if name not in self._local._servers:
                    logger.info(f"[MCP] Starting newly enabled local server '{name}'")
                results[name] = await self._local.start_server(name)

            # Handle remote servers
            connected_remote = set(self._remote._connections.keys())

            # Stop disabled remote servers
            for name in connected_remote - enabled_remote:
                logger.info(f"[MCP] Disconnecting disabled remote server '{name}'")
                await self._remote.disconnect_server(name)
                results[name] = False

            # Start newly enabled remote servers
            for name in enabled_remote:
                if name not in self._remote._connections:
                    logger.info(f"[MCP] Connecting newly enabled remote server '{name}'")
                results[name] = await self._remote.connect_server(name)

            return results

    def __len__(self) -> int:
        """Return number of connected servers."""
        return len(self._local) + len(self._remote)

    def __contains__(self, server_name: str) -> bool:
        """Check if a server is connected."""
        return server_name in self._local or server_name in self._remote

    # --- Compatibility Properties ---

    @property
    def _clients(self) -> dict[str, Any]:
        """Legacy compatibility: return dict of all connected clients.

        Provides backwards compatibility with code that accesses _clients directly.
        """
        clients = {}
        for name, process in self._local._servers.items():
            clients[name] = process
        for name, conn in self._remote._connections.items():
            clients[name] = conn
        return clients

    # --- Format Conversion Methods ---

    def get_tools_openai_format(self) -> list[dict[str, Any]]:
        """Get all MCP tools in OpenAI function format.

        Returns:
            List of tool definitions in OpenAI format for use in API calls
        """
        tools = []
        for server_name, mcp_tool in self.get_all_tools():
            namespaced_name = f"{server_name}__{mcp_tool.name}"

            # Enhance description with server info
            description = mcp_tool.description
            if not description.endswith("."):
                description += "."
            description += f" (MCP: {server_name})"

            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": namespaced_name,
                        "description": description,
                        "parameters": mcp_tool.input_schema,
                    },
                }
            )
        return tools

    def get_tools_claude_format(self) -> list[dict[str, Any]]:
        """Get all MCP tools in Claude native format.

        Returns:
            List of tool definitions in Anthropic Claude format
        """
        tools = []
        for server_name, mcp_tool in self.get_all_tools():
            namespaced_name = f"{server_name}__{mcp_tool.name}"

            # Enhance description with server info
            description = mcp_tool.description
            if not description.endswith("."):
                description += "."
            description += f" (MCP: {server_name})"

            tools.append(
                {
                    "name": namespaced_name,
                    "description": description,
                    "input_schema": mcp_tool.input_schema,
                }
            )
        return tools

    def get_tool_schema(self, namespaced_name: str) -> dict[str, Any] | None:
        """Get the parameter schema for a specific MCP tool.

        Args:
            namespaced_name: The namespaced tool name (server__tool)

        Returns:
            Input schema dict or None if tool not found
        """
        location = self.find_tool(namespaced_name)
        if not location:
            return None

        server_name, tool_name = location

        # Check local
        process = self._local.get_server(server_name)
        if process:
            for tool in process.get_tools():
                if tool.name == tool_name:
                    return tool.input_schema

        # Check remote
        conn = self._remote.get_connection(server_name)
        if conn:
            for tool in conn.get_tools():
                if tool.name == tool_name:
                    return tool.input_schema

        return None

    def is_mcp_tool(self, tool_name: str) -> bool:
        """Check if a tool name is an MCP tool.

        Args:
            tool_name: Tool name to check

        Returns:
            True if it's a connected MCP tool
        """
        return self.find_tool(tool_name) is not None
