"""Compatibility layer for migrating from ToolRegistry to PluginRegistry.

This module provides an adapter that allows existing code using ToolRegistry
to work with the new PluginRegistry-based plugin system.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Awaitable

if TYPE_CHECKING:
    from tools._base import ToolDef, ToolContext
    from ..plugins.registry import PluginRegistry

logger = logging.getLogger(__name__)


class ToolRegistryAdapter:
    """Adapter that bridges ToolRegistry interface to PluginRegistry.

    This allows existing code that uses ToolRegistry.get_instance()
    to continue working while we migrate to the new plugin system.

    The adapter:
    1. Registers tools from old-style tool modules with the new plugin registry
    2. Provides the same interface as ToolRegistry
    3. Handles tool execution through the registry
    """

    _instance = None

    def __init__(self, plugin_registry: "PluginRegistry | None" = None):
        """Initialize adapter.

        Args:
            plugin_registry: Optional PluginRegistry instance
        """
        self._plugin_registry = plugin_registry

    @classmethod
    def get_instance(cls) -> "ToolRegistryAdapter":
        """Get or create singleton adapter instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton instance."""
        cls._instance = None

    def set_plugin_registry(self, registry: "PluginRegistry") -> None:
        """Set the underlying plugin registry.

        Args:
            registry: PluginRegistry to use
        """
        self._plugin_registry = registry

    def register(self, tool: "ToolDef", source_module: str = "builtin") -> None:
        """Register a tool definition.

        Args:
            tool: The tool definition to register
            source_module: Name of module providing this tool

        Raises:
            ValueError: If tool already registered by different module
        """
        if self._plugin_registry is None:
            logger.warning("Plugin registry not set, tool will not be registered")
            return

        from tools._base import ToolDef

        if not isinstance(tool, ToolDef):
            logger.warning(f"Skipping non-ToolDef registration from {source_module}")
            return

        # Register as a plugin tool with plugin_id = source_module
        # Use "legacy" as origin to indicate old-style modules
        self._plugin_registry.register_tool(
            tool,
            plugin_id=source_module,
            optional=False,
            source=f"legacy:{source_module}",
        )

    def unregister(self, tool_name: str) -> bool:
        """Unregister a single tool.

        Args:
            tool_name: Name of tool to unregister

        Returns:
            True if tool was unregistered
        """
        if self._plugin_registry is None:
            return False

        if tool_name in self._plugin_registry.tools:
            del self._plugin_registry.tools[tool_name]

            # Remove from plugin records
            for record in self._plugin_registry.plugins.values():
                if tool_name in record.tool_names:
                    record.tool_names.remove(tool_name)
            return True

        return False

    def unregister_module(self, module_name: str) -> list[str]:
        """Unregister all tools from a specific module.

        Args:
            module_name: Name of module whose tools to unregister

        Returns:
            List of tool names that were unregistered
        """
        removed = []
        if self._plugin_registry is None:
            return removed

        # Get the plugin record for this module
        record = self._plugin_registry.get_plugin(module_name)
        if record:
            for tool_name in record.tool_names[:]:  # Copy list
                if tool_name in self._plugin_registry.tools:
                    del self._plugin_registry.tools[tool_name]
                    removed.append(tool_name)
            record.tool_names.clear()

        return removed

    def get_tool(self, name: str) -> "ToolDef | None":
        """Get a single tool definition by name."""
        if self._plugin_registry is None:
            return None
        return self._plugin_registry.get_tool(name)

    def get_tools(
        self,
        platform: str | None = None,
        capabilities: dict[str, bool] | None = None,
        format: str = "openai",
    ) -> list[dict[str, Any]]:
        """Get tool definitions filtered by platform and capabilities.

        Args:
            platform: Filter to tools available on this platform
            capabilities: Dict of capability -> available
            format: Output format - "openai", "mcp", or "claude"

        Returns:
            List of tool definitions in requested format
        """
        if self._plugin_registry is None:
            return []

        tools = self._plugin_registry.get_tools(platform)

        # Filter by capabilities
        if capabilities:
            filtered = []
            for tool in tools:
                skip = False
                for cap in tool.requires:
                    if not capabilities.get(cap, False):
                        skip = True
                        break
                if not skip:
                    filtered.append(tool)
            tools = filtered

        # Convert to requested format
        result = []
        for tool in tools:
            if format == "mcp":
                result.append(tool.to_mcp_format())
            elif format == "claude":
                result.append(tool.to_claude_format())
            else:  # openai
                result.append(tool.to_openai_format())

        return result

    def get_tool_names(self) -> list[str]:
        """Get list of all registered tool names."""
        if self._plugin_registry is None:
            return []
        return list(self._plugin_registry.tools.keys())

    def get_tools_by_module(self) -> dict[str, list[str]]:
        """Get a mapping of module names to their tool names."""
        if self._plugin_registry is None:
            return {}

        result = {}
        for plugin_id, record in self._plugin_registry.plugins.items():
            if record.tool_names:
                result[plugin_id] = list(record.tool_names)
        return result

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: "ToolContext",
    ) -> str:
        """Execute a tool by name.

        Args:
            tool_name: Name of tool to execute
            arguments: Arguments to pass to tool handler
            context: Execution context with user/platform info

        Returns:
            Tool execution result as a string
        """
        if self._plugin_registry is None:
            return "Error: Plugin registry not initialized"

        tool = self._plugin_registry.get_tool(tool_name)
        if not tool:
            available = list(self._plugin_registry.tools.keys())
            return f"Error: Unknown tool '{tool_name}'. " f"Available tools: {', '.join(available)}"

        try:
            return await tool.handler(arguments, context)
        except Exception as e:
            error_msg = f"Error executing {tool_name}: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return error_msg

    def register_system_prompt(self, module_name: str, prompt: str) -> None:
        """Register a system prompt from a tool module.

        For compatibility with existing code. In the new plugin system,
        plugins should define their own prompts in their manifests.

        Args:
            module_name: Name of module providing this prompt
            prompt: The system prompt text
        """
        # Store prompts as plugin metadata
        if self._plugin_registry is None:
            return

        record = self._plugin_registry.get_plugin(module_name)
        if record:
            # Store in record's extra metadata (would need to add field)
            # For now, just log it
            logger.info(f"System prompt registered for {module_name}")

    def unregister_system_prompt(self, module_name: str) -> bool:
        """Unregister a system prompt.

        Args:
            module_name: Name of module whose prompt to remove

        Returns:
            True if a prompt was removed
        """
        # Placeholder for compatibility
        return False

    def get_system_prompts(
        self,
        platform: str | None = None,
        allowed_modules: list[str] | None = None,
    ) -> str:
        """Get system prompts concatenated.

        Args:
            platform: Optional platform filter
            allowed_modules: If provided, only include prompts from these modules

        Returns:
            Filtered system prompts joined with newlines
        """
        # In the new plugin system, each plugin manages its own prompts
        # This is a compatibility method that returns empty for now
        return ""

    def __len__(self) -> int:
        """Return number of registered tools."""
        if self._plugin_registry is None:
            return 0
        return len(self._plugin_registry.tools)

    def __contains__(self, tool_name: str) -> bool:
        """Check if a tool is registered."""
        if self._plugin_registry is None:
            return False
        return tool_name in self._plugin_registry.tools


# Global adapter instance (singleton pattern)
_adapter_instance = ToolRegistryAdapter()


def get_tool_registry_adapter() -> ToolRegistryAdapter:
    """Get the global ToolRegistryAdapter instance.

    Returns:
        ToolRegistryAdapter singleton
    """
    return _adapter_instance


def set_plugin_registry(registry: "PluginRegistry") -> None:
    """Set the plugin registry for the adapter.

    This should be called during initialization to connect the adapter
    to the actual plugin registry.

    Args:
        registry: PluginRegistry to use
    """
    _adapter_instance.set_plugin_registry(registry)
