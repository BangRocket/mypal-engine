"""Clara Discord slash commands.

Provides all slash commands for Clara administration and configuration.
Uses Pycord's application commands system.
"""

import logging
import os
from typing import Optional

import discord
from discord import option
from discord.ext import commands

from db import SessionLocal
from db.models import GuildConfig

from .embeds import (
    EMBED_COLOR_PRIMARY,
    create_error_embed,
    create_help_embed,
    create_info_embed,
    create_list_embed,
    create_status_embed,
    create_success_embed,
)
from .views import ConfirmView, HelpSelectView

logger = logging.getLogger(__name__)


async def safe_defer(ctx: discord.ApplicationContext) -> bool:
    """Safely defer an interaction, handling timeout errors.

    Discord interactions expire after 3 seconds. If the bot's event loop
    is busy, we might miss this window. This helper handles that gracefully.

    Args:
        ctx: The application context

    Returns:
        True if defer succeeded, False if interaction expired
    """
    try:
        await ctx.defer()
        return True
    except discord.NotFound:
        # Interaction expired (error code 10062)
        logger.warning(f"[commands] Interaction expired before defer for /{ctx.command.qualified_name}")
        return False
    except Exception as e:
        logger.warning(f"[commands] Failed to defer interaction: {e}")
        return False


def get_guild_config(guild_id: str) -> Optional[GuildConfig]:
    """Get guild configuration from database."""
    with SessionLocal() as session:
        config = session.query(GuildConfig).filter(GuildConfig.guild_id == guild_id).first()
        if config:
            session.expunge(config)
        return config


def save_guild_config(config: GuildConfig) -> None:
    """Save guild configuration to database."""
    with SessionLocal() as session:
        existing = session.query(GuildConfig).filter(GuildConfig.guild_id == config.guild_id).first()
        if existing:
            # Update existing
            existing.default_tier = config.default_tier
            existing.auto_tier_enabled = config.auto_tier_enabled
            existing.ors_enabled = config.ors_enabled
            existing.ors_channel_id = config.ors_channel_id
            existing.ors_quiet_start = config.ors_quiet_start
            existing.ors_quiet_end = config.ors_quiet_end
            existing.sandbox_mode = config.sandbox_mode
        else:
            session.add(config)
        session.commit()


def get_or_create_guild_config(guild_id: str) -> GuildConfig:
    """Get or create guild configuration."""
    config = get_guild_config(guild_id)
    if not config:
        config = GuildConfig(guild_id=guild_id)
    return config


