"""Clara Memory System (Rook) - Native memory management for Clara.

This module provides Clara's memory system, called "Rook" internally.

Usage:
    from clara_core.memory import ROOK

    if ROOK:
        ROOK.add(messages, user_id="user-123", agent_id="clara")
        results = ROOK.search("preferences", user_id="user-123")
"""

from clara_core.memory.config import (
    ENABLE_GRAPH_MEMORY,
    GRAPH_STORE_PROVIDER,
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

__all__ = [
    "ClaraMemory",
    "ClaraMemoryConfig",
    "ClaraMemoryItem",
    "ClaraMemoryValidationError",
    "MemoryType",
    "ROOK",
    "config",
    "ROOK_PROVIDER",
    "ROOK_MODEL",
    "ROOK_DATABASE_URL",
    "ENABLE_GRAPH_MEMORY",
    "GRAPH_STORE_PROVIDER",
]
