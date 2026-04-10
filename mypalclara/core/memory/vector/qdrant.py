"""Qdrant vector store implementation."""

import logging
import os
import shutil
import threading
import time

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointIdsList,
    PointStruct,
    Range,
    VectorParams,
)

from mypalclara.core.memory.vector.base import VectorStoreBase

logger = logging.getLogger("clara.memory.vector.qdrant")

# Circuit breaker defaults
_CB_THRESHOLD = int(os.getenv("QDRANT_CB_THRESHOLD", "3"))
_CB_COOLDOWN = float(os.getenv("QDRANT_CB_COOLDOWN", "30"))


class Qdrant(VectorStoreBase):
    """Qdrant vector store implementation."""

    def __init__(
        self,
        collection_name: str,
        embedding_model_dims: int,
        client: QdrantClient = None,
        host: str = None,
        port: int = None,
        path: str = None,
        url: str = None,
        api_key: str = None,
        on_disk: bool = False,
    ):
        """Initialize the Qdrant vector store.

        Args:
            collection_name: Name of the collection.
            embedding_model_dims: Dimensions of the embedding model.
            client: Existing Qdrant client instance.
            host: Host address for Qdrant server.
            port: Port for Qdrant server.
            path: Path for local Qdrant database.
            url: Full URL for Qdrant server.
            api_key: API key for Qdrant server.
            on_disk: Enables persistent storage.
        """
        if client:
            self.client = client
            self.is_local = False
        else:
            params = {}
            if api_key:
                params["api_key"] = api_key
            if url:
                params["url"] = url
            if host and port:
                params["host"] = host
                params["port"] = port

            if not params:
                params["path"] = path
                self.is_local = True
                if not on_disk:
                    if os.path.exists(path) and os.path.isdir(path):
                        shutil.rmtree(path)
            else:
                self.is_local = False

            timeout = int(os.getenv("QDRANT_TIMEOUT", "30"))
            self.client = QdrantClient(**params, timeout=timeout)

        self.collection_name = collection_name
        self.embedding_model_dims = embedding_model_dims
        self.on_disk = on_disk
        self.create_col(embedding_model_dims, on_disk)

        # Circuit breaker state (accessed from ThreadPoolExecutor threads)
        self._cb_lock = threading.Lock()
        self._cb_failures = 0
        self._cb_open_until = 0.0

    def create_col(self, vector_size: int, on_disk: bool = False, distance: Distance = Distance.COSINE):
        """Create a new collection."""
        response = self.list_cols()
        for collection in response.collections:
            if collection.name == self.collection_name:
                logger.debug(f"Collection {self.collection_name} already exists. Skipping creation.")
                self._create_filter_indexes()
                return

        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=VectorParams(size=vector_size, distance=distance, on_disk=on_disk),
        )
        self._create_filter_indexes()

    def _create_filter_indexes(self):
        """Create indexes for commonly used filter fields."""
        if self.is_local:
            logger.debug("Skipping payload index creation for local Qdrant (not supported)")
            return

        common_fields = ["user_id", "agent_id", "run_id", "actor_id", "visibility"]

        for field in common_fields:
            try:
                self.client.create_payload_index(
                    collection_name=self.collection_name, field_name=field, field_schema="keyword"
                )
                logger.info(f"Created index for {field} in collection {self.collection_name}")
            except Exception as e:
                logger.debug(f"Index for {field} might already exist: {e}")

    def insert(self, vectors: list, payloads: list = None, ids: list = None):
        """Insert vectors into a collection."""
        logger.info(f"Inserting {len(vectors)} vectors into collection {self.collection_name}")
        points = [
            PointStruct(
                id=idx if ids is None else ids[idx],
                vector=vector,
                payload=payloads[idx] if payloads else {},
            )
            for idx, vector in enumerate(vectors)
        ]
        self.client.upsert(collection_name=self.collection_name, points=points)

    def _create_filter(self, filters: dict) -> Filter:
        """Create a Filter object from the provided filters."""
        if not filters:
            return None

        conditions = []
        for key, value in filters.items():
            # Skip None values - can't filter on null
            if value is None:
                continue
            if isinstance(value, dict) and "gte" in value and "lte" in value:
                conditions.append(FieldCondition(key=key, range=Range(gte=value["gte"], lte=value["lte"])))
            else:
                conditions.append(FieldCondition(key=key, match=MatchValue(value=value)))
        return Filter(must=conditions) if conditions else None

    def _cb_is_open(self) -> bool:
        """Check if circuit breaker is open (should skip calls)."""
        with self._cb_lock:
            if self._cb_failures < _CB_THRESHOLD:
                return False
            if time.monotonic() >= self._cb_open_until:
                # Cooldown expired — allow a probe attempt
                logger.info("[Qdrant] Circuit breaker half-open, allowing probe")
                return False
            return True

    def _cb_record_success(self) -> None:
        """Record a successful call, resetting the breaker."""
        with self._cb_lock:
            if self._cb_failures > 0:
                logger.info("[Qdrant] Circuit breaker reset after success")
            self._cb_failures = 0
            self._cb_open_until = 0.0

    def _cb_record_failure(self) -> None:
        """Record a failed call, potentially opening the breaker."""
        with self._cb_lock:
            self._cb_failures += 1
            if self._cb_failures >= _CB_THRESHOLD:
                self._cb_open_until = time.monotonic() + _CB_COOLDOWN
                logger.warning(
                    f"[Qdrant] Circuit breaker OPEN after {self._cb_failures} failures, "
                    f"cooling down for {_CB_COOLDOWN}s"
                )

    def search(self, query: str, vectors: list, limit: int = 5, filters: dict = None) -> list:
        """Search for similar vectors."""
        if self._cb_is_open():
            logger.warning("[Qdrant] Circuit breaker open, returning empty results")
            return []
        try:
            query_filter = self._create_filter(filters) if filters else None
            hits = self.client.query_points(
                collection_name=self.collection_name,
                query=vectors,
                query_filter=query_filter,
                limit=limit,
            )
            self._cb_record_success()
            return hits.points
        except Exception as e:
            self._cb_record_failure()
            logger.error(f"[Qdrant] Search failed: {e}")
            return []

    def delete(self, vector_id: int):
        """Delete a vector by ID."""
        self.client.delete(
            collection_name=self.collection_name,
            points_selector=PointIdsList(
                points=[vector_id],
            ),
        )

    def update(self, vector_id: int, vector: list = None, payload: dict = None):
        """Update a vector and its payload."""
        point = PointStruct(id=vector_id, vector=vector, payload=payload)
        self.client.upsert(collection_name=self.collection_name, points=[point])

    def update_payload(self, vector_id: int, payload: dict = None):
        """Merge payload keys into an existing point without changing its vector."""
        self.client.set_payload(
            collection_name=self.collection_name,
            payload=payload,
            points=[vector_id],
        )

    def get(self, vector_id: int) -> dict:
        """Retrieve a vector by ID."""
        result = self.client.retrieve(collection_name=self.collection_name, ids=[vector_id], with_payload=True)
        return result[0] if result else None

    def list_cols(self) -> list:
        """List all collections."""
        return self.client.get_collections()

    def delete_col(self):
        """Delete a collection."""
        self.client.delete_collection(collection_name=self.collection_name)

    def col_info(self) -> dict:
        """Get information about a collection."""
        return self.client.get_collection(collection_name=self.collection_name)

    def list(self, filters: dict = None, limit: int = 100) -> list:
        """List all vectors in a collection."""
        if self._cb_is_open():
            logger.warning("[Qdrant] Circuit breaker open, returning empty results for list")
            return ([], None)
        try:
            query_filter = self._create_filter(filters) if filters else None
            result = self.client.scroll(
                collection_name=self.collection_name,
                scroll_filter=query_filter,
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
            self._cb_record_success()
            return result
        except Exception as e:
            self._cb_record_failure()
            logger.error(f"[Qdrant] List/scroll failed: {e}")
            return ([], None)

    def reset(self):
        """Reset the index by deleting and recreating it."""
        logger.warning(f"Resetting index {self.collection_name}...")
        self.delete_col()
        self.create_col(self.embedding_model_dims, self.on_disk)
