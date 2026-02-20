"""Unified LLM Provider Architecture for Clara.

This module provides a standardized interface for all LLM providers used in Clara.
It replaces the old clara_core/llm.py with a more organized, maintainable structure.

Key Components:
- LLMConfig: Unified configuration dataclass
- LLMProvider: Abstract interface for all providers
- LangChainProvider: Default provider using LangChain
- ToolResponse: Unified response format with tool calling
- Tier system: Model selection by capability level

Quick Start:
    from clara_core.llm import LLMConfig, get_provider

    # Get configured provider
    config = LLMConfig.from_env(tier="high")
    provider = get_provider()

    # Generate completion
    response = provider.complete(messages, config)

    # With tools
    tool_response = provider.complete_with_tools(messages, tools, config)
    if tool_response.has_tool_calls:
        for call in tool_response.tool_calls:
            print(f"Tool: {call.name}, Args: {call.arguments}")

Backward Compatibility:
    The following functions are re-exported for compatibility with existing code:
    - make_llm, make_llm_streaming
    - make_llm_with_tools, make_llm_with_tools_anthropic
    - make_llm_with_tools_langchain, make_llm_with_xml_tools
    - get_model_for_tier, get_base_model, get_current_tier
    - generate_tool_description
"""

from __future__ import annotations

# Backward compatibility imports
from clara_core.llm.compat import (
    generate_tool_description,
    make_llm,
    make_llm_streaming,
    make_llm_with_tools,
    make_llm_with_tools_anthropic,
    make_llm_with_tools_langchain,
    make_llm_with_tools_unified,
    make_llm_with_xml_tools,
    make_llm_with_xml_tools_streaming,
)

# Core components
from clara_core.llm.config import LLMConfig
from clara_core.llm.messages import (
    AssistantMessage,
    ContentPart,
    ContentPartType,
    Message,
    SystemMessage,
    ToolResultMessage,
    UserMessage,
    message_from_dict,
    messages_from_dicts,
)
from clara_core.llm.providers.base import LLMProvider
from clara_core.llm.providers.langchain import (
    DirectAnthropicProvider,
    DirectOpenAIProvider,
    LangChainProvider,
)
from clara_core.llm.providers.registry import ProviderRegistry, get_provider
from clara_core.llm.tiers import (
    DEFAULT_MODELS,
    DEFAULT_TIER,
    ModelTier,
    get_base_model,
    get_current_tier,
    get_model_for_tier,
    get_tier_info,
    get_tool_model,
)
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
    # Core new API
    "LLMConfig",
    "LLMProvider",
    "LangChainProvider",
    "DirectAnthropicProvider",
    "DirectOpenAIProvider",
    "ProviderRegistry",
    "get_provider",
    "ToolResponse",
    "ToolCall",
    "ToolSchema",
    # Typed message types
    "Message",
    "SystemMessage",
    "UserMessage",
    "AssistantMessage",
    "ToolResultMessage",
    "ContentPart",
    "ContentPartType",
    "message_from_dict",
    "messages_from_dicts",
    # Tiers
    "ModelTier",
    "DEFAULT_TIER",
    "DEFAULT_MODELS",
    "get_model_for_tier",
    "get_base_model",
    "get_current_tier",
    "get_tier_info",
    "get_tool_model",
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
    # Backward compatibility
    "make_llm",
    "make_llm_streaming",
    "make_llm_with_tools",
    "make_llm_with_tools_anthropic",
    "make_llm_with_tools_langchain",
    "make_llm_with_tools_unified",
    "make_llm_with_xml_tools",
    "make_llm_with_xml_tools_streaming",
    "generate_tool_description",
]
