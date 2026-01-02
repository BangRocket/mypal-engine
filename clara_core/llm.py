"""LLM backend abstraction for Clara platform.

Provides unified interface to multiple LLM providers:
- OpenRouter (default)
- NanoGPT
- Custom OpenAI-compatible endpoints
- Anthropic (native SDK with base_url support for clewdr)

Also supports tool calling with format conversion for Claude proxies.

Model Tiers:
- high: Most capable, expensive (Opus-class)
- mid: Balanced capability/cost (Sonnet-class) - default
- low: Fast, cheap, good for simple tasks (Haiku-class)
"""

from __future__ import annotations

import json
import os
import warnings
from collections.abc import Callable, Generator
from typing import TYPE_CHECKING, Literal

from anthropic import Anthropic
from openai import OpenAI

if TYPE_CHECKING:
    import anthropic.types
    from openai.types.chat import ChatCompletion

# Model tier type
ModelTier = Literal["high", "mid", "low"]

# Default tier
DEFAULT_TIER: ModelTier = "mid"

# Tool calling configuration
TOOL_FORMAT = os.getenv("TOOL_FORMAT", "openai").lower()
# DEPRECATED: TOOL_FORMAT is no longer needed when using LLM_PROVIDER=anthropic
# Native Anthropic SDK handles tool calling directly without format conversion
if os.getenv("TOOL_FORMAT"):
    warnings.warn(
        "TOOL_FORMAT is deprecated. Use LLM_PROVIDER=anthropic instead.",
        DeprecationWarning,
        stacklevel=2,
    )
# DEPRECATED: TOOL_MODEL env var is no longer used.
# Tools now always use tier-based model selection to respect !high, !mid, !low prefixes.
# This constant is kept for backwards compatibility but has no effect.
TOOL_MODEL = os.getenv("TOOL_MODEL", "")

# Default models per provider per tier
DEFAULT_MODELS = {
    "openrouter": {
        "high": "anthropic/claude-opus-4",
        "mid": "anthropic/claude-sonnet-4",
        "low": "anthropic/claude-haiku",
    },
    "nanogpt": {
        "high": "anthropic/claude-opus-4",
        "mid": "moonshotai/Kimi-K2-Instruct-0905",
        "low": "openai/gpt-4o-mini",
    },
    "openai": {
        "high": "claude-opus-4",
        "mid": "gpt-4o",
        "low": "gpt-4o-mini",
    },
    "anthropic": {
        "high": "claude-opus-4-5",
        "mid": "claude-sonnet-4-5",
        "low": "claude-haiku-4-5",
    },
}

# Global clients for reuse (lazy initialization)
_openrouter_client: OpenAI | None = None
_nanogpt_client: OpenAI | None = None
_custom_openai_client: OpenAI | None = None
_openai_tool_client: OpenAI | None = None
_anthropic_client: Anthropic | None = None
_anthropic_tool_client: Anthropic | None = None


def _get_openrouter_client() -> OpenAI:
    """Get or create OpenRouter client."""
    global _openrouter_client
    if _openrouter_client is None:
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set")

        site = os.getenv("OPENROUTER_SITE", "http://localhost:3000")
        title = os.getenv("OPENROUTER_TITLE", "MyPalClara")

        _openrouter_client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
            default_headers={
                "HTTP-Referer": site,
                "X-Title": title,
            },
        )
    return _openrouter_client


def _get_nanogpt_client() -> OpenAI:
    """Get or create NanoGPT client."""
    global _nanogpt_client
    if _nanogpt_client is None:
        api_key = os.getenv("NANOGPT_API_KEY")
        if not api_key:
            raise RuntimeError("NANOGPT_API_KEY is not set")

        _nanogpt_client = OpenAI(
            base_url="https://nano-gpt.com/api/v1",
            api_key=api_key,
        )
    return _nanogpt_client


def _get_cf_access_headers() -> dict[str, str] | None:
    """Get Cloudflare Access headers if configured.

    For endpoints behind Cloudflare Access (like cloudflared tunnels),
    set these environment variables:
    - CF_ACCESS_CLIENT_ID: Service token client ID
    - CF_ACCESS_CLIENT_SECRET: Service token client secret
    """
    client_id = os.getenv("CF_ACCESS_CLIENT_ID")
    client_secret = os.getenv("CF_ACCESS_CLIENT_SECRET")
    if client_id and client_secret:
        return {
            "CF-Access-Client-Id": client_id,
            "CF-Access-Client-Secret": client_secret,
        }
    return None


