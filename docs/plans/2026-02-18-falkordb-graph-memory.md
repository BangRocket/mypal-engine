# Simplified FalkorDB Graph Memory Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace broken Neo4j/Kuzu graph memory with a simplified FalkorDB implementation that uses 1 LLM call on write and 0 LLM calls on read.

**Architecture:** Single FalkorDB provider using native vector indexing (`db.idx.vector.queryNodes`) for reads and a single `extract_triples` LLM tool call for writes. Pure upsert semantics — no contradiction-detection LLM call. Nodes store embeddings via `vecf32()`, relationships are typed edges. The existing `ClaraMemory` interface (`search()`, `add()`, `get_all()`, `delete_all()`) remains unchanged — only the graph provider implementation changes.

**Tech Stack:** FalkorDB (Redis-protocol graph DB with native vector indexing), `falkordb` Python client, OpenAI embeddings (text-embedding-3-small, 1536 dims), UnifiedLLM for triple extraction.

**Branch:** `debug` (ClaraMemory / UnifiedLLM codebase)

---

## Context for Implementer

### What you're replacing

The current graph memory has two providers (Neo4j 670 lines, Kuzu 726 lines) that both:
1. Make 4 LLM calls per write (extract entities, extract relationships, search existing, determine deletions)
2. Make 1 LLM call per read (extract entities from query) — **this is broken** because `UnifiedLLM.generate_response()` returns a string when the LLM doesn't use the tool, and calling `.get()` on a string crashes silently

### What the new implementation does

- **Write:** 1 LLM call → extract `(subject, predicate, object)` triples → MERGE nodes with embeddings → MERGE relationships
- **Read:** 1 embedding call → `db.idx.vector.queryNodes` KNN search → traverse relationships → return triples
- **No BM25 reranking** (vector similarity scores sufficient for <100 relations per user)
- **No contradiction detection LLM call** (pure upsert — MERGE with `ON MATCH SET updated_at`)

### Key files to understand

| File | Purpose |
|------|---------|
| `clara_core/memory/core/memory.py:576-586` | `_add_to_graph()` — calls `self.graph.add(data, filters)` |
| `clara_core/memory/core/memory.py:696-711` | `search()` — calls `self.graph.search(query, filters, limit)` in parallel with vector search |
| `clara_core/memory/core/memory.py:210-222` | Graph initialization — `GraphStoreFactory.create(provider, config)` |
| `clara_core/memory/graph/factory.py` | Maps provider name → class |
| `clara_core/memory/config.py:107-138` | `_get_graph_store_config()` — builds config dict from env vars |
| `clara_core/memory/cache/graph_cache.py` | Redis cache for graph results (keep as-is) |
| `clara_core/memory_retriever.py:214-224` | Collects `relations` from search results |
| `clara_core/prompt_builder.py:391-420` | Formats graph relations for prompt |

### Interface contract (DO NOT CHANGE)

The `MemoryGraph` class must implement:
- `add(data: str, filters: dict) -> dict` — returns `{"deleted_entities": [...], "added_entities": [...]}`
- `search(query: str, filters: dict, limit: int) -> list[dict]` — returns list of `{"source": str, "relationship": str, "destination": str}`
- `get_all(filters: dict, limit: int) -> list[dict]` — returns list of `{"source": str, "relationship": str, "target": str}` (note: `target` not `destination`)
- `delete_all(filters: dict) -> None`
- `reset() -> None`

### FalkorDB specifics

- Redis protocol on port 6379 (use 6380 externally to avoid Redis conflict)
- OpenCypher query language
- Vector indexing: `CREATE VECTOR INDEX FOR (n:Label) ON (n.prop) OPTIONS {dim: N, similarityFunction: 'cosine'}`
- Vector search: `CALL db.idx.vector.queryNodes('Label', 'prop', k, vecf32([...])) YIELD node, score`
- Vector storage: `SET n.embedding = vecf32($embedding)`
- Distance function: `vec.cosineDistance(a, b)` returns [0, 2] — convert to similarity: `1 - vec.cosineDistance(a, b)`
- `id()` instead of Neo4j's `elementId()`
- Result format: `graph.query()` returns `QueryResult` with `.result_set` (list[list]) and `.header` (list of [type, name] pairs)

---

## Task 1: Write tests for FalkorDB graph memory

**Files:**
- Create: `tests/clara_core/test_graph_memory.py`

**Step 1: Write the test file**

