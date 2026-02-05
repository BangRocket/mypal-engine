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

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from clara_core.llm.providers.base import LLMProvider
from clara_core.llm.tools.formats import (
    convert_message_to_anthropic,
    convert_messages_to_langchain,
)
from clara_core.llm.tools.response import ToolResponse

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from clara_core.llm.config import LLMConfig


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
        messages: list[dict[str, Any]],
        config: "LLMConfig",
    ) -> str:
        """Generate a text completion using LangChain."""
        model = self.get_langchain_model(config)
        lc_messages = convert_messages_to_langchain(messages)
        response = model.invoke(lc_messages)
        return response.content if isinstance(response.content, str) else str(response.content)

    def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        config: "LLMConfig",
    ) -> ToolResponse:
        """Generate a response with tool calling via LangChain bind_tools()."""
        model = self.get_langchain_model(config)

        # Bind tools to model
        if tools:
            model_with_tools = model.bind_tools(tools)
        else:
            model_with_tools = model

        # Convert and invoke
        lc_messages = convert_messages_to_langchain(messages)
        response = model_with_tools.invoke(lc_messages)

        return ToolResponse.from_langchain(response)

    def stream(
        self,
        messages: list[dict[str, Any]],
        config: "LLMConfig",
    ) -> Iterator[str]:
        """Generate a streaming completion using LangChain."""
        model = self.get_langchain_model(config)
        lc_messages = convert_messages_to_langchain(messages)

        for chunk in model.stream(lc_messages):
            content = chunk.content
            if content:
                yield content if isinstance(content, str) else str(content)

    def stream_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        config: "LLMConfig",
    ) -> Iterator[str]:
        """Stream with tool support.

        Note: LangChain streaming with tools doesn't easily expose tool calls
        during streaming, so we yield text chunks and return the full response
        for tool call detection after streaming completes.
        """
        model = self.get_langchain_model(config)

        if tools:
            model_with_tools = model.bind_tools(tools)
        else:
            model_with_tools = model

        lc_messages = convert_messages_to_langchain(messages)

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
                "Amazon Bedrock support requires langchain-aws. "
                "Install it with: pip install langchain-aws"
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
            raise ValueError(
                "Azure OpenAI requires AZURE_DEPLOYMENT_NAME environment variable"
            )

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
    """Direct Anthropic SDK provider for native tool calling.

    Use this when you need direct access to Anthropic's native SDK,
    bypassing LangChain. Useful for:
    - Maximum compatibility with clewdr proxies
    - Direct access to all Anthropic features
    - Avoiding LangChain overhead
    """

    _client = None

    def _get_client(self, config: "LLMConfig"):
        """Get or create Anthropic client."""
        from anthropic import Anthropic

        if self._client is None:
            kwargs: dict[str, Any] = {"api_key": config.api_key}
            if config.base_url:
                kwargs["base_url"] = config.base_url
            if config.extra_headers:
                kwargs["default_headers"] = config.extra_headers
            self._client = Anthropic(**kwargs)
        return self._client

    def complete(
        self,
        messages: list[dict[str, Any]],
        config: "LLMConfig",
    ) -> str:
        """Generate a text completion using native Anthropic SDK."""
        client = self._get_client(config)

        # Extract system messages
        system_parts = []
        filtered = []
        for m in messages:
            if m.get("role") == "system":
                content = m.get("content", "")
                if isinstance(content, str):
                    system_parts.append(content)
            else:
                filtered.append(convert_message_to_anthropic(m))
        system = "\n\n".join(system_parts)

        kwargs: dict[str, Any] = {
            "model": config.model,
            "max_tokens": config.max_tokens,
            "messages": filtered,
        }
        if system:
            kwargs["system"] = system

        response = client.messages.create(**kwargs)
        return response.content[0].text if response.content else ""

    def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        config: "LLMConfig",
    ) -> ToolResponse:
        """Generate a response with native Anthropic tool calling."""
        from clara_core.llm.tools.formats import convert_tools_to_claude_format

        client = self._get_client(config)

        # Extract system messages
        system_parts = []
        filtered = []
        for m in messages:
            if m.get("role") == "system":
                system_parts.append(m.get("content", ""))
            else:
                filtered.append(convert_message_to_anthropic(m))
        system = "\n\n".join(system_parts)

        kwargs: dict[str, Any] = {
            "model": config.model,
            "max_tokens": config.max_tokens,
            "messages": filtered,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = convert_tools_to_claude_format(tools)

        response = client.messages.create(**kwargs)
        return ToolResponse.from_anthropic(response)

    def stream(
        self,
        messages: list[dict[str, Any]],
        config: "LLMConfig",
    ) -> Iterator[str]:
        """Generate a streaming completion using native Anthropic SDK."""
        client = self._get_client(config)

        # Extract system messages
        system_parts = []
        filtered = []
        for m in messages:
            if m.get("role") == "system":
                content = m.get("content", "")
                if isinstance(content, str):
                    system_parts.append(content)
            else:
                filtered.append(convert_message_to_anthropic(m))
        system = "\n\n".join(system_parts)

        kwargs: dict[str, Any] = {
            "model": config.model,
            "max_tokens": config.max_tokens,
            "messages": filtered,
        }
        if system:
            kwargs["system"] = system

        with client.messages.stream(**kwargs) as stream:
            yield from stream.text_stream

    def get_langchain_model(self, config: "LLMConfig") -> "BaseChatModel":
        """Get LangChain model (uses LangChainProvider internally)."""
        from langchain_anthropic import ChatAnthropic

        kwargs: dict[str, Any] = {
            "model": config.model,
            "api_key": config.api_key,
            "max_tokens": config.max_tokens,
        }
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
        """Get or create OpenAI client."""
        from openai import OpenAI

        cache_key = f"{config.base_url}"
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
        messages: list[dict[str, Any]],
        config: "LLMConfig",
    ) -> str:
        """Generate a text completion using OpenAI SDK."""
        client = self._get_client(config)
        response = client.chat.completions.create(
            model=config.model,
            messages=messages,
        )
        content = response.choices[0].message.content
        return content if content else ""

    def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        config: "LLMConfig",
    ) -> ToolResponse:
        """Generate a response with OpenAI tool calling."""
        client = self._get_client(config)

        kwargs: dict[str, Any] = {
            "model": config.model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        response = client.chat.completions.create(**kwargs)
        return ToolResponse.from_openai(response)

    def stream(
        self,
        messages: list[dict[str, Any]],
        config: "LLMConfig",
    ) -> Iterator[str]:
        """Generate a streaming completion using OpenAI SDK."""
        client = self._get_client(config)
        stream = client.chat.completions.create(
            model=config.model,
            messages=messages,
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