def _get_custom_openai_client() -> OpenAI:
    """Get or create custom OpenAI-compatible client."""
    global _custom_openai_client
    if _custom_openai_client is None:
        api_key = os.getenv("CUSTOM_OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("CUSTOM_OPENAI_API_KEY is not set")

        base_url = os.getenv("CUSTOM_OPENAI_BASE_URL", "https://api.openai.com/v1")

        client_kwargs = {
            "base_url": base_url,
            "api_key": api_key,
        }

        # Add Cloudflare Access headers if configured
        cf_headers = _get_cf_access_headers()
        if cf_headers:
            client_kwargs["default_headers"] = cf_headers

        _custom_openai_client = OpenAI(**client_kwargs)
    return _custom_openai_client


def _get_openai_tool_client() -> OpenAI:
    """Get or create dedicated client for tool calling.

    By default, uses the same endpoint as the main chat LLM (based on LLM_PROVIDER).
    Can be overridden with explicit TOOL_* environment variables.
    """
    global _openai_tool_client
    if _openai_tool_client is None:
        provider = os.getenv("LLM_PROVIDER", "openrouter").lower()

        # Determine defaults based on main LLM provider
        if provider == "openai":
            default_key = os.getenv("CUSTOM_OPENAI_API_KEY")
            default_url = os.getenv("CUSTOM_OPENAI_BASE_URL", "https://api.openai.com/v1")
        elif provider == "nanogpt":
            default_key = os.getenv("NANOGPT_API_KEY")
            default_url = "https://nano-gpt.com/api/v1"
        else:  # openrouter
            default_key = os.getenv("OPENROUTER_API_KEY")
            default_url = "https://openrouter.ai/api/v1"

        # Use explicit TOOL_* config or fall back to main LLM config
        api_key = os.getenv("TOOL_API_KEY") or default_key
        base_url = os.getenv("TOOL_BASE_URL") or default_url

        if not api_key:
            raise RuntimeError(
                "No API key found for tool calling. " "Set TOOL_API_KEY or configure your main LLM provider."
            )

        # Build client config
        client_kwargs = {
            "base_url": base_url,
            "api_key": api_key,
        }

        # Add OpenRouter headers if using OpenRouter
        if "openrouter.ai" in base_url:
            site = os.getenv("OPENROUTER_SITE", "http://localhost:3000")
            title = os.getenv("OPENROUTER_TITLE", "MyPalClara")
            client_kwargs["default_headers"] = {
                "HTTP-Referer": site,
                "X-Title": title,
            }
        else:
            # Add Cloudflare Access headers if configured (for custom endpoints)
            cf_headers = _get_cf_access_headers()
            if cf_headers:
                client_kwargs["default_headers"] = cf_headers

        _openai_tool_client = OpenAI(**client_kwargs)
    return _openai_tool_client


def _get_anthropic_client() -> Anthropic:
    """Get or create native Anthropic client.

    Supports custom base_url for proxies like clewdr via ANTHROPIC_BASE_URL.
    """
    global _anthropic_client
    if _anthropic_client is None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")

        base_url = os.getenv("ANTHROPIC_BASE_URL")

        client_kwargs: dict = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url

        # Build default headers
        headers = {}

        # Add Cloudflare Access headers if configured
        cf_headers = _get_cf_access_headers()
        if cf_headers:
            headers.update(cf_headers)

        # Override User-Agent to avoid Cloudflare bot detection
        if base_url:
            headers["User-Agent"] = "Clara/1.0"

        if headers:
            client_kwargs["default_headers"] = headers

        _anthropic_client = Anthropic(**client_kwargs)
    return _anthropic_client


def _get_anthropic_tool_client() -> Anthropic:
    """Get or create dedicated Anthropic client for tool calling.

    By default, uses the same endpoint as main Anthropic client.
    Can be overridden with explicit TOOL_* environment variables.
    """
    global _anthropic_tool_client
    if _anthropic_tool_client is None:
        # Use explicit TOOL_* config or fall back to main Anthropic config
        api_key = os.getenv("TOOL_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
        base_url = os.getenv("TOOL_BASE_URL") or os.getenv("ANTHROPIC_BASE_URL")

        if not api_key:
            raise RuntimeError("No API key found for Anthropic tool calling. " "Set TOOL_API_KEY or ANTHROPIC_API_KEY.")

        client_kwargs: dict = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url

        # Build default headers
        headers = {}

        # Add Cloudflare Access headers if configured
        cf_headers = _get_cf_access_headers()
        if cf_headers:
            headers.update(cf_headers)

        # Override User-Agent to avoid Cloudflare bot detection
        if base_url:
            headers["User-Agent"] = "Clara/1.0"

        if headers:
            client_kwargs["default_headers"] = headers

        _anthropic_tool_client = Anthropic(**client_kwargs)
    return _anthropic_tool_client


# ============== Model Tier Support ==============


def get_model_for_tier(tier: ModelTier, provider: str | None = None) -> str:
    """Get the model name for a specific tier and provider.

    Checks environment variables first, then falls back to defaults.

    Environment variables (by provider):
        OpenRouter: OPENROUTER_MODEL_{HIGH,MID,LOW}
        NanoGPT: NANOGPT_MODEL_{HIGH,MID,LOW}
        OpenAI: CUSTOM_OPENAI_MODEL_{HIGH,MID,LOW}
        Anthropic: ANTHROPIC_MODEL_{HIGH,MID,LOW}

    For backwards compatibility:
        - If tier-specific env var is not set, falls back to the base model env var
        - e.g., OPENROUTER_MODEL is used as the default for OPENROUTER_MODEL_MID

    Args:
        tier: The model tier ("high", "mid", "low")
        provider: The LLM provider. If None, uses LLM_PROVIDER env var.

    Returns:
        The model name to use.
    """
    if provider is None:
        provider = os.getenv("LLM_PROVIDER", "openrouter").lower()

    tier_upper = tier.upper()

    # Check for tier-specific environment variable
    if provider == "openrouter":
        tier_model = os.getenv(f"OPENROUTER_MODEL_{tier_upper}")
        if tier_model:
            return tier_model
        # Fall back to base model for mid tier, or defaults
        if tier == "mid":
            return os.getenv("OPENROUTER_MODEL", DEFAULT_MODELS["openrouter"]["mid"])
        return DEFAULT_MODELS["openrouter"].get(tier, DEFAULT_MODELS["openrouter"]["mid"])

    elif provider == "nanogpt":
        tier_model = os.getenv(f"NANOGPT_MODEL_{tier_upper}")
        if tier_model:
            return tier_model
        if tier == "mid":
            return os.getenv("NANOGPT_MODEL", DEFAULT_MODELS["nanogpt"]["mid"])
        return DEFAULT_MODELS["nanogpt"].get(tier, DEFAULT_MODELS["nanogpt"]["mid"])

    elif provider == "openai":
        tier_model = os.getenv(f"CUSTOM_OPENAI_MODEL_{tier_upper}")
        if tier_model:
            return tier_model
        if tier == "mid":
            return os.getenv("CUSTOM_OPENAI_MODEL", DEFAULT_MODELS["openai"]["mid"])
        return DEFAULT_MODELS["openai"].get(tier, DEFAULT_MODELS["openai"]["mid"])

    elif provider == "anthropic":
        tier_model = os.getenv(f"ANTHROPIC_MODEL_{tier_upper}")
        if tier_model:
            return tier_model
        if tier == "mid":
            return os.getenv("ANTHROPIC_MODEL", DEFAULT_MODELS["anthropic"]["mid"])
        return DEFAULT_MODELS["anthropic"].get(tier, DEFAULT_MODELS["anthropic"]["mid"])

    else:
        raise ValueError(f"Unknown provider: {provider}")


def get_current_tier() -> ModelTier | None:
    """Get the current default tier from environment.

    Returns None if MODEL_TIER is not explicitly set, allowing callers
    to fall back to the base model instead of assuming "mid" tier.
    """
    tier = os.getenv("MODEL_TIER", "").lower()
    if tier in ("high", "mid", "low"):
        return tier  # type: ignore
    return None


def get_tier_info() -> dict:
    """Get information about configured tiers for current provider."""
    provider = os.getenv("LLM_PROVIDER", "openrouter").lower()
    current_tier = get_current_tier()
    return {
        "provider": provider,
        "current_tier": current_tier,
        "default_model": get_base_model(provider),
        "using_tiers": current_tier is not None,
        "models": {
            "high": get_model_for_tier("high", provider),
            "mid": get_model_for_tier("mid", provider),
            "low": get_model_for_tier("low", provider),
        },
    }


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
    provider = os.getenv("LLM_PROVIDER", "openrouter").lower()
    effective_tier = tier or get_current_tier()

    # If no tier specified and MODEL_TIER not set, use base model
    if effective_tier is None:
        model = get_base_model(provider)
    else:
        model = get_model_for_tier(effective_tier, provider)

    if provider == "openrouter":
        return _make_openrouter_llm_with_model(model)
    elif provider == "nanogpt":
        return _make_nanogpt_llm_with_model(model)
    elif provider == "openai":
        return _make_custom_openai_llm_with_model(model)
    elif provider == "anthropic":
        return _make_anthropic_llm_with_model(model)
    else:
        raise ValueError(f"Unknown LLM_PROVIDER={provider}")


def _make_openrouter_llm_with_model(
    model: str,
) -> Callable[[list[dict[str, str]]], str]:
    """Non-streaming OpenRouter LLM with specified model."""
    client = _get_openrouter_client()

    def llm(messages: list[dict[str, str]]) -> str:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
        )
        return resp.choices[0].message.content

    return llm


def _make_nanogpt_llm_with_model(model: str) -> Callable[[list[dict[str, str]]], str]:
    """Non-streaming NanoGPT LLM with specified model."""
    client = _get_nanogpt_client()

    def llm(messages: list[dict[str, str]]) -> str:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
        )
        return resp.choices[0].message.content

    return llm


