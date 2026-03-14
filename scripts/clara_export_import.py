#!/usr/bin/env python3
"""
Export and import Clara's data (sessions, memories, vectors, graph).

Usage:
    python scripts/clara_export_import.py export -o ./backup --user josh
    python scripts/clara_export_import.py import ./backup.tar.gz --dry-run
"""

import argparse
import json
import logging
import os
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Allow running from repo root without install.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MANIFEST_VERSION = "1"

log = logging.getLogger("clara_export_import")


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def build_manifest(
    *,
    source_backends: dict | None = None,
    filters: dict | None = None,
    record_counts: dict | None = None,
) -> dict:
    """Return a manifest dict describing the export archive."""
    return {
        "version": MANIFEST_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_backends": source_backends or {},
        "filters": filters or {},
        "embedding_model": "text-embedding-3-small",
        "embedding_dimensions": 1536,
        "record_counts": record_counts or {},
    }


# ---------------------------------------------------------------------------
# Relational export
# ---------------------------------------------------------------------------

# Ordered by FK dependencies: parents before children.
# Tuples of (filename, model_class_name).
# Models that don't exist in the codebase are omitted.
RELATIONAL_TABLES: list[tuple[str, str]] = [
    ("canonical_users", "CanonicalUser"),
    ("platform_links", "PlatformLink"),
    ("projects", "Project"),
    ("sessions", "Session"),
    ("messages", "Message"),
    ("memory_dynamics", "MemoryDynamics"),
    ("memory_history", "MemoryHistory"),
    ("memory_supersessions", "MemorySupersession"),
    ("intentions", "Intention"),
    ("personality_traits", "PersonalityTrait"),
    ("personality_trait_history", "PersonalityTraitHistory"),
    ("channel_configs", "ChannelConfig"),
    ("guild_configs", "GuildConfig"),
    ("proactive_messages", "ProactiveMessage"),
    ("user_interaction_patterns", "UserInteractionPattern"),
    ("proactive_notes", "ProactiveNote"),
    ("proactive_assessments", "ProactiveAssessment"),
]


def serialize_row(row) -> dict:
    """Convert a SQLAlchemy model instance to a JSON-safe dict."""
    result = {}
    for col in row.__table__.columns:
        value = getattr(row, col.name)
        if isinstance(value, datetime):
            value = value.isoformat()
        result[col.name] = value
    return result


def export_relational(
    tmp_dir: Path,
    user_id: str | None,
    since: str | None,
) -> dict[str, int]:
    """Export relational tables to JSONL files under tmp_dir/relational/.

    Returns a dict mapping filename to record count.
    """
    import mypalclara.db.models as models
    from mypalclara.db.connection import SessionLocal

    rel_dir = tmp_dir / "relational"
    rel_dir.mkdir(parents=True, exist_ok=True)

    since_dt = None
    if since:
        since_dt = datetime.fromisoformat(since)

    counts: dict[str, int] = {}

    with SessionLocal() as session:
        for filename, model_name in RELATIONAL_TABLES:
            model_cls = getattr(models, model_name, None)
            if model_cls is None:
                log.warning("model %s not found, skipping %s", model_name, filename)
                continue

            query = session.query(model_cls)

            # Apply user filter
            if user_id:
                if hasattr(model_cls, "user_id"):
                    query = query.filter(model_cls.user_id == user_id)
                elif hasattr(model_cls, "canonical_user_id"):
                    query = query.filter(model_cls.canonical_user_id == user_id)

            # Apply since filter
            if since_dt and hasattr(model_cls, "created_at"):
                query = query.filter(model_cls.created_at >= since_dt)

            filepath = rel_dir / f"{filename}.jsonl"
            count = 0

            with open(filepath, "w", encoding="utf-8") as f:
                for row in query.yield_per(500):
                    f.write(json.dumps(serialize_row(row), default=str) + "\n")
                    count += 1
                    if count % 500 == 0:
                        log.info("  %s: %d records written...", filename, count)

            if count == 0:
                filepath.unlink(missing_ok=True)
                log.info("  %s: 0 records (file removed)", filename)
            else:
                log.info("  %s: %d records", filename, count)
                counts[filename] = count

    return counts


# ---------------------------------------------------------------------------
# Vector store export
# ---------------------------------------------------------------------------


