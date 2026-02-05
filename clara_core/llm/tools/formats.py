"""Format converters for tool definitions and messages.

Handles conversion between:
- OpenAI format ({"type": "function", "function": {...}})
- Claude format ({"name": ..., "input_schema": ...})
- MCP format ({"name": ..., "inputSchema": ...})
- LangChain format (varies by model)
"""

from __future__ import annotations

import json
from typing import Any


def convert_to_openai_format(tool: dict[str, Any]) -> dict[str, Any]:
    """Convert a tool definition to OpenAI format.

    Args:
        tool: Tool definition in any supported format

    Returns:
        Tool in OpenAI format
    """
    # Already OpenAI format
    if tool.get("type") == "function" and "function" in tool:
        return tool

    # From Claude format
    if "input_schema" in tool:
        return {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool["input_schema"],
            },
        }

    # From MCP format
    if "inputSchema" in tool:
        return {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool["inputSchema"],
            },
        }

    # Assume minimal format with name and parameters
    return {
        "type": "function",
        "function": {
            "name": tool.get("name", "unknown"),
            "description": tool.get("description", ""),
            "parameters": tool.get("parameters", {"type": "object", "properties": {}}),
        },
    }


def convert_to_claude_format(tool: dict[str, Any]) -> dict[str, Any]:
    """Convert a tool definition to Claude format.

    Args:
        tool: Tool definition in any supported format

    Returns:
        Tool in Claude format
    """
    # From OpenAI format
    if tool.get("type") == "function" and "function" in tool:
        func = tool["function"]
        return {
            "name": func.get("name"),
            "description": func.get("description", ""),
            "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
        }

    # Already Claude format
    if "input_schema" in tool:
        return tool

    # From MCP format
    if "inputSchema" in tool:
        return {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "input_schema": tool["inputSchema"],
        }

    # Assume minimal format
    return {
        "name": tool.get("name", "unknown"),
        "description": tool.get("description", ""),
        "input_schema": tool.get("parameters", {"type": "object", "properties": {}}),
    }


def convert_to_mcp_format(tool: dict[str, Any]) -> dict[str, Any]:
    """Convert a tool definition to MCP format.

    Args:
        tool: Tool definition in any supported format

    Returns:
        Tool in MCP format
    """
    # From OpenAI format
    if tool.get("type") == "function" and "function" in tool:
        func = tool["function"]
        return {
            "name": func.get("name"),
            "description": func.get("description", ""),
            "inputSchema": func.get("parameters", {"type": "object", "properties": {}}),
        }

    # From Claude format
    if "input_schema" in tool:
        return {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "inputSchema": tool["input_schema"],
        }

    # Already MCP format
    if "inputSchema" in tool:
        return tool

    # Assume minimal format
    return {
        "name": tool.get("name", "unknown"),
        "description": tool.get("description", ""),
        "inputSchema": tool.get("parameters", {"type": "object", "properties": {}}),
    }


def convert_tools_to_claude_format(tools: list[dict]) -> list[dict]:
    """Convert a list of tools to Claude format.

    Args:
        tools: List of tool definitions in OpenAI format

    Returns:
        List of tools in Claude format
    """
    return [convert_to_claude_format(tool) for tool in tools]