def _make_custom_openai_llm_with_model(
    model: str,
) -> Callable[[list[dict[str, str]]], str]:
    """Non-streaming custom OpenAI-compatible LLM with specified model."""
    client = _get_custom_openai_client()

    def llm(messages: list[dict[str, str]]) -> str:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
        )
        # Handle proxies that return raw strings (e.g., gemini-cli-openai)
        if isinstance(resp, str):
            return resp
        return resp.choices[0].message.content

    return llm


def _make_anthropic_llm_with_model(
    model: str,
) -> Callable[[list[dict[str, str]]], str]:
    """Non-streaming native Anthropic LLM with specified model.

    Handles system message extraction (Anthropic uses separate system param).
    """
    client = _get_anthropic_client()

    def llm(messages: list[dict[str, str]]) -> str:
        # Extract system messages (Anthropic handles it separately)
        # Concatenate multiple system messages since build_prompt creates two
        system_parts = []
        filtered = []
        for m in messages:
            if m.get("role") == "system":
                system_parts.append(m.get("content", ""))
            else:
                filtered.append(m)
        system = "\n\n".join(system_parts)

        kwargs: dict = {
            "model": model,
            "max_tokens": 4096,
            "messages": filtered,
        }
        if system:
            kwargs["system"] = system

        resp = client.messages.create(**kwargs)
        # Anthropic returns content blocks, extract text
        return resp.content[0].text if resp.content else ""

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
    provider = os.getenv("LLM_PROVIDER", "openrouter").lower()
    effective_tier = tier or get_current_tier()

    # If no tier specified and MODEL_TIER not set, use base model
    if effective_tier is None:
        model = get_base_model(provider)
    else:
        model = get_model_for_tier(effective_tier, provider)

    if provider == "openrouter":
        return _make_openrouter_llm_streaming_with_model(model)
    elif provider == "nanogpt":
        return _make_nanogpt_llm_streaming_with_model(model)
    elif provider == "openai":
        return _make_custom_openai_llm_streaming_with_model(model)
    elif provider == "anthropic":
        return _make_anthropic_llm_streaming_with_model(model)
    else:
        raise ValueError(f"Streaming not supported for LLM_PROVIDER={provider}")


