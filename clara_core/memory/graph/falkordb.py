"""FalkorDB graph store implementation for Clara Memory System.

Simplified graph memory using:
- 1 LLM call on write (extract triples)
- 0 LLM calls on read (vector KNN search)
- FalkorDB native vector indexing
"""

import logging
import os
from typing import TYPE_CHECKING

try:
    import falkordb
except ImportError:
    falkordb = None

from clara_core.memory.embeddings.base import BaseEmbedderConfig
from clara_core.memory.embeddings.openai import OpenAIEmbedding
from clara_core.memory.graph.tools import EXTRACT_TRIPLES_TOOL
from clara_core.memory.graph.utils import EXTRACT_TRIPLES_PROMPT, sanitize_relationship_for_cypher
from clara_core.memory.llm.unified import UnifiedLLM, UnifiedLLMConfig

if TYPE_CHECKING:
    from clara_core.memory.cache.graph_cache import GraphCache

logger = logging.getLogger(__name__)

EMBEDDING_DIMS = 1536


class MemoryGraph:
    """FalkorDB-based graph memory for entity and relationship tracking.

    Write path: 1 LLM call to extract (subject, predicate, object) triples,
    then MERGE nodes with embeddings and MERGE relationships.

    Read path: Embed query, use vec.cosineDistance for similarity search,
    traverse relationships from matched nodes.
    """

    def __init__(self, config):
        if falkordb is None:
            raise ImportError("falkordb is not installed. Please install it using pip install falkordb")

        self.config = config

        # FalkorDB connection
        graph_config = config.graph_store.config
        self.client = falkordb.FalkorDB(
            host=graph_config.get("host", "localhost"),
            port=graph_config.get("port", 6379),
            password=graph_config.get("password"),
        )
        self.graph = self.client.select_graph(graph_config.get("graph_name", "clara_memory"))

        # Embedder
        embedder_conf = config.embedder.config
        if isinstance(embedder_conf, dict):
            embedder_conf = BaseEmbedderConfig(**embedder_conf)
        self.embedding_model = OpenAIEmbedding(embedder_conf)

        # Indexes
        self._create_indexes()

        # LLM for triple extraction (write path only)
        llm_config = None
        if config.graph_store and hasattr(config.graph_store, "llm") and config.graph_store.llm:
            llm_config = config.graph_store.llm.config
        elif config.llm:
            llm_config = config.llm.config
        if isinstance(llm_config, dict):
            llm_config = UnifiedLLMConfig(**llm_config)
        self.llm = UnifiedLLM(llm_config)

        # Cache (lazy)
        self._cache: "GraphCache | None" = None
        self._cache_enabled = os.getenv("GRAPH_CACHE_ENABLED", "true").lower() == "true"

    @property
    def cache(self) -> "GraphCache | None":
        if self._cache is None and self._cache_enabled:
            try:
                from clara_core.memory.cache.graph_cache import GraphCache

                self._cache = GraphCache.get_instance()
            except Exception as e:
                logger.debug(f"Graph cache unavailable: {e}")
        return self._cache

    def _query(self, cypher: str, params: dict | None = None) -> list[dict]:
        """Execute Cypher and return list[dict] with column names as keys."""
        result = self.graph.query(cypher, params=params or {})
        if not result.result_set:
            return []
        column_names = [col[1] if isinstance(col, (list, tuple)) else col for col in result.header]
        return [dict(zip(column_names, row)) for row in result.result_set]

    def _create_indexes(self):
        """Create indexes for efficient querying."""
        for stmt in [
            "CREATE INDEX FOR (n:__Entity__) ON (n.user_id)",
            "CREATE INDEX FOR (n:__Entity__) ON (n.name)",
            f"CREATE VECTOR INDEX FOR (n:__Entity__) ON (n.embedding) OPTIONS {{dim: {EMBEDDING_DIMS}, similarityFunction: 'cosine'}}",
        ]:
            try:
                self.graph.query(stmt)
            except Exception:
                pass  # Index already exists

    # ---- Write path ----

    def add(self, data: str, filters: dict) -> dict:
        """Extract triples from text and add to graph.

        Makes exactly 1 LLM call to extract triples, then MERGEs nodes
        and relationships into FalkorDB.
        """
        triples = self._extract_triples(data, filters)
        added = self._merge_triples(triples, filters)

        if self.cache and added:
            self.cache.invalidate_user(filters["user_id"])

        if added:
            logger.info("Graph write: user_id=%s added=%d", filters.get("user_id"), len(added))

        return {"deleted_entities": [], "added_entities": added}

    def _extract_triples(self, data: str, filters: dict) -> list[dict]:
        """Extract (subject, predicate, object) triples via single LLM call."""
        user_id = filters.get("user_id", "user")
        prompt = EXTRACT_TRIPLES_PROMPT.replace("USER_ID", user_id)

        response = self.llm.generate_response(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": data},
            ],
            tools=[EXTRACT_TRIPLES_TOOL],
            tool_choice="required",
        )

        if not response or not isinstance(response, dict):
            logger.debug("LLM did not return tool call for triple extraction")
            return []

        triples = []
        for tool_call in response.get("tool_calls", []):
            if tool_call.get("name") != "extract_triples":
                continue
            for triple in tool_call.get("arguments", {}).get("triples", []):
                subj = triple.get("subject", "").strip()
                pred = triple.get("predicate", "").strip()
                obj = triple.get("object", "").strip()
                if subj and pred and obj:
                    triples.append(
                        {
                            "source": subj.lower().replace(" ", "_"),
                            "relationship": sanitize_relationship_for_cypher(pred.lower().replace(" ", "_")),
                            "destination": obj.lower().replace(" ", "_"),
                        }
                    )

        logger.debug("Extracted %d triples", len(triples))
        return triples

    def _merge_triples(self, triples: list[dict], filters: dict) -> list:
        """MERGE nodes and relationships for each triple."""
        user_id = filters["user_id"]
        results = []

        for triple in triples:
            source = triple["source"]
            destination = triple["destination"]
            relationship = triple["relationship"]

            if not source or not destination or not relationship:
                continue

            source_embedding = self.embedding_model.embed(source)
            dest_embedding = self.embedding_model.embed(destination)

            cypher = f"""
            MERGE (s:__Entity__ {{name: $source_name, user_id: $user_id}})
            ON CREATE SET s.created_at = timestamp(), s.mentions = 1
            ON MATCH SET s.mentions = coalesce(s.mentions, 0) + 1, s.updated_at = timestamp()
            SET s.embedding = vecf32($source_embedding)
            WITH s
            MERGE (d:__Entity__ {{name: $dest_name, user_id: $user_id}})
            ON CREATE SET d.created_at = timestamp(), d.mentions = 1
            ON MATCH SET d.mentions = coalesce(d.mentions, 0) + 1, d.updated_at = timestamp()
            SET d.embedding = vecf32($dest_embedding)
            WITH s, d
            MERGE (s)-[r:{relationship}]->(d)
            ON CREATE SET r.created_at = timestamp(), r.mentions = 1
            ON MATCH SET r.mentions = coalesce(r.mentions, 0) + 1, r.updated_at = timestamp()
            RETURN s.name AS source, type(r) AS relationship, d.name AS target
            """

            try:
                result = self._query(
                    cypher,
                    params={
                        "source_name": source,
                        "dest_name": destination,
                        "user_id": user_id,
                        "source_embedding": source_embedding,
                        "dest_embedding": dest_embedding,
                    },
                )
                results.extend(result)
            except Exception as e:
                logger.error("Failed to merge triple (%s, %s, %s): %s", source, relationship, destination, e)

        return results

    # ---- Read path (no LLM) ----

    def search(self, query: str, filters: dict, limit: int = 10) -> list:
        """Search for related entities using vector KNN search.

        No LLM calls -- embeds the query, finds similar entity nodes via
        FalkorDB's native vector index, then traverses their relationships.
        """
        user_id = filters["user_id"]

        if self.cache:
            cached = self.cache.get_search_results(user_id, query, filters)
            if cached is not None:
                logger.info("Graph search cache hit: %d results", len(cached))
                return cached

        query_embedding = self.embedding_model.embed(query)

        # Vector similarity search using vec.cosineDistance (proven pattern from main).
        # cosineDistance returns [0, 2] where 0 = identical; convert to similarity.
        # The vector index accelerates the distance calculation automatically.
        cypher = """
        MATCH (node:__Entity__ {user_id: $user_id})
        WHERE node.embedding IS NOT NULL
        WITH node, (1 - vec.cosineDistance(node.embedding, vecf32($query_embedding))) AS score
        WHERE score >= 0.3
        CALL {
            WITH node, score
            MATCH (node)-[r]->(other:__Entity__ {user_id: $user_id})
            RETURN node.name AS source, type(r) AS relationship, other.name AS destination, score
            UNION
            WITH node, score
            MATCH (other:__Entity__ {user_id: $user_id})-[r]->(node)
            RETURN other.name AS source, type(r) AS relationship, node.name AS destination, score
        }
        WITH DISTINCT source, relationship, destination, score
        RETURN source, relationship, destination, score
        ORDER BY score DESC
        LIMIT $limit
        """

        results = self._query(
            cypher,
            params={
                "query_embedding": query_embedding,
                "user_id": user_id,
                "limit": limit,
            },
        )

        # Deduplicate and limit
        seen = set()
        deduped = []
        for r in results:
            key = (r["source"], r["relationship"], r["destination"])
            if key not in seen:
                seen.add(key)
                deduped.append(
                    {
                        "source": r["source"],
                        "relationship": r["relationship"],
                        "destination": r["destination"],
                    }
                )
            if len(deduped) >= limit:
                break

        if self.cache and deduped:
            self.cache.set_search_results(user_id, query, deduped, filters)

        logger.info("Graph search: %d results for user %s", len(deduped), user_id)
        return deduped

    # ---- Other operations ----

    def get_all(self, filters: dict, limit: int = 100) -> list:
        """Get all relationships for a user."""
        user_id = filters["user_id"]

        if self.cache:
            cached = self.cache.get_all_relationships(user_id, filters.get("agent_id"))
            if cached is not None:
                return cached[:limit]

        results = self._query(
            """
            MATCH (n:__Entity__ {user_id: $user_id})-[r]->(m:__Entity__ {user_id: $user_id})
            RETURN n.name AS source, type(r) AS relationship, m.name AS target
            LIMIT $limit
            """,
            params={"user_id": user_id, "limit": limit},
        )

        final = [{"source": r["source"], "relationship": r["relationship"], "target": r["target"]} for r in results]

        if self.cache and final:
            self.cache.set_all_relationships(user_id, final, filters.get("agent_id"))

        return final

    def delete_all(self, filters: dict):
        """Delete all graph data for a user."""
        self.graph.query(
            "MATCH (n:__Entity__ {user_id: $user_id}) DETACH DELETE n",
            params={"user_id": filters["user_id"]},
        )
        if self.cache:
            self.cache.invalidate_user(filters["user_id"])

    def reset(self):
        """Delete everything in the graph."""
        logger.warning("Clearing entire graph")
        self.graph.query("MATCH (n) DETACH DELETE n")
