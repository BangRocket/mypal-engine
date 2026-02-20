"""Clara Memory System - Core memory operations.

This module provides the ClaraMemory class, which is Clara's native memory
system based on the mem0 architecture but streamlined for Clara's needs.
"""

import concurrent.futures
import hashlib
import json
import logging
import os
import uuid
import warnings
from copy import deepcopy
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

import pytz
from pydantic import BaseModel, Field, ValidationError

from mypalclara.core.memory.core.base import MemoryBase
from mypalclara.core.memory.core.prompts import get_update_memory_messages
from mypalclara.core.memory.core.storage import get_history_manager
from mypalclara.core.memory.core.utils import (
    extract_json,
    get_fact_retrieval_messages,
    parse_messages,
    parse_vision_messages,
    remove_code_blocks,
)
from mypalclara.core.memory.embeddings.base import BaseEmbedderConfig
from mypalclara.core.memory.embeddings.cached import CachedEmbedding
from mypalclara.core.memory.embeddings.openai import OpenAIEmbedding
from mypalclara.core.memory.llm.unified import UnifiedLLM, UnifiedLLMConfig
from mypalclara.core.memory.vector.factory import VectorStoreFactory

# Suppress SWIG deprecation warnings globally
warnings.filterwarnings("ignore", category=DeprecationWarning, message=".*SwigPy.*")
warnings.filterwarnings("ignore", category=DeprecationWarning, message=".*swigvarlink.*")

logger = logging.getLogger("clara.memory")

# Set up the directory path
home_dir = os.path.expanduser("~")
clara_memory_dir = os.environ.get("CLARA_MEMORY_DIR") or os.path.join(home_dir, ".clara_memory")

# Ensure the directory exists
os.makedirs(clara_memory_dir, exist_ok=True)


class MemoryType(str, Enum):
    """Types of memory that can be stored."""

    PROCEDURAL = "procedural_memory"


class ClaraMemoryItem(BaseModel):
    """A memory item stored in the system."""

    id: str = Field(..., description="The unique identifier for the memory")
    memory: str = Field(..., description="The memory content")
    hash: Optional[str] = Field(None, description="The hash of the memory content")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")
    score: Optional[float] = Field(None, description="Relevance score")
    created_at: Optional[str] = Field(None, description="Creation timestamp")
    updated_at: Optional[str] = Field(None, description="Update timestamp")


class ClaraMemoryValidationError(Exception):
    """Validation error for memory operations."""

    def __init__(
        self,
        message: str,
        error_code: str = None,
        details: Dict = None,
        suggestion: str = None,
    ):
        self.message = message
        self.error_code = error_code
        self.details = details or {}
        self.suggestion = suggestion
        super().__init__(self.message)


