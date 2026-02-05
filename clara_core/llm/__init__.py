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
    - anthropic_to_openai_response
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
    anthropic_to_openai_response,
    convert_message_to_anthropic,
    convert_messages_to_langchain,
    convert_to_claude_format,
    convert_to_mcp_format,
    convert_to_openai_format,
    convert_tools_to_claude_format,
)
from clara_core.llm.tools.response import ToolCall, ToolResponse

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
    # Tiers
    "ModelTier",
    "DEFAULT_TIER",
    "DEFAULT_MODELS",
    "get_model_for_tier",
    "get_base_model",
    "get_current_tier",
    "get_tier_info",
    "get_tool_model",
    # Format converters
    "convert_to_openai_format",
    "convert_to_claude_format",
    "convert_to_mcp_format",
    "convert_message_to_anthropic",
    "convert_tools_to_claude_format",
    "anthropic_to_openai_response",
    "convert_messages_to_langchain",
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
