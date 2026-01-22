"""Clara Core Tools.

This module contains Clara-specific tools that are always available
regardless of MCP server configuration.

Core tools:
- chat_history: Search and retrieve Discord chat history
- system_logs: Access system logs for debugging
- mcp_management: Manage MCP servers (list, install, enable, etc.)

Also defines official MCP server configurations that replace
custom tool implementations with standardized MCP servers.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools._registry import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass
class OfficialMCPServer:
    """Configuration for an official MCP server."""

    name: str
    description: str
    npm_package: str
    env_required: list[str]  # Required environment variables
    env_optional: list[str]  # Optional environment variables
    replaces_tool: str | None  # Name of the tool module it replaces


# Official MCP servers that replace custom tool implementations
OFFICIAL_MCP_SERVERS = [
    OfficialMCPServer(
        name="github",
        description="GitHub repository, issues, PRs, and file operations",
        npm_package="@modelcontextprotocol/server-github",
        env_required=["GITHUB_TOKEN"],
        env_optional=[],
        replaces_tool="github",
    ),
    OfficialMCPServer(
        name="playwright",
        description="Browser automation using Playwright accessibility tree",
        npm_package="@playwright/mcp",
        env_required=[],
        env_optional=["PLAYWRIGHT_BROWSER"],  # chrome, firefox, webkit, msedge
        replaces_tool="playwright_browser",
    ),
    OfficialMCPServer(
        name="tavily",
        description="Web search via Tavily API",
        npm_package="tavily-mcp",
        env_required=["TAVILY_API_KEY"],
        env_optional=[],
        replaces_tool="web_search",
    ),
    OfficialMCPServer(
        name="filesystem",
        description="Secure file operations with configurable access controls",
        npm_package="@modelcontextprotocol/server-filesystem",
        env_required=[],
        env_optional=["CLARA_FILES_DIR"],  # Directories to allow access to
        replaces_tool=None,  # Doesn't replace local_files (missing S3, per-user storage, sandbox integration)
    ),
]


def get_available_official_servers() -> list[OfficialMCPServer]:
    """Get list of official MCP servers that have required env vars configured.

    Returns:
        List of OfficialMCPServer configs that can be installed
    """
    available = []
    for server in OFFICIAL_MCP_SERVERS:
        # Check if all required env vars are set
        missing = [var for var in server.env_required if not os.getenv(var)]
        if not missing:
            available.append(server)
        else:
            logger.debug(f"[core_tools] Skipping {server.name} MCP server - missing env vars: {missing}")
    return available


async def register_core_tools(registry: "ToolRegistry") -> int:
    """Register Clara's core tools with the registry.

    Args:
        registry: The tool registry to register tools with

    Returns:
        Number of tools registered
    """
    from . import chat_history, mcp_management, system_logs

    count = 0

    # Initialize and register chat_history tools
    try:
        await chat_history.initialize()
        for tool_def in chat_history.TOOLS:
            registry.register(tool_def)
            count += 1
        if chat_history.SYSTEM_PROMPT:
            registry.register_system_prompt("chat_history", chat_history.SYSTEM_PROMPT)
        logger.info(f"[core_tools] Registered {len(chat_history.TOOLS)} chat_history tools")
    except Exception as e:
        logger.warning(f"[core_tools] Failed to register chat_history: {e}")

    # Initialize and register system_logs tools
    try:
        await system_logs.initialize()
        for tool_def in system_logs.TOOLS:
            registry.register(tool_def)
            count += 1
        if system_logs.SYSTEM_PROMPT:
            registry.register_system_prompt("system_logs", system_logs.SYSTEM_PROMPT)
        logger.info(f"[core_tools] Registered {len(system_logs.TOOLS)} system_logs tools")
    except Exception as e:
        logger.warning(f"[core_tools] Failed to register system_logs: {e}")

    # Initialize and register mcp_management tools
    try:
        await mcp_management.initialize()
        for tool_def in mcp_management.TOOLS:
            registry.register(tool_def)
            count += 1
        if mcp_management.SYSTEM_PROMPT:
            registry.register_system_prompt("mcp_management", mcp_management.SYSTEM_PROMPT)
        logger.info(f"[core_tools] Registered {len(mcp_management.TOOLS)} mcp_management tools")
    except Exception as e:
        logger.warning(f"[core_tools] Failed to register mcp_management: {e}")

    return count


async def setup_official_mcp_servers() -> dict[str, bool]:
    """Configure and install official MCP servers based on environment.

    This function checks which official MCP servers can be enabled based on
    available environment variables, and ensures they are installed and running.

    Returns:
        Dict mapping server names to success status
    """
    from clara_core.mcp import get_mcp_manager
    from clara_core.mcp.installer import MCPInstaller

    results = {}
    available = get_available_official_servers()

    if not available:
        logger.info("[core_tools] No official MCP servers configured (missing env vars)")
        return results

    installer = MCPInstaller()
    manager = get_mcp_manager()

    for server_config in available:
        try:
            # Check if already installed
            installed = installer.list_installed()
            existing = next((s for s in installed if s["name"] == server_config.name), None)

            if existing:
                # Already installed, just ensure it's running
                if server_config.name not in manager:
                    success = await manager.start_server(server_config.name)
                    results[server_config.name] = success
                else:
                    results[server_config.name] = True
                continue

            # Build environment variables for the server
            env = {}
            for var in server_config.env_required + server_config.env_optional:
                value = os.getenv(var)
                if value:
                    env[var] = value

            # Special handling for server args based on type
            args = None
            if server_config.name == "filesystem":
                # Allow access to CLARA_FILES_DIR or current directory
                files_dir = os.getenv("CLARA_FILES_DIR", "./clara_files")
                args = [files_dir]

            # Install the server
            logger.info(f"[core_tools] Installing official MCP server: {server_config.name}")
            result = await installer.install(
                source=server_config.npm_package,
                name=server_config.name,
                env=env if env else None,
                args=args,
            )

            if result.success:
                # Start the server
                await manager.start_server(server_config.name)
                results[server_config.name] = True
                logger.info(f"[core_tools] Installed {server_config.name} with {result.tools_discovered} tools")
            else:
                results[server_config.name] = False
                logger.warning(f"[core_tools] Failed to install {server_config.name}: {result.error}")

        except Exception as e:
            results[server_config.name] = False
            logger.error(f"[core_tools] Error setting up {server_config.name}: {e}")

    return results


def get_replaced_tool_modules() -> list[str]:
    """Get list of tool module names that are replaced by official MCP servers.

    Returns:
        List of module names (e.g., ['github', 'azure_devops', ...])
    """
    available = get_available_official_servers()
    return [s.replaces_tool for s in available if s.replaces_tool]


__all__ = [
    "OFFICIAL_MCP_SERVERS",
    "OfficialMCPServer",
    "get_available_official_servers",
    "get_replaced_tool_modules",
    "register_core_tools",
    "setup_official_mcp_servers",
]