def _make_openrouter_llm_streaming_with_model(
    model: str,
) -> Callable[[list[dict[str, str]]], Generator[str, None, None]]:
    """Streaming OpenRouter LLM with specified model."""
    client = _get_openrouter_client()

    def llm(messages: list[dict[str, str]]) -> Generator[str, None, None]:
        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
        )
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    return llm


def _make_nanogpt_llm_streaming_with_model(
    model: str,
) -> Callable[[list[dict[str, str]]], Generator[str, None, None]]:
    """Streaming NanoGPT LLM with specified model."""
    client = _get_nanogpt_client()

    def llm(messages: list[dict[str, str]]) -> Generator[str, None, None]:
        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
        )
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    return llm


def _make_custom_openai_llm_streaming_with_model(
    model: str,
) -> Callable[[list[dict[str, str]]], Generator[str, None, None]]:
    """Streaming custom OpenAI-compatible LLM with specified model."""
    client = _get_custom_openai_client()

    def llm(messages: list[dict[str, str]]) -> Generator[str, None, None]:
        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
        )
        # Handle proxies that return raw strings (e.g., gemini-cli-openai)
        if isinstance(stream, str):
            yield stream
            return
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    return llm


def _make_anthropic_llm_streaming_with_model(
    model: str,
) -> Callable[[list[dict[str, str]]], Generator[str, None, None]]:
    """Streaming native Anthropic LLM with specified model."""
    client = _get_anthropic_client()

    def llm(messages: list[dict[str, str]]) -> Generator[str, None, None]:
        # Extract system messages (Anthropic handles it separately)
        # Concatenate multiple system messages since build_prompt creates two
        system_parts = []
        filtered = []
        for m in messages:
            if m.get("role") == "system":
                system_parts.append(m.get("content", ""))
            else:
                filtered.append(m)
        system = "\n\n".join(system_parts)

        kwargs: dict = {
            "model": model,
            "max_tokens": 4096,
            "messages": filtered,
        }
        if system:
            kwargs["system"] = system

        with client.messages.stream(**kwargs) as stream:
            yield from stream.text_stream

    return llm


