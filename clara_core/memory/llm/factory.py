"""Factory for creating LLM instances.

Uses the unified provider that delegates to clara_core.llm for consistent
behavior across all LLM operations.
"""

import importlib
from typing import Dict, Optional, Union

from clara_core.memory.llm.base import BaseLlmConfig
from clara_core.memory.llm.unified import UnifiedLLMConfig


def load_class(class_type):
    """Load a class from a module path."""
    module_path, class_name = class_type.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


class LlmFactory:
    """Factory for creating LLM instances.

    Supports:
    - "unified": Uses clara_core.llm providers (recommended)
    """

    # Provider mappings with their config classes
    provider_to_class = {
        "unified": ("clara_core.memory.llm.unified.UnifiedLLM", UnifiedLLMConfig),
    }

    @classmethod
    def create(cls, provider_name: str, config: Optional[Union[BaseLlmConfig, Dict]] = None, **kwargs):
        """Create an LLM instance.

        Args:
            provider_name: The provider name (e.g., "unified")
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
            # If already correct type, use directly
            if isinstance(config, config_class):
                pass
            # Convert base config to provider-specific config if needed
            elif config_class != BaseLlmConfig:
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
                # Include provider for UnifiedLLMConfig
                if hasattr(config, "provider"):
                    config_dict["provider"] = config.provider
                if hasattr(config, "base_url"):
                    config_dict["base_url"] = config.base_url
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