def _export_qdrant(
    filepath: Path,
    collection_name: str,
    user_id: str | None,
    since: str | None,
) -> int:
    """Export vectors from Qdrant to a JSONL file. Returns record count."""
    from qdrant_client import QdrantClient
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    from mypalclara.core.memory.config import QDRANT_API_KEY, QDRANT_DATA_DIR, QDRANT_URL

    if QDRANT_URL:
        kwargs = {"url": QDRANT_URL}
        if QDRANT_API_KEY:
            kwargs["api_key"] = QDRANT_API_KEY
    else:
        kwargs = {"path": str(QDRANT_DATA_DIR)}

    client = QdrantClient(**kwargs)

    # Build optional filter
    scroll_filter = None
    if user_id:
        scroll_filter = Filter(must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))])

    count = 0
    offset = None  # First call uses None

    with open(filepath, "w", encoding="utf-8") as f:
        while True:
            results, next_offset = client.scroll(
                collection_name=collection_name,
                scroll_filter=scroll_filter,
                with_vectors=True,
                with_payload=True,
                limit=100,
                offset=offset,
            )

            if not results:
                break

            for point in results:
                vector = point.vector
                if isinstance(vector, list):
                    vector = vector
                else:
                    # Handle named vectors -- convert to list if needed
                    vector = list(vector) if vector is not None else []

                record = {
                    "id": point.id,
                    "vector": vector,
                    "payload": point.payload,
                }
                f.write(json.dumps(record, default=str) + "\n")
                count += 1

            if next_offset is None:
                break
            offset = next_offset

    if count % 500 != 0:
        log.info("  vectors (qdrant): %d records", count)

    return count


def _export_pgvector(
    filepath: Path,
    collection_name: str,
    database_url: str,
    user_id: str | None,
    since: str | None,
) -> int:
    """Export vectors from pgvector to a JSONL file. Returns record count."""
    from sqlalchemy import create_engine, text

    engine = create_engine(database_url)
    count = 0

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, embedding, metadata "
                "FROM langchain_pg_embedding "
                "WHERE collection_id = ("
                "  SELECT uuid FROM langchain_pg_collection WHERE name = :collection"
                ")"
            ),
            {"collection": collection_name},
        )

        with open(filepath, "w", encoding="utf-8") as f:
            for row in rows:
                metadata = row.metadata if row.metadata else {}
                if isinstance(metadata, str):
                    metadata = json.loads(metadata)

                # Post-filter by user_id in payload
                if user_id and metadata.get("user_id") != user_id:
                    continue

                embedding = row.embedding
                if isinstance(embedding, str):
                    embedding = json.loads(embedding)
                elif hasattr(embedding, "tolist"):
                    embedding = embedding.tolist()

                record = {
                    "id": str(row.id),
                    "vector": embedding,
                    "payload": metadata,
                }
                f.write(json.dumps(record, default=str) + "\n")
                count += 1

    return count


def export_vectors(
    tmp_dir: Path,
    user_id: str | None,
    since: str | None,
) -> tuple[dict[str, int], str]:
    """Export vector store data. Returns (counts, provider_name)."""
    from mypalclara.core.memory.config import (
        ROOK_COLLECTION_NAME,
        ROOK_DATABASE_URL,
    )

    vec_dir = tmp_dir / "vectors"
    vec_dir.mkdir(parents=True, exist_ok=True)

    filepath = vec_dir / "memories.jsonl"

    # Try Qdrant first, fall back to pgvector
    if not ROOK_DATABASE_URL:
        provider = "qdrant"
        try:
            count = _export_qdrant(filepath, ROOK_COLLECTION_NAME, user_id, since)
        except Exception as e:
            log.error("Qdrant export failed: %s", e)
            count = 0
    else:
        provider = "pgvector"
        try:
            count = _export_pgvector(filepath, ROOK_COLLECTION_NAME, ROOK_DATABASE_URL, user_id, since)
        except Exception as e:
            log.error("pgvector export failed: %s", e)
            count = 0

    counts: dict[str, int] = {}
    if count == 0:
        filepath.unlink(missing_ok=True)
        log.info("  vectors: 0 records (file removed)")
    else:
        log.info("  vectors: %d records via %s", count, provider)
        counts["memories"] = count

    return counts, provider


# ---------------------------------------------------------------------------
# Graph export
# ---------------------------------------------------------------------------


