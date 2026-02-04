"""Factory for creating graph store instances."""

import importlib
from typing import Any


def load_class(class_type):
    """Load a class from a module path."""
    module_path, class_name = class_type.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


class GraphStoreFactory:
    """Factory for creating graph memory instances."""

    # Provider mappings - only include what Clara uses
    provider_to_class = {
        "neo4j": "clara_core.memory.graph.neo4j.MemoryGraph",
        "kuzu": "clara_core.memory.graph.kuzu.MemoryGraph",
        "default": "clara_core.memory.graph.neo4j.MemoryGraph",
    }

    @classmethod
    def create(cls, provider_name: str, config: Any):
        """Create a graph store instance.

        Args:
            provider_name: The provider name (neo4j, kuzu)
            config: ClaraMemoryConfig instance

        Returns:
            Configured graph store instance

        Raises:
            ImportError: If the provider class cannot be imported
        """
        class_type = cls.provider_to_class.get(provider_name, cls.provider_to_class["default"])
        try:
            GraphClass = load_class(class_type)
        except (ImportError, AttributeError) as e:
            raise ImportError(f"Could not import graph store for provider '{provider_name}': {e}")
        return GraphClass(config)

    @classmethod
    def get_supported_providers(cls) -> list:
        """Get list of supported providers."""
        return list(cls.provider_to_class.keys())
