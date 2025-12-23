"""Clara Tools System - Modular, reloadable tools with MCP support.

This package provides the tool infrastructure for Clara:
- Modular tool definitions in separate files
- Hot-reloadable at runtime
- MCP protocol support for external tool exposure

Usage:
    from tools import get_registry, get_loader, ToolContext, ToolDef

    # Initialize and load all tools
    loader = get_loader()
    await loader.load_all()

    # Optionally enable hot-reload
    loader.start_watching()

    # Get tools for LLM
    registry = get_registry()
    tools = registry.get_tools(platform="discord", format="openai")

    # Execute a tool
    context = ToolContext(user_id="user123", platform="discord")
    result = await registry.execute("execute_python", {"code": "print(1)"}, context)

Tool Module Contract:
    Each tool module (tools/*.py, not starting with _) must export:
    - MODULE_NAME: str - Unique identifier
    - MODULE_VERSION: str - Version string
    - TOOLS: list[ToolDef] - Tool definitions

    Optional exports:
    - async def initialize() -> None - Called after loading
    - async def cleanup() -> None - Called before unloading
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from ._base import ToolContext, ToolDef, ToolHandler
from ._loader import ToolLoader
from ._registry import ToolRegistry

if TYPE_CHECKING:
    pass

# Package version
__version__ = "0.1.0"

# Singleton instances
_registry: ToolRegistry | None = None
_loader: ToolLoader | None = None


def get_registry() -> ToolRegistry:
    """Get the global tool registry singleton.

    Returns:
        The ToolRegistry instance
    """
    global _registry
    if _registry is None:
        _registry = ToolRegistry.get_instance()
    return _registry


def get_loader() -> ToolLoader:
    """Get the global tool loader singleton.

    Returns:
        The ToolLoader instance
    """
    global _loader
    if _loader is None:
        tools_dir = Path(__file__).parent
        _loader = ToolLoader(tools_dir, get_registry())
    return _loader


async def init_tools(hot_reload: bool | None = None) -> dict[str, bool]:
    """Initialize the tool system and load all tool modules.

    Args:
        hot_reload: Enable hot-reload watching. If None, reads from
                   TOOL_HOT_RELOAD env var (default: False)

    Returns:
        Dict mapping module names to load success status
    """
    loader = get_loader()
    results = await loader.load_all()

    # Determine hot-reload setting
    if hot_reload is None:
        hot_reload = os.getenv("TOOL_HOT_RELOAD", "false").lower() == "true"

    if hot_reload:
        loader.start_watching()

    return results


async def shutdown_tools() -> None:
    """Shutdown the tool system, cleaning up all modules."""
    loader = get_loader()
    await loader.shutdown()


def reset_tools() -> None:
    """Reset the tool system (for testing)."""
    global _registry, _loader
    if _loader:
        _loader.stop_watching()
    _loader = None
    _registry = None
    ToolRegistry.reset()


__all__ = [
    # Version
    "__version__",
    # Core classes
    "ToolDef",
    "ToolContext",
    "ToolHandler",
    "ToolRegistry",
    "ToolLoader",
    # Singleton accessors
    "get_registry",
    "get_loader",
    # Lifecycle functions
    "init_tools",
    "shutdown_tools",
    "reset_tools",
]