# ============== Tool Calling Support ==============


def _convert_tools_to_claude_format(tools: list[dict]) -> list[dict]:
    """Convert OpenAI-format tools to Claude format.

    OpenAI: {"type": "function", "function": {"name": ..., "parameters": ...}}
    Claude: {"name": ..., "input_schema": ...}
    """
    claude_tools = []
    for tool in tools:
        if tool.get("type") == "function" and "function" in tool:
            func = tool["function"]
            claude_tools.append(
                {
                    "name": func.get("name"),
                    "description": func.get("description", ""),
                    "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
                }
            )
        else:
            # Already in a different format, pass through
            claude_tools.append(tool)
    return claude_tools


def _convert_messages_to_claude_format(messages: list[dict]) -> list[dict]:
    """Convert OpenAI-format messages with tool calls/results to Claude format.

    Handles:
    - Assistant messages with tool_calls -> assistant with tool_use content blocks
    - Tool role messages -> user messages with tool_result content blocks
    """
    claude_messages = []
    pending_tool_results = []

    for msg in messages:
        role = msg.get("role")

        if role == "tool":
            # Collect tool results to batch into a user message
            pending_tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id"),
                    "content": msg.get("content", ""),
                }
            )
            continue

        # If we have pending tool results, add them as a user message first
        if pending_tool_results:
            claude_messages.append(
                {
                    "role": "user",
                    "content": pending_tool_results,
                }
            )
            pending_tool_results = []

        if role == "assistant" and msg.get("tool_calls"):
            # Convert assistant message with tool_calls to Claude format
            content_blocks = []

            # Add text content if present
            if msg.get("content"):
                content_blocks.append(
                    {
                        "type": "text",
                        "text": msg["content"],
                    }
                )

            # Add tool_use blocks
            for tc in msg["tool_calls"]:
                content_blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc.get("id"),
                        "name": tc.get("function", {}).get("name"),
                        "input": json.loads(tc.get("function", {}).get("arguments", "{}")),
                    }
                )

            claude_messages.append(
                {
                    "role": "assistant",
                    "content": content_blocks,
                }
            )
        else:
            # Regular message, pass through
            claude_messages.append(msg)

    # Handle any remaining tool results
    if pending_tool_results:
        claude_messages.append(
            {
                "role": "user",
                "content": pending_tool_results,
            }
        )

    return claude_messages


