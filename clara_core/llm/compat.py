"""Backward compatibility layer for the old clara_core/llm.py API.

This module provides the same function signatures as the old llm.py,
but implemented using the new unified provider architecture.

All existing code can continue to use:
- make_llm, make_llm_streaming
- make_llm_with_tools, make_llm_with_tools_anthropic
- make_llm_with_tools_langchain, make_llm_with_xml_tools
- generate_tool_description

These functions wrap the new LLMProvider interface for compatibility.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Generator
from typing import TYPE_CHECKING, Any

from clara_core.llm.config import LLMConfig
from clara_core.llm.providers.registry import get_provider
from clara_core.llm.tiers import ModelTier
from clara_core.llm.tools.response import ToolResponse

if TYPE_CHECKING:
    import anthropic.types
    from openai.types.chat import ChatCompletion


# ============== Non-streaming LLM ==============


def make_llm(tier: ModelTier | None = None) -> Callable[[list[dict[str, str]]], str]:
    """Return a function(messages) -> assistant_reply string.

    Select backend with env var LLM_PROVIDER:
      - "openrouter" (default)
      - "nanogpt"
      - "openai" (custom OpenAI-compatible endpoint)
      - "anthropic" (native Anthropic SDK with base_url support)

    Args:
        tier: Optional model tier ("high", "mid", "low").
              If None, uses MODEL_TIER env var if set, otherwise uses the base model.
    """
    config = LLMConfig.from_env(tier=tier)
    provider = get_provider("langchain")

    def llm(messages: list[dict[str, str]]) -> str:
        return provider.complete(messages, config)

    return llm


# ============== Streaming LLM ==============


def make_llm_streaming(
    tier: ModelTier | None = None,
) -> Callable[[list[dict[str, str]]], Generator[str, None, None]]:
    """Return a streaming LLM function that yields chunks.

    Args:
        tier: Optional model tier ("high", "mid", "low").
              If None, uses MODEL_TIER env var if set, otherwise uses the base model.
    """
    config = LLMConfig.from_env(tier=tier)
    provider = get_provider("langchain")

    def llm(messages: list[dict[str, str]]) -> Generator[str, None, None]:
        yield from provider.stream(messages, config)

    return llm


# ============== Tool Calling Support ==============


def make_llm_with_tools(
    tools: list[dict] | None = None,
    tier: ModelTier | None = None,
) -> Callable[[list[dict]], "ChatCompletion"]:
    """Return a function(messages) -> ChatCompletion that supports tool calling.

    Uses the same endpoint as your main chat LLM by default.
    For Claude proxies like clewdr, use LLM_PROVIDER=anthropic with
    make_llm_with_tools_anthropic() instead.

    Args:
        tools: List of tool definitions in OpenAI format. If None, no tools.
        tier: Optional model tier ("high", "mid", "low").

    Returns:
        Function that calls the LLM with tool support.
    """
    config = LLMConfig.from_env(tier=tier, for_tools=True)

    def llm(messages: list[dict]) -> "ChatCompletion":
        # Need to return raw ChatCompletion, so use direct SDK
        from openai import OpenAI

        client = OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            default_headers=config.extra_headers,
        )

        kwargs = {"model": config.model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        return client.chat.completions.create(**kwargs)

    return llm


def make_llm_with_tools_anthropic(
    tools: list[dict] | None = None,
    tier: ModelTier | None = None,
) -> Callable[[list[dict]], "anthropic.types.Message"]:
    """Return a function(messages) -> anthropic.types.Message for native tool calling.

    Uses the native Anthropic SDK with native Claude tool format.
    Unlike make_llm_with_tools(), this returns Anthropic Message objects directly.

    Tool calls never use "low" tier - they use the base model at minimum.

    Args:
        tools: List of tool definitions in OpenAI format (will be converted).
        tier: Optional model tier ("high", "mid", "low").
              "low" tier is bumped to use the base model.

    Returns:
        Function that calls Anthropic with native tool support.
    """
    from anthropic import Anthropic

    from clara_core.llm.tools.formats import (
        convert_message_to_anthropic,
        convert_tools_to_claude_format,
    )

    config = LLMConfig.from_env(provider="anthropic", tier=tier, for_tools=True)

    client_kwargs: dict = {"api_key": config.api_key}
    if config.base_url:
        client_kwargs["base_url"] = config.base_url
    if config.extra_headers:
        client_kwargs["default_headers"] = config.extra_headers

    client = Anthropic(**client_kwargs)

    def llm(messages: list[dict]) -> "anthropic.types.Message":
        # Extract system messages (Anthropic handles it separately)
        system_parts = []
        filtered = []
        for m in messages:
            if m.get("role") == "system":
                system_parts.append(m.get("content", ""))
            else:
                filtered.append(convert_message_to_anthropic(m))
        system = "\n\n".join(system_parts)

        kwargs: dict = {
            "model": config.model,
            "max_tokens": config.max_tokens,
            "messages": filtered,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = convert_tools_to_claude_format(tools)

        return client.messages.create(**kwargs)

    return llm


def make_llm_with_tools_langchain(
    tools: list[dict] | None = None,
    tier: ModelTier | None = None,
) -> Callable[[list[dict]], dict]:
    """Return a function(messages) -> dict that supports tool calling via LangChain.

    Uses LangChain's bind_tools() for unified tool calling across all providers.
    This is the recommended approach for tool calling as it handles format conversion
    automatically for each provider.

    Tool calls never use "low" tier - they use the base model at minimum.

    Args:
        tools: List of tool definitions in OpenAI format
        tier: Optional model tier ("high", "mid", "low").
              "low" tier is bumped to use the base model.

    Returns:
        Function that calls the LLM with tool support.
        Returns dict with:
        - content: Text content
        - role: "assistant"
        - tool_calls: List of tool calls in OpenAI format (if any)
    """
    config = LLMConfig.from_env(tier=tier, for_tools=True)
    provider = get_provider("langchain")

    def llm(messages: list[dict]) -> dict:
        response = provider.complete_with_tools(messages, tools or [], config)
        return response.to_openai_dict()

    return llm


# ============== Unified Tool Calling (Recommended) ==============


def make_llm_with_tools_unified(
    tools: list[dict] | None = None,
    tier: ModelTier | None = None,
) -> Callable[[list[dict]], ToolResponse]:
    """Return a unified tool-calling function that works with ANY provider.

    This is the RECOMMENDED way to use tool calling. It:
    - Works with all providers (OpenRouter, NanoGPT, OpenAI, Anthropic)
    - Returns a standardized ToolResponse object
    - Handles format conversion internally
    - No provider-specific branching needed in calling code

    Tool calls never use "low" tier - they use the base model at minimum.

    Args:
        tools: List of tool definitions in OpenAI format
        tier: Optional model tier ("high", "mid", "low").
              "low" tier is bumped to use the base model.

    Returns:
        Function that calls the LLM with tool support.
        Returns ToolResponse with:
        - content: Text content (str)
        - tool_calls: List[ToolCall] with standardized format
        - has_tool_calls: bool property for easy checking

    Example:
        llm = make_llm_with_tools_unified(tools)
        response = llm(messages)

        if response.has_tool_calls:
            for tool_call in response.tool_calls:
                print(f"Call {tool_call.name} with {tool_call.arguments}")
        else:
            print(response.content)
    """
    config = LLMConfig.from_env(tier=tier, for_tools=True)
    provider = get_provider("langchain")

    def llm(messages: list[dict]) -> ToolResponse:
        return provider.complete_with_tools(messages, tools or [], config)

    return llm


# ============== XML-based Tool Calling ==============


def make_llm_with_xml_tools(
    tools: list[dict] | None = None,
    tier: ModelTier | None = None,
) -> Callable[[list[dict]], dict]:
    """Return a function that injects tools into system prompt (OpenClaw-style).

    Instead of using native API tool calling, this approach:
    1. Serializes tools to XML and injects them into the system prompt
    2. Returns the full response text (which may contain function_calls blocks)
    3. Caller is responsible for parsing function calls from the response

    This works with any LLM provider, regardless of native tool support.

    Args:
        tools: List of tool definitions in OpenAI format
        tier: Optional model tier

    Returns:
        Function that calls LLM with tools in system prompt.
        Returns dict with:
        - content: Full response text
        - role: "assistant"
        - tool_calls: Parsed tool calls in OpenAI format (if any)
    """
    from clara_core.plugins.xml_tools import (
        convert_to_openai_tool_calls,
        extract_text_before_function_calls,
        parse_function_calls,
        tools_to_xml_from_dicts,
    )

    # Generate XML tools block
    tools_xml = tools_to_xml_from_dicts(tools) if tools else ""

    # Get the base LLM function
    base_llm = make_llm(tier=tier)

    def llm(messages: list[dict]) -> dict:
        # Inject tools XML into system prompt
        augmented_messages = _inject_tools_into_messages(messages, tools_xml)

        # Call LLM
        response_text = base_llm(augmented_messages)

        # Parse function calls from response
        parsed_calls = parse_function_calls(response_text)

        # If there are tool calls, extract only the text before them
        if parsed_calls:
            content = extract_text_before_function_calls(response_text)
        else:
            content = response_text

        result: dict[str, Any] = {
            "content": content,
            "role": "assistant",
        }

        if parsed_calls:
            result["tool_calls"] = convert_to_openai_tool_calls(parsed_calls)

        return result

    return llm


def make_llm_with_xml_tools_streaming(
    tools: list[dict] | None = None,
    tier: ModelTier | None = None,
) -> Callable[[list[dict]], Generator[str, None, None]]:
    """Return a streaming function with tools injected into system prompt.

    Unlike non-streaming, this yields chunks as they arrive.
    Caller must accumulate the full response to parse function calls.

    Args:
        tools: List of tool definitions in OpenAI format
        tier: Optional model tier

    Returns:
        Function that yields response chunks
    """
    from clara_core.plugins.xml_tools import tools_to_xml_from_dicts

    # Generate XML tools block
    tools_xml = tools_to_xml_from_dicts(tools) if tools else ""

    # Get the base streaming LLM function
    base_llm = make_llm_streaming(tier=tier)

    def llm(messages: list[dict]) -> Generator[str, None, None]:
        # Inject tools XML into system prompt
        augmented_messages = _inject_tools_into_messages(messages, tools_xml)

        # Stream response
        yield from base_llm(augmented_messages)

    return llm


def _inject_tools_into_messages(
    messages: list[dict],
    tools_xml: str,
) -> list[dict]:
    """Inject tools XML into the system prompt of messages."""
    if not tools_xml:
        return messages

    result = []
    system_found = False

    for msg in messages:
        if msg.get("role") == "system" and not system_found:
            content = msg.get("content", "")
            result.append(
                {
                    **msg,
                    "content": f"{content}\n\n{tools_xml}",
                }
            )
            system_found = True
        else:
            result.append(msg)

    if not system_found:
        result.insert(
            0,
            {
                "role": "system",
                "content": tools_xml,
            },
        )

    return result


# ============== Tool Description Generator ==============

# Tool description settings from environment
TOOL_DESC_TIER = os.getenv("TOOL_DESC_TIER", "high").lower()
TOOL_DESC_MAX_WORDS = int(os.getenv("TOOL_DESC_MAX_WORDS", "20"))


async def generate_tool_description(
    tool_name: str,
    args: dict,
    max_words: int | None = None,
) -> str | None:
    """Generate a descriptive explanation of a tool call.

    Uses a configurable model tier for generating rich, contextual descriptions.

    Environment variables:
        TOOL_DESC_TIER: Model tier to use (default: "high" for Opus-class)
        TOOL_DESC_MAX_WORDS: Maximum words in description (default: 20)

    Args:
        tool_name: Name of the tool being called
        args: Tool arguments
        max_words: Override for max words (uses TOOL_DESC_MAX_WORDS if not provided)

    Returns:
        Descriptive explanation or None if generation fails
    """
    import asyncio

    if max_words is None:
        max_words = TOOL_DESC_MAX_WORDS

    # Expand args to show more context
    args_summary = json.dumps(args, default=str, indent=2)
    if len(args_summary) > 500:
        args_summary = args_summary[:500] + "\n..."

    prompt = f"""Describe what this tool call does in {max_words} words or fewer.
