"""Base classes for the Clara tool system.

This module defines the core dataclasses used throughout the tool system:
- ToolDef: Definition of a single tool
- ToolContext: Execution context passed to tool handlers
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    pass


@dataclass
class ToolContext:
    """Context passed to tool handlers during execution.

    Attributes:
        user_id: Unique identifier for the user making the request
        channel_id: Optional channel/conversation identifier
        platform: Platform the request originated from ("discord", "api", "mcp")
        extra: Platform-specific data (e.g., Discord channel object)
    """

    user_id: str = "default"
    channel_id: str | None = None
    platform: str = "api"
    extra: dict[str, Any] = field(default_factory=dict)


# Type alias for tool handlers
ToolHandler = Callable[[dict[str, Any], ToolContext], Awaitable[str]]


@dataclass
class ToolDef:
    """Definition of a single tool.

    Attributes:
        name: Unique identifier for the tool
        description: Human-readable description for LLM consumption
        parameters: JSON Schema defining the tool's input parameters
        handler: Async function that executes the tool
        platforms: List of platforms this tool is available on (None = all)
        requires: List of capabilities required (e.g., ["docker", "email", "files"])
    """

    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
    platforms: list[str] | None = None
    requires: list[str] = field(default_factory=list)

    def to_openai_format(self) -> dict[str, Any]:
        """Convert to OpenAI tool format for LLM consumption."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_mcp_format(self) -> dict[str, Any]:
        """Convert to MCP tool format."""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.parameters,
        }

    def to_claude_format(self) -> dict[str, Any]:
        """Convert to Claude native tool format."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }
