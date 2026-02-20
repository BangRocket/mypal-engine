"""Example plugin demonstrating the new plugin system.

This plugin provides file system tools and shows how to:
- Define a plugin manifest
- Use the register() function
- Register tools with context
- Register hooks
"""

from clara_core.plugins import (
    PluginAPI,
    PluginContext,
)
from tools._base import ToolContext, ToolDef


def register(api: PluginAPI):
    """Plugin entry point - called when plugin is loaded.

    Args:
        api: PluginAPI for registering tools, hooks, etc.
    """
    api.info(f"Registering example plugin: {api.id}")

    # Register a tool using factory pattern
    api.register_tool(
        lambda ctx: [
            ToolDef(
                name="example_echo",
                description="Echo back the provided message",
                parameters={
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "Message to echo back",
                        }
                    },
                    "required": ["message"],
                },
                handler=_make_echo_handler(api),
            )
        ]
    )

    # Register a hook for tool execution
    async def on_tool_end(tool_name: str, result: str, **kwargs):
        """Called after any tool executes."""
        if tool_name == "example_echo":
            api.info(f"Echo tool executed: {result}")

    api.register_hook("tool_end", on_tool_end)


def _make_echo_handler(api: PluginAPI):
    """Create a tool handler that uses the plugin API.

    Args:
        api: PluginAPI instance

    Returns:
        Async tool handler function
    """

    async def handler(args: dict, ctx: ToolContext) -> str:
        message = args.get("message", "")
        api.info(f"Echoing: {message} for user {ctx.user_id}")
        return f"Echo: {message}"

    return handler
