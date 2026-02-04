"""Caching layer for Clara Memory System.

Performance optimizations:
- Redis-backed cache for embeddings, search results, key memories
- Graceful degradation when Redis unavailable
- TTL-based expiration
"""

from clara_core.memory.cache.redis_cache import (
    EMBEDDING_TTL,
    KEY_MEMORIES_TTL,
    SEARCH_RESULTS_TTL,
    RedisCache,
)

__all__ = [
    "RedisCache",
    "EMBEDDING_TTL",
    "SEARCH_RESULTS_TTL",
    "KEY_MEMORIES_TTL",
]
