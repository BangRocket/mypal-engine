"""Clara Memory System (Rook) - Native memory management for Clara.

This module provides Clara's memory system, called "Rook" internally.

Main Classes:
- ClaraMemory: Core memory operations (add, search, delete, delete_all, get_all)

Configuration is handled through environment variables (ROOK_* prefix):
- ROOK_PROVIDER: LLM provider (openrouter, nanogpt, openai, anthropic)
- ROOK_MODEL: LLM model for memory extraction
- ROOK_DATABASE_URL: PostgreSQL with pgvector for production
- ENABLE_GRAPH_MEMORY: Enable graph memory for relationships
- GRAPH_STORE_PROVIDER: Graph store (neo4j, kuzu)

Usage:
    from clara_core.memory import ClaraMemory, ROOK

    # Use the pre-initialized singleton
    if ROOK:
        ROOK.add("User prefers dark mode", user_id="user-123")
        results = ROOK.search("preferences", user_id="user-123")

    # Or create a custom instance
    memory = ClaraMemory.from_config({...})
"""

# Core memory class
# Configuration and singleton
from clara_core.memory.config import (
    ENABLE_GRAPH_MEMORY,
    GRAPH_STORE_PROVIDER,
    KUZU_DATA_DIR,
    NEO4J_PASSWORD,
    NEO4J_URL,
    NEO4J_USERNAME,
    QDRANT_DATA_DIR,
    ROOK,
    ROOK_DATABASE_URL,
    ROOK_MODEL,
    ROOK_PROVIDER,
    config,
)
from clara_core.memory.core.memory import (
    ClaraMemory,
    ClaraMemoryConfig,
    ClaraMemoryItem,
    ClaraMemoryValidationError,
    MemoryType,
)

# Utilities
from clara_core.memory.core.utils import (
    extract_json,
    parse_messages,
    remove_code_blocks,
)

# Factories
from clara_core.memory.vector.factory import VectorStoreFactory

__all__ = [
    # Core
    "ClaraMemory",
    "ClaraMemoryItem",
    "ClaraMemoryConfig",
    "ClaraMemoryValidationError",
    "MemoryType",
    # Singleton
    "ROOK",
    "config",
    # Settings
    "ROOK_PROVIDER",
    "ROOK_MODEL",
    "ROOK_DATABASE_URL",
    "ENABLE_GRAPH_MEMORY",
    "GRAPH_STORE_PROVIDER",
    "QDRANT_DATA_DIR",
    "KUZU_DATA_DIR",
    "NEO4J_URL",
    "NEO4J_USERNAME",
    "NEO4J_PASSWORD",
    # Factories
    "VectorStoreFactory",
    # Utilities
    "parse_messages",
    "remove_code_blocks",
    "extract_json",
]
