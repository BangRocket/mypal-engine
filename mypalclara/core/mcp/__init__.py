"""MCP (Model Context Protocol) Plugin System for Clara.

This package provides functionality for Clara to discover, install, and use
tools from external MCP servers.

Architecture:
- Local servers: Run as subprocesses (stdio transport)
  - Stored in .mcp_servers/local/{name}/config.json
  - Supports hot reload for development
  - Includes built-in servers (rustterm, clara-mcp)

- Remote servers: Connect via HTTP transport
  - Stored in .mcp_servers/remote/{name}/config.json
  - Uses standard MCP config format
  - Supports OAuth authentication (Smithery-hosted)

Usage:
    from mypalclara.core.mcp import (
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
import os

from .installer import InstallResult, MCPInstaller, SmitheryClient, SmitherySearchResult
from .local_server import LocalServerManager, LocalServerProcess, MCPTool
from .manager import MCPServerManager
from .models import (
    MCP_SERVERS_DIR,
    LocalServerConfig,
    MCPServer,
    MCPServerConfig,
    RemoteServerConfig,
    ServerConfig,
    ServerStatus,
    ServerType,
    delete_local_server_config,
    delete_remote_server_config,
    delete_server_config,
    find_server_config,
    get_enabled_local_servers,
    get_enabled_remote_servers,
    get_enabled_servers,
    get_local_config_path,
    get_local_server_dir,
    get_local_servers_dir,
    get_remote_config_path,
    get_remote_server_dir,
    get_remote_servers_dir,
    list_all_server_configs,
    list_local_server_configs,
    list_remote_server_configs,
    list_server_configs,
    load_local_server_config,
    load_remote_server_config,
    load_server_config,
    save_local_server_config,
    save_remote_server_config,
    save_server_config,
)
from .remote_server import (
    RemoteServerConnection,
    RemoteServerManager,
    add_remote_server,
    add_remote_server_from_standard_config,
)

logger = logging.getLogger(__name__)

__all__ = [
    # Core classes
    "MCPServerManager",
    "MCPInstaller",
    "InstallResult",
    "MCPTool",
    # Local server management
    "LocalServerManager",
    "LocalServerProcess",
    "LocalServerConfig",
    # Remote server management
    "RemoteServerManager",
    "RemoteServerConnection",
    "RemoteServerConfig",
    "add_remote_server",
    "add_remote_server_from_standard_config",
    # Smithery
    "SmitheryClient",
    "SmitherySearchResult",
    # Models
    "MCPServer",  # Legacy alias
    "MCPServerConfig",  # Legacy alias
    "ServerConfig",
    "ServerType",
    "ServerStatus",
    "MCP_SERVERS_DIR",
    # Initialization
    "init_mcp",
    "shutdown_mcp",
    "is_initialized",
    "get_mcp_manager",
    # Local server config functions
    "load_local_server_config",
    "save_local_server_config",
    "delete_local_server_config",
    "list_local_server_configs",
    "get_local_server_dir",
    "get_local_servers_dir",
    "get_local_config_path",
    "get_enabled_local_servers",
    # Remote server config functions
    "load_remote_server_config",
    "save_remote_server_config",
    "delete_remote_server_config",
    "list_remote_server_configs",
    "get_remote_server_dir",
    "get_remote_servers_dir",
    "get_remote_config_path",
    "get_enabled_remote_servers",
    # Combined config functions
    "list_all_server_configs",
    "get_enabled_servers",
    "find_server_config",
    # Legacy config functions (deprecated)
    "load_server_config",
    "save_server_config",
    "delete_server_config",
    "list_server_configs",
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
    3. Initialize all enabled MCP servers from storage

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
    tool_count = len(_manager.get_all_tools())

    logger.info(f"[MCP] Initialized {connected}/{total} servers with {tool_count} tools")

    _initialized = True
    return _manager


def _ensure_clara_tools_server() -> None:
    """Ensure the clara-tools native MCP server is registered."""
    import shutil
    from pathlib import Path

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

    existing = load_local_server_config("clara-tools")

    if existing:
        # Update binary path if changed
        if existing.command != binary_path:
            logger.info(f"[MCP] Updating clara-tools binary path: {binary_path}")
            existing.command = binary_path
            save_local_server_config(existing)
        return

    # Register new server
    logger.info(f"[MCP] Registering clara-tools native MCP server: {binary_path}")
    server = LocalServerConfig(
        name="clara-tools",
        command=binary_path,
        source_type="local",
        display_name="Clara Native Tools",
        source_url=binary_path,
        enabled=True,
        status="stopped",
    )
    save_local_server_config(server)


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
