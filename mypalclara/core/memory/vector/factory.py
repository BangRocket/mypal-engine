"""Factory for creating vector store instances."""

import importlib
from typing import Dict, Optional


def load_class(class_type):
    """Load a class from a module path."""
    module_path, class_name = class_type.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


class VectorStoreFactory:
    """Factory for creating vector store instances."""

    # Provider mappings - only include the stores Clara uses
    provider_to_class = {
        "qdrant": "mypalclara.core.memory.vector.qdrant.Qdrant",
        "pgvector": "mypalclara.core.memory.vector.pgvector.PGVector",
    }

    @classmethod
    def create(cls, provider_name: str, config: Optional[Dict] = None):
        """Create a vector store instance.

        Args:
            provider_name: The provider name (qdrant, pgvector)
            config: Configuration dictionary

        Returns:
            Configured vector store instance

        Raises:
            ValueError: If provider is not supported
        """
        class_type = cls.provider_to_class.get(provider_name)
        if class_type:
            if not isinstance(config, dict):
                config = config.model_dump()
            vector_store_instance = load_class(class_type)
            return vector_store_instance(**config)
        else:
            raise ValueError(f"Unsupported VectorStore provider: {provider_name}")

    @classmethod
    def reset(cls, instance):
        """Reset a vector store instance."""
        instance.reset()
        return instance

    @classmethod
    def get_supported_providers(cls) -> list:
        """Get list of supported providers."""
        return list(cls.provider_to_class.keys())

    @classmethod
    def register_provider(cls, name: str, class_path: str):
        """Register a new provider.

        Args:
            name: Provider name
            class_path: Full path to vector store class
        """
        cls.provider_to_class[name] = class_path
