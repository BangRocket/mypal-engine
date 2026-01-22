"""MCP Management tools - Clara core tool.

Provides tools for managing MCP (Model Context Protocol) servers.
These tools are always available as core abilities, independent of MCP server status.

Tools:
- mcp_list: List all installed MCP servers and their status
- mcp_status: Get detailed status of a specific server
- mcp_tools: List tools from a specific server
- smithery_search: Search Smithery registry for MCP servers
- mcp_install: Install an MCP server (admin only)
- mcp_uninstall: Remove an MCP server (admin only)
- mcp_enable: Enable a server
- mcp_disable: Disable a server
- mcp_restart: Restart a server
- mcp_refresh: Reload all MCP servers (admin only)

Platform: Discord (requires channel context for admin checks)
"""

from __future__ import annotations

import logging
from typing import Any

from tools._base import ToolContext, ToolDef

MODULE_NAME = "mcp_management"
MODULE_VERSION = "1.0.0"

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
## MCP Server Management

You can manage MCP (Model Context Protocol) plugin servers that provide additional tools.

**Tools:**
- `mcp_list` - List all installed servers with their status
- `mcp_status` - Get detailed status of a specific server
- `mcp_tools` - List available tools from a server
- `smithery_search` - Search Smithery registry for servers to install
- `mcp_install` - Install a new server (requires admin)
- `mcp_uninstall` - Remove a server (requires admin)
- `mcp_enable` / `mcp_disable` - Toggle servers
- `mcp_restart` - Restart a running server
- `mcp_refresh` - Reload all servers from config (requires admin)

**Installing from Smithery:**
Use `smithery_search` to find servers, then install with `mcp_install` using the `smithery:` prefix.
Example: `mcp_install(source="smithery:e2b")`

**When to Use:**
- User asks about available plugins/tools
- User wants to find, install, enable, or manage MCP servers
- Troubleshooting tool availability issues

**Admin Operations:**
Install, uninstall, enable, and disable operations require Discord admin permissions
(Administrator, Manage Channels, or Clara-Admin role).
""".strip()


def _check_admin_permission(ctx: ToolContext) -> tuple[bool, str | None]:
    """Check if the user has admin permissions for MCP management.

    Returns:
        Tuple of (is_admin, error_message). Error message is None if admin.
    """
    # Check for Discord context
    member = ctx.extra.get("member")
    if member is None:
        # No member context - allow for non-Discord platforms or DMs
        return True, None

    # Check for admin permissions
    try:
        # Check guild permissions
        perms = member.guild_permissions
        if perms.administrator or perms.manage_channels:
            return True, None

        # Check for Clara-Admin role
        from db.channel_config import CLARA_ADMIN_ROLE

        for role in member.roles:
            if role.name == CLARA_ADMIN_ROLE:
                return True, None

        return False, "This operation requires Administrator, Manage Channels permission, or the Clara-Admin role."
    except Exception as e:
        logger.warning(f"[mcp_management] Error checking permissions: {e}")
        # Fail open for edge cases
        return True, None


def _get_manager():
    """Get the MCP server manager singleton."""
    from clara_core.mcp import get_mcp_manager

    return get_mcp_manager()


def _get_installer():
    """Get the MCP installer."""
    from clara_core.mcp.installer import MCPInstaller

    return MCPInstaller()


# --- Tool Handlers ---


async def mcp_list(args: dict[str, Any], ctx: ToolContext) -> str:
    """List all installed MCP servers with their status."""
    try:
        manager = _get_manager()
        statuses = manager.get_all_server_status()

        if not statuses:
            return "No MCP servers installed. Use `mcp_install` to add servers."

        lines = ["**Installed MCP Servers:**\n"]

        for s in statuses:
            status_emoji = {
                "running": "\u2705",  # Green check
                "stopped": "\u26ab",  # Black circle
                "error": "\u274c",  # Red X
            }.get(s.get("status", "stopped"), "\u2753")  # Question mark default

            enabled_text = "enabled" if s.get("enabled", False) else "disabled"
            tool_count = s.get("tool_count", 0)

            lines.append(
                f"{status_emoji} **{s['name']}** ({s.get('source_type', 'unknown')}) - "
                f"{s.get('status', 'unknown')}, {enabled_text}, {tool_count} tools"
            )

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"[mcp_management] Error listing servers: {e}")
        return f"Error listing MCP servers: {e}"


async def mcp_status(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get detailed status of a specific MCP server."""
    server_name = args.get("server_name")

    if not server_name:
        # Return overall status
        manager = _get_manager()
        connected = len(manager)
        statuses = manager.get_all_server_status()
        total = len(statuses)
        enabled = sum(1 for s in statuses if s.get("enabled", False))

        return (
            f"**MCP System Status:**\n"
            f"- Total servers: {total}\n"
            f"- Enabled: {enabled}\n"
            f"- Connected: {connected}"
        )

    try:
        manager = _get_manager()
        status = manager.get_server_status(server_name)

        if not status:
            return f"Server '{server_name}' not found."

        lines = [f"**Server: {status['name']}**\n"]

        # Basic info
        lines.append(f"- Status: {status.get('status', 'unknown')}")
        lines.append(f"- Connected: {'Yes' if status.get('connected', False) else 'No'}")
        lines.append(f"- Transport: {status.get('transport', 'unknown')}")
        lines.append(f"- Tools: {status.get('tool_count', 0)}")

        # Error info if present
        if status.get("last_error"):
            lines.append(f"- Last Error: {status['last_error']}")

        # Tool list
        tools = status.get("tools", [])
        if tools:
            lines.append(f"\n**Available Tools ({len(tools)}):**")
            for tool_name in tools[:15]:  # Cap at 15
                lines.append(f"  - {tool_name}")
            if len(tools) > 15:
                lines.append(f"  ... and {len(tools) - 15} more")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"[mcp_management] Error getting status for {server_name}: {e}")
        return f"Error getting server status: {e}"


