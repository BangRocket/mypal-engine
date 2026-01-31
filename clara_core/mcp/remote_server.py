"""Remote MCP server connection management.

This module handles connecting to remote MCP servers via HTTP/SSE transport.
Remote servers are hosted externally and connect via their server URLs.

Features:
- Connect to remote MCP endpoints
- OAuth authentication support (for Smithery-hosted)
- Custom headers for authentication
- Tool discovery
- Connection pooling and reconnection
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from mcp import ClientSession, types

from .models import (
    RemoteServerConfig,
    load_remote_server_config,
    save_remote_server_config,
    utcnow_iso,
)

if TYPE_CHECKING:
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
class RemoteServerState:
    """Tracks the runtime state of a remote MCP server connection."""

    connected: bool = False
    tools: list[MCPTool] = field(default_factory=list)
    last_error: str | None = None
    reconnect_attempts: int = 0
    max_reconnect_attempts: int = 3


class RemoteServerConnection:
    """Manages connection to a single remote MCP server.

    Handles:
    - Connecting via HTTP/SSE transport
    - OAuth authentication for Smithery-hosted servers
    - Custom headers for authentication
    - Tool discovery
    - Automatic reconnection
    """

    def __init__(
        self,
        config: RemoteServerConfig,
        oauth_client: "SmitheryOAuthClient | None" = None,
        on_tools_changed: Callable[[str, list[MCPTool]], None] | None = None,
    ) -> None:
        """Initialize the remote server connection.

        Args:
            config: Server configuration
            oauth_client: Optional OAuth client for authenticated connections
            on_tools_changed: Callback when tools are discovered/changed
        """
        self.config = config
        self._oauth_client = oauth_client
        self.state = RemoteServerState()
        self._session: ClientSession | None = None
        self._exit_stack: AsyncExitStack | None = None
        self._lock = asyncio.Lock()
        self._on_tools_changed = on_tools_changed

    @property
    def name(self) -> str:
        """Return the server name."""
        return self.config.name

    @property
    def is_connected(self) -> bool:
        """Check if connected to the server."""
        return self.state.connected and self._session is not None

    async def connect(self) -> bool:
        """Connect to the remote MCP server.

        Returns:
            True if connection was successful
        """
        async with self._lock:
            if self.is_connected:
                logger.debug(f"[MCP:{self.name}] Already connected")
                return True

            # Skip servers pending OAuth
            if self.config.status == "pending_auth":
                logger.info(f"[MCP:{self.name}] Pending OAuth authorization, skipping")
                return False

            try:
                return await self._connect()
            except Exception as e:
                self.state.last_error = str(e)
                self.state.connected = False
                logger.error(f"[MCP:{self.name}] Connection failed: {e}")
                self._update_config_status("error", error=str(e))
                return False

    async def _connect(self) -> bool:
        """Establish connection to the remote server."""
        if not self.config.server_url:
            self.state.last_error = "No server URL specified"
            return False

        try:
            from mcp.client.streamable_http import streamablehttp_client
        except ImportError:
            self.state.last_error = "HTTP transport not available in MCP SDK"
            return False

        logger.info(f"[MCP:{self.name}] Connecting to: {self.config.server_url}")

        # Build headers
        headers: dict[str, str] = dict(self.config.headers)

        # Add OAuth token if available
        if self._oauth_client:
            if await self._oauth_client.ensure_valid_token():
                headers.update(self._oauth_client.get_auth_headers())
                logger.info(f"[MCP:{self.name}] Using OAuth authentication")
            else:
                logger.warning(f"[MCP:{self.name}] OAuth client present but no valid token")

        try:
            self._exit_stack = AsyncExitStack()
            await self._exit_stack.__aenter__()

            # Connect with headers
            if headers:
                read_stream, write_stream, _ = await self._exit_stack.enter_async_context(
                    streamablehttp_client(self.config.server_url, headers=headers)
                )
            else:
                read_stream, write_stream, _ = await self._exit_stack.enter_async_context(
                    streamablehttp_client(self.config.server_url)
                )

            # Enter session context
            self._session = await self._exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )

            # Initialize the connection
            await self._session.initialize()

            # Discover tools
            await self._discover_tools()

            self.state.connected = True
            self.state.reconnect_attempts = 0
            self._update_config_status("running")

            logger.info(
                f"[MCP:{self.name}] Connected, discovered {len(self.state.tools)} tools"
            )
            return True

        except Exception as e:
            error_msg = str(e)
            self.state.last_error = error_msg
            self.state.connected = False

            # Check for auth error
            if "401" in error_msg or "unauthorized" in error_msg.lower():
                self.state.last_error = "Authentication required. Use OAuth flow to connect."
                logger.warning(f"[MCP:{self.name}] Authentication required")
                self._update_config_status("pending_auth", error=self.state.last_error)
            else:
                self._update_config_status("error", error=error_msg)

            if self._exit_stack:
                try:
                    await self._exit_stack.__aexit__(None, None, None)
                except Exception:
                    pass
                self._exit_stack = None

            logger.error(f"[MCP:{self.name}] Connection failed: {e}")
            return False

    async def _discover_tools(self) -> None:
        """Discover available tools from the server."""
        if not self._session:
            return

        try:
            tools_result = await self._session.list_tools()
            self.state.tools = [
                MCPTool(
                    name=tool.name,
                    description=tool.description or "",
                    input_schema=(
                        tool.inputSchema
                        if tool.inputSchema
                        else {"type": "object", "properties": {}}
                    ),
                )
                for tool in tools_result.tools
            ]

            # Update cached tools in config
            self.config.set_tools([t.to_dict() for t in self.state.tools])
            save_remote_server_config(self.config)

            # Notify callback
            if self._on_tools_changed:
                self._on_tools_changed(self.name, self.state.tools)

            logger.debug(
                f"[MCP:{self.name}] Discovered tools: {[t.name for t in self.state.tools]}"
            )

        except Exception as e:
            logger.warning(f"[MCP:{self.name}] Failed to discover tools: {e}")
            self.state.tools = []

    async def disconnect(self) -> None:
        """Disconnect from the remote server."""
        async with self._lock:
            was_connected = self.state.connected
            self.state.connected = False
            self._session = None

            if self._exit_stack:
                exit_stack = self._exit_stack
                self._exit_stack = None
                try:
                    await exit_stack.__aexit__(None, None, None)
                except Exception as e:
                    error_msg = str(e)
                    if "cancel scope" in error_msg.lower() or "different task" in error_msg.lower():
                        logger.debug(f"[MCP:{self.name}] Cross-task disconnect: {e}")
                    else:
                        logger.warning(f"[MCP:{self.name}] Error during disconnect: {e}")

            if was_connected:
                self._update_config_status("stopped")
                logger.info(f"[MCP:{self.name}] Disconnected")

    async def reconnect(self) -> bool:
        """Attempt to reconnect to the server.

        Returns:
            True if reconnection was successful
        """
        self.state.reconnect_attempts += 1
        if self.state.reconnect_attempts > self.state.max_reconnect_attempts:
            logger.warning(f"[MCP:{self.name}] Max reconnect attempts reached")
            return False

        logger.info(
            f"[MCP:{self.name}] Reconnect attempt "
            f"{self.state.reconnect_attempts}/{self.state.max_reconnect_attempts}"
        )

        await self.disconnect()
        await asyncio.sleep(1.0 * self.state.reconnect_attempts)
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
            if not await self.reconnect():
                return f"Error: Not connected to MCP server '{self.name}'"

        try:
            result = await self._session.call_tool(tool_name, arguments)

            # Extract text content
            output_parts = []
            for content in result.content:
                if isinstance(content, types.TextContent):
                    output_parts.append(content.text)
                elif isinstance(content, types.ImageContent):
                    output_parts.append(f"[Image: {content.mimeType}]")
                elif isinstance(content, types.EmbeddedResource):
                    output_parts.append(f"[Resource: {content.resource.uri}]")
                else:
                    output_parts.append(str(content))

            # Check for structured content
            if result.structuredContent:
                import json

                output_parts.append(
                    f"\nStructured: {json.dumps(result.structuredContent, indent=2)}"
                )

            return (
                "\n".join(output_parts)
                if output_parts
                else "Tool executed successfully (no output)"
            )

        except Exception as e:
            error_msg = str(e)
            self.state.last_error = error_msg
            logger.error(f"[MCP:{self.name}] Tool call '{tool_name}' failed: {e}")

            # Check for connection error
            if "connection" in error_msg.lower() or "closed" in error_msg.lower():
                self.state.connected = False
                if await self.reconnect():
                    try:
                        result = await self._session.call_tool(tool_name, arguments)
                        output_parts = []
                        for content in result.content:
                            if hasattr(content, "text"):
                                output_parts.append(content.text)
                        return (
                            "\n".join(output_parts)
                            if output_parts
                            else "Tool executed successfully"
                        )
                    except Exception as retry_error:
                        return f"Error calling tool '{tool_name}': {retry_error}"

            return f"Error calling tool '{tool_name}': {error_msg}"

    def get_tools(self) -> list[MCPTool]:
        """Get the list of discovered tools."""
        return self.state.tools

    def get_tool_names(self) -> list[str]:
        """Get the names of all discovered tools."""
        return [tool.name for tool in self.state.tools]

    def get_status(self) -> dict[str, Any]:
        """Get the current status of the connection."""
        return {
            "name": self.name,
            "type": "remote",
            "connected": self.is_connected,
            "server_url": self.config.server_url,
            "transport": self.config.transport,
            "tool_count": len(self.state.tools),
            "tools": [t.name for t in self.state.tools],
            "last_error": self.state.last_error,
            "reconnect_attempts": self.state.reconnect_attempts,
            "oauth_required": self.config.oauth_required,
            "enabled": self.config.enabled,
            "status": self.config.status,
        }

    def _update_config_status(
        self, status: str, error: str | None = None
    ) -> None:
        """Update the config status and save."""
        self.config.status = status
        if error:
            self.config.last_error = error
            self.config.last_error_at = utcnow_iso()
        self.config.updated_at = utcnow_iso()
        save_remote_server_config(self.config)


class RemoteServerManager:
    """Manages multiple remote MCP server connections.

    Provides:
    - Central management of all remote connections
    - Automatic connection to enabled servers
    - OAuth handling for Smithery-hosted servers
    - Tool aggregation across servers
    """

    def __init__(self) -> None:
        """Initialize the remote server manager."""
        self._connections: dict[str, RemoteServerConnection] = {}
        self._lock = asyncio.Lock()
        self._initialized = False

    async def initialize(self) -> dict[str, bool]:
        """Initialize all enabled remote servers.

        Returns:
            Dict mapping server names to connection success status
        """
        async with self._lock:
            if self._initialized:
                return {name: conn.is_connected for name, conn in self._connections.items()}

            from .models import get_enabled_remote_servers

            configs = get_enabled_remote_servers()
            logger.info(f"[MCP Remote] Found {len(configs)} enabled remote servers")

            results = {}
            for config in configs:
                results[config.name] = await self._connect_server(config)

            self._initialized = True
            connected = sum(1 for v in results.values() if v)
            logger.info(f"[MCP Remote] Connected {connected}/{len(results)} servers")
            return results

    async def _connect_server(self, config: RemoteServerConfig) -> bool:
        """Connect to a single remote server."""
        if config.name in self._connections:
            return self._connections[config.name].is_connected

        # Set up OAuth if needed
        oauth_client = None
        if config.source_type == "smithery-hosted":
            from .oauth import SmitheryOAuthClient, load_oauth_state

            oauth_state = load_oauth_state(config.name)
            if oauth_state and oauth_state.tokens:
                oauth_client = SmitheryOAuthClient(config.name, config.server_url)
                logger.info(f"[MCP Remote] Using OAuth for '{config.name}'")

        connection = RemoteServerConnection(
            config,
            oauth_client=oauth_client,
            on_tools_changed=self._on_tools_changed,
        )
        self._connections[config.name] = connection

        return await connection.connect()

    def _on_tools_changed(self, server_name: str, tools: list[MCPTool]) -> None:
        """Callback when a server's tools change."""
        logger.debug(f"[MCP Remote] Tools changed for {server_name}: {len(tools)} tools")

    async def connect_server(self, server_name: str) -> bool:
        """Connect to a specific server by name.

        Args:
            server_name: Name of the server to connect to

        Returns:
            True if connected successfully
        """
        async with self._lock:
            # Check if already connected
            if server_name in self._connections:
                return await self._connections[server_name].connect()

            # Load config
            config = load_remote_server_config(server_name)
            if not config:
                logger.error(f"[MCP Remote] Server '{server_name}' not found")
                return False

            return await self._connect_server(config)

    async def disconnect_server(self, server_name: str) -> bool:
        """Disconnect from a specific server.

        Args:
            server_name: Name of the server to disconnect from

        Returns:
            True if disconnected successfully
        """
        async with self._lock:
            if server_name not in self._connections:
                logger.warning(f"[MCP Remote] Server '{server_name}' not connected")
                return False

            await self._connections[server_name].disconnect()
            return True

    async def reconnect_server(self, server_name: str) -> bool:
        """Reconnect to a specific server.

        Args:
            server_name: Name of the server to reconnect

        Returns:
            True if reconnected successfully
        """
        if server_name in self._connections:
            return await self._connections[server_name].reconnect()
        return await self.connect_server(server_name)

    def get_connection(self, server_name: str) -> RemoteServerConnection | None:
        """Get a connection by server name."""
        return self._connections.get(server_name)

    def get_all_tools(self) -> list[tuple[str, MCPTool]]:
        """Get all tools from all connected servers.

        Returns:
            List of (server_name, tool) tuples
        """
        tools = []
        for server_name, connection in self._connections.items():
            if connection.is_connected:
                for tool in connection.get_tools():
                    tools.append((server_name, tool))
        return tools

    def get_namespaced_tools(self) -> dict[str, MCPTool]:
        """Get all tools with namespaced names.

        Returns:
            Dict mapping namespaced names (server__tool) to MCPTool
        """
        result = {}
        for server_name, tool in self.get_all_tools():
            namespaced_name = f"{server_name}__{tool.name}"
            result[namespaced_name] = tool
        return result

    async def call_tool(
        self, server_name: str, tool_name: str, arguments: dict[str, Any]
    ) -> str:
        """Call a tool on a specific server.

        Args:
            server_name: Name of the server
            tool_name: Name of the tool
            arguments: Tool arguments

        Returns:
            Tool execution result
        """
        connection = self._connections.get(server_name)
        if not connection:
            return f"Error: Server '{server_name}' not connected"

        return await connection.call_tool(tool_name, arguments)

    def get_server_status(self, server_name: str) -> dict[str, Any] | None:
        """Get status of a specific server."""
        connection = self._connections.get(server_name)
        if connection:
            return connection.get_status()

        config = load_remote_server_config(server_name)
        if config:
            return {
                "name": config.name,
                "type": "remote",
                "connected": False,
                "enabled": config.enabled,
                "server_url": config.server_url,
                "tool_count": config.tool_count,
                "status": config.status,
                "last_error": config.last_error,
            }
        return None

    def get_all_status(self) -> list[dict[str, Any]]:
        """Get status of all servers."""
        from .models import list_remote_server_configs

        statuses = []
        configs = list_remote_server_configs()

        for config in configs:
            connection = self._connections.get(config.name)
            if connection:
                statuses.append(connection.get_status())
            else:
                statuses.append({
                    "name": config.name,
                    "type": "remote",
                    "connected": False,
                    "enabled": config.enabled,
                    "server_url": config.server_url,
                    "tool_count": config.tool_count,
                    "status": config.status,
                    "last_error": config.last_error,
                })

        return statuses

    async def shutdown(self) -> None:
        """Disconnect from all remote servers."""
        async with self._lock:
            logger.info(f"[MCP Remote] Disconnecting {len(self._connections)} servers...")

            for server_name, connection in list(self._connections.items()):
                try:
                    await connection.disconnect()
                except Exception as e:
                    logger.warning(f"[MCP Remote] Error disconnecting '{server_name}': {e}")

            self._connections.clear()
            self._initialized = False
            logger.info("[MCP Remote] Shutdown complete")

    def __len__(self) -> int:
        """Return number of connections."""
        return len(self._connections)

    def __contains__(self, server_name: str) -> bool:
        """Check if a server is connected."""
        return server_name in self._connections