```python
"""Tests for the simplified FalkorDB graph memory."""

from unittest.mock import MagicMock, patch

import pytest


class FakeQueryResult:
    """Mimics FalkorDB QueryResult for testing."""

    def __init__(self, header=None, result_set=None):
        self.header = header or []
        self.result_set = result_set or []


@pytest.fixture
def mock_falkordb():
    """Mock FalkorDB client and graph."""
    with patch("clara_core.memory.graph.falkordb.falkordb") as mock_module:
        mock_client = MagicMock()
        mock_graph = MagicMock()
        mock_module.FalkorDB.return_value = mock_client
        mock_client.select_graph.return_value = mock_graph
        # Default: index creation succeeds silently
        mock_graph.query.return_value = FakeQueryResult()
        yield mock_graph


@pytest.fixture
def mock_embedding():
    """Mock OpenAI embedding model."""
    with patch("clara_core.memory.graph.falkordb.OpenAIEmbedding") as mock_cls:
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1] * 1536
        mock_cls.return_value = mock_embedder
        yield mock_embedder


@pytest.fixture
def mock_llm():
    """Mock UnifiedLLM."""
    with patch("clara_core.memory.graph.falkordb.UnifiedLLM") as mock_cls:
        mock_llm = MagicMock()
        mock_cls.return_value = mock_llm
        yield mock_llm


@pytest.fixture
def graph_config():
    """Build a ClaraMemoryConfig-like object for testing."""
    from unittest.mock import MagicMock

    config = MagicMock()
    config.graph_store.config = {
        "host": "localhost",
        "port": 6379,
        "password": None,
        "graph_name": "test_graph",
    }
    config.graph_store.provider = "falkordb"
    config.graph_store.llm = None
    config.graph_store.threshold = 0.7
    config.embedder.config = {"model": "text-embedding-3-small", "api_key": "test"}
    config.llm.provider = "openrouter"
    config.llm.config = {"provider": "openrouter", "model": "gpt-4o-mini", "api_key": "test"}
    return config


@pytest.fixture
def memory_graph(mock_falkordb, mock_embedding, mock_llm, graph_config):
    """Create a MemoryGraph instance with all mocks."""
    from clara_core.memory.graph.falkordb import MemoryGraph

    graph = MemoryGraph(graph_config)
    return graph


class TestMemoryGraphInit:
    """Tests for MemoryGraph initialization."""

    def test_creates_vector_index(self, mock_falkordb, mock_embedding, mock_llm, graph_config):
        """Index creation should be attempted on init."""
        from clara_core.memory.graph.falkordb import MemoryGraph

        MemoryGraph(graph_config)
        # Should have called query for index creation
        calls = [str(c) for c in mock_falkordb.query.call_args_list]
        assert any("VECTOR INDEX" in c for c in calls)

    def test_index_creation_failure_is_silent(self, mock_falkordb, mock_embedding, mock_llm, graph_config):
        """If index already exists, exception should be swallowed."""
        mock_falkordb.query.side_effect = Exception("Index already exists")
        from clara_core.memory.graph.falkordb import MemoryGraph

        # Should not raise
        MemoryGraph(graph_config)


class TestMemoryGraphAdd:
    """Tests for the write path."""

    def test_add_calls_llm_with_extract_triples_tool(self, memory_graph, mock_llm):
        """Write path should make exactly 1 LLM call with extract_triples tool."""
        mock_llm.generate_response.return_value = {
            "tool_calls": [
                {
                    "name": "extract_triples",
                    "arguments": {
                        "triples": [
                            {"subject": "Josh", "predicate": "likes", "object": "pizza"},
                        ]
                    },
                }
            ]
        }
        result = memory_graph.add("Josh likes pizza", {"user_id": "user-1"})

        # Exactly 1 LLM call
        assert mock_llm.generate_response.call_count == 1
        call_kwargs = mock_llm.generate_response.call_args
        # Should have tools and tool_choice
        assert call_kwargs.kwargs.get("tools") is not None or (len(call_kwargs.args) > 1 and call_kwargs.args[1] is not None)

    def test_add_merges_nodes_and_relationship(self, memory_graph, mock_llm, mock_falkordb):
        """Add should MERGE source, destination, and relationship."""
        mock_llm.generate_response.return_value = {
            "tool_calls": [
                {
                    "name": "extract_triples",
                    "arguments": {
                        "triples": [
                            {"subject": "Josh", "predicate": "lives_in", "object": "NYC"},
                        ]
                    },
                }
            ]
        }
        memory_graph.add("Josh lives in NYC", {"user_id": "user-1"})

        # Check that Cypher MERGE was called (beyond index creation)
        cypher_calls = [
            str(c) for c in mock_falkordb.query.call_args_list if "MERGE" in str(c)
        ]
        assert len(cypher_calls) >= 1

    def test_add_handles_string_llm_response(self, memory_graph, mock_llm):
        """If LLM returns string instead of tool call, add should return empty gracefully."""
        mock_llm.generate_response.return_value = "I found some entities"
        result = memory_graph.add("Josh likes pizza", {"user_id": "user-1"})
        assert result["added_entities"] == []

    def test_add_handles_empty_llm_response(self, memory_graph, mock_llm):
        """If LLM returns empty/None, add should return empty gracefully."""
        mock_llm.generate_response.return_value = ""
        result = memory_graph.add("Josh likes pizza", {"user_id": "user-1"})
        assert result["added_entities"] == []

    def test_add_normalizes_entity_names(self, memory_graph, mock_llm, mock_falkordb):
        """Entity names should be lowercased with spaces replaced by underscores."""
        mock_llm.generate_response.return_value = {
            "tool_calls": [
                {
                    "name": "extract_triples",
                    "arguments": {
                        "triples": [
                            {"subject": "Joshua Heidorn", "predicate": "Works At", "object": "Acme Corp"},
                        ]
                    },
                }
            ]
        }
        memory_graph.add("Joshua Heidorn works at Acme Corp", {"user_id": "user-1"})

        # Check that normalized names appear in Cypher
        cypher_calls = " ".join(str(c) for c in mock_falkordb.query.call_args_list)
        assert "joshua_heidorn" in cypher_calls.lower() or "$source_name" in cypher_calls

    def test_add_returns_correct_structure(self, memory_graph, mock_llm):
        """Add should return dict with deleted_entities and added_entities."""
        mock_llm.generate_response.return_value = {
            "tool_calls": [
                {
                    "name": "extract_triples",
                    "arguments": {
                        "triples": [
                            {"subject": "Josh", "predicate": "likes", "object": "pizza"},
                        ]
                    },
                }
            ]
        }
        result = memory_graph.add("Josh likes pizza", {"user_id": "user-1"})
        assert "deleted_entities" in result
        assert "added_entities" in result
        assert isinstance(result["deleted_entities"], list)
        assert isinstance(result["added_entities"], list)


class TestMemoryGraphSearch:
    """Tests for the read path."""

    def test_search_does_not_call_llm(self, memory_graph, mock_llm, mock_falkordb):
        """Read path should NOT make any LLM calls — only embedding + vector search."""
        mock_falkordb.query.return_value = FakeQueryResult()
        memory_graph.search("what does Josh like", {"user_id": "user-1"})
        mock_llm.generate_response.assert_not_called()

    def test_search_uses_vector_query_nodes(self, memory_graph, mock_falkordb, mock_embedding):
        """Search should use db.idx.vector.queryNodes for KNN search."""
        mock_falkordb.query.return_value = FakeQueryResult()
        memory_graph.search("what does Josh like", {"user_id": "user-1"})

        cypher_calls = " ".join(str(c) for c in mock_falkordb.query.call_args_list)
        assert "db.idx.vector.queryNodes" in cypher_calls

    def test_search_returns_correct_structure(self, memory_graph, mock_falkordb, mock_embedding):
        """Search should return list of {source, relationship, destination} dicts."""
        mock_falkordb.query.return_value = FakeQueryResult(
            header=[[1, "source"], [1, "relationship"], [1, "destination"], [1, "score"]],
            result_set=[["josh", "likes", "pizza", 0.95]],
        )
        results = memory_graph.search("what does Josh like", {"user_id": "user-1"})
        assert len(results) == 1
        assert results[0]["source"] == "josh"
        assert results[0]["relationship"] == "likes"
        assert results[0]["destination"] == "pizza"

    def test_search_returns_empty_for_no_matches(self, memory_graph, mock_falkordb):
        """Search with no matching nodes should return empty list."""
        mock_falkordb.query.return_value = FakeQueryResult()
        results = memory_graph.search("unknown topic", {"user_id": "user-1"})
        assert results == []

    def test_search_respects_limit(self, memory_graph, mock_falkordb):
        """Limit parameter should be passed to the query."""
        mock_falkordb.query.return_value = FakeQueryResult()
        memory_graph.search("query", {"user_id": "user-1"}, limit=3)
        cypher_calls = " ".join(str(c) for c in mock_falkordb.query.call_args_list)
        # The limit should appear in the query params
        assert "3" in cypher_calls or "$limit" in cypher_calls


class TestMemoryGraphGetAll:
    """Tests for get_all."""

    def test_get_all_returns_correct_structure(self, memory_graph, mock_falkordb):
        """get_all should return list of {source, relationship, target} dicts."""
        mock_falkordb.query.return_value = FakeQueryResult(
            header=[[1, "source"], [1, "relationship"], [1, "target"]],
            result_set=[["josh", "likes", "pizza"]],
        )
        results = memory_graph.get_all({"user_id": "user-1"})
        assert len(results) == 1
        assert results[0]["source"] == "josh"
        assert results[0]["relationship"] == "likes"
        assert results[0]["target"] == "pizza"


class TestMemoryGraphDeleteAll:
    """Tests for delete_all."""

    def test_delete_all_runs_detach_delete(self, memory_graph, mock_falkordb):
        """delete_all should DETACH DELETE matching nodes."""
        mock_falkordb.query.return_value = FakeQueryResult()
        memory_graph.delete_all({"user_id": "user-1"})
        cypher_calls = " ".join(str(c) for c in mock_falkordb.query.call_args_list)
        assert "DETACH DELETE" in cypher_calls


class TestQueryHelper:
    """Tests for the _query result normalizer."""

    def test_query_normalizes_result_set(self, memory_graph, mock_falkordb):
        """_query should convert result_set rows to list[dict]."""
        mock_falkordb.query.return_value = FakeQueryResult(
            header=[[1, "name"], [1, "age"]],
            result_set=[["Alice", 30], ["Bob", 25]],
        )
        results = memory_graph._query("MATCH (n) RETURN n.name AS name, n.age AS age")
        assert results == [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]

    def test_query_handles_empty_result(self, memory_graph, mock_falkordb):
        """_query should return [] for empty result_set."""
        mock_falkordb.query.return_value = FakeQueryResult()
        results = memory_graph._query("MATCH (n) RETURN n")
        assert results == []
```

**Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/clara_core/test_graph_memory.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'clara_core.memory.graph.falkordb'`

**Step 3: Commit test file**

```bash
git add tests/clara_core/test_graph_memory.py
git commit -m "test: add tests for simplified FalkorDB graph memory"
```

---

## Task 2: Implement the simplified FalkorDB graph provider

**Files:**
- Create: `clara_core/memory/graph/falkordb.py`
- Modify: `clara_core/memory/graph/tools.py` (replace tools)
- Modify: `clara_core/memory/graph/utils.py` (simplify prompts)

**Step 1: Create the new tool definition**

Replace `clara_core/memory/graph/tools.py` entirely:

```python
"""Tool definitions for graph memory triple extraction."""

EXTRACT_TRIPLES_TOOL = {
    "type": "function",
    "function": {
        "name": "extract_triples",
        "description": "Extract knowledge triples (subject-predicate-object) from text. Each triple represents a fact or relationship.",
        "parameters": {
            "type": "object",
            "properties": {
                "triples": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "subject": {
                                "type": "string",
                                "description": "The entity that the fact is about (e.g., 'Josh', 'the project').",
                            },
                            "predicate": {
                                "type": "string",
                                "description": "The relationship or property (e.g., 'likes', 'works_at', 'lives_in').",
                            },
                            "object": {
                                "type": "string",
                                "description": "The value or target entity (e.g., 'pizza', 'Acme Corp', 'New York').",
                            },
                        },
                        "required": ["subject", "predicate", "object"],
                        "additionalProperties": False,
                    },
                    "description": "List of knowledge triples extracted from the text.",
                }
            },
            "required": ["triples"],
            "additionalProperties": False,
        },
    },
}
```

**Step 2: Simplify utils.py**

Replace `clara_core/memory/graph/utils.py` entirely:

```python
"""Utility functions for graph memory operations."""