def export_graph(
    tmp_dir: Path,
    user_id: str | None,
) -> tuple[dict[str, int], str]:
    """Export graph data from FalkorDB. Returns (counts, provider_name)."""
    from mypalclara.core.memory.config import (
        ENABLE_GRAPH_MEMORY,
        FALKORDB_GRAPH_NAME,
        FALKORDB_HOST,
        FALKORDB_PASSWORD,
        FALKORDB_PORT,
    )

    graph_dir = tmp_dir / "graph"
    graph_dir.mkdir(parents=True, exist_ok=True)

    if not ENABLE_GRAPH_MEMORY:
        log.warning("Graph memory disabled (ENABLE_GRAPH_MEMORY=false), skipping graph export")
        return {}, "none"

    try:
        import falkordb
    except ImportError:
        log.warning("falkordb package not installed, skipping graph export")
        return {}, "none"

    try:
        client = falkordb.FalkorDB(
            host=FALKORDB_HOST,
            port=FALKORDB_PORT,
            password=FALKORDB_PASSWORD,
        )
        graph = client.select_graph(FALKORDB_GRAPH_NAME)
    except Exception as e:
        log.error("Failed to connect to FalkorDB: %s", e)
        return {}, "falkordb"

    counts: dict[str, int] = {}

    # Export nodes
    nodes_path = graph_dir / "nodes.jsonl"
    if user_id:
        node_query = "MATCH (n:__Entity__) WHERE n.user_id = $user_id RETURN n"
        node_params = {"user_id": user_id}
    else:
        node_query = "MATCH (n:__Entity__) RETURN n"
        node_params = {}

    try:
        result = graph.query(node_query, node_params)
        node_count = 0
        with open(nodes_path, "w", encoding="utf-8") as f:
            for row in result.result_set:
                node = row[0]
                record = {
                    "id": node.id,
                    "properties": node.properties,
                }
                f.write(json.dumps(record, default=str) + "\n")
                node_count += 1

        if node_count == 0:
            nodes_path.unlink(missing_ok=True)
        else:
            counts["nodes"] = node_count
        log.info("  graph nodes: %d", node_count)
    except Exception as e:
        log.error("Failed to export graph nodes: %s", e)
        nodes_path.unlink(missing_ok=True)

    # Export edges
    edges_path = graph_dir / "edges.jsonl"
    if user_id:
        edge_query = (
            "MATCH (a:__Entity__)-[r]->(b:__Entity__) "
            "WHERE a.user_id = $user_id OR b.user_id = $user_id "
            "RETURN a.name, type(r), r, b.name"
        )
        edge_params = {"user_id": user_id}
    else:
        edge_query = "MATCH (a:__Entity__)-[r]->(b:__Entity__) RETURN a.name, type(r), r, b.name"
        edge_params = {}

    try:
        result = graph.query(edge_query, edge_params)
        edge_count = 0
        with open(edges_path, "w", encoding="utf-8") as f:
            for row in result.result_set:
                record = {
                    "source": row[0],
                    "relation": row[1],
                    "properties": row[2].properties,
                    "target": row[3],
                }
                f.write(json.dumps(record, default=str) + "\n")
                edge_count += 1

        if edge_count == 0:
            edges_path.unlink(missing_ok=True)
        else:
            counts["edges"] = edge_count
        log.info("  graph edges: %d", edge_count)
    except Exception as e:
        log.error("Failed to export graph edges: %s", e)
        edges_path.unlink(missing_ok=True)

    return counts, "falkordb"


# ---------------------------------------------------------------------------
# Relational import
# ---------------------------------------------------------------------------


