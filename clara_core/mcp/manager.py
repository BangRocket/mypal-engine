"""MCP Server Manager for managing multiple MCP server connections.

This module provides the MCPServerManager class which is responsible for:
- Loading server configurations from JSON files (default) or database
- Starting and stopping MCP server connections
- Managing the lifecycle of all MCP clients
- Aggregating tools from all connected servers

Storage modes:
- JSON (default): Configs stored in .mcp_servers/{name}/config.json
- Database: Configs stored in mcp_servers table (set MCP_USE_DATABASE=true)
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any, ClassVar

from .client import MCPClient, MCPTool
from .models import (
    MCPServerConfig,
    delete_server_config,
    get_enabled_servers,
    list_server_configs,
    load_server_config,
    save_server_config,
    utcnow_iso,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Toggle for database vs JSON storage (JSON is default)
USE_DATABASE = os.getenv("MCP_USE_DATABASE", "").lower() in ("true", "1", "yes")


class MCPServerManager:
    """Central manager for all MCP server connections.

    This singleton class manages the lifecycle of MCP clients, loading
    configurations from JSON files (default) or database and maintaining
    connections to enabled servers.

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
        self._configs: dict[str, MCPServerConfig] = {}  # server_name -> config (for JSON mode)
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

    def _load_configs(self) -> list[MCPServerConfig]:
        """Load all enabled server configs from storage."""
        if USE_DATABASE:
            return self._load_configs_from_db()
        return get_enabled_servers()

    def _load_configs_from_db(self) -> list[MCPServerConfig]:
        """Load configs from database (legacy mode)."""
        try:
            from db import SessionLocal

            # Import the SQLAlchemy model for database mode
            from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
            from sqlalchemy.orm import declarative_base

            from db.models import Base

            # Check if mcp_servers table exists in the database
            with SessionLocal() as session:
                # Query using raw SQL to avoid model dependency
                result = session.execute(
                    "SELECT name, source_type, display_name, source_url, transport, "
                    "command, args, cwd, env, endpoint_url, docker_config, enabled, "
                    "status, last_error, tool_count, tools_json, installed_by "
                    "FROM mcp_servers WHERE enabled = true"
                )

                configs = []
                for row in result:
                    config = MCPServerConfig(
                        name=row[0],
                        source_type=row[1],
                        display_name=row[2],
                        source_url=row[3],
                        transport=row[4] or "stdio",
                        command=row[5],
                        args=row[6] if isinstance(row[6], list) else [],
                        cwd=row[7],
                        env=row[8] if isinstance(row[8], dict) else {},
                        endpoint_url=row[9],
                        docker_config=row[10] if isinstance(row[10], dict) else {},
                        enabled=row[11],
                        status=row[12] or "stopped",
                        last_error=row[13],
                        tool_count=row[14] or 0,
                        installed_by=row[16],
                    )
                    configs.append(config)

                return configs

        except Exception as e:
            logger.warning(f"[MCP] Database mode failed, falling back to JSON: {e}")
            return get_enabled_servers()

    def _get_config(self, server_name: str) -> MCPServerConfig | None:
        """Get a specific server config from storage."""
        # Check in-memory cache first
        if server_name in self._configs:
            return self._configs[server_name]

        if USE_DATABASE:
            return self._get_config_from_db(server_name)
        return load_server_config(server_name)

    def _get_config_from_db(self, server_name: str) -> MCPServerConfig | None:
        """Get a config from database (legacy mode)."""
        try:
            from db import SessionLocal

            with SessionLocal() as session:
                result = session.execute(
                    "SELECT name, source_type, display_name, source_url, transport, "
                    "command, args, cwd, env, endpoint_url, docker_config, enabled, "
                    "status, last_error, tool_count, tools_json, installed_by "
                    f"FROM mcp_servers WHERE name = :name",
                    {"name": server_name},
                ).first()

                if not result:
                    return None

                return MCPServerConfig(
                    name=result[0],
                    source_type=result[1],
                    display_name=result[2],
                    source_url=result[3],
                    transport=result[4] or "stdio",
                    command=result[5],
                    args=result[6] if isinstance(result[6], list) else [],
                    cwd=result[7],
                    env=result[8] if isinstance(result[8], dict) else {},
                    endpoint_url=result[9],
                    docker_config=result[10] if isinstance(result[10], dict) else {},
                    enabled=result[11],
                    status=result[12] or "stopped",
                    last_error=result[13],
                    tool_count=result[14] or 0,
                    installed_by=result[16],
                )
        except Exception as e:
            logger.warning(f"[MCP] Database lookup failed: {e}")
            return load_server_config(server_name)

    def _save_config(self, config: MCPServerConfig) -> bool:
        """Save a server config to storage."""
        # Update in-memory cache
        self._configs[config.name] = config

        if USE_DATABASE:
            return self._save_config_to_db(config)
        return save_server_config(config)

    def _save_config_to_db(self, config: MCPServerConfig) -> bool:
        """Save config to database (legacy mode)."""
        try:
            from db import SessionLocal

            with SessionLocal() as session:
                # Upsert using raw SQL
                session.execute(
                    """
                    INSERT INTO mcp_servers (name, source_type, display_name, source_url,
                        transport, command, args, cwd, env, endpoint_url, docker_config,
                        enabled, status, last_error, tool_count, tools_json, installed_by)
                    VALUES (:name, :source_type, :display_name, :source_url,
                        :transport, :command, :args, :cwd, :env, :endpoint_url, :docker_config,
                        :enabled, :status, :last_error, :tool_count, :tools_json, :installed_by)
                    ON CONFLICT (name) DO UPDATE SET
                        source_type = :source_type, display_name = :display_name,
                        source_url = :source_url, transport = :transport, command = :command,
                        args = :args, cwd = :cwd, env = :env, endpoint_url = :endpoint_url,
                        docker_config = :docker_config, enabled = :enabled, status = :status,
                        last_error = :last_error, tool_count = :tool_count, tools_json = :tools_json,
                        installed_by = :installed_by, updated_at = CURRENT_TIMESTAMP
                    """,
                    {
                        "name": config.name,
                        "source_type": config.source_type,
                        "display_name": config.display_name,
                        "source_url": config.source_url,
                        "transport": config.transport,
                        "command": config.command,
                        "args": config.args,
                        "cwd": config.cwd,
                        "env": config.env,
                        "endpoint_url": config.endpoint_url,
                        "docker_config": config.docker_config,
                        "enabled": config.enabled,
                        "status": config.status,
                        "last_error": config.last_error,
                        "tool_count": config.tool_count,
                        "tools_json": config.tools,
                        "installed_by": config.installed_by,
                    },
                )
                session.commit()
                return True
        except Exception as e:
            logger.warning(f"[MCP] Database save failed, using JSON fallback: {e}")
            return save_server_config(config)

    def _delete_config(self, server_name: str) -> bool:
        """Delete a server config from storage."""
        # Remove from in-memory cache
        self._configs.pop(server_name, None)

        if USE_DATABASE:
            return self._delete_config_from_db(server_name)
        return delete_server_config(server_name)

    def _delete_config_from_db(self, server_name: str) -> bool:
        """Delete config from database (legacy mode)."""
        try:
            from db import SessionLocal

            with SessionLocal() as session:
                session.execute(
                    "DELETE FROM mcp_servers WHERE name = :name", {"name": server_name}
                )
                session.commit()
                return True
        except Exception as e:
            logger.warning(f"[MCP] Database delete failed: {e}")
            return delete_server_config(server_name)

    def _list_all_configs(self) -> list[MCPServerConfig]:
        """List all server configs (enabled and disabled)."""
        if USE_DATABASE:
            return self._list_all_configs_from_db()
        return list_server_configs()

    def _list_all_configs_from_db(self) -> list[MCPServerConfig]:
        """List all configs from database."""
        try:
            from db import SessionLocal

            with SessionLocal() as session:
                result = session.execute(
                    "SELECT name, source_type, display_name, source_url, transport, "
                    "command, args, cwd, env, endpoint_url, docker_config, enabled, "
                    "status, last_error, tool_count, tools_json, installed_by "
                    "FROM mcp_servers"
                )

                configs = []
                for row in result:
                    config = MCPServerConfig(
                        name=row[0],
                        source_type=row[1],
                        display_name=row[2],
                        source_url=row[3],
                        transport=row[4] or "stdio",
                        command=row[5],
                        args=row[6] if isinstance(row[6], list) else [],
                        cwd=row[7],
                        env=row[8] if isinstance(row[8], dict) else {},
                        endpoint_url=row[9],
                        docker_config=row[10] if isinstance(row[10], dict) else {},
                        enabled=row[11],
                        status=row[12] or "stopped",
                        last_error=row[13],
                        tool_count=row[14] or 0,
                        installed_by=row[16],
                    )
                    configs.append(config)

                return configs
        except Exception as e:
            logger.warning(f"[MCP] Database list failed: {e}")
            return list_server_configs()

    async def initialize(self) -> dict[str, bool]:
        """Initialize all enabled MCP servers.

        Returns:
            Dict mapping server names to connection success status
        """
        async with self._lock:
            if self._initialized:
                logger.debug("[MCP] Manager already initialized")
                return {name: client.is_connected for name, client in self._clients.items()}

            logger.info("[MCP] Initializing MCP server manager...")
            results = {}

            # Load all enabled servers
            configs = self._load_configs()
            logger.info(f"[MCP] Found {len(configs)} enabled MCP servers")

            for config in configs:
                self._configs[config.name] = config
                results[config.name] = await self._start_server(config)

            self._initialized = True
            logger.info(f"[MCP] Initialization complete: {sum(results.values())}/{len(results)} servers connected")
            return results

    async def _start_server(self, config: MCPServerConfig) -> bool:
        """Start a single MCP server connection.

        Args:
            config: MCPServerConfig configuration

        Returns:
            True if connection was successful
        """
        if config.name in self._clients:
            logger.warning(f"[MCP] Server '{config.name}' already running")
            return self._clients[config.name].is_connected

        logger.info(f"[MCP] Starting server '{config.name}' ({config.source_type})")

        try:
            client = MCPClient(config)
            success = await client.connect()

            if success:
                self._clients[config.name] = client
                # Update config with tool info
                await self._update_server_status(config.name, "running", client.get_tools())
                return True
            else:
                await self._update_server_status(config.name, "error", error=client.state.last_error)
                return False

        except Exception as e:
            logger.error(f"[MCP] Failed to start server '{config.name}': {e}")
            await self._update_server_status(config.name, "error", error=str(e))
            return False

    async def _update_server_status(
        self,
        server_name: str,
        status: str,
        tools: list[MCPTool] | None = None,
        error: str | None = None,
    ) -> None:
        """Update server status in storage.

        Args:
            server_name: Name of the server
            status: New status ("running", "stopped", "error")
            tools: Optional list of discovered tools
            error: Optional error message
        """
        try:
            config = self._get_config(server_name)
            if config:
                config.status = status
                if tools is not None:
                    config.set_tools([t.to_dict() for t in tools])
                if error:
                    config.last_error = error
                    config.last_error_at = utcnow_iso()
                config.updated_at = utcnow_iso()
                self._save_config(config)
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
            config = self._get_config(server_name)
            if not config:
                logger.error(f"[MCP] Server '{server_name}' not found")
                return False

            return await self._start_server(config)

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
        config = self._get_config(server_name)
        if not config:
            return False

        config.enabled = True
        self._save_config(config)

        return await self.start_server(server_name)

    async def disable_server(self, server_name: str) -> bool:
        """Disable a server and stop it.

        Args:
            server_name: Name of the server to disable

        Returns:
            True if successful
        """
        await self.stop_server(server_name)

        config = self._get_config(server_name)
        if not config:
            return False

        config.enabled = False
        self._save_config(config)

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

        # Check storage for stopped servers
        config = self._get_config(server_name)
        if config:
            return {
                "name": config.name,
                "connected": False,
                "transport": config.transport,
                "tool_count": config.tool_count,
                "tools": [t["name"] for t in config.get_tools()],
                "last_error": config.last_error,
                "status": config.status,
            }
        return None

    def get_all_server_status(self) -> list[dict[str, Any]]:
        """Get status of all known servers.

        Returns:
            List of status dicts for all servers
        """
        statuses = []

        configs = self._list_all_configs()
        for config in configs:
            client = self._clients.get(config.name)
            if client:
                statuses.append(client.get_status())
            else:
                statuses.append(
                    {
                        "name": config.name,
                        "connected": False,
                        "enabled": config.enabled,
                        "transport": config.transport,
                        "source_type": config.source_type,
                        "tool_count": config.tool_count,
                        "status": config.status,
                        "last_error": config.last_error,
                    }
                )

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
            self._configs.clear()
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

            configs = self._list_all_configs()

            # Build sets for comparison
            enabled_names = {c.name for c in configs if c.enabled}
            running_names = set(self._clients.keys())

            # Stop servers that should be stopped
            for name in running_names - enabled_names:
                logger.info(f"[MCP] Stopping disabled server '{name}'")
                client = self._clients.pop(name)
                await client.disconnect()
                results[name] = False

            # Start servers that should be running
            for config in configs:
                if config.enabled:
                    self._configs[config.name] = config
                    if config.name not in self._clients:
                        logger.info(f"[MCP] Starting newly enabled server '{config.name}'")
                        results[config.name] = await self._start_server(config)
                    else:
                        # Already running
                        results[config.name] = self._clients[config.name].is_connected

            return results

    def __len__(self) -> int:
        """Return number of connected servers."""
        return len(self._clients)

    def __contains__(self, server_name: str) -> bool:
        """Check if a server is connected."""
        return server_name in self._clients

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
        client = self._clients.get(server_name)
        if not client:
            return None

        for tool in client.get_tools():
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
