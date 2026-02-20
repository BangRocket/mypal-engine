"""MCP Management tools - Clara core tool.

Provides tools for managing MCP (Model Context Protocol) servers.
These tools are always available as core abilities, independent of MCP server status.

Supports two server types:
- Local servers: Run as subprocesses (stdio transport), support hot reload
- Remote servers: Connect via HTTP transport, use standard MCP config format

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
- mcp_hot_reload: Enable/disable hot reload for local servers
- mcp_refresh: Reload all MCP servers (admin only)

Platform: Discord (requires channel context for admin checks)
"""

from __future__ import annotations

import logging
from typing import Any

from mypalclara.tools._base import ToolContext, ToolDef

MODULE_NAME = "mcp_management"
MODULE_VERSION = "1.0.0"

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
## MCP Server Management

You can manage MCP (Model Context Protocol) plugin servers that provide additional tools.

**Server Types:**
- **Local servers**: Run as subprocesses, stored in `.mcp_servers/local/`
- **Remote servers**: Connect via HTTP, stored in `.mcp_servers/remote/`

**Tools:**
- `mcp_list` - List all installed servers with their status
- `mcp_status` - Get detailed status of a specific server
- `mcp_tools` - List available tools from a server
- `smithery_search` - Search Smithery registry for servers to install
- `mcp_install` - Install a new server (requires admin)
- `mcp_uninstall` - Remove a server (requires admin)
- `mcp_enable` / `mcp_disable` - Toggle servers
- `mcp_restart` - Restart a running server
- `mcp_hot_reload` - Enable/disable hot reload for local servers
- `mcp_refresh` - Reload all servers from config (requires admin)

**Installing from Smithery:**
Use `smithery_search` to find servers, then install with `mcp_install` using the `smithery:` prefix.
- Local mode (runs server locally): `mcp_install(source="smithery:e2b")`
- Hosted mode (Smithery infrastructure): `mcp_install(source="smithery-hosted:@smithery/notion")`

**Hosted Smithery Servers (OAuth):**
Hosted servers run on Smithery's infrastructure and may require OAuth authentication.
- `mcp_oauth_start` - Start OAuth flow (returns auth URL)
- `mcp_oauth_complete` - Complete OAuth with authorization code
- `mcp_oauth_status` - Check OAuth status of a server
- `mcp_oauth_set_token` - Manually set an access token

**Hot Reload (Local Servers):**
Local servers can be configured to automatically restart when their source files change.
- `mcp_hot_reload(server_name="name", enable=True)` - Enable watching for file changes

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
        from mypalclara.db.channel_config import CLARA_ADMIN_ROLE

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
    from mypalclara.core.mcp import get_mcp_manager

    return get_mcp_manager()


def _get_installer():
    """Get the MCP installer."""
    from mypalclara.core.mcp.installer import MCPInstaller

    return MCPInstaller()


# --- Tool Handlers ---


