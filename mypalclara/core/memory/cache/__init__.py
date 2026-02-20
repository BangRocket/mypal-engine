"""Caching layer for Clara Memory System.

Performance optimizations:
- Redis-backed cache for embeddings, search results, key memories
- Graph memory cache for search and full snapshots
- Graceful degradation when Redis unavailable
- TTL-based expiration
"""

from clara_core.memory.cache.graph_cache import (
    GRAPH_ALL_TTL,
    GRAPH_SEARCH_TTL,
    GraphCache,
)
from clara_core.memory.cache.redis_cache import (
    EMBEDDING_TTL,
    KEY_MEMORIES_TTL,
    SEARCH_RESULTS_TTL,
    RedisCache,
)

__all__ = [
    "RedisCache",
    "GraphCache",
    "EMBEDDING_TTL",
    "SEARCH_RESULTS_TTL",
    "KEY_MEMORIES_TTL",
    "GRAPH_SEARCH_TTL",
    "GRAPH_ALL_TTL",
]