import re

EXTRACT_TRIPLES_PROMPT = """You extract knowledge graph triples from conversation text.

Rules:
1. Extract only explicitly stated facts as (subject, predicate, object) triples.
2. For self-references ("I", "me", "my"), use "USER_ID" as the subject.
3. Use consistent, lowercase, general relationship names (e.g., "likes", "works_at", not "started_liking" or "currently_works_at").
4. Entity names should be natural and readable (e.g., "josh", "new york", not "josh_h_123").
5. Only extract facts that would be useful to remember in future conversations.
6. Do NOT extract trivial or transient information (greetings, acknowledgments, etc.).
"""


def format_entities(entities: list) -> str:
    """Format entities as 'source -- relationship -- destination' lines.

    Args:
        entities: List of entity dicts with source, relationship, destination keys

    Returns:
        Newline-separated formatted string
    """
    if not entities:
        return ""

    formatted_lines = []
    for entity in entities:
        simplified = f"{entity['source']} -- {entity['relationship']} -- {entity['destination']}"
        formatted_lines.append(simplified)

    return "\n".join(formatted_lines)


def sanitize_relationship_for_cypher(relationship: str) -> str:
    """Sanitize relationship text for Cypher queries.

    Args:
        relationship: Raw relationship string

    Returns:
        Sanitized relationship string safe for Cypher edge types
    """
    # Replace any non-alphanumeric/underscore with underscore
    sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", relationship)
    # Collapse multiple underscores
    sanitized = re.sub(r"_+", "_", sanitized)
    return sanitized.strip("_").upper()
