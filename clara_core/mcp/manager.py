"""MCP Server Manager for managing multiple MCP server connections.

This module provides the MCPServerManager class which is responsible for:
- Loading server configurations from the database
- Starting and stopping MCP server connections
- Managing the lifecycle of all MCP clients
- Aggregating tools from all connected servers
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, ClassVar

from db import SessionLocal

from .client import MCPClient, MCPTool
from .models import MCPServer

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def utcnow():
    """Return current UTC time (naive, for SQLite compatibility)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class MCPServerManager:
    """Central manager for all MCP server connections.

    This singleton class manages the lifecycle of MCP clients, loading
    configurations from the database and maintaining connections to
    enabled servers.

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
        self._clients: dict[str, MCPClient] = {}  # server_name -> MCPClient
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
        """Initialize all enabled MCP servers from the database.

        Returns:
            Dict mapping server names to connection success status
        """
        async with self._lock:
            if self._initialized:
                logger.debug("[MCP] Manager already initialized")
                return {name: client.is_connected for name, client in self._clients.items()}

            logger.info("[MCP] Initializing MCP server manager...")
            results = {}

            # Load all enabled servers from database
            with SessionLocal() as session:
                servers = session.query(MCPServer).filter(MCPServer.enabled == True).all()  # noqa: E712
                logger.info(f"[MCP] Found {len(servers)} enabled MCP servers")

                for server in servers:
                    # Detach from session to use outside the context
                    session.expunge(server)
                    results[server.name] = await self._start_server(server)

            self._initialized = True
            logger.info(f"[MCP] Initialization complete: {sum(results.values())}/{len(results)} servers connected")
            return results

    async def _start_server(self, server: MCPServer) -> bool:
        """Start a single MCP server connection.

        Args:
            server: MCPServer configuration

        Returns:
            True if connection was successful
        """
        if server.name in self._clients:
            logger.warning(f"[MCP] Server '{server.name}' already running")
            return self._clients[server.name].is_connected

        logger.info(f"[MCP] Starting server '{server.name}' ({server.source_type})")

        try:
            client = MCPClient(server)
            success = await client.connect()

            if success:
                self._clients[server.name] = client
                # Update database with tool info
                await self._update_server_status(server.name, "running", client.get_tools())
                return True
            else:
                await self._update_server_status(server.name, "error", error=client.state.last_error)
                return False

        except Exception as e:
            logger.error(f"[MCP] Failed to start server '{server.name}': {e}")
            await self._update_server_status(server.name, "error", error=str(e))
            return False

    async def _update_server_status(
        self,
        server_name: str,
        status: str,
        tools: list[MCPTool] | None = None,
        error: str | None = None,
    ) -> None:
        """Update server status in the database.

        Args:
            server_name: Name of the server
            status: New status ("running", "stopped", "error")
            tools: Optional list of discovered tools
            error: Optional error message
        """
        try:
            with SessionLocal() as session:
                server = session.query(MCPServer).filter(MCPServer.name == server_name).first()
                if server:
                    server.status = status
                    if tools is not None:
                        server.set_tools([t.to_dict() for t in tools])
                    if error:
                        server.last_error = error
                        server.last_error_at = utcnow()
                    server.updated_at = utcnow()
                    session.commit()
        except Exception as e:
            logger.warning(f"[MCP] Failed to update server status: {e}")

    async def start_server(self, server_name: str) -> bool:
        """Start a specific server by name.

        Args:
            server_name: Name of the server to start

        Returns:
            True if the server was started successfully
        """
        async with self._lock:
            with SessionLocal() as session:
                server = session.query(MCPServer).filter(MCPServer.name == server_name).first()
                if not server:
                    logger.error(f"[MCP] Server '{server_name}' not found")
                    return False

                session.expunge(server)
                return await self._start_server(server)

    async def stop_server(self, server_name: str) -> bool:
        """Stop a specific server by name.

        Args:
            server_name: Name of the server to stop

        Returns:
            True if the server was stopped successfully
        """
        async with self._lock:
            if server_name not in self._clients:
                logger.warning(f"[MCP] Server '{server_name}' not running")
                return False

            client = self._clients.pop(server_name)
            await client.disconnect()
            await self._update_server_status(server_name, "stopped")
            logger.info(f"[MCP] Server '{server_name}' stopped")
            return True

    async def restart_server(self, server_name: str) -> bool:
        """Restart a specific server.

        Args:
            server_name: Name of the server to restart

        Returns:
            True if the server was restarted successfully
        """
        await self.stop_server(server_name)
        return await self.start_server(server_name)

    async def enable_server(self, server_name: str) -> bool:
        """Enable a server and start it.

        Args:
            server_name: Name of the server to enable

        Returns:
            True if successful
        """
        with SessionLocal() as session:
            server = session.query(MCPServer).filter(MCPServer.name == server_name).first()
            if not server:
                return False
            server.enabled = True
            session.commit()

        return await self.start_server(server_name)

    async def disable_server(self, server_name: str) -> bool:
        """Disable a server and stop it.

        Args:
            server_name: Name of the server to disable

        Returns:
            True if successful
        """
        await self.stop_server(server_name)

        with SessionLocal() as session:
            server = session.query(MCPServer).filter(MCPServer.name == server_name).first()
            if not server:
                return False
            server.enabled = False
            session.commit()

        return True

    def get_client(self, server_name: str) -> MCPClient | None:
        """Get a client by server name.

        Args:
            server_name: Name of the server

        Returns:
            MCPClient if found and connected, None otherwise
        """
        return self._clients.get(server_name)

    def get_all_tools(self) -> list[tuple[str, MCPTool]]:
        """Get all tools from all connected servers.

        Returns:
            List of (server_name, tool) tuples
        """
        tools = []
        for server_name, client in self._clients.items():
            if client.is_connected:
                for tool in client.get_tools():
                    tools.append((server_name, tool))
        return tools

    def get_namespaced_tools(self) -> dict[str, MCPTool]:
        """Get all tools with namespaced names.

        Tool names are prefixed with server name: {server}__{tool}

        Returns:
            Dict mapping namespaced tool names to MCPTool objects
        """
        result = {}
        for server_name, tool in self.get_all_tools():
            namespaced_name = f"{server_name}__{tool.name}"
            result[namespaced_name] = tool
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
            client = self._clients.get(server_hint)
            if client and base_name in client.get_tool_names():
                return (server_hint, base_name)
            return None

        # Search all servers for the tool
        matches = []
        for server_name, client in self._clients.items():
            if base_name in client.get_tool_names():
                matches.append((server_name, base_name))

        if len(matches) == 1:
            return matches[0]
        elif len(matches) > 1:
            # Ambiguous - return the first match but log a warning
            logger.warning(
                f"[MCP] Ambiguous tool name '{base_name}' found in: {[m[0] for m in matches]}. "
                f"Using '{matches[0][0]}'. Use namespaced name for explicit selection."
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
            return f"Error: Tool '{tool_name}' not found. Available MCP tools: {', '.join(available[:10])}"

        server_name, actual_tool_name = location
        client = self._clients.get(server_name)

        if not client:
            return f"Error: Server '{server_name}' not connected"

        return await client.call_tool(actual_tool_name, arguments)

    def get_server_status(self, server_name: str) -> dict[str, Any] | None:
        """Get status of a specific server.

        Args:
            server_name: Name of the server

        Returns:
            Status dict or None if not found
        """
        client = self._clients.get(server_name)
        if client:
            return client.get_status()

        # Check database for stopped servers
        with SessionLocal() as session:
            server = session.query(MCPServer).filter(MCPServer.name == server_name).first()
            if server:
                return {
                    "name": server.name,
                    "connected": False,
                    "transport": server.transport,
                    "tool_count": server.tool_count,
                    "tools": [t["name"] for t in server.get_tools()],
                    "last_error": server.last_error,
                    "status": server.status,
                }
        return None

    def get_all_server_status(self) -> list[dict[str, Any]]:
        """Get status of all known servers.

        Returns:
            List of status dicts for all servers
        """
        statuses = []

        with SessionLocal() as session:
            servers = session.query(MCPServer).all()
            for server in servers:
                client = self._clients.get(server.name)
                if client:
                    statuses.append(client.get_status())
                else:
                    statuses.append({
                        "name": server.name,
                        "connected": False,
                        "enabled": server.enabled,
                        "transport": server.transport,
                        "source_type": server.source_type,
                        "tool_count": server.tool_count,
                        "status": server.status,
                        "last_error": server.last_error,
                    })

        return statuses

    async def shutdown(self) -> None:
        """Shutdown all MCP server connections."""
        async with self._lock:
            logger.info(f"[MCP] Shutting down {len(self._clients)} servers...")

            for server_name, client in list(self._clients.items()):
                try:
                    await client.disconnect()
                except Exception as e:
                    logger.warning(f"[MCP] Error disconnecting '{server_name}': {e}")

            self._clients.clear()
            self._initialized = False
            logger.info("[MCP] Shutdown complete")

    async def reload(self) -> dict[str, bool]:
        """Reload all server configurations from database.

        Stops servers that were disabled, starts newly enabled servers.

        Returns:
            Dict mapping server names to connection status
        """
        async with self._lock:
            results = {}

            with SessionLocal() as session:
                servers = session.query(MCPServer).all()

                # Build sets for comparison
                enabled_names = {s.name for s in servers if s.enabled}
                running_names = set(self._clients.keys())

                # Stop servers that should be stopped
                for name in running_names - enabled_names:
                    logger.info(f"[MCP] Stopping disabled server '{name}'")
                    client = self._clients.pop(name)
                    await client.disconnect()
                    results[name] = False

                # Start servers that should be running
                for server in servers:
                    if server.enabled:
                        session.expunge(server)
                        if server.name not in self._clients:
                            logger.info(f"[MCP] Starting newly enabled server '{server.name}'")
                            results[server.name] = await self._start_server(server)
                        else:
                            # Already running
                            results[server.name] = self._clients[server.name].is_connected

            return results

    def __len__(self) -> int:
        """Return number of connected servers."""
        return len(self._clients)

    def __contains__(self, server_name: str) -> bool:
        """Check if a server is connected."""
        return server_name in self._clients
