"""MCP Server Management Tools.

Provides tools for installing, managing, and using MCP (Model Context Protocol)
plugin servers. Similar to Claude Code's /plugins command.

Tools:
- mcp_install: Install an MCP server from npm, GitHub, Docker, or local path
- mcp_uninstall: Remove an installed MCP server
- mcp_list: List all installed MCP servers and their tools
- mcp_enable: Enable a disabled server
- mcp_disable: Disable a server without uninstalling
- mcp_restart: Restart a running server
- mcp_status: Get detailed status of a specific server
"""

from __future__ import annotations

import json
from typing import Any

from ._base import ToolContext, ToolDef

MODULE_NAME = "mcp_management"
MODULE_VERSION = "1.0.0"

SYSTEM_PROMPT = """
## MCP Plugin System
You can install and use MCP (Model Context Protocol) servers to extend your capabilities
with additional tools from external sources.

**Management Tools:**
- `mcp_install` - Install a server from npm, GitHub, Docker, or local path
- `mcp_uninstall` - Remove an installed server
- `mcp_list` - List all installed servers and their tools
- `mcp_enable`/`mcp_disable` - Enable or disable a server
- `mcp_restart` - Restart a server
- `mcp_status` - Get detailed status of a server

**Installed MCP Tools:**
MCP tools are automatically available once their server is installed and running.
They use the naming convention: `{server_name}__{tool_name}`

**Examples:**
- Install npm package: `mcp_install("@modelcontextprotocol/server-everything")`
- Install from GitHub: `mcp_install("github.com/user/mcp-server")`
- Call an MCP tool: `everything__echo({"message": "Hello"})`
""".strip()


# --- Lazy imports to avoid circular dependencies ---


def _get_installer():
    """Get the MCP installer instance."""
    from clara_core.mcp import MCPInstaller

    return MCPInstaller()


def _get_manager():
    """Get the MCP server manager instance."""
    from clara_core.mcp import get_mcp_manager

    return get_mcp_manager()


def _get_adapter():
    """Get the MCP registry adapter instance."""
    from clara_core.mcp import get_mcp_adapter

    return get_mcp_adapter()


def _check_admin_permission(ctx: ToolContext) -> bool:
    """Check if user has admin permission for MCP operations.

    For Discord, requires admin/manage_channels permission or Clara-Admin role.
    For other platforms or DMs, returns True (no restriction).
    """
    if ctx.platform != "discord":
        return True

    # Get Discord channel from context
    channel = ctx.extra.get("channel")
    if not channel:
        return True  # No channel context, allow

    # DMs don't have guild, allow by default
    guild = getattr(channel, "guild", None)
    if not guild:
        return True

    # Get the message author from the bot reference
    bot = ctx.extra.get("bot")
    if not bot:
        return True  # Can't check without bot reference

    # Try to get the member
    try:
        member = guild.get_member(int(ctx.user_id))
        if not member:
            return False

        # Check permissions
        if member.guild_permissions.administrator:
            return True
        if member.guild_permissions.manage_channels:
            return True

        # Check for Clara-Admin role
        for role in member.roles:
            if role.name == "Clara-Admin":
                return True

        return False
    except Exception:
        return True  # On error, allow (fail open for non-Discord)


# --- Tool Handlers ---


async def mcp_install(args: dict[str, Any], ctx: ToolContext) -> str:
    """Install an MCP server from various sources.

    Supports:
    - npm packages: @modelcontextprotocol/server-everything
    - GitHub repos: github.com/user/repo or user/repo
    - Docker images: ghcr.io/user/image:tag
    - Local paths: /path/to/mcp-server
    """
    # Check admin permission for installation
    if not _check_admin_permission(ctx):
        return (
            "Error: MCP server installation requires administrator, manage_channels permission, "
            "or the Clara-Admin role. Please ask a server admin to install MCP servers."
        )

    source = args.get("source", "").strip()
    if not source:
        return "Error: No source provided. Specify an npm package, GitHub URL, Docker image, or local path."

    name = args.get("name")
    env_str = args.get("env", "")

    # Parse environment variables if provided
    env = None
    if env_str:
        try:
            env = json.loads(env_str) if env_str.startswith("{") else None
            if env is None:
                # Parse KEY=VALUE format
                env = {}
                for pair in env_str.split(","):
                    if "=" in pair:
                        k, v = pair.split("=", 1)
                        env[k.strip()] = v.strip()
        except json.JSONDecodeError:
            return "Error: Invalid env format. Use JSON object or KEY=VALUE,KEY2=VALUE2 format."

    installer = _get_installer()
    result = await installer.install(
        source=source,
        name=name,
        env=env,
        installed_by=ctx.user_id,
    )

    if result.success:
        # Start the server and sync tools
        manager = _get_manager()
        adapter = _get_adapter()

        if result.server:
            # Server was saved, try to start it
            await manager.start_server(result.server.name)

            # Sync tools to registry
            if adapter:
                await adapter.register_server_tools(result.server.name)

            tool_names = [f"- {result.server.name}__{t['name']}" for t in result.server.get_tools()]
            tools_str = "\n".join(tool_names[:10])
            if len(tool_names) > 10:
                tools_str += f"\n... and {len(tool_names) - 10} more"

            return (
                f"Successfully installed MCP server '{result.server.name}'!\n\n"
                f"Source: {result.server.source_type}\n"
                f"Transport: {result.server.transport}\n"
                f"Tools discovered: {result.tools_discovered}\n\n"
                f"Available tools:\n{tools_str}"
            )

        return f"Server installed with {result.tools_discovered} tools discovered."

    return f"Installation failed: {result.error}"


