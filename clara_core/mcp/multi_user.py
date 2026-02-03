"""Multi-user MCP server isolation.

Provides per-user MCP server management:
- User-scoped server registration and discovery
- Isolated tool access per user
- Global servers available to all users
- Database-backed server state

This module wraps the MCPServerManager to provide user isolation,
ensuring users can only access their own installed servers plus
any global servers.
"""

from __future__ import annotations

import logging
from typing import Any

from .local_server import MCPTool
from .manager import MCPServerManager

logger = logging.getLogger(__name__)


class UserMCPContext:
    """MCP context for a specific user.

    Provides a filtered view of MCP servers and tools that:
    - Includes servers registered by this user
    - Includes global servers (user_id=None)
    - Excludes servers registered by other users

    Usage:
        context = UserMCPContext(user_id="discord-123")
        await context.initialize()

        # Get only tools available to this user
        tools = context.get_available_tools()

        # Call a tool (validates user access)
        result = await context.call_tool("server__tool", {})
    """

    def __init__(self, user_id: str) -> None:
        """Initialize user MCP context.

        Args:
            user_id: The user ID for isolation
        """
        self.user_id = user_id
        self._manager = MCPServerManager.get_instance()
        self._user_servers: set[str] = set()
        self._global_servers: set[str] = set()
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize and load user's server associations from DB."""
        if self._initialized:
            return

        try:
            from db import SessionLocal
            from db.mcp_models import MCPServer

            db = SessionLocal()
            try:
                # Find servers for this user or global servers
                servers = (
                    db.query(MCPServer)
                    .filter(MCPServer.enabled == True)
                    .filter(
                        (MCPServer.user_id == self.user_id)
                        | (MCPServer.user_id.is_(None))
                    )
                    .all()
                )

                for server in servers:
                    if server.user_id == self.user_id:
                        self._user_servers.add(server.name)
                    else:
                        self._global_servers.add(server.name)

                logger.debug(
                    f"[MCP] User {self.user_id}: {len(self._user_servers)} user servers, "
                    f"{len(self._global_servers)} global servers"
                )

            finally:
                db.close()

        except Exception as e:
            logger.warning(f"[MCP] Failed to load user servers: {e}")
            # Fall back to allowing all connected servers
            self._global_servers = set(self._manager._clients.keys())

        self._initialized = True

    def _can_access_server(self, server_name: str) -> bool:
        """Check if user can access a server.

        Args:
            server_name: Name of the server

        Returns:
            True if user has access
        """
        if not self._initialized:
            # Not initialized - allow access but log warning
            logger.warning("[MCP] UserMCPContext not initialized, allowing access")
            return True

        # Check explicit registrations
        if server_name in self._user_servers:
            return True
        if server_name in self._global_servers:
            return True

        # Check if server exists in DB for this user
        try:
            from db import SessionLocal
            from db.mcp_models import MCPServer

            db = SessionLocal()
            try:
                exists = (
                    db.query(MCPServer)
                    .filter(MCPServer.name == server_name)
                    .filter(
                        (MCPServer.user_id == self.user_id)
                        | (MCPServer.user_id.is_(None))
                    )
                    .first()
                    is not None
                )
                return exists
            finally:
                db.close()

        except Exception:
            # DB error - allow access to avoid breaking functionality
            return True

    def get_available_tools(self) -> list[tuple[str, MCPTool]]:
        """Get tools available to this user.

        Returns:
            List of (server_name, tool) tuples for accessible servers
        """
        all_tools = self._manager.get_all_tools()
        return [
            (server_name, tool)
            for server_name, tool in all_tools
            if self._can_access_server(server_name)
        ]

    def get_namespaced_tools(self) -> dict[str, MCPTool]:
        """Get tools with namespaced names, filtered for this user.

        Returns:
            Dict mapping "server__tool" names to MCPTool objects
        """
        all_tools = self._manager.get_namespaced_tools()
        result = {}
        for namespaced_name, tool in all_tools.items():
            server_name = namespaced_name.split("__")[0]
            if self._can_access_server(server_name):
                result[namespaced_name] = tool
        return result

    def get_tools_openai_format(self) -> list[dict[str, Any]]:
        """Get tools in OpenAI format, filtered for this user.

        Returns:
            List of tool definitions in OpenAI format
        """
        all_tools = self._manager.get_tools_openai_format()
        result = []
        for tool in all_tools:
            name = tool.get("function", {}).get("name", "")
            if "__" in name:
                server_name = name.split("__")[0]
                if self._can_access_server(server_name):
                    result.append(tool)
        return result

    def get_tools_claude_format(self) -> list[dict[str, Any]]:
        """Get tools in Claude format, filtered for this user.

        Returns:
            List of tool definitions in Anthropic format
        """
        all_tools = self._manager.get_tools_claude_format()
        result = []
        for tool in all_tools:
            name = tool.get("name", "")
            if "__" in name:
                server_name = name.split("__")[0]
                if self._can_access_server(server_name):
                    result.append(tool)
        return result

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> str:
        """Call a tool if user has access.

        Args:
            tool_name: Tool name (server__tool or just tool)
            arguments: Tool arguments

        Returns:
            Tool result or error message
        """
        location = self._manager.find_tool(tool_name)
        if not location:
            return f"Error: Tool '{tool_name}' not found"

        server_name, actual_tool = location

        if not self._can_access_server(server_name):
            return (
                f"Error: Access denied to server '{server_name}'. "
                "Install the server with mcp_install first."
            )

        return await self._manager.call_tool(tool_name, arguments)

    def find_tool(self, tool_name: str) -> tuple[str, str] | None:
        """Find a tool if user has access.

        Args:
            tool_name: Tool name to find

        Returns:
            (server_name, tool_name) or None
        """
        location = self._manager.find_tool(tool_name)
        if not location:
            return None

        server_name, actual_tool = location
        if not self._can_access_server(server_name):
            return None

        return location

    def is_mcp_tool(self, tool_name: str) -> bool:
        """Check if tool is an accessible MCP tool.

        Args:
            tool_name: Tool name to check

        Returns:
            True if tool exists and user has access
        """
        return self.find_tool(tool_name) is not None

    def get_server_status(self, server_name: str) -> dict[str, Any] | None:
        """Get server status if user has access.

        Args:
            server_name: Server to check

        Returns:
            Status dict or None
        """
        if not self._can_access_server(server_name):
            return None
        return self._manager.get_server_status(server_name)

    def get_all_server_status(self) -> list[dict[str, Any]]:
        """Get status of servers available to this user.

        Returns:
            List of status dicts
        """
        all_status = self._manager.get_all_server_status()
        return [
            status
            for status in all_status
            if self._can_access_server(status.get("name", ""))
        ]