# --- Convenience Functions for Adding Remote Servers ---


def add_remote_server(
    name: str,
    server_url: str,
    headers: dict[str, str] | None = None,
    display_name: str | None = None,
) -> RemoteServerConfig:
    """Add a new remote MCP server configuration.

    Args:
        name: Unique name for the server
        server_url: MCP endpoint URL
        headers: Optional custom headers (e.g., for API keys)
        display_name: Optional display name

    Returns:
        The created RemoteServerConfig
    """
    config = RemoteServerConfig(
        name=name,
        server_url=server_url,
        headers=headers or {},
        display_name=display_name,
    )
    save_remote_server_config(config)
    logger.info(f"[MCP Remote] Added server: {name} -> {server_url}")
    return config


def add_remote_server_from_standard_config(
    config_data: dict[str, Any]
) -> list[RemoteServerConfig]:
    """Add remote servers from standard MCP config format.

    Args:
        config_data: Config in format {"mcpServers": {"name": {"serverUrl": ..., "headers": ...}}}

    Returns:
        List of created RemoteServerConfig objects
    """
    configs = []
    mcp_servers = config_data.get("mcpServers", {})

    for name, server_data in mcp_servers.items():
        config = RemoteServerConfig.from_standard_format(name, server_data)
        save_remote_server_config(config)
        configs.append(config)
        logger.info(f"[MCP Remote] Added server: {name}")

    return configs
