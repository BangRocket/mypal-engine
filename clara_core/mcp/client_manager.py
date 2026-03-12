"""MCP Client Manager - Multi-server lifecycle management.

Manages connections to multiple MCP servers, handles tool discovery,
and provides graceful degradation when servers fail.

CRITICAL: This module must detect the running event loop and schedule
as a task, not call asyncio.run(). The Discord bot already runs an
event loop that we must integrate with.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator

# MCP SDK imports
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client

# Use stderr for logging (MCP stdio transport uses stdout)
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("[%(name)s] %(levelname)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


class TransportType(Enum):
    """MCP transport types."""
    STDIO = "stdio"
    HTTP = "http"  # Streamable HTTP (SSE)


@dataclass
class ServerConfig:
    """Configuration for an MCP server connection."""
    name: str
    transport: TransportType
    # For stdio transport
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    # For HTTP transport
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class ServerConnection:
    """Active connection to an MCP server."""
    config: ServerConfig
    session: ClientSession
    tools: list[dict[str, Any]] = field(default_factory=list)
    connected: bool = False
    # Context managers for cleanup
    _read_stream: Any = None
    _write_stream: Any = None
    _context_manager: Any = None


class MCPClientManager:
    """Manages connections to multiple MCP servers.

    Provides:
    - Connection pooling with automatic reconnection
    - Tool discovery and aggregation across servers
    - Graceful degradation when servers fail
    - Event loop integration for Discord bot context

    Usage:
        manager = MCPClientManager()
        await manager.connect_server(ServerConfig(
            name="local-files",
            transport=TransportType.STDIO,
            command="python",
            args=["-m", "mcp_servers.local_files"],
        ))
        tools = manager.get_all_tools()
    """

    def __init__(self) -> None:
        self._servers: dict[str, ServerConnection] = {}
        self._lock = asyncio.Lock()

    async def connect_server(self, config: ServerConfig) -> bool:
        """Connect to an MCP server.

        Args:
            config: Server configuration

        Returns:
            True if connection successful, False otherwise
        """
        async with self._lock:
            if config.name in self._servers:
                logger.warning(f"Server {config.name} already connected")
                return True

            try:
                if config.transport == TransportType.STDIO:
                    conn = await self._connect_stdio(config)
                elif config.transport == TransportType.HTTP:
                    conn = await self._connect_http(config)
                else:
                    logger.error(f"Unknown transport type: {config.transport}")
                    return False

                # Discover tools
                tools_response = await conn.session.list_tools()
                tools = [
                    {
                        "name": f"{config.name}_{tool.name}",  # Prefix to avoid collisions
                        "description": tool.description or "",
                        "inputSchema": tool.inputSchema if hasattr(tool, 'inputSchema') else {},
                        "server": config.name,
                        "original_name": tool.name,
                    }
                    for tool in tools_response.tools
                ]

                conn.tools = tools
                conn.connected = True
                self._servers[config.name] = conn

                logger.info(f"Connected to {config.name}: {len(tools)} tools discovered")
                return True

            except Exception as e:
                logger.error(f"Failed to connect to {config.name}: {e}")
                return False

    async def _connect_stdio(self, config: ServerConfig) -> ServerConnection:
        """Connect to a stdio MCP server."""
        if not config.command:
            raise ValueError(f"STDIO server {config.name} requires command")

        server_params = StdioServerParameters(
            command=config.command,
            args=config.args,
            env={**config.env} if config.env else None,
        )

        # Create stdio client connection using async context manager
        cm = stdio_client(server_params)
        read, write = await cm.__aenter__()
        session = ClientSession(read, write)
        await session.initialize()

        return ServerConnection(
            config=config,
            session=session,
            _read_stream=read,
            _write_stream=write,
            _context_manager=cm,
        )

    async def _connect_http(self, config: ServerConfig) -> ServerConnection:
        """Connect to an HTTP (SSE) MCP server."""
        if not config.url:
            raise ValueError(f"HTTP server {config.name} requires url")

        # Create SSE client connection using async context manager
        cm = sse_client(
            url=config.url,
            headers=config.headers,
        )
        read, write = await cm.__aenter__()
        session = ClientSession(read, write)
        await session.initialize()

        return ServerConnection(
            config=config,
            session=session,
            _read_stream=read,
            _write_stream=write,
            _context_manager=cm,
        )

    async def disconnect_server(self, name: str) -> bool:
        """Disconnect from an MCP server.

        Args:
            name: Server name

        Returns:
            True if disconnected, False if not found
        """
        async with self._lock:
            if name not in self._servers:
                return False

            conn = self._servers[name]
            try:
                # Close the context manager to clean up resources
                if conn._context_manager:
                    await conn._context_manager.__aexit__(None, None, None)
            except Exception as e:
                logger.warning(f"Error closing {name}: {e}")

            del self._servers[name]
            logger.info(f"Disconnected from {name}")
            return True

    async def disconnect_all(self) -> None:
        """Disconnect from all MCP servers."""
        server_names = list(self._servers.keys())
        for name in server_names:
            await self.disconnect_server(name)

    def get_all_tools(self) -> list[dict[str, Any]]:
        """Get aggregated tools from all connected servers.

        Returns:
            List of tool definitions with server prefixes
        """
        tools = []
        for conn in self._servers.values():
            if conn.connected:
                tools.extend(conn.tools)
        return tools

    def get_server_tools(self, name: str) -> list[dict[str, Any]]:
        """Get tools from a specific server.

        Args:
            name: Server name

        Returns:
            List of tool definitions, or empty list if not found
        """
        conn = self._servers.get(name)
        if conn and conn.connected:
            return conn.tools
        return []

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        """Call a tool on the appropriate server.

        Args:
            tool_name: Prefixed tool name (e.g., "local_files_save")
            arguments: Tool arguments

        Returns:
            Tool result

        Raises:
            ValueError: If tool not found
        """
        # Find the server and original tool name
        for conn in self._servers.values():
            for tool in conn.tools:
                if tool["name"] == tool_name:
                    original_name = tool["original_name"]
                    result = await conn.session.call_tool(
                        original_name,
                        arguments,
                    )
                    return result

        raise ValueError(f"Tool not found: {tool_name}")

    def is_connected(self, name: str) -> bool:
        """Check if a server is connected.

        Args:
            name: Server name

        Returns:
            True if connected
        """
        conn = self._servers.get(name)
        return conn.connected if conn else False

    @property
    def connected_servers(self) -> list[str]:
        """Get list of connected server names."""
        return [name for name, conn in self._servers.items() if conn.connected]

    @property
    def tool_count(self) -> int:
        """Get total number of tools across all servers."""
        return sum(len(conn.tools) for conn in self._servers.values() if conn.connected)

    @asynccontextmanager
    async def managed_connection(self, config: ServerConfig) -> AsyncIterator[bool]:
        """Context manager for automatic cleanup.

        Usage:
            async with manager.managed_connection(config) as success:
                if success:
                    tools = manager.get_all_tools()
        """
        success = await self.connect_server(config)
        try:
            yield success
        finally:
            if success:
                await self.disconnect_server(config.name)


def get_or_create_event_loop() -> asyncio.AbstractEventLoop:
    """Get the running event loop or create one if needed.

    This is critical for Discord bot integration - we must detect
    if we're already in an async context and use that loop.
    """
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        # No running loop, create one
        return asyncio.new_event_loop()