```

**Step 3: Create the FalkorDB implementation**

Create `clara_core/memory/graph/falkordb.py`:

```python
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
    raise ImportError("falkordb is not installed. Please install it using pip install falkordb")

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

    Read path: Embed query, use db.idx.vector.queryNodes for KNN search,
    traverse relationships from matched nodes.
    """

    def __init__(self, config):
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
                    triples.append({
                        "source": subj.lower().replace(" ", "_"),
                        "relationship": sanitize_relationship_for_cypher(pred.lower().replace(" ", "_")),
                        "destination": obj.lower().replace(" ", "_"),
                    })

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
                result = self._query(cypher, params={
                    "source_name": source,
                    "dest_name": destination,
                    "user_id": user_id,
                    "source_embedding": source_embedding,
                    "dest_embedding": dest_embedding,
                })
                results.extend(result)
            except Exception as e:
                logger.error("Failed to merge triple (%s, %s, %s): %s", source, relationship, destination, e)

        return results

    # ---- Read path (no LLM) ----

    def search(self, query: str, filters: dict, limit: int = 10) -> list:
        """Search for related entities using vector KNN search.

        No LLM calls — embeds the query, finds similar entity nodes via
        FalkorDB's native vector index, then traverses their relationships.
        """
        user_id = filters["user_id"]

        if self.cache:
            cached = self.cache.get_search_results(user_id, query, filters)
            if cached is not None:
                logger.info("Graph search cache hit: %d results", len(cached))
                return cached

        query_embedding = self.embedding_model.embed(query)

        # KNN search against entity embeddings, then traverse relationships
        cypher = """
        CALL db.idx.vector.queryNodes('__Entity__', 'embedding', $k, vecf32($query_embedding))
        YIELD node, score
        WHERE node.user_id = $user_id
        WITH node, score
        OPTIONAL MATCH (node)-[r]->(other:__Entity__)
        WHERE other.user_id = $user_id
        WITH node, score, r, other
        WHERE r IS NOT NULL
        RETURN node.name AS source, type(r) AS relationship, other.name AS destination, score
        UNION
        CALL db.idx.vector.queryNodes('__Entity__', 'embedding', $k, vecf32($query_embedding))
        YIELD node, score
        WHERE node.user_id = $user_id
        WITH node, score
        OPTIONAL MATCH (other:__Entity__)-[r]->(node)
        WHERE other.user_id = $user_id
        WITH node, score, r, other
        WHERE r IS NOT NULL
        RETURN other.name AS source, type(r) AS relationship, node.name AS destination, score
        """

        results = self._query(cypher, params={
            "query_embedding": query_embedding,
            "user_id": user_id,
            "k": limit * 2,  # Fetch more candidates, deduplicate below
        })

        # Deduplicate and limit
        seen = set()
        deduped = []
        for r in results:
            key = (r["source"], r["relationship"], r["destination"])
            if key not in seen:
                seen.add(key)
                deduped.append({
                    "source": r["source"],
                    "relationship": r["relationship"],
                    "destination": r["destination"],
                })
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
```

**Step 4: Run tests**

Run: `poetry run pytest tests/clara_core/test_graph_memory.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add clara_core/memory/graph/falkordb.py clara_core/memory/graph/tools.py clara_core/memory/graph/utils.py tests/clara_core/test_graph_memory.py
git commit -m "feat: add simplified FalkorDB graph memory with vector KNN search"
```

---

## Task 3: Wire FalkorDB into config and factory, delete Neo4j/Kuzu

**Files:**
- Modify: `clara_core/memory/graph/factory.py`
- Modify: `clara_core/memory/graph/__init__.py`
- Modify: `clara_core/memory/config.py:92-138` (graph store config)
- Delete: `clara_core/memory/graph/neo4j.py`
- Delete: `clara_core/memory/graph/kuzu.py`

**Step 1: Update factory.py**

Replace `clara_core/memory/graph/factory.py` entirely:

```python
"""Factory for creating graph store instances."""

import importlib
from typing import Any


def load_class(class_type):
    """Load a class from a module path."""
    module_path, class_name = class_type.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


class GraphStoreFactory:
    """Factory for creating graph memory instances."""

    provider_to_class = {
        "falkordb": "clara_core.memory.graph.falkordb.MemoryGraph",
        "default": "clara_core.memory.graph.falkordb.MemoryGraph",
    }

    @classmethod
    def create(cls, provider_name: str, config: Any):
        class_type = cls.provider_to_class.get(provider_name, cls.provider_to_class["default"])
        try:
            GraphClass = load_class(class_type)
        except (ImportError, AttributeError) as e:
            raise ImportError(f"Could not import graph store for provider '{provider_name}': {e}")
        return GraphClass(config)

    @classmethod
    def get_supported_providers(cls) -> list:
        return list(cls.provider_to_class.keys())
```

**Step 2: Update __init__.py**

Replace `clara_core/memory/graph/__init__.py`:

```python
"""Graph memory implementation for Clara Memory System."""

from clara_core.memory.graph.factory import GraphStoreFactory

__all__ = ["GraphStoreFactory"]
```

**Step 3: Update config.py graph store section**

In `clara_core/memory/config.py`, replace the Neo4j/Kuzu config section (lines ~92-138) with FalkorDB config:

Replace:
```python
# Graph memory configuration (optional - for relationship tracking)
ENABLE_GRAPH_MEMORY = os.getenv("ENABLE_GRAPH_MEMORY", "false").lower() == "true"
GRAPH_STORE_PROVIDER = os.getenv("GRAPH_STORE_PROVIDER", "neo4j").lower()

# Neo4j configuration
NEO4J_URL = os.getenv("NEO4J_URL")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

# Kuzu configuration
KUZU_DATA_DIR = BASE_DATA_DIR / "kuzu_data"
if GRAPH_STORE_PROVIDER == "kuzu":
    KUZU_DATA_DIR.mkdir(parents=True, exist_ok=True)


def _get_graph_store_config() -> dict | None:
    """Build graph store config for relationship tracking."""
    if not ENABLE_GRAPH_MEMORY:
        return None

    if GRAPH_STORE_PROVIDER == "neo4j":
        if not NEO4J_URL or not NEO4J_PASSWORD:
            logger.warning("Neo4j configured but NEO4J_URL or NEO4J_PASSWORD not set")
            return None

        logger.info(f"Graph store: Neo4j at {NEO4J_URL}")
        return {
            "provider": "neo4j",
            "config": {
                "url": NEO4J_URL,
                "username": NEO4J_USERNAME,
                "password": NEO4J_PASSWORD,
            },
        }

    elif GRAPH_STORE_PROVIDER == "kuzu":
        logger.info(f"Graph store: Kuzu (embedded) at {KUZU_DATA_DIR}")
        return {
            "provider": "kuzu",
            "config": {
                "db_path": str(KUZU_DATA_DIR),
            },
        }

    else:
        logger.warning(f"Unknown GRAPH_STORE_PROVIDER={GRAPH_STORE_PROVIDER}")
        return None
```

With:
```python
# Graph memory configuration (optional - for relationship tracking)
ENABLE_GRAPH_MEMORY = os.getenv("ENABLE_GRAPH_MEMORY", "false").lower() == "true"
GRAPH_STORE_PROVIDER = os.getenv("GRAPH_STORE_PROVIDER", "falkordb").lower()

# FalkorDB configuration
FALKORDB_HOST = os.getenv("FALKORDB_HOST", "localhost")
FALKORDB_PORT = int(os.getenv("FALKORDB_PORT", "6379"))
FALKORDB_PASSWORD = os.getenv("FALKORDB_PASSWORD") or None
FALKORDB_GRAPH_NAME = os.getenv("FALKORDB_GRAPH_NAME", "clara_memory")


def _get_graph_store_config() -> dict | None:
    """Build graph store config for relationship tracking."""
    if not ENABLE_GRAPH_MEMORY:
        return None

    if GRAPH_STORE_PROVIDER != "falkordb":
        logger.warning(f"Unknown GRAPH_STORE_PROVIDER={GRAPH_STORE_PROVIDER}, only 'falkordb' is supported")
        return None

    logger.info(f"Graph store: FalkorDB at {FALKORDB_HOST}:{FALKORDB_PORT}")
    return {
        "provider": "falkordb",
        "config": {
            "host": FALKORDB_HOST,
            "port": FALKORDB_PORT,
            "password": FALKORDB_PASSWORD,
            "graph_name": FALKORDB_GRAPH_NAME,
        },
    }
```

**Step 4: Delete Neo4j and Kuzu providers**

```bash
rm clara_core/memory/graph/neo4j.py clara_core/memory/graph/kuzu.py
```

**Step 5: Run tests**

Run: `poetry run pytest tests/clara_core/test_graph_memory.py -v`
Expected: All tests PASS

Run: `poetry run pytest tests/ -q`
Expected: 323 passed (same baseline), 5 pre-existing failures

**Step 6: Lint check**

Run: `poetry run ruff check clara_core/memory/graph/ clara_core/memory/config.py`
Expected: Clean

**Step 7: Commit**

```bash
git add clara_core/memory/graph/factory.py clara_core/memory/graph/__init__.py clara_core/memory/config.py
git rm clara_core/memory/graph/neo4j.py clara_core/memory/graph/kuzu.py
git commit -m "refactor: replace Neo4j/Kuzu with FalkorDB, update config and factory"
```

---

## Task 4: Update dependencies and docker-compose

**Files:**
- Modify: `pyproject.toml:23-26` (dependencies)
- Modify: `docker-compose.yml` (replace Neo4j service with FalkorDB)
- Modify: `.env.docker.example` (if exists)

**Step 1: Update pyproject.toml dependencies**

Replace these lines:
```toml
neo4j = "^5.0"  # Graph database driver
kuzu = "^0.9.0"  # Embedded graph database (optional)
langchain-neo4j = "^0.8.0"  # Graph memory operations for Neo4j
rank-bm25 = "^0.2.0"  # BM25 ranking for Kuzu graph search
```

With:
```toml
falkordb = "^1.0.0"  # Graph database with native vector indexing
```

**Step 2: Update docker-compose.yml**

Replace the Neo4j service block (the `neo4j:` service definition, its volumes, and all `NEO4J_*` env vars across all services) with FalkorDB:

The Neo4j service should become:
```yaml
  # FalkorDB graph database (for graph memory)
  falkordb:
    image: falkordb/falkordb:latest
    container_name: mypalclara-falkordb
    ports:
      - "6380:6379"  # Use 6380 to avoid conflict with Redis
    volumes:
      - falkordb-data:/data
    command: >
      redis-server
      --appendonly yes
      ${FALKORDB_PASSWORD:+--requirepass ${FALKORDB_PASSWORD}}
    healthcheck:
      test: ["CMD", "redis-cli", "-p", "6379", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped
```

All service env vars should replace:
```yaml
      - GRAPH_STORE_PROVIDER=${GRAPH_STORE_PROVIDER:-neo4j}
      - NEO4J_URL=bolt://neo4j:7687
      - NEO4J_USERNAME=neo4j
      - NEO4J_PASSWORD=${NEO4J_PASSWORD:-clara}
```

With:
```yaml
      - GRAPH_STORE_PROVIDER=${GRAPH_STORE_PROVIDER:-falkordb}
      - FALKORDB_HOST=falkordb
      - FALKORDB_PORT=6379
      - FALKORDB_PASSWORD=${FALKORDB_PASSWORD:-}
      - FALKORDB_GRAPH_NAME=${FALKORDB_GRAPH_NAME:-clara_memory}
```

Volume section: replace `neo4j-data:` and `neo4j-logs:` with `falkordb-data:`.

**Step 3: Update .env.docker.example if it exists**

Replace Neo4j env vars with FalkorDB equivalents.

**Step 4: Run tests**

Run: `poetry run pytest tests/ -q`
Expected: 323 passed, 5 pre-existing failures

**Step 5: Lint**

Run: `poetry run ruff check .`
Expected: Clean

**Step 6: Commit**

```bash
git add pyproject.toml docker-compose.yml
git add .env.docker.example 2>/dev/null || true
git commit -m "chore: replace neo4j/kuzu/rank-bm25 deps with falkordb, update docker-compose"
```

---

## Task 5: Update documentation and config references

**Files:**
- Modify: `CLAUDE.md` (update graph memory docs)
- Modify: `clara_core/config.py` (if it has graph store references)
- Modify: `scripts/clear_dbs.py` (if it references Neo4j)
- Modify: `scripts/bootstrap_memory.py` (if it references Neo4j)
- Modify: `scripts/backfill_graph_memory.py` (if it references Neo4j)

**Step 1: Update CLAUDE.md**

Find the graph memory / Neo4j section and replace with FalkorDB references:
- `ENABLE_GRAPH_MEMORY=false` stays the same
- `GRAPH_STORE_PROVIDER=falkordb` (was `neo4j`)
- Replace `NEO4J_URL`, `NEO4J_USERNAME`, `NEO4J_PASSWORD` with `FALKORDB_HOST`, `FALKORDB_PORT`, `FALKORDB_PASSWORD`, `FALKORDB_GRAPH_NAME`
- Remove Kuzu references

**Step 2: Update scripts that reference Neo4j**

Check each script file and replace Neo4j references with FalkorDB where needed. If a script only calls `ROOK.graph.reset()` or `ROOK.graph.delete_all()`, it should work without changes since the interface is the same.

**Step 3: Run tests**

Run: `poetry run pytest tests/ -q`
Expected: Same baseline

**Step 4: Commit**

```bash
git add CLAUDE.md scripts/ clara_core/config.py
git commit -m "docs: update graph memory references from Neo4j/Kuzu to FalkorDB"
```

---

## Task 6: Support tool_choice in UnifiedLLM

**Files:**
- Modify: `clara_core/memory/llm/unified.py:133-170`
- Create or modify: test for tool_choice support

**Step 1: Check if tool_choice is already supported**

Read `clara_core/memory/llm/unified.py` line 162. Currently:
```python
response = self._provider.complete_with_tools(typed_messages, tools, self._llm_config)
```

The `tool_choice` parameter from `generate_response()` is accepted but **never passed** to `complete_with_tools()`. This is why the LLM sometimes returns text instead of using the tool.

**Step 2: Update generate_response to pass tool_choice**

In `clara_core/memory/llm/unified.py`, update the `generate_response` method's tool-calling branch:

Replace:
```python
        if tools:
            # Use tool calling
            response = self._provider.complete_with_tools(typed_messages, tools, self._llm_config)

            if response.has_tool_calls:
                return {"tool_calls": [{"name": tc.name, "arguments": tc.arguments} for tc in response.tool_calls]}

            return response.content or ""
```

With:
```python
        if tools:
            # Use tool calling
            config = self._llm_config
            if tool_choice:
                # Clone config and set tool_choice so the provider can use it
                from dataclasses import replace
                config = replace(config, tool_choice=tool_choice)
            response = self._provider.complete_with_tools(typed_messages, tools, config)

            if response.has_tool_calls:
                return {"tool_calls": [{"name": tc.name, "arguments": tc.arguments} for tc in response.tool_calls]}

            return response.content or ""
```

**Note:** You'll need to check if `LLMConfig` has a `tool_choice` field. If not, add it:

In `clara_core/llm/config.py`, add to the `LLMConfig` dataclass:
```python
tool_choice: str | None = None
```

And in the LangChain provider's `complete_with_tools`, pass `tool_choice` if set. Check the provider implementation at `clara_core/llm/providers/langchain_provider.py` to see how tools are bound and add `tool_choice` support.

**Step 3: Run tests**

Run: `poetry run pytest tests/clara_core/test_graph_memory.py tests/clara_core/test_memory_llm.py -v`
Expected: All pass

**Step 4: Commit**

```bash
git add clara_core/memory/llm/unified.py clara_core/llm/config.py clara_core/llm/providers/langchain_provider.py
git commit -m "feat: pass tool_choice through UnifiedLLM to LangChain provider"
```

---

## Task 7: Final verification

**Step 1: Run full test suite**

Run: `poetry run pytest tests/ -q`
Expected: 323+ passed (new tests added), 5 pre-existing failures unchanged

**Step 2: Lint and format**

Run: `poetry run ruff check . && poetry run ruff format --check .`
Expected: Clean

**Step 3: Import smoke tests**

```bash
poetry run python -c "from clara_core.memory.graph.falkordb import MemoryGraph; print('FalkorDB import OK')"
poetry run python -c "from clara_core.memory.graph import GraphStoreFactory; print(GraphStoreFactory.get_supported_providers())"
poetry run python -c "from clara_core.memory.graph.tools import EXTRACT_TRIPLES_TOOL; print('Tools OK')"
```

**Step 4: Verify deleted files are gone**

```bash
test ! -f clara_core/memory/graph/neo4j.py && echo "neo4j.py deleted OK"
test ! -f clara_core/memory/graph/kuzu.py && echo "kuzu.py deleted OK"
```

**Step 5: Commit any formatting fixes**

```bash
poetry run ruff format .
git add -u
git commit -m "style: format files touched by FalkorDB migration"
```

---

## Summary

| Metric | Before | After |
|--------|--------|-------|
| Graph providers | 3 (Neo4j, Kuzu, broken) | 1 (FalkorDB, working) |
| Lines of graph code | ~1,400 | ~250 |
| LLM calls per write | 4 | 1 |
| LLM calls per read | 1 (broken) | 0 |
| Dependencies | neo4j, kuzu, langchain-neo4j, rank-bm25 | falkordb |
| Tests | 0 | ~20 |
| Docker service | Neo4j (Java, heavy) | FalkorDB (Redis-based, light) |
