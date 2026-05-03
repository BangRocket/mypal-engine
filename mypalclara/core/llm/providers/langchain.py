"""LangChain-based LLM provider implementation.

Provides unified access to all supported LLM backends via LangChain:
- ChatOpenAI for OpenRouter, NanoGPT, Custom OpenAI
- ChatAnthropic for native Anthropic (with base_url for clewdr)
- ChatBedrock for Amazon Bedrock (Claude models via AWS)
- AzureChatOpenAI for Azure OpenAI Service

Benefits:
- Unified tool calling via bind_tools()
- Automatic format conversion between providers
- Consistent streaming interface
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from mypalclara.core.llm.providers.base import LLMProvider, _normalize_tools
from mypalclara.core.llm.tools.formats import (
    messages_to_anthropic,
    messages_to_anthropic_blocks,
    messages_to_kimi,
    messages_to_langchain,
    messages_to_openai,
)
from mypalclara.core.llm.tools.response import ToolResponse

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from mypalclara.core.llm.config import LLMConfig
    from mypalclara.core.llm.messages import Message
    from mypalclara.core.llm.tools.schema import ToolSchema


# Cached LangChain models keyed by (provider, model, base_url)
_model_cache: dict[str, "BaseChatModel"] = {}


class LangChainProvider(LLMProvider):
    """LLM provider using LangChain for unified access.

    Supports all Clara providers:
    - openrouter: ChatOpenAI with OpenRouter base URL
    - nanogpt: ChatOpenAI with NanoGPT base URL
    - openai: ChatOpenAI with custom base URL
    - anthropic: ChatAnthropic with native SDK (base_url for clewdr)
    - bedrock: ChatBedrock for Amazon Bedrock (requires langchain-aws)
    - azure: AzureChatOpenAI for Azure OpenAI Service
    """

    def complete(
        self,
        messages: list[Message],
        config: "LLMConfig",
    ) -> str:
        """Generate a text completion using LangChain."""
        model = self.get_langchain_model(config)
        lc_messages = messages_to_langchain(messages)
        response = model.invoke(lc_messages)
        return response.content if isinstance(response.content, str) else str(response.content)

    def complete_with_tools(
        self,
        messages: list[Message],
        tools: "list[ToolSchema | dict[str, Any]]",
        config: "LLMConfig",
    ) -> ToolResponse:
        """Generate a response with tool calling via LangChain bind_tools()."""
        model = self.get_langchain_model(config)
        normalized = _normalize_tools(tools)

        # Bind tools to model
        if normalized:
            bind_kwargs = {}
            if config.tool_choice:
                bind_kwargs["tool_choice"] = config.tool_choice
            model_with_tools = model.bind_tools(normalized, **bind_kwargs)
        else:
            model_with_tools = model

        # Convert and invoke
        lc_messages = messages_to_langchain(messages)
        response = model_with_tools.invoke(lc_messages)

        return ToolResponse.from_langchain(response)

    def stream(
        self,
        messages: list[Message],
        config: "LLMConfig",
    ) -> Iterator[str]:
        """Generate a streaming completion using LangChain."""
        model = self.get_langchain_model(config)
        lc_messages = messages_to_langchain(messages)

        for chunk in model.stream(lc_messages):
            content = chunk.content
            if content:
                yield content if isinstance(content, str) else str(content)

    def stream_with_tools(
        self,
        messages: list[Message],
        tools: "list[ToolSchema | dict[str, Any]]",
        config: "LLMConfig",
    ) -> Iterator[str]:
        """Stream with tool support.

        Note: LangChain streaming with tools doesn't easily expose tool calls
        during streaming, so we yield text chunks and return the full response
        for tool call detection after streaming completes.
        """
        model = self.get_langchain_model(config)
        normalized = _normalize_tools(tools)

        if normalized:
            bind_kwargs = {}
            if config.tool_choice:
                bind_kwargs["tool_choice"] = config.tool_choice
            model_with_tools = model.bind_tools(normalized, **bind_kwargs)
        else:
            model_with_tools = model

        lc_messages = messages_to_langchain(messages)

        for chunk in model_with_tools.stream(lc_messages):
            content = chunk.content
            if content:
                yield content if isinstance(content, str) else str(content)

    def get_langchain_model(self, config: "LLMConfig") -> "BaseChatModel":
        """Get or create a LangChain chat model for the configuration.

        Caches models by provider/model/base_url to avoid recreating clients.

        Args:
            config: LLM configuration

        Returns:
            LangChain BaseChatModel instance
        """
        # Create cache key
        cache_key = f"{config.provider}:{config.model}:{config.base_url}"
        if cache_key in _model_cache:
            return _model_cache[cache_key]

        if config.provider == "anthropic":
            model = self._create_anthropic_model(config)
        elif config.provider == "bedrock":
            model = self._create_bedrock_model(config)
        elif config.provider == "azure":
            model = self._create_azure_model(config)
        elif config.provider == "kimi":
            model = self._create_kimi_model(config)
        else:
            model = self._create_openai_model(config)

        _model_cache[cache_key] = model
        return model

    def _create_anthropic_model(self, config: "LLMConfig") -> "BaseChatModel":
        """Create a ChatAnthropic model."""
        from langchain_anthropic import ChatAnthropic

        kwargs: dict[str, Any] = {
            "model": config.model,
            "api_key": config.api_key,
            "max_tokens": config.max_tokens,
            "temperature": config.temperature,
        }

        if config.base_url:
            kwargs["base_url"] = config.base_url

        if config.extra_headers:
            kwargs["default_headers"] = config.extra_headers

        return ChatAnthropic(**kwargs)

    def _create_openai_model(self, config: "LLMConfig") -> "BaseChatModel":
        """Create a ChatOpenAI model for OpenAI-compatible providers."""
        from langchain_openai import ChatOpenAI

        kwargs: dict[str, Any] = {
            "model": config.model,
            "api_key": config.api_key,
            "temperature": config.temperature,
        }

        if config.base_url:
            kwargs["base_url"] = config.base_url

        if config.extra_headers:
            kwargs["default_headers"] = config.extra_headers

        return ChatOpenAI(**kwargs)

    def _create_kimi_model(self, config: "LLMConfig") -> "BaseChatModel":
        """Create a ChatOpenAI model configured for Moonshot/Kimi."""
        from langchain_openai import ChatOpenAI

        # Kimi K2.6/K2.5 require fixed temperatures based on thinking mode.
        thinking_mode = os.getenv("KIMI_THINKING_MODE", "disabled").strip().lower()
        if thinking_mode not in ("enabled", "disabled"):
            thinking_mode = "disabled"
        temperature = 1.0 if thinking_mode == "enabled" else 0.6

        kwargs: dict[str, Any] = {
            "model": config.model,
            "api_key": config.api_key,
            "base_url": config.base_url or "https://api.moonshot.ai/v1",
            "temperature": temperature,
        }

        if config.extra_headers:
            kwargs["default_headers"] = config.extra_headers

        # Pass thinking mode through model_kwargs so LangChain forwards it
        # in the request body (provider-specific fields must not be top-level).
        kwargs["model_kwargs"] = {"extra_body": {"thinking": {"type": thinking_mode}}}

        return ChatOpenAI(**kwargs)

    def _create_bedrock_model(self, config: "LLMConfig") -> "BaseChatModel":
        """Create a ChatBedrock model for Amazon Bedrock.

        Requires langchain-aws package: pip install langchain-aws

        Uses boto3 credential chain:
        - Environment variables (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)
        - AWS credentials file (~/.aws/credentials)
        - IAM role (for EC2, Lambda, etc.)
        """
        try:
            from langchain_aws import ChatBedrock
        except ImportError as e:
            raise ImportError(
                "Amazon Bedrock support requires langchain-aws. " "Install it with: pip install langchain-aws"
            ) from e

        kwargs: dict[str, Any] = {
            "model_id": config.model,
            "model_kwargs": {
                "temperature": config.temperature,
                "max_tokens": config.max_tokens,
            },
        }

        if config.aws_region:
            kwargs["region_name"] = config.aws_region

        return ChatBedrock(**kwargs)

    def _create_azure_model(self, config: "LLMConfig") -> "BaseChatModel":
        """Create an AzureChatOpenAI model for Azure OpenAI Service.

        Requires:
        - AZURE_OPENAI_ENDPOINT: Your Azure OpenAI endpoint
        - AZURE_OPENAI_API_KEY: API key
        - AZURE_DEPLOYMENT_NAME: Name of your deployment
        - AZURE_API_VERSION: API version (default: 2024-02-15-preview)
        """
        from langchain_openai import AzureChatOpenAI

        if not config.azure_deployment:
            raise ValueError("Azure OpenAI requires AZURE_DEPLOYMENT_NAME environment variable")

        kwargs: dict[str, Any] = {
            "azure_deployment": config.azure_deployment,
            "api_key": config.api_key,
            "api_version": config.azure_api_version or "2024-02-15-preview",
            "temperature": config.temperature,
        }

        if config.base_url:
            kwargs["azure_endpoint"] = config.base_url

        return AzureChatOpenAI(**kwargs)


# Direct SDK providers for cases where LangChain overhead isn't needed


class DirectAnthropicProvider(LLMProvider):
    """Direct Anthropic SDK provider.

    Owned features (cannot be expressed cleanly through LangChain's
    ChatAnthropic):

    - Prompt caching: a ``cache_control: ephemeral`` breakpoint is placed
      on the first system block when ``config.cache_system`` is True. Read
      ``response.usage.cache_read_input_tokens`` to verify hits.
    - Adaptive thinking: pass ``config.thinking="adaptive"`` (recommended
      on Sonnet 4.6 / Opus 4.6 / Opus 4.7).
    - Effort knob: ``config.effort`` in {low, medium, high, xhigh, max}.
    - Sampling-param dropping: ``config.drop_sampling_params=True``
      strips temperature/top_p/top_k. Required on Opus 4.7 (those
      parameters return 400). Auto-enabled by ``LLMConfig.from_env``
      for opus-4-7.
    - Long output: requests with large ``max_tokens`` are routed
      through ``client.messages.stream`` to avoid SDK HTTP timeouts.
    """

    # Above this max_tokens threshold the SDK requires streaming to avoid
    # idle-connection timeouts on long generations.
    _STREAM_MAX_TOKENS_THRESHOLD = 16384

    _clients: dict[str, Any] = {}

    def _get_client(self, config: "LLMConfig"):
        """Get or create Anthropic client.

        Caches clients by (api_key, base_url) to support multiple configurations.
        """
        from anthropic import Anthropic

        cache_key = f"{config.api_key}:{config.base_url}"
        if cache_key not in self._clients:
            kwargs: dict[str, Any] = {"api_key": config.api_key}
            if config.base_url:
                kwargs["base_url"] = config.base_url
            if config.extra_headers:
                kwargs["default_headers"] = config.extra_headers
            self._clients[cache_key] = Anthropic(**kwargs)
        return self._clients[cache_key]

    def _build_kwargs(
        self,
        messages: list[Message],
        config: "LLMConfig",
        *,
        tools: list[dict] | None = None,
    ) -> dict[str, Any]:
        """Assemble kwargs for messages.create / messages.stream.

        Centralized so streaming and non-streaming paths stay in sync.
        """
        system_blocks, api_messages = messages_to_anthropic_blocks(
            messages, cache_persona=config.cache_system
        )

        kwargs: dict[str, Any] = {
            "model": config.model,
            "max_tokens": config.max_tokens,
            "messages": api_messages,
        }
        if system_blocks:
            kwargs["system"] = system_blocks
        if tools:
            kwargs["tools"] = tools

        # Sampling params: only when not explicitly dropped (Opus 4.7 rejects them).
        if not config.drop_sampling_params and config.temperature is not None:
            kwargs["temperature"] = config.temperature

        # Adaptive thinking — model decides when/how much to think.
        if config.thinking in ("adaptive", "disabled"):
            kwargs["thinking"] = {"type": config.thinking}

        # Effort knob lives inside output_config (Opus 4.5+/Sonnet 4.6).
        if config.effort:
            kwargs["output_config"] = {"effort": config.effort}

        if config.tool_choice:
            kwargs["tool_choice"] = config.tool_choice

        return kwargs

    def complete(
        self,
        messages: list[Message],
        config: "LLMConfig",
    ) -> str:
        """Generate a text completion using native Anthropic SDK."""
        client = self._get_client(config)
        kwargs = self._build_kwargs(messages, config)

        if config.max_tokens > self._STREAM_MAX_TOKENS_THRESHOLD:
            with client.messages.stream(**kwargs) as stream:
                final = stream.get_final_message()
            for block in final.content:
                if getattr(block, "type", None) == "text":
                    return block.text
            return ""

        response = client.messages.create(**kwargs)
        for block in response.content:
            if getattr(block, "type", None) == "text":
                return block.text
        return ""

    def complete_with_tools(
        self,
        messages: list[Message],
        tools: "list[ToolSchema | dict[str, Any]]",
        config: "LLMConfig",
    ) -> ToolResponse:
        """Generate a response with native Anthropic tool calling."""
        from mypalclara.core.llm.tools.formats import convert_tools_to_claude_format

        client = self._get_client(config)
        normalized = _normalize_tools(tools)
        claude_tools = convert_tools_to_claude_format(normalized) if normalized else None

        kwargs = self._build_kwargs(messages, config, tools=claude_tools)

        if config.max_tokens > self._STREAM_MAX_TOKENS_THRESHOLD:
            with client.messages.stream(**kwargs) as stream:
                response = stream.get_final_message()
        else:
            response = client.messages.create(**kwargs)

        return ToolResponse.from_anthropic(response)

    def stream(
        self,
        messages: list[Message],
        config: "LLMConfig",
    ) -> Iterator[str]:
        """Generate a streaming completion using native Anthropic SDK."""
        client = self._get_client(config)
        kwargs = self._build_kwargs(messages, config)

        with client.messages.stream(**kwargs) as stream:
            yield from stream.text_stream

    def get_langchain_model(self, config: "LLMConfig") -> "BaseChatModel":
        """Get a LangChain model handle.

        Provided for callers that bypass DirectAnthropicProvider's own
        complete/stream methods. cache_control, thinking, effort, and
        output_config are NOT applied here — LangChain has no clean way
        to surface them. Use the provider's own methods to get those
        features.
        """
        from langchain_anthropic import ChatAnthropic

        kwargs: dict[str, Any] = {
            "model": config.model,
            "api_key": config.api_key,
            "max_tokens": config.max_tokens,
        }
        if not config.drop_sampling_params and config.temperature is not None:
            kwargs["temperature"] = config.temperature
        if config.base_url:
            kwargs["base_url"] = config.base_url
        if config.extra_headers:
            kwargs["default_headers"] = config.extra_headers

        return ChatAnthropic(**kwargs)


class DirectOpenAIProvider(LLMProvider):
    """Direct OpenAI SDK provider for native tool calling.

    Use this for direct access to OpenAI-compatible APIs:
    - OpenRouter
    - NanoGPT
    - Custom OpenAI endpoints
    """

    _clients: dict[str, Any] = {}

    def _get_client(self, config: "LLMConfig"):
        """Get or create OpenAI client.

        Caches clients by (api_key, base_url) to support multiple configurations.
        """
        from openai import OpenAI

        cache_key = f"{config.api_key}:{config.base_url}"
        if cache_key not in self._clients:
            kwargs: dict[str, Any] = {
                "api_key": config.api_key,
            }
            if config.base_url:
                kwargs["base_url"] = config.base_url
            if config.extra_headers:
                kwargs["default_headers"] = config.extra_headers
            self._clients[cache_key] = OpenAI(**kwargs)
        return self._clients[cache_key]

    def complete(
        self,
        messages: list[Message],
        config: "LLMConfig",
    ) -> str:
        """Generate a text completion using OpenAI SDK."""
        client = self._get_client(config)
        api_messages = messages_to_openai(messages)
        response = client.chat.completions.create(
            model=config.model,
            messages=api_messages,
        )
        content = response.choices[0].message.content
        return content if content else ""

    def complete_with_tools(
        self,
        messages: list[Message],
        tools: "list[ToolSchema | dict[str, Any]]",
        config: "LLMConfig",
    ) -> ToolResponse:
        """Generate a response with OpenAI tool calling."""
        client = self._get_client(config)
        api_messages = messages_to_openai(messages)
        normalized = _normalize_tools(tools)

        kwargs: dict[str, Any] = {
            "model": config.model,
            "messages": api_messages,
        }
        if normalized:
            kwargs["tools"] = normalized

        response = client.chat.completions.create(**kwargs)
        return ToolResponse.from_openai(response)

    def stream(
        self,
        messages: list[Message],
        config: "LLMConfig",
    ) -> Iterator[str]:
        """Generate a streaming completion using OpenAI SDK."""
        client = self._get_client(config)
        api_messages = messages_to_openai(messages)
        stream = client.chat.completions.create(
            model=config.model,
            messages=api_messages,
            stream=True,
        )

        # Handle proxies that return raw strings
        if isinstance(stream, str):
            yield stream
            return

        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    def get_langchain_model(self, config: "LLMConfig") -> "BaseChatModel":
        """Get LangChain model."""
        from langchain_openai import ChatOpenAI

        kwargs: dict[str, Any] = {
            "model": config.model,
            "api_key": config.api_key,
        }
        if config.base_url:
            kwargs["base_url"] = config.base_url
        if config.extra_headers:
            kwargs["default_headers"] = config.extra_headers

        return ChatOpenAI(**kwargs)


class DirectKimiProvider(LLMProvider):
    """Direct Moonshot/Kimi provider with Kimi-specific tool message handling.

    Kimi's API is OpenAI-compatible at the transport level, but tool result
    messages need the tool ``name`` in addition to ``tool_call_id``. Keeping this
    as a separate provider avoids leaking that behavior into the generic OpenAI
    route.
    """

    _clients: dict[str, Any] = {}

    def _thinking_mode(self, *, for_tools: bool = False) -> str:
        """Get Kimi thinking mode from environment.

        Uses separate env vars for normal vs tool-calling turns:
        - non-tools: KIMI_THINKING_MODE (default: disabled)
        - tools: KIMI_THINKING_MODE_TOOLS (default: disabled)
        """
        if for_tools:
            mode = os.getenv("KIMI_THINKING_MODE_TOOLS", "disabled").strip().lower()
        else:
            mode = os.getenv("KIMI_THINKING_MODE", "disabled").strip().lower()
        if mode in {"enabled", "disabled"}:
            return mode
        return "disabled"

    def _temperature(self, *, for_tools: bool = False) -> float:
        """Kimi K2.6/K2.5 require fixed temperatures based on thinking mode.

        Thinking mode (default): 1.0
        Non-thinking mode: 0.6
        Any other value results in a 400 error.
        """
        return 1.0 if self._thinking_mode(for_tools=for_tools) == "enabled" else 0.6

    def _get_client(self, config: "LLMConfig"):
        """Get or create an OpenAI SDK client pointed at Kimi."""
        from openai import OpenAI

        base_url = config.base_url or "https://api.moonshot.ai/v1"
        cache_key = f"{config.api_key}:{base_url}"
        if cache_key not in self._clients:
            kwargs: dict[str, Any] = {
                "api_key": config.api_key,
                "base_url": base_url,
            }
            if config.extra_headers:
                kwargs["default_headers"] = config.extra_headers
            self._clients[cache_key] = OpenAI(**kwargs)
        return self._clients[cache_key]

    def complete(
        self,
        messages: list[Message],
        config: "LLMConfig",
    ) -> str:
        """Generate a text completion using the Kimi API."""
        client = self._get_client(config)
        response = client.chat.completions.create(
            model=config.model,
            messages=messages_to_kimi(messages),
            temperature=self._temperature(for_tools=False),
            top_p=0.95,
            extra_body={"thinking": {"type": self._thinking_mode(for_tools=False)}},
        )
        content = response.choices[0].message.content
        return content if content else ""

    def complete_with_tools(
        self,
        messages: list[Message],
        tools: "list[ToolSchema | dict[str, Any]]",
        config: "LLMConfig",
    ) -> ToolResponse:
        """Generate a response with Kimi tool calling."""
        client = self._get_client(config)
        normalized = _normalize_tools(tools)

        kwargs: dict[str, Any] = {
            "model": config.model,
            "messages": messages_to_kimi(messages),
            "temperature": self._temperature(for_tools=True),
            "top_p": 0.95,
            # OpenAI SDK rejects unknown top-level kwargs; provider-specific fields
            # must go through extra_body.
            "extra_body": {"thinking": {"type": self._thinking_mode(for_tools=True)}},
        }
        if normalized:
            kwargs["tools"] = normalized
        if config.tool_choice:
            kwargs["tool_choice"] = config.tool_choice

        response = client.chat.completions.create(**kwargs)
        return ToolResponse.from_openai(response)

    def stream(
        self,
        messages: list[Message],
        config: "LLMConfig",
    ) -> Iterator[str]:
        """Generate a streaming completion using the Kimi API."""
        client = self._get_client(config)
        stream = client.chat.completions.create(
            model=config.model,
            messages=messages_to_kimi(messages),
            temperature=self._temperature(for_tools=False),
            top_p=0.95,
            stream=True,
            extra_body={"thinking": {"type": self._thinking_mode(for_tools=False)}},
        )

        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    def get_langchain_model(self, config: "LLMConfig") -> "BaseChatModel":
        """Get a LangChain model pointed at Kimi for advanced callers."""
        from langchain_openai import ChatOpenAI

        kwargs: dict[str, Any] = {
            "model": config.model,
            "api_key": config.api_key,
            "base_url": config.base_url or "https://api.moonshot.ai/v1",
            "temperature": self._temperature(for_tools=False),
            "top_p": 0.95,
        }
        if config.extra_headers:
            kwargs["default_headers"] = config.extra_headers

        return ChatOpenAI(**kwargs)
