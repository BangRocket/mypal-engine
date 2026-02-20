"""Tool registry for the Clara tool system.

This module provides a backwards-compatible singleton that wraps the
PluginRegistry from mypalclara.core.plugins. All tool management now goes
through the unified plugin system.

The ToolRegistry class maintains the same API as before, but delegates
all operations to PluginRegistry for unified tool/plugin management.
"""

from __future__ import annotations

import json
import logging
from typing import Any, ClassVar

from ._base import ToolContext, ToolDef

logger = logging.getLogger("tools.registry")


def validate_tool_args(
    tool_name: str,
    args: dict[str, Any],
    parameters: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Validate and coerce tool arguments against JSON schema.

    Handles common issues like:
    - String-to-array coercion (when LLM passes "item" instead of ["item"])
    - String-to-integer coercion
    - String-to-boolean coercion
    - Missing required parameters

    Args:
        tool_name: Name of the tool (for error messages)
        args: Arguments to validate
        parameters: JSON Schema for the tool's parameters

    Returns:
        (validated_args, warnings) - Coerced arguments and list of any issues
    """
    validated: dict[str, Any] = {}
    warnings: list[str] = []

    props = parameters.get("properties", {})
    required = set(parameters.get("required", []))

    # Check required params
    for req in required:
        if req not in args:
            warnings.append(f"Missing required parameter: {req}")

    # Validate and coerce each argument
    for name, value in args.items():
        if name not in props:
            # Unknown param - pass through but warn
            warnings.append(f"Unknown parameter: {name}")
            validated[name] = value
            continue

        prop_def = props[name]
        expected_type = prop_def.get("type")

        # Type coercion
        if expected_type == "array" and isinstance(value, str):
            # Try to parse as JSON array, or wrap single value
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    validated[name] = parsed
                else:
                    validated[name] = [value]
                    warnings.append(f"Wrapped string as array for {name}")
            except json.JSONDecodeError:
                validated[name] = [value]
                warnings.append(f"Wrapped string as array for {name}")

        elif expected_type == "integer" and isinstance(value, str):
            try:
                validated[name] = int(value)
            except ValueError:
                validated[name] = value
                warnings.append(f"Failed to coerce {name} to integer")

        elif expected_type == "number" and isinstance(value, str):
            try:
                validated[name] = float(value)
            except ValueError:
                validated[name] = value
                warnings.append(f"Failed to coerce {name} to number")

        elif expected_type == "boolean" and isinstance(value, str):
            validated[name] = value.lower() in ("true", "1", "yes")

        else:
            validated[name] = value

    return validated, warnings


class ToolRegistry:
    """Backwards-compatible wrapper around PluginRegistry.

    This class maintains the original ToolRegistry API while delegating
    all operations to the unified PluginRegistry from mypalclara.core.plugins.

    Usage:
        registry = ToolRegistry.get_instance()
        registry.register(tool_def, source_module="my_module")
        tools = registry.get_tools(platform="discord")
        result = await registry.execute("tool_name", {"arg": "value"}, context)
    """

    _instance: ClassVar[ToolRegistry | None] = None

    def __init__(self) -> None:
        """Initialize the registry wrapper."""
        self._plugin_registry = None
        self._initialized = False

    def _get_plugin_registry(self):
        """Lazy-load the plugin registry to avoid circular imports."""
        if self._plugin_registry is None:
            from mypalclara.core.plugins import get_registry

            self._plugin_registry = get_registry()
        return self._plugin_registry

    @classmethod
    def get_instance(cls) -> ToolRegistry:
        """Get or create the singleton registry instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton instance. Useful for testing."""
        cls._instance = None

    def register(self, tool: ToolDef, source_module: str = "builtin") -> None:
        """Register a tool definition.

        Args:
            tool: The tool definition to register
            source_module: Name of the module providing this tool (for hot-reload)

        Raises:
            ValueError: If a tool with the same name is already registered
                       by a different module
        """
        registry = self._get_plugin_registry()

        # Check for conflicts before registering
        existing_source = registry.tool_sources.get(tool.name)
        if existing_source and existing_source != source_module:
            raise ValueError(f"Tool '{tool.name}' already registered by '{existing_source}'")

        registry.register_tool(tool, plugin_id=source_module)

    def unregister(self, tool_name: str) -> bool:
        """Unregister a single tool.

        Args:
            tool_name: Name of the tool to unregister

        Returns:
            True if the tool was unregistered, False if it wasn't found
        """
        return self._get_plugin_registry().unregister_tool(tool_name)

    def unregister_module(self, module_name: str) -> list[str]:
        """Unregister all tools from a specific module.

        This is used during hot-reload to remove all tools from a module
        before reloading it.

        Args:
            module_name: Name of the module whose tools should be unregistered

        Returns:
            List of tool names that were unregistered
        """
        return self._get_plugin_registry().unregister_module(module_name)

    def get_tool(self, name: str) -> ToolDef | None:
        """Get a single tool definition by name."""
        return self._get_plugin_registry().get_tool(name)

    def get_tools(
        self,
        platform: str | None = None,
        capabilities: dict[str, bool] | None = None,
        format: str = "openai",
    ) -> list[dict[str, Any]]:
        """Get tool definitions filtered by platform and capabilities.

        Args:
            platform: Filter to tools available on this platform (None = all)
            capabilities: Dict of capability -> available (e.g., {"docker": True})
            format: Output format - "openai", "mcp", or "claude"

        Returns:
            List of tool definitions in the requested format
        """
        registry = self._get_plugin_registry()
        return registry.get_tools(platform, capabilities, format)

    def get_tool_names(self) -> list[str]:
        """Get list of all registered tool names."""
        return self._get_plugin_registry().get_tool_names()

    def get_tools_by_module(self) -> dict[str, list[str]]:
        """Get a mapping of module names to their tool names."""
        return self._get_plugin_registry().get_tools_by_module()

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> str:
        """Execute a tool by name.

        Args:
            tool_name: Name of the tool to execute
            arguments: Arguments to pass to the tool handler
            context: Execution context with user/platform info

        Returns:
            Tool execution result as a string
        """
        return await self._get_plugin_registry().execute(tool_name, arguments, context)

    def register_system_prompt(self, module_name: str, prompt: str) -> None:
        """Register a system prompt from a tool module.

        Args:
            module_name: Name of the module providing this prompt
            prompt: The system prompt text describing the module's tools
        """
        self._get_plugin_registry().register_system_prompt(module_name, prompt)

    def unregister_system_prompt(self, module_name: str) -> bool:
        """Unregister a system prompt.

        Args:
            module_name: Name of the module whose prompt to remove

        Returns:
            True if a prompt was removed, False if not found
        """
        return self._get_plugin_registry().unregister_system_prompt(module_name)

    def get_system_prompts(
        self,
        platform: str | None = None,
        allowed_modules: list[str] | None = None,
    ) -> str:
        """Get system prompts concatenated, optionally filtered by module.

        Args:
            platform: Optional platform filter (not currently used but reserved)
            allowed_modules: If provided, only include prompts from these modules.
                            If None, includes all prompts (legacy behavior).

        Returns:
            Filtered system prompts joined with newlines
        """
        return self._get_plugin_registry().get_system_prompts(platform, allowed_modules)

    def __len__(self) -> int:
        """Return the number of registered tools."""
        return len(self._get_plugin_registry())

    def __contains__(self, tool_name: str) -> bool:
        """Check if a tool is registered."""
        return tool_name in self._get_plugin_registry()

    # Legacy properties for backwards compatibility
    @property
    def _tools(self) -> dict[str, ToolDef]:
        """Direct access to tools dict (deprecated, use get_tool instead)."""
        return self._get_plugin_registry().tools

    @property
    def _tool_sources(self) -> dict[str, str]:
        """Direct access to tool sources (deprecated)."""
        return self._get_plugin_registry().tool_sources

    @property
    def _system_prompts(self) -> dict[str, str]:
        """Direct access to system prompts (deprecated)."""
        return self._get_plugin_registry().system_prompts
