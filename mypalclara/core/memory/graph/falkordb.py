"""FalkorDB graph store — typed entities with temporal relationships.

Architecture:
- 1 LLM call on write (extract typed triples with temporal info)
- 0 LLM calls on read (vector KNN search)
- FalkorDB native vector indexing

Entity types: person, project, place, concept, event
Relationships carry temporal metadata (valid_from, valid_to) and
source_episode_id linking back to the conversation where the fact
was learned.
"""

import logging
import os
from typing import TYPE_CHECKING

try:
    import falkordb
except ImportError:
    falkordb = None

from mypalclara.core.memory.config import EMBEDDING_MODEL_DIMS
from mypalclara.core.memory.embeddings.base import BaseEmbedderConfig
from mypalclara.core.memory.embeddings.openai import OpenAIEmbedding
from mypalclara.core.memory.graph.tools import EXTRACT_TRIPLES_TOOL
from mypalclara.core.memory.graph.utils import EXTRACT_TRIPLES_PROMPT, sanitize_relationship_for_cypher
from mypalclara.core.memory.llm.unified import UnifiedLLM, UnifiedLLMConfig

if TYPE_CHECKING:
    from mypalclara.core.memory.cache.graph_cache import GraphCache

logger = logging.getLogger(__name__)


class MemoryGraph:
    """FalkorDB-based graph memory with typed entities and temporal relationships.

    Write path: 1 LLM call to extract typed triples with temporal info,
    then MERGE nodes (with type, aliases) and relationships (with temporal fields).

    Read path: Embed query, vector KNN search, traverse relationships.
    Returns entity types and temporal metadata.
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

        # Embedder (provider-driven, same as ClaraMemory)
        embedder_conf = config.embedder.config
        if isinstance(embedder_conf, dict):
            embedder_conf = BaseEmbedderConfig(**embedder_conf)
        embedder_provider = getattr(config.embedder, "provider", "huggingface")
        if embedder_provider == "openai":
            self.embedding_model = OpenAIEmbedding(embedder_conf)
        else:
            from mypalclara.core.memory.embeddings.huggingface import HuggingFaceEmbedding

            self.embedding_model = HuggingFaceEmbedding(embedder_conf)

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

        # Entity resolver (lazy)
        self._entity_resolver = None

        # Cache (lazy)
        self._cache: "GraphCache | None" = None
        self._cache_enabled = os.getenv("GRAPH_CACHE_ENABLED", "true").lower() == "true"

    @property
    def entity_resolver(self):
        """Lazy-loaded entity resolver for name resolution."""
        if self._entity_resolver is None:
            try:
                from mypalclara.core.memory.entity_resolver import EntityResolver

                self._entity_resolver = EntityResolver()
            except Exception:
                pass
        return self._entity_resolver

    def _resolve_name(self, name: str) -> str:
        """Resolve a name through the entity resolver.

        If the name looks like a platform ID (discord-123), try to resolve
        it to a human name. Otherwise return as-is.
        """
        if self.entity_resolver is None:
            return name
        resolved = self.entity_resolver.resolve(name)
        if resolved != name:
            return resolved.lower().replace(" ", "_")
        return name

    @property
    def cache(self) -> "GraphCache | None":
        if self._cache is None and self._cache_enabled:
            try:
                from mypalclara.core.memory.cache.graph_cache import GraphCache

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
            "CREATE INDEX FOR (n:__Entity__) ON (n.type)",
            f"CREATE VECTOR INDEX FOR (n:__Entity__) ON (n.embedding) OPTIONS {{dim: {EMBEDDING_MODEL_DIMS}, similarityFunction: 'cosine'}}",
        ]:
            try:
                self.graph.query(stmt)
            except Exception:
                pass  # Index already exists

    # ---- Write path ----

    def add(self, data: str, filters: dict, source_episode_id: str | None = None) -> dict:
        """Extract typed triples from text and add to graph.

        Args:
            data: Text to extract triples from.
            filters: Must contain user_id.
            source_episode_id: Optional episode ID linking to the source conversation.
        """
        triples = self._extract_triples(data, filters)
        added = self._merge_triples(triples, filters, source_episode_id)

        if self.cache and added:
            self.cache.invalidate_user(filters["user_id"])

        if added:
            logger.info("Graph write: user_id=%s added=%d", filters.get("user_id"), len(added))

        return {"deleted_entities": [], "added_entities": added}

    def _extract_triples(self, data: str, filters: dict) -> list[dict]:
        """Extract typed triples with temporal info via single LLM call."""
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
                    # Resolve platform IDs to human names
                    subj = self._resolve_name(subj.lower().replace(" ", "_"))
                    obj = self._resolve_name(obj.lower().replace(" ", "_"))
                    triples.append({
                        "source": subj,
                        "source_type": triple.get("subject_type", "concept"),
                        "relationship": sanitize_relationship_for_cypher(pred.lower().replace(" ", "_")),
                        "destination": obj,
                        "destination_type": triple.get("object_type", "concept"),
                        "temporal_note": triple.get("temporal_note", ""),
                    })

        logger.debug("Extracted %d triples", len(triples))
        return triples

    def _merge_triples(
        self, triples: list[dict], filters: dict, source_episode_id: str | None = None
    ) -> list:
        """MERGE typed nodes and temporal relationships for each triple."""
        user_id = filters["user_id"]
        results = []

        for triple in triples:
            source = triple["source"]
            destination = triple["destination"]
            relationship = triple["relationship"]
            source_type = triple.get("source_type", "concept")
            dest_type = triple.get("destination_type", "concept")
            temporal_note = triple.get("temporal_note", "")

            if not source or not destination or not relationship:
                continue

            source_embedding = self.embedding_model.embed(source)
            dest_embedding = self.embedding_model.embed(destination)

            cypher = f"""
            MERGE (s:__Entity__ {{name: $source_name, user_id: $user_id}})
            ON CREATE SET s.created_at = timestamp(), s.mentions = 1,
                          s.type = $source_type, s.first_seen = timestamp()
            ON MATCH SET s.mentions = coalesce(s.mentions, 0) + 1,
                         s.updated_at = timestamp(), s.last_seen = timestamp()
            SET s.embedding = vecf32($source_embedding),
                s.type = CASE WHEN s.type = 'concept' AND $source_type <> 'concept'
                              THEN $source_type ELSE coalesce(s.type, $source_type) END
            WITH s
            MERGE (d:__Entity__ {{name: $dest_name, user_id: $user_id}})
            ON CREATE SET d.created_at = timestamp(), d.mentions = 1,
                          d.type = $dest_type, d.first_seen = timestamp()
            ON MATCH SET d.mentions = coalesce(d.mentions, 0) + 1,
                         d.updated_at = timestamp(), d.last_seen = timestamp()
            SET d.embedding = vecf32($dest_embedding),
                d.type = CASE WHEN d.type = 'concept' AND $dest_type <> 'concept'
                              THEN $dest_type ELSE coalesce(d.type, $dest_type) END
            WITH s, d
            MERGE (s)-[r:{relationship}]->(d)
            ON CREATE SET r.created_at = timestamp(), r.mentions = 1,
                          r.valid_from = $temporal_note,
                          r.source_episode_id = $source_episode_id
            ON MATCH SET r.mentions = coalesce(r.mentions, 0) + 1,
                         r.updated_at = timestamp()
            RETURN s.name AS source, s.type AS source_type,
                   type(r) AS relationship,
                   d.name AS target, d.type AS target_type
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
                        "source_type": source_type,
                        "dest_type": dest_type,
                        "temporal_note": temporal_note,
                        "source_episode_id": source_episode_id or "",
                    },
                )
                results.extend(result)
            except Exception as e:
                logger.error("Failed to merge triple (%s, %s, %s): %s", source, relationship, destination, e)

        return results

    # ---- Read path (no LLM) ----

    def search(self, query: str, filters: dict, limit: int = 10) -> list:
        """Search for related entities using vector KNN search.

        Returns entity types and relationship metadata.
        """
        user_id = filters["user_id"]

        if self.cache:
            cached = self.cache.get_search_results(user_id, query, filters)
            if cached is not None:
                logger.info("Graph search cache hit: %d results", len(cached))
                return cached

        query_embedding = self.embedding_model.embed(query)

        cypher = """
        MATCH (node:__Entity__ {user_id: $user_id})
        WHERE node.embedding IS NOT NULL
        WITH node, (1 - vec.cosineDistance(node.embedding, vecf32($query_embedding))) AS similarity
        WHERE similarity >= 0.3
        CALL {
            WITH node
            MATCH (node)-[r]->(other:__Entity__ {user_id: $user_id})
            RETURN node.name AS source, node.type AS source_type,
                   type(r) AS relationship,
                   r.valid_from AS valid_from, r.valid_to AS valid_to,
                   other.name AS destination, other.type AS dest_type
            UNION
            WITH node
            MATCH (other:__Entity__ {user_id: $user_id})-[r]->(node)
            RETURN other.name AS source, other.type AS source_type,
                   type(r) AS relationship,
                   r.valid_from AS valid_from, r.valid_to AS valid_to,
                   node.name AS destination, node.type AS dest_type
        }
        WITH DISTINCT source, source_type, relationship, destination, dest_type,
                      valid_from, valid_to, similarity
        RETURN source, source_type, relationship, destination, dest_type,
               valid_from, valid_to, similarity AS score
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
                deduped.append({
                    "source": r["source"],
                    "source_type": r.get("source_type", ""),
                    "relationship": r["relationship"],
                    "destination": r["destination"],
                    "dest_type": r.get("dest_type", ""),
                    "valid_from": r.get("valid_from", ""),
                    "valid_to": r.get("valid_to", ""),
                })
            if len(deduped) >= limit:
                break

        if self.cache and deduped:
            self.cache.set_search_results(user_id, query, deduped, filters)

        logger.info("Graph search: %d results for user %s", len(deduped), user_id)
        return deduped

    # ---- Other operations ----

    def get_all(self, filters: dict, limit: int = 100) -> list:
        """Get all relationships for a user with type and temporal info."""
        user_id = filters["user_id"]

        if self.cache:
            cached = self.cache.get_all_relationships(user_id, filters.get("agent_id"))
            if cached is not None:
                return cached[:limit]

        results = self._query(
            """
            MATCH (n:__Entity__ {user_id: $user_id})-[r]->(m:__Entity__ {user_id: $user_id})
            RETURN n.name AS source, n.type AS source_type,
                   type(r) AS relationship,
                   r.valid_from AS valid_from, r.valid_to AS valid_to,
                   m.name AS target, m.type AS target_type
            LIMIT $limit
            """,
            params={"user_id": user_id, "limit": limit},
        )

        final = [
            {
                "source": r["source"],
                "source_type": r.get("source_type", ""),
                "relationship": r["relationship"],
                "destination": r["target"],
                "dest_type": r.get("target_type", ""),
                "valid_from": r.get("valid_from", ""),
                "valid_to": r.get("valid_to", ""),
            }
            for r in results
        ]

        if self.cache and final:
            self.cache.set_all_relationships(user_id, final, filters.get("agent_id"))

        return final

    def get_entities_by_type(self, user_id: str, entity_type: str, limit: int = 50) -> list[dict]:
        """Get all entities of a specific type for a user."""
        return self._query(
            """
            MATCH (n:__Entity__ {user_id: $user_id, type: $entity_type})
            RETURN n.name AS name, n.type AS type,
                   n.mentions AS mentions, n.first_seen AS first_seen,
                   n.last_seen AS last_seen
            ORDER BY n.mentions DESC
            LIMIT $limit
            """,
            params={"user_id": user_id, "entity_type": entity_type, "limit": limit},
        )

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
