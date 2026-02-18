"""Neo4j graph store implementation for Clara Memory System."""

import logging
import os
from typing import TYPE_CHECKING

try:
    from langchain_neo4j import Neo4jGraph
except ImportError:
    raise ImportError("langchain_neo4j is not installed. Please install it using pip install langchain-neo4j")

try:
    from rank_bm25 import BM25Okapi
except ImportError:
    raise ImportError("rank_bm25 is not installed. Please install it using pip install rank-bm25")

from clara_core.memory.embeddings.factory import EmbedderFactory
from clara_core.memory.graph.tools import (
    DELETE_MEMORY_TOOL_GRAPH,
    EXTRACT_ENTITIES_TOOL,
    RELATIONS_TOOL,
)
from clara_core.memory.graph.utils import (
    EXTRACT_RELATIONS_PROMPT,
    format_entities,
    get_delete_messages,
    sanitize_relationship_for_cypher,
)
from clara_core.memory.llm.unified import UnifiedLLM, UnifiedLLMConfig

if TYPE_CHECKING:
    from clara_core.memory.cache.graph_cache import GraphCache

logger = logging.getLogger(__name__)


class MemoryGraph:
    """Neo4j-based graph memory for entity and relationship tracking."""

    def __init__(self, config):
        """Initialize the Neo4j graph memory.

        Args:
            config: ClaraMemoryConfig instance with graph_store, embedder, and llm configs
        """
        self.config = config

        # Initialize Neo4j connection
        graph_config = config.graph_store.config
        self.graph = Neo4jGraph(
            graph_config.get("url"),
            graph_config.get("username", "neo4j"),
            graph_config.get("password"),
            graph_config.get("database", "neo4j"),
            refresh_schema=False,
            driver_config={"notifications_min_severity": "OFF"},
        )

        # Initialize embedder
        self.embedding_model = EmbedderFactory.create(
            config.embedder.provider,
            config.embedder.config,
        )

        # Node label for entities
        self.node_label = ":`__Entity__`"

        # Create indexes
        self._create_indexes()

        # Initialize LLM
        llm_provider = "openai"
        llm_config = None

        if config.graph_store and hasattr(config.graph_store, "llm") and config.graph_store.llm:
            llm_provider = config.graph_store.llm.provider
            llm_config = config.graph_store.llm.config
        elif config.llm:
            llm_provider = config.llm.provider
            llm_config = config.llm.config

        self.llm_provider = llm_provider
        if isinstance(llm_config, dict):
            llm_config = UnifiedLLMConfig(**llm_config)
        self.llm = UnifiedLLM(llm_config)

        # Similarity threshold for node matching
        self.threshold = getattr(config.graph_store, "threshold", 0.7)

        # Initialize cache (lazy-loaded)
        self._cache: "GraphCache | None" = None
        self._cache_enabled = os.getenv("GRAPH_CACHE_ENABLED", "true").lower() == "true"

    @property
    def cache(self) -> "GraphCache | None":
        """Lazy-load graph cache singleton."""
        if self._cache is None and self._cache_enabled:
            try:
                from clara_core.memory.cache.graph_cache import GraphCache

                self._cache = GraphCache.get_instance()
            except Exception as e:
                logger.debug(f"Graph cache unavailable: {e}")
        return self._cache

    def _create_indexes(self):
        """Create Neo4j indexes for efficient querying."""
        try:
            self.graph.query(f"CREATE INDEX entity_single IF NOT EXISTS FOR (n {self.node_label}) ON (n.user_id)")
        except Exception:
            pass
        try:
            self.graph.query(
                f"CREATE INDEX entity_composite IF NOT EXISTS FOR (n {self.node_label}) ON (n.name, n.user_id)"
            )
        except Exception:
            pass

    def add(self, data: str, filters: dict) -> dict:
        """Add entities and relationships to the graph.

        Args:
            data: Text to extract entities from
            filters: Dict with user_id and optional agent_id, run_id

        Returns:
            Dict with deleted_entities and added_entities lists
        """
        entity_type_map = self._retrieve_nodes_from_data(data, filters)
        to_be_added = self._establish_nodes_relations_from_data(data, filters, entity_type_map)
        search_output = self._search_graph_db(node_list=list(entity_type_map.keys()), filters=filters)
        to_be_deleted = self._get_delete_entities_from_search_output(search_output, data, filters)

        deleted_entities = self._delete_entities(to_be_deleted, filters)
        added_entities = self._add_entities(to_be_added, filters, entity_type_map)

        # Invalidate cache after graph modification
        if self.cache and (deleted_entities or added_entities):
            self.cache.invalidate_user(filters["user_id"])

        if added_entities or deleted_entities:
            logger.info(
                "Graph write: user_id=%s added=%d deleted=%d",
                filters.get("user_id"),
                len(added_entities),
                len(deleted_entities),
            )

        return {"deleted_entities": deleted_entities, "added_entities": added_entities}

    def search(self, query: str, filters: dict, limit: int = 100) -> list:
        """Search for entities related to the query.

        Args:
            query: Search query
            filters: Dict with user_id and optional agent_id, run_id
            limit: Maximum results to return

        Returns:
            List of dicts with source, relationship, destination keys
        """
        user_id = filters["user_id"]

        # Check cache first
        if self.cache:
            cached = self.cache.get_search_results(user_id, query, filters)
            if cached is not None:
                logger.info(f"Graph search cache hit: {len(cached)} results")
                return cached

        entity_type_map = self._retrieve_nodes_from_data(query, filters)
        search_output = self._search_graph_db(node_list=list(entity_type_map.keys()), filters=filters)

        if not search_output:
            return []

        # BM25 reranking
        search_outputs_sequence = [
            [item["source"], item["relationship"], item["destination"]] for item in search_output
        ]
        bm25 = BM25Okapi(search_outputs_sequence)
        tokenized_query = query.split(" ")
        reranked_results = bm25.get_top_n(tokenized_query, search_outputs_sequence, n=5)

        search_results = []
        for item in reranked_results:
            search_results.append({"source": item[0], "relationship": item[1], "destination": item[2]})

        # Cache results
        if self.cache and search_results:
            self.cache.set_search_results(user_id, query, search_results, filters)

        logger.info(f"Returned {len(search_results)} search results")
        return search_results

    def delete_all(self, filters: dict):
        """Delete all nodes for a user.

        Args:
            filters: Dict with user_id and optional agent_id, run_id
        """
        node_props = ["user_id: $user_id"]
        if filters.get("agent_id"):
            node_props.append("agent_id: $agent_id")
        if filters.get("run_id"):
            node_props.append("run_id: $run_id")
        node_props_str = ", ".join(node_props)

        cypher = f"""
        MATCH (n {self.node_label} {{{node_props_str}}})
        DETACH DELETE n
        """
        params = {"user_id": filters["user_id"]}
        if filters.get("agent_id"):
            params["agent_id"] = filters["agent_id"]
        if filters.get("run_id"):
            params["run_id"] = filters["run_id"]
        self.graph.query(cypher, params=params)

        # Invalidate cache after deletion
        if self.cache:
            self.cache.invalidate_user(filters["user_id"])

    def get_all(self, filters: dict, limit: int = 100) -> list:
        """Get all relationships for a user.

        Args:
            filters: Dict with user_id and optional agent_id, run_id
            limit: Maximum results

        Returns:
            List of dicts with source, relationship, target keys
        """
        user_id = filters["user_id"]
        agent_id = filters.get("agent_id")

        # Check cache first
        if self.cache:
            cached = self.cache.get_all_relationships(user_id, agent_id)
            if cached is not None:
                logger.info(f"Graph get_all cache hit: {len(cached)} relationships")
                # Apply limit to cached results
                return cached[:limit]

        params = {"user_id": user_id, "limit": limit}
        node_props = ["user_id: $user_id"]

        if agent_id:
            node_props.append("agent_id: $agent_id")
            params["agent_id"] = agent_id
        if filters.get("run_id"):
            node_props.append("run_id: $run_id")
            params["run_id"] = filters["run_id"]

        node_props_str = ", ".join(node_props)

        query = f"""
        MATCH (n {self.node_label} {{{node_props_str}}})-[r]->(m {self.node_label} {{{node_props_str}}})
        RETURN n.name AS source, type(r) AS relationship, m.name AS target
        LIMIT $limit
        """
        results = self.graph.query(query, params=params)

        final_results = [
            {"source": r["source"], "relationship": r["relationship"], "target": r["target"]} for r in results
        ]

        # Cache results
        if self.cache and final_results:
            self.cache.set_all_relationships(user_id, final_results, agent_id)

        logger.info(f"Retrieved {len(final_results)} relationships")
        return final_results

    def reset(self):
        """Reset the graph by clearing all nodes and relationships."""
        logger.warning("Clearing graph...")
        return self.graph.query("MATCH (n) DETACH DELETE n")

    def _retrieve_nodes_from_data(self, data: str, filters: dict) -> dict:
        """Extract entities from text using LLM."""
        search_results = self.llm.generate_response(
            messages=[
                {
                    "role": "system",
                    "content": f"You are a smart assistant who understands entities and their types in a given text. If user message contains self reference such as 'I', 'me', 'my' etc. then use {filters['user_id']} as the source entity. Extract all the entities from the text. ***DO NOT*** answer the question itself if the given text is a question.",
                },
                {"role": "user", "content": data},
            ],
            tools=[EXTRACT_ENTITIES_TOOL],
        )

        entity_type_map = {}

        if not search_results:
            logger.debug("LLM returned no results for entity extraction")
            return entity_type_map

        try:
            for tool_call in search_results.get("tool_calls", []):
                if tool_call["name"] != "extract_entities":
                    continue
                for item in tool_call["arguments"]["entities"]:
                    if not isinstance(item, dict):
                        continue
                    entity = item.get("entity")
                    entity_type = item.get("entity_type")
                    if entity and entity_type:
                        entity_type_map[entity] = entity_type
        except Exception as e:
            logger.exception(f"Error in entity extraction: {e}")

        entity_type_map = {k.lower().replace(" ", "_"): v.lower().replace(" ", "_") for k, v in entity_type_map.items()}
        logger.debug(f"Entity type map: {entity_type_map}")
        return entity_type_map

    def _establish_nodes_relations_from_data(self, data: str, filters: dict, entity_type_map: dict) -> list:
        """Establish relationships between entities using LLM."""
        user_identity = f"user_id: {filters['user_id']}"
        if filters.get("agent_id"):
            user_identity += f", agent_id: {filters['agent_id']}"
        if filters.get("run_id"):
            user_identity += f", run_id: {filters['run_id']}"

        custom_prompt = getattr(self.config.graph_store, "custom_prompt", None)
        system_content = EXTRACT_RELATIONS_PROMPT.replace("USER_ID", user_identity)

        if custom_prompt:
            system_content = system_content.replace("CUSTOM_PROMPT", f"4. {custom_prompt}")
        else:
            system_content = system_content.replace("CUSTOM_PROMPT", "")

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": f"List of entities: {list(entity_type_map.keys())}. \n\nText: {data}"},
        ]

        extracted_entities = self.llm.generate_response(messages=messages, tools=[RELATIONS_TOOL])

        entities = []
        if extracted_entities and extracted_entities.get("tool_calls"):
            entities = extracted_entities["tool_calls"][0].get("arguments", {}).get("entities", [])

        entities = self._remove_spaces_from_entities(entities)
        logger.debug(f"Extracted entities: {entities}")
        return entities

    def _search_graph_db(self, node_list: list, filters: dict, limit: int = 100) -> list:
        """Search for similar nodes and their relationships."""
        result_relations = []

        node_props = ["user_id: $user_id"]
        if filters.get("agent_id"):
            node_props.append("agent_id: $agent_id")
        if filters.get("run_id"):
            node_props.append("run_id: $run_id")
        node_props_str = ", ".join(node_props)

        for node in node_list:
            n_embedding = self.embedding_model.embed(node)

            cypher_query = f"""
            MATCH (n {self.node_label} {{{node_props_str}}})
            WHERE n.embedding IS NOT NULL
            WITH n, round(2 * vector.similarity.cosine(n.embedding, $n_embedding) - 1, 4) AS similarity
            WHERE similarity >= $threshold
            CALL {{
                WITH n
                MATCH (n)-[r]->(m {self.node_label} {{{node_props_str}}})
                RETURN n.name AS source, elementId(n) AS source_id, type(r) AS relationship, elementId(r) AS relation_id, m.name AS destination, elementId(m) AS destination_id
                UNION
                WITH n
                MATCH (n)<-[r]-(m {self.node_label} {{{node_props_str}}})
                RETURN m.name AS source, elementId(m) AS source_id, type(r) AS relationship, elementId(r) AS relation_id, n.name AS destination, elementId(n) AS destination_id
            }}
            WITH distinct source, source_id, relationship, relation_id, destination, destination_id, similarity
            RETURN source, source_id, relationship, relation_id, destination, destination_id, similarity
            ORDER BY similarity DESC
            LIMIT $limit
            """

            params = {
                "n_embedding": n_embedding,
                "threshold": self.threshold,
                "user_id": filters["user_id"],
                "limit": limit,
            }
            if filters.get("agent_id"):
                params["agent_id"] = filters["agent_id"]
            if filters.get("run_id"):
                params["run_id"] = filters["run_id"]

            ans = self.graph.query(cypher_query, params=params)
            result_relations.extend(ans)

        return result_relations

    def _get_delete_entities_from_search_output(self, search_output: list, data: str, filters: dict) -> list:
        """Determine which entities should be deleted based on new information."""
        search_output_string = format_entities(search_output)

        user_identity = f"user_id: {filters['user_id']}"
        if filters.get("agent_id"):
            user_identity += f", agent_id: {filters['agent_id']}"
        if filters.get("run_id"):
            user_identity += f", run_id: {filters['run_id']}"

        system_prompt, user_prompt = get_delete_messages(search_output_string, data, user_identity)

        memory_updates = self.llm.generate_response(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            tools=[DELETE_MEMORY_TOOL_GRAPH],
        )

        to_be_deleted = []
        if not memory_updates:
            return to_be_deleted
        for item in memory_updates.get("tool_calls", []):
            if item.get("name") == "delete_graph_memory":
                to_be_deleted.append(item.get("arguments"))

        to_be_deleted = self._remove_spaces_from_entities(to_be_deleted)
        logger.debug(f"Deleted relationships: {to_be_deleted}")
        return to_be_deleted

    def _delete_entities(self, to_be_deleted: list, filters: dict) -> list:
        """Delete specified relationships from the graph."""
        user_id = filters["user_id"]
        agent_id = filters.get("agent_id")
        run_id = filters.get("run_id")
        results = []

        for item in to_be_deleted:
            source = item["source"]
            destination = item["destination"]
            relationship = item["relationship"]

            params = {"source_name": source, "dest_name": destination, "user_id": user_id}

            source_props = ["name: $source_name", "user_id: $user_id"]
            dest_props = ["name: $dest_name", "user_id: $user_id"]

            if agent_id:
                source_props.append("agent_id: $agent_id")
                dest_props.append("agent_id: $agent_id")
                params["agent_id"] = agent_id
            if run_id:
                source_props.append("run_id: $run_id")
                dest_props.append("run_id: $run_id")
                params["run_id"] = run_id

            source_props_str = ", ".join(source_props)
            dest_props_str = ", ".join(dest_props)

            cypher = f"""
            MATCH (n {self.node_label} {{{source_props_str}}})
            -[r:{relationship}]->
            (m {self.node_label} {{{dest_props_str}}})
            DELETE r
            RETURN n.name AS source, m.name AS target, type(r) AS relationship
            """

            result = self.graph.query(cypher, params=params)
            results.append(result)

        return results

    def _add_entities(self, to_be_added: list, filters: dict, entity_type_map: dict) -> list:
        """Add new entities and relationships to the graph."""
        user_id = filters["user_id"]
        agent_id = filters.get("agent_id")
        run_id = filters.get("run_id")
        results = []

        for item in to_be_added:
            source = item["source"]
            destination = item["destination"]
            relationship = item["relationship"]

            if not source or not destination or not relationship:
                logger.warning(f"Skipping entity with empty field: {item}")
                continue

            # Get embeddings
            source_embedding = self.embedding_model.embed(source)
            dest_embedding = self.embedding_model.embed(destination)

            # Search for existing nodes
            source_node = self._search_source_node(source_embedding, filters)
            dest_node = self._search_destination_node(dest_embedding, filters)

            # Build query based on which nodes exist
            params = {"user_id": user_id}
            if agent_id:
                params["agent_id"] = agent_id
            if run_id:
                params["run_id"] = run_id

            merge_props = ["name: $name", "user_id: $user_id"]
            if agent_id:
                merge_props.append("agent_id: $agent_id")
            if run_id:
                merge_props.append("run_id: $run_id")
            merge_props_str = ", ".join(merge_props)

            if source_node and dest_node:
                # Both nodes exist - just create relationship
                cypher = f"""
                MATCH (source) WHERE elementId(source) = $source_id
                SET source.mentions = coalesce(source.mentions, 0) + 1
                WITH source
                MATCH (destination) WHERE elementId(destination) = $destination_id
                SET destination.mentions = coalesce(destination.mentions, 0) + 1
                MERGE (source)-[r:{relationship}]->(destination)
                ON CREATE SET r.created_at = timestamp(), r.mentions = 1
                ON MATCH SET r.mentions = coalesce(r.mentions, 0) + 1
                RETURN source.name AS source, type(r) AS relationship, destination.name AS target
                """
                params.update(
                    {
                        "source_id": source_node[0]["elementId(source_candidate)"],
                        "destination_id": dest_node[0]["elementId(destination_candidate)"],
                    }
                )
            elif source_node:
                # Source exists, create destination
                cypher = f"""
                MATCH (source) WHERE elementId(source) = $source_id
                SET source.mentions = coalesce(source.mentions, 0) + 1
                WITH source
                MERGE (destination {self.node_label} {{name: $dest_name, user_id: $user_id}})
                ON CREATE SET destination.created = timestamp(), destination.mentions = 1
                ON MATCH SET destination.mentions = coalesce(destination.mentions, 0) + 1
                WITH source, destination
                CALL db.create.setNodeVectorProperty(destination, 'embedding', $dest_embedding)
                WITH source, destination
                MERGE (source)-[r:{relationship}]->(destination)
                ON CREATE SET r.created = timestamp(), r.mentions = 1
                ON MATCH SET r.mentions = coalesce(r.mentions, 0) + 1
                RETURN source.name AS source, type(r) AS relationship, destination.name AS target
                """
                params.update(
                    {
                        "source_id": source_node[0]["elementId(source_candidate)"],
                        "dest_name": destination,
                        "dest_embedding": dest_embedding,
                    }
                )
            elif dest_node:
                # Destination exists, create source
                cypher = f"""
                MATCH (destination) WHERE elementId(destination) = $destination_id
                SET destination.mentions = coalesce(destination.mentions, 0) + 1
                WITH destination
                MERGE (source {self.node_label} {{name: $source_name, user_id: $user_id}})
                ON CREATE SET source.created = timestamp(), source.mentions = 1
                ON MATCH SET source.mentions = coalesce(source.mentions, 0) + 1
                WITH source, destination
                CALL db.create.setNodeVectorProperty(source, 'embedding', $source_embedding)
                WITH source, destination
                MERGE (source)-[r:{relationship}]->(destination)
                ON CREATE SET r.created = timestamp(), r.mentions = 1
                ON MATCH SET r.mentions = coalesce(r.mentions, 0) + 1
                RETURN source.name AS source, type(r) AS relationship, destination.name AS target
                """
                params.update(
                    {
                        "destination_id": dest_node[0]["elementId(destination_candidate)"],
                        "source_name": source,
                        "source_embedding": source_embedding,
                    }
                )
            else:
                # Neither exists, create both
                cypher = f"""
                MERGE (source {self.node_label} {{name: $source_name, user_id: $user_id}})
                ON CREATE SET source.created = timestamp(), source.mentions = 1
                ON MATCH SET source.mentions = coalesce(source.mentions, 0) + 1
                WITH source
                CALL db.create.setNodeVectorProperty(source, 'embedding', $source_embedding)
                WITH source
                MERGE (destination {self.node_label} {{name: $dest_name, user_id: $user_id}})
                ON CREATE SET destination.created = timestamp(), destination.mentions = 1
                ON MATCH SET destination.mentions = coalesce(destination.mentions, 0) + 1
                WITH source, destination
                CALL db.create.setNodeVectorProperty(destination, 'embedding', $dest_embedding)
                WITH source, destination
                MERGE (source)-[r:{relationship}]->(destination)
                ON CREATE SET r.created = timestamp(), r.mentions = 1
                ON MATCH SET r.mentions = coalesce(r.mentions, 0) + 1
                RETURN source.name AS source, type(r) AS relationship, destination.name AS target
                """
                params.update(
                    {
                        "source_name": source,
                        "dest_name": destination,
                        "source_embedding": source_embedding,
                        "dest_embedding": dest_embedding,
                    }
                )

            result = self.graph.query(cypher, params=params)
            results.append(result)

        return results

    def _search_source_node(self, source_embedding: list, filters: dict) -> list:
        """Search for a source node by embedding similarity."""
        where_conditions = ["source_candidate.embedding IS NOT NULL", "source_candidate.user_id = $user_id"]
        if filters.get("agent_id"):
            where_conditions.append("source_candidate.agent_id = $agent_id")
        if filters.get("run_id"):
            where_conditions.append("source_candidate.run_id = $run_id")

        cypher = f"""
        MATCH (source_candidate {self.node_label})
        WHERE {" AND ".join(where_conditions)}
        WITH source_candidate, round(2 * vector.similarity.cosine(source_candidate.embedding, $source_embedding) - 1, 4) AS similarity
        WHERE similarity >= $threshold
        ORDER BY similarity DESC
        LIMIT 1
        RETURN elementId(source_candidate)
        """

        params = {"source_embedding": source_embedding, "user_id": filters["user_id"], "threshold": self.threshold}
        if filters.get("agent_id"):
            params["agent_id"] = filters["agent_id"]
        if filters.get("run_id"):
            params["run_id"] = filters["run_id"]

        return self.graph.query(cypher, params=params)

    def _search_destination_node(self, dest_embedding: list, filters: dict) -> list:
        """Search for a destination node by embedding similarity."""
        where_conditions = ["dest_candidate.embedding IS NOT NULL", "dest_candidate.user_id = $user_id"]
        if filters.get("agent_id"):
            where_conditions.append("dest_candidate.agent_id = $agent_id")
        if filters.get("run_id"):
            where_conditions.append("dest_candidate.run_id = $run_id")

        cypher = f"""
        MATCH (dest_candidate {self.node_label})
        WHERE {" AND ".join(where_conditions)}
        WITH dest_candidate, round(2 * vector.similarity.cosine(dest_candidate.embedding, $dest_embedding) - 1, 4) AS similarity
        WHERE similarity >= $threshold
        ORDER BY similarity DESC
        LIMIT 1
        RETURN elementId(dest_candidate)
        """

        params = {"dest_embedding": dest_embedding, "user_id": filters["user_id"], "threshold": self.threshold}
        if filters.get("agent_id"):
            params["agent_id"] = filters["agent_id"]
        if filters.get("run_id"):
            params["run_id"] = filters["run_id"]

        return self.graph.query(cypher, params=params)

    def _remove_spaces_from_entities(self, entity_list: list) -> list:
        """Normalize entity names by lowercasing and replacing spaces."""
        for item in entity_list:
            item["source"] = item["source"].lower().replace(" ", "_")
            item["relationship"] = sanitize_relationship_for_cypher(item["relationship"].lower().replace(" ", "_"))
            item["destination"] = item["destination"].lower().replace(" ", "_")
        return entity_list
