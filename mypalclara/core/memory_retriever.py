"""Memory retrieval for Clara platform.

Provides the MemoryRetriever class that handles:
- Fetching key, user, and project memories from Rook
- Redis caching for memory search results
- Parallel memory fetches with ThreadPoolExecutor
- FSRS-based re-ranking via MemoryDynamicsManager
- Graph relation retrieval and deduplication
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from clara_core.memory_manager import (
    MAX_GRAPH_RELATIONS,
    MAX_KEY_MEMORIES,
    MAX_MEMORIES_PER_TYPE,
    MAX_SEARCH_QUERY_CHARS,
)
from config.logging import get_logger

logger = get_logger("rook")
memory_logger = get_logger("memory")


class MemoryRetriever:
    """Retrieves and ranks memories from Rook for prompt building.

    Handles parallel fetching of key, user, and project memories,
    Redis caching, FSRS re-ranking, and graph relation retrieval.
    """

    def __init__(
        self,
        agent_id: str,
        on_memory_event: Callable[..., Any] | None = None,
        dynamics_manager: Any | None = None,
    ) -> None:
        self.agent_id = agent_id
        self._on_memory_event = on_memory_event
        self._dynamics_manager = dynamics_manager

    def _get_cache(self):
        """Get Redis cache instance (lazy-loaded)."""
        try:
            from clara_core.memory.cache.redis_cache import RedisCache

            return RedisCache.get_instance()
        except Exception:
            return None

    def _invalidate_memory_cache(self, user_id: str) -> None:
        """Invalidate memory cache for a user after memories change.

        Invalidates search results cache but NOT embeddings (those rarely change).

        Args:
            user_id: User whose cache to invalidate
        """
        cache = self._get_cache()
        if cache and cache.available:
            count = cache.invalidate_search_cache(user_id)
            if count:
                memory_logger.debug(f"Invalidated {count} cached searches for {user_id}")

    def fetch_mem0_context(
        self,
        user_id: str,
        project_id: str,
        user_message: str,
        participants: list[dict] | None = None,
        is_dm: bool = False,
    ) -> tuple[list[str], list[str], list[dict]]:
        """Fetch relevant memories from mem0 using parallel fetches.

        Uses entity-scoped memory with user_id + agent_id for proper isolation.
        Performance optimized with:
        - Parallel fetches via ThreadPoolExecutor
        - Redis caching for key memories and search results
        - Batched FSRS lookups

        Memory retrieval:
        1. Parallel fetch: key memories, user search, project search
        2. Fetch graph relations (entity relationships)
        3. Re-rank with batched FSRS
        4. Combine with key memories first, then relevant (deduplicated)

        Args:
            user_id: The user making the request
            project_id: Project context
            user_message: The message to search for relevant memories
            participants: List of {"id": str, "name": str} for conversation members
            is_dm: Whether this is a DM conversation (changes retrieval priority)

        Returns:
            Tuple of (user_memories, project_memories, graph_relations)
            graph_relations is a list of dicts with keys: source, relationship, destination
        """
        from clara_core.memory import ROOK

        if ROOK is None:
            return [], [], []

        # Truncate search query if too long
        search_query = user_message
        if len(search_query) > MAX_SEARCH_QUERY_CHARS:
            search_query = search_query[-MAX_SEARCH_QUERY_CHARS:]
            logger.debug(f"Truncated search query to {MAX_SEARCH_QUERY_CHARS} chars")

        # Get cache for potential cache hits
        cache = self._get_cache()

        # Define fetch functions for parallel execution
        def fetch_key_memories():
            """Fetch key memories (with caching)."""
            # Try cache first
            if cache and cache.available:
                cached = cache.get_key_memories(user_id, self.agent_id)
                if cached is not None:
                    logger.debug("Key memories cache hit")
                    return {"results": cached, "_cached": True}

            result = ROOK.get_all(
                user_id=user_id,
                agent_id=self.agent_id,
                filters={"is_key": "true"},
                limit=MAX_KEY_MEMORIES,
            )

            # Cache the results
            if cache and cache.available and result.get("results"):
                cache.set_key_memories(user_id, self.agent_id, result["results"])

            return result

        def fetch_user_memories():
            """Fetch user memories via semantic search (with caching)."""
            # Try cache first
            if cache and cache.available:
                cached = cache.get_search_results(user_id, search_query, "user")
                if cached is not None:
                    logger.debug("User search cache hit")
                    return {"results": cached, "_cached": True}

            result = ROOK.search(
                search_query,
                user_id=user_id,
                agent_id=self.agent_id,
            )

            # Cache the results
            if cache and cache.available and result.get("results"):
                cache.set_search_results(user_id, search_query, result["results"], "user")

            return result

        def fetch_project_memories():
            """Fetch project memories via semantic search (with caching)."""
            filters = {"project_id": project_id}

            # Try cache first
            if cache and cache.available:
                cached = cache.get_search_results(user_id, search_query, "project", filters)
                if cached is not None:
                    logger.debug("Project search cache hit")
                    return {"results": cached, "_cached": True}

            result = ROOK.search(
                search_query,
                user_id=user_id,
                agent_id=self.agent_id,
                filters=filters,
            )

            # Cache the results
            if cache and cache.available and result.get("results"):
                cache.set_search_results(user_id, search_query, result["results"], "project", filters)

            return result

        # Execute parallel fetches
        key_res = {"results": []}
        user_res = {"results": []}
        proj_res = {"results": []}

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(fetch_key_memories): "key",
                executor.submit(fetch_user_memories): "user",
                executor.submit(fetch_project_memories): "project",
            }

            for future in as_completed(futures):
                fetch_type = futures[future]
                try:
                    result = future.result()
                    if fetch_type == "key":
                        key_res = result
                    elif fetch_type == "user":
                        user_res = result
                    elif fetch_type == "project":
                        proj_res = result
                except Exception as e:
                    logger.error(f"Error in {fetch_type} memory fetch: {e}", exc_info=True)

        # Build key memories list
        key_mems: list[str] = []
        for r in key_res.get("results", []):
            key_mems.append(f"[KEY] {r['memory']}")

        # Collect graph relations from search results
        graph_relations: list[dict] = []
        if user_res.get("relations"):
            graph_relations.extend(user_res["relations"])
        if proj_res.get("relations"):
            existing = {(r.get("source"), r.get("relationship"), r.get("destination")) for r in graph_relations}
            for rel in proj_res["relations"]:
                key = (rel.get("source"), rel.get("relationship"), rel.get("destination"))
                if key not in existing:
                    graph_relations.append(rel)
                    existing.add(key)

        # Combine all results for batched FSRS ranking
        all_user_results = user_res.get("results", [])
        all_proj_results = proj_res.get("results", [])

        # Re-rank results using batched FSRS retrievability weighting
        user_results = self._dynamics_manager.rank_results_with_fsrs_batch(all_user_results, user_id)
        proj_results = self._dynamics_manager.rank_results_with_fsrs_batch(all_proj_results, user_id)

        # Track memory IDs for later promotion
        retrieved_ids: list[str] = []
        for r in user_results:
            if r.get("id"):
                retrieved_ids.append(r["id"])
        for r in proj_results:
            if r.get("id") and r["id"] not in retrieved_ids:
                retrieved_ids.append(r["id"])
        self._dynamics_manager.set_last_retrieved_memory_ids(user_id, retrieved_ids)

        # Build user memories: key first, then relevant (deduplicated)
        user_mems = list(key_mems)
        key_texts = {m.replace("[KEY] ", "") for m in key_mems}
        seen_raw_texts = set(key_texts)  # Track raw texts for dedup across all sources

        for r in user_results:
            mem = r["memory"]
            if mem not in key_texts:
                seen_raw_texts.add(mem)
                category = r.get("_category")
                if category:
                    user_mems.append(f"[{category}] {mem}")
                else:
                    user_mems.append(mem)

        proj_mems = [r["memory"] for r in proj_results]

        # Fetch participant memories in parallel if we have participants
        if participants:
            participant_futures = {}
            with ThreadPoolExecutor(max_workers=min(len(participants), 3)) as executor:
                for p in participants:
                    p_id = p.get("id")
                    p_name = p.get("name", p_id)
                    if not p_id or p_id == user_id:
                        continue

                    def fetch_participant(name=p_name, query=search_query):
                        return ROOK.search(
                            f"{name} {query[:500]}",
                            user_id=user_id,
                            agent_id=self.agent_id,
                        )

                    participant_futures[executor.submit(fetch_participant)] = p_name

                for future in as_completed(participant_futures):
                    p_name = participant_futures[future]
                    try:
                        p_search = future.result()
                        p_results = self._dynamics_manager.rank_results_with_fsrs_batch(
                            p_search.get("results", []), user_id
                        )
                        for r in p_results:
                            mem = r["memory"]
                            if mem not in seen_raw_texts:
                                seen_raw_texts.add(mem)
                                labeled_mem = f"[About {p_name}]: {mem}"
                                user_mems.append(labeled_mem)
                                if r.get("id") and r["id"] not in retrieved_ids:
                                    retrieved_ids.append(r["id"])
                    except Exception as e:
                        logger.warning(f"Error searching participant {p_name}: {e}")

        # Update tracked IDs after participant search
        self._dynamics_manager.set_last_retrieved_memory_ids(user_id, retrieved_ids)

        # Extract contact-related memories with source info
        for r in user_results:
            metadata = r.get("metadata", {})
            if metadata.get("contact_id"):
                raw_mem = r["memory"]
                if raw_mem not in seen_raw_texts:
                    seen_raw_texts.add(raw_mem)
                    contact_name = metadata.get("contact_name", metadata.get("contact_id"))
                    user_mems.append(f"[About {contact_name}]: {raw_mem}")

        # Limit non-key memories to reduce token usage
        num_key = len(key_mems)
        if len(user_mems) > num_key + MAX_MEMORIES_PER_TYPE:
            user_mems = user_mems[: num_key + MAX_MEMORIES_PER_TYPE]
        if len(proj_mems) > MAX_MEMORIES_PER_TYPE:
            proj_mems = proj_mems[:MAX_MEMORIES_PER_TYPE]
        if len(graph_relations) > MAX_GRAPH_RELATIONS:
            graph_relations = graph_relations[:MAX_GRAPH_RELATIONS]

        if user_mems or proj_mems or graph_relations:
            logger.info(
                f"Found {len(key_mems)} key, "
                f"{len(user_mems) - len(key_mems)} user, "
                f"{len(proj_mems)} project memories, "
                f"{len(graph_relations)} graph relations"
            )
            self._send_memory_embed(
                user_id=user_id,
                key_count=len(key_mems),
                user_count=len(user_mems) - len(key_mems),
                proj_count=len(proj_mems),
                graph_count=len(graph_relations),
                sample_memories=user_mems[:3] if user_mems else [],
            )
        return user_mems, proj_mems, graph_relations

    def _send_memory_embed(
        self,
        user_id: str,
        key_count: int,
        user_count: int,
        proj_count: int,
        sample_memories: list[str],
        graph_count: int = 0,
    ) -> None:
        """Notify about memory retrieval (if callback registered)."""
        if not self._on_memory_event:
            return

        total = key_count + user_count + proj_count + graph_count
        if total == 0:
            return

        # Format samples for notification
        samples = []
        for mem in sample_memories[:3]:
            clean_mem = mem.replace("[KEY] ", "")
            if len(clean_mem) > 60:
                clean_mem = clean_mem[:57] + "..."
            samples.append(clean_mem)

        self._on_memory_event(
            "memory_retrieved",
            {
                "user_id": user_id,
                "key_count": key_count,
                "user_count": user_count,
                "proj_count": proj_count,
                "graph_count": graph_count,
                "total": total,
                "samples": samples,
            },
        )