async def mcp_tools(args: dict[str, Any], ctx: ToolContext) -> str:
    """List tools available from an MCP server."""
    server_name = args.get("server_name")

    try:
        manager = _get_manager()

        if server_name:
            # Tools from specific server
            client = manager.get_client(server_name)
            if not client:
                return f"Server '{server_name}' is not connected. Use `mcp_enable` to start it."

            tools = client.get_tools()
            if not tools:
                return f"No tools available from '{server_name}'."

            lines = [f"**Tools from {server_name} ({len(tools)}):**\n"]
            for tool in tools:
                desc = tool.description[:80] + "..." if len(tool.description) > 80 else tool.description
                lines.append(f"- **{tool.name}**: {desc}")

            return "\n".join(lines)
        else:
            # All MCP tools
            all_tools = manager.get_all_tools()
            if not all_tools:
                return "No MCP tools available. Enable some servers first."

            # Group by server
            by_server: dict[str, list] = {}
            for srv_name, tool in all_tools:
                if srv_name not in by_server:
                    by_server[srv_name] = []
                by_server[srv_name].append(tool)

            lines = [f"**All MCP Tools ({len(all_tools)} total):**\n"]
            for srv_name, tools in by_server.items():
                lines.append(f"\n**{srv_name}** ({len(tools)} tools):")
                for tool in tools[:5]:
                    lines.append(f"  - {tool.name}")
                if len(tools) > 5:
                    lines.append(f"  ... and {len(tools) - 5} more")

            return "\n".join(lines)

    except Exception as e:
        logger.error(f"[mcp_management] Error listing tools: {e}")
        return f"Error listing tools: {e}"


async def mcp_install(args: dict[str, Any], ctx: ToolContext) -> str:
    """Install an MCP server from npm, GitHub, Docker, or local path."""
    # Check admin permission
    is_admin, error = _check_admin_permission(ctx)
    if not is_admin:
        return f"Permission denied: {error}"

    source = args.get("source", "").strip()
    name = args.get("name")

    if not source:
        return "Error: No source specified. Provide an npm package, GitHub URL, Docker image, or local path."

    try:
        installer = _get_installer()
        result = await installer.install(
            source=source,
            name=name,
            installed_by=ctx.user_id,
        )

        if result.success:
            # Auto-start the server
            manager = _get_manager()
            server_name = result.server.name if result.server else name or "unknown"
            await manager.start_server(server_name)

            return (
                f"\u2705 Successfully installed **{server_name}**!\n"
                f"- Source: {source}\n"
                f"- Tools discovered: {result.tools_discovered}\n"
                f"- Server is now running."
            )
        else:
            return f"\u274c Installation failed: {result.error}"

    except Exception as e:
        logger.error(f"[mcp_management] Error installing {source}: {e}")
        return f"Error installing MCP server: {e}"


async def mcp_uninstall(args: dict[str, Any], ctx: ToolContext) -> str:
    """Uninstall an MCP server."""
    # Check admin permission
    is_admin, error = _check_admin_permission(ctx)
    if not is_admin:
        return f"Permission denied: {error}"

    server_name = args.get("server_name", "").strip()
    if not server_name:
        return "Error: No server name specified."

    try:
        # Stop the server first
        manager = _get_manager()
        if server_name in manager:
            await manager.stop_server(server_name)

        # Uninstall
        installer = _get_installer()
        success = await installer.uninstall(server_name)

        if success:
            return f"\u2705 Successfully uninstalled **{server_name}**."
        else:
            return f"\u274c Failed to uninstall '{server_name}'. Server may not exist."

    except Exception as e:
        logger.error(f"[mcp_management] Error uninstalling {server_name}: {e}")
        return f"Error uninstalling server: {e}"