def get_base_model(provider: str | None = None) -> str:
    """Get the base model for a provider (without tier suffix).

    Returns the model from the base env var (e.g., CUSTOM_OPENAI_MODEL)
    rather than a tier-specific one (e.g., CUSTOM_OPENAI_MODEL_LOW).

    Args:
        provider: The LLM provider. If None, uses LLM_PROVIDER env var.

    Returns:
        The base model name.
    """
    if provider is None:
        provider = os.getenv("LLM_PROVIDER", "openrouter").lower()

    if provider == "openrouter":
        return os.getenv("OPENROUTER_MODEL", DEFAULT_MODELS["openrouter"]["mid"])
    elif provider == "nanogpt":
        return os.getenv("NANOGPT_MODEL", DEFAULT_MODELS["nanogpt"]["mid"])
    elif provider == "openai":
        return os.getenv("CUSTOM_OPENAI_MODEL", DEFAULT_MODELS["openai"]["mid"])
    elif provider == "anthropic":
        return os.getenv("ANTHROPIC_MODEL", DEFAULT_MODELS["anthropic"]["mid"])
    else:
        raise ValueError(f"Unknown provider: {provider}")


def _get_tool_model(tier: ModelTier | None = None) -> str:
    """Get the model to use for tool calling.

    Tool calls never use "low" tier - they use the base model at minimum.
    This ensures tools always have sufficient capability (never haiku).

    Args:
        tier: Optional tier override. If None, uses MODEL_TIER env var if set,
              otherwise uses base model. "low" tier is bumped to use the base model.
    """
    provider = os.getenv("LLM_PROVIDER", "openrouter").lower()

    # Never use "low" tier for tools - use base model instead
    if tier == "low":
        return get_base_model(provider)

    # For other tiers (high, mid, None), use tier-based selection
    effective_tier = tier or get_current_tier()

    # If no tier specified and MODEL_TIER not set, use base model
    if effective_tier is None:
        return get_base_model(provider)

    return get_model_for_tier(effective_tier, provider)


def make_llm_with_tools(
    tools: list[dict] | None = None,
    tier: ModelTier | None = None,
) -> Callable[[list[dict]], ChatCompletion]:
    """Return a function(messages) -> ChatCompletion that supports tool calling.

    Uses the same endpoint as your main chat LLM by default.
    Set TOOL_FORMAT=claude if using a Claude proxy (like clewdr).

    The returned function takes messages and returns the full ChatCompletion
    object so the caller can handle tool_calls if present.

    Args:
        tools: List of tool definitions in OpenAI format. If None, no tools.
        tier: Optional model tier ("high", "mid", "low").
              If None, uses the default tier from MODEL_TIER env var or "mid".

    Returns:
        Function that calls the LLM with tool support.
    """
    client = _get_openai_tool_client()
    tool_model = _get_tool_model(tier)
    tool_format = os.getenv("TOOL_FORMAT", "openai").lower()

    def llm(messages: list[dict]) -> ChatCompletion:
        if tool_format == "claude":
            # Convert messages and tools to Claude format for proxies like clewdr
            converted_messages = _convert_messages_to_claude_format(messages)
            kwargs = {"model": tool_model, "messages": converted_messages}
            if tools:
                kwargs["tools"] = _convert_tools_to_claude_format(tools)
        else:
            kwargs = {"model": tool_model, "messages": messages}
            if tools:
                kwargs["tools"] = tools
        return client.chat.completions.create(**kwargs)

    return llm


