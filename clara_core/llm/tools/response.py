"""Tool response dataclass for unified tool calling results."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    """A single tool call from an LLM response.

    Attributes:
        id: Unique identifier for the tool call
        name: Name of the tool/function being called
        arguments: Arguments as a dict (parsed from JSON)
        raw_arguments: Original arguments string (for debugging)
    """

    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str | None = None

    def to_openai_format(self) -> dict[str, Any]:
        """Convert to OpenAI tool_call format."""
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments),
            },
        }

    @classmethod
    def from_openai(cls, tc: dict) -> "ToolCall":
        """Create from OpenAI tool_call dict."""
        func = tc.get("function", {})
        args_str = func.get("arguments", "{}")
        tool_name = func.get("name", "")

        # Parse arguments
        try:
            args = json.loads(args_str) if isinstance(args_str, str) else args_str
        except json.JSONDecodeError as e:
            logger.warning(
                "Failed to parse tool call arguments for %s: %s. "
                "Raw arguments: %s",
                tool_name,
                str(e),
                args_str[:200] if isinstance(args_str, str) else args_str,
            )
            args = {}

        return cls(
            id=tc.get("id", ""),
            name=tool_name,
            arguments=args,
            raw_arguments=args_str if isinstance(args_str, str) else None,
        )

    @classmethod
    def from_langchain(cls, tc: dict, index: int = 0) -> "ToolCall":
        """Create from LangChain tool_call dict."""
        return cls(
            id=tc.get("id", f"call_{index}"),
            name=tc.get("name", ""),
            arguments=tc.get("args", {}),
        )


@dataclass
class ToolResponse:
    """Unified response from LLM with tool calling support.

    Attributes:
        content: Text content of the response (may be None if only tool calls)
        tool_calls: List of tool calls (empty if no tools called)
        raw_response: Original response object for debugging
        stop_reason: Why the response ended (e.g., "end_turn", "tool_use")
    """

    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_response: Any = None
    stop_reason: str | None = None

    @property
    def has_tool_calls(self) -> bool:
        """Check if the response contains tool calls."""
        return len(self.tool_calls) > 0

    def to_openai_dict(self) -> dict[str, Any]:
        """Convert to OpenAI-compatible message dict.

        Returns dict with:
        - content: Text content (or None)
        - role: "assistant"
        - tool_calls: List of tool calls in OpenAI format (if any)
        """
        result: dict[str, Any] = {
            "content": self.content,
            "role": "assistant",
        }
        if self.tool_calls:
            result["tool_calls"] = [tc.to_openai_format() for tc in self.tool_calls]
        return result

    @classmethod
    def from_openai(cls, response: Any) -> "ToolResponse":
        """Create from OpenAI ChatCompletion response."""
        message = response.choices[0].message

        tool_calls = []
        if message.tool_calls:
            for tc in message.tool_calls:
                tool_calls.append(
                    ToolCall.from_openai(
                        {
                            "id": tc.id,
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                    )
                )

        return cls(
            content=message.content,
            tool_calls=tool_calls,
            raw_response=response,
            stop_reason=response.choices[0].finish_reason,
        )

    @classmethod
    def from_anthropic(cls, message: Any) -> "ToolResponse":
        """Create from Anthropic Message response."""
        tool_calls = []
        text_content = ""

        for block in message.content:
            if block.type == "text":
                text_content += block.text
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=block.input,
                    )
                )

        return cls(
            content=text_content or None,
            tool_calls=tool_calls,
            raw_response=message,
            stop_reason=message.stop_reason,
        )

    @classmethod
    def from_langchain(cls, response: Any) -> "ToolResponse":
        """Create from LangChain AIMessage response."""
        # Handle content
        content = response.content if isinstance(response.content, str) else ""

        # Handle multimodal content (list of content blocks)
        if isinstance(response.content, list):
            text_parts = []
            for block in response.content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    text_parts.append(block)
            content = "".join(text_parts)

        # Convert tool_calls
        tool_calls = []
        if response.tool_calls:
            for i, tc in enumerate(response.tool_calls):
                tool_calls.append(ToolCall.from_langchain(tc, i))

        return cls(
            content=content or None,
            tool_calls=tool_calls,
            raw_response=response,
        )

    @classmethod
    def from_dict(cls, data: dict) -> "ToolResponse":
        """Create from a dict (e.g., from old make_llm_with_tools_langchain)."""
        tool_calls = []
        if data.get("tool_calls"):
            for i, tc in enumerate(data["tool_calls"]):
                tool_calls.append(ToolCall.from_openai(tc))

        return cls(
            content=data.get("content"),
            tool_calls=tool_calls,
        )