async def mcp_enable(args: dict[str, Any], ctx: ToolContext) -> str:
    """Enable an MCP server."""
    # Check admin permission
    is_admin, error = _check_admin_permission(ctx)
    if not is_admin:
        return f"Permission denied: {error}"

    server_name = args.get("server_name", "").strip()
    if not server_name:
        return "Error: No server name specified."

    try:
        manager = _get_manager()
        success = await manager.enable_server(server_name)

        if success:
            return f"\u2705 Server **{server_name}** enabled and started."
        else:
            return f"\u274c Failed to enable '{server_name}'. Server may not exist."

    except Exception as e:
        logger.error(f"[mcp_management] Error enabling {server_name}: {e}")
        return f"Error enabling server: {e}"


async def mcp_disable(args: dict[str, Any], ctx: ToolContext) -> str:
    """Disable an MCP server."""
    # Check admin permission
    is_admin, error = _check_admin_permission(ctx)
    if not is_admin:
        return f"Permission denied: {error}"

    server_name = args.get("server_name", "").strip()
    if not server_name:
        return "Error: No server name specified."

    try:
        manager = _get_manager()
        success = await manager.disable_server(server_name)

        if success:
            return f"\u2705 Server **{server_name}** disabled and stopped."
        else:
            return f"\u274c Failed to disable '{server_name}'. Server may not exist."

    except Exception as e:
        logger.error(f"[mcp_management] Error disabling {server_name}: {e}")
        return f"Error disabling server: {e}"


async def mcp_restart(args: dict[str, Any], ctx: ToolContext) -> str:
    """Restart an MCP server."""
    # Check admin permission
    is_admin, error = _check_admin_permission(ctx)
    if not is_admin:
        return f"Permission denied: {error}"

    server_name = args.get("server_name", "").strip()
    if not server_name:
        return "Error: No server name specified."

    try:
        manager = _get_manager()
        success = await manager.restart_server(server_name)

        if success:
            return f"\u2705 Server **{server_name}** restarted successfully."
        else:
            return f"\u274c Failed to restart '{server_name}'. Server may not exist or failed to start."

    except Exception as e:
        logger.error(f"[mcp_management] Error restarting {server_name}: {e}")
        return f"Error restarting server: {e}"


async def mcp_refresh(args: dict[str, Any], ctx: ToolContext) -> str:
    """Reload all MCP server configurations and reconnect."""
    # Check admin permission
    is_admin, error = _check_admin_permission(ctx)
    if not is_admin:
        return f"Permission denied: {error}"

    try:
        manager = _get_manager()
        results = await manager.reload()

        if not results:
            return "No MCP servers configured."

        # Count results
        connected = sum(1 for v in results.values() if v)
        total = len(results)

        lines = [f"**MCP Servers Refreshed:** {connected}/{total} connected\n"]

        for name, success in sorted(results.items()):
            emoji = "\u2705" if success else "\u274c"
            status = "connected" if success else "failed"
            lines.append(f"{emoji} **{name}**: {status}")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"[mcp_management] Error refreshing servers: {e}")
        return f"Error refreshing MCP servers: {e}"


async def smithery_search(args: dict[str, Any], ctx: ToolContext) -> str:
    """Search Smithery registry for MCP servers."""
    query = args.get("query", "").strip()

    if not query:
        return "Error: No search query specified. Provide a search term like 'file system', 'github', or 'database'."

    try:
        from clara_core.mcp.installer import SmitheryClient

        client = SmitheryClient()
        result = await client.search(query, page_size=10)

        if result.error:
            return f"Search failed: {result.error}"

        if not result.servers:
            return f"No servers found for '{query}'. Try different search terms."

        lines = [f"**Smithery Search Results for '{query}':**\n"]
        lines.append(f"Found {result.total} servers (showing top {len(result.servers)}):\n")

        for server in result.servers:
            verified = " \u2713" if server.verified else ""
            uses = f" ({server.use_count} uses)" if server.use_count > 0 else ""
            desc = server.description[:80] + "..." if len(server.description) > 80 else server.description
            lines.append(f"- **{server.qualified_name}**{verified}{uses}")
            lines.append(f"  {desc}")

        lines.append('\n*Install with:* `mcp_install(source="smithery:<name>")`')

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"[mcp_management] Error searching Smithery: {e}")
        return f"Error searching Smithery: {e}"


# --- Tool Definitions ---