async def mcp_uninstall(args: dict[str, Any], ctx: ToolContext) -> str:
    """Uninstall an MCP server."""
    # Check admin permission
    if not _check_admin_permission(ctx):
        return "Error: MCP server uninstallation requires admin permissions."

    server_name = args.get("name", "").strip()
    if not server_name:
        return "Error: No server name provided."

    # Stop the server first
    manager = _get_manager()
    adapter = _get_adapter()

    if server_name in manager:
        await manager.stop_server(server_name)
        if adapter:
            adapter.unregister_server_tools(server_name)

    # Uninstall
    installer = _get_installer()
    if await installer.uninstall(server_name):
        return f"Successfully uninstalled MCP server '{server_name}'."
    else:
        return f"Server '{server_name}' not found."


async def mcp_list(args: dict[str, Any], ctx: ToolContext) -> str:
    """List all installed MCP servers and their tools."""
    installer = _get_installer()
    servers = installer.list_installed()

    if not servers:
        return (
            "No MCP servers installed.\n\n"
            "Install one with mcp_install:\n"
            "- npm: mcp_install(source='@modelcontextprotocol/server-everything')\n"
            "- GitHub: mcp_install(source='github.com/user/mcp-server')\n"
            "- Local: mcp_install(source='/path/to/server')"
        )

    # Get detailed status from manager
    manager = _get_manager()

    lines = ["**Installed MCP Servers:**\n"]
    for server in servers:
        status = "🟢 running" if server["name"] in manager else "🔴 stopped"
        if not server["enabled"]:
            status = "⚪ disabled"

        lines.append(f"**{server['name']}** ({server['source_type']})")
        lines.append(f"  Status: {status}")
        lines.append(f"  Source: {server['source_url']}")
        lines.append(f"  Tools: {server['tool_count']}")

        # Show tool names if connected
        client = manager.get_client(server["name"])
        if client and client.is_connected:
            tool_names = client.get_tool_names()[:5]
            if tool_names:
                lines.append(f"  Available: {', '.join(tool_names)}" + ("..." if len(tool_names) == 5 else ""))

        lines.append("")

    return "\n".join(lines)


async def mcp_enable(args: dict[str, Any], ctx: ToolContext) -> str:
    """Enable a disabled MCP server and start it."""
    # Check admin permission
    if not _check_admin_permission(ctx):
        return "Error: Enabling MCP servers requires admin permissions."

    server_name = args.get("name", "").strip()
    if not server_name:
        return "Error: No server name provided."

    manager = _get_manager()
    adapter = _get_adapter()

    success = await manager.enable_server(server_name)
    if success:
        # Sync tools
        if adapter:
            await adapter.register_server_tools(server_name)
        return f"Server '{server_name}' enabled and started."
    else:
        return f"Failed to enable server '{server_name}'. Check if it exists."


async def mcp_disable(args: dict[str, Any], ctx: ToolContext) -> str:
    """Disable an MCP server without uninstalling it."""
    # Check admin permission
    if not _check_admin_permission(ctx):
        return "Error: Disabling MCP servers requires admin permissions."

    server_name = args.get("name", "").strip()
    if not server_name:
        return "Error: No server name provided."

    manager = _get_manager()
    adapter = _get_adapter()

    # Unregister tools first
    if adapter:
        adapter.unregister_server_tools(server_name)

    success = await manager.disable_server(server_name)
    if success:
        return f"Server '{server_name}' disabled. Use mcp_enable to re-enable it."
    else:
        return f"Failed to disable server '{server_name}'. Check if it exists."