class ClaraCommands(commands.Cog):
    """Clara administrative slash commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _is_gateway_mode(self) -> bool:
        """Check if the bot is running in gateway mode.

        Returns:
            True if connected to gateway, False if running standalone
        """
        gateway_client = getattr(self.bot, "gateway_client", None)
        return gateway_client is not None and gateway_client.is_connected

    def _get_gateway_client(self):
        """Get the gateway client if in gateway mode.

        Returns:
            Gateway client or None
        """
        if self._is_gateway_mode():
            return getattr(self.bot, "gateway_client", None)
        return None

    # ==========================================================================
    # MCP Command Group
    # ==========================================================================

    mcp = discord.SlashCommandGroup("mcp", "Manage MCP plugin servers")

    @mcp.command(name="list", description="List all installed MCP servers")
    async def mcp_list(self, ctx: discord.ApplicationContext):
        """List all MCP servers and their status."""
        if not await safe_defer(ctx):
            return

        try:
            gateway = self._get_gateway_client()

            if gateway:
                # Route through gateway
                response = await gateway.mcp_list()
                if not response.success:
                    await ctx.respond(embed=create_error_embed("Error", response.error or "Unknown error"))
                    return

                statuses = [
                    {
                        "name": s.name,
                        "status": s.status,
                        "enabled": s.enabled,
                        "tool_count": s.tool_count,
                        "source_type": s.source_type,
                    }
                    for s in response.servers
                ]
            else:
                # Local mode
                from clara_core.mcp import get_mcp_manager

                manager = get_mcp_manager()
                statuses = manager.get_all_server_status()

            if not statuses:
                embed = create_info_embed(
                    "No MCP Servers",
                    "No MCP servers are installed.\nUse `/mcp install` to add servers.",
                )
                await ctx.respond(embed=embed)
                return

            # Build status list
            lines = []
            for s in statuses:
                status_emoji = {
                    "running": "\u2705",
                    "stopped": "\u26ab",
                    "error": "\u274c",
                }.get(s.get("status", "stopped"), "\u2753")

                enabled_text = "enabled" if s.get("enabled", False) else "disabled"
                tool_count = s.get("tool_count", 0)
                source = s.get("source_type", "unknown")

                lines.append(f"{status_emoji} **{s['name']}** ({source}) - {enabled_text}, {tool_count} tools")

            embed = create_list_embed(
                f"MCP Servers ({len(statuses)})",
                lines,
                color=EMBED_COLOR_PRIMARY,
            )
            await ctx.respond(embed=embed)

        except Exception as e:
            logger.error(f"[commands] Error listing MCP servers: {e}")
            await ctx.respond(embed=create_error_embed("Error", str(e)))

    @mcp.command(name="status", description="Get detailed status of an MCP server")
    @option("server", description="Server name (omit for overall status)", required=False, default=None)
    async def mcp_status(self, ctx: discord.ApplicationContext, server: str = None):
        """Get detailed MCP server status."""
        if not await safe_defer(ctx):
            return

        try:
            gateway = self._get_gateway_client()

            if gateway:
                # Route through gateway
                response = await gateway.mcp_status(server_name=server)
                if not response.success:
                    await ctx.respond(embed=create_error_embed("Not Found", response.error or "Unknown error"))
                    return

                if not server:
                    # Overall status
                    embed = create_status_embed(
                        "MCP System Status",
                        fields=[
                            ("Total Servers", str(response.total_servers), True),
                            ("Enabled", str(response.enabled_servers), True),
                            ("Connected", str(response.connected_servers), True),
                        ],
                    )
                    await ctx.respond(embed=embed)
                    return

                # Specific server from gateway
                status = response.server
                if not status:
                    await ctx.respond(embed=create_error_embed("Not Found", f"Server '{server}' not found."))
                    return

                fields = [
                    ("Status", status.status, True),
                    ("Connected", "Yes" if status.connected else "No", True),
                    ("Transport", status.transport or "unknown", True),
                    ("Tools", str(status.tool_count), True),
                ]

                if status.last_error:
                    fields.append(("Last Error", status.last_error[:100], False))

                embed = create_status_embed(f"Server: {server}", fields=fields)

                # Add tools list
                if status.tools:
                    tools_text = ", ".join(status.tools[:10])
                    if len(status.tools) > 10:
                        tools_text += f" ... +{len(status.tools) - 10} more"
                    embed.add_field(name="Tools", value=tools_text, inline=False)

                await ctx.respond(embed=embed)
                return

            # Local mode
            from clara_core.mcp import get_mcp_manager

            manager = get_mcp_manager()

            if not server:
                # Overall status
                connected = len(manager)
                statuses = manager.get_all_server_status()
                total = len(statuses)
                enabled = sum(1 for s in statuses if s.get("enabled", False))

                embed = create_status_embed(
                    "MCP System Status",
                    fields=[
                        ("Total Servers", str(total), True),
                        ("Enabled", str(enabled), True),
                        ("Connected", str(connected), True),
                    ],
                )
                await ctx.respond(embed=embed)
                return

            # Specific server
            status = manager.get_server_status(server)
            if not status:
                await ctx.respond(embed=create_error_embed("Not Found", f"Server '{server}' not found."))
                return

            fields = [
                ("Status", status.get("status", "unknown"), True),
                ("Connected", "Yes" if status.get("connected") else "No", True),
                ("Transport", status.get("transport", "unknown"), True),
                ("Tools", str(status.get("tool_count", 0)), True),
            ]

            if status.get("last_error"):
                fields.append(("Last Error", status["last_error"][:100], False))

            embed = create_status_embed(f"Server: {server}", fields=fields)

            # Add tools list
            tools = status.get("tools", [])
            if tools:
                tools_text = ", ".join(tools[:10])
                if len(tools) > 10:
                    tools_text += f" ... +{len(tools) - 10} more"
                embed.add_field(name="Tools", value=tools_text, inline=False)

            await ctx.respond(embed=embed)

        except Exception as e:
            logger.error(f"[commands] Error getting MCP status: {e}")
            await ctx.respond(embed=create_error_embed("Error", str(e)))

    @mcp.command(name="tools", description="List tools from an MCP server")
    @option("server", description="Server name (omit for all tools)", required=False, default=None)
    async def mcp_tools(self, ctx: discord.ApplicationContext, server: str = None):
        """List tools from MCP servers."""
        if not await safe_defer(ctx):
            return

        try:
            from clara_core.mcp import get_mcp_manager

            manager = get_mcp_manager()

            if server:
                client = manager.get_client(server)
                if not client:
                    await ctx.respond(embed=create_error_embed("Not Connected", f"Server '{server}' is not connected."))
                    return

                tools = client.get_tools()
                if not tools:
                    await ctx.respond(embed=create_info_embed("No Tools", f"No tools available from '{server}'."))
                    return

                lines = [f"**{t.name}**: {t.description[:60]}..." for t in tools]
                embed = create_list_embed(f"Tools from {server} ({len(tools)})", lines)
                await ctx.respond(embed=embed)
            else:
                # All tools
                all_tools = manager.get_all_tools()
                if not all_tools:
                    await ctx.respond(embed=create_info_embed("No Tools", "No MCP tools available."))
                    return

                # Group by server
                by_server: dict[str, int] = {}
                for srv_name, _ in all_tools:
                    by_server[srv_name] = by_server.get(srv_name, 0) + 1

                lines = [f"**{srv}**: {count} tools" for srv, count in by_server.items()]
                embed = create_list_embed(f"All MCP Tools ({len(all_tools)} total)", lines)
                await ctx.respond(embed=embed)

        except Exception as e:
            logger.error(f"[commands] Error listing MCP tools: {e}")
            await ctx.respond(embed=create_error_embed("Error", str(e)))

    @mcp.command(name="search", description="Search Smithery registry for MCP servers")
    @option("query", description="Search query (e.g., 'file system', 'github', 'database')")
    async def mcp_search(self, ctx: discord.ApplicationContext, query: str):
        """Search Smithery registry for MCP servers."""
        if not await safe_defer(ctx):
            return

        try:
            from clara_core.mcp.installer import SmitheryClient

            client = SmitheryClient()
            result = await client.search(query, page_size=10)

            if result.error:
                await ctx.respond(embed=create_error_embed("Search Failed", result.error))
                return

            if not result.servers:
                await ctx.respond(embed=create_info_embed("No Results", f"No servers found for '{query}'."))
                return

            # Build response
            lines = []
            for server in result.servers[:10]:
                verified = " \u2713" if server.verified else ""
                uses = f" ({server.use_count} uses)" if server.use_count > 0 else ""
                desc = server.description[:60] + "..." if len(server.description) > 60 else server.description
                lines.append(f"**{server.qualified_name}**{verified}{uses}\n{desc}")

            embed = discord.Embed(
                title=f"Smithery Search: {query}",
                description="\n\n".join(lines),
                color=EMBED_COLOR_PRIMARY,
            )
            embed.set_footer(text=f"Found {result.total} results | Install with: /mcp install smithery:<name>")
            await ctx.respond(embed=embed)

        except Exception as e:
            logger.error(f"[commands] Error searching Smithery: {e}")
            await ctx.respond(embed=create_error_embed("Error", str(e)))

    @mcp.command(name="install", description="Install an MCP server (admin only)")
    @option("source", description="npm package, GitHub URL, smithery:<name>, or local path")
    @option("name", description="Custom name for the server", required=False, default=None)
    @commands.has_permissions(administrator=True)
    async def mcp_install(self, ctx: discord.ApplicationContext, source: str, name: str = None):
        """Install an MCP server."""
        if not await safe_defer(ctx):
            return

        try:
            gateway = self._get_gateway_client()

            if gateway:
                # Route through gateway
                response = await gateway.mcp_install(
                    source=source,
                    name=name,
                    requested_by=str(ctx.author.id),
                    timeout=120.0,
                )

                if response.success:
                    embed = create_success_embed(
                        "Server Installed",
                        f"**{response.server_name}** installed successfully!\n"
                        f"Source: `{source}`\n"
                        f"Tools discovered: {response.tools_discovered}",
                    )
                    await ctx.respond(embed=embed)
                else:
                    await ctx.respond(embed=create_error_embed("Installation Failed", response.error or "Unknown error"))
                return

            # Local mode
            from clara_core.mcp import get_mcp_manager
            from clara_core.mcp.installer import MCPInstaller

            installer = MCPInstaller()
            result = await installer.install(
                source=source,
                name=name,
                installed_by=str(ctx.author.id),
            )

            if result.success:
                # Auto-start
                manager = get_mcp_manager()
                server_name = result.server.name if result.server else name or "unknown"
                await manager.start_server(server_name)

                embed = create_success_embed(
                    "Server Installed",
                    f"**{server_name}** installed successfully!\n"
                    f"Source: `{source}`\n"
                    f"Tools discovered: {result.tools_discovered}",
                )
                await ctx.respond(embed=embed)
            else:
                await ctx.respond(embed=create_error_embed("Installation Failed", result.error or "Unknown error"))

        except Exception as e:
            logger.error(f"[commands] Error installing MCP server: {e}")
            await ctx.respond(embed=create_error_embed("Error", str(e)))

    @mcp.command(name="uninstall", description="Remove an MCP server (admin only)")
    @option("server", description="Server name to uninstall")
    @commands.has_permissions(administrator=True)
    async def mcp_uninstall(self, ctx: discord.ApplicationContext, server: str):
        """Uninstall an MCP server."""
        # Confirm first
        view = ConfirmView(f"uninstall {server}")
        await ctx.respond(
            embed=create_info_embed("Confirm Uninstall", f"Are you sure you want to uninstall **{server}**?"),
            view=view,
        )

        await view.wait()

        if not view.confirmed:
            if view.interaction:
                await view.interaction.response.edit_message(
                    embed=create_info_embed("Cancelled", "Uninstall cancelled."),
                    view=None,
                )
            return

        try:
            gateway = self._get_gateway_client()

            if gateway:
                # Route through gateway
                response = await gateway.mcp_uninstall(server_name=server)

                if response.success:
                    embed = create_success_embed("Server Uninstalled", f"**{server}** has been removed.")
                else:
                    embed = create_error_embed("Failed", response.error or f"Could not uninstall '{server}'.")

                if view.interaction:
                    await view.interaction.response.edit_message(embed=embed, view=None)
                return

            # Local mode
            from clara_core.mcp import get_mcp_manager
            from clara_core.mcp.installer import MCPInstaller

            manager = get_mcp_manager()
            if server in manager:
                await manager.stop_server(server)

            installer = MCPInstaller()
            success = await installer.uninstall(server)

            if success:
                embed = create_success_embed("Server Uninstalled", f"**{server}** has been removed.")
            else:
                embed = create_error_embed("Failed", f"Could not uninstall '{server}'.")

            if view.interaction:
                await view.interaction.response.edit_message(embed=embed, view=None)

        except Exception as e:
            logger.error(f"[commands] Error uninstalling MCP server: {e}")
            if view.interaction:
                await view.interaction.response.edit_message(embed=create_error_embed("Error", str(e)), view=None)

    @mcp.command(name="enable", description="Enable an MCP server")
    @option("server", description="Server name to enable")
    @commands.has_permissions(manage_channels=True)
    async def mcp_enable(self, ctx: discord.ApplicationContext, server: str):
        """Enable an MCP server."""
        if not await safe_defer(ctx):
            return

        try:
            gateway = self._get_gateway_client()

            if gateway:
                # Route through gateway
                response = await gateway.mcp_enable(server_name=server, enabled=True)

                if response.success:
                    await ctx.respond(
                        embed=create_success_embed("Server Enabled", f"**{server}** is now enabled and running.")
                    )
                else:
                    await ctx.respond(embed=create_error_embed("Failed", response.error or f"Could not enable '{server}'."))
                return

            # Local mode
            from clara_core.mcp import get_mcp_manager

            manager = get_mcp_manager()
            success = await manager.enable_server(server)

            if success:
                await ctx.respond(
                    embed=create_success_embed("Server Enabled", f"**{server}** is now enabled and running.")
                )
            else:
                await ctx.respond(embed=create_error_embed("Failed", f"Could not enable '{server}'."))

        except Exception as e:
            logger.error(f"[commands] Error enabling MCP server: {e}")
            await ctx.respond(embed=create_error_embed("Error", str(e)))

    @mcp.command(name="disable", description="Disable an MCP server")
    @option("server", description="Server name to disable")
    @commands.has_permissions(manage_channels=True)
    async def mcp_disable(self, ctx: discord.ApplicationContext, server: str):
        """Disable an MCP server."""
        if not await safe_defer(ctx):
            return

        try:
            gateway = self._get_gateway_client()

            if gateway:
                # Route through gateway
                response = await gateway.mcp_enable(server_name=server, enabled=False)

                if response.success:
                    await ctx.respond(embed=create_success_embed("Server Disabled", f"**{server}** is now disabled."))
                else:
                    await ctx.respond(embed=create_error_embed("Failed", response.error or f"Could not disable '{server}'."))
                return

            # Local mode
            from clara_core.mcp import get_mcp_manager

            manager = get_mcp_manager()
            success = await manager.disable_server(server)

            if success:
                await ctx.respond(embed=create_success_embed("Server Disabled", f"**{server}** is now disabled."))
            else:
                await ctx.respond(embed=create_error_embed("Failed", f"Could not disable '{server}'."))

        except Exception as e:
            logger.error(f"[commands] Error disabling MCP server: {e}")
            await ctx.respond(embed=create_error_embed("Error", str(e)))

    @mcp.command(name="restart", description="Restart an MCP server")
    @option("server", description="Server name to restart")
    @commands.has_permissions(manage_channels=True)
    async def mcp_restart(self, ctx: discord.ApplicationContext, server: str):
        """Restart an MCP server."""
        if not await safe_defer(ctx):
            return

        try:
            gateway = self._get_gateway_client()

            if gateway:
                # Route through gateway
                response = await gateway.mcp_restart(server_name=server, timeout=60.0)

                if response.success:
                    await ctx.respond(embed=create_success_embed("Server Restarted", f"**{server}** has been restarted."))
                else:
                    await ctx.respond(embed=create_error_embed("Failed", response.error or f"Could not restart '{server}'."))
                return

            # Local mode
            from clara_core.mcp import get_mcp_manager

            manager = get_mcp_manager()
            success = await manager.restart_server(server)

            if success:
                await ctx.respond(embed=create_success_embed("Server Restarted", f"**{server}** has been restarted."))
            else:
                await ctx.respond(embed=create_error_embed("Failed", f"Could not restart '{server}'."))

        except Exception as e:
            logger.error(f"[commands] Error restarting MCP server: {e}")
            await ctx.respond(embed=create_error_embed("Error", str(e)))

    @mcp.command(name="refresh", description="Reload all MCP servers (admin only)")
    @commands.has_permissions(administrator=True)
    async def mcp_refresh(self, ctx: discord.ApplicationContext):
        """Reload all MCP server configurations and reconnect."""
        if not await safe_defer(ctx):
            return

        try:
            from clara_core.mcp import get_mcp_manager

            manager = get_mcp_manager()
            results = await manager.reload()

            if not results:
                await ctx.respond(embed=create_info_embed("No Servers", "No MCP servers are configured."))
                return

            # Count results
            connected = sum(1 for v in results.values() if v)
            total = len(results)

            # Build status lines
            lines = []
            for name, success in sorted(results.items()):
                emoji = "\u2705" if success else "\u274c"
                status = "connected" if success else "failed"
                lines.append(f"{emoji} **{name}**: {status}")

            embed = create_list_embed(f"MCP Refresh ({connected}/{total} connected)", lines)
            await ctx.respond(embed=embed)

        except Exception as e:
            logger.error(f"[commands] Error refreshing MCP servers: {e}")
            await ctx.respond(embed=create_error_embed("Error", str(e)))

    @mcp.command(name="oauth_start", description="Start OAuth flow for a hosted Smithery server")
    @option("server", description="Server name to authorize")
    async def mcp_oauth_start(self, ctx: discord.ApplicationContext, server: str):
        """Start OAuth authorization flow for a hosted Smithery server."""
        if not await safe_defer(ctx):
            return

        try:
            from clara_core.mcp.models import load_server_config
            from clara_core.mcp.oauth import SmitheryOAuthClient

            # Check if server exists
            config = load_server_config(server)
            if not config:
                await ctx.respond(embed=create_error_embed("Not Found", f"Server '{server}' not found."))
                return

            if config.source_type != "smithery-hosted":
                await ctx.respond(
                    embed=create_error_embed(
                        "Not Hosted",
                        f"'{server}' is not a hosted Smithery server. OAuth is only for `smithery-hosted:` servers.",
                    )
                )
                return

            # Get redirect URI
            import os

            api_url = os.getenv("CLARA_API_URL", "")
            if api_url:
                redirect_uri = f"{api_url}/oauth/mcp/callback"
            else:
                redirect_uri = "urn:ietf:wg:oauth:2.0:oob"

            # Start OAuth flow
            oauth_client = SmitheryOAuthClient(server, config.endpoint_url)
            auth_url = await oauth_client.start_oauth_flow(redirect_uri)

            if not auth_url:
                await ctx.respond(
                    embed=create_error_embed("OAuth Failed", "Failed to start OAuth flow. Check logs for details.")
                )
                return

            # Build response embed
            embed = discord.Embed(
                title=f"OAuth Authorization for {server}",
                description="Click the link below to authorize Clara to access this Smithery server.",
                color=EMBED_COLOR_PRIMARY,
            )
            embed.add_field(name="Authorization URL", value=auth_url, inline=False)

            if redirect_uri == "urn:ietf:wg:oauth:2.0:oob":
                embed.add_field(
                    name="Next Step",
                    value=f"After authorizing, copy the code and run:\n`/mcp oauth_complete server:{server} code:<your-code>`",
                    inline=False,
                )
            else:
                embed.add_field(
                    name="Next Step",
                    value="After authorizing, you'll be redirected back automatically.",
                    inline=False,
                )

            await ctx.respond(embed=embed)

        except Exception as e:
            logger.error(f"[commands] Error starting OAuth: {e}")
            await ctx.respond(embed=create_error_embed("Error", str(e)))

    @mcp.command(name="oauth_complete", description="Complete OAuth with authorization code")
    @option("server", description="Server name")
    @option("code", description="Authorization code from OAuth callback")
    async def mcp_oauth_complete(self, ctx: discord.ApplicationContext, server: str, code: str):
        """Complete OAuth authorization for a hosted Smithery server."""
        if not await safe_defer(ctx):
            return

        try:
            from clara_core.mcp import get_mcp_manager
            from clara_core.mcp.models import load_server_config, save_server_config
            from clara_core.mcp.oauth import SmitheryOAuthClient

            # Check if server exists
            config = load_server_config(server)
            if not config:
                await ctx.respond(embed=create_error_embed("Not Found", f"Server '{server}' not found."))
                return

            # Get redirect URI (must match what was used in oauth_start)
            import os

            api_url = os.getenv("CLARA_API_URL", "")
            if api_url:
                redirect_uri = f"{api_url}/oauth/mcp/callback"
            else:
                redirect_uri = "urn:ietf:wg:oauth:2.0:oob"

            # Exchange code for tokens
            oauth_client = SmitheryOAuthClient(server, config.endpoint_url)
            success = await oauth_client.exchange_code(code, redirect_uri)

            if not success:
                await ctx.respond(
                    embed=create_error_embed(
                        "Token Exchange Failed",
                        "Could not exchange authorization code. The code may be invalid or expired.\n"
                        "Try starting the flow again with `/mcp oauth_start`.",
                    )
                )
                return

            # Update server status and try to connect
            config.status = "stopped"
            config.last_error = None
            save_server_config(config)

            # Try to start the server
            manager = get_mcp_manager()
            connected = await manager.start_server(server)

            if connected:
                status = manager.get_server_status(server)
                tool_count = status.get("tool_count", 0) if status else 0
                await ctx.respond(
                    embed=create_success_embed(
                        "OAuth Complete",
                        f"**{server}** is now authorized and connected!\n" f"Tools available: {tool_count}",
                    )
                )
            else:
                await ctx.respond(
                    embed=create_info_embed(
                        "OAuth Complete",
                        f"Tokens saved for **{server}**, but server failed to connect.\n"
                        "Use `/mcp status {server}` to check for errors.",
                    )
                )

        except Exception as e:
            logger.error(f"[commands] Error completing OAuth: {e}")
            await ctx.respond(embed=create_error_embed("Error", str(e)))

    @mcp.command(name="oauth_status", description="Check OAuth status of a hosted server")
    @option("server", description="Server name to check")
    async def mcp_oauth_status(self, ctx: discord.ApplicationContext, server: str):
        """Check OAuth status for a hosted Smithery server."""
        if not await safe_defer(ctx):
            return

        try:
            from clara_core.mcp.models import load_server_config
            from clara_core.mcp.oauth import load_oauth_state

            # Check server config
            config = load_server_config(server)
            if not config:
                await ctx.respond(embed=create_error_embed("Not Found", f"Server '{server}' not found."))
                return

            if config.source_type != "smithery-hosted":
                await ctx.respond(
                    embed=create_info_embed(
                        "Not Hosted", f"'{server}' is not a hosted Smithery server. OAuth not required."
                    )
                )
                return

            # Check OAuth state
            oauth_state = load_oauth_state(server)

            fields = [
                ("Server Type", config.source_type, True),
                ("Server Status", config.status, True),
            ]

            if oauth_state and oauth_state.tokens:
                fields.append(("OAuth", "\u2705 Authorized", True))
                if oauth_state.tokens.expires_at:
                    fields.append(("Token Expires", oauth_state.tokens.expires_at[:19], False))
                if oauth_state.tokens.is_expired():
                    fields.append(("Token Status", "\u26a0\ufe0f Expired (will auto-refresh)", False))
                else:
                    fields.append(("Token Status", "\u2705 Valid", False))
            else:
                fields.append(("OAuth", "\u274c Not authorized", True))

            embed = create_status_embed(f"OAuth Status: {server}", fields=fields)

            if not oauth_state or not oauth_state.tokens:
                embed.set_footer(text=f"Start authorization with: /mcp oauth_start server:{server}")

            await ctx.respond(embed=embed)

        except Exception as e:
            logger.error(f"[commands] Error checking OAuth status: {e}")
            await ctx.respond(embed=create_error_embed("Error", str(e)))

    @mcp.command(name="oauth_set_token", description="Manually set OAuth token (admin only)")
    @option("server", description="Server name")
    @option("access_token", description="OAuth access token")
    @option("refresh_token", description="Optional refresh token", required=False, default=None)
    @commands.has_permissions(administrator=True)
    async def mcp_oauth_set_token(
        self, ctx: discord.ApplicationContext, server: str, access_token: str, refresh_token: str = None
    ):
        """Manually set OAuth tokens for a hosted Smithery server."""
        if not await safe_defer(ctx):
            return

        try:
            from clara_core.mcp import get_mcp_manager
            from clara_core.mcp.models import load_server_config, save_server_config
            from clara_core.mcp.oauth import SmitheryOAuthClient

            # Check server config
            config = load_server_config(server)
            if not config:
                await ctx.respond(embed=create_error_embed("Not Found", f"Server '{server}' not found."))
                return

            # Set tokens manually
            oauth_client = SmitheryOAuthClient(server, config.endpoint_url)
            oauth_client.set_tokens_manually(access_token, refresh_token)

            # Update server status
            config.status = "stopped"
            config.last_error = None
            save_server_config(config)

            # Try to connect
            manager = get_mcp_manager()
            connected = await manager.start_server(server)

            if connected:
                status = manager.get_server_status(server)
                tool_count = status.get("tool_count", 0) if status else 0
                await ctx.respond(
                    embed=create_success_embed(
                        "Token Set",
                        f"Token configured for **{server}**!\n"
                        f"Server connected with {tool_count} tools available.",
                    )
                )
            else:
                await ctx.respond(
                    embed=create_info_embed(
                        "Token Set",
                        f"Token saved for **{server}**, but server failed to connect.\n"
                        "The token may be invalid. Check `/mcp status {server}` for errors.",
                    )
                )

        except Exception as e:
            logger.error(f"[commands] Error setting OAuth token: {e}")
            await ctx.respond(embed=create_error_embed("Error", str(e)))

    # ==========================================================================
    # Model Command Group
    # ==========================================================================

    model = discord.SlashCommandGroup("model", "Model and tier settings")

    @model.command(name="status", description="Show current model and tier settings")
    async def model_status(self, ctx: discord.ApplicationContext):
        """Show model status."""
        if not await safe_defer(ctx):
            return

        try:
            config = get_guild_config(str(ctx.guild_id)) if ctx.guild_id else None

            # Get current settings
            default_tier = config.default_tier if config else None
            auto_tier = config.auto_tier_enabled == "true" if config else False

            # Get env defaults
            env_tier = os.getenv("MODEL_TIER", "mid")
            env_auto = os.getenv("AUTO_TIER_SELECTION", "false").lower() == "true"
            provider = os.getenv("LLM_PROVIDER", "openrouter")

            fields = [
                ("Provider", provider, True),
                ("Server Default Tier", default_tier or f"(env: {env_tier})", True),
                ("Auto-Tier", "Enabled" if (auto_tier or env_auto) else "Disabled", True),
            ]

            embed = create_status_embed("Model Settings", fields=fields)
            embed.set_footer(text="Use message prefixes (!high, !mid, !low) to override per-message")
            await ctx.respond(embed=embed)

        except Exception as e:
            logger.error(f"[commands] Error getting model status: {e}")
            await ctx.respond(embed=create_error_embed("Error", str(e)))

    @model.command(name="tier", description="Set default model tier for this server (admin only)")
    @option("tier", description="Model tier", choices=["high", "mid", "low", "default"])
    @commands.has_permissions(administrator=True)
    async def model_tier(self, ctx: discord.ApplicationContext, tier: str):
        """Set default model tier."""
        if not await safe_defer(ctx):
            return

        try:
            if not ctx.guild_id:
                await ctx.respond(embed=create_error_embed("Error", "This command must be used in a server."))
                return

            config = get_or_create_guild_config(str(ctx.guild_id))
            config.default_tier = tier if tier != "default" else None
            save_guild_config(config)

            if tier == "default":
                await ctx.respond(embed=create_success_embed("Tier Reset", "Server will use environment default tier."))
            else:
                await ctx.respond(
                    embed=create_success_embed("Tier Set", f"Default tier set to **{tier}** for this server.")
                )

        except Exception as e:
            logger.error(f"[commands] Error setting model tier: {e}")
            await ctx.respond(embed=create_error_embed("Error", str(e)))

    @model.command(name="auto", description="Toggle auto-tier selection (admin only)")
    @option("enabled", description="Enable or disable auto-tier", choices=["on", "off"])
    @commands.has_permissions(administrator=True)
    async def model_auto(self, ctx: discord.ApplicationContext, enabled: str):
        """Toggle auto-tier selection."""
        if not await safe_defer(ctx):
            return

        try:
            if not ctx.guild_id:
                await ctx.respond(embed=create_error_embed("Error", "This command must be used in a server."))
                return

            config = get_or_create_guild_config(str(ctx.guild_id))
            config.auto_tier_enabled = "true" if enabled == "on" else "false"
            save_guild_config(config)

            status = "enabled" if enabled == "on" else "disabled"
            await ctx.respond(
                embed=create_success_embed("Auto-Tier Updated", f"Auto-tier selection is now **{status}**.")
            )

        except Exception as e:
            logger.error(f"[commands] Error setting auto-tier: {e}")
            await ctx.respond(embed=create_error_embed("Error", str(e)))

    # ==========================================================================
    # ORS Command Group
    # ==========================================================================

    ors = discord.SlashCommandGroup("ors", "Organic Response System (proactive messaging)")

    @ors.command(name="status", description="Show ORS configuration")
    async def ors_status(self, ctx: discord.ApplicationContext):
        """Show ORS status."""
        if not await safe_defer(ctx):
            return

        try:
            config = get_guild_config(str(ctx.guild_id)) if ctx.guild_id else None

            ors_enabled = config.ors_enabled == "true" if config else False
            channel_id = config.ors_channel_id if config else None
            quiet_start = config.ors_quiet_start if config else None
            quiet_end = config.ors_quiet_end if config else None

            # Check environment default
            env_enabled = os.getenv("ORS_ENABLED", "false").lower() == "true"

            fields = [
                ("Status", "Enabled" if (ors_enabled or env_enabled) else "Disabled", True),
                ("Channel", f"<#{channel_id}>" if channel_id else "Not set", True),
            ]

            if quiet_start and quiet_end:
                fields.append(("Quiet Hours", f"{quiet_start} - {quiet_end}", True))

            embed = create_status_embed("ORS Settings", fields=fields)
            await ctx.respond(embed=embed)

        except Exception as e:
            logger.error(f"[commands] Error getting ORS status: {e}")
            await ctx.respond(embed=create_error_embed("Error", str(e)))

    @ors.command(name="enable", description="Enable proactive messaging (admin only)")
    @commands.has_permissions(administrator=True)
    async def ors_enable(self, ctx: discord.ApplicationContext):
        """Enable ORS."""
        if not await safe_defer(ctx):
            return

        try:
            if not ctx.guild_id:
                await ctx.respond(embed=create_error_embed("Error", "This command must be used in a server."))
                return

            config = get_or_create_guild_config(str(ctx.guild_id))
            config.ors_enabled = "true"
            save_guild_config(config)

            await ctx.respond(embed=create_success_embed("ORS Enabled", "Proactive messaging is now enabled."))

        except Exception as e:
            logger.error(f"[commands] Error enabling ORS: {e}")
            await ctx.respond(embed=create_error_embed("Error", str(e)))

    @ors.command(name="disable", description="Disable proactive messaging (admin only)")
    @commands.has_permissions(administrator=True)
    async def ors_disable(self, ctx: discord.ApplicationContext):
        """Disable ORS."""
        if not await safe_defer(ctx):
            return

        try:
            if not ctx.guild_id:
                await ctx.respond(embed=create_error_embed("Error", "This command must be used in a server."))
                return

            config = get_or_create_guild_config(str(ctx.guild_id))
            config.ors_enabled = "false"
            save_guild_config(config)

            await ctx.respond(embed=create_success_embed("ORS Disabled", "Proactive messaging is now disabled."))

        except Exception as e:
            logger.error(f"[commands] Error disabling ORS: {e}")
            await ctx.respond(embed=create_error_embed("Error", str(e)))

    @ors.command(name="channel", description="Set ORS target channel (admin only)")
    @option("channel", description="Channel for proactive messages")
    @commands.has_permissions(administrator=True)
    async def ors_channel(self, ctx: discord.ApplicationContext, channel: discord.TextChannel):
        """Set ORS channel."""
        if not await safe_defer(ctx):
            return

        try:
            if not ctx.guild_id:
                await ctx.respond(embed=create_error_embed("Error", "This command must be used in a server."))
                return

            config = get_or_create_guild_config(str(ctx.guild_id))
            config.ors_channel_id = str(channel.id)
            save_guild_config(config)

            await ctx.respond(
                embed=create_success_embed("ORS Channel Set", f"Proactive messages will be sent to {channel.mention}.")
            )

        except Exception as e:
            logger.error(f"[commands] Error setting ORS channel: {e}")
            await ctx.respond(embed=create_error_embed("Error", str(e)))

    @ors.command(name="quiet", description="Set quiet hours (no proactive messages)")
    @option("start", description="Start time (HH:MM, 24h format)")
    @option("end", description="End time (HH:MM, 24h format)")
    @commands.has_permissions(administrator=True)
    async def ors_quiet(self, ctx: discord.ApplicationContext, start: str, end: str):
        """Set ORS quiet hours."""
        if not await safe_defer(ctx):
            return

        try:
            # Validate time format
            import re

            time_pattern = r"^([01]?[0-9]|2[0-3]):([0-5][0-9])$"
            if not re.match(time_pattern, start) or not re.match(time_pattern, end):
                await ctx.respond(
                    embed=create_error_embed("Invalid Time", "Please use HH:MM format (e.g., 22:00, 08:30)")
                )
                return

            if not ctx.guild_id:
                await ctx.respond(embed=create_error_embed("Error", "This command must be used in a server."))
                return

            config = get_or_create_guild_config(str(ctx.guild_id))
            config.ors_quiet_start = start
            config.ors_quiet_end = end
            save_guild_config(config)

            msg = f"No proactive messages between **{start}** and **{end}**."
            await ctx.respond(embed=create_success_embed("Quiet Hours Set", msg))

        except Exception as e:
            logger.error(f"[commands] Error setting quiet hours: {e}")
            await ctx.respond(embed=create_error_embed("Error", str(e)))

    # ==========================================================================
    # Sandbox Command Group
    # ==========================================================================

    sandbox = discord.SlashCommandGroup("sandbox", "Code execution sandbox")

    @sandbox.command(name="status", description="Show sandbox availability")
    async def sandbox_status(self, ctx: discord.ApplicationContext):
        """Show sandbox status."""
        if not await safe_defer(ctx):
            return

        try:
            from sandbox.manager import get_sandbox_manager

            manager = get_sandbox_manager()
            status = await manager.get_status()

            fields = [
                ("Mode", status.get("mode", "unknown"), True),
                ("Available", "Yes" if status.get("available") else "No", True),
            ]

            if status.get("local_docker"):
                fields.append(("Local Docker", "Available", True))
            if status.get("remote_url"):
                fields.append(("Remote URL", status["remote_url"][:30] + "...", True))

            embed = create_status_embed("Sandbox Status", fields=fields)
            await ctx.respond(embed=embed)

        except Exception as e:
            logger.error(f"[commands] Error getting sandbox status: {e}")
            await ctx.respond(embed=create_error_embed("Error", str(e)))

    @sandbox.command(name="mode", description="Set sandbox mode (admin only)")
    @option("mode", description="Sandbox mode", choices=["local", "remote", "auto"])
    @commands.has_permissions(administrator=True)
    async def sandbox_mode(self, ctx: discord.ApplicationContext, mode: str):
        """Set sandbox mode."""
        if not await safe_defer(ctx):
            return

        try:
            if not ctx.guild_id:
                await ctx.respond(embed=create_error_embed("Error", "This command must be used in a server."))
                return

            config = get_or_create_guild_config(str(ctx.guild_id))
            config.sandbox_mode = mode
            save_guild_config(config)

            await ctx.respond(embed=create_success_embed("Sandbox Mode Set", f"Sandbox mode set to **{mode}**."))

        except Exception as e:
            logger.error(f"[commands] Error setting sandbox mode: {e}")
            await ctx.respond(embed=create_error_embed("Error", str(e)))

    # ==========================================================================
    # Memory Command Group
    # ==========================================================================

    memory = discord.SlashCommandGroup("memory", "Memory system")

    @memory.command(name="status", description="Show memory statistics")
    async def memory_status(self, ctx: discord.ApplicationContext):
        """Show memory status."""
        if not await safe_defer(ctx):
            return

        try:
            # Get memory stats from mem0
            from clara_core.memory import Memory

            m = Memory()
            user_id = str(ctx.author.id)

            # Count memories
            memories = m.get_all(user_id=user_id)
            memory_count = len(memories) if memories else 0

            fields = [
                ("Your Memories", str(memory_count), True),
            ]

            embed = create_status_embed("Memory System", fields=fields)
            embed.set_footer(text="Memories are automatically extracted from conversations.")
            await ctx.respond(embed=embed)

        except Exception as e:
            logger.error(f"[commands] Error getting memory status: {e}")
            await ctx.respond(embed=create_error_embed("Error", str(e)))

    @memory.command(name="search", description="Search your memories")
    @option("query", description="Search query")
    async def memory_search(self, ctx: discord.ApplicationContext, query: str):
        """Search memories."""
        if not await safe_defer(ctx):
            return

        try:
            from clara_core.memory import Memory

            m = Memory()
            user_id = str(ctx.author.id)

            results = m.search(query, user_id=user_id, limit=10)

            if not results:
                await ctx.respond(embed=create_info_embed("No Results", f"No memories found matching '{query}'."))
                return

            lines = []
            for r in results:
                memory_text = r.get("memory", str(r))[:100]
                lines.append(f"\u2022 {memory_text}")

            embed = create_list_embed(f"Memory Search: {query}", lines)
            await ctx.respond(embed=embed)

        except Exception as e:
            logger.error(f"[commands] Error searching memories: {e}")
            await ctx.respond(embed=create_error_embed("Error", str(e)))

    @memory.command(name="clear", description="Clear all your memories")
    async def memory_clear(self, ctx: discord.ApplicationContext):
        """Clear user's memories."""
        view = ConfirmView("clear all memories")
        await ctx.respond(
            embed=create_info_embed(
                "Confirm Memory Clear",
                "This will delete **all** your stored memories. This cannot be undone.",
            ),
            view=view,
        )

        await view.wait()

        if not view.confirmed:
            if view.interaction:
                await view.interaction.response.edit_message(
                    embed=create_info_embed("Cancelled", "Memory clear cancelled."),
                    view=None,
                )
            return

        try:
            from clara_core.memory import Memory

            m = Memory()
            user_id = str(ctx.author.id)

            m.delete_all(user_id=user_id)

            if view.interaction:
                await view.interaction.response.edit_message(
                    embed=create_success_embed("Memories Cleared", "All your memories have been deleted."),
                    view=None,
                )

        except Exception as e:
            logger.error(f"[commands] Error clearing memories: {e}")
            if view.interaction:
                await view.interaction.response.edit_message(
                    embed=create_error_embed("Error", str(e)),
                    view=None,
                )

    # ==========================================================================
    # Email Command Group
    # ==========================================================================

    email = discord.SlashCommandGroup("email", "Email monitoring")

    @email.command(name="status", description="Show email monitoring status")
    async def email_status(self, ctx: discord.ApplicationContext):
        """Show email status."""
        if not await safe_defer(ctx):
            return

        try:
            from db.models import EmailAccount

            user_id = str(ctx.author.id)

            with SessionLocal() as session:
                accounts = session.query(EmailAccount).filter(EmailAccount.user_id == user_id).all()

            if not accounts:
                await ctx.respond(
                    embed=create_info_embed("No Email Accounts", "You haven't connected any email accounts yet.")
                )
                return

            lines = []
            for acc in accounts:
                status_emoji = "\u2705" if acc.enabled == "true" else "\u26ab"
                lines.append(f"{status_emoji} **{acc.email_address}** ({acc.provider_type})")

            embed = create_list_embed(f"Email Accounts ({len(accounts)})", lines)
            await ctx.respond(embed=embed)

        except Exception as e:
            logger.error(f"[commands] Error getting email status: {e}")
            await ctx.respond(embed=create_error_embed("Error", str(e)))

    @email.command(name="channel", description="Set alert channel (admin only)")
    @option("channel", description="Channel for email alerts")
    @commands.has_permissions(administrator=True)
    async def email_channel(self, ctx: discord.ApplicationContext, channel: discord.TextChannel):
        """Set email alert channel."""
        if not await safe_defer(ctx):
            return

        try:
            # This would need to update user's email accounts
            # For now, show a message about using the tool instead
            await ctx.respond(
                embed=create_info_embed(
                    "Use Email Tool",
                    "To set alert channels, use `email_set_alert_channel` in chat.\n"
                    "This allows per-account configuration.",
                )
            )

        except Exception as e:
            logger.error(f"[commands] Error setting email channel: {e}")
            await ctx.respond(embed=create_error_embed("Error", str(e)))

    @email.command(name="presets", description="List available email presets")
    async def email_presets(self, ctx: discord.ApplicationContext):
        """List email presets."""
        if not await safe_defer(ctx):
            return

        presets = {
            "job_hunting": "Recruiter emails, ATS platforms, job keywords",
            "urgent": "Emails with urgent/ASAP keywords",
            "security": "Password resets, 2FA codes, security alerts",
            "financial": "Banking, payment notifications",
            "shipping": "Package tracking, delivery updates",
        }

        lines = [f"**{name}**: {desc}" for name, desc in presets.items()]
        embed = create_list_embed("Email Presets", lines)
        embed.set_footer(text="Apply with: email_apply_preset <preset_name>")
        await ctx.respond(embed=embed)

    # ==========================================================================
    # Backup Command Group
    # ==========================================================================

    backup = discord.SlashCommandGroup("backup", "Database backup management")

    @backup.command(name="now", description="Trigger an immediate backup (admin only)")
    @option("database", description="Database to backup (clara, mem0, or both)", required=False)
    @commands.has_permissions(administrator=True)
    async def backup_now(self, ctx: discord.ApplicationContext, database: str = None):
        """Trigger immediate backup."""
        if not await safe_defer(ctx):
            return

        try:
            from clara_core.services.backup import get_backup_service

            service = get_backup_service()
            databases = [database] if database else None
            result = await service.backup_now(databases=databases)

            if result.success:
                embed = create_success_embed(
                    "Backup Complete",
                    f"{result.message}\n\nDatabases: {', '.join(result.databases_backed_up)}",
                )
            elif result.databases_backed_up:
                # Partial success
                embed = create_info_embed(
                    "Partial Backup",
                    f"{result.message}\n\nSucceeded: {', '.join(result.databases_backed_up)}\n"
                    f"Failed: {', '.join(result.databases_failed)}",
                )
            else:
                embed = create_error_embed("Backup Failed", result.message)

            await ctx.respond(embed=embed)

        except Exception as e:
            logger.error(f"[commands] Error triggering backup: {e}")
            await ctx.respond(embed=create_error_embed("Error", str(e)))

    @backup.command(name="status", description="Show backup status")
    async def backup_status(self, ctx: discord.ApplicationContext):
        """Show backup status."""
        if not await safe_defer(ctx):
            return

        try:
            from clara_core.services.backup import get_backup_service

            service = get_backup_service()
            status = await service.get_status()

            fields = []

            # Configuration status
            config = status.get("configured", {})
            connected = status.get("connected", {})

            clara_status = "\u2705" if connected.get("clara_db") else ("\u26ab" if config.get("clara_db") else "\u274c")
            mem0_status = "\u2705" if connected.get("mem0_db") else ("\u26ab" if config.get("mem0_db") else "\u274c")
            s3_status = "\u2705" if connected.get("s3") else ("\u26ab" if config.get("s3") else "\u274c")

            fields.append(("Clara DB", clara_status, True))
            fields.append(("Mem0 DB", mem0_status, True))
            fields.append(("S3 Storage", s3_status, True))

            # Settings
            settings = status.get("settings", {})
            fields.append(("S3 Bucket", settings.get("s3_bucket", "not set"), True))
            fields.append(("Retention", f"{settings.get('retention_days', 7)} days", True))

            # Last backup
            last = status.get("last_backup")
            if last:
                fields.append(
                    ("Last Backup", f"{last.get('filename', 'unknown')} ({last.get('size_mb', 0):.2f} MB)", False)
                )
            else:
                fields.append(("Last Backup", "None found", False))

            embed = create_status_embed("Backup Status", fields=fields)
            await ctx.respond(embed=embed)

        except Exception as e:
            logger.error(f"[commands] Error getting backup status: {e}")
            await ctx.respond(embed=create_error_embed("Error", str(e)))

    @backup.command(name="list", description="List available backups")
    @option("database", description="Filter by database (clara, mem0)", required=False)
    @option("limit", description="Maximum backups to show", required=False, min_value=1, max_value=50)
    async def backup_list(self, ctx: discord.ApplicationContext, database: str = None, limit: int = 10):
        """List available backups."""
        if not await safe_defer(ctx):
            return

        try:
            from clara_core.services.backup import get_backup_service

            service = get_backup_service()
            backups = await service.list_backups(database=database, limit=limit)

            if not backups:
                await ctx.respond(embed=create_info_embed("No Backups", "No backups found."))
                return

            lines = []
            for b in backups:
                ts = b.timestamp.strftime("%Y-%m-%d %H:%M")
                lines.append(f"**{b.filename}** ({b.database}) - {b.size_mb:.2f} MB - {ts}")

            embed = create_list_embed(f"Backups ({len(backups)})", lines)
            await ctx.respond(embed=embed)

        except Exception as e:
            logger.error(f"[commands] Error listing backups: {e}")
            await ctx.respond(embed=create_error_embed("Error", str(e)))

    # ==========================================================================
    # Clara Utility Commands
    # ==========================================================================

    clara = discord.SlashCommandGroup("clara", "Clara utilities")

    @clara.command(name="help", description="Show Clara command help")
    @option(
        "topic",
        description="Help topic",
        required=False,
        default=None,
        choices=["mcp", "model", "ors", "sandbox", "memory", "email", "backup"],
    )
    async def clara_help(self, ctx: discord.ApplicationContext, topic: str = None):
        """Show help."""
        if topic:
            embed = create_help_embed(topic=topic, commands_info=HelpSelectView.TOPICS.get(topic, {}).get("commands"))
            await ctx.respond(embed=embed)
        else:
            embed = create_help_embed()
            view = HelpSelectView()
            await ctx.respond(embed=embed, view=view)

    @clara.command(name="info", description="Show Clara system information")
    async def clara_info(self, ctx: discord.ApplicationContext):
        """Show Clara info."""
        if not await safe_defer(ctx):
            return

        try:
            provider = os.getenv("LLM_PROVIDER", "openrouter")

            # Get connected servers count
            from clara_core.mcp import get_mcp_manager

            manager = get_mcp_manager()
            mcp_count = len(manager)

            fields = [
                ("LLM Provider", provider, True),
                ("MCP Servers", str(mcp_count), True),
            ]

            embed = create_status_embed("Clara System Info", fields=fields)
            embed.set_footer(text="MyPalClara - Your AI Assistant")
            await ctx.respond(embed=embed)

        except Exception as e:
            logger.error(f"[commands] Error getting info: {e}")
            await ctx.respond(embed=create_error_embed("Error", str(e)))

    @clara.command(name="channel", description="Set Clara's response mode for this channel")
    @option("mode", description="Response mode", choices=["active", "mention", "off"])
    @commands.has_permissions(manage_channels=True)
    async def clara_channel(self, ctx: discord.ApplicationContext, mode: str):
        """Set channel mode."""
        if not await safe_defer(ctx):
            return

        try:
            from db.channel_config import set_channel_mode

            if not ctx.channel_id or not ctx.guild_id:
                await ctx.respond(embed=create_error_embed("Error", "This command must be used in a server channel."))
                return

            set_channel_mode(str(ctx.channel_id), str(ctx.guild_id), mode, str(ctx.author.id))

            mode_descriptions = {
                "active": "Clara will respond to all messages",
                "mention": "Clara will only respond when mentioned",
                "off": "Clara will not respond in this channel",
            }

            await ctx.respond(
                embed=create_success_embed("Channel Mode Set", f"Mode: **{mode}**\n{mode_descriptions[mode]}")
            )

        except Exception as e:
            logger.error(f"[commands] Error setting channel mode: {e}")
            await ctx.respond(embed=create_error_embed("Error", str(e)))

    # ==========================================================================
    # Error Handlers
    # ==========================================================================

    @mcp_install.error
    @mcp_uninstall.error
    @mcp_refresh.error
    @mcp_oauth_set_token.error
    @model_tier.error
    @model_auto.error
    @ors_enable.error
    @ors_disable.error
    @ors_channel.error
    @ors_quiet.error
    @sandbox_mode.error
    @email_channel.error
    @backup_now.error
    @clara_channel.error
    async def permission_error_handler(self, ctx: discord.ApplicationContext, error: Exception):
        """Handle permission errors."""
        if isinstance(error, commands.MissingPermissions):
            await ctx.respond(
                embed=create_error_embed(
                    "Permission Denied",
                    "You need Administrator or Manage Channels permission to use this command.",
                ),
                ephemeral=True,
            )
        else:
            logger.error(f"[commands] Unhandled error: {error}")
            await ctx.respond(embed=create_error_embed("Error", str(error)), ephemeral=True)
