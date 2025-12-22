"""Tool registry for the Clara tool system.

The ToolRegistry is a singleton that manages all registered tools and provides
methods for tool discovery, filtering, and execution.
"""

from __future__ import annotations

import traceback
from typing import Any, ClassVar

from ._base import ToolContext, ToolDef


class ToolRegistry:
    """Central registry for all Clara tools.

    This is a singleton class that manages tool registration, discovery,
    and execution. Tools are registered with metadata including platform
    restrictions and capability requirements.

    Usage:
        registry = ToolRegistry.get_instance()
        registry.register(tool_def, source_module="my_module")
        tools = registry.get_tools(platform="discord")
        result = await registry.execute("tool_name", {"arg": "value"}, context)
    """

    _instance: ClassVar[ToolRegistry | None] = None

    def __init__(self) -> None:
        """Initialize the registry. Use get_instance() instead of direct instantiation."""
        self._tools: dict[str, ToolDef] = {}
        self._tool_sources: dict[str, str] = {}  # tool_name -> module_name
        self._initialized = False

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
        if tool.name in self._tools:
            existing_source = self._tool_sources.get(tool.name)
            if existing_source != source_module:
                raise ValueError(
                    f"Tool '{tool.name}' already registered by '{existing_source}'"
                )
            # Allow re-registration from same module (hot-reload case)

        self._tools[tool.name] = tool
        self._tool_sources[tool.name] = source_module

    def unregister(self, tool_name: str) -> bool:
        """Unregister a single tool.

        Args:
            tool_name: Name of the tool to unregister

        Returns:
            True if the tool was unregistered, False if it wasn't found
        """
        if tool_name in self._tools:
            del self._tools[tool_name]
            del self._tool_sources[tool_name]
            return True
        return False

    def unregister_module(self, module_name: str) -> list[str]:
        """Unregister all tools from a specific module.

        This is used during hot-reload to remove all tools from a module
        before reloading it.

        Args:
            module_name: Name of the module whose tools should be unregistered

        Returns:
            List of tool names that were unregistered
        """
        removed = []
        for tool_name, source in list(self._tool_sources.items()):
            if source == module_name:
                del self._tools[tool_name]
                del self._tool_sources[tool_name]
                removed.append(tool_name)
        return removed

    def get_tool(self, name: str) -> ToolDef | None:
        """Get a single tool definition by name."""
        return self._tools.get(name)

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
        tools = []
        for tool in self._tools.values():
            # Platform filter
            if platform and tool.platforms and platform not in tool.platforms:
                continue

            # Capability filter
            if capabilities:
                skip = False
                for cap in tool.requires:
                    if not capabilities.get(cap, False):
                        skip = True
                        break
                if skip:
                    continue

            # Format conversion
            if format == "mcp":
                tools.append(tool.to_mcp_format())
            elif format == "claude":
                tools.append(tool.to_claude_format())
            else:  # openai
                tools.append(tool.to_openai_format())

        return tools

    def get_tool_names(self) -> list[str]:
        """Get list of all registered tool names."""
        return list(self._tools.keys())

    def get_tools_by_module(self) -> dict[str, list[str]]:
        """Get a mapping of module names to their tool names."""
        result: dict[str, list[str]] = {}
        for tool_name, module_name in self._tool_sources.items():
            if module_name not in result:
                result[module_name] = []
            result[module_name].append(tool_name)
        return result

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
        tool = self._tools.get(tool_name)
        if not tool:
            return f"Error: Unknown tool '{tool_name}'. Available tools: {', '.join(self._tools.keys())}"

        try:
            return await tool.handler(arguments, context)
        except Exception as e:
            error_msg = f"Error executing {tool_name}: {str(e)}"
            # Include traceback in debug mode
            tb = traceback.format_exc()
            print(f"[tools] {error_msg}\n{tb}")
            return error_msg

    def __len__(self) -> int:
        """Return the number of registered tools."""
        return len(self._tools)

    def __contains__(self, tool_name: str) -> bool:
        """Check if a tool is registered."""
        return tool_name in self._tools
