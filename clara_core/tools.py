"""Unified tool registry for Clara platform.

Provides a central registry for all tools that Clara can use, with support for:
- Platform-specific tool filtering
- Async tool execution
- Tool definition in OpenAI format
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar


@dataclass
class ToolDefinition:
    """Definition of a tool that Clara can use."""

    # Tool name (must be unique)
    name: str

    # Tool description for the LLM
    description: str

    # Parameters schema (OpenAI format)
    parameters: dict

    # Async handler function: (args: dict, context: Any) -> str
    handler: Callable[[dict, Any], Awaitable[str]]

    # Platforms this tool is available on (None = all platforms)
    platforms: list[str] | None = None

    # Whether this tool requires special permissions
    requires_docker: bool = False
    requires_email: bool = False
    requires_files: bool = False

    def to_openai_format(self) -> dict:
        """Convert to OpenAI tool format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """Central registry for all Clara tools.

    Usage:
        # Initialize (call once at startup)
        registry = ToolRegistry.initialize()

        # Register a tool
        registry.register(
            name="my_tool",
            description="Does something useful",
            parameters={"type": "object", "properties": {...}},
            handler=my_async_handler,
            platforms=["discord"],  # Optional: restrict to specific platforms
        )

        # Get tools for a platform
        tools = registry.get_tools(platform="discord")

        # Execute a tool
        result = await registry.execute("my_tool", {"arg": "value"}, context)
    """

    _instance: ClassVar["ToolRegistry | None"] = None

    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}

    @classmethod
    def get_instance(cls) -> "ToolRegistry":
        """Get the singleton registry instance."""
        if cls._instance is None:
            raise RuntimeError(
                "ToolRegistry not initialized. Call ToolRegistry.initialize() first."
            )
        return cls._instance

    @classmethod
    def initialize(cls) -> "ToolRegistry":
        """Initialize the singleton registry."""
        if cls._instance is None:
            cls._instance = cls()
            cls._instance._register_builtin_tools()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton (for testing)."""
        cls._instance = None

    def register(
        self,
        name: str,
        description: str,
        parameters: dict,
        handler: Callable[[dict, Any], Awaitable[str]],
        platforms: list[str] | None = None,
        requires_docker: bool = False,
        requires_email: bool = False,
        requires_files: bool = False,
    ) -> None:
        """Register a new tool.

        Args:
            name: Unique tool name
            description: Description for the LLM
            parameters: OpenAI-format parameter schema
            handler: Async function(args, context) -> result_string
            platforms: List of platforms this tool works on (None = all)
            requires_docker: Whether this tool needs Docker
            requires_email: Whether this tool needs email access
            requires_files: Whether this tool needs file storage
        """
        if name in self._tools:
            raise ValueError(f"Tool '{name}' is already registered")

        self._tools[name] = ToolDefinition(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
            platforms=platforms,
            requires_docker=requires_docker,
            requires_email=requires_email,
            requires_files=requires_files,
        )

    def unregister(self, name: str) -> None:
        """Remove a tool from the registry."""
        if name in self._tools:
            del self._tools[name]

    def get_tool(self, name: str) -> ToolDefinition | None:
        """Get a specific tool by name."""
        return self._tools.get(name)

    def get_tools(
        self,
        platform: str | None = None,
        include_docker: bool = True,
        include_email: bool = True,
        include_files: bool = True,
    ) -> list[dict]:
        """Get tools as OpenAI-format definitions.

        Args:
            platform: Filter to tools available on this platform
            include_docker: Include tools that require Docker
            include_email: Include tools that require email
            include_files: Include tools that require file storage

        Returns:
            List of tool definitions in OpenAI format
        """
        tools = []
        for tool in self._tools.values():
            # Check platform filter
            if platform and tool.platforms and platform not in tool.platforms:
                continue

            # Check capability filters
            if tool.requires_docker and not include_docker:
                continue
            if tool.requires_email and not include_email:
                continue
            if tool.requires_files and not include_files:
                continue

            tools.append(tool.to_openai_format())

        return tools

    def get_tool_names(self, platform: str | None = None) -> list[str]:
        """Get list of available tool names."""
        tools = self.get_tools(platform=platform)
        return [t["function"]["name"] for t in tools]

    async def execute(
        self,
        name: str,
        arguments: dict,
        context: Any = None,
    ) -> str:
        """Execute a tool by name.

        Args:
            name: Tool name
            arguments: Tool arguments
            context: Platform-specific context

        Returns:
            Tool result as string

        Raises:
            ValueError: If tool not found
        """
        tool = self._tools.get(name)
        if not tool:
            return f"Error: Unknown tool '{name}'"

        try:
            result = await tool.handler(arguments, context)
            return result
        except Exception as e:
            return f"Error executing {name}: {str(e)}"

    def _register_builtin_tools(self) -> None:
        """Register built-in tools.

        This registers tools that are always available.
        Platform-specific tools (Docker, email, files) are registered
        separately when those subsystems are initialized.
        """
        # Web search tool (available on all platforms if TAVILY_API_KEY is set)
        import os

        if os.getenv("TAVILY_API_KEY"):
            self.register(
                name="web_search",
                description="Search the web for current information. Use this when you need up-to-date information that may not be in your training data.",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query",
                        },
                    },
                    "required": ["query"],
                },
                handler=self._web_search_handler,
            )

    async def _web_search_handler(self, args: dict, context: Any) -> str:
        """Built-in web search handler using Tavily."""
        import os

        import httpx

        query = args.get("query", "")
        if not query:
            return "Error: No search query provided"

        api_key = os.getenv("TAVILY_API_KEY")
        if not api_key:
            return "Error: Web search not configured (TAVILY_API_KEY not set)"

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": api_key,
                        "query": query,
                        "search_depth": "basic",
                        "include_answer": True,
                        "max_results": 5,
                    },
                    timeout=30.0,
                )
                response.raise_for_status()
                data = response.json()

                # Format results
                results = []
                if data.get("answer"):
                    results.append(f"Summary: {data['answer']}\n")

                for r in data.get("results", []):
                    results.append(f"- {r.get('title', 'No title')}")
                    results.append(f"  URL: {r.get('url', '')}")
                    results.append(f"  {r.get('content', '')[:200]}...")
                    results.append("")

                return "\n".join(results) if results else "No results found"

        except Exception as e:
            return f"Search error: {str(e)}"


def register_docker_tools(registry: ToolRegistry) -> None:
    """Register Docker sandbox tools.

    Call this after initializing Docker sandbox manager.
    """
    # Import here to avoid circular imports
    try:
        from sandbox.docker import (
            get_sandbox_manager,
            DOCKER_TOOLS,
        )
    except ImportError:
        print("[tools] Docker tools not available (sandbox.docker module not found)")
        return

    manager = get_sandbox_manager()
    if manager is None:
        print("[tools] Docker tools not available (sandbox manager not initialized)")
        return

    # Register each Docker tool
    for tool_def in DOCKER_TOOLS:
        func_info = tool_def.get("function", {})
        name = func_info.get("name")
        if not name:
            continue

        async def make_handler(tool_name: str):
            async def handler(args: dict, context: Any) -> str:
                # Get user ID from context
                user_id = "default"
                if hasattr(context, "user_id"):
                    user_id = context.user_id
                elif isinstance(context, dict):
                    user_id = context.get("user_id", "default")

                sandbox = manager.get_or_create_sandbox(user_id)
                return await sandbox.execute_tool(tool_name, args)

            return handler

        registry.register(
            name=name,
            description=func_info.get("description", ""),
            parameters=func_info.get("parameters", {"type": "object", "properties": {}}),
            handler=make_handler(name),
            platforms=["discord"],  # Docker tools only for Discord
            requires_docker=True,
        )

    print(f"[tools] Registered {len(DOCKER_TOOLS)} Docker tools")


def register_local_file_tools(registry: ToolRegistry) -> None:
    """Register local file storage tools.

    These tools allow saving and reading files that persist across sessions.
    """
    try:
        from storage.local_files import LOCAL_FILE_TOOLS, get_file_manager
    except ImportError:
        print("[tools] Local file tools not available")
        return

    manager = get_file_manager()
    if manager is None:
        print("[tools] Local file manager not initialized")
        return

    for tool_def in LOCAL_FILE_TOOLS:
        func_info = tool_def.get("function", {})
        name = func_info.get("name")
        if not name:
            continue

        async def make_handler(tool_name: str):
            async def handler(args: dict, context: Any) -> str:
                user_id = "default"
                if hasattr(context, "user_id"):
                    user_id = context.user_id
                elif isinstance(context, dict):
                    user_id = context.get("user_id", "default")

                return await manager.execute_tool(tool_name, args, user_id)

            return handler

        registry.register(
            name=name,
            description=func_info.get("description", ""),
            parameters=func_info.get("parameters", {"type": "object", "properties": {}}),
            handler=make_handler(name),
            requires_files=True,
        )

    print(f"[tools] Registered {len(LOCAL_FILE_TOOLS)} local file tools")


def register_email_tools(registry: ToolRegistry) -> None:
    """Register email tools."""
    try:
        from email_monitor import EMAIL_TOOLS
    except ImportError:
        print("[tools] Email tools not available")
        return

    for tool_def in EMAIL_TOOLS:
        func_info = tool_def.get("function", {})
        name = func_info.get("name")
        if not name:
            continue

        async def make_handler(tool_name: str):
            async def handler(args: dict, context: Any) -> str:
                from email_monitor import execute_email_tool

                return await execute_email_tool(tool_name, args)

            return handler

        registry.register(
            name=name,
            description=func_info.get("description", ""),
            parameters=func_info.get("parameters", {"type": "object", "properties": {}}),
            handler=make_handler(name),
            platforms=["discord"],  # Email tools only for Discord
            requires_email=True,
        )

    print(f"[tools] Registered {len(EMAIL_TOOLS)} email tools")
