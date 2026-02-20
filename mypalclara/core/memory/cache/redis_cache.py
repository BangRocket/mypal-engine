"""Redis-backed cache for Clara Memory System.

Provides caching for:
- Embeddings (TTL: 24h) - rarely change, expensive to compute
- Search results (TTL: 5min) - balance freshness vs speed
- Key memories (TTL: 10min) - change less often

Graceful degradation: if Redis is unavailable, operations are no-ops.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redis import Redis

logger = logging.getLogger("clara.memory.cache")

# TTL defaults (seconds)
EMBEDDING_TTL = 86400  # 24 hours
SEARCH_RESULTS_TTL = 300  # 5 minutes
KEY_MEMORIES_TTL = 600  # 10 minutes


def _get_redis_client() -> "Redis | None":
    """Get Redis client from URL, or None if unavailable."""
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        return None

    try:
        from redis import Redis

        client = Redis.from_url(redis_url, decode_responses=True)
        # Test connection
        client.ping()
        return client
    except Exception as e:
        logger.warning(f"Redis unavailable: {e}")
        return None


class RedisCache:
    """Redis-backed cache with graceful degradation.

    If Redis is unavailable, all operations silently return None/0.
    """

    _instance: "RedisCache | None" = None

    def __init__(self, client: "Redis | None" = None):
        """Initialize cache.

        Args:
            client: Redis client instance. If None, attempts to connect via REDIS_URL.
        """
        self._client = client
        self._initialized = False

    @classmethod
    def get_instance(cls) -> "RedisCache":
        """Get singleton cache instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (for testing)."""
        if cls._instance and cls._instance._client:
            try:
                cls._instance._client.close()
            except Exception:
                pass
        cls._instance = None

    @property
    def client(self) -> "Redis | None":
        """Lazy-load Redis client."""
        if not self._initialized:
            self._initialized = True
            if self._client is None:
                self._client = _get_redis_client()
            if self._client:
                logger.info("Redis cache connected")
        return self._client

    @property
    def available(self) -> bool:
        """Check if Redis is available."""
        return self.client is not None

    # ---------- Embedding Cache ----------

    def _embedding_key(self, text: str, model: str) -> str:
        """Generate cache key for embedding."""
        text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
        return f"clara:emb:{model}:{text_hash}"

    def get_embedding(self, text: str, model: str) -> list[float] | None:
        """Get cached embedding.

        Args:
            text: The text that was embedded
            model: Embedding model name

        Returns:
            Embedding vector or None if not cached
        """
        if not self.client:
            return None

        try:
            key = self._embedding_key(text, model)
            data = self.client.get(key)
            if data:
                return json.loads(data)
        except Exception as e:
            logger.debug(f"Cache get error: {e}")
        return None

    def set_embedding(
        self,
        text: str,
        model: str,
        embedding: list[float],
        ttl: int = EMBEDDING_TTL,
    ) -> bool:
        """Cache an embedding.

        Args:
            text: The text that was embedded
            model: Embedding model name
            embedding: The embedding vector
            ttl: Time to live in seconds

        Returns:
            True if cached successfully
        """
        if not self.client:
            return False

        try:
            key = self._embedding_key(text, model)
            self.client.setex(key, ttl, json.dumps(embedding))
            return True
        except Exception as e:
            logger.debug(f"Cache set error: {e}")
            return False

    # ---------- Search Results Cache ----------

    def _search_key(
        self,
        user_id: str,
        query_hash: str,
        search_type: str,
    ) -> str:
        """Generate cache key for search results."""
        return f"clara:search:{user_id}:{search_type}:{query_hash}"

    def _hash_query(self, query: str, filters: dict | None = None) -> str:
        """Hash a query and filters for cache key."""
        data = query
        if filters:
            data += json.dumps(filters, sort_keys=True)
        return hashlib.sha256(data.encode()).hexdigest()[:16]

    def get_search_results(
        self,
        user_id: str,
        query: str,
        search_type: str = "user",
        filters: dict | None = None,
    ) -> list[dict] | None:
        """Get cached search results.

        Args:
            user_id: User ID
            query: Search query
            search_type: Type of search (user, project, participant)
            filters: Optional search filters

        Returns:
            List of result dicts or None if not cached
        """
        if not self.client:
            return None

        try:
            query_hash = self._hash_query(query, filters)
            key = self._search_key(user_id, query_hash, search_type)
            data = self.client.get(key)
            if data:
                return json.loads(data)
        except Exception as e:
            logger.debug(f"Cache get error: {e}")
        return None

    def set_search_results(
        self,
        user_id: str,
        query: str,
        results: list[dict],
        search_type: str = "user",
        filters: dict | None = None,
        ttl: int = SEARCH_RESULTS_TTL,
    ) -> bool:
        """Cache search results.

        Args:
            user_id: User ID
            query: Search query
            results: Search results to cache
            search_type: Type of search
            filters: Optional search filters
            ttl: Time to live in seconds

        Returns:
            True if cached successfully
        """
        if not self.client:
            return False

        try:
            query_hash = self._hash_query(query, filters)
            key = self._search_key(user_id, query_hash, search_type)
            self.client.setex(key, ttl, json.dumps(results))
            return True
        except Exception as e:
            logger.debug(f"Cache set error: {e}")
            return False

    # ---------- Key Memories Cache ----------

    def _key_memories_key(self, user_id: str, agent_id: str) -> str:
        """Generate cache key for key memories."""
        return f"clara:keymem:{user_id}:{agent_id}"

    def get_key_memories(
        self,
        user_id: str,
        agent_id: str,
    ) -> list[dict] | None:
        """Get cached key memories.

        Args:
            user_id: User ID
            agent_id: Agent ID

        Returns:
            List of memory dicts or None if not cached
        """
        if not self.client:
            return None

        try:
            key = self._key_memories_key(user_id, agent_id)
            data = self.client.get(key)
            if data:
                return json.loads(data)
        except Exception as e:
            logger.debug(f"Cache get error: {e}")
        return None

    def set_key_memories(
        self,
        user_id: str,
        agent_id: str,
        memories: list[dict],
        ttl: int = KEY_MEMORIES_TTL,
    ) -> bool:
        """Cache key memories.

        Args:
            user_id: User ID
            agent_id: Agent ID
            memories: Key memories to cache
            ttl: Time to live in seconds

        Returns:
            True if cached successfully
        """
        if not self.client:
            return False

        try:
            key = self._key_memories_key(user_id, agent_id)
            self.client.setex(key, ttl, json.dumps(memories))
            return True
        except Exception as e:
            logger.debug(f"Cache set error: {e}")
            return False

    # ---------- Cache Invalidation ----------

    def invalidate_user_cache(self, user_id: str) -> int:
        """Invalidate all cached data for a user.

        Args:
            user_id: User ID

        Returns:
            Number of keys deleted
        """
        if not self.client:
            return 0

        try:
            # Find all keys for this user
            patterns = [
                f"clara:search:{user_id}:*",
                f"clara:keymem:{user_id}:*",
            ]
            count = 0
            for pattern in patterns:
                keys = list(self.client.scan_iter(match=pattern, count=100))
                if keys:
                    count += self.client.delete(*keys)
            return count
        except Exception as e:
            logger.debug(f"Cache invalidation error: {e}")
            return 0

    def invalidate_search_cache(self, user_id: str) -> int:
        """Invalidate only search cache for a user (not key memories).

        Args:
            user_id: User ID

        Returns:
            Number of keys deleted
        """
        if not self.client:
            return 0

        try:
            pattern = f"clara:search:{user_id}:*"
            keys = list(self.client.scan_iter(match=pattern, count=100))
            if keys:
                return self.client.delete(*keys)
            return 0
        except Exception as e:
            logger.debug(f"Cache invalidation error: {e}")
            return 0

    def invalidate_key_memories(self, user_id: str, agent_id: str) -> bool:
        """Invalidate key memories cache.

        Args:
            user_id: User ID
            agent_id: Agent ID

        Returns:
            True if deleted successfully
        """
        if not self.client:
            return False

        try:
            key = self._key_memories_key(user_id, agent_id)
            self.client.delete(key)
            return True
        except Exception as e:
            logger.debug(f"Cache invalidation error: {e}")
            return False

    # ---------- Stats ----------

    def get_stats(self) -> dict:
        """Get cache statistics.

        Returns:
            Dict with hit/miss stats if available
        """
        if not self.client:
            return {"available": False}

        try:
            info = self.client.info("stats")
            return {
                "available": True,
                "hits": info.get("keyspace_hits", 0),
                "misses": info.get("keyspace_misses", 0),
            }
        except Exception as e:
            return {"available": True, "error": str(e)}
