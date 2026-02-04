"""Clara Memory System - Native memory management for Clara.

This module provides Clara's memory system, absorbing the functionality
from mem0 into a streamlined, Clara-specific implementation.

Main Classes:
- ClaraMemory: Core memory operations (add, search, get, update, delete)
- MemoryManager: High-level orchestrator for memory operations

Configuration is handled through environment variables:
- MEM0_PROVIDER: LLM provider (openrouter, nanogpt, openai, anthropic)
- MEM0_MODEL: LLM model for memory extraction
- MEM0_DATABASE_URL: PostgreSQL with pgvector for production
- ENABLE_GRAPH_MEMORY: Enable graph memory for relationships
- GRAPH_STORE_PROVIDER: Graph store (neo4j, kuzu)

Usage:
    from clara_core.memory import ClaraMemory, MEM0

    # Use the pre-initialized singleton
    if MEM0:
        MEM0.add("User prefers dark mode", user_id="user-123")
        results = MEM0.search("preferences", user_id="user-123")

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
    MEM0,
    config,
    MEM0_PROVIDER,
    MEM0_MODEL,
    ENABLE_GRAPH_MEMORY,
    GRAPH_STORE_PROVIDER,
    QDRANT_DATA_DIR,
    KUZU_DATA_DIR,
    MEM0_DATABASE_URL,
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
    "MEM0",
    "config",
    # Settings
    "MEM0_PROVIDER",
    "MEM0_MODEL",
    "ENABLE_GRAPH_MEMORY",
    "GRAPH_STORE_PROVIDER",
    "QDRANT_DATA_DIR",
    "KUZU_DATA_DIR",
    "MEM0_DATABASE_URL",
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
