"""MCP (Model Context Protocol) Plugin System for Clara.

This package provides functionality for Clara to discover, install, and use
tools from external MCP servers.

Usage:
    from clara_core.mcp import (
        MCPServerManager,
        MCPInstaller,
        init_mcp,
        get_mcp_manager,
    )

    # Initialize the MCP system at startup
    manager = await init_mcp()

    # Or get the singleton manager
    manager = get_mcp_manager()

    # Install a new server
    installer = MCPInstaller()
    result = await installer.install("@modelcontextprotocol/server-everything")

    # Call a tool
    result = await manager.call_tool("everything__echo", {"message": "Hello"})

    # Get tools in OpenAI format for API calls
    tools = manager.get_tools_openai_format()
"""

from __future__ import annotations

import logging

from .client import MCPClient, MCPTool
from .installer import InstallResult, MCPInstaller
from .manager import MCPServerManager
from .models import MCPServer

# Keep deprecated imports for backwards compatibility
from .registry_adapter import MCPRegistryAdapter, get_mcp_adapter, init_mcp_adapter

logger = logging.getLogger(__name__)

__all__ = [
    # Core classes
    "MCPClient",
    "MCPTool",
    "MCPServerManager",
    "MCPInstaller",
    "MCPServer",
    "InstallResult",
    # Initialization
    "init_mcp",
    "get_mcp_manager",
    # Deprecated (kept for backwards compatibility)
    "MCPRegistryAdapter",
    "get_mcp_adapter",
    "init_mcp_adapter",
]


# Global singleton references
_manager: MCPServerManager | None = None
_initialized: bool = False


def get_mcp_manager() -> MCPServerManager:
    """Get the global MCP server manager instance.

    Returns:
        MCPServerManager singleton
    """
    global _manager
    if _manager is None:
        _manager = MCPServerManager.get_instance()
    return _manager


async def init_mcp() -> MCPServerManager:
    """Initialize the MCP plugin system.

    This should be called at application startup. It will:
    1. Ensure clara-tools native MCP server is registered
    2. Create/get the MCPServerManager singleton
    3. Initialize all enabled MCP servers from the database

    MCP tools are now served directly through the manager's format conversion
    methods (get_tools_openai_format, get_tools_claude_format) rather than
    through the registry adapter bridge pattern.

    Returns:
        MCPServerManager singleton
    """
    global _manager, _initialized

    if _initialized:
        logger.debug("[MCP] Already initialized")
        return _manager

    logger.info("[MCP] Initializing MCP plugin system...")

    # Ensure clara-tools native MCP server is registered
    _ensure_clara_tools_server()

    # Get/create the manager
    _manager = get_mcp_manager()

    # Initialize all enabled servers
    init_results = await _manager.initialize()
    connected = sum(1 for v in init_results.values() if v)
    total = len(init_results)

    # Count total tools discovered
    tool_count = sum(len(client.get_tools()) for client in _manager._clients.values() if client.is_connected)

    logger.info(f"[MCP] Initialized {connected}/{total} servers with {tool_count} tools")

    _initialized = True
    return _manager


def _ensure_clara_tools_server() -> None:
    """Ensure the clara-tools native MCP server is registered in the database."""
    import shutil
    from pathlib import Path

    from db import SessionLocal

    # Find the clara-mcp-server binary
    binary_path = shutil.which("clara-mcp-server")
    if not binary_path:
        # Check local development paths
        project_root = Path(__file__).parent.parent.parent
        release = project_root / "clara-mcp-server" / "target" / "release" / "clara-mcp-server"
        debug = project_root / "clara-mcp-server" / "target" / "debug" / "clara-mcp-server"
        if release.exists():
            binary_path = str(release)
        elif debug.exists():
            binary_path = str(debug)

    if not binary_path:
        logger.warning("[MCP] clara-mcp-server binary not found, skipping native tools")
        return

    # Check if already registered
    with SessionLocal() as session:
        existing = session.query(MCPServer).filter(MCPServer.name == "clara-tools").first()
        if existing:
            # Update binary path if changed
            if existing.command != binary_path:
                logger.info(f"[MCP] Updating clara-tools binary path: {binary_path}")
                existing.command = binary_path
                session.commit()
            return

        # Register new server
        logger.info(f"[MCP] Registering clara-tools native MCP server: {binary_path}")
        server = MCPServer(
            name="clara-tools",
            display_name="Clara Native Tools",
            source_type="local",
            source_url=binary_path,
            transport="stdio",
            command=binary_path,
            enabled=True,
            status="stopped",
        )
        session.add(server)
        session.commit()


async def shutdown_mcp() -> None:
    """Shutdown the MCP plugin system.

    Should be called on application shutdown to cleanly disconnect
    from all MCP servers.
    """
    global _manager, _initialized

    if _manager:
        await _manager.shutdown()

    _manager = None
    _initialized = False
    logger.info("[MCP] Shutdown complete")


def is_initialized() -> bool:
    """Check if the MCP system is initialized.

    Returns:
        True if initialized
    """
    return _initialized
