"""Factory for creating embedder instances."""

import importlib
import os
from typing import Dict, Optional

from clara_core.memory.embeddings.base import BaseEmbedderConfig


def load_class(class_type):
    """Load a class from a module path."""
    module_path, class_name = class_type.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


class MockEmbeddings:
    """Mock embeddings for testing or when embeddings are not needed."""

    def embed(self, text, memory_action=None):
        """Return a mock embedding."""
        return [0.0] * 1536


class EmbedderFactory:
    """Factory for creating embedder instances."""

    # Provider mappings - only include what Clara needs
    provider_to_class = {
        "openai": "clara_core.memory.embeddings.openai.OpenAIEmbedding",
    }

    @classmethod
    def create(
        cls,
        provider_name: str,
        config: Optional[Dict] = None,
        vector_config: Optional[Dict] = None,
        enable_cache: Optional[bool] = None,
    ):
        """Create an embedder instance.

        Args:
            provider_name: The provider name (openai)
            config: Configuration dictionary
            vector_config: Optional vector store config
            enable_cache: Whether to wrap with caching. If None, uses MEMORY_EMBEDDING_CACHE env var.

        Returns:
            Configured embedder instance (optionally wrapped with cache)

        Raises:
            ValueError: If provider is not supported
        """
        # Handle special case for mock embeddings
        if vector_config and getattr(vector_config, "enable_embeddings", False):
            return MockEmbeddings()

        class_type = cls.provider_to_class.get(provider_name)
        if class_type:
            embedder_instance = load_class(class_type)
            base_config = BaseEmbedderConfig(**config)
            embedder = embedder_instance(base_config)

            # Optionally wrap with cache
            if enable_cache is None:
                enable_cache = os.getenv("MEMORY_EMBEDDING_CACHE", "true").lower() == "true"

            if enable_cache and os.getenv("REDIS_URL"):
                from clara_core.memory.embeddings.cached import CachedEmbedding

                embedder = CachedEmbedding(embedder, enabled=True)

            return embedder
        else:
            raise ValueError(f"Unsupported Embedder provider: {provider_name}")

    @classmethod
    def get_supported_providers(cls) -> list:
        """Get list of supported providers."""
        return list(cls.provider_to_class.keys())

    @classmethod
    def register_provider(cls, name: str, class_path: str):
        """Register a new provider.

        Args:
            name: Provider name
            class_path: Full path to embedder class
        """
        cls.provider_to_class[name] = class_path