async def mcp_list(args: dict[str, Any], ctx: ToolContext) -> str:
    """List all installed MCP servers with their status."""
    try:
        manager = _get_manager()
        statuses = manager.get_all_server_status()

        if not statuses:
            return "No MCP servers installed. Use `mcp_install` to add servers."

        # Separate local and remote servers
        local_servers = [s for s in statuses if s.get("type") == "local"]
        remote_servers = [s for s in statuses if s.get("type") == "remote"]

        lines = ["**Installed MCP Servers:**\n"]

        def format_server(s: dict) -> str:
            status_emoji = {
                "running": "\u2705",  # Green check
                "stopped": "\u26ab",  # Black circle
                "error": "\u274c",  # Red X
                "pending_auth": "\U0001f511",  # Key emoji
            }.get(s.get("status", "stopped"), "\u2753")

            enabled_text = "enabled" if s.get("enabled", False) else "disabled"
            tool_count = s.get("tool_count", 0)
            hot_reload = " 🔄" if s.get("hot_reload") else ""

            return (
                f"{status_emoji} **{s['name']}** ({s.get('source_type', 'unknown')}) - "
                f"{s.get('status', 'unknown')}, {enabled_text}, {tool_count} tools{hot_reload}"
            )

        if local_servers:
            lines.append("**Local Servers:**")
            for s in local_servers:
                lines.append(format_server(s))

        if remote_servers:
            if local_servers:
                lines.append("")
            lines.append("**Remote Servers:**")
            for s in remote_servers:
                lines.append(format_server(s))

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
        local_count = sum(1 for s in statuses if s.get("type") == "local")
        remote_count = sum(1 for s in statuses if s.get("type") == "remote")

        return (
            f"**MCP System Status:**\n"
            f"- Total servers: {total} ({local_count} local, {remote_count} remote)\n"
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
        server_type = status.get("type", "unknown")
        lines.append(f"- Type: {server_type}")
        lines.append(f"- Status: {status.get('status', 'unknown')}")
        lines.append(f"- Connected: {'Yes' if status.get('connected', False) else 'No'}")
        lines.append(f"- Enabled: {'Yes' if status.get('enabled', False) else 'No'}")
        lines.append(f"- Tools: {status.get('tool_count', 0)}")

        # Type-specific info
        if server_type == "local":
            lines.append(f"- Command: {status.get('command', 'N/A')}")
            lines.append(f"- Hot Reload: {'Yes' if status.get('hot_reload') else 'No'}")
        elif server_type == "remote":
            lines.append(f"- Server URL: {status.get('server_url', 'N/A')}")
            if status.get("oauth_required"):
                lines.append("- OAuth: Required")

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
            status = manager.get_server_status(server_name)
            if not status:
                return f"Server '{server_name}' not found."

            if not status.get("connected", False):
                return f"Server '{server_name}' is not connected. Use `mcp_enable` to start it."

            # Get tools from the server
            tools = []
            for srv_name, tool in manager.get_all_tools():
                if srv_name == server_name:
                    tools.append(tool)

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
            # Determine server name from result
            server_name = name or "unknown"
            if result.local_config:
                server_name = result.local_config.name
            elif result.remote_config:
                server_name = result.remote_config.name

            # Auto-start the server (unless pending auth)
            manager = _get_manager()
            server_type = result.server_type

            if result.remote_config and result.remote_config.status == "pending_auth":
                return (
                    f"\u2705 Successfully installed **{server_name}** ({server_type})!\n"
                    f"- Source: {source}\n"
                    f"- OAuth required. Use `mcp_oauth_start` to authorize."
                )

            await manager.start_server(server_name)

            return (
                f"\u2705 Successfully installed **{server_name}** ({server_type})!\n"
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
        from mypalclara.core.mcp.installer import SmitheryClient

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
        lines.append('*For hosted:* `mcp_install(source="smithery-hosted:<name>")`')

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"[mcp_management] Error searching Smithery: {e}")
        return f"Error searching Smithery: {e}"


# --- OAuth Tools ---


async def mcp_oauth_start(args: dict[str, Any], ctx: ToolContext) -> str:
    """Start OAuth authorization flow for a hosted Smithery server."""
    server_name = args.get("server_name", "").strip()
    redirect_uri = args.get("redirect_uri", "").strip()

    if not server_name:
        return "Error: No server name specified."

    # Default redirect URI - could be customized per deployment
    if not redirect_uri:
        import os

        api_url = os.getenv("CLARA_API_URL", "")
        if api_url:
            redirect_uri = f"{api_url}/oauth/mcp/callback"
        else:
            # Use a simple "out of band" redirect for manual code entry
            redirect_uri = "urn:ietf:wg:oauth:2.0:oob"

    try:
        from mypalclara.core.mcp.models import load_remote_server_config
        from mypalclara.core.mcp.oauth import SmitheryOAuthClient

        # Check if server exists and is smithery-hosted
        config = load_remote_server_config(server_name)
        if not config:
            return f"Server '{server_name}' not found or is not a remote server. Install it first with mcp_install."

        if config.source_type != "smithery-hosted":
            return f"Server '{server_name}' is not a hosted Smithery server. OAuth is only needed for smithery-hosted servers."

        # Create OAuth client
        oauth_client = SmitheryOAuthClient(server_name, config.server_url)

        # Start the OAuth flow
        auth_url = await oauth_client.start_oauth_flow(redirect_uri)

        if not auth_url:
            return f"Failed to start OAuth flow for '{server_name}'. Check logs for details."

        # Register the callback for automatic processing
        if redirect_uri != "urn:ietf:wg:oauth:2.0:oob" and oauth_client._state:
            from mypalclara.core.mcp.oauth_callback import register_callback

            register_callback(
                state_token=oauth_client._state.state,
                server_name=server_name,
                user_id=ctx.user_id if hasattr(ctx, "user_id") else None,
                redirect_uri=redirect_uri,
            )

        # Return instructions
        lines = [
            f"**OAuth Authorization for {server_name}**\n",
            "1. Click or copy this link to authorize:",
            f"   {auth_url}\n",
            "2. Sign in to Smithery and authorize access.",
        ]

        if redirect_uri == "urn:ietf:wg:oauth:2.0:oob":
            lines.append("3. Copy the authorization code shown after authorization.")
            lines.append(f'4. Complete with: `mcp_oauth_complete(server_name="{server_name}", code="<your-code>")`')
        else:
            lines.append("3. After authorizing, you'll be redirected back automatically.")
            lines.append("4. The connection will be completed when the callback is processed.")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"[mcp_management] OAuth start error for {server_name}: {e}")
        return f"Error starting OAuth: {e}"