def make_llm_with_tools_anthropic(
    tools: list[dict] | None = None,
    tier: ModelTier | None = None,
) -> Callable[[list[dict]], anthropic.types.Message]:
    """Return a function(messages) -> anthropic.types.Message for native tool calling.

    Uses the native Anthropic SDK with native Claude tool format.
    Unlike make_llm_with_tools(), this returns Anthropic Message objects directly.

    Tool calls never use "low" tier - they use the base model at minimum.

    Args:
        tools: List of tool definitions in OpenAI format (will be converted).
        tier: Optional model tier ("high", "mid", "low").
              If None, uses MODEL_TIER env var if set, otherwise uses base model.
              "low" tier is bumped to use the base model.

    Returns:
        Function that calls Anthropic with native tool support.
    """
    client = _get_anthropic_tool_client()

    # Never use "low" tier for tools - use base model instead
    if tier == "low":
        model = get_base_model("anthropic")
    else:
        effective_tier = tier or get_current_tier()
        # If no tier specified and MODEL_TIER not set, use base model
        if effective_tier is None:
            model = get_base_model("anthropic")
        else:
            model = get_model_for_tier(effective_tier, "anthropic")

    def llm(messages: list[dict]) -> anthropic.types.Message:
        # Extract system messages (Anthropic handles it separately)
        # Concatenate multiple system messages since build_prompt creates two
        system_parts = []
        filtered = []
        for m in messages:
            if m.get("role") == "system":
                system_parts.append(m.get("content", ""))
            else:
                filtered.append(_convert_message_to_anthropic(m))
        system = "\n\n".join(system_parts)

        kwargs: dict = {
            "model": model,
            "max_tokens": 4096,
            "messages": filtered,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = _convert_tools_to_claude_format(tools)

        return client.messages.create(**kwargs)

    return llm


def _convert_message_to_anthropic(msg: dict) -> dict:
    """Convert a single OpenAI-style message to Anthropic format.

    Handles:
    - Assistant messages with tool_calls -> assistant with tool_use content blocks
    - Tool role messages -> user messages with tool_result content blocks
    - Regular messages -> pass through
    """
    role = msg.get("role")

    if role == "assistant" and msg.get("tool_calls"):
        # Convert assistant with tool_calls to Claude format
        content = []
        if msg.get("content"):
            content.append({"type": "text", "text": msg["content"]})
        for tc in msg["tool_calls"]:
            content.append(
                {
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["function"]["name"],
                    "input": json.loads(tc["function"]["arguments"]),
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

    return msg


def anthropic_to_openai_response(msg: anthropic.types.Message) -> dict:
    """Convert Anthropic Message to OpenAI-like response dict for compatibility.

    This allows the Discord bot to process Anthropic responses using the same
    code path as OpenAI responses.

    Returns a dict with:
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


# ============== Tool Description Generator ==============


async def generate_tool_description(
    tool_name: str,
    args: dict,
    max_words: int = 7,
) -> str | None:
    """Generate a short, specific description of a tool call using Haiku.

    Uses the low tier (Haiku-class) model for fast, cheap descriptions.

    Args:
        tool_name: Name of the tool being called
        args: Tool arguments
        max_words: Maximum words in description (default 7)

    Returns:
        Short description (3-7 words) or None if generation fails
    """
    import asyncio

    # Summarize args to keep prompt short
    args_summary = json.dumps(args, default=str)
    if len(args_summary) > 200:
        args_summary = args_summary[:200] + "..."

    prompt = f"""Describe what this tool call does in {max_words} words or fewer. Be specific and use action verbs.

Tool: {tool_name}
Args: {args_summary}

Examples:
- execute_python with code="df.describe()" -> "Analyzing dataframe statistics"
- web_search with query="python async tutorial" -> "Searching Python async tutorials"
- read_local_file with path="config.json" -> "Reading config file"

Your description (no quotes, no period):"""

    try:
        provider = os.getenv("LLM_PROVIDER", "openrouter").lower()

        # Use low tier for speed and cost
        if provider == "anthropic":
            client = _get_anthropic_client()
            model = get_model_for_tier("low", "anthropic")

            # Run in thread pool since Anthropic client is sync
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: client.messages.create(
                    model=model,
                    max_tokens=50,
                    messages=[{"role": "user", "content": prompt}],
                ),
            )
            text = response.content[0].text.strip()
        else:
            # Use OpenAI-compatible client
            if provider == "nanogpt":
                client = _get_nanogpt_client()
            elif provider == "openai":
                client = _get_custom_openai_client()
            else:  # openrouter
                client = _get_openrouter_client()

            model = get_model_for_tier("low", provider)

            # Run in thread pool
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: client.chat.completions.create(
                    model=model,
                    max_tokens=50,
                    messages=[{"role": "user", "content": prompt}],
                ),
            )
            text = response.choices[0].message.content.strip()

        # Clean up response
        text = text.strip("\"'")
        if text.endswith("."):
            text = text[:-1]

        # Validate length
        words = text.split()
        if len(words) > max_words + 2:  # Allow a bit of slack
            text = " ".join(words[:max_words])

        return text

    except Exception:
        # Silently fail - description is optional
        return None