def import_relational(
    tmp_dir: Path,
    tables_filter: list[str] | None,
    dry_run: bool,
) -> dict[str, int]:
    """Import relational data from JSONL files under tmp_dir/relational/.

    Uses ``db.merge()`` for idempotent upserts. DateTime columns are
    converted from ISO strings back to ``datetime`` objects.

    Returns a dict mapping filename to record count.
    """
    from sqlalchemy import DateTime as SADateTime

    import mypalclara.db.models as models

    rel_dir = tmp_dir / "relational"
    if not rel_dir.is_dir():
        log.info("No relational/ directory found; skipping relational import")
        return {}

    counts: dict[str, int] = {}

    if dry_run:
        # Dry run: count records without touching the DB
        for filename, model_name in RELATIONAL_TABLES:
            filepath = rel_dir / f"{filename}.jsonl"
            if not filepath.exists():
                continue
            if tables_filter and filename not in tables_filter:
                continue
            count = sum(1 for _ in open(filepath, encoding="utf-8"))
            if count:
                counts[filename] = count
                log.info("  %s: %d records (dry run)", filename, count)
        return counts

    from mypalclara.db.connection import SessionLocal

    with SessionLocal() as session:
        for filename, model_name in RELATIONAL_TABLES:
            filepath = rel_dir / f"{filename}.jsonl"
            if not filepath.exists():
                continue
            if tables_filter and filename not in tables_filter:
                continue

            model_cls = getattr(models, model_name, None)
            if model_cls is None:
                log.warning("model %s not found, skipping %s", model_name, filename)
                continue

            # Identify DateTime columns for ISO string conversion
            dt_columns: set[str] = set()
            for col in model_cls.__table__.columns:
                if isinstance(col.type, SADateTime):
                    dt_columns.add(col.name)

            count = 0
            with open(filepath, encoding="utf-8") as f:
                for line in f:
                    row_data = json.loads(line)

                    # Convert ISO strings back to datetime for DateTime columns
                    for col_name in dt_columns:
                        value = row_data.get(col_name)
                        if value is not None:
                            row_data[col_name] = datetime.fromisoformat(value)

                    obj = model_cls(**row_data)
                    session.merge(obj)
                    count += 1

                    if count % 500 == 0:
                        session.flush()
                        log.info("  %s: %d records merged...", filename, count)

            session.commit()
            if count:
                counts[filename] = count
            log.info("  %s: %d records", filename, count)

    return counts


# ---------------------------------------------------------------------------
# Vector import
# ---------------------------------------------------------------------------


def _import_qdrant(
    filepath: Path,
    collection_name: str,
    need_re_embed: bool,
    dry_run: bool,
    embedder=None,
) -> dict[str, int]:
    """Import vectors into Qdrant from a JSONL file. Returns record counts."""
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, PointStruct, VectorParams

    from mypalclara.core.memory.config import QDRANT_API_KEY, QDRANT_DATA_DIR, QDRANT_URL

    if QDRANT_URL:
        kwargs = {"url": QDRANT_URL}
        if QDRANT_API_KEY:
            kwargs["api_key"] = QDRANT_API_KEY
    else:
        kwargs = {"path": str(QDRANT_DATA_DIR)}

    count = 0

    if dry_run:
        with open(filepath, encoding="utf-8") as f:
            count = sum(1 for _ in f)
        return {"memories": count} if count else {}

    client = QdrantClient(**kwargs)

    # Ensure collection exists
    try:
        client.get_collection(collection_name)
    except Exception:
        client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=1536, distance=Distance.COSINE),
        )

    batch: list = []

    with open(filepath, encoding="utf-8") as f:
        for line in f:
            record = json.loads(line)
            payload = record.get("payload", {})
            vector = record.get("vector", [])

            if need_re_embed and embedder is not None:
                text = payload.get("memory", payload.get("data", ""))
                if text:
                    vector = embedder.embed(text, memory_action="add")

            batch.append(
                PointStruct(
                    id=record["id"],
                    vector=vector,
                    payload=payload,
                )
            )
            count += 1

            if len(batch) >= 100:
                client.upsert(collection_name=collection_name, points=batch)
                batch = []
                log.info("  vectors (qdrant): %d records upserted...", count)

    if batch:
        client.upsert(collection_name=collection_name, points=batch)

    log.info("  vectors (qdrant): %d records total", count)
    return {"memories": count} if count else {}