async def mcp_oauth_complete(args: dict[str, Any], ctx: ToolContext) -> str:
    """Complete OAuth authorization with the code from the callback."""
    server_name = args.get("server_name", "").strip()
    code = args.get("code", "").strip()
    redirect_uri = args.get("redirect_uri", "").strip()

    if not server_name:
        return "Error: No server name specified."

    if not code:
        return "Error: No authorization code specified."

    try:
        from mypalclara.core.mcp.models import load_remote_server_config, save_remote_server_config
        from mypalclara.core.mcp.oauth import SmitheryOAuthClient

        # Check if server exists
        config = load_remote_server_config(server_name)
        if not config:
            return f"Server '{server_name}' not found or is not a remote server."

        # Default redirect URI
        if not redirect_uri:
            import os

            api_url = os.getenv("CLARA_API_URL", "")
            if api_url:
                redirect_uri = f"{api_url}/oauth/mcp/callback"
            else:
                redirect_uri = "urn:ietf:wg:oauth:2.0:oob"

        # Exchange code for tokens
        oauth_client = SmitheryOAuthClient(server_name, config.server_url)
        success = await oauth_client.exchange_code(code, redirect_uri)

        if not success:
            return "Failed to exchange authorization code. The code may be invalid or expired. Try starting the flow again with mcp_oauth_start."

        # Update server status and try to connect
        config.status = "stopped"  # Clear pending_auth status
        config.last_error = None
        save_remote_server_config(config)

        # Try to start the server
        manager = _get_manager()
        connected = await manager.start_server(server_name)

        if connected:
            status = manager.get_server_status(server_name)
            tool_count = status.get("tool_count", 0) if status else 0
            return f"✅ OAuth completed for **{server_name}**! Server connected with {tool_count} tools available."
        else:
            return f"✅ OAuth tokens saved for **{server_name}**, but server failed to connect. Check mcp_status for details."

    except Exception as e:
        logger.error(f"[mcp_management] OAuth complete error for {server_name}: {e}")
        return f"Error completing OAuth: {e}"


