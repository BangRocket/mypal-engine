"""PostgreSQL with pgvector vector store implementation."""

import json
import logging
from contextlib import contextmanager
from typing import Any, List, Optional

from pydantic import BaseModel

from clara_core.memory.vector.base import VectorStoreBase

# Try to import psycopg (psycopg3) first, then fall back to psycopg2
try:
    from psycopg.types.json import Json
    from psycopg_pool import ConnectionPool
    PSYCOPG_VERSION = 3
except ImportError:
    try:
        from psycopg2.extras import Json, execute_values
        from psycopg2.pool import ThreadedConnectionPool as ConnectionPool
        PSYCOPG_VERSION = 2
    except ImportError:
        raise ImportError(
            "Neither 'psycopg' nor 'psycopg2' library is available. "
            "Please install one of them using 'pip install psycopg[pool]' or 'pip install psycopg2'"
        )

logger = logging.getLogger("clara.memory.vector.pgvector")


class OutputData(BaseModel):
    """Output data model for vector search results."""
    id: Optional[str]
    score: Optional[float]
    payload: Optional[dict]


class PGVector(VectorStoreBase):
    """PostgreSQL with pgvector vector store implementation."""

    def __init__(
        self,
        dbname=None,
        collection_name=None,
        embedding_model_dims=None,
        user=None,
        password=None,
        host=None,
        port=None,
        diskann=False,
        hnsw=False,
        minconn=1,
        maxconn=5,
        sslmode=None,
        connection_string=None,
        connection_pool=None,
    ):
        """Initialize the PGVector database.

        Args:
            dbname: Database name
            collection_name: Collection name
            embedding_model_dims: Dimension of the embedding vector
            user: Database user
            password: Database password
            host: Database host
            port: Database port
            diskann: Use DiskANN for faster search
            hnsw: Use HNSW for faster search
            minconn: Minimum connections in pool
            maxconn: Maximum connections in pool
            sslmode: SSL mode for PostgreSQL connection
            connection_string: PostgreSQL connection string (overrides individual params)
            connection_pool: Existing connection pool object
        """
        self.collection_name = collection_name
        self.use_diskann = diskann
        self.use_hnsw = hnsw
        self.embedding_model_dims = embedding_model_dims
        self.connection_pool = None

        # Connection setup with priority: connection_pool > connection_string > individual parameters
        if connection_pool is not None:
            self.connection_pool = connection_pool
        elif connection_string:
            if sslmode:
                if 'sslmode=' in connection_string:
                    import re
                    connection_string = re.sub(r'sslmode=[^ ]*', f'sslmode={sslmode}', connection_string)
                else:
                    connection_string = f"{connection_string} sslmode={sslmode}"
        else:
            connection_string = f"postgresql://{user}:{password}@{host}:{port}/{dbname}"
            if sslmode:
                connection_string = f"{connection_string} sslmode={sslmode}"

        if self.connection_pool is None:
            if PSYCOPG_VERSION == 3:
                self.connection_pool = ConnectionPool(conninfo=connection_string, min_size=minconn, max_size=maxconn, open=True)
            else:
                self.connection_pool = ConnectionPool(minconn=minconn, maxconn=maxconn, dsn=connection_string)

        collections = self.list_cols()
        if collection_name not in collections:
            self.create_col()

    @contextmanager
    def _get_cursor(self, commit: bool = False):
        """Get a cursor from the connection pool."""
        if PSYCOPG_VERSION == 3:
            with self.connection_pool.connection() as conn:
                with conn.cursor() as cur:
                    try:
                        yield cur
                        if commit:
                            conn.commit()
                    except Exception:
                        conn.rollback()
                        logger.error("Error in cursor context (psycopg3)", exc_info=True)
                        raise
        else:
            conn = self.connection_pool.getconn()
            cur = conn.cursor()
            try:
                yield cur
                if commit:
                    conn.commit()
            except Exception as exc:
                conn.rollback()
                logger.error(f"Error occurred: {exc}")
                raise exc
            finally:
                cur.close()
                self.connection_pool.putconn(conn)

    def create_col(self, vector_size=None, distance=None) -> None:
        """Create a new collection (table in PostgreSQL)."""
        dims = vector_size or self.embedding_model_dims
        with self._get_cursor(commit=True) as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.collection_name} (
                    id UUID PRIMARY KEY,
                    vector vector({dims}),
                    payload JSONB
                );
                """
            )
            if self.use_diskann and dims < 2000:
                cur.execute("SELECT * FROM pg_extension WHERE extname = 'vectorscale'")
                if cur.fetchone():
                    cur.execute(
                        f"""
                        CREATE INDEX IF NOT EXISTS {self.collection_name}_diskann_idx
                        ON {self.collection_name}
                        USING diskann (vector);
                        """
                    )
            elif self.use_hnsw:
                cur.execute(
                    f"""
                    CREATE INDEX IF NOT EXISTS {self.collection_name}_hnsw_idx
                    ON {self.collection_name}
                    USING hnsw (vector vector_cosine_ops)
                    """
                )

    def insert(self, vectors: list[list[float]], payloads=None, ids=None) -> None:
        """Insert vectors into the collection."""
        logger.info(f"Inserting {len(vectors)} vectors into collection {self.collection_name}")
        json_payloads = [json.dumps(payload) for payload in payloads]
        data = [(id, vector, payload) for id, vector, payload in zip(ids, vectors, json_payloads)]

        if PSYCOPG_VERSION == 3:
            with self._get_cursor(commit=True) as cur:
                cur.executemany(
                    f"INSERT INTO {self.collection_name} (id, vector, payload) VALUES (%s, %s, %s)",
                    data,
                )
        else:
            with self._get_cursor(commit=True) as cur:
                execute_values(
                    cur,
                    f"INSERT INTO {self.collection_name} (id, vector, payload) VALUES %s",
                    data,
                )

    def search(
        self,
        query: str,
        vectors: list[float],
        limit: Optional[int] = 5,
        filters: Optional[dict] = None,
    ) -> List[OutputData]:
        """Search for similar vectors."""
        filter_conditions = []
        filter_params = []

        if filters:
            for k, v in filters.items():
                # Skip None values - can't filter on null
                if v is None:
                    continue
                filter_conditions.append("payload->>%s = %s")
                filter_params.extend([k, str(v)])

        filter_clause = "WHERE " + " AND ".join(filter_conditions) if filter_conditions else ""

        with self._get_cursor() as cur:
            cur.execute(
                f"""
                SELECT id, vector <=> %s::vector AS distance, payload
                FROM {self.collection_name}
                {filter_clause}
                ORDER BY distance
                LIMIT %s
                """,
                (vectors, *filter_params, limit),
            )
            results = cur.fetchall()
        return [OutputData(id=str(r[0]), score=float(r[1]), payload=r[2]) for r in results]

    def delete(self, vector_id: str) -> None:
        """Delete a vector by ID."""
        with self._get_cursor(commit=True) as cur:
            cur.execute(f"DELETE FROM {self.collection_name} WHERE id = %s", (vector_id,))

    def update(
        self,
        vector_id: str,
        vector: Optional[list[float]] = None,
        payload: Optional[dict] = None,
    ) -> None:
        """Update a vector and its payload."""
        with self._get_cursor(commit=True) as cur:
            if vector:
                cur.execute(
                    f"UPDATE {self.collection_name} SET vector = %s WHERE id = %s",
                    (vector, vector_id),
                )
            if payload:
                cur.execute(
                    f"UPDATE {self.collection_name} SET payload = %s WHERE id = %s",
                    (Json(payload), vector_id),
                )

    def get(self, vector_id: str) -> OutputData:
        """Retrieve a vector by ID."""
        with self._get_cursor() as cur:
            cur.execute(
                f"SELECT id, vector, payload FROM {self.collection_name} WHERE id = %s",
                (vector_id,),
            )
            result = cur.fetchone()
            if not result:
                return None
            return OutputData(id=str(result[0]), score=None, payload=result[2])

    def list_cols(self) -> List[str]:
        """List all collections."""
        with self._get_cursor() as cur:
            cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
            return [row[0] for row in cur.fetchall()]

    def delete_col(self) -> None:
        """Delete a collection."""
        with self._get_cursor(commit=True) as cur:
            cur.execute(f"DROP TABLE IF EXISTS {self.collection_name}")

    def col_info(self) -> dict[str, Any]:
        """Get information about a collection."""
        with self._get_cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    table_name,
                    (SELECT COUNT(*) FROM {self.collection_name}) as row_count,
                    (SELECT pg_size_pretty(pg_total_relation_size('{self.collection_name}'))) as total_size
                FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = %s
            """,
                (self.collection_name,),
            )
            result = cur.fetchone()
        return {"name": result[0], "count": result[1], "size": result[2]}

    def list(
        self,
        filters: Optional[dict] = None,
        limit: Optional[int] = 100
    ) -> List[OutputData]:
        """List all vectors in a collection."""
        filter_conditions = []
        filter_params = []

        if filters:
            for k, v in filters.items():
                # Skip None values - can't filter on null
                if v is None:
                    continue
                filter_conditions.append("payload->>%s = %s")
                filter_params.extend([k, str(v)])

        filter_clause = "WHERE " + " AND ".join(filter_conditions) if filter_conditions else ""

        query = f"""
            SELECT id, vector, payload
            FROM {self.collection_name}
            {filter_clause}
            LIMIT %s
        """

        with self._get_cursor() as cur:
            cur.execute(query, (*filter_params, limit))
            results = cur.fetchall()
        return [[OutputData(id=str(r[0]), score=None, payload=r[2]) for r in results]]

    def __del__(self) -> None:
        """Close the database connection pool."""
        try:
            if PSYCOPG_VERSION == 3:
                self.connection_pool.close()
            else:
                self.connection_pool.closeall()
        except Exception:
            pass

    def reset(self) -> None:
        """Reset the index by deleting and recreating it."""
        logger.warning(f"Resetting index {self.collection_name}...")
        self.delete_col()
        self.create_col()