def _import_pgvector(
    filepath: Path,
    collection_name: str,
    database_url: str,
    need_re_embed: bool,
    dry_run: bool,
    embedder=None,
) -> dict[str, int]:
    """Import vectors into pgvector from a JSONL file. Returns record counts."""
    from sqlalchemy import create_engine, text

    count = 0

    if dry_run:
        with open(filepath, encoding="utf-8") as f:
            count = sum(1 for _ in f)
        return {"memories": count} if count else {}

    engine = create_engine(database_url)

    with engine.connect() as conn:
        # Get or create collection UUID
        row = conn.execute(
            text("SELECT uuid FROM langchain_pg_collection WHERE name = :name"),
            {"name": collection_name},
        ).fetchone()

        if row is None:
            import uuid

            collection_uuid = str(uuid.uuid4())
            conn.execute(
                text("INSERT INTO langchain_pg_collection (uuid, name) VALUES (:uuid, :name)"),
                {"uuid": collection_uuid, "name": collection_name},
            )
        else:
            collection_uuid = str(row[0])

        with open(filepath, encoding="utf-8") as f:
            for line in f:
                record = json.loads(line)
                payload = record.get("payload", {})
                vector = record.get("vector", [])

                if need_re_embed and embedder is not None:
                    text_content = payload.get("memory", payload.get("data", ""))
                    if text_content:
                        vector = embedder.embed(text_content, memory_action="add")

                conn.execute(
                    text(
                        "INSERT INTO langchain_pg_embedding (id, collection_id, embedding, metadata) "
                        "VALUES (:id, :collection_id, :embedding, :metadata) "
                        "ON CONFLICT (id) DO UPDATE SET "
                        "embedding = EXCLUDED.embedding, metadata = EXCLUDED.metadata"
                    ),
                    {
                        "id": record["id"],
                        "collection_id": collection_uuid,
                        "embedding": json.dumps(vector),
                        "metadata": json.dumps(payload),
                    },
                )
                count += 1

        conn.commit()

    log.info("  vectors (pgvector): %d records total", count)
    return {"memories": count} if count else {}


def import_vectors(
    tmp_dir: Path,
    manifest: dict,
    re_embed: bool,
    dry_run: bool,
) -> dict[str, int]:
    """Import vector data from JSONL. Returns record counts."""
    from mypalclara.core.memory.config import ROOK_COLLECTION_NAME, ROOK_DATABASE_URL

    filepath = tmp_dir / "vectors" / "memories.jsonl"
    if not filepath.exists():
        log.info("No vectors/memories.jsonl found; skipping vector import")
        return {}

    need_re_embed = re_embed or manifest.get("embedding_model") != "text-embedding-3-small"

    embedder = None
    if need_re_embed and not dry_run:
        from mypalclara.core.memory.embeddings.base import BaseEmbedderConfig
        from mypalclara.core.memory.embeddings.openai import OpenAIEmbedding

        embedder = OpenAIEmbedding(
            BaseEmbedderConfig(
                model="text-embedding-3-small",
                api_key=os.getenv("OPENAI_API_KEY"),
            )
        )
        log.info("Re-embedding enabled — vectors will be recomputed")

    if not ROOK_DATABASE_URL:
        return _import_qdrant(filepath, ROOK_COLLECTION_NAME, need_re_embed, dry_run, embedder)
    else:
        return _import_pgvector(filepath, ROOK_COLLECTION_NAME, ROOK_DATABASE_URL, need_re_embed, dry_run, embedder)


# ---------------------------------------------------------------------------
# Graph import
# ---------------------------------------------------------------------------