Be specific, descriptive, and explain the purpose.
Include relevant details from the arguments.

Tool: {tool_name}
Arguments:
{args_summary}

Guidelines:
- Use present tense action verbs (e.g., "Executing", "Fetching", "Analyzing")
- Include key details like filenames, search queries, or specific actions
- Explain the purpose when clear from context
- Be concise but informative

Examples:
- execute_python with code="df.describe()" -> "Analyzing dataframe for summary statistics"
- web_search with query="python async" -> "Searching for Python async tutorials"
- read_local_file with filename="config.json" -> "Reading config.json settings"
- github_create_issue with title="Fix bug" -> "Creating GitHub issue for bug fix"
- install_package with package="pandas" -> "Installing pandas library"

Your description (no quotes, no period at end):"""

    try:
        # Use configurable tier (default: high for richer descriptions)
        tier = TOOL_DESC_TIER
        if tier not in ("high", "mid", "low"):
            tier = "high"

        config = LLMConfig.from_env(tier=tier)  # type: ignore
        provider = get_provider("langchain")

        messages = [{"role": "user", "content": prompt}]

        # Run in executor since providers are sync
        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(
            None,
            lambda: provider.complete(messages, config),
        )

        # Clean up response
        text = text.strip().strip("\"'")
        if text.endswith("."):
            text = text[:-1]

        # Validate length
        words = text.split()
        if len(words) > max_words + 5:
            text = " ".join(words[:max_words])

        return text

    except Exception:
        # Silently fail - description is optional
        return None
