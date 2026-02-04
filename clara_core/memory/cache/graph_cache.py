"""Redis-backed cache for Clara Graph Memory.

Caches:
- Graph search results (TTL: 5min) - vector similarity + BM25 reranked results
- Full graph snapshots (TTL: 10min) - get_all() results per user

NOT cached (too context-dependent):
- LLM entity extraction - results vary based on conversation context
- LLM relationship extraction - same reason
- LLM delete decisions - same reason

Graceful degradation: if Redis is unavailable, operations are no-ops.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clara_core.memory.cache.redis_cache import RedisCache

logger = logging.getLogger("clara.memory.cache.graph")

# TTL defaults (seconds)
GRAPH_SEARCH_TTL = 300  # 5 minutes
GRAPH_ALL_TTL = 600  # 10 minutes


class GraphCache:
    """Redis-backed cache for graph memory operations.

    Caches expensive graph queries while being conservative about
    LLM-dependent operations which are too context-sensitive to cache.
    """

    _instance: "GraphCache | None" = None

    def __init__(self, redis_cache: "RedisCache | None" = None):
        """Initialize graph cache.

        Args:
            redis_cache: RedisCache instance. If None, uses singleton.
        """
        self._redis_cache = redis_cache
        self._initialized = False

    @classmethod
    def get_instance(cls) -> "GraphCache":
        """Get singleton cache instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (for testing)."""
        cls._instance = None

    @property
    def cache(self) -> "RedisCache | None":
        """Lazy-load Redis cache singleton."""
        if not self._initialized:
            self._initialized = True
            if self._redis_cache is None:
                from clara_core.memory.cache.redis_cache import RedisCache
                self._redis_cache = RedisCache.get_instance()
        return self._redis_cache

    @property
    def available(self) -> bool:
        """Check if cache is available."""
        return self.cache is not None and self.cache.available

    # ---------- Graph Search Cache ----------

    def _search_key(self, user_id: str, query_hash: str) -> str:
        """Generate cache key for graph search results."""
        return f"clara:graph:search:{user_id}:{query_hash}"

    def _hash_query(self, query: str, filters: dict | None = None) -> str:
        """Hash a query and filters for cache key."""
        data = query
        if filters:
            # Include relevant filter fields in hash
            filter_data = {
                k: v for k, v in sorted(filters.items())
                if k in ("user_id", "agent_id", "run_id")
            }
            data += json.dumps(filter_data, sort_keys=True)
        return hashlib.sha256(data.encode()).hexdigest()[:16]

    def get_search_results(
        self,
        user_id: str,
        query: str,
        filters: dict | None = None,
    ) -> list[dict] | None:
        """Get cached graph search results.

        Args:
            user_id: User ID
            query: Search query
            filters: Optional search filters

        Returns:
            List of result dicts or None if not cached
        """
        if not self.available:
            return None

        try:
            query_hash = self._hash_query(query, filters)
            key = self._search_key(user_id, query_hash)
            data = self.cache.client.get(key)
            if data:
                logger.debug(f"Graph search cache hit for user {user_id}")
                return json.loads(data)
        except Exception as e:
            logger.debug(f"Graph cache get error: {e}")
        return None

    def set_search_results(
        self,
        user_id: str,
        query: str,
        results: list[dict],
        filters: dict | None = None,
        ttl: int = GRAPH_SEARCH_TTL,
    ) -> bool:
        """Cache graph search results.

        Args:
            user_id: User ID
            query: Search query
            results: Search results to cache
            filters: Optional search filters
            ttl: Time to live in seconds

        Returns:
            True if cached successfully
        """
        if not self.available:
            return False

        try:
            query_hash = self._hash_query(query, filters)
            key = self._search_key(user_id, query_hash)
            self.cache.client.setex(key, ttl, json.dumps(results))
            logger.debug(f"Graph search cached for user {user_id}")
            return True
        except Exception as e:
            logger.debug(f"Graph cache set error: {e}")
            return False

    # ---------- Full Graph Cache ----------

    def _all_key(self, user_id: str, agent_id: str | None = None) -> str:
        """Generate cache key for full graph snapshot."""
        if agent_id:
            return f"clara:graph:all:{user_id}:{agent_id}"
        return f"clara:graph:all:{user_id}"

    def get_all_relationships(
        self,
        user_id: str,
        agent_id: str | None = None,
    ) -> list[dict] | None:
        """Get cached full graph snapshot.

        Args:
            user_id: User ID
            agent_id: Optional agent ID

        Returns:
            List of relationship dicts or None if not cached
        """
        if not self.available:
            return None

        try:
            key = self._all_key(user_id, agent_id)
            data = self.cache.client.get(key)
            if data:
                logger.debug(f"Graph all cache hit for user {user_id}")
                return json.loads(data)
        except Exception as e:
            logger.debug(f"Graph cache get error: {e}")
        return None

    def set_all_relationships(
        self,
        user_id: str,
        relationships: list[dict],
        agent_id: str | None = None,
        ttl: int = GRAPH_ALL_TTL,
    ) -> bool:
        """Cache full graph snapshot.

        Args:
            user_id: User ID
            relationships: All relationships for user
            agent_id: Optional agent ID
            ttl: Time to live in seconds

        Returns:
            True if cached successfully
        """
        if not self.available:
            return False

        try:
            key = self._all_key(user_id, agent_id)
            self.cache.client.setex(key, ttl, json.dumps(relationships))
            logger.debug(f"Graph all cached for user {user_id}")
            return True
        except Exception as e:
            logger.debug(f"Graph cache set error: {e}")
            return False

    # ---------- Cache Invalidation ----------

    def invalidate_user(self, user_id: str) -> int:
        """Invalidate all graph cache for a user.

        Call this when the graph is modified (add, delete).

        Args:
            user_id: User ID

        Returns:
            Number of keys deleted
        """
        if not self.available:
            return 0

        try:
            patterns = [
                f"clara:graph:search:{user_id}:*",
                f"clara:graph:all:{user_id}*",
            ]
            count = 0
            for pattern in patterns:
                keys = list(self.cache.client.scan_iter(match=pattern, count=100))
                if keys:
                    count += self.cache.client.delete(*keys)
            if count > 0:
                logger.debug(f"Invalidated {count} graph cache keys for user {user_id}")
            return count
        except Exception as e:
            logger.debug(f"Graph cache invalidation error: {e}")
            return 0

    def invalidate_search(self, user_id: str) -> int:
        """Invalidate only search cache for a user.

        Args:
            user_id: User ID

        Returns:
            Number of keys deleted
        """
        if not self.available:
            return 0

        try:
            pattern = f"clara:graph:search:{user_id}:*"
            keys = list(self.cache.client.scan_iter(match=pattern, count=100))
            if keys:
                count = self.cache.client.delete(*keys)
                logger.debug(f"Invalidated {count} graph search cache keys for user {user_id}")
                return count
            return 0
        except Exception as e:
            logger.debug(f"Graph cache invalidation error: {e}")
            return 0
