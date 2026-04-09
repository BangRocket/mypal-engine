"""Clara Memory System (Palace) - Native memory management for Clara.

This module provides Clara's memory system, called "Palace" internally.

Usage:
    from mypalclara.core.memory import PALACE

    if PALACE:
        PALACE.add(messages, user_id="user-123", agent_id="clara")
        results = PALACE.search("preferences", user_id="user-123")
"""

from mypalclara.core.memory.config import (
    ENABLE_GRAPH_MEMORY,
    GRAPH_STORE_PROVIDER,
    PALACE,
    PALACE_DATABASE_URL,
    PALACE_MODEL,
    PALACE_PROVIDER,
    config,
)
from mypalclara.core.memory.core.memory import (
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
    "PALACE",
    "config",
    "PALACE_PROVIDER",
    "PALACE_MODEL",
    "PALACE_DATABASE_URL",
    "ENABLE_GRAPH_MEMORY",
    "GRAPH_STORE_PROVIDER",
]
