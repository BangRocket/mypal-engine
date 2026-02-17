"""Main plugin system initialization.

This module provides the main entry point for the plugin system,
handling initialization of the plugin registry, loader, and integrations.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from .hooks import HookEvent
from .loader import PluginLoader, PluginLoadOptions
from .mcp_integration import create_mcp_plugin_record, get_mcp_plugin
from .registry import PluginRegistry
from .types import (
    DiagnosticLevel,
    PluginAPI,
    PluginRecord,
)

logger = logging.getLogger(__name__)

# Global instances
_registry: PluginRegistry | None = None
_loader: PluginLoader | None = None


def get_registry() -> PluginRegistry:
    """Get the global plugin registry.

    Returns:
        PluginRegistry singleton
    """
    global _registry
    if _registry is None:
        _registry = PluginRegistry()
    return _registry


def get_loader() -> PluginLoader:
    """Get the global plugin loader.

    Returns:
        PluginLoader singleton
    """
    global _loader
    if _loader is None:
        _loader = PluginLoader()
    return _loader


async def initialize_plugins(
    config: dict | None = None,
    workspace_dir: Path | None = None,
    enable_mcp: bool = True,
) -> PluginRegistry:
    """Initialize the complete plugin system.

    Args:
        config: Main app configuration
        workspace_dir: Optional workspace directory
        enable_mcp: Whether to initialize MCP integration

    Returns:
        Initialized PluginRegistry
    """
    logger.info("Initializing Clara plugin system...")

    # Get or create registry
    registry = get_registry()

    # Configure registry with app config
    if config:
        registry.config = config
    if workspace_dir:
        registry.runtime.state_dir = workspace_dir / ".clara" / "state"
        registry.runtime.config_dir = workspace_dir / ".clara" / "config"

    # Load all plugins
    loader = get_loader()
    options = PluginLoadOptions(
        config=config or {},
        workspace_dir=workspace_dir,
        logger=logger,
        cache=True,
        mode="full",
    )

    load_results = await loader.load_all(registry, options)
    loaded_count = sum(1 for _, success in load_results if success)
    logger.info(f"Loaded {loaded_count}/{len(load_results)} plugins")

    # Resolve factory-based tools
    await registry.resolve_factory_tools()

    # Initialize MCP integration if enabled
    if enable_mcp:
        await initialize_mcp_integration(registry)

    # Check for diagnostics
    diagnostics = registry.get_diagnostics()
    errors = [d for d in diagnostics if d.level == DiagnosticLevel.ERROR]
    if errors:
        logger.error(f"Plugin system had {len(errors)} errors:")
        for diag in errors:
            plugin_id_str = diag.plugin_id if diag.plugin_id else "system"
            logger.error(f"  [{plugin_id_str}] {diag.message}")

    logger.info("Plugin system initialization complete")
    logger.info(f"  Total plugins: {len(registry.plugins)}")
    logger.info(f"  Total tools: {len(registry.tools)}")
    logger.info(f"  Total hooks: {sum(len(h) for h in registry.hooks.values())}")

    return registry


async def initialize_mcp_integration(registry: PluginRegistry) -> None:
    """Initialize MCP integration as a plugin.

    Args:
        registry: PluginRegistry to register MCP with
    """
    try:
        # Create MCP plugin record
        mcp_record = create_mcp_plugin_record()
        registry.register_plugin(mcp_record)

        # Create mock PluginAPI for MCP plugin
        mcp_api = PluginAPI(
            id="mcp",
            name="MCP Server Integration",
            version="1.0.0",
            description="Integration for Model Context Protocol servers",
            source="clara_core.plugins.mcp_integration",
            config=registry.config,
            plugin_config={},
            runtime=registry.runtime,
            logger=logger,
            # Registration methods (bound to registry)
            register_tool=lambda t, opt=False: registry.register_tool(t, "mcp", opt, "clara_core.plugins.mcp"),
            register_hook=lambda e, h: registry.register_hook(e, h, "mcp", "clara_core.plugins.mcp"),
            register_channel=lambda c: None,  # MCP doesn't register channels
            register_provider=lambda p: None,  # MCP doesn't register providers
            register_service=lambda s: None,  # MCP doesn't register services
            register_command=lambda cmd: None,
            resolve_path=lambda p: registry.runtime.resolve_path(p),
        )

        # Initialize MCP plugin
        mcp_plugin = get_mcp_plugin()
        await mcp_plugin.initialize(registry, mcp_api)

        logger.info("MCP integration initialized")

    except Exception as e:
        logger.error(f"Failed to initialize MCP integration: {e}", exc_info=True)
        # Import Diagnostic here to avoid circular import
        from .types import Diagnostic

        registry.push_diagnostic(
            Diagnostic(
                level=DiagnosticLevel.ERROR,
                message=f"MCP initialization failed: {e}",
                plugin_id="mcp",
                source="clara_core.plugins.mcp_integration",
            )
        )


async def reload_plugins(
    config: dict | None = None,
    workspace_dir: Path | None = None,
) -> PluginRegistry:
    """Reload all plugins.

    Args:
        config: Main app configuration
        workspace_dir: Optional workspace directory

    Returns:
        Reloaded PluginRegistry
    """
    logger.info("Reloading plugins...")

    # Reset registry state
    registry = get_registry()
    registry.plugins.clear()
    registry.tools.clear()
    registry.hooks.clear()
    registry.channels.clear()
    registry.providers.clear()
    registry.services.clear()
    registry.tool_registrations.clear()

    # Re-initialize
    return await initialize_plugins(config, workspace_dir)


def get_plugin_status() -> dict[str, dict]:
    """Get status of all loaded plugins.

    Returns:
        Dict mapping plugin IDs to status info
    """
    registry = get_registry()

    status = {}
    for plugin_id, record in registry.plugins.items():
        status[plugin_id] = {
            "id": record.id,
            "name": record.name,
            "version": record.version,
            "enabled": record.enabled,
            "status": record.status,
            "error": record.error,
            "origin": record.origin.value,
            "tools": len(record.tool_names),
            "hooks": len(record.hook_names),
            "channels": len(record.channel_ids),
            "providers": len(record.provider_ids),
            "services": len(record.service_ids),
        }

    return status


async def emit_system_hooks(event_type: str, **kwargs) -> list:
    """Emit a hook event through the registry.

    Convenience function for emitting hooks.

    Args:
        event_type: Hook event name
        **kwargs: Event data

    Returns:
        List of results from all handlers
    """
    registry = get_registry()
    return await registry.emit_hook(event_type, **kwargs)


async def shutdown_plugins() -> None:
    """Shutdown the plugin system gracefully.

    Cleans up all plugins, MCP connections, etc.
    """
    logger.info("Shutting down plugin system...")

    # Shutdown MCP integration
    mcp_plugin = get_mcp_plugin()
    await mcp_plugin.shutdown()

    logger.info("Plugin system shutdown complete")
