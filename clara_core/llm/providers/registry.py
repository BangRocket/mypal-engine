"""Provider registry for managing LLM provider instances.

Provides a singleton registry for accessing configured LLM providers
with automatic provider selection based on configuration.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clara_core.llm.config import LLMConfig
    from clara_core.llm.providers.base import LLMProvider


class ProviderRegistry:
    """Singleton registry for LLM providers.

    Manages provider instances and provides factory methods for
    creating configured providers.

    Usage:
        # Get the default provider for current environment
        provider = ProviderRegistry.get_provider()

        # Get a specific provider type
        provider = ProviderRegistry.get_provider("langchain")

        # Get provider with specific config
        config = LLMConfig.from_env(tier="high")
        provider = ProviderRegistry.get_provider(config=config)
    """

    _instance = None
    _providers: dict[str, "LLMProvider"] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._providers = {}
        return cls._instance

    @classmethod
    def get_provider(
        cls,
        provider_type: str = "langchain",
        config: "LLMConfig | None" = None,
    ) -> "LLMProvider":
        """Get or create a provider instance.

        Args:
            provider_type: Type of provider to use:
                - "langchain": LangChain-based (default, recommended)
                - "direct_anthropic": Direct Anthropic SDK
                - "direct_openai": Direct OpenAI SDK
            config: Optional LLMConfig for initialization

        Returns:
            LLMProvider instance
        """
        from clara_core.llm.providers.langchain import (
            DirectAnthropicProvider,
            DirectOpenAIProvider,
            LangChainProvider,
        )

        registry = cls()

        # Return cached provider if available
        if provider_type in registry._providers:
            return registry._providers[provider_type]

        # Create new provider
        if provider_type == "langchain":
            provider = LangChainProvider()
        elif provider_type == "direct_anthropic":
            provider = DirectAnthropicProvider()
        elif provider_type == "direct_openai":
            provider = DirectOpenAIProvider()
        else:
            raise ValueError(f"Unknown provider type: {provider_type}")

        registry._providers[provider_type] = provider
        return provider

    @classmethod
    def get_default_provider(cls) -> "LLMProvider":
        """Get the default provider based on LLM_PROVIDER env var.

        Returns LangChain provider which handles all backends.
        """
        return cls.get_provider("langchain")

    @classmethod
    def get_direct_provider(cls) -> "LLMProvider":
        """Get a direct SDK provider based on LLM_PROVIDER env var.

        Uses native SDK (Anthropic or OpenAI) instead of LangChain.
        Useful for maximum compatibility or avoiding LangChain overhead.
        """
        provider_name = os.getenv("LLM_PROVIDER", "openrouter").lower()

        if provider_name == "anthropic":
            return cls.get_provider("direct_anthropic")
        else:
            return cls.get_provider("direct_openai")

    @classmethod
    def clear_cache(cls):
        """Clear all cached providers.

        Useful for testing or when environment changes.
        """
        registry = cls()
        registry._providers.clear()


def get_provider(
    provider_type: str = "langchain",
    config: "LLMConfig | None" = None,
) -> "LLMProvider":
    """Convenience function to get a provider.

    Args:
        provider_type: Type of provider ("langchain", "direct_anthropic", "direct_openai")
        config: Optional configuration

    Returns:
        LLMProvider instance
    """
    return ProviderRegistry.get_provider(provider_type, config)
