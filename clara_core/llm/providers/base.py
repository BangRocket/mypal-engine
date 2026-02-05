"""Abstract base class for LLM providers.

Defines the interface that all LLM provider implementations must follow.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from clara_core.llm.config import LLMConfig
    from clara_core.llm.messages import Message
    from clara_core.llm.tools.response import ToolResponse
    from clara_core.llm.tools.schema import ToolSchema


def _normalize_tools(tools: "list[ToolSchema | dict[str, Any]]") -> list[dict[str, Any]]:
    """Normalize a list of ToolSchema or dicts to OpenAI-format dicts.

    Providers call this at their boundary to accept both typed and untyped tools.

    Args:
        tools: Mixed list of ToolSchema objects and/or OpenAI-format dicts.

    Returns:
        List of dicts in OpenAI format.
    """
    from clara_core.llm.tools.schema import ToolSchema

    result = []
    for t in tools:
        if isinstance(t, ToolSchema):
            result.append(t.to_openai())
        else:
            result.append(t)
    return result


class LLMProvider(ABC):
    """Abstract base class for LLM providers.

    Provides a unified interface for different LLM backends:
    - complete: Non-streaming text completion
    - complete_with_tools: Tool calling with structured response
    - stream: Streaming text completion
    - stream_with_tools: Streaming with tool support
    - get_langchain_model: Access to underlying LangChain model

    All methods accept list[Message] and an LLMConfig for provider configuration.
    Tool methods accept list[ToolSchema | dict] — providers normalize at their boundary.
    """

    @abstractmethod
    def complete(
        self,
        messages: list[Message],
        config: "LLMConfig",
    ) -> str:
        """Generate a text completion.

        Args:
            messages: List of typed Message objects
            config: LLM configuration

        Returns:
            Generated text response
        """
        pass

    @abstractmethod
    def complete_with_tools(
        self,
        messages: list[Message],
        tools: "list[ToolSchema | dict[str, Any]]",
        config: "LLMConfig",
    ) -> "ToolResponse":
        """Generate a response with tool calling support.

        Args:
            messages: List of typed Message objects
            tools: List of ToolSchema objects or OpenAI-format dicts
            config: LLM configuration

        Returns:
            ToolResponse with content and optional tool_calls
        """
        pass

    @abstractmethod
    def stream(
        self,
        messages: list[Message],
        config: "LLMConfig",
    ) -> Iterator[str]:
        """Generate a streaming text completion.

        Args:
            messages: List of typed Message objects
            config: LLM configuration

        Yields:
            Text chunks as they arrive
        """
        pass

    def stream_with_tools(
        self,
        messages: list[Message],
        tools: "list[ToolSchema | dict[str, Any]]",
        config: "LLMConfig",
    ) -> Iterator[str]:
        """Generate a streaming response with tools.

        Default implementation falls back to non-streaming.
        Providers can override for true streaming support.

        Args:
            messages: List of typed Message objects
            tools: List of tool definitions
            config: LLM configuration

        Yields:
            Text chunks (tool calls detected at end)
        """
        # Default: use non-streaming and yield result
        response = self.complete_with_tools(messages, tools, config)
        if response.content:
            yield response.content

    async def acomplete(
        self,
        messages: list[Message],
        config: "LLMConfig",
    ) -> str:
        """Async text completion.

        Default implementation runs sync version in executor.

        Args:
            messages: List of typed Message objects
            config: LLM configuration

        Returns:
            Generated text response
        """
        import asyncio

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.complete(messages, config),
        )

    async def acomplete_with_tools(
        self,
        messages: list[Message],
        tools: "list[ToolSchema | dict[str, Any]]",
        config: "LLMConfig",
    ) -> "ToolResponse":
        """Async tool completion.

        Default implementation runs sync version in executor.

        Args:
            messages: List of typed Message objects
            tools: List of tool definitions
            config: LLM configuration

        Returns:
            ToolResponse with content and optional tool_calls
        """
        import asyncio

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.complete_with_tools(messages, tools, config),
        )

    async def astream(
        self,
        messages: list[Message],
        config: "LLMConfig",
    ) -> AsyncIterator[str]:
        """Async streaming completion.

        Default implementation wraps sync stream.

        Args:
            messages: List of typed Message objects
            config: LLM configuration

        Yields:
            Text chunks as they arrive
        """
        import asyncio

        loop = asyncio.get_event_loop()

        # Run sync stream in executor, yielding chunks
        def _sync_stream():
            return list(self.stream(messages, config))

        chunks = await loop.run_in_executor(None, _sync_stream)
        for chunk in chunks:
            yield chunk

    @abstractmethod
    def get_langchain_model(
        self,
        config: "LLMConfig",
    ) -> "BaseChatModel":
        """Get the underlying LangChain chat model.

        Useful for advanced use cases that need direct LangChain access.

        Args:
            config: LLM configuration

        Returns:
            LangChain BaseChatModel instance
        """
        pass