def import_graph(
    tmp_dir: Path,
    dry_run: bool,
) -> dict[str, int]:
    """Import graph data into FalkorDB from JSONL files. Returns record counts."""
    from mypalclara.core.memory.config import (
        ENABLE_GRAPH_MEMORY,
        FALKORDB_GRAPH_NAME,
        FALKORDB_HOST,
        FALKORDB_PASSWORD,
        FALKORDB_PORT,
    )

    nodes_path = tmp_dir / "graph" / "nodes.jsonl"
    edges_path = tmp_dir / "graph" / "edges.jsonl"

    if not nodes_path.exists() and not edges_path.exists():
        log.info("No graph data found; skipping graph import")
        return {}

    if not ENABLE_GRAPH_MEMORY:
        log.warning("Graph memory disabled (ENABLE_GRAPH_MEMORY=false), skipping graph import")
        return {}

    try:
        import falkordb
    except ImportError:
        log.warning("falkordb package not installed, skipping graph import")
        return {}

    counts: dict[str, int] = {}

    if dry_run:
        if nodes_path.exists():
            node_count = sum(1 for _ in open(nodes_path, encoding="utf-8"))
            if node_count:
                counts["nodes"] = node_count
                log.info("  graph nodes: %d (dry run)", node_count)
        if edges_path.exists():
            edge_count = sum(1 for _ in open(edges_path, encoding="utf-8"))
            if edge_count:
                counts["edges"] = edge_count
                log.info("  graph edges: %d (dry run)", edge_count)
        return counts

    try:
        client = falkordb.FalkorDB(
            host=FALKORDB_HOST,
            port=FALKORDB_PORT,
            password=FALKORDB_PASSWORD,
        )
        graph = client.select_graph(FALKORDB_GRAPH_NAME)
    except Exception as e:
        log.error("Failed to connect to FalkorDB: %s", e)
        return {}

    # Import nodes
    if nodes_path.exists():
        node_count = 0
        with open(nodes_path, encoding="utf-8") as f:
            for line in f:
                record = json.loads(line)
                props = record.get("properties", {})
                name = props.get("name", "")
                # Build SET clause for all properties
                set_parts = []
                params = {"name": name}
                for key, value in props.items():
                    if key == "name":
                        continue
                    param_key = f"p_{key}"
                    set_parts.append(f"n.{key} = ${param_key}")
                    params[param_key] = value

                query = "MERGE (n:__Entity__ {name: $name})"
                if set_parts:
                    query += " SET " + ", ".join(set_parts)
                graph.query(query, params)
                node_count += 1

        if node_count:
            counts["nodes"] = node_count
        log.info("  graph nodes: %d", node_count)

    # Import edges
    if edges_path.exists():
        edge_count = 0
        with open(edges_path, encoding="utf-8") as f:
            for line in f:
                record = json.loads(line)
                source = record["source"]
                target = record["target"]
                relation = record.get("relation", "RELATION")
                props = record.get("properties", {})

                set_parts = []
                params = {"source": source, "target": target}
                for key, value in props.items():
                    param_key = f"p_{key}"
                    set_parts.append(f"r.{key} = ${param_key}")
                    params[param_key] = value

                query = (
                    "MERGE (a:__Entity__ {name: $source}) "
                    "MERGE (b:__Entity__ {name: $target}) "
                    f"MERGE (a)-[r:{relation}]->(b)"
                )
                if set_parts:
                    query += " SET " + ", ".join(set_parts)
                graph.query(query, params)
                edge_count += 1

        if edge_count:
            counts["edges"] = edge_count
        log.info("  graph edges: %d", edge_count)

    return counts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _detect_relational_backend() -> str:
    """Return 'postgresql' or 'sqlite' based on DATABASE_URL."""
    db_url = os.getenv("DATABASE_URL", "")
    if db_url.startswith("postgres"):
        return "postgresql"
    return "sqlite"


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def cmd_export(args: argparse.Namespace) -> None:
    """Export Clara data to a tar.gz archive."""
    log.info(
        "export requested  output=%s  user=%s  since=%s",
        args.output,
        args.user,
        args.since,
    )

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="clara-export-") as tmpdir:
        tmp_dir = Path(tmpdir)

        # 1. Relational export
        log.info("=== Relational export ===")
        rel_counts = export_relational(tmp_dir, args.user, args.since)

        # 2. Vector export
        log.info("=== Vector export ===")
        vec_counts, vec_provider = export_vectors(tmp_dir, args.user, args.since)

        # 3. Graph export
        log.info("=== Graph export ===")
        graph_counts, graph_provider = export_graph(tmp_dir, args.user)

        # 4. Build manifest
        source_backends = {
            "relational": _detect_relational_backend(),
            "vector": vec_provider,
            "graph": graph_provider,
        }
        filters: dict[str, str] = {}
        if args.user:
            filters["user"] = args.user
        if args.since:
            filters["since"] = args.since

        record_counts: dict[str, int] = {}
        record_counts.update(rel_counts)
        record_counts.update(vec_counts)
        record_counts.update(graph_counts)

        manifest = build_manifest(
            source_backends=source_backends,
            filters=filters,
            record_counts=record_counts,
        )
        manifest_path = tmp_dir / "manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        # 5. Bundle into tar.gz
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archive_name = f"clara-export-{timestamp}.tar.gz"
        archive_path = output_dir / archive_name

        with tarfile.open(archive_path, "w:gz") as tar:
            for file_path in tmp_dir.rglob("*"):
                if file_path.is_file():
                    arcname = file_path.relative_to(tmp_dir)
                    tar.add(file_path, arcname=str(arcname))

        # 6. Summary
        total_records = sum(record_counts.values())
        log.info("=== Export complete ===")
        log.info("Archive: %s", archive_path)
        log.info("Total records: %d", total_records)
        for name, count in sorted(record_counts.items()):
            log.info("  %s: %d", name, count)