def convert_message_to_anthropic(msg: dict) -> dict:
    """Convert a single OpenAI-style message to Anthropic format.

    Handles:
    - Assistant messages with tool_calls -> assistant with tool_use content blocks
    - Tool role messages -> user messages with tool_result content blocks
    - User messages with multimodal content (images) -> converted to Anthropic image format
    - Regular messages -> pass through

    Args:
        msg: OpenAI-style message dict

    Returns:
        Anthropic-style message dict
    """
    role = msg.get("role")

    if role == "assistant" and msg.get("tool_calls"):
        # Convert assistant with tool_calls to Claude format
        content = []
        if msg.get("content"):
            content.append({"type": "text", "text": msg["content"]})
        for tc in msg["tool_calls"]:
            args = tc["function"]["arguments"]
            if isinstance(args, str):
                args = json.loads(args)
            content.append(
                {
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["function"]["name"],
                    "input": args,
                }
            )
        return {"role": "assistant", "content": content}

    elif role == "tool":
        # Convert tool result to user message with tool_result
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": msg["tool_call_id"],
                    "content": msg.get("content", ""),
                }
            ],
        }

    elif role == "user" and isinstance(msg.get("content"), list):
        # Convert multimodal content from OpenAI format to Anthropic format
        converted_content = []
        for part in msg["content"]:
            if part.get("type") == "text":
                converted_content.append(part)
            elif part.get("type") == "image_url":
                # Convert from OpenAI's data URL format to Anthropic's base64 format
                image_url = part.get("image_url", {}).get("url", "")
                if image_url.startswith("data:"):
                    # Parse data URL: data:image/png;base64,<data>
                    try:
                        header, base64_data = image_url.split(",", 1)
                        media_type = header.split(":")[1].split(";")[0]
                        converted_content.append(
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": base64_data,
                                },
                            }
                        )
                    except (ValueError, IndexError):
                        # Fall back to passing as-is if parsing fails
                        converted_content.append(part)
                else:
                    # HTTP URL - Anthropic supports URLs directly
                    converted_content.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "url",
                                "url": image_url,
                            },
                        }
                    )
        return {"role": "user", "content": converted_content}

    return msg


def anthropic_to_openai_response(msg: Any) -> dict:
    """Convert Anthropic Message to OpenAI-like response dict for compatibility.

    This allows code to process Anthropic responses using the same
    code path as OpenAI responses.

    Args:
        msg: Anthropic Message object

    Returns:
        Dict with:
        - content: text content (or None)
        - role: "assistant"
        - tool_calls: list of tool calls in OpenAI format (if any)
    """
    tool_calls = []
    text_content = ""

    for block in msg.content:
        if block.type == "text":
            text_content += block.text
        elif block.type == "tool_use":
            tool_calls.append(
                {
                    "id": block.id,
                    "type": "function",
                    "function": {
                        "name": block.name,
                        "arguments": json.dumps(block.input),
                    },
                }
            )

    result = {
        "content": text_content or None,
        "role": "assistant",
    }
    if tool_calls:
        result["tool_calls"] = tool_calls

    return result


def convert_messages_to_langchain(messages: list[dict]) -> list:
    """Convert OpenAI-style messages to LangChain format.

    Args:
        messages: List of OpenAI-style message dicts

    Returns:
        List of LangChain message objects
    """
    from langchain_core.messages import (
        AIMessage,
        HumanMessage,
        SystemMessage,
        ToolMessage,
    )

    lc_messages = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content", "")

        if role == "system":
            lc_messages.append(SystemMessage(content=content))
        elif role == "user":
            # Handle multimodal content
            if isinstance(content, list):
                lc_messages.append(HumanMessage(content=content))
            else:
                lc_messages.append(HumanMessage(content=content))
        elif role == "assistant":
            # Handle assistant with tool_calls
            if msg.get("tool_calls"):
                # LangChain needs tool_calls in a specific format
                ai_msg = AIMessage(
                    content=content or "",
                    tool_calls=[
                        {
                            "id": tc["id"],
                            "name": tc["function"]["name"],
                            "args": json.loads(tc["function"]["arguments"])
                            if isinstance(tc["function"]["arguments"], str)
                            else tc["function"]["arguments"],
                        }
                        for tc in msg["tool_calls"]
                    ],
                )
                lc_messages.append(ai_msg)
            else:
                lc_messages.append(AIMessage(content=content))
        elif role == "tool":
            lc_messages.append(
                ToolMessage(
                    content=content,
                    tool_call_id=msg.get("tool_call_id", ""),
                )
            )

    return lc_messages
