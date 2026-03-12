"""Clara MCP (Model Context Protocol) Integration.

This package provides MCP client infrastructure for Clara:
- MCPClientManager: Multi-server lifecycle management
- MCPToolAdapter: Routes tool calls to MCP or legacy registry
- MCPFeatureFlags: Per-tool routing control

MCP enables Clara to use standardized tool servers instead of
the custom tools/ module, supporting both first-party servers
(GitHub, Google) and custom Clara servers.

IMPORTANT: MCP stdio transport uses stdout for JSON-RPC.
Never use print() or log to stdout in MCP server processes.
Use stderr for all logging.

Usage:
    from clara_core.mcp import MCPClientManager, MCPToolAdapter, ServerConfig

    # Create manager and connect to servers
    manager = MCPClientManager()
    await manager.connect_server(ServerConfig(
        name="local-files",
        transport=TransportType.STDIO,
        command="python",
        args=["-m", "mcp_servers.local_files"],
    ))

    # Create adapter with legacy fallback
    from tools import get_registry
    adapter = MCPToolAdapter(manager, get_registry())

    # Use adapter for tool calls
    tools = adapter.get_tools(platform="discord")
    result = await adapter.execute("local_files_save", {...}, context)
"""

__version__ = "0.1.0"

from .adapter import MCPToolAdapter
from .client_manager import (
    MCPClientManager,
    ServerConfig,
    ServerConnection,
    TransportType,
    get_or_create_event_loop,
)
from .feature_flags import (
    MCPFeatureFlags,
    get_mcp_flags,
    reset_mcp_flags,
)
from .server_configs import (
    CLAUDE_CODE_CONFIG,
    DOCKER_SANDBOX_CONFIG,
    HOST_SIDE_SERVERS,
    LOCAL_FILES_CONFIG,
    get_host_side_servers,
    get_server_config,
)

__all__ = [
    "__version__",
    # Client manager
    "MCPClientManager",
    "ServerConfig",
    "ServerConnection",
    "TransportType",
    "get_or_create_event_loop",
    # Adapter
    "MCPToolAdapter",
    # Feature flags
    "MCPFeatureFlags",
    "get_mcp_flags",
    "reset_mcp_flags",
    # Server configs
    "HOST_SIDE_SERVERS",
    "get_host_side_servers",
    "get_server_config",
    "LOCAL_FILES_CONFIG",
    "DOCKER_SANDBOX_CONFIG",
    "CLAUDE_CODE_CONFIG",
]