def cmd_import(args: argparse.Namespace) -> None:
    """Import Clara data from a tar.gz archive."""
    log.info(
        "import requested  archive=%s  dry_run=%s  re_embed=%s  tables=%s  strict=%s",
        args.archive,
        args.dry_run,
        args.re_embed,
        args.tables,
        args.strict,
    )

    archive_path = Path(args.archive)
    if not archive_path.exists():
        log.error("Archive not found: %s", archive_path)
        sys.exit(1)

    tables_filter: list[str] | None = None
    if args.tables:
        tables_filter = [t.strip() for t in args.tables.split(",")]

    with tempfile.TemporaryDirectory(prefix="clara-import-") as tmpdir:
        tmp_dir = Path(tmpdir)

        # Extract archive
        log.info("Extracting %s ...", archive_path)
        with tarfile.open(archive_path, "r:gz") as tar:
            if sys.version_info >= (3, 12):
                tar.extractall(tmp_dir, filter="data")
            else:
                tar.extractall(tmp_dir)  # noqa: S202

        # Read and validate manifest
        manifest_path = tmp_dir / "manifest.json"
        if not manifest_path.exists():
            log.error("Archive missing manifest.json")
            sys.exit(1)

        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)

        if manifest.get("version") != MANIFEST_VERSION:
            log.error(
                "Unsupported manifest version: %s (expected %s)",
                manifest.get("version"),
                MANIFEST_VERSION,
            )
            sys.exit(1)

        log.info("Manifest OK  version=%s  created=%s", manifest["version"], manifest.get("created_at"))

        # Initialize DB schema (no migrations — just ensure tables exist)
        if not args.dry_run:
            from mypalclara.db.connection import init_db

            init_db(run_migrations=False)

        record_counts: dict[str, int] = {}

        # 1. Relational import
        log.info("=== Relational import ===")
        rel_counts = import_relational(tmp_dir, tables_filter, args.dry_run)
        record_counts.update(rel_counts)

        # 2. Vector import (only if "memories" in filter, or no filter)
        if tables_filter is None or "memories" in tables_filter:
            log.info("=== Vector import ===")
            vec_counts = import_vectors(tmp_dir, manifest, args.re_embed, args.dry_run)
            record_counts.update(vec_counts)

        # 3. Graph import (only if "nodes"/"edges" in filter, or no filter)
        if tables_filter is None or "nodes" in tables_filter or "edges" in tables_filter:
            log.info("=== Graph import ===")
            graph_counts = import_graph(tmp_dir, args.dry_run)
            record_counts.update(graph_counts)

        # Summary
        total_records = sum(record_counts.values())
        label = "dry run" if args.dry_run else "imported"
        log.info("=== Import complete (%s) ===", label)
        log.info("Total records: %d", total_records)
        for name, count in sorted(record_counts.items()):
            log.info("  %s: %d", name, count)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build and return the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="clara_export_import",
        description="Export / import Clara data (sessions, memories, vectors, graph).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # -- export ---------------------------------------------------------------
    p_export = sub.add_parser("export", help="Export data to a tar.gz archive")
    p_export.add_argument(
        "-o",
        "--output",
        default=".",
        help="Directory to write the archive into (default: current dir)",
    )
    p_export.add_argument("--user", default=None, help="Export only this user's data")
    p_export.add_argument(
        "--since",
        default=None,
        help="Only include records created/updated after this ISO date",
    )
    p_export.set_defaults(func=cmd_export)

    # -- import ---------------------------------------------------------------
    p_import = sub.add_parser("import", help="Import data from a tar.gz archive")
    p_import.add_argument("archive", help="Path to the .tar.gz archive to import")
    p_import.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and report without writing",
    )
    p_import.add_argument(
        "--re-embed",
        action="store_true",
        help="Recompute embeddings instead of importing stored vectors",
    )
    p_import.add_argument(
        "--tables",
        default=None,
        help="Comma-separated list of tables to import (default: all)",
    )
    p_import.add_argument(
        "--strict",
        action="store_true",
        help="Abort on first error instead of skipping bad records",
    )
    p_import.set_defaults(func=cmd_import)

    return parser


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