async def mcp_oauth_status(args: dict[str, Any], ctx: ToolContext) -> str:
    """Check OAuth status for a hosted Smithery server."""
    server_name = args.get("server_name", "").strip()

    if not server_name:
        return "Error: No server name specified."

    try:
        from mypalclara.core.mcp.models import load_remote_server_config
        from mypalclara.core.mcp.oauth import load_oauth_state

        # Check server config
        config = load_remote_server_config(server_name)
        if not config:
            return f"Server '{server_name}' not found or is not a remote server."

        if config.source_type != "smithery-hosted":
            return f"Server '{server_name}' is not a hosted Smithery server."

        # Check OAuth state
        oauth_state = load_oauth_state(server_name)

        lines = [f"**OAuth Status for {server_name}:**\n"]
        lines.append(f"- Server Type: {config.source_type}")
        lines.append(f"- Server URL: {config.server_url}")
        lines.append(f"- Server Status: {config.status}")

        if oauth_state and oauth_state.tokens:
            lines.append("- OAuth: ✅ Authorized")
            if oauth_state.tokens.expires_at:
                lines.append(f"- Token Expires: {oauth_state.tokens.expires_at}")
            if oauth_state.tokens.is_expired():
                lines.append("- Token Status: ⚠️ Expired (will auto-refresh)")
            else:
                lines.append("- Token Status: ✅ Valid")
        else:
            lines.append("- OAuth: ❌ Not authorized")
            lines.append(f'\nUse `mcp_oauth_start(server_name="{server_name}")` to begin authorization.')

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"[mcp_management] OAuth status error for {server_name}: {e}")
        return f"Error checking OAuth status: {e}"


async def mcp_oauth_set_token(args: dict[str, Any], ctx: ToolContext) -> str:
    """Manually set an OAuth token for a hosted Smithery server."""
    # Check admin permission
    is_admin, error = _check_admin_permission(ctx)
    if not is_admin:
        return f"Permission denied: {error}"

    server_name = args.get("server_name", "").strip()
    access_token = args.get("access_token", "").strip()
    refresh_token = args.get("refresh_token", "").strip() or None

    if not server_name:
        return "Error: No server name specified."

    if not access_token:
        return "Error: No access token specified."

    try:
        from mypalclara.core.mcp.models import load_remote_server_config, save_remote_server_config
        from mypalclara.core.mcp.oauth import SmitheryOAuthClient

        # Check server config
        config = load_remote_server_config(server_name)
        if not config:
            return f"Server '{server_name}' not found or is not a remote server."

        # Set tokens manually
        oauth_client = SmitheryOAuthClient(server_name, config.server_url)
        oauth_client.set_tokens_manually(access_token, refresh_token)

        # Update server status
        config.status = "stopped"
        config.last_error = None
        save_remote_server_config(config)

        # Try to connect
        manager = _get_manager()
        connected = await manager.start_server(server_name)

        if connected:
            status = manager.get_server_status(server_name)
            tool_count = status.get("tool_count", 0) if status else 0
            return f"✅ Token set for **{server_name}**! Server connected with {tool_count} tools available."
        else:
            return f"✅ Token saved for **{server_name}**, but server failed to connect. The token may be invalid."

    except Exception as e:
        logger.error(f"[mcp_management] OAuth set token error for {server_name}: {e}")
        return f"Error setting token: {e}"