async def mcp_restart(args: dict[str, Any], ctx: ToolContext) -> str:
    """Restart an MCP server."""
    # Check admin permission
    if not _check_admin_permission(ctx):
        return "Error: Restarting MCP servers requires admin permissions."

    server_name = args.get("name", "").strip()
    if not server_name:
        return "Error: No server name provided."

    manager = _get_manager()
    adapter = _get_adapter()

    # Unregister old tools
    if adapter:
        adapter.unregister_server_tools(server_name)

    success = await manager.restart_server(server_name)
    if success:
        # Re-register tools
        if adapter:
            await adapter.register_server_tools(server_name)
        return f"Server '{server_name}' restarted successfully."
    else:
        return f"Failed to restart server '{server_name}'."


async def mcp_status(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get detailed status of a specific MCP server."""
    server_name = args.get("name", "").strip()
    if not server_name:
        # Return overview of all servers
        manager = _get_manager()
        statuses = manager.get_all_server_status()

        if not statuses:
            return "No MCP servers configured."

        lines = ["**MCP Server Status:**\n"]
        for s in statuses:
            status_icon = "🟢" if s.get("connected") else "🔴"
            lines.append(f"{status_icon} **{s['name']}**: {s.get('status', 'unknown')}")
            if s.get("last_error"):
                lines.append(f"   Error: {s['last_error'][:100]}")
            lines.append(f"   Tools: {s.get('tool_count', 0)}")

        return "\n".join(lines)

    manager = _get_manager()
    status = manager.get_server_status(server_name)

    if not status:
        return f"Server '{server_name}' not found."

    lines = [
        f"**MCP Server: {server_name}**\n",
        f"Connected: {'✓' if status.get('connected') else '✗'}",
        f"Transport: {status.get('transport', 'unknown')}",
        f"Tool Count: {status.get('tool_count', 0)}",
    ]

    if status.get("tools"):
        lines.append("\nAvailable Tools:")
        for tool in status["tools"][:20]:
            lines.append(f"  - {server_name}__{tool}")
        if len(status.get("tools", [])) > 20:
            lines.append(f"  ... and {len(status['tools']) - 20} more")

    if status.get("last_error"):
        lines.append(f"\nLast Error: {status['last_error']}")

    return "\n".join(lines)


# --- Tool Definitions ---

TOOLS = [
    ToolDef(
        name="mcp_install",
        description=(
            "Install an MCP (Model Context Protocol) server from various sources. "
            "Supports npm packages (e.g., @modelcontextprotocol/server-everything), "
            "GitHub repos (e.g., github.com/user/repo), Docker images, or local paths. "
            "Once installed, the server's tools become available automatically."
        ),
        parameters={
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": (
                        "Source to install from: npm package name, GitHub URL/path, "
                        "Docker image, or local filesystem path"
                    ),
                },
                "name": {
                    "type": "string",
                    "description": "Optional custom name for the server (auto-generated if not provided)",
                },
                "env": {
                    "type": "string",
                    "description": (
                        "Optional environment variables as JSON object or KEY=VALUE,KEY2=VALUE2 format"
                    ),
                },
            },
            "required": ["source"],
        },
        handler=mcp_install,
    ),
    ToolDef(
        name="mcp_uninstall",
        description="Uninstall an MCP server and remove all its tools.",
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the MCP server to uninstall",
                },
            },
            "required": ["name"],
        },
        handler=mcp_uninstall,
    ),
    ToolDef(
        name="mcp_list",
        description=(
            "List all installed MCP servers with their status and available tools. "
            "Shows whether each server is running, stopped, or disabled."
        ),
        parameters={
            "type": "object",
            "properties": {},
        },
        handler=mcp_list,
    ),
    ToolDef(
        name="mcp_enable",
        description="Enable a disabled MCP server and start it.",
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the MCP server to enable",
                },
            },
            "required": ["name"],
        },
        handler=mcp_enable,
    ),
    ToolDef(
        name="mcp_disable",
        description="Disable an MCP server without uninstalling it. The server can be re-enabled later.",
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the MCP server to disable",
                },
            },
            "required": ["name"],
        },
        handler=mcp_disable,
    ),
    ToolDef(
        name="mcp_restart",
        description="Restart a running MCP server. Useful if the server is unresponsive or after configuration changes.",
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the MCP server to restart",
                },
            },
            "required": ["name"],
        },
        handler=mcp_restart,
    ),
    ToolDef(
        name="mcp_status",
        description=(
            "Get detailed status of an MCP server including connection state, "
            "available tools, and any errors. If no name is provided, shows status of all servers."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the MCP server (optional, shows all if not provided)",
                },
            },
        },
        handler=mcp_status,
    ),
]


# --- Lifecycle Hooks ---


async def initialize() -> None:
    """Initialize MCP management module."""
    print("[mcp_management] MCP management tools loaded")


async def cleanup() -> None:
    """Cleanup on module unload."""
    pass