def _build_filters_and_metadata(
    *,
    user_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    run_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    input_metadata: Optional[Dict[str, Any]] = None,
    input_filters: Optional[Dict[str, Any]] = None,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Build metadata and filters from session identifiers.

    Args:
        user_id: User identifier
        agent_id: Agent identifier
        run_id: Run identifier
        actor_id: Actor identifier for filtering
        input_metadata: Base metadata dictionary
        input_filters: Base filters dictionary

    Returns:
        Tuple of (metadata_template, query_filters)

    Raises:
        ClaraMemoryValidationError: If no session identifiers provided
    """
    base_metadata = deepcopy(input_metadata) if input_metadata else {}
    effective_filters = deepcopy(input_filters) if input_filters else {}

    session_ids_provided = []

    if user_id:
        base_metadata["user_id"] = user_id
        effective_filters["user_id"] = user_id
        session_ids_provided.append("user_id")

    if agent_id:
        base_metadata["agent_id"] = agent_id
        effective_filters["agent_id"] = agent_id
        session_ids_provided.append("agent_id")

    if run_id:
        base_metadata["run_id"] = run_id
        effective_filters["run_id"] = run_id
        session_ids_provided.append("run_id")

    if not session_ids_provided:
        raise ClaraMemoryValidationError(
            message="At least one of 'user_id', 'agent_id', or 'run_id' must be provided.",
            error_code="VALIDATION_001",
            details={"provided_ids": {"user_id": user_id, "agent_id": agent_id, "run_id": run_id}},
            suggestion="Please provide at least one identifier to scope the memory operation.",
        )

    resolved_actor_id = actor_id or effective_filters.get("actor_id")
    if resolved_actor_id:
        effective_filters["actor_id"] = resolved_actor_id

    return base_metadata, effective_filters


class ClaraMemory(MemoryBase):
    """Clara's native memory system.

    This class provides semantic memory storage with support for:
    - Vector-based similarity search
    - LLM-based fact extraction
    - Optional graph memory for entity relationships
    - Memory history tracking

    Usage:
        memory = ClaraMemory.from_config(config_dict)
        memory.add(messages, user_id="user-123")
        results = memory.search(query, user_id="user-123")
    """

    def __init__(self, config):
        """Initialize ClaraMemory.

        Args:
            config: ClaraMemoryConfig instance
        """
        self.config = config

        self.custom_fact_extraction_prompt = getattr(config, "custom_fact_extraction_prompt", None)
        self.custom_update_memory_prompt = getattr(config, "custom_update_memory_prompt", None)
        self.retrieval_criteria = getattr(config, "retrieval_criteria", None)

        # Initialize embedding model
        embedder_conf = self.config.embedder.config
        if isinstance(embedder_conf, dict):
            embedder_conf = BaseEmbedderConfig(**embedder_conf)
        self.embedding_model = OpenAIEmbedding(embedder_conf)

        # Wrap with cache if configured
        enable_cache = os.getenv("MEMORY_EMBEDDING_CACHE", "true").lower() == "true"
        if enable_cache and os.getenv("REDIS_URL"):
            self.embedding_model = CachedEmbedding(self.embedding_model, enabled=True)

        # Initialize vector store
        self.vector_store = VectorStoreFactory.create(
            self.config.vector_store.provider, self.config.vector_store.config
        )

        # Initialize LLM
        llm_conf = self.config.llm.config
        if isinstance(llm_conf, dict):
            llm_conf = UnifiedLLMConfig(**llm_conf)
        self.llm = UnifiedLLM(llm_conf)

        # Initialize history database (optional - for memory change tracking)
        # Uses PostgreSQL if DATABASE_URL is set, otherwise SQLite
        try:
            self.db = get_history_manager(self.config.history_db_path)
        except Exception as e:
            logger.warning(f"Memory history disabled: {e}")
            self.db = None
        self.collection_name = self.config.vector_store.config.get("collection_name", "clara_memories")
        self.api_version = getattr(config, "version", "v1.1")

        # Reranker (not used by default)
        self.reranker = None

        # Graph memory (optional)
        self.enable_graph = False
        if hasattr(self.config, "graph_store") and self.config.graph_store and self.config.graph_store.config:
            try:
                from mypalclara.core.memory.graph.factory import GraphStoreFactory

                provider = self.config.graph_store.provider
                self.graph = GraphStoreFactory.create(provider, self.config)
                self.enable_graph = True
            except ImportError:
                logger.warning("Graph memory not available - missing dependencies")
                self.graph = None
        else:
            self.graph = None

    @classmethod
    def from_config(cls, config_dict: Dict[str, Any]):
        """Create a ClaraMemory instance from a config dictionary.

        Args:
            config_dict: Configuration dictionary

        Returns:
            ClaraMemory instance
        """
        try:
            config = ClaraMemoryConfig(**config_dict)
        except ValidationError as e:
            logger.error(f"Configuration validation error: {e}")
            raise
        return cls(config)

    def _should_use_agent_memory_extraction(self, messages, metadata):
        """Determine whether to use agent memory extraction.

        Uses agent memory extraction if agent_id is present and
        messages contain assistant role messages.
        """
        has_agent_id = metadata.get("agent_id") is not None
        has_assistant_messages = any(msg.get("role") == "assistant" for msg in messages)
        return has_agent_id and has_assistant_messages

    def add(
        self,
        messages,
        *,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        infer: bool = True,
        memory_type: Optional[str] = None,
        prompt: Optional[str] = None,
        timestamp: Optional[int] = None,
    ):
        """Add new memories from messages.

        Args:
            messages: Message content (str, dict, or list of dicts)
            user_id: User identifier
            agent_id: Agent identifier
            run_id: Run identifier
            metadata: Additional metadata
            infer: If True, use LLM to extract facts from messages
            memory_type: Type of memory (e.g., "procedural_memory")
            prompt: Custom extraction prompt
            timestamp: Unix timestamp for the memory

        Returns:
            dict: Results with added/updated memories
        """
        processed_metadata, effective_filters = _build_filters_and_metadata(
            user_id=user_id,
            agent_id=agent_id,
            run_id=run_id,
            input_metadata=metadata,
        )

        if memory_type is not None and memory_type != MemoryType.PROCEDURAL.value:
            raise ClaraMemoryValidationError(
                message=f"Invalid 'memory_type'. Use {MemoryType.PROCEDURAL.value} for procedural memories.",
                error_code="VALIDATION_002",
                details={"provided_type": memory_type},
            )

        # Normalize messages format
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]
        elif isinstance(messages, dict):
            messages = [messages]
        elif isinstance(messages, list) and messages and not isinstance(messages[0], dict):
            # Typed Message objects — convert to dicts for downstream processing
            messages = [m.to_dict() for m in messages]
        elif not isinstance(messages, list):
            raise ClaraMemoryValidationError(
                message="messages must be str, dict, list[dict], or list[Message]",
                error_code="VALIDATION_003",
            )

        # Handle procedural memory
        if agent_id is not None and memory_type == MemoryType.PROCEDURAL.value:
            return self._create_procedural_memory(messages, metadata=processed_metadata, prompt=prompt)

        # Handle vision content
        if hasattr(self.config.llm, "config") and self.config.llm.config.get("enable_vision"):
            messages = parse_vision_messages(messages, self.llm, self.config.llm.config.get("vision_details"))
        else:
            messages = parse_vision_messages(messages)

        # Process in parallel: vector store + graph
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future1 = executor.submit(
                self._add_to_vector_store, messages, processed_metadata, effective_filters, infer, timestamp
            )
            future2 = executor.submit(self._add_to_graph, messages, effective_filters)

            concurrent.futures.wait([future1, future2])

            vector_store_result = future1.result()
            graph_result = future2.result()

        if self.enable_graph:
            return {"results": vector_store_result, "relations": graph_result}

        return {"results": vector_store_result}

    def _add_to_vector_store(self, messages, metadata, filters, infer, timestamp=None):
        """Add messages to the vector store."""
        if not infer:
            # Raw storage without inference
            returned_memories = []
            for msg_dict in messages:
                if not isinstance(msg_dict, dict) or not msg_dict.get("role") or not msg_dict.get("content"):
                    logger.warning(f"Skipping invalid message: {msg_dict}")
                    continue

                if msg_dict["role"] == "system":
                    continue

                per_msg_meta = deepcopy(metadata)
                per_msg_meta["role"] = msg_dict["role"]

                actor_name = msg_dict.get("name")
                if actor_name:
                    per_msg_meta["actor_id"] = actor_name

                content = msg_dict["content"]
                embeddings = self.embedding_model.embed(content, "add")
                mem_id = self._create_memory(content, embeddings, per_msg_meta, timestamp=timestamp)

                returned_memories.append(
                    {
                        "id": mem_id,
                        "memory": content,
                        "event": "ADD",
                        "actor_id": actor_name,
                        "role": msg_dict["role"],
                    }
                )
            return returned_memories

        # Use LLM inference to extract facts
        parsed_messages = parse_messages(messages)

        if self.custom_fact_extraction_prompt:
            system_prompt = self.custom_fact_extraction_prompt
            user_prompt = f"Input:\n{parsed_messages}"
        else:
            is_agent_memory = self._should_use_agent_memory_extraction(messages, metadata)
            system_prompt, user_prompt = get_fact_retrieval_messages(parsed_messages, is_agent_memory)

        response = self.llm.generate_response(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
        )

        try:
            response = remove_code_blocks(response)
            if not response.strip():
                new_retrieved_facts = []
            else:
                try:
                    new_retrieved_facts = json.loads(response)["facts"]
                except json.JSONDecodeError:
                    extracted = extract_json(response)
                    new_retrieved_facts = json.loads(extracted)["facts"]
        except Exception as e:
            logger.error(f"Error parsing facts: {e}")
            new_retrieved_facts = []

        if not new_retrieved_facts:
            logger.debug("No facts extracted from input")
            return []

        # Search for existing memories
        retrieved_old_memory = []
        new_message_embeddings = {}

        search_filters = {}
        if filters.get("user_id"):
            search_filters["user_id"] = filters["user_id"]
        if filters.get("agent_id"):
            search_filters["agent_id"] = filters["agent_id"]
        if filters.get("run_id"):
            search_filters["run_id"] = filters["run_id"]

        for new_mem in new_retrieved_facts:
            # Handle both string and dict fact formats
            if isinstance(new_mem, dict):
                mem_text = new_mem.get("text", "")
                mem_is_key = new_mem.get("is_key", False)
            else:
                mem_text = new_mem
                mem_is_key = False

            if not mem_text:
                continue

            embeddings = self.embedding_model.embed(mem_text, "add")
            new_message_embeddings[mem_text] = embeddings

            existing_memories = self.vector_store.search(
                query=mem_text,
                vectors=embeddings,
                limit=5,
                filters=search_filters,
            )

            for mem in existing_memories:
                existing_is_key = mem.payload.get("is_key", "false")
                retrieved_old_memory.append(
                    {
                        "id": mem.id,
                        "text": mem.payload.get("data", ""),
                        "is_key": existing_is_key,
                    }
                )

        # Deduplicate
        unique_data = {item["id"]: item for item in retrieved_old_memory}
        retrieved_old_memory = list(unique_data.values())
        logger.info(f"Found {len(retrieved_old_memory)} existing memories")

        # Map UUIDs to integers to avoid hallucination issues
        temp_uuid_mapping = {}
        for idx, item in enumerate(retrieved_old_memory):
            temp_uuid_mapping[str(idx)] = item["id"]
            retrieved_old_memory[idx]["id"] = str(idx)

        if new_retrieved_facts:
            function_calling_prompt = get_update_memory_messages(
                retrieved_old_memory, new_retrieved_facts, self.custom_update_memory_prompt
            )

            try:
                response = self.llm.generate_response(
                    messages=[{"role": "user", "content": function_calling_prompt}],
                    response_format={"type": "json_object"},
                )
            except Exception as e:
                logger.error(f"Error getting memory actions: {e}")
                response = ""

            try:
                if not response or not response.strip():
                    new_memories_with_actions = {}
                else:
                    response = remove_code_blocks(response)
                    new_memories_with_actions = json.loads(response)
            except Exception as e:
                logger.error(f"Invalid JSON response: {e}")
                new_memories_with_actions = {}
        else:
            new_memories_with_actions = {}

        returned_memories = []
        try:
            for resp in new_memories_with_actions.get("memory", []):
                logger.debug(f"Processing memory action: {resp}")
                try:
                    action_text = resp.get("text")
                    if not action_text:
                        continue

                    event_type = resp.get("event")
                    is_key_value = resp.get("is_key", False)
                    is_key_str = "true" if is_key_value else "false"

                    if event_type == "ADD":
                        add_meta = deepcopy(metadata)
                        add_meta["is_key"] = is_key_str
                        mem_id = self._create_memory(
                            data=action_text,
                            existing_embeddings=new_message_embeddings,
                            metadata=add_meta,
                            timestamp=timestamp,
                        )
                        returned_memories.append(
                            {
                                "id": mem_id,
                                "memory": action_text,
                                "event": event_type,
                                "is_key": is_key_str,
                            }
                        )
                    elif event_type == "UPDATE":
                        target_id = temp_uuid_mapping.get(resp.get("id"))
                        if target_id:
                            update_meta = deepcopy(metadata)
                            update_meta["is_key"] = is_key_str
                            self._update_memory(
                                memory_id=target_id,
                                data=action_text,
                                existing_embeddings=new_message_embeddings,
                                metadata=update_meta,
                            )
                            returned_memories.append(
                                {
                                    "id": target_id,
                                    "memory": action_text,
                                    "event": event_type,
                                    "previous_memory": resp.get("old_memory"),
                                    "is_key": is_key_str,
                                }
                            )
                        else:
                            # Target not found, convert to ADD
                            logger.info("UPDATE target not found, converting to ADD")
                            add_meta = deepcopy(metadata)
                            add_meta["is_key"] = is_key_str
                            mem_id = self._create_memory(
                                data=action_text,
                                existing_embeddings=new_message_embeddings,
                                metadata=add_meta,
                                timestamp=timestamp,
                            )
                            returned_memories.append(
                                {
                                    "id": mem_id,
                                    "memory": action_text,
                                    "event": "ADD",
                                    "is_key": is_key_str,
                                }
                            )
                    elif event_type == "DELETE":
                        target_id = temp_uuid_mapping.get(resp.get("id"))
                        if target_id:
                            self._delete_memory(memory_id=target_id)
                            returned_memories.append(
                                {
                                    "id": target_id,
                                    "memory": action_text,
                                    "event": event_type,
                                }
                            )
                    elif event_type == "NONE":
                        logger.debug("NOOP for memory")
                except Exception as e:
                    logger.error(f"Error processing memory action: {e}")
        except Exception as e:
            logger.error(f"Error iterating memory actions: {e}")

        return returned_memories

    def _add_to_graph(self, messages, filters):
        """Add entities to the graph store."""
        added_entities = []
        if self.enable_graph:
            if filters.get("user_id") is None:
                filters["user_id"] = "user"

            data = "\n".join([msg["content"] for msg in messages if "content" in msg and msg["role"] != "system"])
            added_entities = self.graph.add(data, filters)

        return added_entities

    def get_all(
        self,
        *,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 100,
    ):
        """List all memories.

        Args:
            user_id: Filter by user
            agent_id: Filter by agent
            run_id: Filter by run
            filters: Additional filters
            limit: Maximum results

        Returns:
            dict: Results with list of memories
        """
        _, effective_filters = _build_filters_and_metadata(
            user_id=user_id, agent_id=agent_id, run_id=run_id, input_filters=filters
        )

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future_memories = executor.submit(self._get_all_from_vector_store, effective_filters, limit)
            future_graph = executor.submit(self.graph.get_all, effective_filters, limit) if self.enable_graph else None

            futures = [future_memories, future_graph] if future_graph else [future_memories]
            concurrent.futures.wait(futures)

            all_memories = future_memories.result()
            graph_entities = future_graph.result() if future_graph else None

        if self.enable_graph:
            return {"results": all_memories, "relations": graph_entities}

        return {"results": all_memories}

    def _get_all_from_vector_store(self, filters, limit):
        """Get all memories from the vector store."""
        memories_result = self.vector_store.list(filters=filters, limit=limit)

        # Handle different return formats
        if isinstance(memories_result, (tuple, list)) and len(memories_result) > 0:
            first = memories_result[0]
            if isinstance(first, (list, tuple)):
                actual_memories = first
            else:
                actual_memories = memories_result
        else:
            actual_memories = memories_result

        promoted_keys = ["user_id", "agent_id", "run_id", "actor_id", "role"]
        core_keys = {"data", "hash", "created_at", "updated_at", "id", *promoted_keys}

        formatted = []
        for mem in actual_memories:
            item = ClaraMemoryItem(
                id=mem.id,
                memory=mem.payload.get("data", ""),
                hash=mem.payload.get("hash"),
                created_at=mem.payload.get("created_at"),
                updated_at=mem.payload.get("updated_at"),
            ).model_dump(exclude={"score"})

            for key in promoted_keys:
                if key in mem.payload:
                    item[key] = mem.payload[key]

            additional = {k: v for k, v in mem.payload.items() if k not in core_keys}
            if additional:
                item["metadata"] = additional

            formatted.append(item)

        return formatted

    def search(
        self,
        query: str,
        *,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        limit: int = 100,
        filters: Optional[Dict[str, Any]] = None,
        threshold: Optional[float] = None,
    ):
        """Search for memories.

        Args:
            query: Search query
            user_id: Filter by user
            agent_id: Filter by agent
            run_id: Filter by run
            limit: Maximum results
            filters: Additional filters
            threshold: Minimum score threshold

        Returns:
            dict: Results with list of memories
        """
        if not query or not query.strip():
            return {"results": []}

        _, effective_filters = _build_filters_and_metadata(
            user_id=user_id, agent_id=agent_id, run_id=run_id, input_filters=filters
        )

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future_memories = executor.submit(self._search_vector_store, query, effective_filters, limit, threshold)
            future_graph = (
                executor.submit(self.graph.search, query, effective_filters, limit) if self.enable_graph else None
            )

            futures = [future_memories, future_graph] if future_graph else [future_memories]
            concurrent.futures.wait(futures)

            memories = future_memories.result()
            graph_entities = future_graph.result() if future_graph else None

        if self.enable_graph:
            return {"results": memories, "relations": graph_entities}

        return {"results": memories}

    def _search_vector_store(self, query, filters, limit, threshold=None):
        """Search the vector store."""
        embeddings = self.embedding_model.embed(query, "search")
        memories = self.vector_store.search(query=query, vectors=embeddings, limit=limit, filters=filters)

        promoted_keys = ["user_id", "agent_id", "run_id", "actor_id", "role"]
        core_keys = {"data", "hash", "created_at", "updated_at", "id", *promoted_keys}

        results = []
        for mem in memories:
            item = ClaraMemoryItem(
                id=mem.id,
                memory=mem.payload.get("data", ""),
                hash=mem.payload.get("hash"),
                created_at=mem.payload.get("created_at"),
                updated_at=mem.payload.get("updated_at"),
                score=mem.score,
            ).model_dump()

            for key in promoted_keys:
                if key in mem.payload:
                    item[key] = mem.payload[key]

            additional = {k: v for k, v in mem.payload.items() if k not in core_keys}
            if additional:
                item["metadata"] = additional

            if threshold is None or mem.score >= threshold:
                results.append(item)

        return results

    def delete(self, memory_id):
        """Delete a memory by ID.

        Args:
            memory_id: ID of the memory

        Returns:
            dict: Success message
        """
        self._delete_memory(memory_id)
        return {"message": "Memory deleted successfully!"}

    def delete_all(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ):
        """Delete all memories matching the filters.

        Args:
            user_id: Filter by user
            agent_id: Filter by agent
            run_id: Filter by run

        Returns:
            dict: Success message
        """
        filters = {}
        if user_id:
            filters["user_id"] = user_id
        if agent_id:
            filters["agent_id"] = agent_id
        if run_id:
            filters["run_id"] = run_id

        if not filters:
            raise ValueError("At least one filter (user_id, agent_id, run_id) is required.")

        memories = self.vector_store.list(filters=filters)[0]
        for memory in memories:
            self._delete_memory(memory.id)
        self.vector_store.reset()

        logger.info(f"Deleted {len(memories)} memories")

        if self.enable_graph:
            self.graph.delete_all(filters)

        return {"message": "Memories deleted successfully!"}

    def history(self, memory_id):
        """Get the history of a memory.

        Args:
            memory_id: ID of the memory

        Returns:
            list: History records
        """
        if not self.db:
            return []
        return self.db.get_history(memory_id)

    def _create_memory(self, data, existing_embeddings, metadata=None, timestamp=None):
        """Create a new memory entry."""
        logger.debug(f"Creating memory: {data[:100]}...")

        if isinstance(existing_embeddings, dict) and data in existing_embeddings:
            embeddings = existing_embeddings[data]
        elif isinstance(existing_embeddings, list):
            embeddings = existing_embeddings
        else:
            embeddings = self.embedding_model.embed(data, memory_action="add")

        memory_id = str(uuid.uuid4())
        metadata = metadata or {}
        metadata["data"] = data
        metadata["hash"] = hashlib.md5(data.encode()).hexdigest()

        if timestamp is not None:
            metadata["created_at"] = datetime.fromtimestamp(timestamp, pytz.timezone("US/Pacific")).isoformat()
        else:
            metadata["created_at"] = datetime.now(pytz.timezone("US/Pacific")).isoformat()

        self.vector_store.insert(
            vectors=[embeddings],
            ids=[memory_id],
            payloads=[metadata],
        )

        if self.db:
            self.db.add_history(
                memory_id,
                None,
                data,
                "ADD",
                created_at=metadata.get("created_at"),
                actor_id=metadata.get("actor_id"),
                role=metadata.get("role"),
            )

        return memory_id

    def _create_procedural_memory(self, messages, metadata=None, prompt=None):
        """Create a procedural memory from messages."""
        from mypalclara.core.memory.core.prompts import PROCEDURAL_MEMORY_SYSTEM_PROMPT

        logger.info("Creating procedural memory")

        parsed_messages = [
            {"role": "system", "content": prompt or PROCEDURAL_MEMORY_SYSTEM_PROMPT},
            *messages,
            {"role": "user", "content": "Create procedural memory of the above conversation."},
        ]

        try:
            procedural_memory = self.llm.generate_response(messages=parsed_messages)
            procedural_memory = remove_code_blocks(procedural_memory)
        except Exception as e:
            logger.error(f"Error generating procedural memory: {e}")
            raise

        if metadata is None:
            raise ValueError("Metadata required for procedural memory.")

        metadata["memory_type"] = MemoryType.PROCEDURAL.value
        embeddings = self.embedding_model.embed(procedural_memory, memory_action="add")
        memory_id = self._create_memory(procedural_memory, {procedural_memory: embeddings}, metadata=metadata)

        return {"results": [{"id": memory_id, "memory": procedural_memory, "event": "ADD"}]}

    def _update_memory(self, memory_id, data, existing_embeddings, metadata=None):
        """Update an existing memory."""
        logger.info(f"Updating memory {memory_id}")

        try:
            existing_memory = self.vector_store.get(vector_id=memory_id)
        except Exception:
            logger.error(f"Error getting memory {memory_id}")
            raise ValueError(f"Memory {memory_id} not found")

        prev_value = existing_memory.payload.get("data")
        new_metadata = deepcopy(metadata) if metadata else {}

        new_metadata["data"] = data
        new_metadata["hash"] = hashlib.md5(data.encode()).hexdigest()
        new_metadata["created_at"] = existing_memory.payload.get("created_at")
        new_metadata["updated_at"] = datetime.now(pytz.timezone("US/Pacific")).isoformat()

        # Preserve session identifiers
        for key in ["user_id", "agent_id", "run_id", "actor_id", "role"]:
            if key not in new_metadata and key in existing_memory.payload:
                new_metadata[key] = existing_memory.payload[key]

        if isinstance(existing_embeddings, dict) and data in existing_embeddings:
            embeddings = existing_embeddings[data]
        else:
            embeddings = self.embedding_model.embed(data, "update")

        self.vector_store.update(
            vector_id=memory_id,
            vector=embeddings,
            payload=new_metadata,
        )

        if self.db:
            self.db.add_history(
                memory_id,
                prev_value,
                data,
                "UPDATE",
                created_at=new_metadata["created_at"],
                updated_at=new_metadata["updated_at"],
                actor_id=new_metadata.get("actor_id"),
                role=new_metadata.get("role"),
            )

        return memory_id

    def _delete_memory(self, memory_id):
        """Delete a memory."""
        logger.info(f"Deleting memory {memory_id}")
        existing_memory = self.vector_store.get(vector_id=memory_id)
        if not existing_memory or not existing_memory.payload:
            logger.warning(f"Memory {memory_id} not found")
            return None

        prev_value = existing_memory.payload.get("data", "")
        self.vector_store.delete(vector_id=memory_id)

        if self.db:
            self.db.add_history(
                memory_id,
                prev_value,
                None,
                "DELETE",
                actor_id=existing_memory.payload.get("actor_id"),
                role=existing_memory.payload.get("role"),
                is_deleted=1,
            )

        return memory_id


# Simplified config classes (for internal use - full config in config.py)
class ProviderConfig(BaseModel):
    """Configuration for a provider."""

    provider: str
    config: Dict[str, Any]


class ClaraMemoryConfig(BaseModel):
    """Configuration for ClaraMemory."""

    vector_store: ProviderConfig
    llm: ProviderConfig
    embedder: ProviderConfig
    history_db_path: str = os.path.join(clara_memory_dir, "history.db")
    graph_store: Optional[ProviderConfig] = None
    version: str = "v1.1"
    custom_fact_extraction_prompt: Optional[str] = None
    custom_update_memory_prompt: Optional[str] = None
    retrieval_criteria: Optional[List[Dict]] = None


# Add PROCEDURAL_MEMORY_SYSTEM_PROMPT to prompts.py
PROCEDURAL_MEMORY_SYSTEM_PROMPT = """
You are a memory summarization system that records and preserves the complete interaction history between a human and an AI agent. You are provided with the agent's execution history over the past N steps. Your task is to produce a comprehensive summary of the agent's output history that contains every detail necessary for the agent to continue the task without ambiguity.

Overall Structure:
- Overview: Task objective and progress status
- Sequential Agent Actions: Numbered steps with action, result, key findings, and context
"""
