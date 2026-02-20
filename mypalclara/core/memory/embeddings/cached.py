"""Cached embedding wrapper for Clara Memory System.

Wraps any embedder with Redis caching:
- Cache hit: ~5ms
- Cache miss: ~150ms (then cached)
- Graceful fallback if Redis unavailable
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Literal, Optional

from mypalclara.core.memory.embeddings.base import EmbeddingBase

if TYPE_CHECKING:
    from mypalclara.core.memory.cache.redis_cache import RedisCache

logger = logging.getLogger("clara.memory.embeddings.cached")


class CachedEmbedding(EmbeddingBase):
    """Embedding wrapper with Redis caching.

    Transparently caches embeddings to avoid repeated API calls
    for the same text. Falls back gracefully if cache unavailable.
    """

    def __init__(
        self,
        embedder: EmbeddingBase,
        cache: Optional["RedisCache"] = None,
        enabled: bool = True,
    ):
        """Initialize cached embedding wrapper.

        Args:
            embedder: The underlying embedder to wrap
            cache: Redis cache instance. If None, uses singleton.
            enabled: Whether caching is enabled (can be toggled via env)
        """
        # Don't call super().__init__ - we're wrapping, not extending
        self._embedder = embedder
        self._cache = cache
        self._enabled = enabled and os.getenv("MEMORY_EMBEDDING_CACHE", "true").lower() == "true"
        self._hits = 0
        self._misses = 0

    @property
    def cache(self) -> "RedisCache | None":
        """Lazy-load cache singleton."""
        if self._cache is None and self._enabled:
            from mypalclara.core.memory.cache.redis_cache import RedisCache

            self._cache = RedisCache.get_instance()
        return self._cache

    @property
    def config(self):
        """Delegate config to wrapped embedder."""
        return self._embedder.config

    def embed(
        self,
        text: str,
        memory_action: Optional[Literal["add", "search", "update"]] = None,
    ) -> list[float]:
        """Get embedding with caching.

        Args:
            text: The text to embed
            memory_action: The type of embedding action

        Returns:
            Embedding vector
        """
        if not self._enabled or not self.cache or not self.cache.available:
            return self._embedder.embed(text, memory_action)

        model = getattr(self.config, "model", "unknown")

        # Try cache first
        cached = self.cache.get_embedding(text, model)
        if cached is not None:
            self._hits += 1
            logger.debug(f"Embedding cache hit (total hits: {self._hits})")
            return cached

        # Cache miss - compute and store
        self._misses += 1
        embedding = self._embedder.embed(text, memory_action)

        # Cache the result
        self.cache.set_embedding(text, model, embedding)
        logger.debug(f"Embedding cache miss (total misses: {self._misses})")

        return embedding

    def get_stats(self) -> dict:
        """Get cache statistics.

        Returns:
            Dict with hits, misses, and hit rate
        """
        total = self._hits + self._misses
        hit_rate = self._hits / total if total > 0 else 0.0
        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": hit_rate,
            "enabled": self._enabled,
            "cache_available": self.cache.available if self.cache else False,
        }


def wrap_with_cache(
    embedder: EmbeddingBase,
    cache: Optional["RedisCache"] = None,
) -> CachedEmbedding:
    """Wrap an embedder with caching.

    Convenience function to wrap any embedder with cache support.

    Args:
        embedder: The embedder to wrap
        cache: Optional cache instance

    Returns:
        CachedEmbedding wrapper
    """
    return CachedEmbedding(embedder, cache)
