"""Factory for creating LLM instances."""

import importlib
from typing import Dict, Optional, Union

from clara_core.memory.llm.base import BaseLlmConfig, OpenAIConfig, AnthropicConfig


def load_class(class_type):
    """Load a class from a module path."""
    module_path, class_name = class_type.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


class LlmFactory:
    """Factory for creating LLM instances."""

    # Provider mappings with their config classes (only providers Clara uses)
    provider_to_class = {
        "openai": ("clara_core.memory.llm.openai.OpenAILLM", OpenAIConfig),
        "anthropic": ("clara_core.memory.llm.anthropic.AnthropicLLM", AnthropicConfig),
    }

    @classmethod
    def create(
        cls,
        provider_name: str,
        config: Optional[Union[BaseLlmConfig, Dict]] = None,
        **kwargs
    ):
        """Create an LLM instance.

        Args:
            provider_name: The provider name (openai, anthropic)
            config: Configuration object or dict
            **kwargs: Additional configuration parameters

        Returns:
            Configured LLM instance

        Raises:
            ValueError: If provider is not supported
        """
        if provider_name not in cls.provider_to_class:
            raise ValueError(f"Unsupported LLM provider: {provider_name}")

        class_type, config_class = cls.provider_to_class[provider_name]
        llm_class = load_class(class_type)

        # Handle configuration
        if config is None:
            config = config_class(**kwargs)
        elif isinstance(config, dict):
            config.update(kwargs)
            config = config_class(**config)
        elif isinstance(config, BaseLlmConfig):
            # Convert base config to provider-specific config if needed
            if config_class != BaseLlmConfig:
                config_dict = {
                    "model": config.model,
                    "temperature": config.temperature,
                    "api_key": config.api_key,
                    "max_tokens": config.max_tokens,
                    "top_p": config.top_p,
                    "top_k": config.top_k,
                    "enable_vision": config.enable_vision,
                    "vision_details": config.vision_details,
                }
                config_dict.update(kwargs)
                config = config_class(**config_dict)

        return llm_class(config)

    @classmethod
    def get_supported_providers(cls) -> list:
        """Get list of supported providers."""
        return list(cls.provider_to_class.keys())

    @classmethod
    def register_provider(cls, name: str, class_path: str, config_class=None):
        """Register a new provider.

        Args:
            name: Provider name
            class_path: Full path to LLM class
            config_class: Configuration class for the provider (defaults to BaseLlmConfig)
        """
        if config_class is None:
            config_class = BaseLlmConfig
        cls.provider_to_class[name] = (class_path, config_class)
