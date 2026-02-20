"""Tool-related utilities for LLM providers."""

from clara_core.llm.tools.formats import (
    convert_message_to_anthropic,
    convert_to_claude_format,
    convert_to_mcp_format,
    convert_to_openai_format,
    convert_tools_to_claude_format,
    message_to_anthropic,
    message_to_openai,
    messages_to_anthropic,
    messages_to_langchain,
    messages_to_openai,
)
from clara_core.llm.tools.response import ToolCall, ToolResponse
from clara_core.llm.tools.schema import ToolSchema

__all__ = [
    "ToolResponse",
    "ToolCall",
    "ToolSchema",
    # Tool format converters
    "convert_to_openai_format",
    "convert_to_claude_format",
    "convert_to_mcp_format",
    "convert_tools_to_claude_format",
    # Dict-based message converter (deprecated, use message_to_anthropic)
    "convert_message_to_anthropic",
    # Typed-message converters
    "message_to_openai",
    "messages_to_openai",
    "message_to_anthropic",
    "messages_to_anthropic",
    "messages_to_langchain",
]
