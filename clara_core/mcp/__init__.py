"""MCP (Model Context Protocol) Plugin System for Clara.

This package provides functionality for Clara to discover, install, and use
tools from external MCP servers.

Usage:
    from clara_core.mcp import (
        MCPServerManager,
        MCPInstaller,
        MCPRegistryAdapter,
        init_mcp,
        get_mcp_manager,
    )

    # Initialize the MCP system at startup
    manager, adapter = await init_mcp(registry)

    # Or get the singleton manager
    manager = get_mcp_manager()

    # Install a new server
    installer = MCPInstaller()
    result = await installer.install("@modelcontextprotocol/server-everything")

    # Call a tool
    result = await manager.call_tool("everything__echo", {"message": "Hello"})
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .client import MCPClient, MCPTool
from .installer import InstallResult, MCPInstaller
from .manager import MCPServerManager
from .models import MCPServer
from .registry_adapter import MCPRegistryAdapter, get_mcp_adapter, init_mcp_adapter

if TYPE_CHECKING:
    from tools._registry import ToolRegistry

logger = logging.getLogger(__name__)

__all__ = [
    # Core classes
    "MCPClient",
    "MCPTool",
    "MCPServerManager",
    "MCPInstaller",
    "MCPRegistryAdapter",
    "MCPServer",
    "InstallResult",
    # Initialization
    "init_mcp",
    "get_mcp_manager",
    "get_mcp_adapter",
]


# Global singleton references
_manager: MCPServerManager | None = None
_adapter: MCPRegistryAdapter | None = None
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


async def init_mcp(registry: ToolRegistry) -> tuple[MCPServerManager, MCPRegistryAdapter]:
    """Initialize the MCP plugin system.

    This should be called at application startup after the tool registry
    is initialized. It will:
    1. Create/get the MCPServerManager singleton
    2. Initialize all enabled MCP servers from the database
    3. Create the registry adapter to bridge MCP tools to Clara
    4. Register all discovered tools with the registry

    Args:
        registry: Clara's ToolRegistry instance

    Returns:
        Tuple of (MCPServerManager, MCPRegistryAdapter)
    """
    global _manager, _adapter, _initialized

    if _initialized:
        logger.debug("[MCP] Already initialized")
        return _manager, _adapter

    logger.info("[MCP] Initializing MCP plugin system...")

    # Get/create the manager
    _manager = get_mcp_manager()

    # Initialize all enabled servers
    init_results = await _manager.initialize()
    connected = sum(1 for v in init_results.values() if v)
    total = len(init_results)
    logger.info(f"[MCP] Initialized {connected}/{total} servers")

    # Create the registry adapter
    _adapter = init_mcp_adapter(_manager, registry)

    # Sync all tools to the registry
    tool_results = await _adapter.sync_all()
    tool_count = sum(len(tools) for tools in tool_results.values())
    logger.info(f"[MCP] Registered {tool_count} tools from {len(tool_results)} servers")

    _initialized = True
    return _manager, _adapter


async def shutdown_mcp() -> None:
    """Shutdown the MCP plugin system.

    Should be called on application shutdown to cleanly disconnect
    from all MCP servers.
    """
    global _manager, _adapter, _initialized

    if _manager:
        await _manager.shutdown()

    _manager = None
    _adapter = None
    _initialized = False
    logger.info("[MCP] Shutdown complete")


def is_initialized() -> bool:
    """Check if the MCP system is initialized.

    Returns:
        True if initialized
    """
    return _initialized
