"""Kuzu graph store implementation for Clara Memory System.

Kuzu is an embedded graph database that doesn't require a separate server.
"""

import logging

try:
    import kuzu
except ImportError:
    raise ImportError("kuzu is not installed. Please install it using pip install kuzu")

try:
    from rank_bm25 import BM25Okapi
except ImportError:
    raise ImportError("rank_bm25 is not installed. Please install it using pip install rank-bm25")

from clara_core.memory.embeddings.factory import EmbedderFactory
from clara_core.memory.llm.factory import LlmFactory
from clara_core.memory.graph.tools import (
    DELETE_MEMORY_TOOL_GRAPH,
    EXTRACT_ENTITIES_TOOL,
    RELATIONS_TOOL,
)
from clara_core.memory.graph.utils import (
    EXTRACT_RELATIONS_PROMPT,
    format_entities,
    get_delete_messages,
)

logger = logging.getLogger(__name__)


class MemoryGraph:
    """Kuzu-based graph memory for entity and relationship tracking.

    Kuzu is an embedded graph database that stores data locally without
    requiring a separate server process.
    """

    def __init__(self, config):
        """Initialize the Kuzu graph memory.

        Args:
            config: ClaraMemoryConfig instance with graph_store, embedder, and llm configs
        """
        self.config = config

        # Initialize embedder first to get embedding dimensions
        self.embedding_model = EmbedderFactory.create(
            config.embedder.provider,
            config.embedder.config,
        )
        self.embedding_dims = getattr(self.embedding_model.config, "embedding_dims", 1536)

        # Initialize Kuzu database
        graph_config = config.graph_store.config
        db_path = graph_config.get("db_path") or graph_config.get("db")
        self.db = kuzu.Database(db_path)
        self.graph = kuzu.Connection(self.db)

        # Create schema
        self.node_label = ":Entity"
        self.rel_label = ":CONNECTED_TO"
        self._create_schema()

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
        self.llm = LlmFactory.create(llm_provider, llm_config)

        # Similarity threshold
        self.threshold = getattr(config.graph_store, "threshold", 0.7)

    def _create_schema(self):
        """Create Kuzu schema for entities and relationships."""
        self._execute(
            """
            CREATE NODE TABLE IF NOT EXISTS Entity(
                id SERIAL PRIMARY KEY,
                user_id STRING,
                agent_id STRING,
                run_id STRING,
                name STRING,
                mentions INT64,
                created TIMESTAMP,
                embedding FLOAT[]);
            """
        )
        self._execute(
            """
            CREATE REL TABLE IF NOT EXISTS CONNECTED_TO(
                FROM Entity TO Entity,
                name STRING,
                mentions INT64,
                created TIMESTAMP,
                updated TIMESTAMP
            );
            """
        )

    def _execute(self, query: str, parameters: dict = None) -> list:
        """Execute a Kuzu query and return results as list of dicts."""
        results = self.graph.execute(query, parameters)
        return list(results.rows_as_dict())

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

        return {"deleted_entities": deleted_entities, "added_entities": added_entities}

    def search(self, query: str, filters: dict, limit: int = 5) -> list:
        """Search for entities related to the query.

        Args:
            query: Search query
            filters: Dict with user_id and optional agent_id, run_id
            limit: Maximum results to return

        Returns:
            List of dicts with source, relationship, destination keys
        """
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
        reranked_results = bm25.get_top_n(tokenized_query, search_outputs_sequence, n=limit)

        search_results = []
        for item in reranked_results:
            search_results.append({"source": item[0], "relationship": item[1], "destination": item[2]})

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
        self._execute(cypher, parameters=params)

    def get_all(self, filters: dict, limit: int = 100) -> list:
        """Get all relationships for a user.

        Args:
            filters: Dict with user_id and optional agent_id, run_id
            limit: Maximum results

        Returns:
            List of dicts with source, relationship, target keys
        """
        params = {"user_id": filters["user_id"], "limit": limit}
        node_props = ["user_id: $user_id"]

        if filters.get("agent_id"):
            node_props.append("agent_id: $agent_id")
            params["agent_id"] = filters["agent_id"]
        if filters.get("run_id"):
            node_props.append("run_id: $run_id")
            params["run_id"] = filters["run_id"]

        node_props_str = ", ".join(node_props)

        query = f"""
        MATCH (n {self.node_label} {{{node_props_str}}})-[r]->(m {self.node_label} {{{node_props_str}}})
        RETURN n.name AS source, r.name AS relationship, m.name AS target
        LIMIT $limit
        """
        results = self._execute(query, parameters=params)

        final_results = [
            {"source": r["source"], "relationship": r["relationship"], "target": r["target"]}
            for r in results
        ]

        logger.info(f"Retrieved {len(final_results)} relationships")
        return final_results

    def reset(self):
        """Reset the graph by clearing all nodes and relationships."""
        logger.warning("Clearing graph...")
        return self._execute("MATCH (n) DETACH DELETE n")

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

        try:
            for tool_call in search_results.get("tool_calls", []):
                if tool_call["name"] != "extract_entities":
                    continue
                for item in tool_call["arguments"]["entities"]:
                    entity_type_map[item["entity"]] = item["entity_type"]
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
        if extracted_entities.get("tool_calls"):
            entities = extracted_entities["tool_calls"][0].get("arguments", {}).get("entities", [])

        entities = self._remove_spaces_from_entities(entities)
        logger.debug(f"Extracted entities: {entities}")
        return entities

    def _search_graph_db(self, node_list: list, filters: dict, limit: int = 100) -> list:
        """Search for similar nodes and their relationships."""
        result_relations = []

        params = {
            "threshold": self.threshold,
            "user_id": filters["user_id"],
            "limit": limit,
        }

        node_props = ["user_id: $user_id"]
        if filters.get("agent_id"):
            node_props.append("agent_id: $agent_id")
            params["agent_id"] = filters["agent_id"]
        if filters.get("run_id"):
            node_props.append("run_id: $run_id")
            params["run_id"] = filters["run_id"]
        node_props_str = ", ".join(node_props)

        for node in node_list:
            n_embedding = self.embedding_model.embed(node)
            params["n_embedding"] = n_embedding

            results = []
            for match_fragment in [
                f"(n)-[r]->(m {self.node_label} {{{node_props_str}}}) WITH n as src, r, m as dst, similarity",
                f"(m {self.node_label} {{{node_props_str}}})-[r]->(n) WITH m as src, r, n as dst, similarity"
            ]:
                results.extend(self._execute(
                    f"""
                    MATCH (n {self.node_label} {{{node_props_str}}})
                    WHERE n.embedding IS NOT NULL
                    WITH n, array_cosine_similarity(n.embedding, CAST($n_embedding,'FLOAT[{self.embedding_dims}]')) AS similarity
                    WHERE similarity >= CAST($threshold, 'DOUBLE')
                    MATCH {match_fragment}
                    RETURN
                        src.name AS source,
                        id(src) AS source_id,
                        r.name AS relationship,
                        id(r) AS relation_id,
                        dst.name AS destination,
                        id(dst) AS destination_id,
                        similarity
                    LIMIT $limit
                    """,
                    parameters=params
                ))

            # Sort and limit since Kuzu doesn't support sort over unions
            result_relations.extend(sorted(results, key=lambda x: x["similarity"], reverse=True)[:limit])

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

            params = {
                "source_name": source,
                "dest_name": destination,
                "user_id": user_id,
                "relationship_name": relationship,
            }

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
            -[r {self.rel_label} {{name: $relationship_name}}]->
            (m {self.node_label} {{{dest_props_str}}})
            DELETE r
            RETURN n.name AS source, r.name AS relationship, m.name AS target
            """

            result = self._execute(cypher, parameters=params)
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

            # Get embeddings
            source_embedding = self.embedding_model.embed(source)
            dest_embedding = self.embedding_model.embed(destination)

            # Search for existing nodes
            source_node = self._search_source_node(source_embedding, filters)
            dest_node = self._search_destination_node(dest_embedding, filters)

            params = {"user_id": user_id, "relationship_name": relationship}
            if agent_id:
                params["agent_id"] = agent_id
            if run_id:
                params["run_id"] = run_id

            merge_props = ["name: $name", "user_id: $user_id"]
            if agent_id:
                merge_props.append("agent_id: $agent_id")
            if run_id:
                merge_props.append("run_id: $run_id")

            if source_node and dest_node:
                # Both nodes exist
                cypher = f"""
                MATCH (source) WHERE id(source) = internal_id($src_table, $src_offset)
                SET source.mentions = coalesce(source.mentions, 0) + 1
                WITH source
                MATCH (destination) WHERE id(destination) = internal_id($dst_table, $dst_offset)
                SET destination.mentions = coalesce(destination.mentions, 0) + 1
                MERGE (source)-[r {self.rel_label} {{name: $relationship_name}}]->(destination)
                ON CREATE SET r.created = current_timestamp(), r.mentions = 1
                ON MATCH SET r.mentions = coalesce(r.mentions, 0) + 1
                RETURN source.name AS source, r.name AS relationship, destination.name AS target
                """
                params.update({
                    "src_table": source_node[0]["id"]["table"],
                    "src_offset": source_node[0]["id"]["offset"],
                    "dst_table": dest_node[0]["id"]["table"],
                    "dst_offset": dest_node[0]["id"]["offset"],
                })
            elif source_node:
                # Source exists, create destination
                merge_props_str = ", ".join(["name: $dest_name", "user_id: $user_id"] +
                                            (["agent_id: $agent_id"] if agent_id else []) +
                                            (["run_id: $run_id"] if run_id else []))
                cypher = f"""
                MATCH (source) WHERE id(source) = internal_id($table_id, $offset_id)
                SET source.mentions = coalesce(source.mentions, 0) + 1
                WITH source
                MERGE (destination {self.node_label} {{{merge_props_str}}})
                ON CREATE SET
                    destination.created = current_timestamp(),
                    destination.mentions = 1,
                    destination.embedding = CAST($dest_embedding,'FLOAT[{self.embedding_dims}]')
                ON MATCH SET
                    destination.mentions = coalesce(destination.mentions, 0) + 1
                WITH source, destination
                MERGE (source)-[r {self.rel_label} {{name: $relationship_name}}]->(destination)
                ON CREATE SET r.created = current_timestamp(), r.mentions = 1
                ON MATCH SET r.mentions = coalesce(r.mentions, 0) + 1
                RETURN source.name AS source, r.name AS relationship, destination.name AS target
                """
                params.update({
                    "table_id": source_node[0]["id"]["table"],
                    "offset_id": source_node[0]["id"]["offset"],
                    "dest_name": destination,
                    "dest_embedding": dest_embedding,
                })
            elif dest_node:
                # Destination exists, create source
                merge_props_str = ", ".join(["name: $source_name", "user_id: $user_id"] +
                                            (["agent_id: $agent_id"] if agent_id else []) +
                                            (["run_id: $run_id"] if run_id else []))
                cypher = f"""
                MATCH (destination) WHERE id(destination) = internal_id($table_id, $offset_id)
                SET destination.mentions = coalesce(destination.mentions, 0) + 1
                WITH destination
                MERGE (source {self.node_label} {{{merge_props_str}}})
                ON CREATE SET
                    source.created = current_timestamp(),
                    source.mentions = 1,
                    source.embedding = CAST($source_embedding,'FLOAT[{self.embedding_dims}]')
                ON MATCH SET
                    source.mentions = coalesce(source.mentions, 0) + 1
                WITH source, destination
                MERGE (source)-[r {self.rel_label} {{name: $relationship_name}}]->(destination)
                ON CREATE SET r.created = current_timestamp(), r.mentions = 1
                ON MATCH SET r.mentions = coalesce(r.mentions, 0) + 1
                RETURN source.name AS source, r.name AS relationship, destination.name AS target
                """
                params.update({
                    "table_id": dest_node[0]["id"]["table"],
                    "offset_id": dest_node[0]["id"]["offset"],
                    "source_name": source,
                    "source_embedding": source_embedding,
                })
            else:
                # Neither exists, create both
                source_props_str = ", ".join(["name: $source_name", "user_id: $user_id"] +
                                             (["agent_id: $agent_id"] if agent_id else []) +
                                             (["run_id: $run_id"] if run_id else []))
                dest_props_str = ", ".join(["name: $dest_name", "user_id: $user_id"] +
                                           (["agent_id: $agent_id"] if agent_id else []) +
                                           (["run_id: $run_id"] if run_id else []))
                cypher = f"""
                MERGE (source {self.node_label} {{{source_props_str}}})
                ON CREATE SET
                    source.created = current_timestamp(),
                    source.mentions = 1,
                    source.embedding = CAST($source_embedding,'FLOAT[{self.embedding_dims}]')
                ON MATCH SET
                    source.mentions = coalesce(source.mentions, 0) + 1
                WITH source
                MERGE (destination {self.node_label} {{{dest_props_str}}})
                ON CREATE SET
                    destination.created = current_timestamp(),
                    destination.mentions = 1,
                    destination.embedding = CAST($dest_embedding,'FLOAT[{self.embedding_dims}]')
                ON MATCH SET
                    destination.mentions = coalesce(destination.mentions, 0) + 1
                WITH source, destination
                MERGE (source)-[r {self.rel_label} {{name: $relationship_name}}]->(destination)
                ON CREATE SET r.created = current_timestamp(), r.mentions = 1
                ON MATCH SET r.mentions = coalesce(r.mentions, 0) + 1
                RETURN source.name AS source, r.name AS relationship, destination.name AS target
                """
                params.update({
                    "source_name": source,
                    "dest_name": destination,
                    "source_embedding": source_embedding,
                    "dest_embedding": dest_embedding,
                })

            result = self._execute(cypher, parameters=params)
            results.append(result)

        return results

    def _search_source_node(self, source_embedding: list, filters: dict) -> list:
        """Search for a source node by embedding similarity."""
        params = {
            "source_embedding": source_embedding,
            "user_id": filters["user_id"],
            "threshold": self.threshold,
        }

        where_conditions = ["source_candidate.embedding IS NOT NULL", "source_candidate.user_id = $user_id"]
        if filters.get("agent_id"):
            where_conditions.append("source_candidate.agent_id = $agent_id")
            params["agent_id"] = filters["agent_id"]
        if filters.get("run_id"):
            where_conditions.append("source_candidate.run_id = $run_id")
            params["run_id"] = filters["run_id"]

        cypher = f"""
        MATCH (source_candidate {self.node_label})
        WHERE {" AND ".join(where_conditions)}
        WITH source_candidate,
            array_cosine_similarity(source_candidate.embedding, CAST($source_embedding,'FLOAT[{self.embedding_dims}]')) AS similarity
        WHERE similarity >= $threshold
        ORDER BY similarity DESC
        LIMIT 2
        RETURN id(source_candidate) as id, similarity
        """

        return self._execute(cypher, parameters=params)

    def _search_destination_node(self, dest_embedding: list, filters: dict) -> list:
        """Search for a destination node by embedding similarity."""
        params = {
            "dest_embedding": dest_embedding,
            "user_id": filters["user_id"],
            "threshold": self.threshold,
        }

        where_conditions = ["dest_candidate.embedding IS NOT NULL", "dest_candidate.user_id = $user_id"]
        if filters.get("agent_id"):
            where_conditions.append("dest_candidate.agent_id = $agent_id")
            params["agent_id"] = filters["agent_id"]
        if filters.get("run_id"):
            where_conditions.append("dest_candidate.run_id = $run_id")
            params["run_id"] = filters["run_id"]

        cypher = f"""
        MATCH (dest_candidate {self.node_label})
        WHERE {" AND ".join(where_conditions)}
        WITH dest_candidate,
            array_cosine_similarity(dest_candidate.embedding, CAST($dest_embedding,'FLOAT[{self.embedding_dims}]')) AS similarity
        WHERE similarity >= $threshold
        ORDER BY similarity DESC
        LIMIT 2
        RETURN id(dest_candidate) as id, similarity
        """

        return self._execute(cypher, parameters=params)

    def _remove_spaces_from_entities(self, entity_list: list) -> list:
        """Normalize entity names by lowercasing and replacing spaces."""
        for item in entity_list:
            item["source"] = item["source"].lower().replace(" ", "_")
            item["relationship"] = item["relationship"].lower().replace(" ", "_")
            item["destination"] = item["destination"].lower().replace(" ", "_")
        return entity_list
