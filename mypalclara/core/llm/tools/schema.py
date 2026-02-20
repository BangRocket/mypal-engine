"""Unified tool schema for the LLM pipeline.

ToolSchema is a lightweight, frozen dataclass that normalizes tool definitions
from multiple sources (ToolDef, MCPTool, raw dicts) into a single type.
Format conversion (OpenAI, Claude, MCP) happens at the provider boundary.

Pipeline:
    ToolDef / MCPTool / dict  →  ToolSchema  →  Provider  →  LLM SDK
                                  ^^^^^^^^^^^
                                  Unified, typed
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolSchema:
    """Normalized tool definition for the LLM pipeline.

    Attributes:
        name: Unique tool identifier (e.g., "execute_python").
        description: Human-readable description for the LLM.
        parameters: JSON Schema dict describing input parameters.
    """

    name: str
    description: str
    parameters: dict[str, Any]

    def to_openai(self) -> dict[str, Any]:
        """Convert to OpenAI tool format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_claude(self) -> dict[str, Any]:
        """Convert to Anthropic Claude tool format."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }

    def to_mcp(self) -> dict[str, Any]:
        """Convert to MCP tool format."""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.parameters,
        }

    @classmethod
    def from_openai_dict(cls, d: dict[str, Any]) -> ToolSchema:
        """Create from an OpenAI-format tool dict.

        Handles:
        - Full OpenAI format: {"type": "function", "function": {...}}
        - Claude format: {"name": ..., "input_schema": ...}
        - MCP format: {"name": ..., "inputSchema": ...}
        - Minimal format: {"name": ..., "parameters": ...}
        """
        if d.get("type") == "function" and "function" in d:
            func = d["function"]
            return cls(
                name=func.get("name", "unknown"),
                description=func.get("description", ""),
                parameters=func.get("parameters", {"type": "object", "properties": {}}),
            )

        if "input_schema" in d:
            return cls(
                name=d.get("name", "unknown"),
                description=d.get("description", ""),
                parameters=d["input_schema"],
            )

        if "inputSchema" in d:
            return cls(
                name=d.get("name", "unknown"),
                description=d.get("description", ""),
                parameters=d["inputSchema"],
            )

        return cls(
            name=d.get("name", "unknown"),
            description=d.get("description", ""),
            parameters=d.get("parameters", {"type": "object", "properties": {}}),
        )

    @classmethod
    def from_tool_def(cls, td: Any) -> ToolSchema:
        """Create from a ToolDef instance.

        Args:
            td: A tools._base.ToolDef dataclass instance.
        """
        return cls(
            name=td.name,
            description=td.description,
            parameters=td.parameters,
        )
