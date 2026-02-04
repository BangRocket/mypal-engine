"""Clara Memory System (Rook) - Native memory management for Clara.

This module provides Clara's memory system, called "Rook" internally.
It absorbs the functionality from mem0 into a streamlined, Clara-specific implementation.

Main Classes:
- ClaraMemory: Core memory operations (add, search, get, update, delete)
- MemoryManager: High-level orchestrator for memory operations

Configuration is handled through environment variables (ROOK_* preferred, MEM0_* fallback):
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
from clara_core.memory.core.memory import (
    ClaraMemory,
    ClaraMemoryItem,
    ClaraMemoryConfig,
    ClaraMemoryValidationError,
    MemoryType,
)

# Configuration and singleton
from clara_core.memory.config import (
    ROOK,
    MEM0,  # Backward compatibility alias for ROOK
    config,
    ROOK_PROVIDER,
    ROOK_MODEL,
    ROOK_DATABASE_URL,
    MEM0_PROVIDER,  # Backward compatibility alias
    MEM0_MODEL,  # Backward compatibility alias
    MEM0_DATABASE_URL,  # Backward compatibility alias
    ENABLE_GRAPH_MEMORY,
    GRAPH_STORE_PROVIDER,
    QDRANT_DATA_DIR,
    KUZU_DATA_DIR,
    NEO4J_URL,
    NEO4J_USERNAME,
    NEO4J_PASSWORD,
)

# Factories
from clara_core.memory.vector.factory import VectorStoreFactory
from clara_core.memory.embeddings.factory import EmbedderFactory
from clara_core.memory.llm.factory import LlmFactory

# Utilities
from clara_core.memory.core.utils import (
    parse_messages,
    remove_code_blocks,
    extract_json,
)

# Backward compatibility - alias Memory to ClaraMemory
Memory = ClaraMemory

# Re-export MemoryManager for convenience (actual implementation in memory_manager.py)
# This allows: from clara_core.memory import MemoryManager
try:
    from clara_core.memory_manager import MemoryManager, load_initial_profile
except ImportError:
    # Module may not be available in all contexts
    MemoryManager = None
    load_initial_profile = None

__all__ = [
    # Core
    "ClaraMemory",
    "ClaraMemoryItem",
    "ClaraMemoryConfig",
    "ClaraMemoryValidationError",
    "MemoryType",
    # Singleton
    "ROOK",
    "MEM0",  # Backward compatibility alias for ROOK
    "config",
    # Settings (ROOK_* preferred)
    "ROOK_PROVIDER",
    "ROOK_MODEL",
    "ROOK_DATABASE_URL",
    # Settings (MEM0_* backward compatibility)
    "MEM0_PROVIDER",
    "MEM0_MODEL",
    "MEM0_DATABASE_URL",
    # Other settings
    "ENABLE_GRAPH_MEMORY",
    "GRAPH_STORE_PROVIDER",
    "QDRANT_DATA_DIR",
    "KUZU_DATA_DIR",
    "NEO4J_URL",
    "NEO4J_USERNAME",
    "NEO4J_PASSWORD",
    # Factories
    "VectorStoreFactory",
    "EmbedderFactory",
    "LlmFactory",
    # Utilities
    "parse_messages",
    "remove_code_blocks",
    "extract_json",
    # Backward compatibility
    "Memory",
    "MemoryManager",
    "load_initial_profile",
]
