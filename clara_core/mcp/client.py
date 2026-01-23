"""MCP Client wrapper for connecting to MCP servers.

This module provides the MCPClient class which wraps the official MCP SDK
to connect to a single MCP server, discover tools, and execute tool calls.

Supports:
- stdio transport (local servers via subprocess)
- HTTP/SSE transport (remote servers)
- OAuth authentication (for Smithery-hosted servers)
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

if TYPE_CHECKING:
    from .models import MCPServer
    from .oauth import SmitheryOAuthClient

logger = logging.getLogger(__name__)


@dataclass
class MCPTool:
    """Represents a tool discovered from an MCP server."""

    name: str
    description: str
    input_schema: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Convert to a dictionary for JSON serialization."""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


@dataclass
class MCPClientState:
    """Tracks the state of an MCP client connection."""

    connected: bool = False
    tools: list[MCPTool] = field(default_factory=list)
    last_error: str | None = None
    reconnect_attempts: int = 0
    max_reconnect_attempts: int = 3


class MCPClient:
    """Client for connecting to a single MCP server.

    Supports stdio and HTTP transports based on server configuration.
    Handles connection lifecycle, tool discovery, and tool execution.
    Supports OAuth authentication for Smithery-hosted servers.
    """

    def __init__(self, server: MCPServer, oauth_client: SmitheryOAuthClient | None = None) -> None:
        """Initialize the MCP client.

        Args:
            server: MCPServer configuration from the database
            oauth_client: Optional OAuth client for authenticated connections
        """
        self.server = server
        self.state = MCPClientState()
        self._session: ClientSession | None = None
        self._lock = asyncio.Lock()
        self._exit_stack: Any = None
        self._oauth_client = oauth_client

    @property
    def name(self) -> str:
        """Return the server name."""
        return self.server.name

    @property
    def is_connected(self) -> bool:
        """Check if the client is currently connected."""
        return self.state.connected and self._session is not None

    async def connect(self) -> bool:
        """Connect to the MCP server.

        Returns:
            True if connection was successful, False otherwise
        """
        async with self._lock:
            if self.is_connected:
                return True

            try:
                if self.server.transport == "stdio":
                    return await self._connect_stdio()
                elif self.server.transport in ("sse", "streamable-http"):
                    return await self._connect_http()
                else:
                    self.state.last_error = f"Unsupported transport: {self.server.transport}"
                    logger.error(f"[MCP:{self.name}] {self.state.last_error}")
                    return False
            except Exception as e:
                self.state.last_error = str(e)
                self.state.connected = False
                logger.error(f"[MCP:{self.name}] Connection failed: {e}")
                return False

    async def _connect_stdio(self) -> bool:
        """Connect using stdio transport."""
        if not self.server.command:
            self.state.last_error = "No command specified for stdio transport"
            return False

        # Build environment with system env plus any custom vars
        env = dict(os.environ)
        env.update(self.server.get_env())

        # Create server parameters
        params = StdioServerParameters(
            command=self.server.command,
            args=self.server.get_args(),
            env=env,
            cwd=self.server.cwd,
        )

        logger.info(f"[MCP:{self.name}] Connecting via stdio: {params.command} {' '.join(params.args or [])}")

        try:
            # Use AsyncExitStack to manage context managers
            from contextlib import AsyncExitStack

            self._exit_stack = AsyncExitStack()
            await self._exit_stack.__aenter__()

            # Enter stdio client context
            read_stream, write_stream = await self._exit_stack.enter_async_context(stdio_client(params))

            # Enter session context
            self._session = await self._exit_stack.enter_async_context(ClientSession(read_stream, write_stream))

            # Initialize the connection
            await self._session.initialize()

            # Discover tools
            await self._discover_tools()

            self.state.connected = True
            self.state.reconnect_attempts = 0
            logger.info(f"[MCP:{self.name}] Connected successfully, discovered {len(self.state.tools)} tools")
            return True

        except Exception as e:
            self.state.last_error = str(e)
            self.state.connected = False
            if self._exit_stack:
                try:
                    await self._exit_stack.__aexit__(None, None, None)
                except Exception:
                    pass  # Ignore cleanup errors during failed connection
                self._exit_stack = None
            logger.error(f"[MCP:{self.name}] Stdio connection failed: {e}")
            return False

    async def _connect_http(self) -> bool:
        """Connect using HTTP/SSE transport.

        Supports OAuth authentication for Smithery-hosted servers.
        If OAuth client is provided and has valid tokens, they will be used.
        """
        if not self.server.endpoint_url:
            self.state.last_error = "No endpoint URL specified for HTTP transport"
            return False

        try:
            from mcp.client.streamable_http import streamablehttp_client
        except ImportError:
            self.state.last_error = "HTTP transport not available in MCP SDK"
            return False

        logger.info(f"[MCP:{self.name}] Connecting via HTTP: {self.server.endpoint_url}")

        # Build headers with OAuth token if available
        headers: dict[str, str] = {}

        if self._oauth_client:
            # Ensure we have a valid token
            if await self._oauth_client.ensure_valid_token():
                headers = self._oauth_client.get_auth_headers()
                logger.info(f"[MCP:{self.name}] Using OAuth authentication")
            else:
                logger.warning(f"[MCP:{self.name}] OAuth client present but no valid token")

        # Also check for manual token in server env
        if not headers and self.server.get_env().get("SMITHERY_ACCESS_TOKEN"):
            headers["Authorization"] = f"Bearer {self.server.get_env()['SMITHERY_ACCESS_TOKEN']}"
            logger.info(f"[MCP:{self.name}] Using manual access token")

        try:
            from contextlib import AsyncExitStack

            self._exit_stack = AsyncExitStack()
            await self._exit_stack.__aenter__()

            # Enter HTTP client context with optional headers
            if headers:
                read_stream, write_stream, _ = await self._exit_stack.enter_async_context(
                    streamablehttp_client(self.server.endpoint_url, headers=headers)
                )
            else:
                read_stream, write_stream, _ = await self._exit_stack.enter_async_context(
                    streamablehttp_client(self.server.endpoint_url)
                )

            # Enter session context
            self._session = await self._exit_stack.enter_async_context(ClientSession(read_stream, write_stream))

            # Initialize the connection
            await self._session.initialize()

            # Discover tools
            await self._discover_tools()

            self.state.connected = True
            self.state.reconnect_attempts = 0
            logger.info(f"[MCP:{self.name}] Connected successfully, discovered {len(self.state.tools)} tools")
            return True

        except Exception as e:
            error_msg = str(e)
            self.state.last_error = error_msg
            self.state.connected = False

            # Check if this is an auth error (401)
            if "401" in error_msg or "unauthorized" in error_msg.lower():
                self.state.last_error = "Authentication required. Use OAuth flow to connect."
                logger.warning(f"[MCP:{self.name}] Authentication required for this server")

            if self._exit_stack:
                try:
                    await self._exit_stack.__aexit__(None, None, None)
                except Exception:
                    pass  # Ignore cleanup errors during failed connection
                self._exit_stack = None
            logger.error(f"[MCP:{self.name}] HTTP connection failed: {e}")
            return False

    async def _discover_tools(self) -> None:
        """Discover available tools from the connected server."""
        if not self._session:
            return

        try:
            tools_result = await self._session.list_tools()
            self.state.tools = [
                MCPTool(
                    name=tool.name,
                    description=tool.description or "",
                    input_schema=tool.inputSchema if tool.inputSchema else {"type": "object", "properties": {}},
                )
                for tool in tools_result.tools
            ]
            logger.debug(f"[MCP:{self.name}] Discovered tools: {[t.name for t in self.state.tools]}")
        except Exception as e:
            logger.warning(f"[MCP:{self.name}] Failed to discover tools: {e}")
            self.state.tools = []

    async def disconnect(self) -> None:
        """Disconnect from the MCP server."""
        async with self._lock:
            # Mark as disconnected first
            was_connected = self.state.connected
            self.state.connected = False
            self._session = None

            if self._exit_stack:
                exit_stack = self._exit_stack
                self._exit_stack = None
                try:
                    await exit_stack.__aexit__(None, None, None)
                except Exception as e:
                    # Cancel scope errors are expected when disconnecting from a different task
                    # (e.g., during shutdown). The resources will be cleaned up when the process exits.
                    error_msg = str(e)
                    if "cancel scope" in error_msg.lower() or "different task" in error_msg.lower():
                        logger.debug(f"[MCP:{self.name}] Cross-task disconnect (expected during shutdown): {e}")
                    else:
                        logger.warning(f"[MCP:{self.name}] Error during disconnect: {e}")

            if was_connected:
                logger.info(f"[MCP:{self.name}] Disconnected")

    async def reconnect(self) -> bool:
        """Attempt to reconnect to the server.

        Returns:
            True if reconnection was successful, False otherwise
        """
        self.state.reconnect_attempts += 1
        if self.state.reconnect_attempts > self.state.max_reconnect_attempts:
            logger.warning(f"[MCP:{self.name}] Max reconnect attempts reached")
            return False

        logger.info(
            f"[MCP:{self.name}] Reconnect attempt {self.state.reconnect_attempts}/{self.state.max_reconnect_attempts}"
        )

        await self.disconnect()
        await asyncio.sleep(1.0 * self.state.reconnect_attempts)  # Exponential backoff
        return await self.connect()

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Call a tool on the MCP server.

        Args:
            tool_name: Name of the tool to call
            arguments: Arguments to pass to the tool

        Returns:
            Result of the tool call as a string
        """
        if not self.is_connected or not self._session:
            # Try to reconnect
            if not await self.reconnect():
                return f"Error: Not connected to MCP server '{self.name}'"

        try:
            result = await self._session.call_tool(tool_name, arguments)

            # Extract text content from the result
            output_parts = []
            for content in result.content:
                if isinstance(content, types.TextContent):
                    output_parts.append(content.text)
                elif isinstance(content, types.ImageContent):
                    output_parts.append(f"[Image: {content.mimeType}]")
                elif isinstance(content, types.EmbeddedResource):
                    output_parts.append(f"[Resource: {content.resource.uri}]")
                else:
                    # Unknown content type
                    output_parts.append(str(content))

            # Also check for structured content (MCP 2025-06-18 spec)
            if result.structuredContent:
                import json

                output_parts.append(f"\nStructured: {json.dumps(result.structuredContent, indent=2)}")

            return "\n".join(output_parts) if output_parts else "Tool executed successfully (no output)"

        except Exception as e:
            error_msg = str(e)
            self.state.last_error = error_msg
            logger.error(f"[MCP:{self.name}] Tool call '{tool_name}' failed: {e}")

            # Check if this is a connection error
            if "connection" in error_msg.lower() or "closed" in error_msg.lower():
                self.state.connected = False
                # Try reconnecting once
                if await self.reconnect():
                    # Retry the tool call
                    try:
                        result = await self._session.call_tool(tool_name, arguments)
                        output_parts = []
                        for content in result.content:
                            if isinstance(content, types.TextContent):
                                output_parts.append(content.text)
                        return "\n".join(output_parts) if output_parts else "Tool executed successfully"
                    except Exception as retry_error:
                        return f"Error calling tool '{tool_name}': {retry_error}"

            return f"Error calling tool '{tool_name}': {error_msg}"

    def get_tools(self) -> list[MCPTool]:
        """Get the list of discovered tools.

        Returns:
            List of MCPTool objects
        """
        return self.state.tools

    def get_tool_names(self) -> list[str]:
        """Get the names of all discovered tools.

        Returns:
            List of tool names
        """
        return [tool.name for tool in self.state.tools]

    def get_status(self) -> dict[str, Any]:
        """Get the current status of the client.

        Returns:
            Dictionary with connection status, tools, and error info
        """
        return {
            "name": self.name,
            "connected": self.is_connected,
            "transport": self.server.transport,
            "tool_count": len(self.state.tools),
            "tools": [t.name for t in self.state.tools],
            "last_error": self.state.last_error,
            "reconnect_attempts": self.state.reconnect_attempts,
        }


@asynccontextmanager
async def create_mcp_client(server: MCPServer):
    """Context manager for creating an MCP client.

    Usage:
        async with create_mcp_client(server) as client:
            tools = client.get_tools()
            result = await client.call_tool("tool_name", {})

    Args:
        server: MCPServer configuration

    Yields:
        Connected MCPClient instance
    """
    client = MCPClient(server)
    try:
        await client.connect()
        yield client
    finally:
        await client.disconnect()
