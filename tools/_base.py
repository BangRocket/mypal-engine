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
        emoji: Emoji to display in status messages (default: 🔧)
        label: Short display label (defaults to name if None)
        detail_keys: Parameter keys to show in status (e.g., ["query", "filename"])
        risk_level: Risk level for policy engine ("safe", "moderate", "dangerous")
        intent: Tool intent for policy classification ("read", "write", "execute", "network")
    """

    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
    platforms: list[str] | None = None
    requires: list[str] = field(default_factory=list)
    # Display metadata
    emoji: str = "🔧"
    label: str | None = None
    detail_keys: list[str] = field(default_factory=list)
    # Policy metadata
    risk_level: str = "safe"  # safe, moderate, dangerous
    intent: str = "read"  # read, write, execute, network

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

    def get_display_label(self) -> str:
        """Get the display label for status messages.

        Returns label if set, otherwise converts name to title case.
        """
        if self.label:
            return self.label
        # Convert snake_case to Title Case
        return self.name.replace("_", " ").title()

    def format_status(self, params: dict[str, Any], step: int | None = None) -> str:
        """Format a status message for this tool execution.

        Args:
            params: Tool parameters being passed
            step: Optional step number for multi-tool sequences

        Returns:
            Formatted status string like "-# 🐍 Python... (analyzing CSV) (step 1)"
        """
        label = self.get_display_label()

        # Extract detail values from params
        details = []
        for key in self.detail_keys:
            if key in params:
                val = str(params[key])
                # Truncate long values
                if len(val) > 50:
                    val = val[:47] + "..."
                details.append(val)

        detail_str = f" ({', '.join(details)})" if details else ""
        step_str = f" (step {step})" if step is not None else ""

        return f"-# {self.emoji} {label}...{detail_str}{step_str}"