TOOLS = [
    ToolDef(
        name="mcp_list",
        description=(
            "List all installed MCP (Model Context Protocol) plugin servers and their status. "
            "Shows which servers are running, stopped, or have errors, and how many tools each provides."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=mcp_list,
        platforms=["discord"],
    ),
    ToolDef(
        name="mcp_status",
        description=(
            "Get detailed status of an MCP server, including connection state, available tools, "
            "and any error messages. If no server name is given, returns overall MCP system status."
        ),
        parameters={
            "type": "object",
            "properties": {
                "server_name": {
                    "type": "string",
                    "description": "Name of the server to check. Omit for overall status.",
                },
            },
            "required": [],
        },
        handler=mcp_status,
        platforms=["discord"],
    ),
    ToolDef(
        name="mcp_tools",
        description=(
            "List tools available from MCP servers. Can list tools from a specific server "
            "or all tools from all connected servers."
        ),
        parameters={
            "type": "object",
            "properties": {
                "server_name": {
                    "type": "string",
                    "description": "Name of the server to list tools from. Omit for all servers.",
                },
            },
            "required": [],
        },
        handler=mcp_tools,
        platforms=["discord"],
    ),
    ToolDef(
        name="smithery_search",
        description=(
            "Search the Smithery registry for MCP servers. Returns a list of available servers "
            "matching your query. Use this to discover servers before installing with mcp_install."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (e.g., 'file system', 'github', 'database', 'web')",
                },
            },
            "required": ["query"],
        },
        handler=smithery_search,
        platforms=["discord"],
    ),
    ToolDef(
        name="mcp_install",
        description=(
            "Install an MCP server from Smithery, npm, GitHub, Docker, or local path. "
            "Examples: 'smithery:e2b', '@modelcontextprotocol/server-everything', 'github.com/user/repo', "
            "'ghcr.io/user/server:latest', '/path/to/local/server'. "
            "Use smithery_search to find servers first. Requires admin permission."
        ),
        parameters={
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "Source to install from (smithery:<name>, npm package, GitHub URL, Docker image, or local path)",
                },
                "name": {
                    "type": "string",
                    "description": "Optional custom name for the server (auto-detected if not provided)",
                },
            },
            "required": ["source"],
        },
        handler=mcp_install,
        platforms=["discord"],
        requires=["admin"],
    ),
    ToolDef(
        name="mcp_uninstall",
        description=(
            "Uninstall an MCP server. Stops the server and removes its configuration. " "Requires admin permission."
        ),
        parameters={
            "type": "object",
            "properties": {
                "server_name": {
                    "type": "string",
                    "description": "Name of the server to uninstall",
                },
            },
            "required": ["server_name"],
        },
        handler=mcp_uninstall,
        platforms=["discord"],
        requires=["admin"],
    ),
    ToolDef(
        name="mcp_enable",
        description=(
            "Enable an MCP server and start it. The server will reconnect and its tools "
            "will become available. Requires admin permission."
        ),
        parameters={
            "type": "object",
            "properties": {
                "server_name": {
                    "type": "string",
                    "description": "Name of the server to enable",
                },
            },
            "required": ["server_name"],
        },
        handler=mcp_enable,
        platforms=["discord"],
        requires=["admin"],
    ),
    ToolDef(
        name="mcp_disable",
        description=(
            "Disable an MCP server. Stops the server and its tools will not be available "
            "until re-enabled. Requires admin permission."
        ),
        parameters={
            "type": "object",
            "properties": {
                "server_name": {
                    "type": "string",
                    "description": "Name of the server to disable",
                },
            },
            "required": ["server_name"],
        },
        handler=mcp_disable,
        platforms=["discord"],
        requires=["admin"],
    ),
    ToolDef(
        name="mcp_restart",
        description=(
            "Restart an MCP server. Useful for applying configuration changes or recovering "
            "from errors. Requires admin permission."
        ),
        parameters={
            "type": "object",
            "properties": {
                "server_name": {
                    "type": "string",
                    "description": "Name of the server to restart",
                },
            },
            "required": ["server_name"],
        },
        handler=mcp_restart,
        platforms=["discord"],
        requires=["admin"],
    ),
    ToolDef(
        name="mcp_refresh",
        description=(
            "Reload all MCP server configurations and reconnect to all enabled servers. "
            "Stops servers that were disabled and starts newly enabled ones. "
            "Useful after modifying configs or when servers need reconnection. Requires admin permission."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=mcp_refresh,
        platforms=["discord"],
        requires=["admin"],
    ),
]


# --- Lifecycle Hooks ---


async def initialize() -> None:
    """Initialize MCP management module."""
    logger.info("[mcp_management] MCP management core tools initialized")


async def cleanup() -> None:
    """Cleanup on module unload."""
    pass
