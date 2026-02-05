"""MCP plugin integration.

This module integrates the existing MCP server system with the new plugin system,
allowing MCP servers to be registered as plugins.

MCP tools are automatically added to the `group:mcp` policy group for
unified access control with native tools.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable

if TYPE_CHECKING:
    from ..mcp.manager import MCPServerManager
    from .registry import PluginRegistry
    from .types import PluginAPI, PluginRecord

from .hooks import HookEvent
from .policies import get_policy_engine
from .types import PluginKind

logger = logging.getLogger(__name__)


@dataclass
class MCPPluginConfig:
    """Configuration for MCP plugin."""

    enabled: bool = True
    servers: list[str] = field(default_factory=list)  # Server IDs to load


class MCPPlugin:
    """Plugin adapter for MCP system.

    Bridges the existing MCPServerManager with the plugin system.
    """

    def __init__(
        self,
        mcp_manager: "MCPServerManager | None" = None,
    ) -> None:
        """Initialize MCP plugin.

        Args:
            mcp_manager: Optional MCPServerManager instance
        """
        self._mcp_manager = mcp_manager

    async def initialize(
        self,
        registry: "PluginRegistry",
        api: "PluginAPI",
    ) -> None:
        """Initialize MCP plugin and register with registry.

        Args:
            registry: PluginRegistry to register with
            api: PluginAPI for registering tools/hooks
        """
        from ..mcp.manager import MCPServerManager

        # Create MCP manager if not provided
        if self._mcp_manager is None:
            self._mcp_manager = MCPServerManager.get_instance()

        # Initialize MCP manager (load servers)
        logger.info("Initializing MCP plugin...")
        await self._mcp_manager.initialize()

        # Register MCP tools as plugin tools
        self._register_mcp_tools(registry, api)

        # Register MCP hooks
        self._register_mcp_hooks(registry, api)

        logger.info("MCP plugin initialized")

    def _register_mcp_tools(self, registry: "PluginRegistry", api: "PluginAPI") -> None:
        """Register all MCP tools as plugin tools.

        MCP tools are automatically added to the `group:mcp` policy group
        for unified access control.

        Args:
            registry: PluginRegistry to register with
            api: PluginAPI for registration
        """
        if self._mcp_manager is None:
            return

        # Get all tools from MCP manager
        all_tools = self._mcp_manager.get_namespaced_tools()

        # Get policy engine for group registration
        policy_engine = get_policy_engine()
        mcp_tool_names = []

        for tool_name, mcp_tool in all_tools.items():
            # Create tool definition
            from tools._base import ToolDef

            # Create a factory to properly capture tool_name by value
            def make_handler(tn: str):
                async def mcp_tool_handler(args: dict, ctx) -> str:
                    """Handler that delegates to MCP manager."""
                    # Call the tool via MCP manager
                    result = await self._mcp_manager.call_tool(tn, args)
                    return result

                return mcp_tool_handler

            handler = make_handler(tool_name)

            # Register tool with namespaced name
            tool_def = ToolDef(
                name=tool_name,
                description=(f"{mcp_tool.description} (from MCP server: " f"{tool_name.split('__')[0]})"),
                parameters=mcp_tool.input_schema,
                handler=handler,
                platforms=["discord"],  # MCP tools available on Discord
            )

            # Register via API (which uses the registry)
            # Use "mcp" as plugin_id to group all MCP tools
            api.register_tool(tool_def)

            # Track for policy group
            mcp_tool_names.append(tool_name)

        # Register all MCP tools in the group:mcp policy group
        if mcp_tool_names:
            policy_engine.register_group("group:mcp", mcp_tool_names)
            logger.info(f"Added {len(mcp_tool_names)} tools to group:mcp")

        logger.info(f"Registered {len(all_tools)} MCP tools")

    def _register_mcp_hooks(self, registry: "PluginRegistry", api: "PluginAPI") -> None:
        """Register MCP lifecycle hooks.

        Args:
            registry: PluginRegistry to register with
            api: PluginAPI for registration
        """
        # Wrap MCP manager methods to emit hooks
        original_initialize = self._mcp_manager.initialize
        original_start = self._mcp_manager.start_server
        original_stop = self._mcp_manager.stop_server

        async def wrapped_initialize() -> dict[str, bool]:
            """Wrapper that emits hooks."""
            result = await original_initialize()
            for server_name, success in result.items():
                if success:
                    # Emit hook for each started server
                    await registry.emit_hook(
                        HookEvent.MCP_SERVER_START.value,
                        server_name=server_name,
                        server_type="local",
                        transport="stdio",
                    )
            return result

        async def wrapped_start(server_name: str) -> bool:
            """Wrapper that emits hooks."""
            success = await original_start(server_name)
            if success:
                await registry.emit_hook(
                    HookEvent.MCP_SERVER_START.value,
                    server_name=server_name,
                    server_type="local",
                    transport="stdio",
                )
            else:
                await registry.emit_hook(
                    HookEvent.MCP_SERVER_ERROR.value,
                    server_name=server_name,
                    server_type="local",
                    transport="stdio",
                    error="Failed to start MCP server",
                )
            return success

        async def wrapped_stop(server_name: str) -> bool:
            """Wrapper that emits hooks."""
            success = await original_stop(server_name)
            if success:
                await registry.emit_hook(
                    HookEvent.MCP_SERVER_STOP.value,
                    server_name=server_name,
                    server_type="local",
                    duration_ms=0,
                )
            return success

        # Replace methods on manager
        self._mcp_manager.initialize = wrapped_initialize  # type: ignore
        self._mcp_manager.start_server = wrapped_start  # type: ignore
        self._mcp_manager.stop_server = wrapped_stop  # type: ignore

        logger.info("MCP hooks registered")

    async def shutdown(self) -> None:
        """Shutdown MCP plugin.

        Shuts down all MCP servers gracefully.
        """
        if self._mcp_manager is None:
            return

        logger.info("Shutting down MCP plugin...")
        await self._mcp_manager.shutdown()
        logger.info("MCP plugin shutdown complete")


# Singleton MCP plugin instance
_mcp_plugin: MCPPlugin | None = None


def get_mcp_plugin() -> MCPPlugin:
    """Get or create singleton MCP plugin instance.

    Returns:
        MCPPlugin singleton
    """
    global _mcp_plugin
    if _mcp_plugin is None:
        _mcp_plugin = MCPPlugin()
    return _mcp_plugin


async def register_mcp_plugin(registry: "PluginRegistry", api: "PluginAPI") -> None:
    """Register MCP plugin with the registry.

    This is the entry point for MCP integration.

    Args:
        registry: PluginRegistry to register with
        api: PluginAPI for registration
    """
    mcp_plugin = get_mcp_plugin()
    await mcp_plugin.initialize(registry, api)


def create_mcp_plugin_record() -> "PluginRecord":
    """Create a plugin record for the MCP system.

    Returns:
        PluginRecord for MCP
    """
    from .types import PluginOrigin, PluginRecord

    return PluginRecord(
        id="mcp",
        name="MCP Server Integration",
        version="1.0.0",
        description="Integration for Model Context Protocol servers",
        kind=PluginKind.MCP,
        origin=PluginOrigin.BUNDLED,
        source="clara_core.plugins.mcp",
        workspace_dir=None,
        enabled=True,
        status="loaded",
        error=None,
        config_schema={
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "default": True,
                    "description": "Enable MCP server integration",
                },
                "servers_dir": {
                    "type": "string",
                    "default": ".mcp_servers",
                    "description": "Directory containing MCP server configs",
                },
            },
        },
        # Tools will be registered dynamically
        tool_names=[],
        hook_names=[
            HookEvent.MCP_SERVER_START,
            HookEvent.MCP_SERVER_STOP,
            HookEvent.MCP_SERVER_ERROR,
        ],
    )