async def register_server_for_user(
    user_id: str,
    server_name: str,
    server_type: str = "local",
    source_type: str | None = None,
    source_url: str | None = None,
    installed_by: str | None = None,
) -> str:
    """Register an MCP server for a user in the database.

    Args:
        user_id: User ID (or None for global)
        server_name: Name of the server
        server_type: "local" or "remote"
        source_type: Installation source (npm, smithery, github, etc.)
        source_url: Source URL for reference
        installed_by: Who installed it

    Returns:
        Server ID
    """
    try:
        from db import SessionLocal
        from db.mcp_models import MCPServer

        db = SessionLocal()
        try:
            # Check if already exists
            existing = (
                db.query(MCPServer)
                .filter(MCPServer.name == server_name)
                .filter(
                    (MCPServer.user_id == user_id)
                    if user_id
                    else MCPServer.user_id.is_(None)
                )
                .first()
            )

            if existing:
                logger.debug(f"[MCP] Server '{server_name}' already registered for user")
                return existing.id

            # Create new registration
            server = MCPServer(
                user_id=user_id,
                name=server_name,
                server_type=server_type,
                source_type=source_type,
                source_url=source_url,
                installed_by=installed_by or user_id,
                enabled=True,
                status="pending",
            )
            db.add(server)
            db.commit()

            logger.info(
                f"[MCP] Registered server '{server_name}' for "
                f"{'global' if not user_id else f'user {user_id}'}"
            )
            return server.id

        finally:
            db.close()

    except Exception as e:
        logger.error(f"[MCP] Failed to register server: {e}")
        raise


async def unregister_server_for_user(user_id: str, server_name: str) -> bool:
    """Unregister an MCP server for a user.

    Args:
        user_id: User ID
        server_name: Name of the server

    Returns:
        True if removed, False if not found
    """
    try:
        from db import SessionLocal
        from db.mcp_models import MCPServer

        db = SessionLocal()
        try:
            server = (
                db.query(MCPServer)
                .filter(MCPServer.name == server_name)
                .filter(MCPServer.user_id == user_id)
                .first()
            )

            if not server:
                logger.debug(f"[MCP] Server '{server_name}' not found for user")
                return False

            db.delete(server)
            db.commit()

            logger.info(f"[MCP] Unregistered server '{server_name}' for user {user_id}")
            return True

        finally:
            db.close()

    except Exception as e:
        logger.error(f"[MCP] Failed to unregister server: {e}")
        return False


async def get_user_servers(user_id: str) -> list[dict[str, Any]]:
    """Get all servers available to a user.

    Args:
        user_id: User ID

    Returns:
        List of server info dicts
    """
    try:
        from db import SessionLocal
        from db.mcp_models import MCPServer

        db = SessionLocal()
        try:
            servers = (
                db.query(MCPServer)
                .filter(
                    (MCPServer.user_id == user_id)
                    | (MCPServer.user_id.is_(None))
                )
                .all()
            )

            result = []
            for server in servers:
                result.append({
                    "id": server.id,
                    "name": server.name,
                    "server_type": server.server_type,
                    "source_type": server.source_type,
                    "enabled": server.enabled,
                    "status": server.status,
                    "is_global": server.user_id is None,
                    "total_tool_calls": server.total_tool_calls or 0,
                    "last_used_at": server.last_used_at.isoformat() if server.last_used_at else None,
                })

            return result

        finally:
            db.close()

    except Exception as e:
        logger.error(f"[MCP] Failed to get user servers: {e}")
        return []


# Cache for user contexts
_user_contexts: dict[str, UserMCPContext] = {}


def get_user_mcp_context(user_id: str) -> UserMCPContext:
    """Get or create MCP context for a user.

    Args:
        user_id: User ID

    Returns:
        UserMCPContext instance
    """
    if user_id not in _user_contexts:
        _user_contexts[user_id] = UserMCPContext(user_id)
    return _user_contexts[user_id]


def clear_user_context(user_id: str) -> None:
    """Clear cached context for a user (e.g., after server changes).

    Args:
        user_id: User ID to clear
    """
    if user_id in _user_contexts:
        del _user_contexts[user_id]