async def mcp_hot_reload(args: dict[str, Any], ctx: ToolContext) -> str:
    """Enable or disable hot reload for a local MCP server."""
    # Check admin permission
    is_admin, error = _check_admin_permission(ctx)
    if not is_admin:
        return f"Permission denied: {error}"

    server_name = args.get("server_name", "").strip()
    enable = args.get("enable", True)

    if not server_name:
        return "Error: No server name specified."

    try:
        from mypalclara.core.mcp.models import load_local_server_config

        # Check if server exists and is local
        config = load_local_server_config(server_name)
        if not config:
            return (
                f"Server '{server_name}' not found or is not a local server. Hot reload only works with local servers."
            )

        manager = _get_manager()

        if enable:
            success = await manager.hot_reload_server(server_name)
            if success:
                return f"🔄 Hot reload enabled for **{server_name}**. Server will restart when source files change."
            else:
                return f"❌ Failed to enable hot reload for '{server_name}'."
        else:
            success = await manager.disable_hot_reload(server_name)
            if success:
                return f"✅ Hot reload disabled for **{server_name}**."
            else:
                return f"❌ Failed to disable hot reload for '{server_name}'."

    except Exception as e:
        logger.error(f"[mcp_management] Hot reload error for {server_name}: {e}")
        return f"Error configuring hot reload: {e}"


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
            "Examples: 'smithery:e2b' (local), 'smithery-hosted:@smithery/notion' (hosted with OAuth), "
            "'@modelcontextprotocol/server-everything', 'github.com/user/repo', "
            "'ghcr.io/user/server:latest', '/path/to/local/server'. "
            "Use smithery_search to find servers first. Requires admin permission. "
            "Hosted servers may require OAuth authorization after install."
        ),
        parameters={
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "Source to install from (smithery:<name>, smithery-hosted:<name>, npm package, GitHub URL, Docker image, or local path)",
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
    ToolDef(
        name="mcp_hot_reload",
        description=(
            "Enable or disable hot reload for a local MCP server. When enabled, the server "
            "will automatically restart when source files change. Useful for development. "
            "Only works with local servers (not remote). Requires admin permission."
        ),
        parameters={
            "type": "object",
            "properties": {
                "server_name": {
                    "type": "string",
                    "description": "Name of the local server",
                },
                "enable": {
                    "type": "boolean",
                    "description": "True to enable hot reload, False to disable (default: True)",
                },
            },
            "required": ["server_name"],
        },
        handler=mcp_hot_reload,
        platforms=["discord"],
        requires=["admin"],
    ),
    # OAuth tools for hosted Smithery servers
    ToolDef(
        name="mcp_oauth_start",
        description=(
            "Start OAuth authorization flow for a hosted Smithery server. Returns an authorization URL "
            "that the user must visit to grant access. After authorizing, use mcp_oauth_complete with the code."
        ),
        parameters={
            "type": "object",
            "properties": {
                "server_name": {
                    "type": "string",
                    "description": "Name of the hosted Smithery server to authorize",
                },
                "redirect_uri": {
                    "type": "string",
                    "description": "Optional custom redirect URI for the OAuth callback",
                },
            },
            "required": ["server_name"],
        },
        handler=mcp_oauth_start,
        platforms=["discord"],
    ),
    ToolDef(
        name="mcp_oauth_complete",
        description=(
            "Complete OAuth authorization for a hosted Smithery server by providing the authorization code "
            "received after the user authorizes access. Call mcp_oauth_start first to get the auth URL."
        ),
        parameters={
            "type": "object",
            "properties": {
                "server_name": {
                    "type": "string",
                    "description": "Name of the hosted Smithery server",
                },
                "code": {
                    "type": "string",
                    "description": "Authorization code from the OAuth callback",
                },
                "redirect_uri": {
                    "type": "string",
                    "description": "Optional redirect URI (must match the one used in mcp_oauth_start)",
                },
            },
            "required": ["server_name", "code"],
        },
        handler=mcp_oauth_complete,
        platforms=["discord"],
    ),
    ToolDef(
        name="mcp_oauth_status",
        description=(
            "Check OAuth authorization status for a hosted Smithery server. Shows whether the server "
            "is authorized, token expiry, and current connection status."
        ),
        parameters={
            "type": "object",
            "properties": {
                "server_name": {
                    "type": "string",
                    "description": "Name of the hosted Smithery server to check",
                },
            },
            "required": ["server_name"],
        },
        handler=mcp_oauth_status,
        platforms=["discord"],
    ),
    ToolDef(
        name="mcp_oauth_set_token",
        description=(
            "Manually set an OAuth access token for a hosted Smithery server. Use this if you have "
            "a pre-obtained token (e.g., from Smithery's web interface). Requires admin permission."
        ),
        parameters={
            "type": "object",
            "properties": {
                "server_name": {
                    "type": "string",
                    "description": "Name of the hosted Smithery server",
                },
                "access_token": {
                    "type": "string",
                    "description": "OAuth access token",
                },
                "refresh_token": {
                    "type": "string",
                    "description": "Optional refresh token for token renewal",
                },
            },
            "required": ["server_name", "access_token"],
        },
        handler=mcp_oauth_set_token,
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
