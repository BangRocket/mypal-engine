"""Format converters for tool definitions and messages.

Handles conversion between:
- OpenAI format ({"type": "function", "function": {...}})
- Claude format ({"name": ..., "input_schema": ...})
- MCP format ({"name": ..., "inputSchema": ...})
- LangChain format (varies by model)

Typed-message converters (Message -> provider-specific format):
- message_to_openai / messages_to_openai
- message_to_anthropic / messages_to_anthropic
- messages_to_langchain (from typed Messages)
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mypalclara.core.llm.messages import Message


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

    .. deprecated::
        Use ``message_to_anthropic()`` with typed Message objects instead.

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


# =============================================================================
# Typed-message converters (Message -> provider-specific format)
# =============================================================================


def message_to_openai(msg: "Message") -> dict[str, Any]:
    """Convert a typed Message to OpenAI-format dict.

    Delegates to each message type's to_dict() method, which already
    produces OpenAI format.

    Args:
        msg: A typed Message instance.

    Returns:
        OpenAI-format message dict.
    """
    return msg.to_dict()


def messages_to_openai(msgs: list["Message"]) -> list[dict[str, Any]]:
    """Convert a list of typed Messages to OpenAI-format dicts.

    Args:
        msgs: List of typed Message instances.

    Returns:
        List of OpenAI-format message dicts.
    """
    return [message_to_openai(m) for m in msgs]


def message_to_anthropic(msg: "Message") -> dict[str, Any]:
    """Convert a typed Message to Anthropic API format.

    Handles the key differences from OpenAI format:
    - Assistant messages with tool_calls -> content blocks with tool_use
    - ToolResultMessage -> user message with tool_result content block
    - UserMessage with multimodal parts -> Anthropic image format
    - SystemMessage -> pass through (caller should extract separately)

    Args:
        msg: A typed Message instance.

    Returns:
        Anthropic-format message dict.
    """
    from mypalclara.core.llm.messages import (
        AssistantMessage as AssistantMsg,
    )
    from mypalclara.core.llm.messages import (
        ContentPartType,
    )
    from mypalclara.core.llm.messages import (
        SystemMessage as SystemMsg,
    )
    from mypalclara.core.llm.messages import (
        ToolResultMessage as ToolResultMsg,
    )
    from mypalclara.core.llm.messages import (
        UserMessage as UserMsg,
    )

    if isinstance(msg, SystemMsg):
        return {"role": "system", "content": msg.content}

    if isinstance(msg, UserMsg):
        if msg.parts:
            converted_content = []
            for part in msg.parts:
                if part.type == ContentPartType.TEXT:
                    converted_content.append({"type": "text", "text": part.text or ""})
                elif part.type == ContentPartType.IMAGE_BASE64:
                    converted_content.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": part.media_type or "image/jpeg",
                                "data": part.data or "",
                            },
                        }
                    )
                elif part.type == ContentPartType.IMAGE_URL:
                    converted_content.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "url",
                                "url": part.url or "",
                            },
                        }
                    )
            return {"role": "user", "content": converted_content}
        return {"role": "user", "content": msg.content}

    if isinstance(msg, AssistantMsg):
        if msg.tool_calls:
            content: list[dict[str, Any]] = []
            if msg.content:
                content.append({"type": "text", "text": msg.content})
            for tc in msg.tool_calls:
                content.append(
                    {
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    }
                )
            return {"role": "assistant", "content": content}
        return {"role": "assistant", "content": msg.content or ""}

    if isinstance(msg, ToolResultMsg):
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": msg.tool_call_id,
                    "content": msg.content,
                }
            ],
        }

    return msg.to_dict()


def messages_to_anthropic(
    msgs: list["Message"],
) -> tuple[str, list[dict[str, Any]]]:
    """Convert typed Messages to Anthropic API format, extracting system messages.

    Anthropic's API takes system as a separate parameter. This function
    splits system messages out and joins them, returning the rest as
    converted API messages.

    Args:
        msgs: List of typed Message instances.

    Returns:
        Tuple of (system_prompt, api_messages) where system_prompt is
        the joined system messages and api_messages is the list of
        non-system messages in Anthropic format.
    """
    from mypalclara.core.llm.messages import SystemMessage as SystemMsg

    system_parts: list[str] = []
    api_messages: list[dict[str, Any]] = []

    for msg in msgs:
        if isinstance(msg, SystemMsg):
            system_parts.append(msg.content)
        else:
            api_messages.append(message_to_anthropic(msg))

    return "\n\n".join(system_parts), api_messages


def messages_to_langchain(msgs: list["Message"]) -> list:
    """Convert typed Messages to LangChain message objects.

    Args:
        msgs: List of typed Message instances.

    Returns:
        List of LangChain message objects (SystemMessage, HumanMessage,
        AIMessage, ToolMessage).
    """
    from langchain_core.messages import AIMessage as LCAIMessage
    from langchain_core.messages import HumanMessage as LCHumanMessage
    from langchain_core.messages import SystemMessage as LCSystemMessage
    from langchain_core.messages import ToolMessage as LCToolMessage

    from mypalclara.core.llm.messages import (
        AssistantMessage as AssistantMsg,
    )
    from mypalclara.core.llm.messages import (
        SystemMessage as SystemMsg,
    )
    from mypalclara.core.llm.messages import (
        ToolResultMessage as ToolResultMsg,
    )
    from mypalclara.core.llm.messages import (
        UserMessage as UserMsg,
    )

    lc_messages = []
    for msg in msgs:
        if isinstance(msg, SystemMsg):
            lc_messages.append(LCSystemMessage(content=msg.content))
        elif isinstance(msg, UserMsg):
            if msg.parts:
                # Pass multimodal content as list (LangChain handles it)
                lc_messages.append(LCHumanMessage(content=[p.to_dict() for p in msg.parts]))
            else:
                lc_messages.append(LCHumanMessage(content=msg.content))
        elif isinstance(msg, AssistantMsg):
            if msg.tool_calls:
                lc_messages.append(
                    LCAIMessage(
                        content=msg.content or "",
                        tool_calls=[
                            {
                                "id": tc.id,
                                "name": tc.name,
                                "args": tc.arguments,
                            }
                            for tc in msg.tool_calls
                        ],
                    )
                )
            else:
                lc_messages.append(LCAIMessage(content=msg.content or ""))
        elif isinstance(msg, ToolResultMsg):
            lc_messages.append(
                LCToolMessage(
                    content=msg.content,
                    tool_call_id=msg.tool_call_id,
                )
            )

    return lc_messages
